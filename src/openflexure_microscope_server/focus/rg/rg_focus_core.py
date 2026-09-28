"""Serializable R/G focus contracts retained by the GPL integration layer.

All image processing and numerical registration run in the separately licensed
``fast-ofm-core`` process. This module intentionally contains no numerical
implementation and may be imported without loading OpenCV or NumPy.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator

from ...fast_ofm_contracts import StrictModel


class RGFocusCoreSettings(StrictModel):
    """Explicit tissue, registration, and consistency thresholds."""

    registration_highpass_sigma_px: float = Field(
        default=8.0,
        gt=0,
        validation_alias=AliasChoices(
            "registration_highpass_sigma_px", "phase_highpass_sigma_px"
        ),
    )
    mutual_information_bins: int = Field(default=32, ge=8, le=256)
    texture_background_sigma_px: float = Field(default=12.0, gt=0)
    texture_score_sigma_px: float = Field(default=4.0, gt=0)
    texture_gradient_weight: float = Field(default=0.25, ge=0)
    texture_percentile: float = Field(default=55.0, ge=0, le=100)
    texture_morphology_kernel_px: int = Field(default=7, ge=1, le=63)
    patch_size_px: int = Field(default=128, ge=8)
    patch_stride_px: int = Field(default=64, ge=1)
    minimum_tissue_coverage: float = Field(default=0.35, ge=0, le=1)
    minimum_patch_signal: float = Field(default=16.0, ge=0)
    jpeg8_saturation_level: float = Field(default=250.0, gt=0, le=255)
    maximum_patch_saturation_fraction: float = Field(default=0.01, ge=0, le=1)
    minimum_patch_std: float = Field(default=4.0, ge=0)
    minimum_patch_response: float = Field(default=0.05, ge=0, le=1)
    minimum_patch_peak_margin: float = Field(default=0.03, ge=0, le=1)
    minimum_patch_spectral_correlation: float = Field(default=0.05, ge=-1, le=1)
    maximum_absolute_shift_px: float = Field(default=40.0, gt=0)
    maximum_absolute_orthogonal_shift_px: float = Field(default=4.0, gt=0)
    masked_inpaint_radius_px: float = Field(default=3.0, gt=0, le=32)
    minimum_shifted_support_fraction: float = Field(default=0.50, gt=0, le=1)
    maximum_patch_count: int = Field(default=80, ge=1)
    minimum_patch_count: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def consistent_settings(self) -> Self:
        """Reject parameter combinations that cannot produce a valid grid."""
        if self.patch_stride_px > self.patch_size_px:
            raise ValueError("Patch stride cannot exceed patch size")
        if self.minimum_patch_count > self.maximum_patch_count:
            raise ValueError("Minimum patch count exceeds maximum patch count")
        if self.texture_morphology_kernel_px % 2 == 0:
            raise ValueError("Texture morphology kernel must be odd")
        if self.maximum_absolute_orthogonal_shift_px > self.maximum_absolute_shift_px:
            raise ValueError("Orthogonal shift range exceeds primary shift range")
        return self


class TextureMaskMetrics(StrictModel):
    """Diagnostics from the candidate texture-mask calculation."""

    threshold: float
    coverage_total: float = Field(ge=0, le=1)
    coverage_inside_valid: float = Field(ge=0, le=1)


class PatchBox(StrictModel):
    """One fixed rectangle in the common R/G plane."""

    patch_id: str = Field(min_length=1)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class PatchMeasurement(StrictModel):
    """Accepted mutual-information measurement for one common patch."""

    patch_id: str
    x: int
    y: int
    w: int
    h: int
    dx: float
    dy: float
    response: float
    peak_margin: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    median_r: float = Field(ge=0)
    median_g: float = Field(ge=0)
    std_r: float = Field(ge=0)
    std_g: float = Field(ge=0)
    saturation_r: float = Field(ge=0, le=1)
    saturation_g: float = Field(ge=0, le=1)
    spectral_correlation: float = Field(ge=-1, le=1)


PatchStatus = Literal[
    "accepted",
    "insufficient_support",
    "low_signal",
    "saturated",
    "low_texture",
    "correlation_failed",
    "shift_out_of_range",
    "spectral_mismatch",
]


class PatchEvaluation(StrictModel):
    """Accepted or rejected result for every fixed common-plane window."""

    patch_id: str
    x: int
    y: int
    w: int
    h: int
    status: PatchStatus
    reason: str
    coverage: float = Field(ge=0, le=1)
    median_r: float | None = Field(default=None, ge=0)
    median_g: float | None = Field(default=None, ge=0)
    std_r: float | None = Field(default=None, ge=0)
    std_g: float | None = Field(default=None, ge=0)
    saturation_r: float | None = Field(default=None, ge=0, le=1)
    saturation_g: float | None = Field(default=None, ge=0, le=1)
    dx: float | None = None
    dy: float | None = None
    response: float | None = None
    peak_margin: float | None = Field(default=None, ge=0, le=1)
    spectral_correlation: float | None = Field(default=None, ge=-1, le=1)


class ShiftSummary(StrictModel):
    """Robust descriptive statistics returned by the external core."""

    dx: float
    dy: float
    response: float
    patch_count: int = Field(ge=1)
    dx_mad: float = Field(ge=0)
    dy_mad: float = Field(ge=0)
