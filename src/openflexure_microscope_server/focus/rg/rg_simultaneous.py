"""Serializable contracts for simultaneous RED+GREEN RAW autofocus.

The GPL server owns configuration, hardware sequencing, persistence, and safe
fallback. Spectral unmixing, registration, fitting, and focus evaluation run in
the separately licensed ``fast-ofm-core`` process.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from ...fast_ofm_contracts import StrictModel
from .rg_focus_core import PatchStatus, RGFocusCoreSettings


class SimultaneousCalibrationSettings(StrictModel):
    """Bounded calibration settings in native RAW-plane pixels."""

    plane_roi: tuple[int, int, int, int] = (314, 60, 1400, 1400)
    frame_timeout_s: float = Field(default=5, gt=0, le=10)
    timeout_s: float = Field(default=300, gt=0, le=600)
    dark_frames: int = Field(default=2, ge=2, le=8)
    fit_cycles: int = Field(default=3, ge=2, le=8)
    validation_cycles: int = Field(default=2, ge=2, le=6)
    smoothing_sigma_px: float = Field(default=4.0, ge=1, le=24)
    minimum_source_norm_dn: float = Field(default=24, ge=4, le=512)
    maximum_saturation_fraction: float = Field(default=0.001, ge=0, le=0.02)
    maximum_invalid_fraction: float = Field(default=0.08, ge=0, le=0.2)
    maximum_condition_number: float = Field(default=5.0, gt=1, le=20)
    maximum_holdout_cv: float = Field(default=0.10, gt=0, le=0.3)
    maximum_cross_leakage_fraction: float = Field(default=0.12, ge=0, le=0.5)
    maximum_mixed_residual_fraction: float = Field(default=0.15, gt=0, le=0.5)
    minimum_mixed_scale: float = Field(default=0.70, gt=0, le=1)
    maximum_mixed_scale: float = Field(default=1.20, ge=1, le=2)
    optics_id: str = Field(default="current", min_length=1, max_length=100)
    brightness_id: str = Field(
        default="arduino-v4-r150-g150-w255-live-unsaved",
        min_length=1,
        max_length=100,
    )

    @field_validator("plane_roi", mode="before")
    @classmethod
    def json_roi(cls, value: object) -> object:
        """Accept the JSON array container while retaining strict integer values."""
        return tuple(value) if isinstance(value, list) else value

    def checked_roi(
        self, plane_size: list[int] | tuple[int, int]
    ) -> tuple[int, int, int, int]:
        """Require the declared processing window to fit captured RAW planes."""
        if len(plane_size) != 2:
            raise ValueError("RAW plane size is missing")
        width, height = (int(value) for value in plane_size)
        x, y, roi_width, roi_height = self.plane_roi
        if min(x, y) < 0 or min(roi_width, roi_height) < 16:
            raise ValueError("RAW processing ROI is invalid")
        if x + roi_width > width or y + roi_height > height:
            raise ValueError("RAW processing ROI exceeds the captured planes")
        return self.plane_roi


def _default_focus_core() -> RGFocusCoreSettings:
    """Return the bounded registration policy sent to the core process."""
    return RGFocusCoreSettings(
        registration_highpass_sigma_px=8,
        texture_background_sigma_px=12,
        texture_score_sigma_px=4,
        texture_morphology_kernel_px=5,
        patch_size_px=128,
        patch_stride_px=64,
        minimum_tissue_coverage=0.25,
        minimum_patch_signal=2,
        minimum_patch_std=3,
        minimum_patch_response=0.03,
        minimum_patch_peak_margin=0.02,
        minimum_patch_spectral_correlation=0.03,
        maximum_absolute_shift_px=22,
        maximum_absolute_orthogonal_shift_px=3,
        maximum_patch_count=8,
        minimum_patch_count=6,
    )


class SimultaneousFocusSettings(StrictModel):
    """Config-owned one-frame focus curve, movement, and registration policy."""

    calibration_positions_um: tuple[float, ...] = Field(
        default=(-32, -24, -16, -8, 0, 8, 16, 24, 32), strict=False
    )
    holdout_positions_um: tuple[float, ...] = Field(
        default=(-24, -8, 8, 24), strict=False
    )
    preload_um: float = Field(default=12, gt=0, le=50)
    approach_sign: Literal[-1, 1] = 1
    calibration_timeout_s: float = Field(default=600, gt=0, le=1800)
    autofocus_timeout_s: float = Field(default=20, gt=0, le=120)
    minimum_capture_budget_s: float = Field(default=3, gt=0, le=30)
    maximum_holdout_error_um: float = Field(default=4, gt=0, le=10)
    minimum_slope_norm_px_per_um: float = Field(default=0.05, gt=0)
    focus_tolerance_um: float = Field(default=2, gt=0, le=10)
    maximum_correction_um: float = Field(default=32, gt=0, le=50)
    focus_plane_roi: tuple[int, int, int, int] = (350, 350, 700, 700)
    processing_downsample: Literal[1, 2, 4] = 2
    source_blank_level_dn: float = Field(default=200, gt=32, le=240)
    maximum_component_level: float = Field(default=1.2, gt=1, le=2)
    maximum_frame_saturation_fraction: float = Field(default=0.001, ge=0, le=0.02)
    maximum_frame_residual_fraction: float = Field(default=0.20, gt=0, le=0.5)
    outlier_mad_multiplier: float = Field(default=3.5, ge=1, le=10)
    minimum_outlier_threshold_px: float = Field(default=1, gt=0, le=5)
    maximum_shift_mad_px: float = Field(default=2.5, gt=0, le=10)
    minimum_confidence: float = Field(default=0.03, ge=0, le=1)
    maximum_fit_rmse_um: float = Field(default=4, gt=0, le=10)
    maximum_calibration_cross_track_px: float = Field(default=3, gt=0, le=12)
    core: RGFocusCoreSettings = Field(default_factory=_default_focus_core)

    @field_validator("focus_plane_roi", mode="before")
    @classmethod
    def json_focus_roi(cls, value: object) -> object:
        """Accept a JSON array while retaining strict native-plane integers."""
        return tuple(value) if isinstance(value, list) else value

    def checked_focus_roi(
        self, calibrated_plane_size: tuple[int, int] | list[int]
    ) -> tuple[int, int, int, int]:
        """Require a bounded crop with enough post-downsample windows."""
        if len(calibrated_plane_size) != 2:
            raise ValueError("Calibrated simultaneous R/G plane size is missing")
        plane_width, plane_height = (int(value) for value in calibrated_plane_size)
        x, y, width, height = self.focus_plane_roi
        if min(x, y) < 0 or min(width, height) < 16:
            raise ValueError("Simultaneous focus ROI is invalid")
        if x + width > plane_width or y + height > plane_height:
            raise ValueError("Simultaneous focus ROI exceeds the calibrated RAW maps")
        working_height = height // self.processing_downsample
        working_width = width // self.processing_downsample
        size = self.core.patch_size_px
        stride = self.core.patch_stride_px
        if min(working_height, working_width) < size:
            raise ValueError("Simultaneous focus ROI is too small after downsampling")
        available = (
            len(range(0, working_height - size + 1, stride))
            * len(range(0, working_width - size + 1, stride))
        )
        if available < self.core.minimum_patch_count:
            raise ValueError(
                "Simultaneous focus ROI cannot provide the minimum patch count"
            )
        return self.focus_plane_roi

    @model_validator(mode="after")
    def valid_grid(self) -> Self:
        """Require an ordered symmetric fit/holdout grid with a central fit point."""
        grid = self.calibration_positions_um
        holdouts = set(self.holdout_positions_um)
        if (
            len(grid) < 7
            or tuple(sorted(grid)) != grid
            or len(set(grid)) != len(grid)
            or 0 not in grid
            or not holdouts
            or not holdouts.issubset(grid)
            or 0 in holdouts
            or not any(value < 0 for value in holdouts)
            or not any(value > 0 for value in holdouts)
            or set(grid) != {-value for value in grid}
            or holdouts != {-value for value in holdouts}
        ):
            raise ValueError(
                "Focus calibration needs an ordered two-sided fit/holdout grid"
            )
        fit = [value for value in grid if value not in holdouts]
        if not any(value < 0 for value in fit) or not any(value > 0 for value in fit):
            raise ValueError("Focus fit points must span both sides of zero")
        proven_correction = min(abs(grid[0]), grid[-1])
        if self.maximum_correction_um > proven_correction:
            raise ValueError(
                "Maximum correction exceeds the two-sided calibration range"
            )
        if self.focus_tolerance_um > self.maximum_correction_um:
            raise ValueError("Focus tolerance exceeds the maximum correction")
        return self


class SimultaneousShiftMeasurement(StrictModel):
    """One tissue-aware shift decision from a spectrally unmixed RAW frame."""

    status: Literal["ready", "refused"]
    reason: str
    dx: float | None = None
    dy: float | None = None
    confidence: float = Field(ge=0, le=1)
    candidate_patch_count: int = Field(ge=0)
    accepted_patch_count: int = Field(ge=0)
    inlier_patch_count: int = Field(ge=0)
    tissue_coverage: float = Field(ge=0, le=1)
    dx_mad: float = Field(ge=0)
    dy_mad: float = Field(ge=0)
    median_response: float = Field(ge=0)
    patch_status_counts: dict[PatchStatus, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ready_has_shift(self) -> Self:
        """Require coordinates only for measurements accepted by all QC gates."""
        if self.status == "ready" and (self.dx is None or self.dy is None):
            raise ValueError("A ready simultaneous R/G measurement needs dx and dy")
        return self


class SimultaneousFocusObservation(StrictModel):
    """One fixed-grid calibration point measured from one mixed RAW frame."""

    capture_id: str = Field(min_length=1)
    z_um: float
    role: Literal["fit", "holdout"]
    measurement: SimultaneousShiftMeasurement


class SimultaneousFocusCurve(StrictModel):
    """Validated linear two-dimensional shift-to-defocus model."""

    offset_px: tuple[float, float] = Field(strict=False)
    slope_px_per_um: tuple[float, float] = Field(strict=False)
    slope_norm_px_per_um: float = Field(gt=0)
    applicable_z_range_um: tuple[float, float] = Field(strict=False)
    fit_rmse_um: float = Field(ge=0)
    holdout_errors_um: dict[str, float]
    holdout_max_absolute_error_um: float = Field(ge=0)
    cross_track_limit_px: float = Field(gt=0)


class SimultaneousFocusSearchSettings(StrictModel):
    """Diagnostic-only resource limits sent to the core process."""

    maximum_patch_count: int = Field(default=24, ge=8, le=48)
    timeout_s: float = Field(default=8, gt=0, le=20)
    maximum_plane_pixels: int = Field(default=2_000_000, ge=256, le=2_000_000)
    minimum_central_anchor_count: int = Field(default=2, ge=2, le=8)
