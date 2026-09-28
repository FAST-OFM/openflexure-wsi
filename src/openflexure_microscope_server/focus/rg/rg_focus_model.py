"""Fit and apply the bounded signed 2D R/G shift-to-Z calibration model."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator

from ...fast_ofm_contracts import StrictModel
from .rg_focus_core import RGFocusCoreSettings
from .rg_focus_estimator import MeasurementStatus, RGFocusEstimatorSettings
from .rg_focus_field import TissueFieldSettings

CalibrationRole = Literal["fit", "holdout", "approach_above", "approach_below"]
ApproachRole = Literal["approach_above", "approach_below"]

JPEG_DRAFT_POLICY_SHA256 = (
    "82ceeb734f77c07260dcf72fccb5ed03eee4a876721386d9f452298c65a415fb"
)
JPEG_DRAFT_POLICY_ID = (
    "ofm-rg-focus-jpeg8-imx477-4x-roi810x760-draft-v1+sha256-"
    f"{JPEG_DRAFT_POLICY_SHA256}"
)


class RGFocusCalibrationPoint(StrictModel):
    """One preassigned calibration observation, including rejected measurements."""

    capture_id: str = Field(min_length=1)
    role: CalibrationRole
    z_um: float
    measurement_status: MeasurementStatus
    dx: float | None = None
    dy: float | None = None
    confidence: float = Field(ge=0, le=1)
    white_score: float = Field(gt=0)

    @model_validator(mode="after")
    def ready_has_shift(self) -> Self:
        """Require a complete finite shift vector for every ready observation."""
        if self.measurement_status == "ready" and (self.dx is None or self.dy is None):
            raise ValueError("A ready calibration point is missing its R/G shift")
        return self


class RGFocusModelSettings(StrictModel):
    """Predeclared validation and approach-repeatability requirements."""

    minimum_valid_fit_points: int = Field(default=5, ge=3)
    minimum_valid_holdout_points: int = Field(default=2, ge=2)
    maximum_holdout_error_um: float = Field(default=4.0, gt=0)
    minimum_slope_norm_px_per_um: float = Field(default=0.05, gt=0)
    maximum_approach_shift_difference_px: float = Field(default=1.0, gt=0)
    maximum_return_error_um: float = Field(default=2.0, gt=0)
    maximum_return_prediction_difference_um: float = Field(default=2.0, gt=0)
    maximum_stationary_prediction_difference_um: float = Field(default=2.0, gt=0)
    minimum_white_score_fraction: float = Field(default=0.85, gt=0, le=1)
    maximum_reference_focus_offset_um: float = Field(default=12.0, gt=0)
    maximum_approach_white_score_fraction: float = Field(default=0.15, gt=0, le=1)


class RGFocusMeasurementPolicy(StrictModel):
    """Freeze every tissue and shift QC parameter used with a model profile."""

    measurement_domain: Literal["legacy-raw", "processed-jpeg-rgb8"] = "legacy-raw"
    legacy_raw_saturation_level: float | None = Field(default=None, gt=0)
    core: RGFocusCoreSettings = Field(default_factory=RGFocusCoreSettings)
    estimator: RGFocusEstimatorSettings = Field(
        default_factory=RGFocusEstimatorSettings
    )
    tissue_field: TissueFieldSettings = Field(default_factory=TissueFieldSettings)

    @model_validator(mode="before")
    @classmethod
    def preserve_compatible_schema(cls, value: object) -> object:
        """Load historical evidence while removing only retired derived fields."""
        if not isinstance(value, dict):
            return value
        result = copy.deepcopy(value)
        core = result.get("core")
        if isinstance(core, dict):
            core.pop("minimum_measurement_response", None)
        if isinstance(core, dict) and "saturation_level" in core:
            if result.get("measurement_domain", "legacy-raw") != "legacy-raw":
                raise ValueError("RAW saturation cannot be used in a JPEG policy")
            result["measurement_domain"] = "legacy-raw"
            result["legacy_raw_saturation_level"] = core.pop("saturation_level")
        return result

    def require_jpeg(self) -> None:
        """Refuse historical policies instead of guessing JPEG8 DN/pixel settings."""
        if (
            self.measurement_domain != "processed-jpeg-rgb8"
            or self.legacy_raw_saturation_level is not None
        ):
            raise ValueError("Save an explicit processed-JPEG RGB8 focus policy")


def canonical_measurement_policy_bytes(policy: RGFocusMeasurementPolicy) -> bytes:
    """Return the policy-only canonical bytes used by the reviewed draft artifact."""
    payload = json.dumps(
        policy.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{payload}\n".encode()


def measurement_policy_sha256(policy: RGFocusMeasurementPolicy) -> str:
    """Identify a complete normalized measurement policy, including its final LF."""
    return hashlib.sha256(canonical_measurement_policy_bytes(policy)).hexdigest()


def jpeg_measurement_draft_policy() -> RGFocusMeasurementPolicy:
    """Build the complete source-owned, uncommissioned JPEG draft policy."""
    policy = RGFocusMeasurementPolicy.model_validate(
        {
            "measurement_domain": "processed-jpeg-rgb8",
            "legacy_raw_saturation_level": None,
            "core": {
                "registration_highpass_sigma_px": 4.0,
                "mutual_information_bins": 32,
                "texture_background_sigma_px": 6.0,
                "texture_score_sigma_px": 2.0,
                "texture_gradient_weight": 0.25,
                "texture_percentile": 55.0,
                "texture_morphology_kernel_px": 3,
                "patch_size_px": 128,
                "patch_stride_px": 64,
                "minimum_tissue_coverage": 0.35,
                "minimum_patch_signal": 8.0,
                "jpeg8_saturation_level": 250.0,
                "maximum_patch_saturation_fraction": 0.01,
                "minimum_patch_std": 4.0,
                "minimum_patch_response": 0.05,
                "minimum_patch_peak_margin": 0.03,
                "minimum_patch_spectral_correlation": 0.05,
                "maximum_absolute_shift_px": 24.0,
                "maximum_absolute_orthogonal_shift_px": 4.0,
                "masked_inpaint_radius_px": 1.5,
                "minimum_shifted_support_fraction": 0.5,
                "maximum_patch_count": 24,
                "minimum_patch_count": 9,
            },
            "estimator": {
                "inlier_aggregation": "arithmetic_mean",
                "minimum_inlier_fraction": 0.6,
                "outlier_mad_multiplier": 2.5,
                "minimum_outlier_threshold_px": 1.0,
                "maximum_dx_mad_px": 1.0,
                "maximum_dy_mad_px": 1.0,
                "maximum_radial_p90_px": 3.25,
                "minimum_confidence": 0.05,
            },
            "tissue_field": {
                "edge_margin_px": 128,
                "minimum_white_median": 16.0,
                "white_saturation_level": 250.0,
                "maximum_white_saturation_fraction": 0.02,
                "coherent_texture_blur_sigma_px": 0.75,
                "coherent_texture_background_sigma_px": 4.0,
                "minimum_coherent_texture_fraction": 0.01,
                "minimum_component_pixels": 512,
                "minimum_tissue_fraction": 0.03,
            },
        }
    )
    if measurement_policy_sha256(policy) != JPEG_DRAFT_POLICY_SHA256:
        raise RuntimeError(
            "Source JPEG draft policy differs from its immutable identity"
        )
    return policy


class RGFocusStationaryObservation(StrictModel):
    """One no-motion commissioning result bound to its frozen field identity."""

    capture_id: str = Field(min_length=1)
    measurement_status: MeasurementStatus
    dx: float | None = None
    dy: float | None = None
    white_score: float = Field(gt=0)
    accepted_patch_count: int = Field(ge=0)
    inlier_fraction: float = Field(ge=0, le=1)
    field_id: str = Field(min_length=1)
    geometry_id: str = Field(min_length=1)
    binding_sha256: str = Field(min_length=64, max_length=64)
    mask_sha256: str = Field(min_length=64, max_length=64)
    window_ids: tuple[str, ...] = Field(strict=False)

    @model_validator(mode="after")
    def ready_has_shift(self) -> Self:
        """Require a finite 2D shift for every ready stationary result."""
        if self.measurement_status == "ready" and (
            self.dx is None
            or self.dy is None
            or not math.isfinite(self.dx)
            or not math.isfinite(self.dy)
        ):
            raise ValueError("A ready stationary observation is missing its R/G shift")
        return self


class RGFocusApproachCheck(StrictModel):
    """Projected-Z and WHITE evidence from both final-approach returns."""

    status: Literal["passed", "failed"]
    reason: str
    projected_z_um: dict[ApproachRole, float]
    absolute_target_errors_um: dict[ApproachRole, float]
    mutual_prediction_difference_um: float | None = Field(default=None, ge=0)
    white_scores: dict[ApproachRole, float]
    white_to_reference_fractions: dict[ApproachRole, float]
    white_mutual_fraction: float | None = Field(default=None, ge=0)
    target_z_um: float = 0.0
    maximum_return_error_um: float = Field(gt=0)
    maximum_mutual_prediction_difference_um: float = Field(gt=0)
    minimum_white_score_fraction: float = Field(gt=0, le=1)
    maximum_white_mutual_fraction: float = Field(gt=0, le=1)


class RGFocusActivationEvidence(StrictModel):
    """Complete prospective gate ledger required by every active JPEG model."""

    contract_id: Literal["jpeg-demo-calibration-v1"] = "jpeg-demo-calibration-v1"
    status: Literal["passed", "failed"]
    failures: tuple[str, ...] = Field(strict=False)
    exact_grid_and_returns: bool
    all_grid_and_returns_ready: bool
    design_rank: int = Field(ge=0)
    slope_finite_nonzero: bool
    applicable_range_covers_contract: bool = Field(
        validation_alias=AliasChoices(
            "applicable_range_covers_contract",
            "applicable_range_covers_minus6_plus6",
        )
    )
    reference_white_score: float = Field(gt=0)
    maximum_grid_white_score: float = Field(gt=0)
    maximum_grid_white_z_um: float
    grid_white_maximum_z_positions_um: tuple[float, ...] = Field(strict=False)
    maximum_reference_focus_offset_um: float = Field(gt=0)
    reference_white_fraction: float = Field(ge=0)
    grid_white_maximum_internal: bool
    grid_white_maximum_has_measured_neighbours: bool
    approach: RGFocusApproachCheck
    stationary_observations: tuple[
        RGFocusStationaryObservation, RGFocusStationaryObservation
    ] = Field(strict=False)
    stationary_identity_match: bool
    stationary_projected_z_um: tuple[float, float] | None = Field(
        default=None, strict=False
    )
    stationary_prediction_difference_um: float | None = Field(default=None, ge=0)
    maximum_stationary_prediction_difference_um: float = Field(gt=0)


class RGFocusModelObservation(StrictModel):
    """One accepted source observation for the frozen empirical model envelopes."""

    capture_id: str = Field(min_length=1)
    role: Literal["fit", "holdout", "approach_above", "approach_below", "stationary"]
    z_um: float
    dx: float
    dy: float
    signed_cross_track_residual_px: float


class RGFocusModelProfile(StrictModel):
    """A serialisable signed model with validation scope and activation decision."""

    id: str = Field(min_length=1)
    status: Literal["candidate", "valid"]
    reason: str
    source_series_id: str = Field(min_length=1)
    method: Literal["linear_2d_shift_projection"] = "linear_2d_shift_projection"
    offset_px: tuple[float, float] = Field(strict=False)
    slope_px_per_um: tuple[float, float] = Field(strict=False)
    slope_norm_px_per_um: float = Field(ge=0)
    focus_z_um: float | None
    empirical_observations: tuple[RGFocusModelObservation, ...] = Field(strict=False)
    empirical_cross_track_limit_px: float | None = Field(ge=0)
    empirical_prediction_error_um: float | None = Field(ge=0)
    fit_capture_ids: tuple[str, ...] = Field(strict=False)
    holdout_capture_ids: tuple[str, ...] = Field(strict=False)
    rejected_capture_reasons: dict[str, str]
    fit_z_range_um: tuple[float, float] = Field(strict=False)
    holdout_z_range_um: tuple[float, float] = Field(strict=False)
    applicable_z_range_um: tuple[float, float] = Field(strict=False)
    fit_rmse_um: float | None = Field(default=None, ge=0)
    holdout_rmse_um: float | None = Field(default=None, ge=0)
    holdout_max_absolute_error_um: float | None = Field(default=None, ge=0)
    holdout_absolute_error_p95_um: float | None = Field(default=None, ge=0)
    holdout_predictions_um: dict[str, float]
    holdout_errors_um: dict[str, float]
    approach_check: RGFocusApproachCheck
    activation_evidence: RGFocusActivationEvidence
    compatibility: dict[str, object]
    source_evidence: dict[str, str]
    measurement_policy: RGFocusMeasurementPolicy
    settings: RGFocusModelSettings

    @model_validator(mode="after")
    def ordered_ranges(self) -> Self:
        """Enforce only the process-boundary structure of a persisted profile."""
        for low, high in (
            self.fit_z_range_um,
            self.holdout_z_range_um,
            self.applicable_z_range_um,
        ):
            if low >= high:
                raise ValueError("R/G model ranges must have positive width")
        if self.status == "valid" and (
            self.activation_evidence.status != "passed"
            or self.slope_norm_px_per_um <= 0
            or self.focus_z_um is None
            or self.empirical_cross_track_limit_px is None
            or self.empirical_prediction_error_um is None
        ):
            raise ValueError("Valid profile is missing required activation evidence")
        return self

    @property
    def applicable_error_range_um(self) -> tuple[float, float]:
        """Translate the validated commanded-Z interval around the WHITE target."""
        if self.focus_z_um is None:
            raise ValueError("R/G model has no unambiguous WHITE focus target")
        return (
            self.applicable_z_range_um[0] - self.focus_z_um,
            self.applicable_z_range_um[1] - self.focus_z_um,
        )
