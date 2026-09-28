"""GPL-side value translation for Fast OFM Core focus predictions."""

from __future__ import annotations

import math
from collections.abc import Mapping

from openflexure_microscope_server.focus.sparse_focus import (
    FocusAnchor,
    FocusEstimate,
    SparseFocusSettings,
)
from openflexure_microscope_server.integrations.fast_ofm_core.client import (
    FastOFMCoreBlockingProcess,
    FastOFMCoreError,
)


def _optional_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FastOFMCoreError("CORE_PROTOCOL_ERROR", f"{name} is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise FastOFMCoreError("CORE_PROTOCOL_ERROR", f"{name} is not finite")
    return result


def predict_sparse_focus(
    process: FastOFMCoreBlockingProcess,
    anchors: tuple[FocusAnchor, ...],
    *,
    target_x_um: float,
    target_y_um: float,
    current_z_um: float,
    settings: SparseFocusSettings,
) -> FocusEstimate:
    """Request one prediction and validate every field used for stage motion."""
    response = process.request(
        "focus.surface.predict",
        {
            "anchors": [
                {
                    "anchor_id": f"rg-{index}",
                    "position_um": {
                        "x_um": anchor.x_um,
                        "y_um": anchor.y_um,
                        "z_um": anchor.z_um,
                    },
                    "source": "rg",
                    "confidence": 1.0,
                }
                for index, anchor in enumerate(anchors)
            ],
            "target_um": {
                "x_um": target_x_um,
                "y_um": target_y_um,
                "z_um": current_z_um,
            },
            "limits": {
                "minimum_plane_points": settings.minimum_plane_points,
                "maximum_neighbors": settings.maximum_neighbors,
                "maximum_support_distance_um": settings.neighbor_radius_um,
                "maximum_extrapolation_um": settings.maximum_extrapolation_um,
                "maximum_prediction_delta_um": settings.maximum_prediction_delta_um,
                "maximum_residual_um": settings.maximum_fit_residual_um,
                "maximum_abs_slope": settings.maximum_plane_slope_um_per_um,
                "maximum_condition_number": settings.maximum_condition_number,
            },
        },
        timeout_s=5.0,
    )
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise FastOFMCoreError("CORE_PROTOCOL_ERROR", "Focus result is not an object")
    status = result.get("status")
    if status not in {"predicted", "refused"}:
        raise FastOFMCoreError("CORE_PROTOCOL_ERROR", "Unknown focus result status")
    source = result.get("model", "none")
    reason = result.get("reason")
    support_count = result.get("support_count")
    if not isinstance(source, str) or not isinstance(reason, str):
        raise FastOFMCoreError("CORE_PROTOCOL_ERROR", "Invalid focus result text")
    if isinstance(support_count, bool) or not isinstance(support_count, int):
        raise FastOFMCoreError("CORE_PROTOCOL_ERROR", "Invalid focus support count")
    target = _optional_number(result.get("predicted_z_um"), "predicted_z_um")
    usable = status == "predicted"
    if usable != (target is not None):
        raise FastOFMCoreError(
            "CORE_PROTOCOL_ERROR", "Focus status and target disagree"
        )
    return FocusEstimate(
        usable=usable,
        target_z_um=target,
        source=source,
        reason=reason,
        support_count=support_count,
        fit_residual_um=_optional_number(
            result.get("fit_residual_um"), "fit_residual_um"
        ),
        slope_um_per_um=_optional_number(
            result.get("slope_um_per_um"), "slope_um_per_um"
        ),
        extrapolation_um=_optional_number(
            result.get("extrapolation_um"), "extrapolation_um"
        ),
        validation_error_um=_optional_number(
            result.get("validation_error_um"), "validation_error_um"
        ),
    )
