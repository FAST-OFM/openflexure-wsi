"""Strict field-bound quality decision for tissue-aware R/G registration.

This OF-024 layer consumes corrected common R/G planes and a fresh OF-032
``TissueField``.  It owns operational rejection and robust aggregation only; it
does not capture frames, switch lights, move Z, fit a focus model, or fall back
to a whole-frame or sharpness-difference measurement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Self

import numpy as np
from pydantic import Field, model_validator

from ...fast_ofm_contracts import CalibrationStage, StrictModel
from .rg_focus_core import (
    PatchEvaluation,
)
from .rg_focus_field import (
    FrameReference,
    TissueField,
)

MeasurementStatus = Literal[
    "ready",
    "no_tissue",
    "insufficient_tissue",
    "low_signal",
    "saturated",
    "spectral_mismatch",
    "correlation_failed",
    "inconsistent_shifts",
]


class RGFocusEstimatorSettings(StrictModel):
    """Predeclared operational tolerances for robust shift aggregation."""

    inlier_aggregation: Literal["arithmetic_mean"] = "arithmetic_mean"
    minimum_inlier_fraction: float = Field(default=0.60, gt=0, le=1)
    outlier_mad_multiplier: float = Field(default=3.5, ge=1, le=10)
    minimum_outlier_threshold_px: float = Field(default=0.75, gt=0)
    maximum_dx_mad_px: float = Field(default=2.0, gt=0)
    maximum_dy_mad_px: float = Field(default=2.0, gt=0)
    maximum_radial_p90_px: float = Field(default=3.0, gt=0)
    minimum_confidence: float = Field(default=0.15, ge=0, le=1)

    @model_validator(mode="after")
    def consistent_spread_limits(self) -> Self:
        """Keep the joint residual limit at least as large as each MAD gate."""
        if self.maximum_radial_p90_px < max(
            self.maximum_dx_mad_px, self.maximum_dy_mad_px
        ):
            raise ValueError("Radial residual limit is smaller than an axis MAD limit")
        return self


class RGFocusMeasurement(StrictModel):
    """Serializable measurement or refusal with complete fixed-window evidence."""

    status: MeasurementStatus
    reason: str
    dx: float | None = None
    dy: float | None = None
    confidence: float = Field(ge=0, le=1)
    median_response: float = Field(ge=0)
    median_spectral_correlation: float = Field(ge=-1, le=1)
    dx_mad: float = Field(ge=0)
    dy_mad: float = Field(ge=0)
    radial_p90: float = Field(ge=0)
    candidate_patch_count: int = Field(ge=0)
    accepted_patch_count: int = Field(ge=0)
    inlier_patch_count: int = Field(ge=0)
    inlier_fraction: float = Field(ge=0, le=1)
    support_fraction: float = Field(ge=0, le=1)
    rejection_counts: dict[str, int]
    windows: tuple[PatchEvaluation, ...] = Field(strict=False)


@dataclass(frozen=True)
class RGFocusInputs:
    """One already captured, corrected pair and its immutable field provenance."""

    red_plane: np.ndarray
    green_plane: np.ndarray
    red_source_jpeg8: np.ndarray
    green_source_jpeg8: np.ndarray
    red_valid: np.ndarray
    green_valid: np.ndarray
    field: TissueField | None
    red_reference: FrameReference
    green_reference: FrameReference
    geometry_value: Mapping[str, object]


def rg_shift_manifest_stage() -> CalibrationStage:
    """Describe the real OF-024 computation for the LED calibration manifest."""
    return CalibrationStage(
        id="rg_shift",
        name="Tissue-aware R/G shift",
        description="Measure mutual-information displacement only on fixed fresh-WHITE tissue windows.",
        inputs=[
            "corrected_red_plane",
            "corrected_green_plane",
            "valid_masks",
            "tissue_field",
            "frame_references",
            "parameters",
        ],
        outputs=["shift", "confidence", "spread", "window_diagnostics"],
        action="estimate_rg_shift",
        success_criterion="Ready shift with sufficient mutually consistent tissue windows",
        timeout_setting="measurement_timeout_s",
        cancellation="Discard the pair; never substitute whole-frame or sharpness-difference focus.",
        hardware_required=False,
    )
