"""Runtime focus-surface integration without Pi hardware."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from labthings_fastapi.exceptions import InvocationCancelledError
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.focus_scan import (
    FocusExperimentEnvelope,
    FocusScanSafetyError,
    FocusScanSession,
    FocusScanSettings,
    FrozenRGFocusSettings,
)
from openflexure_microscope_server.focus.focus_surface import (
    FocusObservation,
    FocusObservationProvenance,
    FocusObservationQuality,
    FocusRunSettings,
)
from openflexure_microscope_server.focus.rg.rg_focus_control import (
    RGFocusControlSettings,
)
from openflexure_microscope_server.focus.rg.rg_focus_model import (
    RGFocusMeasurementPolicy,
    RGFocusModelSettings,
)
from openflexure_microscope_server.scanning.scan_directories import ScanDirectory
from openflexure_microscope_server.things.focus.rg_focus import (
    CapturedRGFocusPair,
    RGFocus,
)

from .focus_surface_process_stub import external_focus_prediction
from .rg_decision_process_stub import external_rg_decision
from .rg_model_process_stub import external_profile_validation, fit_rg_focus_model
from .test_focus_approach import binding as approach_binding
from .test_focus_approach import focus_stage as _focus_stage_fixture
from .test_focus_surface import surface_settings, white_settings
from .test_rg_focus_control import calibration_point
from .test_rg_focus_control import measurement as rg_measurement
from .test_rg_focus_model import (
    empirical_profile,
    shifted_white_profile,
    stationary_observations,
)


@pytest.fixture
def focus_runtime_stage(mocker):
    """Reuse the accepted real-stage/HTTP-fake boundary under a local fixture name."""
    yield from _focus_stage_fixture.__wrapped__(mocker)


def valid_profile():
    """Return a small valid model with independent two-sided approach evidence."""
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
        measurement_policy=RGFocusMeasurementPolicy(
            measurement_domain="processed-jpeg-rgb8"
        ),
    )


def enabled_settings(stage_binding, *, white=False, maximum_candidates=4):
    """Build explicit test-only limits; none are production commissioning values."""
    surface = surface_settings(
        maximum_neighbors=4,
        maximum_candidate_observations=maximum_candidates,
    )
    run = FocusRunSettings(
        autofocus_method="led",
        focus_strategy="single_autofocus",
        surface=surface,
        unpredicted_focus_mode="white_then_led" if white else "selected_method",
        white_search=(
            white_settings(
                search_z_range_um=(-12, 8),
                white_search_timeout_s=300,
                total_focus_budget_s=500,
                approach_and_rg_reserve_s=120,
                maximum_white_led_disagreement_um=3,
            )
            if white
            else None
        ),
        binding=stage_binding,
    )
    return FocusScanSettings(
        run=run,
        expected_prediction_error_range_um=(-2, 2),
        maximum_field_elapsed_s=600,
        maximum_field_z_travel_um=500,
        post_move_verification_reserve_s=30,
        experiment_envelope=FocusExperimentEnvelope(
            x_um=(-500, 500), y_um=(-500, 500), z_um=(-50, 50)
        ),
    )


def frozen(stage_binding):
    """Return one immutable R/G snapshot matched to the test binding."""
    return FrozenRGFocusSettings(
        profile=valid_profile(),
        measurement_policy=valid_profile().measurement_policy,
        control=RGFocusControlSettings(),
        binding=stage_binding,
        effective_focus_tolerance_um=external_profile_validation(
            valid_profile(),
            RGFocusControlSettings().focus_tolerance_um,
        )[1],
    )


def memory_writer(records):
    """Return a JSON-valid evidence sink with the production call shape."""

    def write(path, value):
        records[path] = json.loads(json.dumps(dict(value), allow_nan=False))

    return write


def confirmed_observation(scan_id, stage_binding, *, observation_id="observation-1"):
    """Create one recent final LED point to seed a mapped next field."""
    now = time.monotonic()
    return FocusObservation(
        scan_id=scan_id,
        field_id="prior-field",
        attempt_id="prior-attempt",
        observation_id=observation_id,
        x_um=0,
        y_um=0,
        z_um=0,
        final_readback_settled=True,
        final_readback_monotonic_s=now - 0.01,
        recorded_monotonic_s=now,
        source="led_autofocus",
        status="confirmed",
        binding=stage_binding,
        provenance=FocusObservationProvenance(
            result_id="result-1",
            report_ref="rg_focus/result-1/report.json",
            rg_measurement_id="pair-1",
            capture_ids=("red-1", "green-1"),
        ),
        quality=FocusObservationQuality(
            measurement_status="ready",
            confidence=0.9,
            accepted_patch_count=8,
            inlier_fraction=0.8,
            final_error_um=0.1,
            focus_tolerance_um=1.5,
            independent_post_move_verification=True,
        ),
    )


def native_white_session(
    stage,
    mocker,
    tmp_path,
    *,
    white=True,
    field_z_budget=500,
    profile_override=None,
    control_override=None,
):
    """Use the native RG loop and real HTTP-fake stage around synthetic optics."""
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    captured = frozen(stage_binding)
    if profile_override is not None:
        captured = captured.model_copy(
            update={
                "profile": profile_override,
                "measurement_policy": profile_override.measurement_policy,
            }
        )
    if control_override is not None:
        captured = captured.model_copy(update={"control": control_override})
    effective_focus_tolerance_um = external_profile_validation(
        captured.profile,
        captured.control.focus_tolerance_um,
    )[1]
    captured = captured.model_copy(
        update={
            "effective_focus_tolerance_um": effective_focus_tolerance_um,
        }
    )
    rg_focus = create_thing_without_server(RGFocus, mock_all_slots=True)
    rg_focus._data_dir = str(tmp_path)
    mocker.patch.object(RGFocus, "_stage", property(lambda _self: stage))
    mocker.patch.object(rg_focus, "checked_profile", return_value=captured.profile)
    mocker.patch.object(
        rg_focus,
        "_external_profile_validation",
        side_effect=external_profile_validation,
    )
    rg_focus.measurement_parameters = captured.measurement_policy
    rg_focus.control_parameters = captured.control
    rg_focus.approach_parameters = captured.binding.approach_parameters
    mocker.patch.object(
        rg_focus, "_external_decision", side_effect=external_rg_decision
    )
    mocker.patch.object(
        rg_focus, "external_focus_prediction", side_effect=external_focus_prediction
    )
    mocker.patch.object(
        rg_focus,
        "prepare_tissue_field_for_scan",
        return_value={
            "id": "tissue-1",
            "status": "ready",
            "reason": "test",
            "frame_id": "white-1",
            "report_ref": "tissue/report.json",
        },
    )
    autofocus = MagicMock()

    def white_driver(*, dz, start, before_move, after_move=None):
        assert dz > 0
        assert start == "base"
        before_move("white_test_peak")
        x, y, _z = stage.get_xyz_position()
        stage.move_absolute_in_segments((x, y, 0))
        if after_move is not None:
            after_move("white_test_peak")
        return SimpleNamespace(model_dump=lambda **_kwargs: {"fixture_peak_z": 0})

    autofocus.fast_autofocus_bounded.side_effect = white_driver
    records = {}
    settings = enabled_settings(stage_binding, white=white).model_copy(
        update={"maximum_field_z_travel_um": field_z_budget}
    )
    session = FocusScanSession(
        scan_id="native-scan",
        settings=settings,
        stage=stage,
        rg_focus=rg_focus,
        autofocus=autofocus,
        frozen_rg=captured,
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer(records),
    )
    return session, rg_focus, records


def status_session(stage, writer=None):
    """Create a selected-method session for bounded presentation-only assertions."""
    snap = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snap, rg_focus_model_id="profile")
    records = {}
    return (
        FocusScanSession(
            scan_id="status-scan",
            settings=enabled_settings(stage_binding),
            stage=stage,
            rg_focus=MagicMock(),
            autofocus=MagicMock(),
            frozen_rg=frozen(stage_binding),
            current_binding=lambda: stage_binding,
            surface_predictor=external_focus_prediction,
            evidence_writer=writer or memory_writer(records),
        ),
        records,
    )


def test_old_contract_is_off_selected_method_and_active_limits_are_mandatory():
    """Old JSON remains inert; enabled JSON cannot infer budgets or uncertainty."""
    old = FocusScanSettings.model_validate({})
    assert old.run.surface.enabled is False
    assert old.run.unpredicted_focus_mode == "selected_method"
    with pytest.raises(ValidationError, match="field budget"):
        FocusScanSettings(
            run=FocusRunSettings(
                autofocus_method="led",
                focus_strategy="single_autofocus",
                surface=surface_settings(),
                binding=approach_binding(),
            )
        )


def test_runtime_summary_separates_measured_focused_captured_and_completion(
    focus_runtime_stage,
):
    """Only published lifecycle transitions increment their distinct counters."""
    stage, _controller = focus_runtime_stage
    session, _records = status_session(stage)
    field = session.prepare_field((0, 0))
    planned = session.runtime_summary
    assert planned.path == "selected_method"
    assert planned.outcome == "planned"
    assert planned.predicted_z_um is None
    assert planned.counters.fields_planned == 1

    field.mark_measured(
        iteration=1,
        capture_id="pair-1",
        frame_ids=("white-1", "red-1", "green-1"),
        measurement_status="ready",
    )
    measured = session.runtime_summary
    assert measured.stage == "measured"
    assert measured.measured_z_um == 0
    assert measured.counters.fields_focused == 0

    field.stage = "focused"
    session.record(field, "focused", {"final_z_um": 1.25})
    focused = session.runtime_summary
    assert focused.outcome == "focused"
    assert focused.final_z_um == 1.25
    assert focused.counters.fields_focused == 1
    assert focused.counters.fields_captured == 0

    session.finish_field(field, imaged=True)
    session.complete()
    completed = session.runtime_summary
    assert completed.scan_state == "completed"
    assert completed.outcome == "captured"
    assert completed.counters.fields_focused == 1
    assert completed.counters.fields_captured == 1


def test_runtime_summary_keeps_no_tissue_distinct_when_white_tile_is_saved(
    focus_runtime_stage,
):
    """A keep_z tile is captured but never relabelled as focused tissue."""
    stage, _controller = focus_runtime_stage
    session, _records = status_session(stage)
    field = session.prepare_field((0, 0))
    field.no_tissue = True
    field.stage = "no_tissue"
    session.record(field, "no_tissue", {"policy": "keep_z"})
    session.finish_field(field, imaged=True)

    summary = session.runtime_summary
    assert summary.outcome == "no_tissue"
    assert summary.counters.fields_no_tissue == 1
    assert summary.counters.fields_captured == 1
    assert summary.counters.fields_focused == 0


@pytest.mark.parametrize(
    ("result_id", "exception", "outcome", "counter"),
    [
        ("rg-result", RuntimeError("R/G QC failed"), "failed", "fields_failed"),
        (None, RuntimeError("R/G outcome lost"), "unknown", "fields_unknown"),
        (None, InvocationCancelledError(), "cancelled", "fields_cancelled"),
    ],
)
def test_runtime_summary_reports_terminal_focus_outcomes_once(
    focus_runtime_stage, result_id, exception, outcome, counter
):
    """Failure, ambiguity and cancellation have disjoint labels and counters."""
    stage, _controller = focus_runtime_stage
    session, _records = status_session(stage)
    field = session.prepare_field((0, 0))
    if outcome in ("failed", "unknown"):
        field.record_rg_failure(result_id, exception)
    session.stop(field, exception)

    summary = session.runtime_summary
    assert summary.scan_state == "stopped"
    assert summary.outcome == outcome
    assert getattr(summary.counters, counter) == 1
    assert (
        sum(
            (
                summary.counters.fields_failed,
                summary.counters.fields_unknown,
                summary.counters.fields_cancelled,
            )
        )
        == 1
    )


def test_failed_journal_publish_never_claims_focused_or_captured(
    focus_runtime_stage,
):
    """A failed checkpoint leaves the attempted success unknown and uncounted."""
    stage, _controller = focus_runtime_stage
    records = {}
    fail = False

    def writer(path, value):
        if fail:
            raise OSError("test-only journal failure")
        records[path] = json.loads(json.dumps(dict(value), allow_nan=False))

    session, _unused = status_session(stage, writer)
    field = session.prepare_field((0, 0))
    field.stage = "focused"
    fail = True
    with pytest.raises(OSError, match="test-only journal failure"):
        session.record(field, "focused", {"final_z_um": 2.0})

    summary = session.runtime_summary
    assert summary.scan_state == "stopped"
    assert summary.outcome == "unknown"
    assert summary.counters.fields_unknown == 1
    assert summary.counters.fields_focused == 0
    assert summary.counters.fields_captured == 0


def test_atomic_focus_archive_is_separate_and_rejects_escape(tmp_path):
    """Field evidence is replace-safe and cannot alter gallery/database paths."""
    scan = ScanDirectory.new_scan_dir("scan", str(tmp_path))
    scan.save_focus_evidence("events/0001.json", {"stage": "planned"})
    path = Path(scan.dir_path) / "focus/events/0001.json"
    assert json.loads(path.read_text()) == {"stage": "planned"}
    assert not list(path.parent.glob("tmp*"))
    with pytest.raises(ValueError, match="inside"):
        scan.save_focus_evidence("../scan_data.json", {"bad": True})


def test_scan_policy_translates_model_interval_around_white_target(focus_runtime_stage):
    """The scan error interval changes origin without extending measured positions."""
    stage, _controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    profile = shifted_white_profile(2)
    captured = FrozenRGFocusSettings(
        profile=profile,
        measurement_policy=profile.measurement_policy,
        control=RGFocusControlSettings(),
        binding=stage_binding,
        effective_focus_tolerance_um=external_profile_validation(
            profile,
            RGFocusControlSettings().focus_tolerance_um,
        )[1],
    )
    records = {}
    session = FocusScanSession(
        scan_id="shifted-target",
        settings=enabled_settings(stage_binding),
        stage=stage,
        rg_focus=MagicMock(),
        autofocus=MagicMock(),
        frozen_rg=captured,
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer(records),
    )
    assert session.frozen_rg.profile.focus_z_um == 2
    assert session.frozen_rg.profile.applicable_z_range_um == (-6, 6)
    assert session.policy.rg_applicable_error_range_um == (-8, 4)
    assert (
        FrozenRGFocusSettings.model_validate_json(captured.model_dump_json())
        == captured
    )


def test_mapped_next_xy_executes_accepted_stage_plan_and_bounds_ram(
    focus_runtime_stage,
):
    """The actual next XY reaches OF-066 through the real HTTP-fake MoonrakerStage."""
    stage, controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(
        snapshot,
        rg_focus_model_id="profile",
    )
    records = {}
    session = FocusScanSession(
        scan_id="scan-1",
        settings=enabled_settings(stage_binding),
        stage=stage,
        rg_focus=MagicMock(),
        autofocus=MagicMock(),
        frozen_rg=frozen(stage_binding),
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer(records),
    )
    for index in range(6):
        session.observations.append(
            confirmed_observation(
                "scan-1", stage_binding, observation_id=f"observation-{index}"
            )
        )
    assert len(session.observations) == 4
    field = session.prepare_field((10, -5))
    assert field.prediction.status == "usable"
    assert field.standard_transit is False
    assert field.prepared is not None
    assert stage.position == {"x": 10, "y": -5, "z": -3}
    assert controller.scripts
    assert all("M400" in script for script in controller.scripts)
    planned = [row for row in records.values() if row.get("event") == "field_planned"]
    assert planned[0]["target_stage_units"] == [10, -5]


@pytest.mark.parametrize(
    ("inferred_error_um", "purpose", "command_count"),
    [(-2.0, "direct_correction", 1), (2.0, "reapproach_correction", 2)],
)
def test_mapped_correction_uses_stage_owned_preparation(
    focus_runtime_stage, inferred_error_um, purpose, command_count
):
    """The runtime reaches OF-066 direct/reapproach paths without a second planner."""
    stage, controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    records = {}
    session = FocusScanSession(
        scan_id="scan-1",
        settings=enabled_settings(stage_binding),
        stage=stage,
        rg_focus=MagicMock(),
        autofocus=MagicMock(),
        frozen_rg=frozen(stage_binding),
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer(records),
    )
    for index in range(4):
        session.observations.append(
            confirmed_observation(
                "scan-1", stage_binding, observation_id=f"observation-{index}"
            )
        )
    field = session.prepare_field((10, -5))
    assert field.prepared is not None
    commands_before = len(controller.scripts)
    updated = session.plan_correction(field, inferred_error_um)
    assert updated is field.prepared
    assert len(controller.scripts) - commands_before == command_count
    plans = [
        row["details"]["plan"]
        for row in records.values()
        if row.get("event") == "correction_planned"
    ]
    assert plans[-1]["purpose"] == purpose
    assert plans[-1]["requires_rg_verification"] is True


def test_mapped_scan_correction_uses_one_pair_without_training_surface(
    focus_runtime_stage, mocker, tmp_path
):
    """A bounded mapped correction saves the tile but not a synthetic map point."""
    stage, _controller = focus_runtime_stage
    session, rg_focus, records = native_white_session(
        stage, mocker, tmp_path, white=False
    )
    binding = session.frozen_rg.binding
    for index in range(4):
        session.observations.append(
            confirmed_observation(
                session.scan_id,
                binding,
                observation_id=f"observation-{index}",
            )
        )
    field = session.prepare_field((10, -5))
    assert field.prediction.status == "usable"
    capture = mocker.patch.object(
        rg_focus,
        "_capture_pair",
        return_value=CapturedRGFocusPair(
            measurement=rg_measurement(dx=6, dy=-0.8),
            capture_id="mapped-pair",
            field=SimpleNamespace(),
            white_score=100,
            capture_path="/flat/mapped-pair",
            frame_ids=("mapped-white", "mapped-red", "mapped-green"),
        ),
    )

    result = rg_focus.autofocus_for_scan(field)

    assert result["status"] == "focused"
    assert result["verification_mode"] == "single_pair_model"
    assert result["independent_post_move_verification"] is False
    assert len(result["iterations"]) == 1
    assert capture.call_count == 1
    tampered = json.loads(json.dumps(result))
    tampered["iterations"][0]["target_units"][2] += 1
    with pytest.raises(FocusScanSafetyError, match="final stage readback"):
        session.validate_rg_result(field, tampered)
    field.validate_rg_result(result)
    field.accept_rg_result()
    assert field.stage == "focused"
    assert len(session.observations) == 4
    candidates = [
        row for path, row in records.items() if path.startswith("observations/")
    ]
    assert len(candidates) == 1
    assert candidates[0]["status"] == "candidate"
    focused = [row for row in records.values() if row.get("event") == "focused"]
    assert focused[-1]["details"]["verification_mode"] == "single_pair_model"
    assert focused[-1]["details"]["surface_update"] is False


def test_unpredicted_scan_correction_uses_one_pair_without_training_surface(
    focus_runtime_stage, mocker, tmp_path
):
    """The demo seed uses the modelled residual but cannot train the surface."""
    stage, _controller = focus_runtime_stage
    session, rg_focus, records = native_white_session(
        stage, mocker, tmp_path, white=False
    )
    field = session.prepare_field((10, -5))
    assert field.prediction.status == "unavailable"
    capture = mocker.patch.object(
        rg_focus,
        "_capture_pair",
        return_value=CapturedRGFocusPair(
            measurement=rg_measurement(dx=6, dy=-0.8),
            capture_id="seed-pair",
            field=SimpleNamespace(),
            white_score=100,
            capture_path="/flat/seed-pair",
            frame_ids=("seed-white", "seed-red", "seed-green"),
        ),
    )

    result = rg_focus.autofocus_for_scan(field)

    assert result["status"] == "focused"
    assert result["verification_mode"] == "single_pair_model"
    assert result["independent_post_move_verification"] is False
    assert len(result["iterations"]) == 1
    assert capture.call_count == 1
    field.validate_rg_result(result)
    field.accept_rg_result()
    assert not session.observations
    candidates = [
        row for path, row in records.items() if path.startswith("observations/")
    ]
    assert len(candidates) == 1
    assert candidates[0]["status"] == "candidate"
    assert candidates[0]["pre_update_prediction"] is None
    focused = [row for row in records.values() if row.get("event") == "focused"]
    assert focused[-1]["details"]["verification_mode"] == "single_pair_model"
    assert focused[-1]["details"]["surface_update"] is False


def test_actual_white_peak_handoff_owns_preparation_without_second_preload(
    focus_runtime_stage, mocker, tmp_path
):
    """WHITE peak -11→-3 proof permits the native direct -3→0 correction."""
    stage, controller = focus_runtime_stage
    session, rg_focus, _records = native_white_session(stage, mocker, tmp_path)
    field = session.prepare_field((10, 0))
    assert field.prepared is not None
    assert stage._prepared_approach == field.prepared
    assert stage.get_xyz_position() == (10, 0, -3)
    before = len(controller.scripts)
    capture = mocker.patch.object(
        rg_focus,
        "_capture_pair",
        side_effect=[
            CapturedRGFocusPair(
                measurement=rg_measurement(dx=6, dy=-0.8),
                capture_id="first",
                field=SimpleNamespace(),
                white_score=100,
                capture_path="/flat/first",
                frame_ids=("first-white", "first-red", "first-green"),
            ),
            CapturedRGFocusPair(
                measurement=rg_measurement(),
                capture_id="verify",
                field=SimpleNamespace(),
                white_score=100,
                capture_path="/flat/verify",
                frame_ids=("verify-white", "verify-red", "verify-green"),
            ),
        ],
    )
    result = rg_focus.autofocus_for_scan(field)
    assert result["status"] == "focused"
    assert capture.call_count == 1
    scripts = controller.scripts[before:]
    assert len(scripts) == 1
    assert "Z0.000000" in scripts[0]
    assert not any("Z0.008000" in script for script in scripts)


@pytest.mark.parametrize("prepared", [False, True])
def test_scan_executes_small_correction_with_frozen_empirical_budget(
    focus_runtime_stage, mocker, tmp_path, prepared
):
    """Both initial and prepared paths execute the move needed by the same residual budget."""
    stage, controller = focus_runtime_stage
    session, rg_focus, _records = native_white_session(
        stage,
        mocker,
        tmp_path,
        white=prepared,
        profile_override=empirical_profile(),
        control_override=RGFocusControlSettings(
            focus_tolerance_um=2, maximum_iterations=2
        ),
    )
    field = session.prepare_field((10, 0))
    assert (field.prepared is not None) is prepared
    initial_z = stage.get_xyz_position()[2]
    before = len(controller.scripts)
    rg_focus.control_parameters = rg_focus.control_parameters.model_copy(
        update={"focus_tolerance_um": 4.0}
    )
    calls = []

    def capture(_directory, iteration, **_kwargs):
        calls.append(iteration)
        error = 1.0 if iteration == 1 else 0.0
        return CapturedRGFocusPair(
            measurement=rg_measurement(dx=4.25 - 0.8 * error, dy=-1.5 + 0.1 * error),
            capture_id=f"pair-{iteration}",
            field=SimpleNamespace(),
            white_score=100,
            capture_path=f"/flat/pair-{iteration}",
            frame_ids=(f"white-{iteration}", f"red-{iteration}", f"green-{iteration}"),
        )

    mocker.patch.object(rg_focus, "_capture_pair", side_effect=capture)
    result = rg_focus.autofocus_for_scan(field)
    assert result["status"] == "focused"
    assert calls == [1]
    assert result["effective_residual_tolerance_um"] == pytest.approx(0.8)
    assert session.policy.focus_tolerance_um == pytest.approx(0.8)
    assert rg_focus.control_parameters.focus_tolerance_um == 4
    assert stage.get_xyz_position()[2] == initial_z - 1
    assert len(controller.scripts) > before
    assert result["total_correction_um"] == 1


def test_exhausted_empirical_budget_stops_scan_before_field_or_preload(
    focus_runtime_stage, mocker, tmp_path
):
    """No preparation path exists when the frozen model has no residual budget."""
    stage, controller = focus_runtime_stage
    profile = empirical_profile()
    with pytest.raises(ValueError, match="exhausts"):
        native_white_session(
            stage,
            mocker,
            tmp_path,
            profile_override=profile,
            control_override=RGFocusControlSettings(
                focus_tolerance_um=profile.empirical_prediction_error_um
            ),
        )
    assert not controller.scripts


def test_native_rg_uses_frozen_control_after_next_run_edit(
    focus_runtime_stage, mocker, tmp_path
):
    """A form edit is preserved for the next run but cannot alter this scan."""
    stage, _controller = focus_runtime_stage
    session, rg_focus, _records = native_white_session(stage, mocker, tmp_path)
    field = session.prepare_field((10, 0))
    calls = []

    def capture(_directory, iteration, **_kwargs):
        calls.append(iteration)
        if iteration == 1:
            rg_focus.control_parameters = rg_focus.control_parameters.model_copy(
                update={"maximum_total_correction_um": 20.0}
            )
        return CapturedRGFocusPair(
            measurement=(
                rg_measurement(dx=6, dy=-0.8) if iteration == 1 else rg_measurement()
            ),
            capture_id=f"pair-{iteration}",
            field=SimpleNamespace(),
            white_score=100,
            capture_path=f"/flat/pair-{iteration}",
            frame_ids=(f"white-{iteration}", f"red-{iteration}", f"green-{iteration}"),
        )

    mocker.patch.object(rg_focus, "_capture_pair", side_effect=capture)
    result = rg_focus.autofocus_for_scan(field)
    assert result["status"] == "focused"
    assert calls == [1]
    assert result["parameters"]["maximum_total_correction_um"] == 32
    assert rg_focus.control_parameters.maximum_total_correction_um == 20


def test_selected_method_initial_preload_refuses_field_travel_before_post(
    focus_runtime_stage, mocker, tmp_path
):
    """Enabled selected method preflights 0→-12→-4 against the field budget."""
    stage, controller = focus_runtime_stage
    session, rg_focus, _records = native_white_session(
        stage, mocker, tmp_path, white=False, field_z_budget=10
    )
    field = session.prepare_field((0, 0))
    mocker.patch.object(
        rg_focus,
        "_capture_pair",
        return_value=CapturedRGFocusPair(
            measurement=rg_measurement(dx=-1, dy=-0.1),
            capture_id="first",
            field=SimpleNamespace(),
            white_score=100,
            capture_path="/flat/first",
            frame_ids=("white", "red", "green"),
        ),
    )
    with pytest.raises(FocusScanSafetyError, match="travel"):
        rg_focus.autofocus_for_scan(field)
    assert not controller.scripts


@pytest.mark.parametrize("effect", ["safe_xy_transit_1", "white_led_full_approach"])
def test_motion_checkpoint_latency_refuses_before_native_post(
    focus_runtime_stage, mocker, tmp_path, effect
):
    """A mandatory planned write cannot age past deadline and still admit motion."""
    stage, controller = focus_runtime_stage
    clock = [time.monotonic()]
    mocker.patch(
        "openflexure_microscope_server.focus.focus_scan.time.monotonic",
        side_effect=lambda: clock[0],
    )
    session, _rg_focus, _records = native_white_session(stage, mocker, tmp_path)
    original_write = session._write
    command_count = []

    def delayed_write(path, value):
        original_write(path, value)
        if (
            path.startswith("events/")
            and value.get("event") == "effect_planned"
            and value.get("details", {}).get("effect") == effect
        ):
            command_count.append(len(controller.scripts))
            clock[0] += 650

    session._write = delayed_write
    with pytest.raises(FocusScanSafetyError, match="deadline|reserve"):
        session.prepare_field((10, 0))
    assert len(command_count) == 1
    assert len(controller.scripts) == command_count[0]


def test_white_total_deadline_counts_slow_tissue_before_search(
    focus_runtime_stage, mocker, tmp_path
):
    """Slow confirmed tissue work cannot rejuvenate the total WHITE-to-R/G budget."""
    stage, controller = focus_runtime_stage
    clock = [time.monotonic()]
    mocker.patch(
        "openflexure_microscope_server.focus.focus_scan.time.monotonic",
        side_effect=lambda: clock[0],
    )
    session, rg_focus, _records = native_white_session(stage, mocker, tmp_path)

    def slow_tissue(*_args):
        clock[0] += 81
        return {
            "id": "tissue-slow",
            "status": "ready",
            "reason": "test",
            "frame_id": "white-slow",
            "report_ref": "tissue/slow.json",
        }

    rg_focus.prepare_tissue_field_for_scan.side_effect = slow_tissue
    with pytest.raises(FocusScanSafetyError, match="WHITE-to-R/G deadline"):
        session.prepare_field((10, 0))
    session.autofocus.fast_autofocus_bounded.assert_not_called()
    assert len(controller.scripts) == 1


@pytest.mark.parametrize("field_count", [10, 300])
def test_long_scan_bounds_observation_identity_window_and_duplicate_result(
    focus_runtime_stage,
    field_count,
):
    """Archive stays complete while observations and their identity index stay bounded."""
    stage, _controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    records = {}
    session = FocusScanSession(
        scan_id="bounded-identities",
        settings=enabled_settings(stage_binding, maximum_candidates=4),
        stage=stage,
        rg_focus=MagicMock(),
        autofocus=MagicMock(),
        frozen_rg=frozen(stage_binding),
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer(records),
    )
    for index in range(field_count):
        field = session.prepare_field((0, 0))
        if field.prepared is not None:
            session.plan_correction(field, -3.0)
        result = {
            "id": f"af-{index}",
            "status": "focused",
            "report_ref": f"rg_focus/af-{index}/report.json",
            "total_correction_um": 0,
            "duration_s": 1,
            "iterations": [
                {
                    "capture_id": f"pair-{index}",
                    "frame_ids": [
                        f"white-{index}",
                        f"red-{index}",
                        f"green-{index}",
                    ],
                    "measurement": rg_measurement().model_dump(mode="json"),
                    "decision": {
                        "status": "focused",
                        "reason": "within tolerance",
                        "inferred_z_error_um": 0.0,
                        "correction_um": 0.0,
                    },
                }
            ],
        }
        field.validate_rg_result(result)
        field.accept_rg_result()
        if index == field_count - 1:
            with pytest.raises(FocusScanSafetyError, match="repeats"):
                field.validate_rg_result(result)
        session.finish_field(field, imaged=True)
    assert len(session.observations) == 4
    assert len(session._observation_ids) == 4
    assert (
        len([path for path in records if path.startswith("observations/")])
        == field_count
    )


def test_disk_failure_at_planned_checkpoint_sends_no_move(focus_runtime_stage):
    """Mandatory journal failure before an effect prevents the first controller POST."""
    stage, controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    writes = 0

    def failing_writer(_path, _value):
        nonlocal writes
        writes += 1
        if writes >= 2:
            raise OSError("rename failed")

    session = FocusScanSession(
        scan_id="scan-1",
        settings=enabled_settings(stage_binding),
        stage=stage,
        rg_focus=MagicMock(),
        autofocus=MagicMock(),
        frozen_rg=frozen(stage_binding),
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=failing_writer,
    )
    session.observations.append(confirmed_observation("scan-1", stage_binding))
    with pytest.raises(OSError, match="rename failed"):
        session.prepare_field((10, 0))
    assert controller.scripts == []
    assert session.return_to_start_allowed is False


def test_post_move_binding_failure_records_actual_reached_state_first(
    focus_runtime_stage,
):
    """A changed binding cannot erase the move already issued to the controller."""
    stage, controller = focus_runtime_stage
    session, records = status_session(stage)
    field = session.prepare_field((0, 0))
    before = len(controller.scripts)
    with field.motion_guard("fixture_move"):
        stage.move_absolute_in_segments((0, 0, 10))
    changed = session.frozen_rg.binding.model_copy(
        update={"reference_id": "changed-after-move"}
    )
    session._current_binding = lambda: changed

    with pytest.raises(FocusScanSafetyError, match="binding changed"):
        field.sync_after_effect("fixture_move")

    assert len(controller.scripts) == before + 1
    assert stage.get_xyz_position() == (0, 0, 10)
    assert field._last_xyz_um == (0.0, 0.0, 10.0)
    assert field.budget.z_travel_um == 10
    confirmed = [
        row
        for row in records.values()
        if row.get("event") == "effect_confirmed"
        and row.get("details", {}).get("effect") == "fixture_move"
    ]
    assert len(confirmed) == 1
    assert confirmed[0]["details"]["actual_commanded_xyz_um"] == [0.0, 0.0, 10.0]
    assert confirmed[0]["details"]["actual_z_travel_um"] == 10
    assert confirmed[0]["details"]["binding_status"] == "mismatch"
    assert (
        len(
            [
                row
                for path, row in records.items()
                if path.startswith("events/") and row.get("event") == "stopped"
            ]
        )
        == 1
    )
    assert not session.observations
    assert not session.return_to_start_allowed
    session._current_binding = lambda: session.frozen_rg.binding
    with pytest.raises(FocusScanSafetyError, match="already stopped"):
        field.before_effect("forbidden_next_move")
    assert len(controller.scripts) == before + 1


def test_post_move_readback_and_binding_failure_records_unknown_with_precedence(
    focus_runtime_stage, mocker
):
    """Both reconciliation faults are archived; readback remains primary."""
    stage, controller = focus_runtime_stage
    session, records = status_session(stage)
    field = session.prepare_field((0, 0))
    with field.motion_guard("fixture_move"):
        stage.move_absolute_in_segments((0, 0, 10))
    changed = session.frozen_rg.binding.model_copy(
        update={"reference_id": "changed-after-move"}
    )
    session._current_binding = lambda: changed
    readback = OSError("fixture stage readback failed")
    mocker.patch.object(stage, "focus_motion_snapshot", side_effect=readback)

    with pytest.raises(OSError, match="stage readback failed") as caught:
        field.sync_after_effect("fixture_move")

    assert caught.value is readback
    assert isinstance(caught.value.__cause__, FocusScanSafetyError)
    assert field._last_xyz_um is None
    failures = [
        row
        for row in records.values()
        if row.get("event") == "effect_reconciliation_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["details"]["actual_state"] == "unknown"
    assert failures[0]["details"]["readback_error"] == str(readback)
    assert "binding changed" in failures[0]["details"]["binding_error"]
    assert not session.observations
    assert not session.return_to_start_allowed
    assert len(controller.scripts) == 1


def test_white_no_tissue_stops_before_white_or_z_and_keeps_field_policy(
    focus_runtime_stage,
):
    """Fresh no-tissue evidence performs safe XY only and never starts either AF."""
    stage, _controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    rg_focus = MagicMock()
    rg_focus.prepare_tissue_field_for_scan.return_value = {
        "id": "tissue-1",
        "status": "no_tissue",
        "reason": "empty",
        "frame_id": "white-1",
        "report_ref": "rg_focus/tissue-1/report.json",
    }
    autofocus = MagicMock()
    session = FocusScanSession(
        scan_id="scan-1",
        settings=enabled_settings(stage_binding, white=True),
        stage=stage,
        rg_focus=rg_focus,
        autofocus=autofocus,
        frozen_rg=frozen(stage_binding),
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer({}),
    )
    field = session.prepare_field((10, 0))
    assert field.no_tissue is True
    assert stage.position == {"x": 10, "y": 0, "z": 0}
    autofocus.fast_autofocus_bounded.assert_not_called()
    assert stage._prepared_approach is None


def test_binding_fault_is_not_relabelled_as_white_unavailable(focus_runtime_stage):
    """A changed reference/profile fails before fallback rather than starting WHITE."""
    stage, _controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    changed = stage_binding.model_copy(update={"reference_id": "changed"})
    autofocus = MagicMock()
    with pytest.raises(FocusScanSafetyError, match="binding"):
        FocusScanSession(
            scan_id="scan-1",
            settings=enabled_settings(stage_binding, white=True),
            stage=stage,
            rg_focus=MagicMock(),
            autofocus=autofocus,
            frozen_rg=frozen(stage_binding),
            current_binding=lambda: changed,
            surface_predictor=external_focus_prediction,
            evidence_writer=memory_writer({}),
        )
    autofocus.fast_autofocus_bounded.assert_not_called()


def test_final_rg_observation_is_archived_before_distinct_tile_capture(
    focus_runtime_stage,
):
    """Confirmed AF evidence trains once; captured remains a later field state."""
    stage, _controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    records = {}
    rg_focus = MagicMock()
    rg_focus.prepare_tissue_field_for_scan.return_value = {
        "id": "tissue-1",
        "status": "ready",
        "reason": "tissue",
        "frame_id": "white-1",
        "report_ref": "rg_focus/tissue-1/report.json",
    }
    autofocus = MagicMock()
    autofocus.fast_autofocus_bounded.return_value.model_dump.return_value = {
        "heights": [],
        "sharpnesses": [],
    }
    session = FocusScanSession(
        scan_id="scan-1",
        settings=enabled_settings(stage_binding),
        stage=stage,
        rg_focus=rg_focus,
        autofocus=autofocus,
        frozen_rg=frozen(stage_binding),
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer(records),
    )
    session.observations.append(confirmed_observation("scan-1", stage_binding))
    field = session.prepare_field((10, 0))
    result = {
        "id": "af-1",
        "status": "focused",
        "report_ref": "rg_focus/af-1/report.json",
        "total_correction_um": 0,
        "duration_s": 2.5,
        "iterations": [
            {
                "capture_id": "pair-final",
                "frame_ids": ["white-final", "red-final", "green-final"],
                "measurement": rg_measurement().model_dump(mode="json"),
                "decision": {
                    "status": "focused",
                    "reason": "within tolerance",
                    "inferred_z_error_um": 0.25,
                    "correction_um": 0,
                },
            }
        ],
    }
    field.validate_rg_result(result)
    field.accept_rg_result()
    assert field.stage == "focused"
    observation_paths = [path for path in records if path.startswith("observations/")]
    assert len(observation_paths) == 1
    assert len(session.observations) == 2
    assert not any(row.get("event") == "captured" for row in records.values())
    session.finish_field(field, imaged=True)
    assert any(row.get("event") == "captured" for row in records.values())


def test_white_led_disagreement_stops_without_observation(focus_runtime_stage):
    """A WHITE-success checkpoint cannot become a map sample after disagreement."""
    stage, _controller = focus_runtime_stage
    snapshot = stage.focus_motion_snapshot(
        {"x": (-500, 500), "y": (-500, 500), "z": (-50, 50)}
    )
    stage_binding = approach_binding(snapshot, rg_focus_model_id="profile")
    records = {}
    rg_focus = MagicMock()
    rg_focus.prepare_tissue_field_for_scan.return_value = {
        "id": "tissue-1",
        "status": "ready",
        "reason": "tissue",
        "frame_id": "white-1",
        "report_ref": "rg_focus/tissue-1/report.json",
    }
    autofocus = MagicMock()
    autofocus.fast_autofocus_bounded.return_value.model_dump.return_value = {}
    session = FocusScanSession(
        scan_id="scan-1",
        settings=enabled_settings(stage_binding, white=True),
        stage=stage,
        rg_focus=rg_focus,
        autofocus=autofocus,
        frozen_rg=frozen(stage_binding),
        current_binding=lambda: stage_binding,
        surface_predictor=external_focus_prediction,
        evidence_writer=memory_writer(records),
    )
    field = session.prepare_field((0, 0))
    rg_focus.prepare_tissue_field_for_scan.assert_called_once_with(
        "scan-1", field.field_id, field.frozen.measurement_policy
    )
    autofocus.fast_autofocus_bounded.assert_called_once()
    assert autofocus.fast_autofocus_bounded.call_args.kwargs["start"] == "base"
    assert field.stage == "approach_completed"
    assert stage.position == {"x": 0, "y": 0, "z": -15}
    # The mocked WHITE owner leaves the real stage at the hand-off measurement.
    field.white_focus_z_um = 20
    result = {
        "id": "af-1",
        "status": "focused",
        "report_ref": "rg_focus/af-1/report.json",
        "iterations": [
            {
                "capture_id": "pair-final",
                "frame_ids": ["white-final", "red-final", "green-final"],
                "measurement": rg_measurement().model_dump(mode="json"),
                "decision": {
                    "status": "focused",
                    "reason": "within tolerance",
                    "inferred_z_error_um": 0.1,
                    "correction_um": 0,
                },
            }
        ],
    }
    with pytest.raises(FocusScanSafetyError, match="disagrees"):
        field.validate_rg_result(result)
    assert not [path for path in records if path.startswith("observations/")]
    assert field.focus_progress is not None
    assert field.focus_progress.stage == "white_completed"
    assert field.focus_progress.rg_status == "failed"
    assert field.focus_progress.rg_result_id == "af-1"
    assert any(row.get("event") == "rg_failed" for row in records.values())
