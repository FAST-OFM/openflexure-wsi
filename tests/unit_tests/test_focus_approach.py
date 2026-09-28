"""Pure XYZ/approach planning and fake-controller execution checks."""

import json
import time

import httpx
import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.fast_ofm_contracts import MotionSettings
from openflexure_microscope_server.focus.focus_approach import (
    AxisEnvelope,
    AxisMotionProfile,
    CorrectionProgress,
    FocusApproachPolicy,
    FocusApproachRequest,
    FocusCorrectionRequest,
    FocusFieldBudget,
    FocusInitialCorrectionRequest,
    PreparedApproach,
    StageMotionSnapshot,
    WhitePeakApproachRequest,
    WhiteSweepRequest,
    plan_focus_approach,
    plan_focus_correction,
    plan_initial_focus_correction,
    plan_white_peak_approach,
    plan_white_sweep,
    validate_executable_focus_plan,
)
from openflexure_microscope_server.focus.focus_surface import (
    FocusAxisScale,
    FocusBinding,
    FocusPrediction,
    FocusPredictionDiagnostics,
    WhiteSearchSettings,
)
from openflexure_microscope_server.focus.rg.rg_focus_calibration import (
    RGFocusApproachSettings,
)
from openflexure_microscope_server.things.stage import moonraker as stage_module
from openflexure_microscope_server.things.stage.moonraker import (
    MoonrakerStage,
    StageSafetyError,
)
from tests.unit_tests.test_moonraker_stage import Controller


def axis(
    name,
    *,
    scale=1000.0,
    sign=1,
    limits=(-100.0, 100.0),
    single=25.0,
    speed=1000.0,
    timeout=15.0,
):
    """Return an explicit test-only axis profile in physical micrometres."""
    envelope = AxisEnvelope(minimum_um=limits[0], maximum_um=limits[1])
    return AxisMotionProfile(
        axis=name,
        units_per_mm=scale,
        direction_sign=sign,
        travel=envelope,
        controller=envelope,
        experiment=envelope,
        maximum_single_move_um=single,
        velocity_um_s=speed,
        acceleration_um_s2=5000.0,
        move_timeout_s=timeout,
        settle_s=0.01,
    )


def snapshot(**overrides):
    """Return a commanded readback with unequal scale/sign identities."""
    values = {
        "reference_id": "reference-1",
        "motion_revision": 4,
        "reference_origin_commanded_mm": (1.0, -2.0, 0.5),
        "current_commanded_xyz_um": (0.0, 0.0, 5.0),
        "axes": (
            axis("x", scale=1000.0, sign=-1),
            axis("y", scale=2000.0, sign=1),
            axis("z", scale=4000.0, sign=-1),
        ),
        "controller_max_velocity_um_s": 20000.0,
        "controller_max_acceleration_um_s2": 50000.0,
        "request_timeout_s": 0.1,
    }
    values.update(overrides)
    return StageMotionSnapshot(**values)


def binding(stage_snapshot=None, *, approach_sign=1, reference_id=None, **overrides):
    """Return a complete valid binding matched to the stage snapshot."""
    stage_snapshot = stage_snapshot or snapshot()
    values = {
        "stage_controller_id": "moonraker-main",
        "reference_id": reference_id or stage_snapshot.reference_id,
        "reference_status": "valid",
        "axis_scales": tuple(
            FocusAxisScale(
                axis=item.axis,
                units_per_mm=item.units_per_mm,
                direction_sign=item.direction_sign,
            )
            for item in stage_snapshot.axes
        ),
        "camera_stage_mapping_id": "mapping-1",
        "camera_stage_mapping_status": "valid",
        "geometry_id": "geometry-1",
        "rg_focus_model_id": "rg-model-1",
        "rg_focus_model_status": "valid",
        "red_flat_field_profile_id": "red-flat-1",
        "red_flat_field_status": "valid",
        "green_flat_field_profile_id": "green-flat-1",
        "green_flat_field_status": "valid",
        "approach_profile_id": "approach-1",
        "approach_status": "valid",
        "approach_parameters": RGFocusApproachSettings(
            preload_um=8, approach_sign=approach_sign
        ),
    }
    values.update(overrides)
    return FocusBinding(**values)


def diagnostics():
    """Return numerical diagnostics without treating residual as uncertainty."""
    return FocusPredictionDiagnostics(
        candidate_observation_count=1,
        local_observation_count=1,
        selected_support_count=1,
        nearest_distance_um=10.0,
        support_age_s=2.0,
        fit_rmse_um=0.2,
        maximum_absolute_residual_um=0.3,
        prediction_delta_um=1.0,
    )


def prediction(stage_binding, **overrides):
    """Return a usable typed prediction for the actual requested XY."""
    values = {
        "scan_id": "scan-1",
        "field_id": "field-2",
        "attempt_id": "attempt-1",
        "prediction_id": "prediction-1",
        "target_x_um": 20.25,
        "target_y_um": -10.25,
        "target_z_um": 5.0,
        "status": "usable",
        "source": "nearest_neighbor",
        "reason": "bounded neighbor",
        "binding": stage_binding,
        "support_observation_ids": ("observation-1",),
        "support_field_ids": ("field-1",),
        "support_distances_um": (10.0,),
        "diagnostics": diagnostics(),
        "predicted_monotonic_s": 20.0,
    }
    values.update(overrides)
    return FocusPrediction(**values)


def unavailable_prediction(stage_binding, **overrides):
    """Return an optical unavailable result that can select the ordinary/WHITE path."""
    values = {
        "scan_id": "scan-1",
        "field_id": "field-2",
        "attempt_id": "attempt-1",
        "prediction_id": "prediction-1",
        "target_x_um": 20.25,
        "target_y_um": -10.25,
        "status": "unavailable",
        "reason": "No nearby support",
        "unavailable_reason": "outside_support",
        "binding": stage_binding,
        "predicted_monotonic_s": 20.0,
    }
    values.update(overrides)
    return FocusPrediction(**values)


def policy(**overrides):
    """Return bounded asymmetric R/G and approach values used only by tests."""
    values = {
        "approach_profile_id": "approach-1",
        "measurement_offset_um": -3.0,
        "expected_prediction_error_range_um": (-1.0, 1.0),
        "rg_applicable_error_range_um": (-10.0, 8.0),
        "focus_tolerance_um": 1.0,
        "maximum_single_correction_um": 12.0,
        "maximum_total_correction_um": 20.0,
        "maximum_iterations": 3,
    }
    values.update(overrides)
    return FocusApproachPolicy(**values)


def budget(**overrides):
    """Return one cumulative field budget with ample test-only capacity."""
    values = {
        "budget_id": "field-budget-1",
        "origin_xyz_um": (0.0, 0.0, 5.0),
        "maximum_elapsed_s": 200.0,
        "elapsed_s": 5.0,
        "maximum_z_travel_um": 200.0,
        "z_travel_um": 2.0,
        "post_move_verification_reserve_s": 5.0,
    }
    values.update(overrides)
    values.setdefault(
        "deadline_monotonic_s",
        time.monotonic() + values["maximum_elapsed_s"] - values["elapsed_s"],
    )
    return FocusFieldBudget(**values)


def approach_request(**overrides):
    """Return all pure preparation inputs with no hardware owner."""
    stage_snapshot = overrides.pop("stage_snapshot", snapshot())
    stage_binding = overrides.pop("frozen_binding", binding(stage_snapshot))
    values = {
        "plan_id": "plan-1",
        "prediction": prediction(stage_binding),
        "target_xy_um": (20.25, -10.25),
        "stage_snapshot": stage_snapshot,
        "frozen_binding": stage_binding,
        "current_binding": stage_binding,
        "policy": policy(),
        "budget": budget(),
        "progress": CorrectionProgress(
            measurement_iteration=1, total_correction_um=0.0
        ),
    }
    values.update(overrides)
    return FocusApproachRequest(**values)


@pytest.mark.parametrize("approach_sign", [-1, 1])
def test_full_joint_xyz_then_final_approach_for_both_signs(approach_sign):
    """E04/E11: measurement is on the chosen side and preload is completed first."""
    stage_snapshot = snapshot()
    stage_binding = binding(stage_snapshot, approach_sign=approach_sign)
    request = approach_request(
        stage_snapshot=stage_snapshot,
        frozen_binding=stage_binding,
        current_binding=stage_binding,
        prediction=prediction(stage_binding),
        policy=policy(measurement_offset_um=-3.0 * approach_sign),
    )
    result = plan_focus_approach(request)
    assert result.status == "planned"
    assert result.segments[-1].phase == "measurement"
    assert result.segments[0].target_stage_units[:2] == (20, -20)
    expected_focus_units = round(5.0 * 4)
    measurement_units = result.final_stage_units[2]
    assert (measurement_units - expected_focus_units) * approach_sign < 0
    previous_z = result.segments[-2].target_stage_units[2]
    assert (measurement_units - previous_z) * approach_sign > 0
    assert result.reserved_correction_endpoints_um
    assert (
        result.reserved_motion_time_s > request.budget.post_move_verification_reserve_s
    )


@pytest.mark.parametrize("approach_sign", [-1, 1])
def test_signed_measurement_offset_preserves_declared_mirror_path(approach_sign):
    """OF-064 offset is signed: focus +/-6, offset -/+3, preload 8."""
    axes = tuple(
        axis(
            name,
            scale=1000.0,
            sign=1,
            limits=(-500.0, 500.0) if name != "z" else (-24.0, 24.0),
            single=250.0,
        )
        for name in ("x", "y", "z")
    )
    stage_snapshot = snapshot(
        axes=axes, current_commanded_xyz_um=(0.0, 0.0, approach_sign * 4.0)
    )
    stage_binding = binding(stage_snapshot, approach_sign=approach_sign)
    result = plan_focus_approach(
        approach_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=prediction(
                stage_binding,
                target_x_um=100.0,
                target_y_um=200.0,
                target_z_um=approach_sign * 6.0,
            ),
            target_xy_um=(100.0, 200.0),
            policy=policy(
                measurement_offset_um=-3.0 * approach_sign,
                rg_applicable_error_range_um=(-8.0, 8.0),
            ),
        )
    )
    assert result.status == "planned"
    assert [segment.target_xyz_um for segment in result.segments] == [
        (100.0, 200.0, -5.0 * approach_sign),
        (100.0, 200.0, 3.0 * approach_sign),
    ]


@pytest.mark.parametrize("approach_sign", [-1, 1])
def test_signed_measurement_offset_must_match_approach_direction(approach_sign):
    """A signed offset on the post-focus side refuses rather than being reinterpreted."""
    stage_snapshot = snapshot()
    stage_binding = binding(stage_snapshot, approach_sign=approach_sign)
    result = plan_focus_approach(
        approach_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=prediction(stage_binding),
            policy=policy(measurement_offset_um=3.0 * approach_sign),
        )
    )
    assert result.status == "refused"
    assert result.reason_code == "measurement_offset_direction"
    assert not result.segments


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ((-20.0, -20.0, 5.0), (20.25, -10.25)),
        ((30.0, 20.0, 5.0), (20.25, -10.25)),
        ((20.0, -10.0, -20.0), (20.25, -10.25)),
    ],
)
def test_current_target_relations_and_axis_sign_is_identity_only(current, target):
    """E04: all relative directions quantize per axis without applying adapter sign."""
    stage_snapshot = snapshot(current_commanded_xyz_um=current)
    stage_binding = binding(stage_snapshot)
    result = plan_focus_approach(
        approach_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            target_xy_um=target,
            prediction=prediction(
                stage_binding, target_x_um=target[0], target_y_um=target[1]
            ),
        )
    )
    assert result.status == "planned"
    assert result.final_stage_units[:2] == (
        round(target[0]),
        round(target[1] * 2),
    )


def test_coarse_scale_rounds_preload_outward_without_expanding_white_range():
    """E04/E26: preload remains complete while bounded WHITE endpoints round inward."""
    coarse_z = axis("z", scale=300.0, sign=-1, single=30.0)
    stage_snapshot = snapshot(axes=(snapshot().axes[0], snapshot().axes[1], coarse_z))
    stage_binding = binding(stage_snapshot)
    approach = plan_focus_approach(
        approach_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=prediction(stage_binding),
        )
    )
    assert approach.status == "planned"
    final_preload_um = (
        approach.segments[-1].target_xyz_um[2] - approach.segments[-2].target_xyz_um[2]
    )
    assert final_preload_um >= 8.0
    white = plan_white_sweep(
        white_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=unavailable_prediction(stage_binding),
        )
    )
    assert white.status == "planned"
    assert white.possible_peak_z_um is not None
    assert white.possible_peak_z_um[0] >= 5.0 - 12.0
    assert white.possible_peak_z_um[1] <= 5.0 + 8.0


def test_unavailable_is_ordinary_but_fault_is_refused():
    """E08/E25: optical absence and a contract fault remain distinct outcomes."""
    request = approach_request()
    ordinary = plan_focus_approach(
        request.model_copy(
            update={"prediction": unavailable_prediction(request.frozen_binding)}
        )
    )
    fault = FocusPrediction(
        scan_id="scan-1",
        field_id="field-2",
        attempt_id="attempt-1",
        prediction_id="bad",
        target_x_um=20.25,
        target_y_um=-10.25,
        status="fault",
        reason="binding mismatch",
        fault_code="binding_mismatch",
        predicted_monotonic_s=20.0,
    )
    refused = plan_focus_approach(request.model_copy(update={"prediction": fault}))
    lost = plan_focus_approach(
        request.model_copy(
            update={
                "prediction": unavailable_prediction(request.frozen_binding),
                "current_binding": request.current_binding.model_copy(
                    update={"reference_status": "lost"}
                ),
            }
        )
    )
    assert ordinary.status == "ordinary"
    assert refused.status == "refused"
    assert lost.status == "refused"
    assert lost.reason_code == "binding_changed"
    assert not ordinary.segments
    assert not refused.segments


@pytest.mark.parametrize(
    ("approach_sign", "applicable", "expected"),
    [
        (1, (-4.0, 8.0), "planned"),
        (1, (-3.99, 8.0), "refused"),
        (-1, (-8.0, 4.0), "planned"),
        (-1, (-8.0, 3.99), "refused"),
    ],
)
def test_asymmetric_rg_range_checks_both_prediction_error_bounds(
    approach_sign, applicable, expected
):
    """E10: explicit prediction error, not fit residual, gates both model edges."""
    stage_snapshot = snapshot()
    stage_binding = binding(stage_snapshot, approach_sign=approach_sign)
    result = plan_focus_approach(
        approach_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=prediction(stage_binding),
            policy=policy(
                measurement_offset_um=-3.0 * approach_sign,
                rg_applicable_error_range_um=applicable,
            ),
        )
    )
    assert result.status == expected


def test_asymmetric_model_reserves_only_declared_prediction_error_range():
    """A wide model tail cannot make a narrower, fully bounded field impossible."""
    stage_snapshot = snapshot()
    stage_binding = binding(stage_snapshot)
    result = plan_focus_approach(
        approach_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=prediction(stage_binding),
            policy=policy(
                expected_prediction_error_range_um=(-5.0, 5.0),
                measurement_offset_um=-3.0,
                rg_applicable_error_range_um=(-8.0, 24.0),
                maximum_single_correction_um=16.0,
                maximum_total_correction_um=16.0,
            ),
        )
    )
    assert result.status == "planned"
    assert result.reserved_correction_endpoints_um


def test_unsafe_preload_or_late_correction_reserve_sends_no_partial_plan():
    """E13: a safe final point cannot hide an unsafe preload or later reserve."""
    tight_z = axis("z", scale=4000.0, sign=-1, limits=(-9.0, 20.0))
    stage_snapshot = snapshot(axes=(snapshot().axes[0], snapshot().axes[1], tight_z))
    stage_binding = binding(stage_snapshot)
    result = plan_focus_approach(
        approach_request(
            stage_snapshot=stage_snapshot,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=prediction(stage_binding),
            policy=policy(expected_prediction_error_range_um=(-8.0, 1.0)),
        )
    )
    assert result.status == "refused"
    assert result.reason_code in {"travel_envelope", "correction_reserve"}
    assert not result.segments


def test_controller_dynamics_and_cumulative_field_budgets_refuse_before_segments():
    """E13/E14: controller speed, segment timeout and one field budget stay frozen."""
    slow = snapshot(controller_max_velocity_um_s=1.0)
    slow_binding = binding(slow)
    timeout_result = plan_focus_approach(
        approach_request(
            stage_snapshot=slow,
            frozen_binding=slow_binding,
            current_binding=slow_binding,
            prediction=prediction(slow_binding),
        )
    )
    budget_result = plan_focus_approach(
        approach_request(
            budget=budget(maximum_elapsed_s=5.1),
        )
    )
    assert timeout_result.reason_code == "move_timeout"
    assert budget_result.reason_code == "time_budget"
    assert not timeout_result.segments
    assert not budget_result.segments


def test_preparation_reserves_an_iteration_for_post_move_rg_verification():
    """E11/E26: a prepared move is refused when no verification iteration remains."""
    result = plan_focus_approach(
        approach_request(
            progress=CorrectionProgress(
                measurement_iteration=3, total_correction_um=0.0
            )
        )
    )
    assert result.status == "refused"
    assert result.reason_code == "iteration_reserve"
    assert not result.segments


@pytest.mark.parametrize(
    ("error", "purpose", "phases"),
    [
        (-2.0, "direct_correction", ("direct_correction",)),
        (
            2.0,
            "reapproach_correction",
            ("correction_preload", "correction_measurement"),
        ),
    ],
)
def test_correction_uses_live_preparation_and_no_double_preload(error, purpose, phases):
    """E11: direct load continues once; reverse load performs one full reapproach."""
    request = approach_request()
    initial = plan_focus_approach(request)
    prepared = PreparedApproach(
        source_plan_id=initial.plan_id,
        reference_id=request.stage_snapshot.reference_id,
        motion_revision=request.stage_snapshot.motion_revision,
        xyz_stage_units=initial.final_stage_units,
        binding=request.frozen_binding,
        policy=request.policy,
        budget_after=initial.budget_after,
        progress=request.progress,
        approach_sign=request.frozen_binding.approach_parameters.approach_sign,
    )
    current = request.stage_snapshot.model_copy(
        update={
            "current_commanded_xyz_um": initial.segments[-1].target_xyz_um,
        }
    )
    correction = plan_focus_correction(
        FocusCorrectionRequest(
            plan_id="correction-1",
            inferred_error_um=error,
            stage_snapshot=current,
            current_binding=request.current_binding,
            prepared=prepared,
            budget=initial.budget_after,
            progress=request.progress,
        )
    )
    assert correction.status == "planned"
    assert correction.purpose == purpose
    assert tuple(dict.fromkeys(item.phase for item in correction.segments)) == phases
    assert correction.requires_rg_verification


def correction_request_for_error(error, *, z_scale=4000.0, tolerance=1.0):
    """Build internally consistent preparation evidence for correction decisions."""
    stage_snapshot = snapshot(
        axes=(
            snapshot().axes[0],
            snapshot().axes[1],
            axis("z", scale=z_scale, sign=-1),
        )
    )
    stage_binding = binding(stage_snapshot)
    frozen_policy = policy(focus_tolerance_um=tolerance)
    current = stage_snapshot.model_copy(
        update={"current_commanded_xyz_um": (20.0, -10.0, 2.0)}
    )
    progress = CorrectionProgress(measurement_iteration=1, total_correction_um=0.0)
    field_budget = budget()
    prepared = PreparedApproach(
        source_plan_id="prepared-1",
        reference_id=current.reference_id,
        motion_revision=current.motion_revision,
        xyz_stage_units=current.current_stage_units,
        binding=stage_binding,
        policy=frozen_policy,
        budget_after=field_budget,
        progress=progress,
        approach_sign=1,
    )
    return FocusCorrectionRequest(
        plan_id="correction-1",
        inferred_error_um=error,
        stage_snapshot=current,
        current_binding=stage_binding,
        prepared=prepared,
        budget=field_budget,
        progress=progress,
    )


def test_in_tolerance_is_no_move_and_subquantum_is_resolution_limited():
    """E12: zero/subquantum outcomes cannot become movement or a retry loop."""
    focused = plan_focus_correction(correction_request_for_error(0.5))
    limited = plan_focus_correction(
        correction_request_for_error(2.0, z_scale=100.0, tolerance=1.0)
    )
    assert focused.status == "focused"
    assert not focused.segments
    assert limited.status == "resolution_limited"
    assert not limited.segments


@pytest.mark.parametrize(
    "change", ["reference", "revision", "position", "budget", "deadline"]
)
def test_preparation_invalidates_on_identity_history_position_or_budget(change):
    """E19/E20: matching XYZ cannot restore a changed reference or motion history."""
    request = correction_request_for_error(-2.0)
    stage_snapshot = request.stage_snapshot
    field_budget = request.budget
    if change == "reference":
        stage_snapshot = stage_snapshot.model_copy(update={"reference_id": "new-zero"})
    elif change == "revision":
        stage_snapshot = stage_snapshot.model_copy(
            update={"motion_revision": stage_snapshot.motion_revision + 2}
        )
    elif change == "position":
        stage_snapshot = stage_snapshot.model_copy(
            update={"current_commanded_xyz_um": (21.0, -10.0, 2.0)}
        )
    elif change == "budget":
        field_budget = field_budget.model_copy(update={"elapsed_s": 0.0})
    else:
        field_budget = field_budget.model_copy(
            update={"deadline_monotonic_s": field_budget.deadline_monotonic_s + 1.0}
        )
    result = plan_focus_correction(
        request.model_copy(
            update={"stage_snapshot": stage_snapshot, "budget": field_budget}
        )
    )
    assert result.status == "refused"
    assert not result.segments


def white_request(**overrides):
    """Return explicit asymmetric WHITE search inputs without a default 50 um."""
    stage_snapshot = overrides.pop("stage_snapshot", snapshot())
    stage_binding = overrides.pop("frozen_binding", binding(stage_snapshot))
    values = {
        "plan_id": "white-plan-1",
        "prediction": unavailable_prediction(stage_binding),
        "target_xy_um": (20.25, -10.25),
        "centre_z_um": 5.0,
        "stage_snapshot": stage_snapshot,
        "frozen_binding": stage_binding,
        "current_binding": stage_binding,
        "policy": policy(),
        "budget": budget(),
        "settings": WhiteSearchSettings(
            search_z_range_um=(-12.0, 8.0),
            white_search_timeout_s=50.0,
            total_focus_budget_s=120.0,
            approach_and_rg_reserve_s=50.0,
            maximum_white_led_disagreement_um=1.0,
        ),
    }
    values.update(overrides)
    return WhiteSweepRequest(**values)


def test_white_sweep_covers_asymmetric_full_native_range_and_transfer():
    """E26: pre-centre, sweep, base return, every peak and transfer are bounded."""
    request = white_request()
    result = plan_white_sweep(request)
    assert result.status == "planned"
    assert result.native_start == "base"
    assert result.native_total_dz_stage_units == 80
    assert result.native_sweep_high_z_stage_units == 52
    assert result.native_return_base_z_stage_units == -28
    assert result.possible_peak_z_um == (-7.0, 13.0)
    assert tuple(item.peak_z_um for item in result.peak_transfers) == (-7.0, 13.0)
    assert all(item.approach_segments for item in result.peak_transfers)
    assert min(result.transfer_envelope_z_um) < -7.0
    assert max(result.transfer_envelope_z_um) > 13.0
    assert result.leaves_preparation_unproved


def test_white_handoff_uses_signed_offset_for_negative_approach():
    """Every possible WHITE peak hands off above focus before a negative approach."""
    stage_snapshot = snapshot()
    stage_binding = binding(stage_snapshot, approach_sign=-1)
    request = white_request(
        stage_snapshot=stage_snapshot,
        frozen_binding=stage_binding,
        current_binding=stage_binding,
        prediction=unavailable_prediction(stage_binding),
        policy=policy(measurement_offset_um=3.0),
    )
    result = plan_white_sweep(request)
    assert result.status == "planned"
    for transfer in result.peak_transfers:
        measurement = transfer.approach_segments[-1].target_xyz_um[2]
        assert measurement == transfer.peak_z_um + 3.0
        first_measurement = next(
            index
            for index, segment in enumerate(transfer.approach_segments)
            if segment.phase == "measurement"
        )
        preload_start = (
            transfer.peak_z_um
            if first_measurement == 0
            else transfer.approach_segments[first_measurement - 1].target_xyz_um[2]
        )
        assert preload_start - measurement >= 8.0


@pytest.mark.parametrize("approach_sign", [-1, 1])
def test_actual_white_peak_plans_stage_owned_outward_preload_at_coarse_scale(
    approach_sign,
):
    """Actual WHITE evidence uses disagreement bounds and a complete coarse preload."""
    coarse_z = axis("z", scale=300.0, sign=-1, single=40.0)
    initial = snapshot(axes=(snapshot().axes[0], snapshot().axes[1], coarse_z))
    stage_binding = binding(initial, approach_sign=approach_sign)
    sweep = plan_white_sweep(
        white_request(
            stage_snapshot=initial,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            prediction=unavailable_prediction(stage_binding),
            policy=policy(measurement_offset_um=-3.0 * approach_sign),
        )
    )
    assert sweep.status == "planned"
    assert sweep.possible_peak_z_stage_units is not None
    peak_units = sweep.possible_peak_z_stage_units[0 if approach_sign == 1 else 1]
    peak_um = peak_units * 1000 / coarse_z.units_per_mm
    current = initial.model_copy(
        update={
            "motion_revision": initial.motion_revision + 3,
            "current_commanded_xyz_um": (20.0, -10.0, peak_um),
        }
    )
    assert sweep.budget_before is not None
    remaining = sweep.budget_before.model_copy(
        update={"elapsed_s": sweep.budget_before.elapsed_s + 1}
    )
    result = plan_white_peak_approach(
        WhitePeakApproachRequest(
            plan_id="actual-white-handoff",
            sweep_plan=sweep,
            actual_peak_z_stage_units=peak_units,
            stage_snapshot=current,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            policy=policy(measurement_offset_um=-3.0 * approach_sign),
            budget=remaining,
            progress=CorrectionProgress(measurement_iteration=1, total_correction_um=0),
            current_monotonic_s=time.monotonic(),
        )
    )
    assert result.status == "planned"
    assert result.purpose == "white_peak_preparation"
    assert result.policy.expected_prediction_error_range_um == (-1.0, 1.0)
    preload = abs(
        result.segments[-1].target_xyz_um[2] - result.segments[-2].target_xyz_um[2]
    )
    assert preload >= stage_binding.approach_parameters.preload_um
    validate_executable_focus_plan(result)


def test_actual_white_peak_outside_original_sweep_is_refused():
    """A live Z outside the frozen native sweep cannot become WHITE hand-off proof."""
    initial = snapshot()
    stage_binding = binding(initial)
    request = white_request(
        stage_snapshot=initial,
        frozen_binding=stage_binding,
        current_binding=stage_binding,
        prediction=unavailable_prediction(stage_binding),
    )
    sweep = plan_white_sweep(request)
    assert sweep.possible_peak_z_stage_units is not None
    peak_units = sweep.possible_peak_z_stage_units[1] + 1
    current = initial.model_copy(
        update={
            "motion_revision": initial.motion_revision + 3,
            "current_commanded_xyz_um": (
                20.0,
                -10.0,
                peak_units * 1000 / initial.axes[2].units_per_mm,
            ),
        }
    )
    result = plan_white_peak_approach(
        WhitePeakApproachRequest(
            plan_id="outside-white-sweep",
            sweep_plan=sweep,
            actual_peak_z_stage_units=peak_units,
            stage_snapshot=current,
            frozen_binding=stage_binding,
            current_binding=stage_binding,
            policy=request.policy,
            budget=request.budget,
            progress=CorrectionProgress(measurement_iteration=1, total_correction_um=0),
            current_monotonic_s=time.monotonic(),
        )
    )
    assert result.status == "refused"
    assert result.reason_code == "white_peak_range"
    assert not result.segments


@pytest.mark.parametrize(
    "change", ["native_range", "original_budget", "field_limits", "policy", "deadline"]
)
def test_serialized_white_context_refuses_before_controller_post(focus_stage, change):
    """Canonical JSON stays valid, but inconsistent frozen WHITE evidence cannot move."""
    request = white_request()
    sweep = plan_white_sweep(request)
    assert sweep.status == "planned"
    current = request.stage_snapshot.model_copy(
        update={
            "motion_revision": request.stage_snapshot.motion_revision + 3,
            "current_commanded_xyz_um": (20.0, -10.0, 0.0),
        }
    )
    plan = plan_white_peak_approach(
        WhitePeakApproachRequest(
            plan_id="serialized-white-context",
            sweep_plan=sweep,
            actual_peak_z_stage_units=0,
            stage_snapshot=current,
            frozen_binding=request.frozen_binding,
            current_binding=request.current_binding,
            policy=request.policy,
            budget=request.budget,
            progress=CorrectionProgress(measurement_iteration=1, total_correction_um=0),
            current_monotonic_s=time.monotonic(),
        )
    )
    assert plan.status == "planned", plan.reason
    validate_executable_focus_plan(
        type(plan).model_validate_json(plan.model_dump_json())
    )
    payload = plan.model_dump(mode="json")
    original = payload["white_sweep_plan"]
    if change == "native_range":
        original["settings"]["search_z_range_um"] = [-1.0, 1.0]
    elif change == "original_budget":
        original["budget_before"]["deadline_monotonic_s"] += 10.0
    elif change == "field_limits":
        original["budget_before"]["maximum_z_travel_um"] += 1.0
    elif change == "policy":
        payload["policy"]["focus_tolerance_um"] += 0.1
    else:
        original["total_focus_deadline_monotonic_s"] += 10.0
    # Decode before the expected refusal: tuple/list schema rejection must not
    # masquerade as the semantic check this test is intended to exercise.
    decoded = type(plan).model_validate_json(json.dumps(payload))
    with pytest.raises(ValueError, match="WHITE"):
        validate_executable_focus_plan(decoded)
    stage, controller = focus_stage
    with pytest.raises(StageSafetyError, match="Invalid focus plan semantics.*WHITE"):
        stage.execute_focus_plan(decoded, plan.binding)
    assert not controller.scripts
    assert stage.controller_state["reference_valid"]


def test_initial_selected_correction_preflights_complete_field_travel():
    """The first selected-method correction includes preload before any execution."""
    current = snapshot(current_commanded_xyz_um=(0.0, 0.0, 0.0))
    stage_binding = binding(current)
    request = FocusInitialCorrectionRequest(
        plan_id="selected-initial-correction",
        inferred_error_um=4.0,
        stage_snapshot=current,
        frozen_binding=stage_binding,
        current_binding=stage_binding,
        policy=policy(),
        budget=budget(
            origin_xyz_um=(0.0, 0.0, 0.0),
            maximum_z_travel_um=10.0,
            z_travel_um=0.0,
        ),
        progress=CorrectionProgress(measurement_iteration=1, total_correction_um=0),
    )
    refused = plan_initial_focus_correction(request)
    assert refused.status == "refused"
    assert refused.reason_code == "z_travel_budget"
    planned = plan_initial_focus_correction(
        request.model_copy(
            update={
                "budget": request.budget.model_copy(
                    update={"maximum_z_travel_um": 20.0}
                )
            }
        )
    )
    assert planned.status == "planned"
    assert [segment.target_xyz_um[2] for segment in planned.segments] == [-12.0, -4.0]
    validate_executable_focus_plan(planned)


def test_white_native_single_move_and_late_transfer_are_checked_before_plan():
    """E13/E26: native total dz and a dangerous later hand-off both refuse cleanly."""
    z_single = axis("z", scale=4000.0, sign=-1, single=10.0)
    short_snapshot = snapshot(axes=(snapshot().axes[0], snapshot().axes[1], z_single))
    short_binding = binding(short_snapshot)
    single_refusal = plan_white_sweep(
        white_request(
            stage_snapshot=short_snapshot,
            frozen_binding=short_binding,
            current_binding=short_binding,
            prediction=unavailable_prediction(short_binding),
        )
    )
    tight_z = axis("z", scale=4000.0, sign=-1, limits=(-17.0, 20.0))
    tight_snapshot = snapshot(axes=(snapshot().axes[0], snapshot().axes[1], tight_z))
    tight_binding = binding(tight_snapshot)
    transfer_refusal = plan_white_sweep(
        white_request(
            stage_snapshot=tight_snapshot,
            frozen_binding=tight_binding,
            current_binding=tight_binding,
            prediction=unavailable_prediction(tight_binding),
        )
    )
    assert single_refusal.reason_code == "white_single_command"
    assert transfer_refusal.status == "refused"
    assert not transfer_refusal.precentre_segments


@pytest.fixture
def focus_stage(mocker):
    """Create the real MoonrakerStage with only an HTTP-level fake controller."""
    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    controller = Controller()
    stage = create_thing_without_server(MoonrakerStage, hardware={"allow_motion": True})
    profile = stage.hardware_settings
    profile["axes"]["z"].update(
        enabled=True,
        min_mm=-0.1,
        max_mm=0.1,
        max_move_mm=0.025,
        ui_step_mm=0.005,
        units_per_mm=1000.0,
        direction_sign=-1,
        speed_mm_s=0.1,
        accel_mm_s2=1.0,
        settle_ms=10.0,
    )
    stage._hardware = MotionSettings.model_validate(profile)
    stage._client = httpx.Client(
        base_url="http://controller", transport=httpx.MockTransport(controller.handle)
    )
    stage.set_zero_position(confirmed_manual_zero=True, exclusive_control=True)
    yield stage, controller
    stage._client.close()


def real_stage_plan(stage, **budget_overrides):
    """Build a pure plan entirely from a fresh real-stage adapter snapshot."""
    budget_values = {"maximum_elapsed_s": 500.0, **budget_overrides}
    live = stage.focus_motion_snapshot(
        {"x": (-500.0, 500.0), "y": (-500.0, 500.0), "z": (-50.0, 50.0)}
    )
    stage_binding = binding(live)
    request = approach_request(
        stage_snapshot=live,
        frozen_binding=stage_binding,
        current_binding=stage_binding,
        prediction=prediction(
            stage_binding,
            target_x_um=100.0,
            target_y_um=-50.0,
            target_z_um=0.0,
        ),
        target_xy_um=(100.0, -50.0),
        budget=budget(**budget_values),
    )
    return plan_focus_approach(request), stage_binding


def test_real_stage_executes_plan_and_issues_only_verified_preparation(focus_stage):
    """Execute through MoonrakerStage helpers; adapter sign is applied exactly once."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    assert plan.status == "planned"
    prepared = stage.execute_focus_plan(plan, stage_binding)
    assert prepared.reference_id == stage.controller_state["reference_id"]
    assert prepared.motion_revision == stage.controller_state["motion_revision"]
    assert prepared.xyz_stage_units == plan.final_stage_units
    assert stage.position == {"x": 100, "y": -50, "z": -3}
    assert any("Z0.011000" in script for script in controller.scripts)
    assert all("M400" in script for script in controller.scripts)


def test_serialized_semantic_path_substitutions_refuse_before_first_post(focus_stage):
    """A schema-valid payload cannot replace the complete final loaded approach."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    original = plan.model_dump(mode="json")
    final = plan.segments[-1]
    sign = plan.binding.approach_parameters.approach_sign

    variants = {}

    no_op = json.loads(json.dumps(original))
    no_op_row = no_op["segments"][-1]
    no_op_row["target_stage_units"] = list(plan.stage_snapshot.current_stage_units)
    no_op_row["target_xyz_um"] = list(plan.stage_snapshot.current_quantized_xyz_um)
    no_op["segments"] = [no_op_row]
    variants["no-op"] = no_op

    xy_only = json.loads(json.dumps(original))
    xy_row = xy_only["segments"][-1]
    xy_row["target_stage_units"][2] = plan.stage_snapshot.current_stage_units[2]
    xy_row["target_xyz_um"][2] = plan.stage_snapshot.current_quantized_xyz_um[2]
    xy_only["segments"] = [xy_row]
    variants["XY-only"] = xy_only

    too_short = json.loads(json.dumps(original))
    too_short["segments"][0]["target_stage_units"][2] = (
        final.target_stage_units[2] - sign
    )
    too_short["segments"][0]["target_xyz_um"][2] = final.target_xyz_um[2] - sign
    variants["short preload"] = too_short

    reordered = json.loads(json.dumps(original))
    reordered["segments"] = list(reversed(reordered["segments"]))
    variants["reordered"] = reordered

    wrong_phases = json.loads(json.dumps(original))
    wrong_phases["segments"][0]["phase"] = "measurement"
    variants["phase substitution"] = wrong_phases

    wrong_physical = json.loads(json.dumps(original))
    wrong_physical["segments"][-1]["target_xyz_um"][2] += 0.25
    variants["physical endpoint"] = wrong_physical

    wrong_native = json.loads(json.dumps(original))
    wrong_native["segments"][-1]["target_stage_units"][2] += 1
    variants["native endpoint"] = wrong_native

    for name, payload in variants.items():
        forged = type(plan).model_validate_json(json.dumps(payload))
        with pytest.raises(StageSafetyError, match="semantic"):
            stage.execute_focus_plan(forged, stage_binding)
        assert not controller.scripts, name


def test_direct_correction_requires_matching_stage_issued_preparation(focus_stage):
    """Matching public fields do not replace the adapter's live preparation history."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    prepared = stage.execute_focus_plan(plan, stage_binding)
    submitted = len(controller.scripts)
    envelope = {
        axis.axis: (axis.experiment.minimum_um, axis.experiment.maximum_um)
        for axis in plan.stage_snapshot.axes
    }
    correction = plan_focus_correction(
        FocusCorrectionRequest(
            plan_id="direct-after-rg",
            inferred_error_um=-3.0,
            stage_snapshot=stage.focus_motion_snapshot(envelope),
            current_binding=stage_binding,
            prepared=prepared,
            budget=prepared.budget_after,
            progress=prepared.progress,
        )
    )
    assert correction.status == "planned"
    assert correction.purpose == "direct_correction"
    assert correction.required_preparation is not None
    forged_proof = correction.required_preparation.model_copy(
        update={"source_plan_id": "not-issued-by-this-stage"}
    )
    forged = correction.model_copy(update={"required_preparation": forged_proof})
    with pytest.raises(StageSafetyError, match="stage-issued preparation"):
        stage.execute_focus_plan(forged, stage_binding)
    assert len(controller.scripts) == submitted


@pytest.mark.parametrize(
    ("error", "purpose", "added_commands"),
    [(-3.0, "direct_correction", 1), (2.0, "reapproach_correction", 2)],
)
def test_stage_executes_correction_from_its_live_preparation(
    focus_stage, error, purpose, added_commands
):
    """Direct load continuity and reverse full approach both remain stage-owned."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    prepared = stage.execute_focus_plan(plan, stage_binding)
    submitted = len(controller.scripts)
    envelope = {
        axis.axis: (axis.experiment.minimum_um, axis.experiment.maximum_um)
        for axis in plan.stage_snapshot.axes
    }
    correction = plan_focus_correction(
        FocusCorrectionRequest(
            plan_id="verified-correction",
            inferred_error_um=error,
            stage_snapshot=stage.focus_motion_snapshot(envelope),
            current_binding=stage_binding,
            prepared=prepared,
            budget=prepared.budget_after,
            progress=prepared.progress,
        )
    )
    assert correction.status == "planned"
    assert correction.purpose == purpose
    corrected = stage.execute_focus_plan(correction, stage_binding)
    assert len(controller.scripts) == submitted + added_commands
    assert corrected.motion_revision == prepared.motion_revision + added_commands
    assert stage._prepared_approach == corrected


def test_stage_preparation_survives_light_but_not_new_zero(focus_stage):
    """SET_PIN queue accounting is non-mechanical; Set zero creates new identity."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    prepared = stage.execute_focus_plan(plan, stage_binding)
    old_reference = prepared.reference_id
    with stage._light_operation(stage._url):
        controller.status["toolhead"]["print_time"] += 1
    assert stage._prepared_approach == prepared
    stage.set_zero_position(confirmed_manual_zero=True, exclusive_control=True)
    assert stage.controller_state["reference_id"] != old_reference
    assert stage._prepared_approach is None


def test_stage_preparation_is_lost_after_out_and_back_motion(focus_stage):
    """Returning to identical XYZ cannot restore the stage-owned load history."""
    stage, _controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    prepared = stage.execute_focus_plan(plan, stage_binding)
    stage.move_absolute(z=prepared.xyz_stage_units[2] - 1)
    stage.move_absolute(z=prepared.xyz_stage_units[2])
    assert stage.position == dict(
        zip(stage.axis_names, prepared.xyz_stage_units, strict=True)
    )
    assert stage._prepared_approach is None
    assert stage.controller_state["motion_revision"] == prepared.motion_revision + 2


def test_runtime_budget_records_slow_successful_post_time(focus_stage, mocker):
    """Successful waits consume monotonic field time, not only the plan estimate."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    initial_time = time.monotonic()
    clock = [initial_time]
    post_delays = []
    original_post = stage._client.post

    def slow_valid_post(*args, **kwargs):
        delay = float(kwargs["timeout"]) * 0.75
        post_delays.append(delay)
        clock[0] += delay
        return original_post(*args, **kwargs)

    mocker.patch.object(stage_module.time, "monotonic", side_effect=lambda: clock[0])
    mocker.patch.object(stage._client, "post", side_effect=slow_valid_post)
    prepared = stage.execute_focus_plan(plan, stage_binding)
    initial_elapsed = plan.budget_before.elapsed_at(initial_time)
    assert len(post_delays) == len(plan.segments) == 2
    assert prepared.budget_after.elapsed_s >= initial_elapsed + sum(post_delays)
    assert prepared.budget_after.elapsed_s > plan.budget_after.elapsed_s


def test_runtime_budget_expiry_between_segments_keeps_confirmed_reference(
    focus_stage, mocker
):
    """A slow confirmed first leg blocks the second without claiming uncertainty."""
    stage, controller = focus_stage
    baseline, _ = real_stage_plan(stage)
    just_enough = (
        baseline.budget_before.elapsed_s
        + baseline.estimated_motion_time_s
        + baseline.reserved_motion_time_s
        + 1.0
    )
    plan, stage_binding = real_stage_plan(stage, maximum_elapsed_s=just_enough)
    clock = [time.monotonic()]
    original_post = stage._client.post

    def slow_valid_post(*args, **kwargs):
        clock[0] += float(kwargs["timeout"]) * 0.75
        return original_post(*args, **kwargs)

    mocker.patch.object(stage_module.time, "monotonic", side_effect=lambda: clock[0])
    mocker.patch.object(stage._client, "post", side_effect=slow_valid_post)
    with pytest.raises(StageSafetyError, match="field time budget"):
        stage.execute_focus_plan(plan, stage_binding)
    assert len(controller.scripts) == 1
    assert stage.controller_state["reference_valid"]
    assert stage.controller_state["motion_revision"] == 1
    assert not stage.moving
    assert stage._prepared_approach is None


def test_frozen_deadline_counts_delay_before_execution(focus_stage, mocker):
    """A serialized plan cannot regain field time by waiting before execution."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    required_remaining = plan.estimated_motion_time_s + plan.reserved_motion_time_s
    clock = [plan.budget_before.deadline_monotonic_s - required_remaining + 0.1]
    mocker.patch.object(stage_module.time, "monotonic", side_effect=lambda: clock[0])
    with pytest.raises(StageSafetyError, match="field time budget"):
        stage.execute_focus_plan(plan, stage_binding)
    assert not controller.scripts
    assert stage.controller_state["reference_valid"]
    assert stage._prepared_approach is None


def test_focus_executor_rechecks_budget_after_inner_read_before_post(
    focus_stage, mocker
):
    """A slow successful internal read refuses before POST without losing zero."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    clock = [time.monotonic()]
    original_segments = stage.move_absolute_in_segments
    original_fetch = stage._fetch_state
    inside_segments = False
    delayed = False

    def slow_inner_fetch():
        nonlocal delayed
        result = original_fetch()
        if inside_segments and not delayed:
            delayed = True
            clock[0] += plan.budget_before.maximum_elapsed_s + 1
        return result

    def enter_segments(*args, **kwargs):
        nonlocal inside_segments
        inside_segments = True
        return original_segments(*args, **kwargs)

    mocker.patch.object(stage_module.time, "monotonic", side_effect=lambda: clock[0])
    mocker.patch.object(stage, "_fetch_state", side_effect=slow_inner_fetch)
    mocker.patch.object(stage, "move_absolute_in_segments", side_effect=enter_segments)
    with pytest.raises(StageSafetyError, match="field time budget"):
        stage.execute_focus_plan(plan, stage_binding)
    assert delayed
    assert not controller.scripts
    assert stage.controller_state["reference_valid"]
    assert not stage.moving


@pytest.mark.parametrize("failure", ["timeout", "moving", "wrong_target"])
def test_sent_timeout_or_http200_without_idle_never_returns_preparation(
    focus_stage, failure
):
    """E15: one ambiguous command stops the plan without retry or false idle."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    if failure == "timeout":
        controller.timeout = True
    elif failure == "moving":
        controller.velocity_after_move = 0.1
    else:
        controller.wrong_target = True
    with pytest.raises(StageSafetyError, match="uncertain"):
        stage.execute_focus_plan(plan, stage_binding)
    assert len(controller.scripts) == 1
    assert stage._reference is None
    assert stage.controller_state["reference_id"] is None
    assert stage.moving


def test_stage_rechecks_unsafe_late_plan_segment_before_first_post(focus_stage):
    """A corrupted unsafe late part cannot allow an earlier safe command to escape."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    late = plan.segments[-1].model_copy(update={"target_stage_units": (100, -50, 1000)})
    corrupted = plan.model_copy(update={"segments": (*plan.segments[:-1], late)})
    with pytest.raises(StageSafetyError, match="semantic|envelope|controller limits"):
        stage.execute_focus_plan(corrupted, stage_binding)
    assert not controller.scripts


def test_focus_execution_keeps_uncommissioned_backlash_gate(focus_stage):
    """A nonzero upstream backlash setting still refuses before the first command."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    stage.backlash_steps = {"x": 1, "y": 0, "z": 0}
    with pytest.raises(StageSafetyError, match="Uncommissioned backlash"):
        stage.execute_focus_plan(plan, stage_binding)
    assert not controller.scripts


def test_cancel_between_plan_parts_stops_after_confirmed_command(focus_stage, mocker):
    """E18: cancellation cannot erase a sent move or enqueue the remaining plan."""
    import labthings_fastapi as lt

    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    sleep = mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep",
        side_effect=[None, None, lt.exceptions.InvocationCancelledError()],
    )
    with pytest.raises(lt.exceptions.InvocationCancelledError):
        stage.execute_focus_plan(plan, stage_binding)
    assert len(controller.scripts) == 1
    assert stage.controller_state["reference_valid"]
    assert stage.controller_state["motion_revision"] == 1
    sleep.side_effect = None


def test_final_readback_preserves_remaining_verification_reserve(focus_stage, mocker):
    """Final successful readback may consume slack, but never the mandatory reserve."""
    stage, controller = focus_stage
    plan, stage_binding = real_stage_plan(stage)
    required = plan.estimated_motion_time_s + plan.reserved_motion_time_s
    clock = [plan.budget_before.deadline_monotonic_s - required - 1.0]
    completed_segments = []
    original_move = stage.move_absolute_in_segments
    original_readback = stage._hardware_update_position

    def confirmed_move(*args, **kwargs):
        original_move(*args, **kwargs)
        completed_segments.append(True)

    def delayed_final_readback():
        original_readback()
        if len(completed_segments) == len(plan.segments):
            clock[0] = (
                plan.budget_before.deadline_monotonic_s
                - plan.reserved_motion_time_s
                + 1.0
            )

    mocker.patch.object(stage_module.time, "monotonic", side_effect=lambda: clock[0])
    mocker.patch.object(stage, "move_absolute_in_segments", side_effect=confirmed_move)
    mocker.patch.object(
        stage, "_hardware_update_position", side_effect=delayed_final_readback
    )
    with pytest.raises(StageSafetyError, match="field time budget"):
        stage.execute_focus_plan(plan, stage_binding)
    assert len(controller.scripts) == len(plan.segments)
    assert stage.controller_state["reference_valid"]
    assert not stage.moving
    assert stage._prepared_approach is None
