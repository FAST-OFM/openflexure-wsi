"""Bounded R/G autofocus orchestration tests with no camera or stage hardware."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.focus_scan import FrozenRGFocusSettings
from openflexure_microscope_server.focus.rg.rg_focus_calibration import (
    RGFocusApproachSettings,
)
from openflexure_microscope_server.focus.rg.rg_focus_control import (
    RGFocusControlSettings,
)
from openflexure_microscope_server.focus.rg.rg_focus_model import (
    RGFocusMeasurementPolicy,
    RGFocusModelSettings,
)
from openflexure_microscope_server.integrations.fast_ofm_core import FastOFMCoreError
from openflexure_microscope_server.things.focus import rg_focus as focus_module
from openflexure_microscope_server.things.focus.rg_focus import (
    CapturedRGFocusPair,
    RGFocus,
    _demo_focus_contract,
)

from .rg_decision_process_stub import external_rg_decision
from .rg_model_process_stub import external_profile_validation, fit_rg_focus_model
from .test_focus_approach import binding as approach_binding
from .test_rg_focus_control import calibration_point, measurement
from .test_rg_focus_model import stationary_observations
from .test_rg_focus_thing import commission_profile

JPEG_DOMAIN = "processed-jpeg-rgb8"


def captured_pair(value, capture_id="pair"):
    """Return the owned three-frame JPEG result used by orchestration tests."""
    return CapturedRGFocusPair(
        measurement=value,
        capture_id=capture_id,
        field=SimpleNamespace(),
        white_score=None,
        capture_path=f"/flat/{capture_id}",
        frame_ids=(
            f"{capture_id}:white",
            f"{capture_id}:red",
            f"{capture_id}:green",
        ),
    )


@pytest.fixture
def profile():
    """Return a valid signed model for the mocked orchestration loop."""
    points = [calibration_point(f"fit-{z}", "fit", z) for z in (-8, -4, 0, 4, 8)]
    points += [calibration_point(f"hold-{z}", "holdout", z) for z in (-6, -2, 2, 6)]
    points += [
        calibration_point("above", "approach_above", 0),
        calibration_point("below", "approach_below", 0),
    ]
    return fit_rg_focus_model(
        points,
        RGFocusModelSettings(maximum_holdout_error_um=2.0),
        reference_white_score=100.0,
        stationary_observations=stationary_observations(),
        profile_id="profile",
        source_series_id="series",
        compatibility={},
        measurement_policy=RGFocusMeasurementPolicy(measurement_domain=JPEG_DOMAIN),
    )


def test_surface_binding_uses_live_reference_calibration_and_profile_ids(
    profile, mocker
):
    """The scan binding is derived from live owners, not an enabled/prepared flag."""
    rg_focus = create_thing_without_server(RGFocus, mock_all_slots=True)
    mocker.patch.object(
        rg_focus,
        "_external_profile_validation",
        side_effect=external_profile_validation,
    )
    rg_focus.measurement_parameters = profile.measurement_policy
    rg_focus._stage._url = "http://controller"
    rg_focus._stage.controller_state = {
        "reference_valid": True,
        "reference_id": "reference-1",
    }
    rg_focus._stage.hardware_settings = {
        "axes": {
            axis: {"units_per_mm": scale, "direction_sign": sign}
            for axis, scale, sign in (
                ("x", 1000.0, 1),
                ("y", 2000.0, -1),
                ("z", 4000.0, 1),
            )
        }
    }
    rg_focus._rg_flat_field.calibration_status = {
        "red": {"status": "valid"},
        "green": {"status": "valid"},
    }
    rg_focus._rg_flat_field.profiles = {
        "red": {"id": "red-flat"},
        "green": {"id": "green-flat"},
    }
    rg_focus.approach_parameters = RGFocusApproachSettings()
    mocker.patch.object(rg_focus, "checked_profile", return_value=profile)
    mocker.patch.object(rg_focus, "_binding", return_value={"geometry_id": "geo-1"})
    csm = {"camera_stage_mapping_calibration": {"matrix": [[1, 0], [0, 1]]}}
    surface_binding = rg_focus.focus_surface_binding(csm)
    assert surface_binding.reference_id == "reference-1"
    assert surface_binding.camera_stage_mapping_status == "valid"
    assert surface_binding.rg_focus_model_id == profile.id
    assert surface_binding.red_flat_field_profile_id == "red-flat"
    assert surface_binding.green_flat_field_profile_id == "green-flat"
    assert [axis.units_per_mm for axis in surface_binding.axis_scales] == [
        1000.0,
        2000.0,
        4000.0,
    ]
    assert rg_focus.focus_surface_binding(csm) == surface_binding
    changed = rg_focus.focus_surface_binding({"changed": True})
    assert changed.camera_stage_mapping_id != surface_binding.camera_stage_mapping_id

    # Once a scan has frozen its contract, edits to next-run form parameters do
    # not redefine the active binding.  Physical/profile identities are still
    # regenerated and compared at every boundary.
    frozen_profile = commission_profile(rg_focus, profile, {"geometry_id": "geo-1"})
    rg_focus.profile = frozen_profile.model_dump(mode="json")
    frozen = FrozenRGFocusSettings(
        profile=frozen_profile,
        measurement_policy=rg_focus.measurement_parameters,
        control=rg_focus.control_parameters,
        binding=surface_binding,
        effective_focus_tolerance_um=external_profile_validation(
            frozen_profile,
            rg_focus.control_parameters.focus_tolerance_um,
        )[1],
    )
    rg_focus.approach_parameters = RGFocusApproachSettings(preload_um=16)
    mocker.patch(
        "openflexure_microscope_server.things.focus.rg_focus.focus_binding",
        return_value={"geometry_id": "geo-1"},
    )
    assert rg_focus.focus_surface_binding(csm, frozen=frozen) == surface_binding
    assert rg_focus.approach_parameters.preload_um == 16

    rg_focus.profile = frozen_profile.model_copy(
        update={"source_series_id": "replacement"}
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="profile changed"):
        rg_focus.focus_surface_binding(csm, frozen=frozen)


@pytest.fixture
def autofocus_thing(tmp_path, request, mocker):
    """Replace only measurement and stage operations around the real finite loop."""
    thing = create_thing_without_server(RGFocus, mock_all_slots=True)
    thing._data_dir = str(tmp_path)
    fitted = request.getfixturevalue("profile")
    thing.measurement_parameters = fitted.measurement_policy
    mocker.patch.object(thing, "checked_profile", return_value=fitted)
    thing._stage.hardware_settings = {"axes": {"z": {"units_per_mm": 1000.0}}}
    position = [10, 20, 100]
    thing._stage.get_xyz_position.side_effect = lambda: tuple(position)
    mocker.patch.object(thing, "_external_decision", side_effect=external_rg_decision)
    mocker.patch.object(
        thing,
        "_external_profile_validation",
        side_effect=external_profile_validation,
    )

    def move(target):
        position[:] = target

    thing._stage.move_absolute_in_segments.side_effect = move
    thing._stage.move_z_with_preload.side_effect = lambda z, _preload, _sign: move(
        (position[0], position[1], z)
    )
    return thing, position


@pytest.mark.parametrize("status", ["ready", "no_tissue"])
def test_single_measure_uses_owned_handoff_without_focus_or_motion(
    autofocus_thing, mocker, profile, status
):
    """Diagnostic measurement needs no valid focus model and never corrects Z."""
    thing, position = autofocus_thing
    thing.profile = profile.model_copy(update={"status": "candidate"}).model_dump(
        mode="json"
    )
    capture = mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(status=status), "single-pair"),
    )
    result = thing.measure(prepared=True)
    directory = Path(thing.data_dir) / result["id"]
    capture.assert_called_once_with(directory, 1)
    assert result["kind"] == "measure"
    assert result["diagnostic_only"] is True
    assert result["autofocus_authorized"] is False
    assert result["status"] == status
    assert result["capture_id"] == "single-pair"
    assert result["capture_path"] == "/flat/single-pair"
    assert result["frame_ids"] == [
        "single-pair:white",
        "single-pair:red",
        "single-pair:green",
    ]
    assert (directory / "report.json").is_file()
    assert position == [10, 20, 100]
    thing.checked_profile.assert_not_called()
    thing._stage.move_absolute_in_segments.assert_not_called()
    thing._stage.move_z_with_preload.assert_not_called()


def test_single_measure_requires_preparation_before_capture(autofocus_thing, mocker):
    """The diagnostic fast path does not bypass the operator readiness input."""
    thing, _position = autofocus_thing
    capture = mocker.patch.object(thing, "_capture_measurement")
    with pytest.raises(ValueError, match="Confirm tissue"):
        thing.measure()
    capture.assert_not_called()
    thing._stage.move_z_with_preload.assert_not_called()


def test_measure_correct_remeasure_stops_focused(autofocus_thing, mocker):
    """One signed move is followed by an independent successful measurement."""
    thing, position = autofocus_thing
    captures = [measurement(dx=-1.0, dy=-0.1), measurement()]
    mocker.patch.object(
        thing,
        "_capture_measurement",
        side_effect=[
            captured_pair(captures[0], "first"),
            captured_pair(captures[1], "second"),
        ],
    )
    result = thing.autofocus(prepared=True)
    assert result["status"] == "focused"
    assert result["focus_z_units"] == 96
    assert result["total_correction_um"] == pytest.approx(4)
    assert len(result["iterations"]) == 2
    assert position == [10, 20, 96]
    thing._stage.move_absolute_in_segments.assert_not_called()
    assert thing._stage.move_z_with_preload.call_args_list == [
        mocker.call(100, 8, 1),
        mocker.call(96, 8, 1),
    ]
    assert result["initial_z_path_units"] == [92, 100]
    assert result["initial_final_approach_confirmed"] is True
    assert result["iterations"][0]["z_path_units"] == [88, 96]
    saved = Path(thing.data_dir) / result["data_path"] / "report.json"
    assert saved.is_file()


def test_demo_scan_uses_one_pair_and_one_modelled_correction(autofocus_thing, mocker):
    """A no-surface demo field never pays for a post-move R/G pair."""
    thing, position = autofocus_thing
    capture = mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(dx=-1.0, dy=-0.1), "single"),
    )

    result = thing.autofocus_with_white_fallback_for_scan(white_dz=50)

    assert result["status"] == "focused"
    assert result["focus_method"] == "rg"
    assert result["focus_z_units"] == 96
    assert result["total_correction_um"] == pytest.approx(4)
    assert result["verification_mode"] == "single_pair_model"
    assert result["independent_post_move_verification"] is False
    assert result["final_estimated_error_um"] == pytest.approx(0)
    assert len(result["iterations"]) == 1
    assert capture.call_count == 1
    assert position == [10, 20, 96]
    thing._white_autofocus.fast_autofocus.assert_not_called()


def test_control_hybrid_uses_one_pair_and_one_modelled_correction(
    autofocus_thing, mocker
):
    """The Control R/G action uses the same bounded one-pair demo policy."""
    thing, position = autofocus_thing
    capture = mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(dx=-1.0, dy=-0.1), "single"),
    )

    result = thing.autofocus_with_white_fallback(prepared=True, white_dz=50)

    assert result["status"] == "focused"
    assert result["focus_method"] == "rg"
    assert result["verification_mode"] == "single_pair_model"
    assert result["independent_post_move_verification"] is False
    assert result["final_estimated_error_um"] == pytest.approx(0)
    assert result["total_correction_um"] == pytest.approx(4)
    assert len(result["iterations"]) == 1
    assert capture.call_count == 1
    assert position == [10, 20, 96]
    thing._white_autofocus.fast_autofocus.assert_not_called()


def test_demo_range_uses_near_full_move_and_final_verification(autofocus_thing, mocker):
    """The demo contract permits one near-full-range move before verification."""
    thing, position = autofocus_thing
    points = [calibration_point(f"fit-{z}", "fit", z) for z in (-9, -3, 0, 3, 9)]
    points += [calibration_point(f"hold-{z}", "holdout", z) for z in (-6, 6)]
    points += [
        calibration_point("above", "approach_above", 0),
        calibration_point("below", "approach_below", 0),
    ]
    fitted = fit_rg_focus_model(
        points,
        RGFocusModelSettings(
            minimum_valid_holdout_points=2,
            maximum_holdout_error_um=2.0,
        ),
        reference_white_score=100.0,
        stationary_observations=stationary_observations(),
        profile_id="wide-profile",
        source_series_id="wide-series",
        compatibility={},
        measurement_policy=RGFocusMeasurementPolicy(measurement_domain=JPEG_DOMAIN),
    )
    thing.checked_profile.return_value = fitted
    thing.measurement_parameters = fitted.measurement_policy
    thing.control_parameters = _demo_focus_contract()["control_parameters"]
    mocker.patch.object(
        thing,
        "_capture_measurement",
        side_effect=[
            captured_pair(measurement(dx=11.0, dy=-1.3), "coarse"),
            captured_pair(measurement(), "verified"),
        ],
    )

    result = thing.autofocus(prepared=True)

    assert result["status"] == "focused"
    assert result["total_correction_um"] == 8
    assert [
        row["decision"]["correction_um"] for row in result["iterations"]
    ] == pytest.approx([8, 0])
    assert position == [10, 20, 108]
    assert thing._stage.move_z_with_preload.call_args_list == [
        mocker.call(100, 8, 1),
        mocker.call(108, 8, 1),
    ]


def test_scan_context_freezes_parameters_and_returns_distinct_final_frames(
    autofocus_thing, mocker, profile
):
    """The native R/G loop emits final mode-frame provenance before tile capture."""
    thing, _position = autofocus_thing
    stage_binding = approach_binding(rg_focus_model_id=profile.id)
    field = SimpleNamespace(
        frozen=FrozenRGFocusSettings(
            profile=profile,
            measurement_policy=thing.measurement_parameters,
            control=thing.control_parameters,
            binding=stage_binding,
            effective_focus_tolerance_um=external_profile_validation(
                profile,
                thing.control_parameters.focus_tolerance_um,
            )[1],
        ),
        session=SimpleNamespace(scan_id="scan-1"),
        field_id="field-1",
        attempt_id="attempt-1",
        prediction=SimpleNamespace(status="unavailable"),
        before_effect=MagicMock(),
        sync_after_effect=MagicMock(),
        mark_measured=MagicMock(),
        current_binding=MagicMock(return_value=stage_binding),
    )
    pair = CapturedRGFocusPair(
        measurement=measurement(),
        capture_id="pair-1",
        field=SimpleNamespace(),
        white_score=None,
        capture_path="/flat/pair-1",
        frame_ids=(
            "field-1:white:100",
            "field-1:red:101",
            "field-1:green:202",
        ),
    )
    mocker.patch.object(thing, "_capture_pair", return_value=pair)
    result = thing.autofocus_for_scan(field)
    assert result["status"] == "focused"
    assert result["scan_id"] == "scan-1"
    assert result["iterations"][0]["frame_ids"] == list(pair.frame_ids)
    field.before_effect.assert_called_once_with(
        "rg_capture_1", reserve_s=thing.control_parameters.minimum_capture_budget_s
    )
    field.sync_after_effect.assert_called_once_with("rg_capture_1")
    field.mark_measured.assert_called_once_with(
        iteration=1,
        capture_id="pair-1",
        frame_ids=pair.frame_ids,
        measurement_status="ready",
    )
    thing._stage.move_z_with_preload.assert_not_called()


def test_rejected_measurement_records_failure_without_move(autofocus_thing, mocker):
    """no_tissue is retained after the zero-net initial approach, without fallback."""
    thing, position = autofocus_thing
    mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(status="no_tissue"), "empty"),
    )
    with pytest.raises(ValueError, match="no_tissue"):
        thing.autofocus(prepared=True)
    thing._stage.move_absolute_in_segments.assert_not_called()
    thing._stage.move_z_with_preload.assert_called_once_with(100, 8, 1)
    assert position == [10, 20, 100]
    reports = list(Path(thing.data_dir).glob("*/report.json"))
    assert len(reports) == 1
    assert '"status": "failed"' in reports[0].read_text()


def test_eligible_refusal_runs_exactly_one_explicit_white_fallback(
    autofocus_thing, mocker
):
    """The hybrid action records one R/G refusal and never retries either method."""
    thing, position = autofocus_thing
    capture = mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(status="no_tissue"), "empty"),
    )
    white_result = MagicMock()
    white_result.model_dump.return_value = {"sharpness": [1.0, 2.0]}
    thing._white_autofocus.fast_autofocus.return_value = white_result

    result = thing.autofocus_with_white_fallback(prepared=True, white_dz=50)

    assert result["status"] == "focused"
    assert result["focus_method"] == "white_fallback"
    assert result["fallback_count"] == 1
    assert "no_tissue" in result["fallback_reason"]
    assert result["rg_result_id"]
    assert result["rg_report_ref"].endswith("/report.json")
    assert result["start_position_units"] == [10, 20, 100]
    assert result["final_position_units"] == [10, 20, 100]
    assert capture.call_count == 1
    thing._white_autofocus.fast_autofocus.assert_called_once_with(dz=50, start="centre")
    reports = [
        json.loads(path.read_text())
        for path in Path(thing.data_dir).glob("*/report.json")
    ]
    assert sorted(report["kind"] for report in reports) == [
        "autofocus",
        "autofocus_white_fallback",
    ]


def test_core_unavailable_after_safe_capture_routes_to_white_fallback(
    autofocus_thing, mocker
):
    """A process failure before any correction is a recorded WHITE fallback."""
    thing, _position = autofocus_thing
    mocker.patch.object(
        thing,
        "_capture_measurement",
        side_effect=FastOFMCoreError(
            "CORE_UNAVAILABLE", "not installed", retryable=True
        ),
    )
    white_result = MagicMock()
    white_result.model_dump.return_value = {"sharpness": [1.0]}
    thing._white_autofocus.fast_autofocus.return_value = white_result

    result = thing.autofocus_with_white_fallback(prepared=True, white_dz=50)

    assert result["focus_method"] == "white_fallback"
    assert result["fallback_count"] == 1
    assert "CORE_UNAVAILABLE" in result["fallback_reason"]


def test_scan_white_precheck_crops_existing_fast_ofm_frame_to_exact_rg_geometry(
    autofocus_thing, mocker
):
    """The advisory precheck reuses one source frame without camera or light calls."""
    from .test_rg_focus_field import geometry

    thing, _position = autofocus_thing
    geometry_value = geometry()
    source_width, source_height = geometry_value["source_image_size"]
    source = np.arange(source_width * source_height * 3, dtype=np.uint32)
    source = (source % 256).astype(np.uint8).reshape(source_height, source_width, 3)
    thing._cam.jpeg_measurement_configuration = {"camera": "current"}
    mocker.patch.object(
        focus_module,
        "processing_binding",
        return_value={"geometry": geometry_value},
    )
    metrics = SimpleNamespace(
        status="insufficient_tissue",
        reason="too little coherent support",
        model_dump=lambda **_kwargs: {"status": "insufficient_tissue"},
    )
    prepare = mocker.patch.object(
        thing,
        "_external_tissue_field",
        return_value=SimpleNamespace(metrics=metrics),
    )

    result = thing.precheck_scan_white(source)

    x, y, width, height = geometry_value["processing_roi"]
    cropped = prepare.call_args.args[0]
    assert cropped.shape == (height, width, 3)
    assert np.array_equal(cropped, source[y : y + height, x : x + width])
    assert result["status"] == "insufficient_tissue"
    assert result["source"] == "fresh_fast_ofm_lores_white"
    thing._cam.assert_not_called()
    thing._stage.assert_not_called()


def test_rejected_scan_precheck_runs_white_without_attempting_rg(
    autofocus_thing, mocker
):
    """A classified weak field archives the direct route and exactly one sweep."""
    thing, _position = autofocus_thing
    capture = mocker.patch.object(thing, "_capture_measurement")
    white_result = MagicMock()
    white_result.model_dump.return_value = {"sharpness": [3.0, 5.0]}
    thing._white_autofocus.fast_autofocus.return_value = white_result
    precheck = {
        "status": "no_tissue",
        "reason": "No coherent WHITE structure above the noise gate",
        "metrics": {"box_count": 0},
        "source": "fresh_fast_ofm_lores_white",
    }

    result = thing.autofocus_white_precheck_for_scan(
        white_dz=50,
        precheck=precheck,
    )

    assert result["status"] == "focused"
    assert result["kind"] == "autofocus_white_precheck"
    assert result["focus_method"] == "white_precheck"
    assert result["fallback_count"] == 1
    assert result["rg_attempted"] is False
    assert result["precheck"] == precheck
    capture.assert_not_called()
    thing._white_autofocus.fast_autofocus.assert_called_once_with(dz=50, start="centre")
    saved = json.loads(next(Path(thing.data_dir).glob("*/report.json")).read_text())
    assert saved["kind"] == "autofocus_white_precheck"
    assert saved["rg_attempted"] is False


def test_simultaneous_refusal_routes_directly_to_white_with_peripheral_telemetry(
    autofocus_thing,
):
    """One RG shot and one failed peripheral search remain one WHITE episode."""
    thing, _position = autofocus_thing
    white_result = MagicMock()
    white_result.model_dump.return_value = {"sharpness": [4.0, 6.0]}
    thing._white_autofocus.fast_autofocus.return_value = white_result

    result = thing.autofocus_white_after_simultaneous_refusal(
        white_dz=50,
        simultaneous_reason="Only 3 tissue windows passed",
        simultaneous_result_id="sim-result",
        simultaneous_report_ref="rg_simultaneous/sim-result/focus-report.json",
        separate_rg_unavailable_reason="Current JPEG policy is not commissioned",
        mixed_capture_count=1,
        peripheral_search={"attempted": True, "used": False, "status": "refused"},
    )

    assert result["status"] == "focused"
    assert result["kind"] == "autofocus_white_simultaneous_fallback"
    assert result["focus_method"] == "white_fallback"
    assert result["fallback_count"] == 1
    assert result["rg_attempted"] is True
    assert result["simultaneous_attempted"] is True
    assert result["mixed_capture_count"] == 1
    assert result["separate_rg_attempted"] is False
    assert result["peripheral_search"]["attempted"] is True
    assert result["peripheral_search"]["used"] is False
    assert result["simultaneous_result_id"] == "sim-result"
    assert "not commissioned" in result["separate_rg_unavailable_reason"]
    thing._white_autofocus.fast_autofocus.assert_called_once_with(dz=50, start="centre")
    saved = json.loads(next(Path(thing.data_dir).glob("*/report.json")).read_text())
    assert saved["peripheral_search"] == result["peripheral_search"]
    assert saved["mixed_capture_count"] == 1


def test_sparse_white_degradation_never_attempts_rg(autofocus_thing, mocker):
    """A model refusal sends exactly one WHITE sweep with no invented RG shot."""
    thing, _position = autofocus_thing
    capture = mocker.patch.object(thing, "_capture_measurement")
    thing._white_autofocus.fast_autofocus.return_value.model_dump.return_value = {}
    result = thing.autofocus_white_sparse_fallback_for_scan(
        white_dz=50, reason="No validated local model after three recovery probes"
    )
    assert result["status"] == "focused"
    assert result["focus_method"] == "white_fallback"
    assert result["fallback_count"] == 1
    assert result["sparse_degraded"] is True
    assert result["rg_attempted"] is False
    assert result["simultaneous_attempted"] is False
    assert result["mixed_capture_count"] == 0
    assert result["peripheral_search"] is None
    capture.assert_not_called()
    thing._white_autofocus.fast_autofocus.assert_called_once_with(dz=50, start="centre")


@pytest.mark.parametrize("mixed_capture_count", [0, 1])
def test_simultaneous_owner_handoff_persists_diagnostics_in_real_white_helper(
    autofocus_thing, mixed_capture_count
):
    """Verify the owner-to-helper contract, not just the wrapper's returned overlay."""
    from openflexure_microscope_server.things.focus.rg_simultaneous import (
        RGSimultaneous,
        SimultaneousFocusFallbackEligibleError,
    )
    from openflexure_microscope_server.things.scanning.scan_workflows import (
        _compact_focus_outcome,
    )

    thing, _position = autofocus_thing
    thing._white_autofocus.fast_autofocus.return_value.model_dump.return_value = {}
    peripheral = {
        "attempted": mixed_capture_count > 0,
        "used": False,
        "status": "refused",
        "evaluated_patch_count": 12 if mixed_capture_count else 0,
        "peripheral_patch_count": 6 if mixed_capture_count else 0,
        "central_anchor_count": 0,
        "stop_reason": "no central support",
        "elapsed_s": 0.2 if mixed_capture_count else 0.0,
    }
    error = SimultaneousFocusFallbackEligibleError(
        "insufficient windows",
        result_id="sim-refused",
        report_ref="rg_simultaneous/sim-refused/focus-report.json",
        peripheral_search=peripheral,
        mixed_capture_count=mixed_capture_count,
    )
    result = RGSimultaneous._white_after_refusal(
        SimpleNamespace(_rg_focus=thing), error, 50
    )
    saved = json.loads(next(Path(thing.data_dir).glob("*/report.json")).read_text())
    assert saved["peripheral_search"] == peripheral
    assert saved["mixed_capture_count"] == mixed_capture_count
    assert saved["fallback_count"] == 1
    outcome = _compact_focus_outcome(result)
    assert outcome["mixed_capture_count"] == mixed_capture_count
    assert outcome["rg_attempted"] is (mixed_capture_count > 0)
    assert outcome["peripheral_search"] == peripheral
    assert outcome["fallback_count"] == 1


@pytest.mark.parametrize("error", [ValueError("lost reference"), KeyboardInterrupt()])
def test_white_failure_preserves_original_exception_and_focus_report(
    autofocus_thing, error
):
    """Hardware/cancellation is still a hard stop, but its handoff is traceable."""
    thing, _position = autofocus_thing
    thing._white_autofocus.fast_autofocus.side_effect = error
    with pytest.raises(type(error)) as caught:
        thing.autofocus_white_after_simultaneous_refusal(
            white_dz=50,
            simultaneous_reason="insufficient windows",
            simultaneous_result_id="refused",
            simultaneous_report_ref="rg/refused.json",
            separate_rg_unavailable_reason="direct WHITE policy",
            mixed_capture_count=1,
            peripheral_search={"attempted": True, "used": False},
        )
    assert caught.value is error
    report = error.scan_focus_outcome
    assert report["status"] == "failed"
    assert report["mixed_capture_count"] == 1
    assert report["fallback_count"] == 1
    assert report["peripheral_search"] == {"attempted": True, "used": False}
    assert report["report_ref"].endswith("/report.json")


def test_non_measurement_failure_never_enters_white_fallback(autofocus_thing):
    """Invalid calibration/reference/camera state remains fail-closed."""
    thing, _position = autofocus_thing
    thing.checked_profile.side_effect = ValueError("candidate-only")
    with pytest.raises(ValueError, match="candidate-only"):
        thing.autofocus_with_white_fallback(prepared=True, white_dz=50)
    thing._white_autofocus.fast_autofocus.assert_not_called()


def test_timeout_before_capture_sends_no_hardware_action(autofocus_thing, mocker):
    """An impossible remaining capture budget fails before owner acquisition or move."""
    thing, _position = autofocus_thing
    thing.control_parameters = RGFocusControlSettings(
        timeout_s=1, minimum_capture_budget_s=1
    )
    capture = mocker.patch.object(thing, "_capture_measurement")
    times = iter([0.0, 1.0])
    mocker.patch(
        "openflexure_microscope_server.things.focus.rg_focus.time.monotonic",
        side_effect=lambda: next(times),
    )
    with pytest.raises(ValueError, match="capture budget"):
        thing.autofocus(prepared=True)
    capture.assert_not_called()
    thing._stage.move_absolute_in_segments.assert_not_called()


def test_initial_approach_consuming_capture_budget_stops_before_capture(
    autofocus_thing, mocker
):
    """A completed zero-net approach cannot spend the reserved pair budget."""
    thing, position = autofocus_thing
    capture = mocker.patch.object(thing, "_capture_measurement")
    times = iter([0.0, 0.0, 100.0])
    mocker.patch(
        "openflexure_microscope_server.things.focus.rg_focus.time.monotonic",
        side_effect=lambda: next(times),
    )
    with pytest.raises(ValueError, match="after initial final approach"):
        thing.autofocus(prepared=True)
    capture.assert_not_called()
    thing._stage.move_z_with_preload.assert_called_once_with(100, 8, 1)
    assert position == [10, 20, 100]


@pytest.mark.parametrize(
    "delay_phase", ["capture", "readback", "decision_archive", "final_success_archive"]
)
def test_post_capture_deadline_refuses_focused_and_preserves_evidence(
    autofocus_thing, mocker, delay_phase
):
    """A late pair, readback, decision or terminal archive cannot return focused."""
    thing, position = autofocus_thing
    now = [0.0]
    mocker.patch.object(focus_module.time, "monotonic", side_effect=lambda: now[0])
    captured = [False]

    def capture(_directory, _iteration):
        captured[0] = True
        if delay_phase == "capture":
            now[0] = 150.0
        return captured_pair(measurement(), "late-pair")

    def readback():
        if captured[0] and delay_phase == "readback":
            now[0] = 150.0
        return tuple(position)

    real_write = focus_module.write_json

    def write(path, value):
        real_write(path, value)
        if (
            delay_phase == "decision_archive"
            and value.get("status") is None
            and value.get("iterations")
            and value["iterations"][0]["decision"] is not None
        ) or (
            delay_phase == "final_success_archive" and value.get("status") == "focused"
        ):
            now[0] = 150.0

    mocker.patch.object(thing, "_capture_measurement", side_effect=capture)
    thing._stage.get_xyz_position.side_effect = readback
    mocker.patch.object(focus_module, "write_json", side_effect=write)
    with pytest.raises(ValueError, match="timeout expired"):
        thing.autofocus(prepared=True)
    report_path = next(Path(thing.data_dir).glob("*/report.json"))
    saved = json.loads(report_path.read_text())
    assert saved["status"] == "failed"
    assert saved["iterations"][0]["capture_id"] == "late-pair"
    assert saved["iterations"][0]["measurement"]["status"] == "ready"
    assert saved["iterations"][0]["decision"]["status"] == "focused"
    thing._stage.move_z_with_preload.assert_called_once_with(100, 8, 1)
    thing._stage.move_absolute_in_segments.assert_not_called()


def test_candidate_refusal_happens_before_capture(autofocus_thing, mocker):
    """An unavailable profile cannot be bypassed by the manual action boundary."""
    thing, _position = autofocus_thing
    thing.checked_profile.side_effect = ValueError("candidate-only")
    capture = mocker.patch.object(thing, "_capture_measurement")
    with pytest.raises(ValueError, match="candidate-only"):
        thing.autofocus(prepared=True)
    capture.assert_not_called()
    thing._stage.move_absolute_in_segments.assert_not_called()


def test_invalid_stage_reference_refuses_before_capture(autofocus_thing, mocker):
    """A valid optical model does not bypass the live controller reference gate."""
    thing, _position = autofocus_thing
    thing._stage.validate_path.side_effect = ValueError("fresh local zero required")
    capture = mocker.patch.object(thing, "_capture_measurement")
    with pytest.raises(ValueError, match="fresh local zero"):
        thing.autofocus(prepared=True)
    capture.assert_not_called()
    thing._stage.move_absolute_in_segments.assert_not_called()


def calibration_pair_driver(thing, position, mocker, *, reference_status="ready"):
    """Return synthetic signed pairs at the actually commanded relative Z."""
    for name, value in _demo_focus_contract().items():
        setattr(thing, name, value)
    field = SimpleNamespace(
        metrics=SimpleNamespace(status=reference_status),
        reference=SimpleNamespace(field_id="calibration", geometry_id="geometry"),
        mask=np.zeros((8, 8), dtype=bool),
        boxes=tuple(SimpleNamespace(patch_id=f"patch-{index}") for index in range(10)),
    )
    calls = []

    def capture(
        _directory,
        iteration,
        *,
        field_id=None,
        fixed_field=None,
        calculate_white_score=False,
        measurement_policy=None,
        persist_colour_frames=False,
    ):
        assert persist_colour_frames is True
        z_um = position[2] - 100
        calls.append(
            (
                iteration,
                z_um,
                field_id,
                fixed_field,
                calculate_white_score,
                measurement_policy,
            )
        )
        return CapturedRGFocusPair(
            measurement=measurement(dx=3.0 - z_um, dy=-0.5 + 0.1 * z_um),
            capture_id=f"capture-{iteration}",
            field=fixed_field or field,
            white_score=100.0 - abs(z_um),
            capture_path=f"/flat/capture-{iteration}",
            frame_ids=(
                f"capture-{iteration}:white",
                f"capture-{iteration}:red",
                f"capture-{iteration}:green",
            ),
        )

    mocker.patch.object(thing, "_capture_pair", side_effect=capture)

    def fit_in_process_for_orchestration_test(**values):
        values.pop("directory")
        return fit_rg_focus_model(**values)

    mocker.patch.object(
        thing,
        "_external_calibration_fit",
        side_effect=fit_in_process_for_orchestration_test,
    )
    mocker.patch(
        "openflexure_microscope_server.things.focus.rg_focus.focus_binding",
        return_value={},
    )
    mocker.patch.object(
        RGFocus,
        "readiness",
        property(
            lambda _self: {
                "calibration_ready": True,
                "snapshot_epoch": "test-readiness",
            }
        ),
    )
    return calls


def test_calibration_action_runs_fixed_plan_and_saves_only_validated_profile(
    autofocus_thing, mocker
):
    """The Settings action acquires exactly one bounded plan and returns to start."""
    thing, position = autofocus_thing
    calls = calibration_pair_driver(thing, position, mocker)
    result = thing.calibrate(prepared=True)
    assert result["status"] == "valid"
    assert result["returned_to_commanded_start"] is True
    assert len(result["stationary_pairs"]) == 2
    assert len(result["captures"]) == 20
    assert len(calls) == 22
    assert calls[0][3] is None
    assert calls[1][3] is not None
    assert all(call[3] is calls[1][3] for call in calls[1:])
    assert all(call[2] == result["id"] for call in calls)
    assert all(call[4] is True for call in calls)
    assert all(call[5] == thing.measurement_parameters for call in calls)
    assert [call[1] for call in calls[:3]] == [0, 0, 0]
    assert [call[1] for call in calls[3:]] == [
        -8,
        12,
        32,
        4,
        -20,
        -32,
        -16,
        -4,
        0,
        8,
        0,
        -12,
        16,
        24,
        28,
        20,
        -24,
        -28,
        0,
    ]
    assert min(entry["offset_um"] for entry in result["moves"]) == -32
    assert max(entry["offset_um"] for entry in result["moves"]) == 32
    assert min(result["planned_z_path_um"]) == -42
    assert max(result["planned_z_path_um"]) == 32
    assert [entry["offset_um"] for entry in result["moves"]] == [
        -8,
        12,
        32,
        4,
        -20,
        -32,
        -16,
        -4,
        8,
        0,
        8,
        -8,
        0,
        -12,
        16,
        24,
        28,
        20,
        -24,
        -28,
        0,
    ]
    approach_returns = [result["moves"][9], result["moves"][12]]
    assert all(entry["z_path_units"] == [90, 100] for entry in approach_returns)
    assert all(entry["target_units"][:2] == [10, 20] for entry in result["moves"])
    assert position == [10, 20, 100]
    assert thing.profile["status"] == "valid"
    assert thing.profile["applicable_z_range_um"] == [-32.0, 32.0]
    assert thing.measurement_policy_confirmation["state"] == "commissioned"
    assert result["commissioning"]["persisted"] is True
    assert result["commissioning"]["evidence"]["saved_jpeg_replay"] == {
        "comparison": "exact",
        "tolerance": 0,
    }
    report_path = Path(thing.data_dir) / result["data_path"] / "report.json"
    assert report_path.is_file()
    assert json.loads(report_path.read_text())["expected_pair_count"] == 22


def test_calibration_bad_reference_preserves_profile_and_sends_no_move(
    autofocus_thing, mocker
):
    """A rejected fresh WHITE field stops before the first calibration move."""
    thing, position = autofocus_thing
    previous = {"id": "previous", "status": "candidate"}
    thing.profile = previous
    calls = calibration_pair_driver(
        thing, position, mocker, reference_status="no_tissue"
    )
    with pytest.raises(ValueError, match="Stationary commissioning failed"):
        thing.calibrate(prepared=True)
    assert len(calls) == 2
    thing._stage.move_absolute_in_segments.assert_not_called()
    assert position == [10, 20, 100]
    assert thing.profile == previous
    thing._stage.move_z_with_preload.assert_not_called()
    reports = sorted(Path(thing.data_dir).glob("*/report.json"))
    failure = json.loads(reports[-1].read_text())
    assert failure["returned_to_commanded_start"] is True
    assert failure["failed_phase"] == "stationary_readback"


def test_stationary_commissioning_save_failure_rolls_back_before_motion(
    autofocus_thing, mocker
):
    """Two passing pairs cannot authorize motion if atomic evidence save fails."""
    thing, position = autofocus_thing
    previous_profile = {"id": "previous", "status": "candidate"}
    previous_confirmation = {"state": "draft_uncommissioned"}
    thing.profile = previous_profile
    thing.measurement_policy_confirmation = previous_confirmation
    calls = calibration_pair_driver(thing, position, mocker)
    mocker.patch.object(
        thing, "save_settings", side_effect=OSError("commissioning persistence failed")
    )
    with pytest.raises(OSError, match="commissioning persistence failed"):
        thing.calibrate(prepared=True)
    assert len(calls) == 2
    assert thing.measurement_policy_confirmation == previous_confirmation
    assert thing.profile == previous_profile
    assert position == [10, 20, 100]
    thing._stage.move_absolute_in_segments.assert_not_called()
    thing._stage.move_z_with_preload.assert_not_called()


def test_unsafe_preload_refuses_before_reference_or_motion(autofocus_thing, mocker):
    """Validate intermediate endpoints before even starting a calibration pair."""
    thing, _position = autofocus_thing
    thing.approach_parameters = RGFocusApproachSettings(preload_um=16)
    capture = mocker.patch.object(thing, "_capture_pair")
    mocker.patch(
        "openflexure_microscope_server.things.focus.rg_focus.focus_binding",
        return_value={},
    )
    mocker.patch.object(
        RGFocus,
        "readiness",
        property(
            lambda _self: {
                "calibration_ready": True,
                "snapshot_epoch": "test-readiness",
            }
        ),
    )
    with pytest.raises(ValueError, match="preload exceeds"):
        thing.calibrate(prepared=True)
    capture.assert_not_called()
    thing._stage.move_z_with_preload.assert_not_called()
    thing._stage.move_absolute_in_segments.assert_not_called()


@pytest.mark.parametrize("error_um", [-5, 5])
def test_exact_demo_corrects_both_signs_with_independent_preload_clearance(
    autofocus_thing, mocker, error_um
):
    """A ten-micron net budget permits both signs inside the real clearance."""
    thing, position = autofocus_thing
    contract = _demo_focus_contract()
    thing.control_parameters = contract["control_parameters"]
    thing.approach_parameters = contract["approach_parameters"]
    mocker.patch.object(
        thing,
        "_capture_measurement",
        side_effect=[
            captured_pair(
                measurement(dx=3.0 - error_um, dy=-0.5 + 0.1 * error_um), "first"
            ),
            captured_pair(measurement(), "verified"),
        ],
    )
    result = thing.autofocus(prepared=True)
    target = 100 - error_um
    assert result["status"] == "focused"
    assert result["total_correction_um"] == 5
    assert len(result["iterations"]) == 2
    assert position == [10, 20, target]
    assert result["iterations"][0]["z_path_units"] == [target - 10, target]
    thing._stage.validate_path.assert_any_call([{"z": target - 10}, {"z": target}])
    assert thing._stage.move_z_with_preload.call_args_list == [
        mocker.call(100, 10, 1),
        mocker.call(target, 10, 1),
    ]


def test_af_endpoint_fits_but_correction_preload_does_not(autofocus_thing, mocker):
    """The separate excursion limit covers the initial and correction preload paths."""
    thing, _position = autofocus_thing
    thing.control_parameters = RGFocusControlSettings(
        maximum_total_correction_um=8, maximum_absolute_z_excursion_um=8
    )
    mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(dx=-1.0, dy=-0.1)),
    )
    with pytest.raises(ValueError, match="preload exceeds"):
        thing.autofocus(prepared=True)
    thing._stage.move_z_with_preload.assert_called_once_with(100, 8, 1)
    thing._stage.move_absolute_in_segments.assert_not_called()


def test_exact_demo_keeps_hardware_boundary_on_preload_endpoint(
    autofocus_thing, mocker
):
    """A safe net endpoint never bypasses the stage's actual physical boundary."""
    thing, _position = autofocus_thing
    thing.control_parameters = _demo_focus_contract()["control_parameters"]

    def validate(path):
        if any(point["z"] < 90 for point in path):
            raise ValueError("physical Z boundary")

    thing._stage.validate_path.side_effect = validate
    mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(dx=-2.0, dy=0.0)),
    )
    with pytest.raises(ValueError, match="physical Z boundary"):
        thing.autofocus(prepared=True)
    thing._stage.validate_path.assert_any_call([{"z": 87}, {"z": 95}])
    thing._stage.move_z_with_preload.assert_called_once_with(100, 8, 1)
    thing._stage.move_absolute_in_segments.assert_not_called()


def test_external_z_change_during_capture_stops_correction(autofocus_thing, mocker):
    """Do not accept measurements at a position changed outside this action."""
    thing, position = autofocus_thing

    def capture(*_args):
        position[2] += 1
        return captured_pair(measurement(dx=-1.0, dy=-0.1))

    mocker.patch.object(thing, "_capture_measurement", side_effect=capture)
    with pytest.raises(ValueError, match="XYZ changed"):
        thing.autofocus(prepared=True)
    thing._stage.move_z_with_preload.assert_called_once_with(100, 8, 1)


def test_ambiguous_preload_is_not_retried(autofocus_thing, mocker):
    """An uncertain first leg cannot be turned into a second move or a retry."""
    thing, _position = autofocus_thing
    capture = mocker.patch.object(
        thing,
        "_capture_measurement",
        return_value=captured_pair(measurement(dx=-1.0, dy=-0.1)),
    )
    thing._stage.move_z_with_preload.side_effect = TimeoutError("uncertain move")
    with pytest.raises(TimeoutError, match="uncertain"):
        thing.autofocus(prepared=True)
    assert thing._stage.move_z_with_preload.call_count == 1
    capture.assert_not_called()
