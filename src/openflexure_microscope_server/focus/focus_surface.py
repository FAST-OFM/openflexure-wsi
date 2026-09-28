"""GPL-side focus-surface contracts with no prediction implementation.

The OpenFlexure process validates persisted values and hardware bindings here.
Numerical support selection and Z prediction are performed by the separately
installed Fast OFM Core process through the JSON adapter.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ..fast_ofm_contracts import AxisName, StrictModel
from .rg.rg_focus_calibration import RGFocusApproachSettings
from .rg.rg_focus_estimator import MeasurementStatus

Identifier = Annotated[str, Field(min_length=1, pattern=r"\S")]
AutofocusMethod = Literal["none", "openflexure", "led", "simultaneous_rg"]
FocusStrategy = Literal["single_autofocus", "smart_stack"]
UnpredictedFocusMode = Literal["selected_method", "white_then_led"]
NoTissueMode = Literal["keep_z", "skip_tile", "pause"]
FocusObservationSource = Literal[
    "seed",
    "led_autofocus",
    "white_autofocus",
    "surface_prediction",
    "preload",
    "none",
]
FocusObservationStatus = Literal[
    "confirmed",
    "candidate",
    "intermediate",
    "failed",
    "cancelled",
    "no_tissue",
    "imaged",
]
FocusPredictionStatus = Literal["usable", "unavailable", "disabled", "fault"]
FocusPredictionSource = Literal["seed", "nearest_neighbor", "local_plane"]
FocusUnavailableReason = Literal[
    "no_observations",
    "insufficient_support",
    "stale_support",
    "poor_local_fit",
    "outside_support",
    "support_discontinuity",
    "prediction_delta_exceeded",
]
FocusObservationFaultCode = Literal[
    "foreign_scan",
    "binding_mismatch",
    "binding_unusable",
    "future_timestamp",
    "observation_id_conflict",
    "ambiguous_xy_timestamp",
    "candidate_limit_exceeded",
]


class FocusObservationContractError(ValueError):
    """A non-optical contract fault returned by an external prediction service."""

    def __init__(
        self,
        code: FocusObservationFaultCode,
        observation_id: str | None,
        detail: str,
    ) -> None:
        """Store a machine-readable code and affected observation identity."""
        super().__init__(detail)
        self.code = code
        self.observation_id = observation_id


def _json_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def _strict_sign(value: object) -> object:
    if type(value) is not int or value not in (-1, 1):
        raise ValueError("Axis or approach sign must be integer -1 or +1")
    return value


class FocusAxisScale(StrictModel):
    """One axis conversion frozen at the stage boundary."""

    axis: AxisName
    units_per_mm: float = Field(gt=0)
    direction_sign: Literal[-1, 1]

    @field_validator("direction_sign", mode="before")
    @classmethod
    def direction_is_an_integer_sign(cls, value: object) -> object:
        """Keep bool/float/string values out of a signed axis binding."""
        return _strict_sign(value)


class FocusBinding(StrictModel):
    """Identities and physical parameters that bind predictions to one machine."""

    stage_controller_id: Identifier
    reference_id: Identifier
    reference_status: Literal["valid", "lost"]
    axis_scales: tuple[FocusAxisScale, FocusAxisScale, FocusAxisScale]
    camera_stage_mapping_id: Identifier
    camera_stage_mapping_status: Literal["valid", "candidate"]
    geometry_id: Identifier
    rg_focus_model_id: Identifier
    rg_focus_model_status: Literal["valid", "candidate"]
    red_flat_field_profile_id: Identifier
    red_flat_field_status: Literal["valid", "validation_failed"]
    green_flat_field_profile_id: Identifier
    green_flat_field_status: Literal["valid", "validation_failed"]
    approach_profile_id: Identifier
    approach_status: Literal["valid", "candidate"]
    approach_parameters: RGFocusApproachSettings

    @field_validator("axis_scales", mode="before")
    @classmethod
    def axes_accept_json_array(cls, value: object) -> object:
        """Accept JSON containers while retaining strict tuple values."""
        return _json_tuple(value)

    @field_validator("approach_parameters", mode="before")
    @classmethod
    def approach_sign_is_strict(cls, value: object) -> object:
        """Protect the reused approach model from sign coercion."""
        if isinstance(value, Mapping) and "approach_sign" in value:
            _strict_sign(value["approach_sign"])
        return value

    @model_validator(mode="after")
    def exactly_xyz_scales(self) -> Self:
        """Require one canonical scale for every physical axis."""
        if tuple(scale.axis for scale in self.axis_scales) != ("x", "y", "z"):
            raise ValueError(
                "Axis scales must contain X, Y and Z exactly once in order"
            )
        return self


class FocusBindingCheck(StrictModel):
    """Compatibility/readiness result with explicit mismatched fields."""

    compatible: bool
    usable: bool
    reason: Identifier
    mismatched_fields: tuple[str, ...] = ()

    @field_validator("mismatched_fields", mode="before")
    @classmethod
    def fields_accept_json_array(cls, value: object) -> object:
        """Accept persisted JSON while retaining an immutable tuple."""
        return _json_tuple(value)


_BINDING_COMPATIBILITY_FIELDS = (
    "stage_controller_id",
    "reference_id",
    "axis_scales",
    "camera_stage_mapping_id",
    "geometry_id",
    "rg_focus_model_id",
    "red_flat_field_profile_id",
    "green_flat_field_profile_id",
    "approach_profile_id",
    "approach_parameters",
)


def _binding_readiness_problems(binding: FocusBinding) -> tuple[str, ...]:
    expected = {
        "reference_status": "valid",
        "camera_stage_mapping_status": "valid",
        "rg_focus_model_status": "valid",
        "red_flat_field_status": "valid",
        "green_flat_field_status": "valid",
        "approach_status": "valid",
    }
    return tuple(
        name
        for name, required in expected.items()
        if getattr(binding, name) != required
    )


def check_focus_binding(
    expected: FocusBinding, current: FocusBinding
) -> FocusBindingCheck:
    """Compare hardware identities and require both snapshots to be usable."""
    mismatches = tuple(
        name
        for name in _BINDING_COMPATIBILITY_FIELDS
        if getattr(expected, name) != getattr(current, name)
    )
    expected_problems = tuple(
        f"expected.{name}" for name in _binding_readiness_problems(expected)
    )
    current_problems = tuple(
        f"current.{name}" for name in _binding_readiness_problems(current)
    )
    if mismatches:
        return FocusBindingCheck(
            compatible=False,
            usable=False,
            reason="Focus binding identities or frozen parameters changed",
            mismatched_fields=mismatches + expected_problems + current_problems,
        )
    if expected_problems or current_problems:
        if expected_problems and current_problems:
            reason = (
                "Frozen and current focus bindings are not valid for LED surface use"
            )
        elif expected_problems:
            reason = "Frozen expected focus binding is not valid for LED surface use"
        else:
            reason = "Current focus binding is not valid for LED surface use"
        return FocusBindingCheck(
            compatible=True,
            usable=False,
            reason=reason,
            mismatched_fields=expected_problems + current_problems,
        )
    return FocusBindingCheck(
        compatible=True,
        usable=True,
        reason="Focus binding identities, parameters and statuses are compatible",
    )


class FocusSurfaceSettings(StrictModel):
    """Run-frozen limits sent to the external focus-surface implementation."""

    enabled: bool = False
    minimum_plane_points: int = Field(default=4, ge=4, le=64)
    maximum_neighbors: int = Field(default=12, ge=4, le=64)
    maximum_candidate_observations: int = Field(default=256, ge=4, le=4096)
    neighbor_radius_um: float | None = Field(default=None, gt=0)
    allow_extrapolation: bool = False
    maximum_extrapolation_um: float | None = Field(default=None, ge=0)
    maximum_prediction_delta_um: float | None = Field(default=None, gt=0)
    measurement_offset_um: float | None = None
    maximum_fit_residual_um: float | None = Field(default=None, gt=0)
    minimum_fit_inlier_fraction: float | None = Field(default=None, gt=0, le=1)
    maximum_fit_condition_number: float | None = Field(default=None, gt=1)
    maximum_plane_slope_um_per_um: float | None = Field(default=None, gt=0)
    maximum_observation_age_s: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def complete_operational_gates(self) -> Self:
        """Require every safety gate before enabling external predictions."""
        if self.maximum_neighbors < self.minimum_plane_points:
            raise ValueError("Maximum neighbors cannot be below minimum plane points")
        if self.maximum_candidate_observations < self.maximum_neighbors:
            raise ValueError(
                "Maximum candidate observations cannot be below maximum neighbors"
            )
        required = (
            "neighbor_radius_um",
            "maximum_extrapolation_um",
            "maximum_prediction_delta_um",
            "measurement_offset_um",
            "maximum_fit_residual_um",
            "minimum_fit_inlier_fraction",
            "maximum_fit_condition_number",
            "maximum_plane_slope_um_per_um",
            "maximum_observation_age_s",
        )
        if self.enabled and any(getattr(self, name) is None for name in required):
            raise ValueError("Enabled focus surface requires every operational gate")
        if not self.enabled:
            return self
        if self.maximum_extrapolation_um is None or self.neighbor_radius_um is None:
            raise ValueError("Enabled focus surface requires distance limits")
        if self.allow_extrapolation != (self.maximum_extrapolation_um > 0):
            raise ValueError(
                "Extrapolation flag and maximum extrapolation distance disagree"
            )
        if self.maximum_extrapolation_um > self.neighbor_radius_um:
            raise ValueError("Extrapolation distance cannot exceed neighbor radius")
        if (
            self.maximum_fit_residual_um is None
            or self.maximum_prediction_delta_um is None
            or self.maximum_fit_residual_um > self.maximum_prediction_delta_um
        ):
            raise ValueError("Fit residual gate cannot exceed prediction delta gate")
        if (
            self.measurement_offset_um is None
            or abs(self.measurement_offset_um) > self.maximum_prediction_delta_um
        ):
            raise ValueError("Measurement offset exceeds the prediction delta gate")
        return self


class WhiteSearchSettings(StrictModel):
    """Frozen bounds and reserve for one WHITE-to-R/G transfer."""

    search_z_range_um: tuple[float, float]
    white_search_timeout_s: float = Field(gt=0)
    total_focus_budget_s: float = Field(gt=0)
    approach_and_rg_reserve_s: float = Field(gt=0)
    maximum_white_led_disagreement_um: float = Field(gt=0)
    maximum_white_searches_per_field: Literal[1] = 1

    @field_validator("search_z_range_um", mode="before")
    @classmethod
    def range_accepts_json_array(cls, value: object) -> object:
        """Accept a JSON array but retain strict numeric validation."""
        return _json_tuple(value)

    @field_validator("maximum_white_searches_per_field", mode="before")
    @classmethod
    def exactly_one_integer_search(cls, value: object) -> object:
        """Make the one-WHITE-per-field invariant resistant to coercion."""
        if type(value) is not int or value != 1:
            raise ValueError("Exactly one WHITE search is allowed per field attempt")
        return value

    @model_validator(mode="after")
    def bounded_transfer_budget(self) -> Self:
        """Require a two-sided range and preserve the post-WHITE reserve."""
        low, high = self.search_z_range_um
        if not low < 0 < high:
            raise ValueError("WHITE search range must extend to both sides of zero")
        if (
            self.white_search_timeout_s + self.approach_and_rg_reserve_s
            > self.total_focus_budget_s
        ):
            raise ValueError(
                "WHITE timeout and approach/RG reserve exceed total budget"
            )
        if self.maximum_white_led_disagreement_um > high - low:
            raise ValueError("WHITE/LED disagreement gate exceeds the search range")
        return self


class FocusRunSettings(StrictModel):
    """Frozen scan choices that activate an otherwise inert surface contract."""

    autofocus_method: AutofocusMethod = "openflexure"
    focus_strategy: FocusStrategy = "smart_stack"
    surface: FocusSurfaceSettings = Field(default_factory=FocusSurfaceSettings)
    unpredicted_focus_mode: UnpredictedFocusMode = "selected_method"
    white_search: WhiteSearchSettings | None = None
    no_tissue_mode: NoTissueMode = "keep_z"
    on_focus_failure: Literal["pause"] = "pause"
    binding: FocusBinding | None = None

    @model_validator(mode="after")
    def supported_active_combination(self) -> Self:
        """Fail before motion for unsupported mixed-focus selections."""
        if not self.surface.enabled:
            if self.unpredicted_focus_mode == "white_then_led":
                raise ValueError("white_then_led requires an enabled focus surface")
            if self.white_search is not None:
                raise ValueError("WHITE transfer settings require white_then_led")
            return self
        if self.autofocus_method != "led" or self.focus_strategy != "single_autofocus":
            raise ValueError("Focus surface supports only LED single_autofocus")
        if self.binding is None:
            raise ValueError("Enabled focus surface requires a frozen binding")
        if not check_focus_binding(self.binding, self.binding).usable:
            raise ValueError("Enabled focus surface requires valid compatible profiles")
        if self.unpredicted_focus_mode == "white_then_led":
            if self.white_search is None:
                raise ValueError(
                    "white_then_led requires bounded WHITE transfer settings"
                )
        elif self.white_search is not None:
            raise ValueError("WHITE transfer settings require white_then_led")
        return self


class FocusObservationQuality(StrictModel):
    """Saved R/G measurement QC used to validate persisted observations."""

    measurement_status: MeasurementStatus
    confidence: float = Field(ge=0, le=1)
    accepted_patch_count: int = Field(ge=0)
    inlier_fraction: float = Field(ge=0, le=1)
    final_error_um: float | None = None
    focus_tolerance_um: float = Field(gt=0)
    independent_post_move_verification: bool

    @property
    def confirms_led_focus(self) -> bool:
        """Return whether QC, tolerance and independent verification passed."""
        return (
            self.measurement_status == "ready"
            and self.accepted_patch_count > 0
            and self.final_error_um is not None
            and abs(self.final_error_um) <= self.focus_tolerance_um
            and self.independent_post_move_verification
        )


class FocusObservationProvenance(StrictModel):
    """Stable result/report/frame identities, not artifact contents."""

    result_id: Identifier
    report_ref: Identifier
    rg_measurement_id: Identifier | None = None
    capture_ids: tuple[str, ...] = ()

    @field_validator("capture_ids", mode="before")
    @classmethod
    def captures_accept_json_array(cls, value: object) -> object:
        """Accept saved JSON arrays and keep the snapshot immutable."""
        return _json_tuple(value)

    @model_validator(mode="after")
    def unique_capture_ids(self) -> Self:
        """Prevent repeated frame identities from inflating provenance."""
        if len(self.capture_ids) != len(set(self.capture_ids)):
            raise ValueError("Capture IDs must be unique")
        if any(not value or value.isspace() for value in self.capture_ids):
            raise ValueError("Capture IDs must be non-empty")
        return self


class FocusPredictionEvidence(StrictModel):
    """Prediction saved before observation, including later signed error."""

    prediction_id: Identifier
    predicted_z_um: float
    prediction_error_um: float
    support_age_s: float = Field(ge=0)


class FocusObservation(StrictModel):
    """One immutable field result at a settled commanded XYZ readback."""

    scan_id: Identifier
    field_id: Identifier
    attempt_id: Identifier
    observation_id: Identifier
    x_um: float
    y_um: float
    z_um: float
    readback_kind: Literal["commanded_not_encoder"] = "commanded_not_encoder"
    final_readback_settled: bool
    final_readback_monotonic_s: float = Field(ge=0)
    recorded_monotonic_s: float = Field(ge=0)
    source: FocusObservationSource
    status: FocusObservationStatus
    binding: FocusBinding
    provenance: FocusObservationProvenance
    quality: FocusObservationQuality | None = None
    pre_update_prediction: FocusPredictionEvidence | None = None

    @model_validator(mode="after")
    def internally_consistent_result(self) -> Self:
        """Keep numeric positions from upgrading unverified results."""
        if self.final_readback_monotonic_s > self.recorded_monotonic_s:
            raise ValueError("Final readback cannot occur after observation recording")
        if self.pre_update_prediction is not None and not math.isclose(
            self.pre_update_prediction.prediction_error_um,
            self.z_um - self.pre_update_prediction.predicted_z_um,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError("Saved pre-update prediction error is inconsistent")
        if self.status != "confirmed":
            return self
        if self.source != "led_autofocus":
            raise ValueError("Only LED autofocus can create a confirmed observation")
        if not self.final_readback_settled:
            raise ValueError("Confirmed observation requires a settled final readback")
        if self.quality is None or not self.quality.confirms_led_focus:
            raise ValueError(
                "Confirmed observation requires independently verified LED QC"
            )
        if (
            self.provenance.rg_measurement_id is None
            or len(self.provenance.capture_ids) < 2
        ):
            raise ValueError(
                "Confirmed observation requires R/G measurement provenance"
            )
        if not check_focus_binding(self.binding, self.binding).usable:
            raise ValueError("Confirmed observation requires a valid LED binding")
        return self


class FocusObservationSuitability(StrictModel):
    """Serialized external decision about observation suitability."""

    usable: bool
    reason: Identifier
    age_s: float | None = Field(default=None, ge=0)


class FocusPredictionDiagnostics(StrictModel):
    """Fit/support diagnostics, explicitly not a confidence interval."""

    interpretation: Literal["diagnostic_not_confidence_interval"] = (
        "diagnostic_not_confidence_interval"
    )
    candidate_observation_count: int = Field(ge=0)
    local_observation_count: int = Field(default=0, ge=0)
    selected_support_count: int = Field(default=0, ge=0)
    nearest_distance_um: float | None = Field(default=None, ge=0)
    support_age_s: float | None = Field(default=None, ge=0)
    fit_rmse_um: float | None = Field(default=None, ge=0)
    maximum_absolute_residual_um: float | None = Field(default=None, ge=0)
    fit_rank: int | None = Field(default=None, ge=0, le=3)
    fit_condition_number: float | None = Field(default=None, ge=1)
    fit_inlier_fraction: float | None = Field(default=None, ge=0, le=1)
    plane_slope_x_um_per_um: float | None = None
    plane_slope_y_um_per_um: float | None = None
    plane_slope_magnitude_um_per_um: float | None = Field(default=None, ge=0)
    excluded_observation_ids: tuple[str, ...] = ()
    extrapolation_distance_um: float | None = Field(default=None, ge=0)
    prediction_delta_um: float | None = None

    @field_validator("excluded_observation_ids", mode="before")
    @classmethod
    def excluded_ids_accept_json_array(cls, value: object) -> object:
        """Accept persisted JSON arrays while retaining identities."""
        return _json_tuple(value)

    @model_validator(mode="after")
    def diagnostic_counts_and_ids_are_coherent(self) -> Self:
        """Keep bounded selection counts internally consistent."""
        if self.local_observation_count > self.candidate_observation_count:
            raise ValueError("Local observation count exceeds candidate count")
        if self.selected_support_count > self.local_observation_count:
            raise ValueError("Selected support count exceeds local count")
        if len(self.excluded_observation_ids) != len(
            set(self.excluded_observation_ids)
        ):
            raise ValueError("Excluded observation IDs must be unique")
        return self


class FocusPredictionRequest(StrictModel):
    """One process-neutral query in physical micrometres."""

    expected_scan_id: Identifier
    field_id: Identifier
    attempt_id: Identifier
    prediction_id: Identifier
    target_x_um: float
    target_y_um: float
    current_z_um: float
    predicted_monotonic_s: float = Field(ge=0)
    expected_binding: FocusBinding | None = None
    settings: FocusSurfaceSettings


class FocusPrediction(StrictModel):
    """A target-Z decision whose four statuses have disjoint payloads."""

    scan_id: Identifier
    field_id: Identifier
    attempt_id: Identifier
    prediction_id: Identifier
    target_x_um: float
    target_y_um: float
    target_z_um: float | None = None
    status: FocusPredictionStatus
    source: FocusPredictionSource | None = None
    reason: Identifier
    unavailable_reason: FocusUnavailableReason | None = None
    binding: FocusBinding | None = None
    support_observation_ids: tuple[str, ...] = ()
    support_field_ids: tuple[str, ...] = ()
    support_distances_um: tuple[Annotated[float, Field(ge=0)], ...] = ()
    diagnostics: FocusPredictionDiagnostics | None = None
    extrapolated: bool = False
    fault_code: FocusObservationFaultCode | None = None
    predicted_monotonic_s: float = Field(ge=0)

    @field_validator(
        "support_observation_ids",
        "support_field_ids",
        "support_distances_um",
        mode="before",
    )
    @classmethod
    def supports_accept_json_array(cls, value: object) -> object:
        """Accept JSON arrays while retaining immutable tuples."""
        return _json_tuple(value)

    @model_validator(mode="after")
    def status_payload_is_disjoint(self) -> Self:  # noqa: C901, PLR0912
        """Keep nonusable statuses from masquerading as target-Z values."""
        if len(self.support_observation_ids) != len(set(self.support_observation_ids)):
            raise ValueError("Prediction support IDs must be unique")
        if len(self.support_field_ids) != len(set(self.support_field_ids)):
            raise ValueError("Prediction support IDs must be unique")
        if not (
            len(self.support_observation_ids)
            == len(self.support_field_ids)
            == len(self.support_distances_um)
        ):
            raise ValueError("Prediction support IDs and distances must align")
        if any(
            not value or value.isspace()
            for value in (*self.support_observation_ids, *self.support_field_ids)
        ):
            raise ValueError("Prediction support IDs must be non-empty")
        if self.status == "usable":
            if self.target_z_um is None:
                raise ValueError("Usable prediction requires target Z, including Z=0")
            if self.source not in ("nearest_neighbor", "local_plane"):
                raise ValueError("Usable prediction requires an optical support source")
            if (
                not self.support_observation_ids
                or self.binding is None
                or self.diagnostics is None
            ):
                raise ValueError(
                    "Usable prediction requires binding, support and diagnostics"
                )
            if not check_focus_binding(self.binding, self.binding).usable:
                raise ValueError("Usable prediction requires a valid LED binding")
            if self.unavailable_reason is not None or self.fault_code is not None:
                raise ValueError("Usable prediction cannot carry refusal fields")
            if self.diagnostics.support_age_s is None:
                raise ValueError("Usable prediction requires support age")
            if self.source == "local_plane" and len(self.support_observation_ids) < 4:
                raise ValueError(
                    "Local plane prediction requires at least four supports"
                )
            return self
        if (
            self.target_z_um is not None
            or self.support_observation_ids
            or self.support_field_ids
            or self.support_distances_um
            or self.extrapolated
        ):
            raise ValueError("Only usable prediction may carry target Z or support")
        if self.status == "unavailable":
            if self.unavailable_reason is None:
                raise ValueError("Unavailable prediction requires an optical reason")
            if self.fault_code is not None:
                raise ValueError("Unavailable prediction cannot carry a fault code")
            if (
                self.binding is None
                or not check_focus_binding(self.binding, self.binding).usable
            ):
                raise ValueError(
                    "Profile/reference faults cannot be labelled optical unavailable"
                )
            return self
        if self.unavailable_reason is not None or self.source is not None:
            raise ValueError("Disabled/fault prediction cannot claim optical refusal")
        if self.diagnostics is not None:
            raise ValueError("Disabled/fault prediction cannot carry fit diagnostics")
        if self.status == "fault" and self.fault_code is None:
            raise ValueError("Fault prediction requires a narrow fault code")
        if self.status == "disabled" and self.fault_code is not None:
            raise ValueError("Disabled prediction cannot carry a fault code")
        return self


class FocusFieldProgress(StrictModel):
    """Field-scoped WHITE-to-R/G checkpoint data."""

    scan_id: Identifier
    field_id: Identifier
    attempt_id: Identifier
    prediction_id: Identifier
    stage: Literal["planned", "white_completed", "rg_verified"]
    white_search_id: Identifier
    white_search_count: Literal[1] = 1
    white_status: Literal["planned", "succeeded", "failed", "unknown"]
    white_result_id: Identifier | None = None
    rg_status: Literal["not_started", "succeeded", "failed", "unknown"]
    rg_result_id: Identifier | None = None
    automatic_white_retry_allowed: Literal[False] = False
    recorded_monotonic_s: float = Field(ge=0)

    @field_validator("white_search_count", mode="before")
    @classmethod
    def one_white_search_only(cls, value: object) -> object:
        """Reject bool/coerced/repeated WHITE search counts."""
        if type(value) is not int or value != 1:
            raise ValueError("Exactly one WHITE search is recorded per field attempt")
        return value

    @model_validator(mode="after")
    def sequential_checkpoint(self) -> Self:
        """Reject impossible stage/outcome combinations and hidden retries."""
        white_has_result = self.white_status in ("succeeded", "failed")
        if white_has_result != (self.white_result_id is not None):
            raise ValueError("WHITE completion status and result ID disagree")
        rg_has_result = self.rg_status in ("succeeded", "failed")
        if rg_has_result != (self.rg_result_id is not None):
            raise ValueError("R/G completion status and result ID disagree")
        if self.stage == "planned":
            if self.white_status == "succeeded" or self.rg_status != "not_started":
                raise ValueError("Planned stage cannot claim completed WHITE or R/G")
        elif self.stage == "white_completed":
            if self.white_status != "succeeded" or self.rg_status == "succeeded":
                raise ValueError("white_completed requires WHITE success before R/G")
        elif self.white_status != "succeeded" or self.rg_status != "succeeded":
            raise ValueError("rg_verified requires successful WHITE and R/G results")
        return self
