"""Run-scoped integration of focus-surface decisions with the native scan loop.

This module deliberately does not own a route, camera, transport, or autofocus
algorithm.  It freezes one scan contract, prepares exactly the target selected by
the existing route planner, and records atomic evidence around the existing owners.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Literal, Self, cast
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

import labthings_fastapi as lt

from ..fast_ofm_contracts import StrictModel
from .focus_approach import (
    CorrectionProgress,
    FocusApproachPolicy,
    FocusApproachRequest,
    FocusCorrectionRequest,
    FocusFieldBudget,
    FocusInitialCorrectionRequest,
    PreparedApproach,
    WhitePeakApproachRequest,
    WhiteSweepRequest,
    plan_focus_approach,
    plan_focus_correction,
    plan_initial_focus_correction,
    plan_white_peak_approach,
    plan_white_sweep,
)
from .focus_surface import (
    FocusBinding,
    FocusFieldProgress,
    FocusObservation,
    FocusObservationProvenance,
    FocusObservationQuality,
    FocusPrediction,
    FocusPredictionEvidence,
    FocusPredictionRequest,
    FocusRunSettings,
    check_focus_binding,
)
from .rg.rg_focus_control import RGFocusControlSettings
from .rg.rg_focus_estimator import RGFocusMeasurement
from .rg.rg_focus_model import RGFocusMeasurementPolicy, RGFocusModelProfile


class FocusScanSafetyError(RuntimeError):
    """Stop an enabled scan without retrying or returning after an unsafe outcome."""


@dataclass(frozen=True)
class _ValidatedRGResult:
    """Immutable bridge between R/G verification and post-tile map insertion."""

    observation: FocusObservation
    verification_mode: Literal["measured_at_final", "single_pair_model"]
    total_correction_um: float | None
    duration_s: float | None


def _json_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


class FocusExperimentEnvelope(StrictModel):
    """Explicit scan-local physical XYZ limits in micrometres."""

    x_um: tuple[float, float]
    y_um: tuple[float, float]
    z_um: tuple[float, float]

    @field_validator("x_um", "y_um", "z_um", mode="before")
    @classmethod
    def accept_json_ranges(cls, value: object) -> object:
        """Accept persisted arrays while retaining immutable range tuples."""
        return _json_tuple(value)

    @model_validator(mode="after")
    def ordered_ranges(self) -> Self:
        """Reject empty envelopes rather than widening or guessing a limit."""
        if any(low >= high for low, high in (self.x_um, self.y_um, self.z_um)):
            raise ValueError("Focus experiment XYZ ranges must have positive width")
        return self

    def as_stage_mapping(self) -> dict[str, tuple[float, float]]:
        """Return the shape consumed by the accepted Moonraker snapshot boundary."""
        return {"x": self.x_um, "y": self.y_um, "z": self.z_um}


class FocusScanSettings(StrictModel):
    """One frozen runtime contract layered directly on ``FocusRunSettings``.

    Operational budgets have no active defaults.  Old workflow JSON therefore
    remains off/selected_method, while an enabled request must name every gate.
    """

    run: FocusRunSettings = Field(default_factory=FocusRunSettings)
    expected_prediction_error_range_um: tuple[float, float] | None = None
    maximum_field_elapsed_s: float | None = Field(default=None, gt=0)
    maximum_field_z_travel_um: float | None = Field(default=None, gt=0)
    post_move_verification_reserve_s: float | None = Field(default=None, gt=0)
    experiment_envelope: FocusExperimentEnvelope | None = None

    @field_validator("expected_prediction_error_range_um", mode="before")
    @classmethod
    def accept_json_prediction_range(cls, value: object) -> object:
        """Accept one persisted two-sided range without inventing uncertainty."""
        return _json_tuple(value)

    @model_validator(mode="after")
    def complete_enabled_budget(self) -> Self:
        """Require all field limits before an enabled surface can authorize motion."""
        if not self.run.surface.enabled:
            return self
        required = (
            self.expected_prediction_error_range_um,
            self.maximum_field_elapsed_s,
            self.maximum_field_z_travel_um,
            self.post_move_verification_reserve_s,
            self.experiment_envelope,
        )
        if any(value is None for value in required):
            raise ValueError(
                "Enabled focus scan requires every field budget and XYZ envelope"
            )
        low, high = self.expected_prediction_error_range_um  # type: ignore[misc]
        if not low <= 0 <= high or low == high:
            raise ValueError(
                "Prediction error range must contain zero with positive width"
            )
        white = self.run.white_search
        maximum_elapsed = self.maximum_field_elapsed_s
        if maximum_elapsed is None:
            raise ValueError("Enabled focus scan omitted its elapsed budget")
        if white is not None and white.total_focus_budget_s > maximum_elapsed:
            raise ValueError("WHITE transfer budget exceeds the complete field budget")
        return self

    def with_selection(
        self,
        autofocus_method: Literal["none", "openflexure", "led", "simultaneous_rg"],
        focus_strategy: Literal["single_autofocus", "smart_stack"],
    ) -> Self:
        """Bind old top-level selectors to the single frozen run contract."""
        run = self.run
        if run.surface.enabled and (
            run.autofocus_method != autofocus_method
            or run.focus_strategy != focus_strategy
        ):
            raise ValueError(
                "Enabled focus contract disagrees with the selected method"
            )
        if not run.surface.enabled:
            run = run.model_copy(
                update={
                    "autofocus_method": autofocus_method,
                    "focus_strategy": focus_strategy,
                }
            )
        return self.model_copy(update={"run": run})


class FocusRuntimeCounters(StrictModel):
    """Bounded scan-local counters updated only at journal publication boundaries."""

    fields_planned: int = Field(default=0, ge=0)
    fields_no_tissue: int = Field(default=0, ge=0)
    fields_focused: int = Field(default=0, ge=0)
    fields_captured: int = Field(default=0, ge=0)
    fields_failed: int = Field(default=0, ge=0)
    fields_cancelled: int = Field(default=0, ge=0)
    fields_unknown: int = Field(default=0, ge=0)
    white_searches_completed: int = Field(default=0, ge=0)


class FocusRuntimeSummary(StrictModel):
    """Small in-memory view of the latest successfully published field outcome."""

    enabled: bool
    scan_state: Literal["inactive", "running", "completed", "stopped"]
    outcome: Literal[
        "not_started",
        "planned",
        "no_tissue",
        "failed",
        "cancelled",
        "unknown",
        "focused",
        "captured",
    ]
    path: Literal["mapped_rg", "selected_method", "white_then_rg"]
    selected_method: Literal["none", "openflexure", "led", "simultaneous_rg"]
    focus_strategy: Literal["single_autofocus", "smart_stack"]
    stage: str
    field_id: str | None = None
    predicted_z_um: float | None = None
    measured_z_um: float | None = None
    final_z_um: float | None = None
    white_focus_z_um: float | None = None
    reason: str
    scan_reason: str | None = None
    white_search_count: Literal[0, 1] = 0
    counters: FocusRuntimeCounters = Field(default_factory=FocusRuntimeCounters)

    @classmethod
    def from_settings(cls, settings: FocusScanSettings) -> Self:
        """Create a truthful pre-field view from the already frozen scan settings."""
        enabled = settings.run.surface.enabled
        return cls(
            enabled=enabled,
            scan_state="running" if enabled else "inactive",
            outcome="not_started",
            path="selected_method",
            selected_method=settings.run.autofocus_method,
            focus_strategy=settings.run.focus_strategy,
            stage="not_started",
            reason=(
                "Waiting for the first focus field"
                if enabled
                else "Focus prediction is disabled; the selected method is unchanged"
            ),
        )


class FrozenRGFocusSettings(StrictModel):
    """Exact R/G inputs captured before the scan's first movement."""

    profile: RGFocusModelProfile
    measurement_policy: RGFocusMeasurementPolicy
    control: RGFocusControlSettings
    binding: FocusBinding
    effective_focus_tolerance_um: float = Field(gt=0, le=4)

    @model_validator(mode="after")
    def matches_profile_identity(self) -> Self:
        """Reject a torn snapshot assembled across two R/G configurations."""
        if self.profile.id != self.binding.rg_focus_model_id:
            raise ValueError("Frozen R/G profile identity disagrees with its binding")
        if self.profile.measurement_policy != self.measurement_policy:
            raise ValueError("Frozen R/G profile and measurement policy disagree")
        return self


EvidenceWriter = Callable[[str, Mapping[str, Any]], None]
CurrentBinding = Callable[[], FocusBinding]
SurfacePredictor = Callable[
    [tuple[FocusObservation, ...], FocusPredictionRequest], FocusPrediction
]


class FocusFieldRuntime:
    """Mutable state for one field attempt, consumed by the existing workflow."""

    def __init__(
        self,
        session: FocusScanSession,
        *,
        field_id: str,
        attempt_id: str,
        prediction: FocusPrediction,
        target_units: tuple[int, int],
        target_um: tuple[float, float],
        budget: FocusFieldBudget,
    ) -> None:
        """Create one unfinished attempt from the already chosen planner target."""
        self.session = session
        self.field_id = field_id
        self.attempt_id = attempt_id
        self.prediction = prediction
        self.target_units = target_units
        self.target_um = target_um
        self.budget = budget
        self.prepared: PreparedApproach | None = None
        self.last_correction_z_path_units: tuple[int, ...] = ()
        self.mode: Literal["mapped", "selected_method", "white_then_led"] = (
            "mapped" if prediction.status == "usable" else "selected_method"
        )
        self.standard_transit = prediction.status != "usable"
        self.no_tissue = False
        self.white_focus_z_um: float | None = None
        self.white_result_id: str | None = None
        self.focus_progress: FocusFieldProgress | None = None
        self.stage = "planned"
        self._last_xyz_um: tuple[float, float, float] | None = budget.origin_xyz_um
        self._validated_rg_result: _ValidatedRGResult | None = None
        self.focus_deadline_monotonic_s: float | None = None
        self.white_search_deadline_monotonic_s: float | None = None

    @property
    def frozen(self) -> FrozenRGFocusSettings:
        """Expose the immutable R/G snapshot to its existing owner."""
        return self.session.frozen_rg

    @property
    def policy(self) -> FocusApproachPolicy:
        """Return the one approach/correction policy frozen for this scan."""
        return self.session.policy

    @property
    def experiment_envelope(self) -> dict[str, tuple[float, float]]:
        """Return the run-frozen stage envelope."""
        return self.session.experiment_envelope

    def current_binding(self) -> FocusBinding:
        """Re-read and compare every live identity at a physical boundary."""
        current = self.session.current_binding()
        check = check_focus_binding(self.frozen.binding, current)
        if not check.compatible or not check.usable:
            raise FocusScanSafetyError(f"Focus binding changed: {check.reason}")
        return current

    def _elapsed(self) -> float:
        return self.budget.elapsed_at(time.monotonic())

    def _refresh_elapsed(self) -> float:
        """Age the field budget through journal/compute time without an effect."""
        elapsed = self._elapsed()
        if elapsed > self.budget.maximum_elapsed_s:
            raise FocusScanSafetyError("Focus field deadline is exhausted")
        self.budget = self.budget.model_copy(update={"elapsed_s": elapsed})
        return elapsed

    def _check_effect_boundary(self, name: str, *, reserve_s: float = 0.0) -> None:
        """Recheck cancellation, physical binding and every active real deadline."""
        if self.session._stopped:
            raise FocusScanSafetyError("Focus session already stopped before effect")
        lt.raise_if_cancelled()
        self.current_binding()
        # The live binding read may block, so cancellation and time are checked
        # afterwards at the final known-no-command boundary as well.
        lt.raise_if_cancelled()
        now = time.monotonic()
        elapsed = self.budget.elapsed_at(now)
        if elapsed + reserve_s > self.budget.maximum_elapsed_s:
            raise FocusScanSafetyError(
                f"Field deadline cannot preserve the reserve before {name}"
            )
        if (
            self.focus_deadline_monotonic_s is not None
            and now + reserve_s > self.focus_deadline_monotonic_s
        ):
            raise FocusScanSafetyError(
                f"WHITE-to-R/G deadline cannot preserve the reserve before {name}"
            )
        if (
            self.white_search_deadline_monotonic_s is not None
            and now > self.white_search_deadline_monotonic_s
        ):
            raise FocusScanSafetyError(f"WHITE search deadline expired before {name}")
        self.budget = self.budget.model_copy(update={"elapsed_s": elapsed})

    def before_effect(self, name: str, *, reserve_s: float = 0.0) -> None:
        """Checkpoint and validate the cumulative deadline before a new effect."""
        self._check_effect_boundary(name, reserve_s=reserve_s)
        self.session.record(self, "effect_planned", {"effect": name})
        # Atomic evidence is mandatory and may itself block.  Recheck after it,
        # before the caller can reach any physical owner.
        self._check_effect_boundary(name, reserve_s=reserve_s)

    def preflight_z_path_units(self, targets: tuple[int, ...]) -> None:
        """Check an enabled ordinary-path Z sequence against field limits."""
        snapshot = self.session.stage.focus_motion_snapshot(self.experiment_envelope)
        z_axis = snapshot.axes[2]
        previous = snapshot.current_quantized_xyz_um[2]
        added = 0.0
        for target in targets:
            target_um = target * 1000 / z_axis.units_per_mm
            if not z_axis.experiment.contains(target_um):
                raise FocusScanSafetyError(
                    "Selected-method Z path exceeds the experiment envelope"
                )
            added += abs(target_um - previous)
            previous = target_um
        if self.budget.z_travel_um + added > self.budget.maximum_z_travel_um:
            raise FocusScanSafetyError(
                "Selected-method Z path exceeds the field travel budget"
            )

    @contextmanager
    def motion_guard(self, name: str, *, reserve_s: float = 0.0) -> Iterator[None]:
        """Checkpoint once and enforce the same gate at the native final POST."""
        self.before_effect(name, reserve_s=reserve_s)
        with self.motion_post_guard(name, reserve_s=reserve_s):
            yield

    @contextmanager
    def motion_post_guard(self, name: str, *, reserve_s: float = 0.0) -> Iterator[None]:
        """Enforce a previously checkpointed effect at the final native POST."""
        with self.session.stage.motion_pre_post_check(
            lambda: self._check_effect_boundary(name, reserve_s=reserve_s)
        ):
            yield

    def sync_after_effect(  # noqa: C901
        self, name: str, *, actual_z_travel_um: float | None = None
    ) -> None:
        """Age time/travel from actual confirmed readback; never reset the budget."""
        snapshot_error: BaseException | None = None
        binding_error: BaseException | None = None
        snapshot = None
        try:
            snapshot = self.session.stage.focus_motion_snapshot(
                self.experiment_envelope
            )
        except BaseException as exc:
            snapshot_error = exc
        try:
            self.current_binding()
        except BaseException as exc:
            binding_error = exc

        elapsed = self._elapsed()
        if snapshot is None:
            if snapshot_error is None:
                snapshot_error = FocusScanSafetyError(
                    "Post-effect stage snapshot returned no authoritative state"
                )
            # A completed command with failed readback has no authoritative current
            # position.  Do not let the pre-command value survive as reached state.
            self._last_xyz_um = None
            self.budget = self.budget.model_copy(update={"elapsed_s": elapsed})
            self.session.record(
                self,
                "effect_reconciliation_failed",
                {
                    "effect": name,
                    "actual_state": "unknown",
                    "readback_error_type": type(snapshot_error).__name__,
                    "readback_error": str(snapshot_error),
                    "binding_error_type": (
                        type(binding_error).__name__
                        if binding_error is not None
                        else None
                    ),
                    "binding_error": (
                        str(binding_error) if binding_error is not None else None
                    ),
                },
            )
            self.session.stop(self, snapshot_error)
            if binding_error is not None:
                raise snapshot_error from binding_error
            raise snapshot_error

        current = snapshot.current_quantized_xyz_um
        previous = self._last_xyz_um
        if previous is None:
            error = FocusScanSafetyError(
                "Cannot reconcile an effect after actual stage state became unknown"
            )
            self.session.stop(self, error)
            raise error
        added_z = (
            abs(current[2] - previous[2])
            if actual_z_travel_um is None
            else actual_z_travel_um
        )
        exhausted_z = (
            self.budget.z_travel_um + added_z > self.budget.maximum_z_travel_um
        )
        exhausted_time = elapsed > self.budget.maximum_elapsed_s
        now = time.monotonic()
        exhausted_focus = (
            self.focus_deadline_monotonic_s is not None
            and now > self.focus_deadline_monotonic_s
        )
        exhausted_white = (
            self.white_search_deadline_monotonic_s is not None
            and now > self.white_search_deadline_monotonic_s
        )
        self.budget = self.budget.model_copy(
            update={
                "elapsed_s": elapsed,
                "z_travel_um": self.budget.z_travel_um + added_z,
            }
        )
        self._last_xyz_um = current
        self.session.record(
            self,
            "effect_confirmed",
            {
                "effect": name,
                "actual_commanded_xyz_um": list(current),
                "actual_z_travel_um": added_z,
                "binding_status": (
                    "mismatch" if binding_error is not None else "compatible"
                ),
                "binding_error_type": (
                    type(binding_error).__name__ if binding_error is not None else None
                ),
                "binding_error": (
                    str(binding_error) if binding_error is not None else None
                ),
            },
        )
        if binding_error is not None:
            self.session.stop(self, binding_error)
            raise binding_error
        if exhausted_z:
            raise FocusScanSafetyError(
                "Confirmed move exhausted the field Z-travel budget"
            )
        if exhausted_time:
            raise FocusScanSafetyError("Confirmed effect exhausted the field deadline")
        if exhausted_focus:
            raise FocusScanSafetyError(
                "Confirmed effect exhausted the WHITE-to-R/G deadline"
            )
        if exhausted_white:
            raise FocusScanSafetyError(
                "Confirmed effect exhausted the WHITE search deadline"
            )

    def validate_rg_result(self, result: Mapping[str, Any]) -> None:
        """Validate and close R/G timing without publishing a map observation."""
        try:
            self.before_effect(
                "rg_result_validate",
                reserve_s=self.budget.post_move_verification_reserve_s,
            )
            self._validated_rg_result = self.session.validate_rg_result(self, result)
        except BaseException as exc:
            if self is self.session.active_field and self.stage != "focused":
                identifier = result.get("id")
                self.record_rg_failure(
                    identifier if isinstance(identifier, str) and identifier else None,
                    exc,
                )
            raise

    def accept_rg_result(self) -> None:
        """Publish the already validated result after tile restore and binding sync."""
        self.session.accept_rg_result(self)

    def record_rg_failure(self, result_id: str | None, exc: BaseException) -> None:
        """Preserve a failed R/G outcome without turning WHITE into a map point."""
        if self.focus_progress is not None:
            self.focus_progress = self.focus_progress.model_copy(
                update={
                    "rg_status": "failed" if result_id is not None else "unknown",
                    "rg_result_id": result_id,
                    "recorded_monotonic_s": time.monotonic(),
                }
            )
        self.session.record(
            self,
            "rg_failed" if result_id is not None else "rg_unknown",
            {
                "rg_result_id": result_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )

    def mark_measured(
        self,
        *,
        iteration: int,
        capture_id: str,
        frame_ids: tuple[str, ...],
        measurement_status: str,
    ) -> None:
        """Checkpoint a completed R/G measurement before its control decision."""
        if self._last_xyz_um is None:
            raise FocusScanSafetyError(
                "Cannot measure while actual stage state is unknown"
            )
        self.stage = "measured"
        self.session.record(
            self,
            "measured",
            {
                "iteration": iteration,
                "capture_id": capture_id,
                "frame_ids": list(frame_ids),
                "measurement_status": measurement_status,
                "measured_z_um": self._last_xyz_um[2],
            },
        )

    def before_main_capture(self) -> None:
        """Reserve and checkpoint the distinct saved-tile acquisition."""
        reserve = self.budget.post_move_verification_reserve_s
        self.before_effect("white_tile_capture", reserve_s=reserve)


class FocusScanSession:
    """One scan-local surface and append-only evidence archive."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        scan_id: str,
        settings: FocusScanSettings,
        stage: Any,
        rg_focus: Any,
        autofocus: Any,
        frozen_rg: FrozenRGFocusSettings,
        current_binding: CurrentBinding,
        surface_predictor: SurfacePredictor,
        evidence_writer: EvidenceWriter,
    ) -> None:
        """Freeze owners/settings and publish a no-resume manifest before motion."""
        if not settings.run.surface.enabled:
            raise ValueError("A focus session cannot be created for a disabled surface")
        self.scan_id = scan_id
        self.settings = settings.model_copy(deep=True)
        self.stage = stage
        self.rg_focus = rg_focus
        self.autofocus = autofocus
        self.frozen_rg = frozen_rg.model_copy(deep=True)
        self._current_binding = current_binding
        self._surface_predictor = surface_predictor
        self._write = evidence_writer
        self.observations: deque[FocusObservation] = deque(
            maxlen=settings.run.surface.maximum_candidate_observations
        )
        self._observation_ids: deque[str] = deque(
            maxlen=settings.run.surface.maximum_candidate_observations
        )
        self._sequence = 0
        self.active_field: FocusFieldRuntime | None = None
        self.return_to_start_allowed = False
        self._stopped = False
        self._terminal_status_field_id: str | None = None
        self._terminal_status_outcome: (
            Literal["failed", "cancelled", "unknown"] | None
        ) = None
        self._runtime_summary = FocusRuntimeSummary.from_settings(self.settings)
        self.policy = self._build_policy()
        envelope = settings.experiment_envelope
        if envelope is None:
            raise ValueError("Enabled focus scan omitted its XYZ envelope")
        self.experiment_envelope = envelope.as_stage_mapping()
        check = check_focus_binding(settings.run.binding, frozen_rg.binding)  # type: ignore[arg-type]
        if not check.compatible or not check.usable:
            raise FocusScanSafetyError(
                f"Frozen focus binding is not current: {check.reason}"
            )
        live_check = check_focus_binding(frozen_rg.binding, current_binding())
        if not live_check.compatible or not live_check.usable:
            raise FocusScanSafetyError(
                f"Live focus binding changed before the manifest: {live_check.reason}"
            )
        self._write(
            "manifest.json",
            {
                "scan_id": self.scan_id,
                "resume_allowed": False,
                "settings": self.settings.model_dump(mode="json"),
                "frozen_rg": self.frozen_rg.model_dump(mode="json"),
                "started_monotonic_s": time.monotonic(),
            },
        )

    @property
    def runtime_summary(self) -> FocusRuntimeSummary:
        """Return a detached copy without reading the controller or evidence archive."""
        return self._runtime_summary.model_copy(deep=True)

    def _build_policy(self) -> FocusApproachPolicy:
        surface = self.settings.run.surface
        expected = self.settings.expected_prediction_error_range_um
        if surface.measurement_offset_um is None or expected is None:
            raise ValueError("Enabled focus scan omitted approach policy inputs")
        control = self.frozen_rg.control
        return FocusApproachPolicy(
            approach_profile_id=self.frozen_rg.binding.approach_profile_id,
            measurement_offset_um=surface.measurement_offset_um,
            expected_prediction_error_range_um=expected,
            rg_applicable_error_range_um=self.frozen_rg.profile.applicable_error_range_um,
            focus_tolerance_um=self.frozen_rg.effective_focus_tolerance_um,
            maximum_single_correction_um=control.maximum_single_correction_um,
            maximum_total_correction_um=control.maximum_total_correction_um,
            maximum_iterations=control.maximum_iterations,
        )

    def current_binding(self) -> FocusBinding:
        """Return a freshly generated live binding."""
        return self._current_binding()

    def _field_payload(self, field: FocusFieldRuntime) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "field_id": field.field_id,
            "attempt_id": field.attempt_id,
            "prediction_id": field.prediction.prediction_id,
            "target_stage_units": list(field.target_units),
            "target_xy_um": list(field.target_um),
            "path": field.mode,
            "state": field.stage,
            "prediction": field.prediction.model_dump(mode="json"),
            "budget": field.budget.model_dump(mode="json"),
            "white_progress": (
                field.focus_progress.model_dump(mode="json")
                if field.focus_progress is not None
                else None
            ),
            "no_tissue": field.no_tissue,
            "white_focus_z_um": field.white_focus_z_um,
            "automatic_resume_allowed": False,
        }

    def record(
        self, field: FocusFieldRuntime, event: str, details: Mapping[str, Any]
    ) -> None:
        """Atomically publish one unique event and the latest field checkpoint."""
        self._sequence += 1
        event_id = str(uuid4())
        payload = {
            "event_id": event_id,
            "sequence": self._sequence,
            "event": event,
            "recorded_monotonic_s": time.monotonic(),
            **self._field_payload(field),
            "details": dict(details),
        }
        try:
            self._write(f"events/{self._sequence:08d}-{event_id}.json", payload)
            self._write(f"fields/{field.field_id}.json", payload)
        except BaseException as exc:
            self._stopped = True
            self.return_to_start_allowed = False
            self._publish_journal_failure(field, exc)
            raise
        self._publish_runtime_summary(field, event, details)

    def _increment_counter(self, name: str) -> None:
        """Increment one named immutable counter without retaining field history."""
        counters = self._runtime_summary.counters
        self._runtime_summary = self._runtime_summary.model_copy(
            update={
                "counters": counters.model_copy(
                    update={name: getattr(counters, name) + 1}
                )
            }
        )

    def _publish_terminal_outcome(
        self,
        field: FocusFieldRuntime,
        outcome: Literal["failed", "cancelled", "unknown"],
    ) -> None:
        """Count at most one terminal failure class for the sequential active field."""
        if self._terminal_status_field_id == field.field_id:
            previous = self._terminal_status_outcome
            if previous in (outcome, "unknown") or (
                previous == "cancelled" and outcome == "failed"
            ):
                return
            # The outer lifecycle can identify cancellation after an RG error
            # was journalled. Reclassify this field, never count it twice.
            if previous is not None:
                counters = self._runtime_summary.counters
                name = f"fields_{previous}"
                self._runtime_summary = self._runtime_summary.model_copy(
                    update={
                        "counters": counters.model_copy(
                            update={name: getattr(counters, name) - 1}
                        )
                    }
                )
        self._terminal_status_field_id = field.field_id
        self._terminal_status_outcome = outcome
        self._increment_counter(f"fields_{outcome}")

    def _publish_journal_failure(
        self, field: FocusFieldRuntime, exc: BaseException
    ) -> None:
        """Expose uncertainty without publishing the uncommitted success transition."""
        self._publish_terminal_outcome(field, "unknown")
        self._runtime_summary = self._runtime_summary.model_copy(
            update={
                "scan_state": "stopped",
                "outcome": "unknown",
                "scan_reason": "Focus evidence journal write failed",
                "reason": f"Focus evidence journal write failed: {exc}",
            }
        )

    def _publish_runtime_summary(  # noqa: C901, PLR0912
        self, field: FocusFieldRuntime, event: str, details: Mapping[str, Any]
    ) -> None:
        """Derive one bounded view only after both event and checkpoint writes succeed."""
        if (
            self._terminal_status_field_id == field.field_id
            and self._terminal_status_outcome == "unknown"
        ):
            # A later error-report write cannot recover missing mandatory evidence.
            # Keep the last published measurements and the uncertainty reason.
            if event == "stopped":
                self._runtime_summary = self._runtime_summary.model_copy(
                    update={"scan_state": "stopped"}
                )
            return
        path = {
            "mapped": "mapped_rg",
            "selected_method": "selected_method",
            "white_then_led": "white_then_rg",
        }[field.mode]
        update: dict[str, Any] = {
            "field_id": field.field_id,
            "path": path,
            "stage": field.stage,
            "predicted_z_um": field.prediction.target_z_um,
            "white_focus_z_um": field.white_focus_z_um,
        }
        if event == "field_planned":
            self._increment_counter("fields_planned")
            update.update(
                outcome="planned",
                reason=field.prediction.reason,
                measured_z_um=None,
                final_z_um=None,
                white_search_count=0,
            )
        elif event == "tissue_checked":
            update["reason"] = str(details.get("reason", "Tissue check completed"))
        elif event == "no_tissue":
            self._increment_counter("fields_no_tissue")
            update.update(
                outcome="no_tissue",
                reason=f"No tissue; policy: {details.get('policy', 'unknown')}",
            )
        elif event == "white_completed":
            self._increment_counter("white_searches_completed")
            update.update(
                white_search_count=1,
                white_focus_z_um=details.get("focus_z_um"),
                reason="WHITE focus completed; R/G verification is still required",
            )
        elif event == "measured":
            update.update(
                measured_z_um=details.get("measured_z_um"),
                reason=f"R/G measurement: {details.get('measurement_status', 'unknown')}",
            )
        elif event == "focused":
            self._increment_counter("fields_focused")
            update.update(
                outcome="focused",
                final_z_um=details.get("final_z_um"),
                reason=(
                    "R/G focus was independently verified and journalled"
                    if details.get("surface_update")
                    else (
                        "One-pair R/G correction passed the frozen model; "
                        "the surface was not updated"
                    )
                ),
            )
        elif event == "captured":
            self._increment_counter("fields_captured")
            if field.no_tissue:
                update.update(
                    outcome="no_tissue",
                    reason="No tissue; keep_z WHITE tile was captured",
                )
            else:
                update.update(
                    outcome="captured",
                    final_z_um=(
                        field._last_xyz_um[2]
                        if field._last_xyz_um is not None
                        else None
                    ),
                    reason="Focused field and saved WHITE tile were journalled",
                )
        elif event == "rg_failed":
            self._publish_terminal_outcome(field, "failed")
            update.update(
                outcome="failed", reason=str(details.get("error", "R/G failed"))
            )
        elif event in ("rg_unknown", "white_unknown"):
            self._publish_terminal_outcome(field, "unknown")
            update.update(
                outcome="unknown",
                reason=str(details.get("error", f"{event} outcome is unknown")),
            )
        elif event == "stopped":
            cancelled = bool(details.get("cancelled"))
            current_outcome = self._runtime_summary.outcome
            if field.no_tissue:
                outcome = "no_tissue"
            elif current_outcome == "unknown":
                outcome = "unknown"
            elif cancelled:
                outcome = "cancelled"
            else:
                outcome = "failed"
            if outcome in ("failed", "cancelled", "unknown"):
                self._publish_terminal_outcome(
                    field,
                    cast(Literal["failed", "cancelled", "unknown"], outcome),
                )
            update.update(
                scan_state="stopped",
                outcome=outcome,
                scan_reason=str(details.get("error", "Focus scan stopped")),
                reason=str(details.get("error", "Focus scan stopped")),
            )
        self._runtime_summary = self._runtime_summary.model_copy(update=update)

    def _new_budget(
        self, started: float, origin: tuple[float, float, float]
    ) -> FocusFieldBudget:
        maximum_elapsed = self.settings.maximum_field_elapsed_s
        maximum_z = self.settings.maximum_field_z_travel_um
        reserve = self.settings.post_move_verification_reserve_s
        if maximum_elapsed is None or maximum_z is None or reserve is None:
            raise ValueError("Enabled focus scan omitted its field budget")
        return FocusFieldBudget(
            budget_id=str(uuid4()),
            origin_xyz_um=origin,
            deadline_monotonic_s=started + maximum_elapsed,
            maximum_elapsed_s=maximum_elapsed,
            elapsed_s=0,
            maximum_z_travel_um=maximum_z,
            z_travel_um=0,
            post_move_verification_reserve_s=reserve,
        )

    def prepare_field(self, target_units: tuple[int, int]) -> FocusFieldRuntime:
        """Predict and prepare the planner's actual next XY exactly once."""
        if self._stopped or self.active_field is not None:
            raise FocusScanSafetyError("Focus session has an unfinished field attempt")
        started = time.monotonic()
        snapshot = self.stage.focus_motion_snapshot(self.experiment_envelope)
        axes = snapshot.axes
        target_um = (
            target_units[0] * 1000 / axes[0].units_per_mm,
            target_units[1] * 1000 / axes[1].units_per_mm,
        )
        field_id = str(uuid4())
        attempt_id = str(uuid4())
        prediction_id = str(uuid4())
        prediction = self._surface_predictor(
            tuple(self.observations),
            FocusPredictionRequest(
                expected_scan_id=self.scan_id,
                field_id=field_id,
                attempt_id=attempt_id,
                prediction_id=prediction_id,
                target_x_um=target_um[0],
                target_y_um=target_um[1],
                current_z_um=snapshot.current_quantized_xyz_um[2],
                predicted_monotonic_s=time.monotonic(),
                expected_binding=self.frozen_rg.binding,
                settings=self.settings.run.surface,
            ),
        )
        field = FocusFieldRuntime(
            self,
            field_id=field_id,
            attempt_id=attempt_id,
            prediction=prediction,
            target_units=target_units,
            target_um=target_um,
            budget=self._new_budget(started, snapshot.current_quantized_xyz_um),
        )
        self.active_field = field
        if (
            prediction.status == "unavailable"
            and self.settings.run.unpredicted_focus_mode == "white_then_led"
        ):
            field.mode = "white_then_led"
            field.standard_transit = False
            field.focus_progress = FocusFieldProgress(
                scan_id=self.scan_id,
                field_id=field_id,
                attempt_id=attempt_id,
                prediction_id=prediction_id,
                stage="planned",
                white_search_id=str(uuid4()),
                white_status="planned",
                rg_status="not_started",
                recorded_monotonic_s=time.monotonic(),
            )
            white = self.settings.run.white_search
            if white is None:
                raise FocusScanSafetyError("WHITE fallback lacks frozen settings")
            field.focus_deadline_monotonic_s = (
                field.budget.deadline_monotonic_s
                - field.budget.maximum_elapsed_s
                + white.total_focus_budget_s
            )
        self.record(field, "field_planned", {})
        try:
            if prediction.status == "fault":
                raise FocusScanSafetyError(
                    f"Focus prediction fault: {prediction.reason}"
                )
            if prediction.status == "disabled":
                raise FocusScanSafetyError("Enabled focus session returned disabled")
            if prediction.status == "usable":
                self._prepare_mapped(field, snapshot)
            elif field.mode == "white_then_led":
                self._prepare_white(field, snapshot)
        except BaseException as exc:
            self.stop(field, exc)
            raise
        return field

    def _prepare_mapped(self, field: FocusFieldRuntime, snapshot: Any) -> None:
        field._refresh_elapsed()
        plan = plan_focus_approach(
            FocusApproachRequest(
                plan_id=str(uuid4()),
                prediction=field.prediction,
                target_xy_um=field.target_um,
                stage_snapshot=snapshot,
                frozen_binding=self.frozen_rg.binding,
                current_binding=field.current_binding(),
                policy=self.policy,
                budget=field.budget,
                progress=CorrectionProgress(
                    measurement_iteration=1, total_correction_um=0
                ),
            )
        )
        if plan.status != "planned":
            raise FocusScanSafetyError(
                f"Mapped focus preparation refused: {plan.reason}"
            )
        self.record(
            field, "mapped_approach_planned", {"plan": plan.model_dump(mode="json")}
        )
        with field.motion_guard(
            "mapped_xyz_approach", reserve_s=plan.reserved_motion_time_s
        ):
            field.prepared = self.stage.execute_focus_plan(
                plan, field.current_binding()
            )
        field.budget = field.prepared.budget_after
        field._last_xyz_um = plan.segments[-1].target_xyz_um
        field.stage = "approach_completed"
        self.record(
            field,
            "approach_completed",
            {"prepared": field.prepared.model_dump(mode="json")},
        )

    def _move_segments(
        self,
        field: FocusFieldRuntime,
        segments: tuple[Any, ...],
        name: str,
        *,
        reserve_s: float | None = None,
    ) -> None:
        for index, segment in enumerate(segments):
            effect_reserve = (
                field.budget.post_move_verification_reserve_s
                if reserve_s is None
                else reserve_s
            )
            with field.motion_guard(f"{name}_{index + 1}", reserve_s=effect_reserve):
                self.stage.move_absolute_in_segments(segment.target_stage_units)
            field.sync_after_effect(f"{name}_{index + 1}")

    def _prepare_white(  # noqa: C901, PLR0915
        self, field: FocusFieldRuntime, snapshot: Any
    ) -> None:
        white = self.settings.run.white_search
        if white is None or field.focus_progress is None:
            raise FocusScanSafetyError("WHITE fallback lacks frozen settings/progress")
        field._refresh_elapsed()
        plan = plan_white_sweep(
            WhiteSweepRequest(
                plan_id=str(uuid4()),
                prediction=field.prediction,
                target_xy_um=field.target_um,
                centre_z_um=snapshot.current_quantized_xyz_um[2],
                stage_snapshot=snapshot,
                frozen_binding=self.frozen_rg.binding,
                current_binding=field.current_binding(),
                policy=self.policy,
                budget=field.budget,
                settings=white,
            )
        )
        if plan.status != "planned" or plan.native_total_dz_stage_units is None:
            raise FocusScanSafetyError(f"WHITE transfer refused: {plan.reason}")
        self.record(field, "white_path_planned", {"plan": plan.model_dump(mode="json")})
        field.before_effect(
            "white_transfer_start", reserve_s=plan.worst_case_motion_time_s
        )
        self._move_segments(field, plan.target_transit_segments, "safe_xy_transit")
        field.before_effect(
            "fresh_white_tissue_check",
            reserve_s=(white.white_search_timeout_s + white.approach_and_rg_reserve_s),
        )
        tissue = self.rg_focus.prepare_tissue_field_for_scan(
            self.scan_id, field.field_id, self.frozen_rg.measurement_policy
        )
        field.sync_after_effect("fresh_white_tissue_check")
        status = tissue["status"]
        self.record(field, "tissue_checked", tissue)
        if status == "no_tissue":
            field.no_tissue = True
            field.stage = "no_tissue"
            field.focus_deadline_monotonic_s = None
            self.record(
                field, "no_tissue", {"policy": self.settings.run.no_tissue_mode}
            )
            return
        if status != "ready":
            raise FocusScanSafetyError(
                f"Tissue check is uncertain: {status}: {tissue['reason']}"
            )
        white_started = time.monotonic()
        field.white_search_deadline_monotonic_s = (
            white_started + white.white_search_timeout_s
        )
        self._move_segments(
            field,
            plan.precentre_segments,
            "white_precentre",
            reserve_s=(white.white_search_timeout_s + white.approach_and_rg_reserve_s),
        )

        def white_boundary(phase: str) -> None:
            field.before_effect(phase, reserve_s=white.approach_and_rg_reserve_s)

        def white_confirmed(phase: str) -> None:
            field.sync_after_effect(phase)
            if time.monotonic() - white_started > white.white_search_timeout_s:
                raise FocusScanSafetyError(
                    "Confirmed WHITE move exhausted the WHITE search timeout"
                )

        white_result_id = str(uuid4())
        try:
            with self.stage.motion_pre_post_check(
                lambda: field._check_effect_boundary(
                    "native_white_move",
                    reserve_s=white.approach_and_rg_reserve_s,
                )
            ):
                focus_data = self.autofocus.fast_autofocus_bounded(
                    dz=plan.native_total_dz_stage_units,
                    start="base",
                    before_move=white_boundary,
                    after_move=white_confirmed,
                )
        except BaseException:
            field.focus_progress = field.focus_progress.model_copy(
                update={
                    "white_status": "unknown",
                    "recorded_monotonic_s": time.monotonic(),
                }
            )
            try:
                self.record(field, "white_unknown", {})
            except BaseException:
                pass
            raise
        peak_snapshot = self.stage.focus_motion_snapshot(self.experiment_envelope)
        field._check_effect_boundary(
            "white_peak_readback", reserve_s=white.approach_and_rg_reserve_s
        )
        field.white_focus_z_um = peak_snapshot.current_quantized_xyz_um[2]
        field.white_search_deadline_monotonic_s = None
        field.white_result_id = white_result_id
        field.focus_progress = field.focus_progress.model_copy(
            update={
                "stage": "white_completed",
                "white_status": "succeeded",
                "white_result_id": white_result_id,
                "recorded_monotonic_s": time.monotonic(),
            }
        )
        self.record(
            field,
            "white_completed",
            {
                "white_result_id": white_result_id,
                "focus_z_um": field.white_focus_z_um,
                "sharpness": focus_data.model_dump(mode="json"),
            },
        )
        field._refresh_elapsed()
        handoff = plan_white_peak_approach(
            WhitePeakApproachRequest(
                plan_id=str(uuid4()),
                sweep_plan=plan,
                actual_peak_z_stage_units=peak_snapshot.current_stage_units[2],
                stage_snapshot=peak_snapshot,
                frozen_binding=self.frozen_rg.binding,
                current_binding=field.current_binding(),
                policy=self.policy,
                budget=field.budget,
                progress=CorrectionProgress(
                    measurement_iteration=1, total_correction_um=0
                ),
                current_monotonic_s=time.monotonic(),
            )
        )
        if handoff.status != "planned":
            raise FocusScanSafetyError(f"WHITE peak hand-off refused: {handoff.reason}")
        self.record(
            field,
            "white_handoff_planned",
            {"plan": handoff.model_dump(mode="json")},
        )
        with field.motion_guard(
            "white_led_full_approach", reserve_s=handoff.reserved_motion_time_s
        ):
            field.prepared = self.stage.execute_focus_plan(
                handoff, field.current_binding()
            )
        field.budget = field.prepared.budget_after
        field._last_xyz_um = handoff.segments[-1].target_xyz_um
        field.stage = "approach_completed"
        self.record(
            field,
            "approach_completed",
            {
                "source": "white_peak_handoff",
                "prepared": field.prepared.model_dump(mode="json"),
            },
        )

    def plan_correction(
        self, field: FocusFieldRuntime, inferred_error_um: float
    ) -> PreparedApproach:
        """Plan every enabled correction through the accepted stage executor."""
        field._refresh_elapsed()
        if field.prepared is None:
            snapshot = self.stage.focus_motion_snapshot(self.experiment_envelope)
            initial = plan_initial_focus_correction(
                FocusInitialCorrectionRequest(
                    plan_id=str(uuid4()),
                    inferred_error_um=inferred_error_um,
                    stage_snapshot=snapshot,
                    frozen_binding=self.frozen_rg.binding,
                    current_binding=field.current_binding(),
                    policy=self.policy,
                    budget=field.budget,
                    progress=CorrectionProgress(
                        measurement_iteration=1, total_correction_um=0
                    ),
                )
            )
            if initial.status != "planned":
                raise FocusScanSafetyError(
                    f"Initial focus correction refused: {initial.reason}"
                )
            self.record(
                field,
                "correction_planned",
                {"plan": initial.model_dump(mode="json")},
            )
            with field.motion_guard(
                "initial_focus_correction",
                reserve_s=initial.reserved_motion_time_s,
            ):
                prepared = self.stage.execute_focus_plan(
                    initial, field.current_binding()
                )
            field.last_correction_z_path_units = tuple(
                segment.target_stage_units[2] for segment in initial.segments
            )
            field.prepared = prepared
            field.budget = prepared.budget_after
            field._last_xyz_um = initial.segments[-1].target_xyz_um
            field.stage = "approach_completed"
            self.record(
                field,
                "correction_completed",
                {"prepared": prepared.model_dump(mode="json")},
            )
            return prepared
        snapshot = self.stage.focus_motion_snapshot(self.experiment_envelope)
        plan = plan_focus_correction(
            FocusCorrectionRequest(
                plan_id=str(uuid4()),
                inferred_error_um=inferred_error_um,
                stage_snapshot=snapshot,
                current_binding=field.current_binding(),
                prepared=field.prepared,
                budget=field.budget,
                progress=field.prepared.progress,
            )
        )
        if plan.status == "focused":
            return field.prepared
        if plan.status != "planned":
            raise FocusScanSafetyError(f"Focus correction refused: {plan.reason}")
        self.record(field, "correction_planned", {"plan": plan.model_dump(mode="json")})
        with field.motion_guard(
            "focus_correction", reserve_s=plan.reserved_motion_time_s
        ):
            prepared = self.stage.execute_focus_plan(plan, field.current_binding())
        field.last_correction_z_path_units = tuple(
            segment.target_stage_units[2] for segment in plan.segments
        )
        field.prepared = prepared
        field.budget = prepared.budget_after
        field._last_xyz_um = plan.segments[-1].target_xyz_um
        field.stage = "approach_completed"
        self.record(
            field,
            "correction_completed",
            {"prepared": prepared.model_dump(mode="json")},
        )
        return prepared

    def validate_rg_result(  # noqa: C901, PLR0912, PLR0915
        self, field: FocusFieldRuntime, result: Mapping[str, Any]
    ) -> _ValidatedRGResult:
        """Validate R/G evidence and close its deadline without training the map."""
        if (
            field is not self.active_field
            or field.stage in ("rg_verified", "focused")
            or field._validated_rg_result is not None
        ):
            raise FocusScanSafetyError("R/G result repeats or targets another field")
        iterations = result.get("iterations")
        if not isinstance(iterations, list) or not iterations:
            raise FocusScanSafetyError("R/G result has no final verification pair")
        final = iterations[-1]
        # The R/G owner persists JSON-mode evidence, so tuple fields arrive here
        # as arrays even though the in-memory strict contract uses tuples.
        measurement = RGFocusMeasurement.model_validate(
            final.get("measurement"), strict=False
        )
        decision = final.get("decision")
        if not isinstance(decision, Mapping):
            raise FocusScanSafetyError("R/G result omitted its final decision")
        if result.get("status") != "focused":
            raise FocusScanSafetyError("R/G result is not focused")
        result_mode = result.get("verification_mode")
        decision_status = decision.get("status")
        modelled_one_pair = (
            result_mode == "single_pair_model" and decision_status == "move"
        )
        measured_at_final = decision_status == "focused"
        if not measured_at_final and not modelled_one_pair:
            raise FocusScanSafetyError(
                "R/G result has neither final measurement nor bounded one-pair correction"
            )
        frame_ids = tuple(final.get("frame_ids", ()))
        if len(frame_ids) != 3 or len(set(frame_ids)) != 3:
            raise FocusScanSafetyError(
                "Final WHITE/R/G verification lacks distinct frame identities"
            )
        current_binding = field.current_binding()
        snapshot = self.stage.focus_motion_snapshot(self.experiment_envelope)
        xyz = snapshot.current_quantized_xyz_um
        inferred_error = decision.get("inferred_z_error_um")
        if (
            not isinstance(inferred_error, (int, float))
            or isinstance(inferred_error, bool)
            or not math.isfinite(float(inferred_error))
        ):
            raise FocusScanSafetyError("Final R/G decision omitted its error")
        final_error = (
            result.get("final_estimated_error_um")
            if modelled_one_pair
            else inferred_error
        )
        if (
            not isinstance(final_error, (int, float))
            or isinstance(final_error, bool)
            or not math.isfinite(float(final_error))
        ):
            raise FocusScanSafetyError("R/G result omitted its final estimated error")
        if abs(float(final_error)) > self.policy.focus_tolerance_um:
            raise FocusScanSafetyError(
                "R/G result exceeds the frozen residual focus tolerance"
            )
        result_id = result.get("id")
        report_ref = result.get("report_ref")
        if not isinstance(result_id, str) or not result_id:
            raise FocusScanSafetyError("R/G result omitted its identity")
        if not isinstance(report_ref, str) or not report_ref:
            raise FocusScanSafetyError("R/G result omitted its report reference")
        if modelled_one_pair:
            if result.get("independent_post_move_verification") is not False:
                raise FocusScanSafetyError(
                    "One-pair result misstates independent post-move verification"
                )
            target_units = tuple(final.get("target_units", ()))
            if (
                len(target_units) != 3
                or target_units != snapshot.current_stage_units
                or result.get("focus_z_units") != snapshot.current_stage_units[2]
            ):
                raise FocusScanSafetyError(
                    "One-pair correction target does not match the final stage readback"
                )
            total_correction = result.get("total_correction_um")
            if (
                not isinstance(total_correction, (int, float))
                or isinstance(total_correction, bool)
                or not math.isfinite(float(total_correction))
                or total_correction <= 0
            ):
                raise FocusScanSafetyError(
                    "One-pair result omitted its completed correction"
                )
        if field.white_focus_z_um is not None:
            disagreement = abs(xyz[2] - field.white_focus_z_um)
            white = self.settings.run.white_search
            if white is None or disagreement > white.maximum_white_led_disagreement_um:
                raise FocusScanSafetyError(
                    "Final LED focus disagrees with the frozen WHITE gate"
                )
        prediction_evidence = None
        if field.prediction.status == "usable":
            diagnostics = field.prediction.diagnostics
            if (
                field.prediction.target_z_um is None
                or diagnostics is None
                or diagnostics.support_age_s is None
            ):
                raise FocusScanSafetyError(
                    "Usable prediction omitted pre-update evidence"
                )
            prediction_evidence = FocusPredictionEvidence(
                prediction_id=field.prediction.prediction_id,
                predicted_z_um=field.prediction.target_z_um,
                prediction_error_um=xyz[2] - field.prediction.target_z_um,
                support_age_s=diagnostics.support_age_s,
            )
        observation = FocusObservation(
            scan_id=self.scan_id,
            field_id=field.field_id,
            attempt_id=field.attempt_id,
            observation_id=str(uuid4()),
            x_um=xyz[0],
            y_um=xyz[1],
            z_um=xyz[2],
            final_readback_settled=True,
            final_readback_monotonic_s=time.monotonic(),
            recorded_monotonic_s=time.monotonic(),
            source="led_autofocus",
            status="confirmed" if measured_at_final else "candidate",
            binding=current_binding,
            provenance=FocusObservationProvenance(
                result_id=result_id,
                report_ref=report_ref,
                rg_measurement_id=str(final["capture_id"]),
                capture_ids=frame_ids,
            ),
            quality=FocusObservationQuality(
                measurement_status=measurement.status,
                confidence=measurement.confidence,
                accepted_patch_count=measurement.accepted_patch_count,
                inlier_fraction=measurement.inlier_fraction,
                final_error_um=float(final_error),
                focus_tolerance_um=self.policy.focus_tolerance_um,
                independent_post_move_verification=measured_at_final,
            ),
            pre_update_prediction=prediction_evidence,
        )
        if observation.observation_id in self._observation_ids:
            raise FocusScanSafetyError("Observation identity was already archived")
        reserve = field.budget.post_move_verification_reserve_s
        field._check_effect_boundary("rg_result_validate", reserve_s=reserve)
        field.stage = "rg_verified"
        if field.focus_progress is not None:
            field.focus_progress = field.focus_progress.model_copy(
                update={
                    "stage": "rg_verified",
                    "rg_status": "succeeded",
                    "rg_result_id": result_id,
                    "recorded_monotonic_s": time.monotonic(),
                }
            )
        self.record(
            field,
            "rg_validated",
            {
                "rg_result_id": result_id,
                "observation_id": observation.observation_id,
                "capture_ids": list(observation.provenance.capture_ids),
                "final_z_um": observation.z_um,
                "verification_mode": (
                    "measured_at_final" if measured_at_final else "single_pair_model"
                ),
                "surface_update": observation.status == "confirmed",
            },
        )
        # Journal time belongs to focus verification too. Close the focus-specific
        # deadline only after the validation checkpoint itself is safely durable.
        field._check_effect_boundary("rg_result_validate", reserve_s=reserve)
        field.focus_deadline_monotonic_s = None
        field.white_search_deadline_monotonic_s = None

        total_correction = result.get("total_correction_um")
        duration = result.get("duration_s")
        return _ValidatedRGResult(
            observation=observation,
            verification_mode=(
                "measured_at_final" if measured_at_final else "single_pair_model"
            ),
            total_correction_um=(
                float(total_correction)
                if isinstance(total_correction, (int, float))
                else None
            ),
            duration_s=(
                float(duration) if isinstance(duration, (int, float)) else None
            ),
        )

    def accept_rg_result(self, field: FocusFieldRuntime) -> None:
        """Archive and train from the result validated before the separate tile."""
        pending = field._validated_rg_result
        if (
            field is not self.active_field
            or field.stage != "rg_verified"
            or pending is None
        ):
            raise FocusScanSafetyError(
                "R/G result was not validated for this unfinished field"
            )
        observation = pending.observation
        if observation.observation_id in self._observation_ids:
            raise FocusScanSafetyError("Observation identity was already archived")
        try:
            self._write(
                f"observations/{observation.observation_id}.json",
                observation.model_dump(mode="json"),
            )
        except BaseException:
            self._stopped = True
            self.return_to_start_allowed = False
            raise
        self._observation_ids.append(observation.observation_id)
        if observation.status == "confirmed":
            self.observations.append(observation)
        field.stage = "focused"
        self.record(
            field,
            "focused",
            {
                "observation_id": observation.observation_id,
                "rg_result_id": observation.provenance.result_id,
                "capture_ids": list(observation.provenance.capture_ids),
                "verification_mode": pending.verification_mode,
                "surface_update": observation.status == "confirmed",
                "total_correction_um": pending.total_correction_um,
                "duration_s": pending.duration_s,
                "final_z_um": observation.z_um,
            },
        )
        field._validated_rg_result = None

    def finish_field(self, field: FocusFieldRuntime, *, imaged: bool) -> None:
        """Record the separate tile outcome and release the attempt for the next XY."""
        if field is not self.active_field:
            raise FocusScanSafetyError("Cannot finish a non-active focus field")
        if imaged:
            if field._last_xyz_um is None:
                raise FocusScanSafetyError(
                    "Cannot finish a field whose actual stage state is unknown"
                )
            field.stage = "captured"
            self.record(field, "captured", {})
        elif not field.no_tissue:
            raise FocusScanSafetyError("A focused field was not captured")
        self.active_field = None

    def stop(self, field: FocusFieldRuntime | None, exc: BaseException) -> None:
        """Preserve reached state and forbid automatic movement after any enabled failure."""
        if self._stopped:
            return
        self._stopped = True
        self.return_to_start_allowed = False
        if field is None:
            cancelled = isinstance(exc, lt.exceptions.InvocationCancelledError)
            self._runtime_summary = self._runtime_summary.model_copy(
                update={
                    "scan_state": "stopped",
                    "outcome": "cancelled" if cancelled else "failed",
                    "scan_reason": str(exc) or type(exc).__name__,
                    "reason": str(exc) or type(exc).__name__,
                }
            )
            return
        try:
            self.record(
                field,
                "stopped",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "cancelled": isinstance(
                        exc, lt.exceptions.InvocationCancelledError
                    ),
                },
            )
        except BaseException:
            pass

    def complete(self, reason: str = "Focus route completed") -> None:
        """Permit the existing checked return only after a clean completed route."""
        if self.active_field is not None or self._stopped:
            raise FocusScanSafetyError(
                "Focus session cannot complete with unfinished state"
            )
        self.return_to_start_allowed = True
        self._runtime_summary = self._runtime_summary.model_copy(
            update={"scan_state": "completed", "scan_reason": reason}
        )
