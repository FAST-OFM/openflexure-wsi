"""Contract-only checks for the GPL side of focus-surface integration."""

import pytest
from pydantic import ValidationError

from openflexure_microscope_server.focus.focus_surface import (
    FocusAxisScale,
    FocusBinding,
    FocusFieldProgress,
    FocusObservation,
    FocusObservationProvenance,
    FocusObservationQuality,
    FocusPrediction,
    FocusPredictionDiagnostics,
    FocusPredictionEvidence,
    FocusPredictionRequest,
    FocusRunSettings,
    FocusSurfaceSettings,
    WhiteSearchSettings,
    check_focus_binding,
)
from openflexure_microscope_server.focus.rg.rg_focus_calibration import (
    RGFocusApproachSettings,
)


def binding(**overrides):
    """Return a complete explicit valid binding with no runtime-state hash."""
    values = {
        "stage_controller_id": "moonraker-main",
        "reference_id": "operator-zero-7",
        "reference_status": "valid",
        "axis_scales": (
            FocusAxisScale(axis="x", units_per_mm=1000.0, direction_sign=1),
            FocusAxisScale(axis="y", units_per_mm=800.0, direction_sign=-1),
            FocusAxisScale(axis="z", units_per_mm=1200.0, direction_sign=1),
        ),
        "camera_stage_mapping_id": "csm-3",
        "camera_stage_mapping_status": "valid",
        "geometry_id": "hq-crop-2800-common-plane",
        "rg_focus_model_id": "rg-model-9",
        "rg_focus_model_status": "valid",
        "red_flat_field_profile_id": "red-flat-4",
        "red_flat_field_status": "valid",
        "green_flat_field_profile_id": "green-flat-5",
        "green_flat_field_status": "valid",
        "approach_profile_id": "z-approach-2",
        "approach_status": "valid",
        "approach_parameters": RGFocusApproachSettings(preload_um=8, approach_sign=1),
    }
    values.update(overrides)
    return FocusBinding(**values)


def surface_settings(**overrides):
    """Use explicit test-only gates; active production settings have no guesses."""
    values = {
        "enabled": True,
        "minimum_plane_points": 4,
        "maximum_neighbors": 12,
        "neighbor_radius_um": 1500.0,
        "allow_extrapolation": False,
        "maximum_extrapolation_um": 0.0,
        "maximum_prediction_delta_um": 12.0,
        "measurement_offset_um": -3.0,
        "maximum_fit_residual_um": 2.0,
        "minimum_fit_inlier_fraction": 0.75,
        "maximum_fit_condition_number": 100.0,
        "maximum_plane_slope_um_per_um": 0.02,
        "maximum_observation_age_s": 60.0,
    }
    values.update(overrides)
    return FocusSurfaceSettings(**values)


def white_settings(**overrides):
    """Return bounded test values, not commissioned hardware numbers."""
    values = {
        "search_z_range_um": (-12.0, 8.0),
        "white_search_timeout_s": 35.0,
        "total_focus_budget_s": 80.0,
        "approach_and_rg_reserve_s": 40.0,
        "maximum_white_led_disagreement_um": 4.0,
    }
    values.update(overrides)
    return WhiteSearchSettings(**values)


def quality(**overrides):
    """Return independently verified final R/G quality."""
    values = {
        "measurement_status": "ready",
        "confidence": 0.8,
        "accepted_patch_count": 6,
        "inlier_fraction": 0.75,
        "final_error_um": 0.25,
        "focus_tolerance_um": 1.5,
        "independent_post_move_verification": True,
    }
    values.update(overrides)
    return FocusObservationQuality(**values)


def provenance(**overrides):
    """Return final R/G provenance with distinct frame identities."""
    values = {
        "result_id": "af-result-1",
        "report_ref": "reports/field-1.json",
        "rg_measurement_id": "rg-measurement-1",
        "capture_ids": ("red-1", "green-1"),
    }
    values.update(overrides)
    return FocusObservationProvenance(**values)


def observation(**overrides):
    """Return one confirmed R/G observation in physical micrometres."""
    values = {
        "scan_id": "scan-1",
        "field_id": "field-1",
        "attempt_id": "attempt-1",
        "observation_id": "observation-1",
        "x_um": -125.5,
        "y_um": 250.25,
        "z_um": 0.0,
        "final_readback_settled": True,
        "final_readback_monotonic_s": 9.0,
        "recorded_monotonic_s": 10.0,
        "source": "led_autofocus",
        "status": "confirmed",
        "binding": binding(),
        "provenance": provenance(),
        "quality": quality(),
    }
    values.update(overrides)
    return FocusObservation(**values)


def diagnostics(**overrides):
    """Return external fit/support diagnostics, not a confidence interval."""
    values = {
        "candidate_observation_count": 4,
        "local_observation_count": 4,
        "selected_support_count": 4,
        "nearest_distance_um": 100.0,
        "support_age_s": 5.0,
        "fit_rmse_um": 0.5,
        "maximum_absolute_residual_um": 0.8,
        "fit_condition_number": 12.0,
        "extrapolation_distance_um": 0.0,
        "prediction_delta_um": -1.0,
    }
    values.update(overrides)
    return FocusPredictionDiagnostics(**values)


def prediction_request(**overrides):
    """Return a frozen process request in physical micrometres."""
    values = {
        "expected_scan_id": "scan-1",
        "field_id": "target-field",
        "attempt_id": "target-attempt",
        "prediction_id": "target-prediction",
        "target_x_um": 0.0,
        "target_y_um": 0.0,
        "current_z_um": 0.0,
        "predicted_monotonic_s": 100.0,
        "expected_binding": binding(),
        "settings": surface_settings(),
    }
    values.update(overrides)
    return FocusPredictionRequest(**values)


def usable_prediction(**overrides):
    """Return a valid external local-plane response."""
    values = {
        "scan_id": "scan-1",
        "field_id": "field-5",
        "attempt_id": "attempt-5",
        "prediction_id": "prediction-5",
        "target_x_um": -500.25,
        "target_y_um": 750.5,
        "target_z_um": 0.0,
        "status": "usable",
        "source": "local_plane",
        "reason": "External support passed configured gates",
        "binding": binding(),
        "support_observation_ids": ("o1", "o2", "o3", "o4"),
        "support_field_ids": ("f1", "f2", "f3", "f4"),
        "support_distances_um": (100.0, 200.0, 300.0, 400.0),
        "diagnostics": diagnostics(),
        "predicted_monotonic_s": 20.0,
    }
    values.update(overrides)
    return FocusPrediction(**values)


def test_disabled_defaults_preserve_old_policy() -> None:
    """Missing extension settings remain inert."""
    restored = FocusRunSettings.model_validate_json(
        '{"autofocus_method":"none","focus_strategy":"single_autofocus"}'
    )
    assert not restored.surface.enabled
    assert restored.binding is None


def test_enabled_surface_requires_all_explicit_gates() -> None:
    """No numerical limit is guessed for an active external prediction."""
    with pytest.raises(ValidationError, match="every operational gate"):
        FocusSurfaceSettings(enabled=True)


def test_binding_check_reports_identity_and_readiness_separately() -> None:
    """Changed IDs are incompatible; candidate statuses are compatible but unusable."""
    expected = binding()
    mismatch = check_focus_binding(
        expected, expected.model_copy(update={"reference_id": "other"})
    )
    assert not mismatch.compatible
    candidate = check_focus_binding(
        expected,
        expected.model_copy(update={"rg_focus_model_status": "candidate"}),
    )
    assert candidate.compatible
    assert not candidate.usable


def test_binding_axes_and_signs_are_strict() -> None:
    """Stage conversion cannot silently accept reordered or boolean signs."""
    with pytest.raises(ValidationError):
        FocusAxisScale(axis="x", units_per_mm=1000.0, direction_sign=True)
    with pytest.raises(ValidationError, match="X, Y and Z"):
        binding(axis_scales=tuple(reversed(binding().axis_scales)))


def test_white_budget_keeps_post_search_reserve() -> None:
    """One WHITE fallback may not consume its required R/G reserve."""
    with pytest.raises(ValidationError, match="exceed total budget"):
        white_settings(white_search_timeout_s=50, approach_and_rg_reserve_s=40)


def test_confirmed_observation_requires_verified_rg_provenance() -> None:
    """A numeric Z alone cannot become trusted surface support."""
    with pytest.raises(ValidationError, match="independently verified"):
        observation(quality=quality(independent_post_move_verification=False))
    with pytest.raises(ValidationError, match="measurement provenance"):
        observation(provenance=provenance(rg_measurement_id=None))


def test_prediction_evidence_signed_error_is_checked() -> None:
    """Persisted pre-update error must equal measured minus predicted Z."""
    evidence = FocusPredictionEvidence(
        prediction_id="prediction-1",
        predicted_z_um=2,
        prediction_error_um=3,
        support_age_s=1,
    )
    with pytest.raises(ValidationError, match="inconsistent"):
        observation(z_um=4, pre_update_prediction=evidence)


def test_prediction_statuses_have_disjoint_payloads() -> None:
    """Only a usable response may carry a motor target or support IDs."""
    assert usable_prediction(target_z_um=0).target_z_um == 0
    with pytest.raises(ValidationError, match="Only usable"):
        FocusPrediction(
            scan_id="scan-1",
            field_id="field-1",
            attempt_id="attempt-1",
            prediction_id="prediction-1",
            target_x_um=0,
            target_y_um=0,
            target_z_um=5,
            status="unavailable",
            reason="no support",
            unavailable_reason="no_observations",
            binding=binding(),
            predicted_monotonic_s=1,
        )


def test_contracts_roundtrip_as_json_without_python_objects() -> None:
    """Request and response values survive the process serialization boundary."""
    request = prediction_request()
    assert (
        FocusPredictionRequest.model_validate_json(request.model_dump_json()) == request
    )
    response = usable_prediction()
    assert FocusPrediction.model_validate_json(response.model_dump_json()) == response


def test_field_progress_rejects_hidden_white_retry() -> None:
    """The GPL checkpoint contract preserves one WHITE search per attempt."""
    with pytest.raises(ValidationError, match="Exactly one WHITE"):
        FocusFieldProgress(
            scan_id="scan-1",
            field_id="field-1",
            attempt_id="attempt-1",
            prediction_id="prediction-1",
            stage="planned",
            white_search_id="white-1",
            white_search_count=2,
            white_status="planned",
            rg_status="not_started",
            recorded_monotonic_s=1,
        )
