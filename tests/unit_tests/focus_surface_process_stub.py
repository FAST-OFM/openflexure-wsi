"""Minimal external-service fixture for GPL focus workflow tests.

This is intentionally not a production surface fit. It returns either a bounded
nearest measured point or a typed refusal so workflow tests can exercise motion,
journaling and fallback without vendoring Fast OFM Core mathematics.
"""

from __future__ import annotations

import math

from openflexure_microscope_server.focus.focus_surface import (
    FocusObservation,
    FocusPrediction,
    FocusPredictionDiagnostics,
    FocusPredictionRequest,
    check_focus_binding,
)


def external_focus_prediction(
    observations: tuple[FocusObservation, ...], request: FocusPredictionRequest
) -> FocusPrediction:
    """Return a deterministic process double with the production response model."""
    common = {
        "scan_id": request.expected_scan_id,
        "field_id": request.field_id,
        "attempt_id": request.attempt_id,
        "prediction_id": request.prediction_id,
        "target_x_um": request.target_x_um,
        "target_y_um": request.target_y_um,
        "predicted_monotonic_s": request.predicted_monotonic_s,
    }
    if not request.settings.enabled:
        return FocusPrediction(
            **common,
            status="disabled",
            reason="Fixture received a disabled surface",
        )
    binding = request.expected_binding
    if binding is None or not check_focus_binding(binding, binding).usable:
        return FocusPrediction(
            **common,
            status="fault",
            reason="Fixture received an unusable binding",
            fault_code="binding_unusable",
        )
    usable = [
        value
        for value in observations
        if value.scan_id == request.expected_scan_id
        and value.status == "confirmed"
        and value.source == "led_autofocus"
        and check_focus_binding(binding, value.binding).usable
    ]
    if not usable:
        return FocusPrediction(
            **common,
            status="unavailable",
            reason="Fixture has no measured support",
            unavailable_reason="no_observations",
            binding=binding,
            diagnostics=FocusPredictionDiagnostics(candidate_observation_count=0),
        )
    nearest = min(
        usable,
        key=lambda value: math.hypot(
            value.x_um - request.target_x_um,
            value.y_um - request.target_y_um,
        ),
    )
    distance = math.hypot(
        nearest.x_um - request.target_x_um,
        nearest.y_um - request.target_y_um,
    )
    radius = request.settings.neighbor_radius_um
    maximum_delta = request.settings.maximum_prediction_delta_um
    if radius is None or distance > radius:
        reason = "outside_support"
    elif (
        maximum_delta is None
        or abs(nearest.z_um - request.current_z_um) > maximum_delta
    ):
        reason = "prediction_delta_exceeded"
    else:
        age = max(0.0, request.predicted_monotonic_s - nearest.recorded_monotonic_s)
        return FocusPrediction(
            **common,
            target_z_um=nearest.z_um,
            status="usable",
            source="nearest_neighbor",
            reason="Fixture returned nearest measured support",
            binding=binding,
            support_observation_ids=(nearest.observation_id,),
            support_field_ids=(nearest.field_id,),
            support_distances_um=(distance,),
            diagnostics=FocusPredictionDiagnostics(
                candidate_observation_count=len(usable),
                local_observation_count=1,
                selected_support_count=1,
                nearest_distance_um=distance,
                support_age_s=age,
                prediction_delta_um=nearest.z_um - request.current_z_um,
            ),
        )
    return FocusPrediction(
        **common,
        status="unavailable",
        reason=f"Fixture refused {reason}",
        unavailable_reason=reason,
        binding=binding,
        diagnostics=FocusPredictionDiagnostics(
            candidate_observation_count=len(usable),
            nearest_distance_um=distance,
        ),
    )
