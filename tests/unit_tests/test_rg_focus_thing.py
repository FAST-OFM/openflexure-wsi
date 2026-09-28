"""Native profile persistence and compatibility checks for R/G focus."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.rg.rg_flat_field import (
    JPEG_DOMAIN,
    FlatFieldSettings,
    processing_binding,
)
from openflexure_microscope_server.focus.rg.rg_focus_calibration import (
    RGFocusApproachSettings,
)
from openflexure_microscope_server.focus.rg.rg_focus_model import (
    JPEG_DRAFT_POLICY_ID,
    JPEG_DRAFT_POLICY_SHA256,
    RGFocusCalibrationPoint,
    RGFocusMeasurementPolicy,
    RGFocusModelSettings,
    RGFocusStationaryObservation,
    canonical_measurement_policy_bytes,
    jpeg_measurement_draft_policy,
)
from openflexure_microscope_server.things.focus import rg_focus as focus_module
from openflexure_microscope_server.things.focus.rg_focus import (
    RGFocus,
    focus_binding,
    measurement_implementation_identity,
)
from tests.unit_tests.rg_decision_process_stub import external_rg_decision
from tests.unit_tests.rg_model_process_stub import (
    external_profile_validation,
    fit_rg_focus_model,
)
from tests.unit_tests.test_rg_focus_model import (
    empirical_profile,
    stationary_observations,
)

GEOMETRY = {
    "measurement_space": JPEG_DOMAIN,
    "sensor": "imx477",
    "sensor_resolution": [4056, 3040],
    "sensor_crop": [0, 0, 3040, 3040],
    "image_size": [64, 64],
    "plane_size": [64, 64],
    "white_size": [64, 64],
    "bit_depth": 8,
    "white_level": 255,
    "channel_order": ["R", "G", "B"],
    "array_axes": "height,width,channel",
    "pixel_to_sensor": [[47.5, 0, 23.25], [0, 47.5, 23.25]],
    "white_to_sensor": [[47.5, 0, 23.25], [0, 47.5, 23.25]],
    "common_plane_to_sensor": [[47.5, 0, 23.25], [0, 47.5, 23.25]],
}


def point(capture_id, role, z, *, approach_error=0.0):
    """Create a well-conditioned signed observation with non-zero focus offset."""
    return RGFocusCalibrationPoint(
        capture_id=capture_id,
        role=role,
        z_um=float(z),
        measurement_status="ready",
        dx=3.0 - z + approach_error,
        dy=-0.5 + 0.1 * z,
        confidence=0.8,
        white_score=100.0 - abs(z),
    )


@pytest.fixture
def focus_thing(tmp_path, mocker):
    """Attach exact mocked runtime bindings to the real persistent Thing."""
    settings = tmp_path / "settings"
    settings.mkdir()
    thing = create_thing_without_server(
        RGFocus,
        mock_all_slots=True,
        settings_folder=str(settings),
    )
    thing.load_settings()
    mocker.patch.object(thing, "_external_decision", side_effect=external_rg_decision)
    mocker.patch.object(
        thing,
        "_external_profile_validation",
        side_effect=external_profile_validation,
    )
    camera = {
        "measurement_space": JPEG_DOMAIN,
        "geometry": GEOMETRY,
        "exposure_time_us": 4476,
        "analogue_gain": 1.0,
        "colour_gains": [1.0, 1.0],
        "camera": {"mode": "default"},
        "isp": {"controls": {"AeEnable": False, "AwbEnable": False}},
        "encoding": {"format": "jpeg", "quality": 95, "subsampling": 0},
    }
    flat_parameters = FlatFieldSettings(
        measurement_domain=JPEG_DOMAIN,
        processing_roi=(0, 0, 64, 64),
        minimum_signal_dn=8,
        maximum_dark_signal_dn=32,
    )
    processed = processing_binding(camera, flat_parameters)
    thing._cam.jpeg_measurement_configuration = camera
    thing._rg_flat_field.parameters = flat_parameters
    thing.parameters = RGFocusModelSettings(maximum_holdout_error_um=2.0)
    thing.measurement_parameters = RGFocusMeasurementPolicy(
        measurement_domain=JPEG_DOMAIN
    )
    thing._stage.hardware_settings = {
        "axes": {"z": {"units_per_mm": 1000.0, "settle_ms": 1000.0}}
    }
    binding = {
        "camera": processed,
        "optics_id": "current",
        "illumination_id": "current",
    }
    processing = {
        "digital_gain": 1.0,
        "colour_gains": [1.0, 1.0],
        "colour_correction_matrix": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
    }
    thing._rg_flat_field.profiles = {
        mode: {
            "id": "ff",
            "method": "phase_matched_processed_jpeg_measured_dark",
            "maps_sha256": f"{mode}-hash",
            "binding": binding,
            "processing_reference": processing,
        }
        for mode in ("red", "green")
    }
    thing._rg_flat_field.calibration_status = {
        mode: {"status": "valid"} for mode in ("red", "green")
    }
    return thing


def profile(thing, *, approach_error=0.0):
    """Fit a profile bound to the mock microscope; an approach error keeps it candidate."""
    points = [point(f"fit-{z}", "fit", z) for z in (-8, -4, 0, 4, 8)]
    points += [point(f"hold-{z}", "holdout", z) for z in (-6, -2, 2, 6)]
    points += [
        point("above", "approach_above", 0),
        point("below", "approach_below", 0, approach_error=approach_error),
    ]
    evidence = (thing.measurement_policy_confirmation or {}).get(
        "commissioning_evidence"
    )
    stationary = (
        tuple(
            RGFocusStationaryObservation.model_validate(row)
            for row in evidence["stationary_observations"]
        )
        if evidence is not None
        else stationary_observations()
    )
    return fit_rg_focus_model(
        points,
        RGFocusModelSettings(maximum_holdout_error_um=2.0),
        reference_white_score=100.0,
        stationary_observations=stationary,
        profile_id="profile",
        source_series_id="series",
        compatibility=focus_binding(
            thing._stage,
            thing._cam,
            thing._rg_flat_field,
            thing.approach_parameters,
        ),
        source_evidence={
            "measurements_sha256": "abc",
            **(
                {"commissioning_evidence_id": evidence["id"]}
                if evidence is not None
                else {}
            ),
        },
        measurement_policy=thing.measurement_parameters,
    )


def commission_profile(thing, fitted, binding):
    """Prepare real hash-bound commissioning for use-gate tests without hardware."""
    policy = jpeg_measurement_draft_policy()
    implementation_id = str(measurement_implementation_identity()["id"])
    binding_sha256 = focus_module._identity(binding)
    stationary = tuple(
        row.model_copy(
            update={
                "binding_sha256": binding_sha256,
                "geometry_id": binding["geometry_id"],
            }
        )
        for row in fitted.activation_evidence.stationary_observations
    )
    first = stationary[0]
    core = {
        "status": "passed",
        "failures": [],
        "policy_id": JPEG_DRAFT_POLICY_ID,
        "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
        "measurement_implementation_id": implementation_id,
        "binding_sha256": binding_sha256,
        "field": {
            "field_id": first.field_id,
            "geometry_id": first.geometry_id,
            "binding_sha256": first.binding_sha256,
            "mask_sha256": first.mask_sha256,
            "window_ids": list(first.window_ids),
        },
        "capture_ids": [row.capture_id for row in stationary],
        "stationary_observations": [row.model_dump(mode="json") for row in stationary],
        "white_mutual_fraction": abs(
            stationary[0].white_score - stationary[1].white_score
        )
        / max(row.white_score for row in stationary),
        "maximum_white_mutual_fraction": 0.15,
        "minimum_patch_count": 9,
        "minimum_inlier_fraction": 0.6,
        "saved_jpeg_replay": {"comparison": "exact", "tolerance": 0},
    }
    evidence = {"id": focus_module._identity(core), **core}
    thing.measurement_parameters = policy
    thing.measurement_policy_confirmation = {
        "policy_id": JPEG_DRAFT_POLICY_ID,
        "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
        "measurement_implementation_id": implementation_id,
        "state": "commissioned",
        "commissioning_evidence": evidence,
    }
    return fitted.model_copy(
        update={
            "measurement_policy": policy,
            "compatibility": binding,
            "activation_evidence": fitted.activation_evidence.model_copy(
                update={"stationary_observations": stationary}
            ),
            "source_evidence": {
                **fitted.source_evidence,
                "commissioning_evidence_id": evidence["id"],
            },
        }
    )


def test_valid_profile_is_saved_in_native_settings_and_restored(focus_thing):
    """A checked fit survives native settings roundtrip without touching hardware."""
    fitted = profile(focus_thing)
    assert fitted.status == "valid"
    result = focus_thing.install_profile(fitted)
    assert result["status"] == "valid"
    settings_file = Path(focus_thing._thing_server_interface.settings_file_path)
    restarted = create_thing_without_server(
        RGFocus,
        mock_all_slots=True,
        settings_folder=str(settings_file.parent),
    )
    restarted.load_settings()
    assert restarted.profile == fitted.model_dump(mode="json")


@pytest.mark.parametrize(
    "defect",
    ["missing", "uncommissioned", "implementation", "tampered", "profile_link"],
)
def test_profile_use_requires_current_exact_commissioning_before_capture(
    focus_thing, mocker, defect
):
    """Every operational entry refuses stale/missing evidence before any owner effect."""
    fitted = commission_profile(
        focus_thing, profile(focus_thing), focus_thing._binding()
    )
    focus_thing.profile = fitted.model_dump(mode="json")
    assert focus_thing.checked_profile() == fitted
    frozen = SimpleNamespace(
        profile=fitted,
        measurement_policy=fitted.measurement_policy,
        control=focus_thing.control_parameters,
        binding=SimpleNamespace(approach_parameters=focus_thing.approach_parameters),
        effective_focus_tolerance_um=external_profile_validation(
            fitted,
            focus_thing.control_parameters.focus_tolerance_um,
        )[1],
    )
    if defect == "missing":
        focus_thing.measurement_policy_confirmation = None
    elif defect == "uncommissioned":
        focus_thing.measurement_policy_confirmation["state"] = "draft_uncommissioned"
    elif defect == "implementation":
        mocker.patch.object(
            focus_module,
            "measurement_implementation_identity",
            return_value={"id": "changed"},
        )
    elif defect == "tampered":
        focus_thing.measurement_policy_confirmation["commissioning_evidence"]["field"][
            "mask_sha256"
        ] = "0" * 64
    else:
        focus_thing.profile["source_evidence"]["commissioning_evidence_id"] = (
            "unrelated"
        )
        frozen.profile = type(fitted).model_validate(focus_thing.profile)
    capture = mocker.patch.object(focus_thing, "_capture_measurement")
    focus_thing._stage.reset_mock()
    with pytest.raises(ValueError, match="commission"):
        focus_thing.autofocus(prepared=True)
    with pytest.raises(ValueError, match="commission"):
        focus_thing._frozen_scan_profile(frozen)
    capture.assert_not_called()
    focus_thing._stage.validate_path.assert_not_called()
    focus_thing._stage.move_z_with_preload.assert_not_called()


def test_frozen_use_ignores_next_run_forms_but_keeps_real_commissioning(focus_thing):
    """The active scan's policy/approach/control remain frozen while forms are edited."""
    fitted = commission_profile(
        focus_thing, profile(focus_thing), focus_thing._binding()
    )
    focus_thing.profile = fitted.model_dump(mode="json")
    frozen = SimpleNamespace(
        profile=fitted,
        measurement_policy=fitted.measurement_policy,
        control=focus_thing.control_parameters,
        binding=SimpleNamespace(approach_parameters=focus_thing.approach_parameters),
        effective_focus_tolerance_um=external_profile_validation(
            fitted,
            focus_thing.control_parameters.focus_tolerance_um,
        )[1],
    )
    focus_thing.measurement_parameters = RGFocusMeasurementPolicy()
    focus_thing.approach_parameters = RGFocusApproachSettings(preload_um=16)
    focus_thing.control_parameters = focus_thing.control_parameters.model_copy(
        update={"focus_tolerance_um": 0.1}
    )
    assert focus_thing._frozen_scan_profile(frozen)[0] == fitted


def test_exhausted_empirical_budget_refuses_profile_use_before_capture(
    focus_thing, mocker
):
    """A valid calibration still cannot spend the entire configured focus tolerance."""
    fitted = commission_profile(
        focus_thing, empirical_profile(), focus_thing._binding()
    )
    focus_thing.profile = fitted.model_dump(mode="json")
    focus_thing.control_parameters = focus_thing.control_parameters.model_copy(
        update={"focus_tolerance_um": fitted.empirical_prediction_error_um}
    )
    capture = mocker.patch.object(focus_thing, "_capture_measurement")
    with pytest.raises(ValueError, match="exhausts"):
        focus_thing.autofocus(prepared=True)
    capture.assert_not_called()
    focus_thing._stage.validate_path.assert_not_called()


def test_candidate_is_persisted_but_cannot_be_used(focus_thing):
    """A failed approach remains visible evidence and checked_profile refuses it."""
    fitted = profile(focus_thing, approach_error=2.1)
    result = focus_thing.install_profile(fitted)
    assert result["status"] == "candidate"
    with pytest.raises(ValueError, match="candidate-only"):
        focus_thing.checked_profile()


@pytest.mark.parametrize("change", ["camera", "map", "stage", "approach"])
def test_runtime_compatibility_change_invalidates_saved_profile(focus_thing, change):
    """Camera, map and Z dynamics changes are all fail-closed."""
    focus_thing.install_profile(profile(focus_thing))
    if change == "camera":
        focus_thing._cam.jpeg_measurement_configuration = {
            **focus_thing._cam.jpeg_measurement_configuration,
            "exposure_time_us": 5000,
        }
    elif change == "map":
        focus_thing._rg_flat_field.profiles["red"]["maps_sha256"] = "changed"
    elif change == "stage":
        focus_thing._stage.hardware_settings["axes"]["z"]["settle_ms"] = 500.0
    else:
        focus_thing.set_approach_parameters(RGFocusApproachSettings(approach_sign=-1))
    assert focus_thing.calibration_status["status"] == "incompatible"


def test_rejected_install_does_not_replace_previous_profile(focus_thing):
    """An incompatible upload leaves the previous native setting byte-for-byte intact."""
    original = profile(focus_thing)
    focus_thing.install_profile(original)
    settings_file = Path(focus_thing._thing_server_interface.settings_file_path)
    before = settings_file.read_bytes()
    incompatible = original.model_copy(
        update={"compatibility": {**original.compatibility, "optics_id": "other"}}
    )
    with pytest.raises(ValueError, match="incompatible"):
        focus_thing.install_profile(incompatible)
    assert focus_thing.profile == original.model_dump(mode="json")
    assert settings_file.read_bytes() == before


def test_legacy_raw_policy_loads_as_evidence_and_remains_incompatible(focus_thing):
    """Old saved RAW settings do not crash startup or become JPEG by default."""
    old = RGFocusMeasurementPolicy.model_validate(
        {"core": {"saturation_level": 4094.0}}
    )
    assert old.measurement_domain == "legacy-raw"
    assert old.legacy_raw_saturation_level == 4094.0
    with pytest.raises(ValueError, match="explicit processed-JPEG"):
        old.require_jpeg()
    focus_thing.measurement_parameters = old
    focus_thing.profile = {"historical": "preserved"}
    assert focus_thing.calibration_status["status"] == "incompatible"
    migrated = focus_thing.use_jpeg_measurement_preset()
    assert migrated == jpeg_measurement_draft_policy()
    assert focus_thing.profile == {"historical": "preserved"}
    assert focus_thing.measurement_policy_confirmation == {
        "policy_id": JPEG_DRAFT_POLICY_ID,
        "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
        "measurement_implementation_id": measurement_implementation_identity()["id"],
        "state": "draft_uncommissioned",
    }
    with pytest.raises(ValidationError):
        RGFocusMeasurementPolicy.model_validate(
            {
                "measurement_domain": JPEG_DOMAIN,
                "core": {"saturation_level": 4094.0},
            }
        )


def test_source_jpeg_policy_is_exact_canonical_artifact():
    """The factory owns every field and hashes the reviewed final-LF payload."""
    policy = jpeg_measurement_draft_policy()
    payload = canonical_measurement_policy_bytes(policy)
    assert len(payload) == 1375
    assert payload.endswith(b"\n")
    assert hashlib.sha256(payload).hexdigest() == JPEG_DRAFT_POLICY_SHA256
    assert json.loads(payload) == policy.model_dump(mode="json")
    assert policy.core.patch_size_px == 128
    assert policy.core.maximum_patch_count == 24
    assert policy.core.maximum_absolute_shift_px == 24
    assert policy.estimator.minimum_outlier_threshold_px == 1.0
    assert policy.estimator.inlier_aggregation == "arithmetic_mean"
    assert policy.estimator.outlier_mad_multiplier == 2.5
    assert policy.core.minimum_patch_count == 9
    assert policy.estimator.maximum_radial_p90_px == 3.25
    assert policy.tissue_field.edge_margin_px == 128
    assert policy.tissue_field.minimum_component_pixels == 512
    historical = policy.model_dump(mode="json")
    historical["core"]["minimum_measurement_response"] = 0.15
    assert RGFocusMeasurementPolicy.model_validate(historical) == policy


def test_jpeg_preset_replaces_all_legacy_values_and_is_idempotent(focus_thing):
    """Migration cannot carry customized RAW filter, patch or pixel thresholds."""
    legacy = RGFocusMeasurementPolicy.model_validate(
        {
            "core": {
                "saturation_level": 1234,
                "patch_size_px": 256,
                "phase_highpass_sigma_px": 17,
                "maximum_absolute_shift_px": 33,
            },
            "estimator": {"minimum_outlier_threshold_px": 2.25},
            "tissue_field": {"edge_margin_px": 60},
        }
    )
    focus_thing._commit_many(
        {
            "measurement_parameters": legacy,
            "profile": {"legacy": "retained"},
        }
    )
    for owner in (focus_thing._cam, focus_thing._stage, focus_thing._rg_flat_field):
        owner.reset_mock()
    assert focus_thing.use_jpeg_measurement_preset() == jpeg_measurement_draft_policy()
    settings_file = Path(focus_thing._thing_server_interface.settings_file_path)
    first_bytes = settings_file.read_bytes()
    first_confirmation = copy.deepcopy(focus_thing.measurement_policy_confirmation)
    assert focus_thing.use_jpeg_measurement_preset() == jpeg_measurement_draft_policy()
    assert settings_file.read_bytes() == first_bytes
    assert focus_thing.measurement_policy_confirmation == first_confirmation
    assert focus_thing.profile == {"legacy": "retained"}
    assert focus_thing._cam.mock_calls == []
    assert focus_thing._stage.mock_calls == []
    assert focus_thing._rg_flat_field.mock_calls == []


def test_jpeg_preset_save_failure_rolls_back_all_settings(focus_thing, mocker):
    """A failed atomic save restores the complete prior policy and confirmation."""
    legacy = RGFocusMeasurementPolicy.model_validate(
        {"core": {"saturation_level": 4094, "patch_size_px": 256}}
    )
    focus_thing._commit_many(
        {
            "measurement_parameters": legacy,
            "measurement_policy_confirmation": {"legacy": True},
            "profile": {"legacy": "profile"},
        }
    )
    settings_file = Path(focus_thing._thing_server_interface.settings_file_path)
    before = settings_file.read_bytes()
    mocker.patch.object(
        focus_thing, "save_settings", side_effect=OSError("settings disk failure")
    )
    with pytest.raises(OSError, match="settings disk failure"):
        focus_thing.use_jpeg_measurement_preset()
    assert focus_thing.measurement_parameters == legacy
    assert focus_thing.measurement_policy_confirmation == {"legacy": True}
    assert focus_thing.profile == {"legacy": "profile"}
    assert settings_file.read_bytes() == before


def test_demo_contract_save_failure_rolls_back_every_member(focus_thing, mocker):
    """The composite action never leaves a mixed in-memory or persisted contract."""
    previous = {
        name: copy.deepcopy(getattr(focus_thing, name))
        for name in (
            "measurement_parameters",
            "measurement_policy_confirmation",
            "parameters",
            "control_parameters",
            "calibration_parameters",
            "approach_parameters",
            "profile",
        )
    }
    focus_thing._commit("profile", {"legacy": "retained"})
    previous["profile"] = {"legacy": "retained"}
    settings_file = Path(focus_thing._thing_server_interface.settings_file_path)
    before = settings_file.read_bytes()
    mocker.patch.object(
        focus_thing, "save_settings", side_effect=OSError("settings disk failure")
    )
    with pytest.raises(OSError, match="settings disk failure"):
        focus_thing.apply_demo_focus_contract()
    for name, value in previous.items():
        assert getattr(focus_thing, name) == value
    assert settings_file.read_bytes() == before


def test_demo_contract_is_atomic_exact_and_still_uncommissioned(focus_thing):
    """One action persists the complete bounded contract without claiming readiness."""
    focus_thing.profile = {"legacy": "retained"}
    result = focus_thing.apply_demo_focus_contract()
    assert focus_thing.measurement_parameters == jpeg_measurement_draft_policy()
    assert focus_thing.calibration_parameters.z_offsets_um == (
        -32,
        -28,
        -24,
        -20,
        -16,
        -12,
        -8,
        -4,
        0,
        4,
        8,
        12,
        16,
        20,
        24,
        28,
        32,
    )
    assert focus_thing.calibration_parameters.fit_offsets_um == (
        -32,
        -24,
        -16,
        -8,
        0,
        8,
        16,
        24,
        32,
    )
    assert focus_thing.calibration_parameters.holdout_offsets_um == (
        -28,
        -20,
        -12,
        -4,
        4,
        12,
        20,
        28,
    )
    assert focus_thing.calibration_parameters.step_um == 4
    assert focus_thing.calibration_parameters.maximum_pairs == 20
    assert focus_thing.calibration_parameters.maximum_absolute_z_um == 42
    assert focus_thing.calibration_parameters.maximum_holdout_error_um == 2
    assert focus_thing.parameters.maximum_holdout_error_um == 2
    assert focus_thing.parameters.maximum_reference_focus_offset_um == 12
    assert focus_thing.control_parameters.maximum_iterations == 3
    assert focus_thing.control_parameters.focus_tolerance_um == 4
    assert focus_thing.control_parameters.cross_track_envelope_multiplier == 8
    assert focus_thing.control_parameters.cross_track_measurement_mad_multiplier == 1
    assert focus_thing.control_parameters.maximum_single_correction_um == 32
    assert focus_thing.control_parameters.maximum_total_correction_um == 32
    assert focus_thing.control_parameters.maximum_absolute_z_excursion_um == 42
    assert focus_thing.approach_parameters == RGFocusApproachSettings(
        preload_um=10, approach_sign=1
    )
    assert result["calibration_plan"] == {
        "path_um": [
            -18,
            -8,
            2,
            12,
            22,
            32,
            -6,
            4,
            -30,
            -20,
            -42,
            -32,
            -26,
            -16,
            -14,
            -4,
            8,
            -10,
            0,
            -2,
            8,
            -8,
            -10,
            0,
            -22,
            -12,
            6,
            16,
            14,
            24,
            18,
            28,
            10,
            20,
            -34,
            -24,
            -38,
            -28,
            -10,
            0,
        ],
        "pair_count": 20,
    }
    assert result["demo_contract_match"]
    assert result["policy_match"]
    assert not result["ready"]
    assert not result["measure_ready"]
    assert not result["calibration_ready"]
    assert not result["autofocus_ready"]
    assert result["policy_state"] == "draft_uncommissioned"
    assert "jpeg_policy_uncommissioned" in {row["code"] for row in result["mismatches"]}
    assert focus_thing.profile == {"legacy": "retained"}


def test_readiness_reports_legacy_without_owner_effects(focus_thing):
    """Incomplete historical evidence is safe and no hardware method is invoked."""
    focus_thing.measurement_parameters = RGFocusMeasurementPolicy.model_validate(
        {"core": {"saturation_level": 4094}}
    )
    focus_thing.measurement_policy_confirmation = None
    focus_thing.profile = {"historical": "preserved"}
    for owner in (focus_thing._cam, focus_thing._stage, focus_thing._rg_flat_field):
        owner.reset_mock()
    snapshot = focus_thing.readiness
    codes = [row["code"] for row in snapshot["mismatches"]]
    assert snapshot["policy_state"] == "legacy"
    assert codes[:3] == [
        "legacy_measurement_policy",
        "policy_hash_mismatch",
        "jpeg_policy_unconfirmed",
    ]
    assert "jpeg_policy_uncommissioned" in codes
    assert "focus_profile_incompatible" in codes
    assert not snapshot["ready"]
    assert focus_thing.profile == {"historical": "preserved"}
    assert focus_thing._cam.mock_calls == []
    assert focus_thing._stage.mock_calls == []
    assert focus_thing._rg_flat_field.mock_calls == []


def test_raw_settings_file_and_profile_survive_startup_as_incompatible(
    focus_thing, mocker
):
    """An actual pre-JPEG settings document loads without effects or evidence loss."""
    focus_thing._commit("profile", {"historical": "raw-profile"})
    settings_file = Path(focus_thing._thing_server_interface.settings_file_path)
    saved = json.loads(settings_file.read_text())
    saved["measurement_parameters"] = {
        "core": {"saturation_level": 4094.0, "patch_size_px": 256}
    }
    settings_file.write_text(json.dumps(saved))
    restarted = create_thing_without_server(
        RGFocus,
        mock_all_slots=True,
        settings_folder=str(settings_file.parent),
    )
    mocker.patch.object(
        restarted,
        "_external_profile_validation",
        side_effect=external_profile_validation,
    )
    restarted.load_settings()
    snapshot = restarted.readiness
    assert snapshot["policy_state"] == "legacy"
    assert snapshot["focus_profile"]["status"] == "incompatible"
    assert restarted.profile == {"historical": "raw-profile"}
    assert restarted.measurement_parameters.legacy_raw_saturation_level == 4094
    assert restarted._cam.mock_calls == []
    assert restarted._stage.mock_calls == []
    assert restarted._rg_flat_field.mock_calls == []


def test_readiness_reports_exact_geometry_and_camera_mismatch(focus_thing):
    """The accepted 4x ROI geometry is displayed exactly without becoming ready."""
    affine = [[4.0, 0.0, 1.5], [0.0, 4.0, 1.5]]
    source_geometry = {
        "measurement_space": JPEG_DOMAIN,
        "sensor": "imx477",
        "sensor_resolution": [4056, 3040],
        "sensor_crop": [0, 0, 4056, 3040],
        "image_size": [1014, 760],
        "plane_size": [1014, 760],
        "white_size": [1014, 760],
        "bit_depth": 8,
        "white_level": 255,
        "channel_order": ["R", "G", "B"],
        "array_axes": "height,width,channel",
        "pixel_to_sensor": affine,
        "white_to_sensor": affine,
        "common_plane_to_sensor": affine,
    }
    focus_thing._cam.jpeg_measurement_configuration = {
        "measurement_space": JPEG_DOMAIN,
        "geometry": source_geometry,
        "camera": {"mode": "default"},
    }
    focus_thing._rg_flat_field.parameters = FlatFieldSettings(
        measurement_domain=JPEG_DOMAIN,
        processing_roi=(102, 0, 810, 760),
        minimum_signal_dn=8,
        maximum_dark_signal_dn=32,
    )
    snapshot = focus_thing.readiness
    assert snapshot["geometry"]["match"]
    assert snapshot["geometry"]["effective"]["source_image_size"] == [1014, 760]
    assert snapshot["geometry"]["effective"]["processing_roi"] == [102, 0, 810, 760]
    assert snapshot["geometry"]["effective"]["sensor_scale"] == [4.0, 4.0]
    assert snapshot["geometry"]["effective"]["sensor_roi"] == [408, 0, 3240, 3040]
    assert "camera_binding_mismatch" in {row["code"] for row in snapshot["mismatches"]}
    assert not snapshot["binding_match"]


def test_readiness_transitions_from_calibration_only_to_commissioned_profile(
    focus_thing, mocker
):
    """Matching evidence enables measure/calibrate, then a valid model enables AF."""
    processed = processing_binding(
        focus_thing._cam.jpeg_measurement_configuration,
        focus_thing._rg_flat_field.parameters,
    )
    geometry = processed["geometry"]
    processing = focus_thing._rg_flat_field.profiles["red"]["processing_reference"]
    mocker.patch.object(
        focus_module,
        "EXPECTED_GEOMETRY_ID",
        focus_binding(focus_thing._stage, focus_thing._cam, focus_thing._rg_flat_field)[
            "geometry_id"
        ],
    )
    mocker.patch.object(
        focus_module,
        "EXPECTED_GEOMETRY",
        {
            key: copy.deepcopy(geometry[key])
            for key in (
                "source_image_size",
                "processing_roi",
                "image_size",
                "plane_size",
                "white_size",
                "pixel_to_sensor",
                "white_to_sensor",
                "common_plane_to_sensor",
            )
        }
        | {
            "sensor_scale": [47.5, 47.5],
            "sensor_roi": [0, 0, 3040, 3040],
        },
    )
    mocker.patch.object(
        focus_module,
        "EXPECTED_CAMERA_BINDING_SHA256",
        focus_module._identity(processed),
    )
    mocker.patch.object(
        focus_module,
        "EXPECTED_PROCESSING_REFERENCE_SHA256",
        focus_module._identity(processing),
    )
    mocker.patch.object(focus_module, "EXPECTED_FLAT_FIELD_PROFILE_ID", "ff")
    mocker.patch.object(
        focus_module,
        "EXPECTED_FLAT_FIELD_MAPS",
        {"red": "red-hash", "green": "green-hash"},
    )
    mocker.patch.object(focus_module, "EXPECTED_ILLUMINATION_ID", "current")
    draft = focus_thing.apply_demo_focus_contract()
    assert draft["calibration_ready"]
    assert not draft["measure_ready"]
    binding = focus_binding(
        focus_thing._stage,
        focus_thing._cam,
        focus_thing._rg_flat_field,
        focus_thing.approach_parameters,
    )
    implementation_id = draft["measurement_implementation"]["id"]
    binding_sha256 = focus_module._identity(binding)
    stationary = stationary_observations(
        binding_sha256=binding_sha256,
        geometry_id=binding["geometry_id"],
    )
    field = {
        "field_id": stationary[0].field_id,
        "geometry_id": stationary[0].geometry_id,
        "binding_sha256": stationary[0].binding_sha256,
        "mask_sha256": stationary[0].mask_sha256,
        "window_ids": list(stationary[0].window_ids),
    }
    evidence_core = {
        "status": "passed",
        "failures": [],
        "policy_id": JPEG_DRAFT_POLICY_ID,
        "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
        "measurement_implementation_id": implementation_id,
        "binding_sha256": binding_sha256,
        "field": field,
        "capture_ids": [item.capture_id for item in stationary],
        "stationary_observations": [
            item.model_dump(mode="json") for item in stationary
        ],
        "white_mutual_fraction": 0.0,
        "maximum_white_mutual_fraction": 0.15,
        "minimum_patch_count": 9,
        "minimum_inlier_fraction": 0.6,
        "saved_jpeg_replay": {"comparison": "exact", "tolerance": 0},
    }
    evidence = {"id": focus_module._identity(evidence_core), **evidence_core}
    focus_thing._commit(
        "measurement_policy_confirmation",
        {
            "policy_id": JPEG_DRAFT_POLICY_ID,
            "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
            "measurement_implementation_id": implementation_id,
            "state": "commissioned",
            "commissioning_evidence": evidence,
        },
    )
    commissioned = focus_thing.readiness
    assert commissioned["policy_state"] == "commissioned"
    assert commissioned["commissioning_match"]
    assert commissioned["measure_ready"]
    assert commissioned["calibration_ready"]
    assert not commissioned["autofocus_ready"]
    tampered = copy.deepcopy(evidence)
    tampered["stationary_observations"][1]["accepted_patch_count"] = 8
    tampered_core = {key: value for key, value in tampered.items() if key != "id"}
    tampered["id"] = focus_module._identity(tampered_core)
    focus_thing.measurement_policy_confirmation["commissioning_evidence"] = tampered
    assert not focus_thing.readiness["measure_ready"]
    focus_thing.measurement_policy_confirmation["commissioning_evidence"] = evidence
    focus_thing.install_profile(profile(focus_thing))
    ready = focus_thing.readiness
    assert ready["measure_ready"]
    assert ready["calibration_ready"]
    assert ready["autofocus_ready"]
