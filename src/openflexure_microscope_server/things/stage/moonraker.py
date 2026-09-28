"""A fail-closed OpenFlexure stage adapter for Moonraker/Klipper.

Coordinates are virtual integer units, not Sangaboard motor steps. No connection
operation homes, arms, moves, changes firmware, or claims a reference automatically.
The first deployment is read-only (allow_motion=False).
"""

from __future__ import annotations

import math
import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self, cast
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

import labthings_fastapi as lt

from openflexure_microscope_server.fast_ofm_contracts import (
    AxisName,
    AxisSettings,
    MotionSettings,
    StrictModel,
)
from openflexure_microscope_server.focus.focus_approach import (
    AxisEnvelope,
    AxisMotionProfile,
    FocusFieldBudget,
    FocusMotionPlan,
    PreparedApproach,
    StageMotionSnapshot,
    validate_executable_focus_plan,
)
from openflexure_microscope_server.focus.focus_surface import (
    FocusBinding,
    check_focus_binding,
)

from . import BacklashCompensation, BaseStage
from .moonraker_status import MoonrakerStatusStream


class StageSafetyError(lt.exceptions.InvocationError):
    """The stage cannot safely accept this operation."""


class AxisMotionLimits(StrictModel):
    """Independent operator-controlled travel and single-command limits in mm."""

    travel_limit_enabled: bool
    min_mm: float | None
    max_mm: float | None
    single_move_limit_enabled: bool
    max_move_mm: float = Field(gt=0)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        """Keep finite, ordered bounds even while their enforcement is disabled."""
        if self.min_mm is None or self.max_mm is None:
            if self.travel_limit_enabled:
                raise ValueError("Enabled travel limits require both bounds")
        elif not self.min_mm <= 0 <= self.max_mm or self.min_mm == self.max_mm:
            raise ValueError("Travel range must contain zero and have positive width")
        return self


class StageMotionLimits(StrictModel):
    """An atomic limit update must explicitly specify every axis."""

    x: AxisMotionLimits
    y: AxisMotionLimits
    z: AxisMotionLimits


class AxisMotionDynamics(StrictModel):
    """Operator-controlled automatic motion timing for one axis."""

    speed_mm_s: float = Field(gt=0)
    accel_mm_s2: float = Field(gt=0)
    settle_ms: float = Field(ge=0, le=5000)
    move_timeout_s: float = Field(gt=0, le=120)


class StageMotionDynamics(StrictModel):
    """An atomic dynamics update must explicitly specify every axis."""

    x: AxisMotionDynamics
    y: AxisMotionDynamics
    z: AxisMotionDynamics


# Numerical zero only (one picometre/second), not a configurable movement speed.
# Klipper can report e.g. -3.47e-18 after M400; exact equality rejects a stopped axis.
VELOCITY_ROUNDOFF_MM_S = 1e-9


class ControllerState(BaseModel):
    """A complete controller snapshot, not an encoder measurement."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    pid: int = Field(gt=0)
    eventtime: float
    state: str
    homed_axes: str
    enabled: dict[str, bool]
    gcode_position: tuple[float, float, float]
    machine_position: tuple[float, float, float]
    homing_origin: tuple[float, float, float]
    axis_minimum: tuple[float, float, float]
    axis_maximum: tuple[float, float, float]
    live_velocity: float
    print_time: float
    estimated_print_time: float
    max_velocity: float = Field(gt=0)
    max_accel: float = Field(gt=0)

    @property
    def idle(self) -> bool:
        """Whether reported motion and the planned motion queue are both idle."""
        return (
            abs(self.live_velocity) <= VELOCITY_ROUNDOFF_MM_S
            and self.print_time <= self.estimated_print_time
        )


@dataclass
class Reference:
    """Operator-selected zero, valid until released or controller state is lost.

    Elapsed time alone cannot invalidate zero. It is deliberately not persisted
    across server shutdowns, and controller/position checks still run on every use.
    """

    identifier: str
    origin: tuple[float, float, float]
    expected: ControllerState


@dataclass(frozen=True)
class PreparedScanPreload:
    """Stage-owned evidence that a scan transit ended at its Z preload point."""

    reference_id: str
    motion_revision: int
    target_xyz: tuple[int, int, int]
    preload_xyz: tuple[int, int, int]
    preload_units: int
    approach_sign: int


@dataclass(frozen=True)
class CompletedScanPreload:
    """Stage-owned evidence that the planned final Z approach was completed."""

    reference_id: str
    motion_revision: int
    target_xyz: tuple[int, int, int]
    preload_units: int
    approach_sign: int


@dataclass(frozen=True)
class CheckedMotionCommand:
    """One fully checked absolute G-code segment."""

    target: dict[str, float]
    speed: float
    accel: float


@dataclass(frozen=True)
class CheckedMotionPath:
    """All commands and the final state expected from one atomic controller POST."""

    commands: tuple[CheckedMotionCommand, ...]
    final_state: ControllerState
    timeout_s: float
    settle_s: float


def xyz(values: list[float]) -> tuple[float, float, float]:
    """Take three explicit finite coordinates, without substituting absent fields."""
    return values[0], values[1], values[2]


class MoonrakerStage(BaseStage):
    """Use the native BaseStage movement interface with bounded Moonraker commands."""

    backlash_steps: dict[str, int] = lt.setting(
        default_factory=lambda: {"x": 0, "y": 0, "z": 0}, readonly=True
    )

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        moonraker_url: str = "http://127.0.0.1:7125",
        hardware: dict[str, Any] | None = None,
    ) -> None:
        """Validate configuration without opening hardware or inventing a reference."""
        super().__init__(thing_server_interface)
        parsed = urlparse(moonraker_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("moonraker_url must be an HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("Put the API key in FAST_OFM_MOONRAKER_API_KEY, not a URL")
        self._url = moonraker_url.rstrip("/")
        self._hardware = MotionSettings.model_validate(hardware or {})
        self._client: httpx.Client | None = None
        self._status_stream: MoonrakerStatusStream | None = None
        self._status_generation = 0
        # Private to this synchronous operation and thread, never a cross-move cache.
        self._state_reads: ContextVar[dict[str, ControllerState] | None] = ContextVar(
            "moonraker_operation_state", default=None
        )
        self._motion_pre_post_checks: ContextVar[tuple[Callable[[], None], ...]] = (
            ContextVar("moonraker_motion_pre_post_checks", default=())
        )
        self._reference: Reference | None = None
        # The only live mechanical preparation proof.  It is issued here after
        # verified focus motion and cleared by every later movement/reference loss.
        self._prepared_approach: PreparedApproach | None = None
        # One just-completed atomic scan path may be consumed exactly once by the
        # acquisition routine.  It is never persisted or accepted after any move.
        self._completed_scan_preload: CompletedScanPreload | None = None
        # Confirmed physical commands only.  A new local zero has a new reference
        # identity; it does not pretend that prior motion never happened.
        self._motion_revision = 0
        self._fault = "A fresh operator reference is required"
        # Command/M400 acknowledgement window, excluding readback and settling.
        self.last_motion_interval: tuple[float, float] | None = None
        # Diagnostic only: whether the last completed motion used push or query.
        self.last_motion_readback_source: Literal["none", "push", "query"] = "none"
        self.last_motion_readback_duration_s: float | None = None
        # Invocation-local policy: a manual action must not change automatic moves
        # in another thread or leave the shorter delay behind after an exception.
        self._manual_control: ContextVar[bool] = ContextVar(
            "manual_control", default=False
        )

    def __enter__(self) -> Self:
        """Open command transport and a read-only state subscription; never send G-code."""
        headers = {}
        api_key = os.getenv("FAST_OFM_MOONRAKER_API_KEY")
        if api_key:
            headers["X-Api-Key"] = api_key
        self._client = httpx.Client(
            base_url=self._url,
            timeout=self._hardware.request_timeout_s,
            headers=headers,
            trust_env=False,
        )
        self._status_stream = MoonrakerStatusStream(
            self._url,
            api_key,
            self._hardware.request_timeout_s,
            self._hardware.status_reconnect_interval_s,
        )
        self._status_stream.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP client and discard its non-persistent reference."""
        self._invalidate("Stage connection closed")
        if self._status_stream is not None:
            self._status_stream.close()
            self._status_stream = None
        if self._client is not None:
            self._client.close()

    saved_motion_limits: StageMotionLimits | None = lt.setting(
        default=None, readonly=True
    )
    """Persisted operator overrides; updates go through set_motion_limits only."""

    saved_motion_dynamics: StageMotionDynamics | None = lt.setting(
        default=None, readonly=True
    )
    """Persisted automatic timing; updates go through set_motion_dynamics only."""

    @lt.property
    def motion_limits(self) -> StageMotionLimits:
        """Return effective shared limits, defaulting to the deployment profile."""
        if self.saved_motion_limits is not None:
            return self.saved_motion_limits
        return StageMotionLimits.model_validate(
            {
                axis: {
                    "travel_limit_enabled": spec.min_mm is not None
                    and spec.max_mm is not None,
                    "min_mm": spec.min_mm,
                    "max_mm": spec.max_mm,
                    "single_move_limit_enabled": True,
                    "max_move_mm": spec.max_move_mm,
                }
                for axis, spec in self._hardware.axes.items()
            }
        )

    @lt.property
    def motion_dynamics(self) -> StageMotionDynamics:
        """Return effective automatic dynamics, defaulting to deployment values."""
        if self.saved_motion_dynamics is not None:
            return self.saved_motion_dynamics
        return StageMotionDynamics.model_validate(
            {
                axis: {
                    "speed_mm_s": spec.speed_mm_s,
                    "accel_mm_s2": spec.accel_mm_s2,
                    "settle_ms": spec.settle_ms,
                    "move_timeout_s": spec.move_timeout_s,
                }
                for axis, spec in self._hardware.axes.items()
            }
        )

    def _axis_settings(self, axis: AxisName) -> AxisSettings:
        """Merge immutable hardware geometry with persisted motion timing."""
        dynamics = getattr(self.motion_dynamics, axis)
        return self._hardware.axes[axis].model_copy(update=dynamics.model_dump())

    @lt.property
    def hardware_settings(self) -> dict[str, Any]:
        """Return the effective profile, including all operator overrides."""
        profile = self._hardware.model_dump()
        if self.saved_motion_limits is not None:
            for axis, limits in self.motion_limits.model_dump().items():
                profile["axes"][axis].update(limits)
        if self.saved_motion_dynamics is not None:
            for axis, dynamics in self.motion_dynamics.model_dump().items():
                profile["axes"][axis].update(dynamics)
        return profile

    @lt.action
    def set_motion_dynamics(self, dynamics: StageMotionDynamics) -> StageMotionDynamics:
        """Save automatic timing atomically while idle; never issue a move."""
        dynamics = StageMotionDynamics.model_validate(dynamics)
        with self._hardware_lock:
            if not self._hardware.allow_operator_controls:
                raise StageSafetyError(
                    "Operator controls are disabled in the deployment profile"
                )
            state = self._fetch_state()
            if state.state != "ready" or not state.idle or self.moving:
                raise StageSafetyError("Stop motion before changing dynamics")
            for axis, spec in self._hardware.axes.items():
                if not spec.enabled:
                    continue
                value = getattr(dynamics, axis)
                if value.speed_mm_s > state.max_velocity:
                    raise StageSafetyError(
                        f"Axis {axis} speed exceeds the controller limit"
                    )
                if value.accel_mm_s2 > state.max_accel:
                    raise StageSafetyError(
                        f"Axis {axis} acceleration exceeds the controller limit"
                    )
                required = (
                    spec.max_move_mm / value.speed_mm_s
                    + 2 * value.speed_mm_s / value.accel_mm_s2
                    + self._hardware.request_timeout_s
                )
                if value.move_timeout_s <= required:
                    raise StageSafetyError(
                        f"Axis {axis} timeout is too short for one full move: "
                        f"requires {required:.3f} s"
                    )
            self.saved_motion_dynamics = dynamics
            return self.motion_dynamics

    @lt.action
    def set_motion_limits(
        self, limits: StageMotionLimits, confirm_disable: bool = False
    ) -> StageMotionLimits:
        """Save all limits atomically while idle; never move, arm, or set zero."""
        limits = StageMotionLimits.model_validate(limits)
        with self._hardware_lock:
            if not self._hardware.allow_operator_controls:
                raise StageSafetyError(
                    "Operator controls are disabled in the deployment profile"
                )
            disabling = any(
                spec.enabled
                and (
                    not getattr(limits, axis).travel_limit_enabled
                    or not getattr(limits, axis).single_move_limit_enabled
                )
                for axis, spec in self._hardware.axes.items()
            )
            if disabling and not confirm_disable:
                raise StageSafetyError(
                    "Confirm disabling software limits; controller limits still apply"
                )
            state = self._fetch_state()
            if state.state != "ready" or not state.idle or self.moving:
                raise StageSafetyError("Stop motion before changing limits")
            self._validate_limit_update(limits, state)
            self.saved_motion_limits = limits
            return self.motion_limits

    def _validate_limit_update(
        self, limits: StageMotionLimits, state: ControllerState
    ) -> None:
        """Validate representable steps and avoid excluding the current reference."""
        if self._reference is not None:
            reference = self._validate_reference(state)
            for i, axis in enumerate(self.axis_names):
                spec = self._hardware.axes[cast(AxisName, axis)]
                bound = getattr(limits, axis)
                if spec.enabled and bound.travel_limit_enabled:
                    local = (
                        state.gcode_position[i] - reference.origin[i]
                    ) * spec.direction_sign
                    if bound.min_mm is None or bound.max_mm is None:
                        raise StageSafetyError("Missing travel bounds")
                    if not bound.min_mm <= local <= bound.max_mm:
                        raise StageSafetyError(
                            f"Axis {axis} is outside the proposed travel range"
                        )
        for axis, spec in self._hardware.axes.items():
            bound = getattr(limits, axis)
            if (
                spec.enabled
                and bound.single_move_limit_enabled
                and bound.max_move_mm * spec.units_per_mm < 1
            ):
                raise StageSafetyError(
                    f"Axis {axis} single move is below one stage unit"
                )

    @lt.property
    def capabilities(self) -> dict[str, Any]:
        """Capabilities that do not imply any hardware acceptance has taken place."""
        return {
            "continuous_jog": False,
            "bounded_move": self._hardware.allow_motion,
            "backlash_commissioned": False,
            "position_source": "Klipper commanded position, not an encoder",
            "reference_persisted": False,
            "operator_controls": self._hardware.allow_operator_controls,
        }

    def _invalidate(self, reason: str) -> None:
        self._reference = None
        self._prepared_approach = None
        self._completed_scan_preload = None
        self._fault = reason

    def _get(self, path: str) -> dict[str, Any]:
        if self._client is None:
            raise StageSafetyError("Stage HTTP client is not open")
        response = self._client.get(path)
        response.raise_for_status()
        return response.json()["result"]

    @contextmanager
    def _state_scope(self) -> Iterator[None]:
        """Share one fresh snapshot through nested helpers, not across commands."""
        if self._state_reads.get() is not None:
            yield
            return
        token = self._state_reads.set({})
        try:
            yield
        finally:
            self._state_reads.reset(token)

    def _clear_state_reads(self) -> None:
        reads = self._state_reads.get()
        if reads is not None:
            reads.clear()

    @contextmanager
    def motion_pre_post_check(self, check: Callable[[], None]) -> Iterator[None]:
        """Apply an invocation-local gate immediately before each controller POST.

        Callers may nest a scan-owned deadline with the accepted focus executor's
        own plan budget.  A refusal here is known to precede the command and must
        not invalidate an otherwise valid reference or mechanical preparation.
        """
        current = self._motion_pre_post_checks.get()
        token = self._motion_pre_post_checks.set((*current, check))
        try:
            yield
        finally:
            self._motion_pre_post_checks.reset(token)

    def _check_motion_pre_post(self) -> None:
        """Run every active final command gate after blocking move preflight."""
        for check in self._motion_pre_post_checks.get():
            check()

    def _check_status_connection(self) -> int:
        if self._status_stream is None:
            return 0
        generation, available = self._status_stream.cache.health()
        if generation != self._status_generation:
            self._status_generation = generation
            self._clear_state_reads()
            self._invalidate("Controller connection changed; set a fresh local zero")
        if not available:
            self._invalidate("Controller status subscription unavailable")
            raise StageSafetyError(self._fault)
        return generation

    def subscribed_status(self, controller_url: str) -> dict[str, Any] | None:
        """Share age-bounded raw state with illumination without taking its lock.

        This is read-only display data, never a movement or light-switch preflight.
        A stale/unavailable stream returns no state, not remembered ON/OFF values.
        """
        if controller_url != self._url:
            raise StageSafetyError("Light and stage controller URLs must match")
        if self._status_stream is None:
            return None
        snapshot = self._status_stream.cache.snapshot(self._hardware.status_max_age_s)
        return None if snapshot is None else snapshot[2]

    def record_light_readback(
        self, controller_url: str, result: dict[str, Any]
    ) -> None:
        """Publish verified gate readback without replacing newer stage properties."""
        if controller_url != self._url:
            raise StageSafetyError("Light and stage controller URLs must match")
        if self._status_stream is None:
            return
        snapshot = self._status_stream.cache.snapshot(self._hardware.status_max_age_s)
        if snapshot is not None:
            generation, pid, _ = snapshot
            try:
                self._status_stream.cache.record_query(generation, pid, result)
            except ConnectionError:
                # Readback remains valid as a point-in-time light result. A lost
                # subscription stays unavailable and cannot regain a stage zero.
                pass

    def _read_state(self) -> ControllerState:
        if self._state_reads.get() is not None or self._status_stream is None:
            return self._fetch_state()
        generation = self._check_status_connection()
        snapshot = self._status_stream.cache.snapshot(self._hardware.status_max_age_s)
        if snapshot is None:
            return self._fetch_state()
        epoch, pid, result = snapshot
        try:
            state = self._decode_state(pid, result)
            if epoch != generation or self._check_status_connection() != generation:
                raise StageSafetyError("Controller changed during status read")
            return state
        except Exception as exc:
            self._invalidate("Controller unavailable or incomplete response")
            raise StageSafetyError(self._fault) from exc

    def _fetch_state(self, refresh: bool = False) -> ControllerState:
        try:
            generation = self._check_status_connection()
            if refresh:
                self._clear_state_reads()
            reads = self._state_reads.get()
            if reads is not None and "state" in reads:
                return reads["state"]
            info = self._get("/printer/info")
            result = self._get(
                "/printer/objects/query?toolhead&gcode_move&stepper_enable&webhooks&motion_report"
            )
            state = self._decode_state(info["process_id"], result)
            if self._check_status_connection() != generation:
                raise StageSafetyError("Controller changed during status query")
            if self._status_stream is not None:
                self._status_stream.cache.record_query(generation, state.pid, result)
            if reads is not None:
                reads["state"] = state
            return state
        except Exception as exc:
            self._invalidate("Controller unavailable or incomplete response")
            raise StageSafetyError(self._fault) from exc

    def _fetch_state_with_objects(
        self, extra_objects: tuple[str, ...]
    ) -> tuple[ControllerState, dict[str, Any]]:
        """Fetch one fresh controller snapshot plus trusted extra objects.

        This is an acquisition-only consolidation point: it still performs a
        real post-effect Moonraker query, but controller and light properties
        share that one atomic response instead of two sequential 250 ms polls.
        The result also refreshes the normal operation-local state cache.
        """
        if not extra_objects or any(not value for value in extra_objects):
            raise ValueError("At least one named status object is required")
        try:
            generation = self._check_status_connection()
            self._clear_state_reads()
            info = self._get("/printer/info")
            suffix = "".join(f"&{quote(name, safe='')}" for name in extra_objects)
            result = self._get(
                "/printer/objects/query?toolhead&gcode_move&stepper_enable"
                f"&webhooks&motion_report{suffix}"
            )
            state = self._decode_state(info["process_id"], result)
            if self._check_status_connection() != generation:
                raise StageSafetyError("Controller changed during status query")
            if self._status_stream is not None:
                self._status_stream.cache.record_query(generation, state.pid, result)
            reads = self._state_reads.get()
            if reads is not None:
                reads["state"] = state
            return state, result
        except Exception as exc:
            self._invalidate("Controller unavailable or incomplete response")
            raise StageSafetyError(self._fault) from exc

    @staticmethod
    def _decode_state(pid: int, result: dict[str, Any]) -> ControllerState:
        status = result["status"]
        toolhead = status["toolhead"]
        gcode = status["gcode_move"]
        return ControllerState(
            pid=pid,
            eventtime=result["eventtime"],
            state=status["webhooks"]["state"],
            homed_axes=toolhead["homed_axes"],
            enabled=status["stepper_enable"]["steppers"],
            gcode_position=xyz(gcode["gcode_position"]),
            machine_position=xyz(toolhead["position"]),
            homing_origin=xyz(gcode["homing_origin"]),
            axis_minimum=xyz(toolhead["axis_minimum"]),
            axis_maximum=xyz(toolhead["axis_maximum"]),
            live_velocity=status["motion_report"]["live_velocity"],
            print_time=toolhead["print_time"],
            estimated_print_time=toolhead["estimated_print_time"],
            max_velocity=toolhead["max_velocity"],
            max_accel=toolhead["max_accel"],
        )

    def _ready(self, state: ControllerState) -> None:
        axes = [name for name, spec in self._hardware.axes.items() if spec.enabled]
        if state.state != "ready" or not state.idle:
            raise StageSafetyError("Controller must be ready with an idle motion queue")
        if not all(axis in state.homed_axes for axis in axes):
            raise StageSafetyError("Enabled axes have no current controller reference")
        if not all(state.enabled.get("stepper_" + axis) is True for axis in axes):
            raise StageSafetyError("Enabled-axis motors must already be armed")

    def _validate_reference(self, state: ControllerState) -> Reference:
        reference = self._reference
        if reference is None:
            raise StageSafetyError(self._fault)
        try:
            self._ready(state)
            previous = reference.expected
            if state.pid != previous.pid or state.eventtime < previous.eventtime:
                raise StageSafetyError("Controller restarted; reference invalid")
            if state.homing_origin != previous.homing_origin:
                raise StageSafetyError("Controller origin changed outside Fast OFM")
            if state.print_time != previous.print_time:
                raise StageSafetyError(
                    "External motion detected; release Fluidd control"
                )
            for new, old in zip(
                (*state.gcode_position, *state.machine_position),
                (*previous.gcode_position, *previous.machine_position),
                strict=True,
            ):
                if abs(new - old) > self._hardware.position_tolerance_mm:
                    raise StageSafetyError("External coordinate change detected")
        except StageSafetyError as exc:
            self._invalidate(str(exc))
            raise
        return reference

    def _expected_reference_state(self) -> ControllerState | None:
        """Return an owned no-I/O snapshot last verified by our own operation."""
        with self._hardware_lock:
            reference = self._reference
            return (
                None if reference is None else reference.expected.model_copy(deep=True)
            )

    def _post_light_readback(
        self,
        extra_objects: tuple[str, ...],
        after_check: Callable[[dict[str, Any]], None] | None,
    ) -> ControllerState:
        """Run one fresh post-command read, optionally checking merged objects."""
        if not extra_objects:
            return self._fetch_state(refresh=True)
        after, result = self._fetch_state_with_objects(extra_objects)
        if after_check is not None:
            after_check(result)
        return after

    @contextmanager
    def _light_operation(
        self,
        controller_url: str,
        verified_before: ControllerState | None = None,
        *,
        extra_objects: tuple[str, ...] = (),
        after_check: Callable[[dict[str, Any]], None] | None = None,
    ) -> Iterator[None]:
        """Account for our light-only queue activity without re-establishing zero.

        SET_PIN advances Klipper print_time even without motor motion. Only the
        illumination adapter enters this private scope, under OFM's global lock;
        the stage lock excludes reference polling until the completed readback.
        Outside this scope, external queue activity still invalidates reference.

        A focus exposure may supply its fresh post-camera controller snapshot.
        That skips one duplicate HTTP read but requires an existing reference;
        the same strict validation and fresh completed readback still apply.
        """
        if controller_url != self._url:
            raise StageSafetyError("Light and stage controller URLs must match")
        if after_check is not None and not extra_objects:
            raise ValueError("A combined post-light check requires status objects")
        with self._hardware_lock:
            before = (
                self._fetch_state()
                if verified_before is None
                else verified_before.model_copy(deep=True)
            )
            if before.state != "ready" or not before.idle:
                raise StageSafetyError("Light switching requires an idle controller")
            reference = self._reference
            if verified_before is not None:
                reference = self._validate_reference(before)
            elif reference is not None:
                try:
                    self._validate_reference(before)
                except StageSafetyError:
                    # Light control must work without zero; never repair a lost one.
                    reference = None
            try:
                yield
                after = self._post_light_readback(extra_objects, after_check)
                unchanged = (
                    "pid",
                    "state",
                    "homed_axes",
                    "enabled",
                    "gcode_position",
                    "machine_position",
                    "homing_origin",
                    "axis_minimum",
                    "axis_maximum",
                    "max_velocity",
                    "max_accel",
                )
                if (
                    not after.idle
                    or after.eventtime < before.eventtime
                    or after.print_time < before.print_time
                    or any(
                        getattr(after, key) != getattr(before, key) for key in unchanged
                    )
                ):
                    raise StageSafetyError("Controller changed during light switching")
                if reference is not None and self._reference is reference:
                    reference.expected = after
            except (Exception, lt.exceptions.InvocationCancelledError):
                self._invalidate(
                    "Light operation outcome uncertain; set a fresh local zero"
                )
                raise

    @lt.property
    def controller_state(self) -> dict[str, Any]:
        """Age-bounded subscribed state and validity of the local reference."""
        with self._hardware_lock:
            state = self._read_state()
            position: dict[str, int] | None = None
            try:
                reference = self._validate_reference(state)
                position = self._position_from_state(state, reference)
            except StageSafetyError:
                pass
            return {
                **state.model_dump(),
                "motion_idle": state.idle,
                "reference_valid": self._reference is not None,
                "reference_id": (
                    None if self._reference is None else self._reference.identifier
                ),
                "motion_revision": self._motion_revision,
                "last_motion_readback_source": self.last_motion_readback_source,
                "last_motion_readback_duration_s": self.last_motion_readback_duration_s,
                "fault": self._fault,
                "motion_allowed": self._hardware.allow_motion,
                "position_source": "commanded_not_encoder",
                "position": position,
            }

    @lt.property
    def position(self) -> Mapping[str, int]:
        """Referenced position; unavailable positions must never become fabricated zeros."""
        with self._hardware_lock:
            self._hardware_update_position()
            return super().position

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Record unreferenced state honestly while still allowing camera captures."""
        try:
            return self.controller_state
        except StageSafetyError as exc:
            return {"reference_valid": False, "position": None, "fault": str(exc)}

    @lt.action
    def enable_motors(self, exclusive_control: bool = False) -> None:
        """Energise XYZ without moving; a separate confirmed zero is still required."""
        with self._hardware_lock:
            before = self._operator_ready(exclusive_control)
            script = "\n".join(
                f"SET_STEPPER_ENABLE STEPPER=stepper_{axis} ENABLE=1"
                for axis in self.axis_names
            )
            after = self._operator_command(script, before)
            if not all(
                after.enabled.get("stepper_" + axis) is True for axis in self.axis_names
            ):
                raise StageSafetyError(
                    "Motor enable was not confirmed; inspect controller"
                )

    @lt.action
    def disable_motors(self, confirmed_release: bool = False) -> None:
        """Release XYZ only when idle; this is not an emergency stop."""
        with self._hardware_lock:
            before = self._operator_ready(confirmed_release)
            after = self._operator_command("M84", before)
            if not all(
                after.enabled.get("stepper_" + axis) is False
                for axis in self.axis_names
            ):
                raise StageSafetyError(
                    "Motor disable was not confirmed; inspect controller"
                )

    def _operator_ready(self, confirmed: bool) -> ControllerState:
        if not self._hardware.allow_operator_controls:
            raise StageSafetyError(
                "Operator controls are disabled in the deployment profile"
            )
        if not confirmed:
            raise StageSafetyError(
                "Confirm safe operator control before changing the stage"
            )
        state = self._fetch_state()
        if state.state != "ready" or not state.idle:
            raise StageSafetyError("Controller must be ready with an idle motion queue")
        if any("stepper_" + axis not in state.enabled for axis in self.axis_names):
            raise StageSafetyError("Missing XYZ motor state")
        return state

    def _operator_command(
        self, script: str, before: ControllerState, changes_coordinates: bool = False
    ) -> ControllerState:
        # These are explicit button actions, never startup/reconnect side effects.
        self._invalidate("Motor/reference state changed; set a fresh local zero")
        try:
            if self._client is None:
                raise StageSafetyError("Stage HTTP client is not open")
            response = self._client.post(
                "/printer/gcode/script",
                json={"script": script + "\nM400"},
                timeout=self._hardware.request_timeout_s,
            )
            response.raise_for_status()
            if response.json().get("result") != "ok":
                raise StageSafetyError("Controller did not acknowledge the operation")
            after = self._fetch_state(refresh=True)
            if (
                after.pid != before.pid
                or after.eventtime < before.eventtime
                or after.state != "ready"
                or not after.idle
                or after.homing_origin != before.homing_origin
            ):
                raise StageSafetyError("Controller changed during operator action")
            if not changes_coordinates and (
                after.gcode_position != before.gcode_position
                or after.machine_position != before.machine_position
            ):
                raise StageSafetyError(
                    "Unexpected position change during motor operation"
                )
            return after
        except Exception as exc:
            self._invalidate(
                "Operator action outcome uncertain; inspect controller, do not retry"
            )
            raise StageSafetyError(self._fault) from exc

    @lt.action
    def set_zero_position(
        self,
        confirmed_manual_zero: bool = False,
        exclusive_control: bool = False,
        initialise_controller: bool = False,
    ) -> None:
        """Set current position as local zero; never move or arm motors.

        Both confirmations are required. Other clients (including Fluidd) must not
        move the stage during this session; the HTTP API offers no exclusive lease.
        """
        with self._hardware_lock, self._state_scope():
            if not self._hardware.allow_motion:
                raise StageSafetyError("Motion is disabled in the deployment profile")
            if not confirmed_manual_zero or not exclusive_control:
                raise StageSafetyError(
                    "Confirm manual zero and exclusive stage control"
                )
            state = self._fetch_state()
            if initialise_controller:
                state = self._operator_ready(exclusive_control)
                if not all(
                    state.enabled.get("stepper_" + axis) is True
                    for axis in self.axis_names
                ):
                    raise StageSafetyError(
                        "Enable motors before setting the local zero"
                    )
                axes = [
                    axis for axis, spec in self._hardware.axes.items() if spec.enabled
                ]
                words = " ".join(f"{axis.upper()}=0" for axis in axes)
                after = self._operator_command(
                    f"SET_KINEMATIC_POSITION {words} SET_HOMED={''.join(axes)}",
                    state,
                    changes_coordinates=True,
                )
                for i, axis in enumerate(self.axis_names):
                    expected = 0.0 if axis in axes else state.machine_position[i]
                    if (
                        abs(after.machine_position[i] - expected)
                        > self._hardware.position_tolerance_mm
                    ):
                        raise StageSafetyError("Controller zero was not confirmed")
                state = after
            self._ready(state)
            self._prepared_approach = None
            self._reference = Reference(
                identifier=str(uuid.uuid4()),
                origin=state.gcode_position,
                expected=state,
            )
            self._fault = ""
            self._hardware_update_position()

    @lt.action
    def release_control(self) -> None:
        """Discard the reference; do not issue a stop, motor disable or physical move."""
        with self._hardware_lock:
            self._invalidate("Operator released stage control")

    @lt.action
    def invert_axis_direction(self, axis: Literal["x", "y", "z"]) -> None:
        """Require a new deployment profile/reference for axis-direction changes."""
        raise StageSafetyError(
            f"Change {axis} direction_sign in hardware settings, then re-reference"
        )

    def _hardware_update_position(self) -> None:
        state = self._read_state()
        reference = self._validate_reference(state)
        self._hardware_position = self._position_from_state(state, reference)

    def _position_from_state(
        self, state: ControllerState, reference: Reference
    ) -> dict[str, int]:
        """Convert one already-verified controller snapshot to local stage units."""
        return {
            axis: round(
                (state.gcode_position[i] - reference.origin[i])
                * self._hardware.axes[cast(AxisName, axis)].units_per_mm
                * self._hardware.axes[cast(AxisName, axis)].direction_sign
            )
            for i, axis in enumerate(self.axis_names)
        }

    def _axis_target(
        self,
        state: ControllerState,
        reference: Reference,
        axis: str,
        displacement: float,
        enforce_single_limit: bool = True,
    ) -> float:
        i = self.axis_names.index(axis)
        spec = self._hardware.axes[cast(AxisName, axis)]
        if not spec.enabled:
            raise StageSafetyError(f"Axis {axis} is disabled")
        local_mm = (state.gcode_position[i] - reference.origin[i]) * spec.direction_sign
        final_mm = local_mm + displacement
        limits = getattr(self.motion_limits, axis)
        if limits.travel_limit_enabled:
            if limits.min_mm is None or limits.max_mm is None:
                raise StageSafetyError("Missing motion bounds")
            if (
                not limits.min_mm <= local_mm <= limits.max_mm
                or not limits.min_mm <= final_mm <= limits.max_mm
            ):
                raise StageSafetyError(f"Axis {axis} would exceed the local envelope")
        if (
            enforce_single_limit
            and limits.single_move_limit_enabled
            and abs(displacement) > limits.max_move_mm
        ):
            raise StageSafetyError(
                f"Axis {axis} exceeds maximum single move: requested "
                f"{displacement:.6f} mm, limit {limits.max_move_mm:.6f} mm"
            )
        machine_target = state.machine_position[i] + displacement * spec.direction_sign
        if not state.axis_minimum[i] <= machine_target <= state.axis_maximum[i]:
            raise StageSafetyError(f"Axis {axis} would exceed controller limits")
        return state.gcode_position[i] + displacement * spec.direction_sign

    def _check_target(
        self, state: ControllerState, reference: Reference, delta: Mapping[str, int]
    ) -> tuple[dict[str, float], float, float, float, float]:
        if not self._hardware.allow_motion:
            raise StageSafetyError("Motion is disabled in the deployment profile")
        if any(self.backlash_steps.values()) or any(self.axis_inverted.values()):
            raise StageSafetyError(
                "Uncommissioned backlash or direction override; no move sent"
            )
        if set(delta) - set(self.axis_names):
            raise StageSafetyError("Only X, Y and Z are supported")
        target = {}
        specs = []
        for axis in self.axis_names:
            spec = self._axis_settings(cast(AxisName, axis))
            displacement = delta.get(axis, 0) / spec.units_per_mm
            if displacement == 0:
                continue
            target[axis] = self._axis_target(state, reference, axis, displacement)
            specs.append(spec)
        if not specs:
            return {}, 0, 0, 0, 0
        components = [
            abs(target[axis] - state.gcode_position[self.axis_names.index(axis)])
            for axis in target
        ]
        distance = math.hypot(*components)
        if any(component == 0 for component in components):
            raise StageSafetyError("Displacement is below coordinate precision")
        # Klipper's feed/acceleration apply along the straight path, while our
        # profile bounds each axis. Axis i travels component_i / distance of it.
        speed = min(
            state.max_velocity,
            *(
                spec.speed_mm_s * (distance / component)
                for spec, component in zip(specs, components, strict=True)
            ),
        )
        accel = min(
            state.max_accel,
            *(
                spec.accel_mm_s2 * (distance / component)
                for spec, component in zip(specs, components, strict=True)
            ),
        )
        # _execute_move formats six decimals. Round down so serialization cannot
        # increase an axis limit; use these same effective values for the budget.
        speed = math.floor(speed * 1_000_000) / 1_000_000
        accel = math.floor(accel * 1_000_000) / 1_000_000
        if speed <= 0 or accel <= 0:
            raise StageSafetyError("Motion dynamics are below G-code precision")
        settle = (
            self._hardware.manual_settle_ms
            if self._manual_control.get()
            else max(spec.settle_ms for spec in specs)
        ) / 1000
        timeout = min(spec.move_timeout_s for spec in specs)
        required = (
            distance / speed + 2 * speed / accel + self._hardware.request_timeout_s
        )
        if timeout <= required:
            raise StageSafetyError(
                "Configured move timeout is too short for the trajectory: "
                f"requires {required:.3f} s, configured {timeout:.3f} s"
            )
        return target, speed, accel, settle, timeout

    def _execute_move(
        self,
        state: ControllerState,
        target: dict[str, float],
        speed: float,
        accel: float,
        timeout: float,
    ) -> None:
        if self._client is None:
            raise StageSafetyError("Stage HTTP client is not open")
        words = " ".join(f"{axis.upper()}{value:.6f}" for axis, value in target.items())
        script = "\n".join(
            [
                "SAVE_GCODE_STATE NAME=FAST_OFM_OPENFLEXURE",
                "G90",
                "M220 S100",
                f"SET_VELOCITY_LIMIT VELOCITY={speed:.6f} ACCEL={accel:.6f}",
                f"G1 {words} F{speed * 60:.6f}",
                "M400",
                "RESTORE_GCODE_STATE NAME=FAST_OFM_OPENFLEXURE",
                f"SET_VELOCITY_LIMIT VELOCITY={state.max_velocity:.6f} ACCEL={state.max_accel:.6f}",
            ]
        )
        # One POST only. A timeout is ambiguous and must NEVER result in a retry.
        started_at = time.time()
        response = self._client.post(
            "/printer/gcode/script", json={"script": script}, timeout=timeout
        )
        acknowledged_at = time.time()
        response.raise_for_status()
        if response.json().get("result") != "ok":
            raise StageSafetyError("Controller did not acknowledge completion")
        self.last_motion_interval = (started_at, acknowledged_at)

    def _post_motion_state(
        self,
        before: ControllerState,
        verify: Callable[[ControllerState], None],
    ) -> ControllerState:
        """Prefer a conclusive post-command push, retaining the fresh-query fallback."""
        started = time.monotonic()
        stream = self._status_stream
        wait_s = self._hardware.post_command_push_wait_s
        if stream is not None and wait_s > 0:
            generation = self._status_generation
            cursor = before.eventtime
            deadline = time.monotonic() + wait_s
            while True:
                snapshot = stream.cache.wait_for_snapshot(
                    generation,
                    cursor,
                    self._hardware.status_max_age_s,
                    max(0.0, deadline - time.monotonic()),
                )
                if snapshot is None:
                    break
                epoch, pid, result = snapshot
                if epoch != generation or self._check_status_connection() != generation:
                    break
                candidate = self._decode_state(pid, result)
                try:
                    verify(candidate)
                except StageSafetyError:
                    cursor = candidate.eventtime
                    continue
                self._clear_state_reads()
                reads = self._state_reads.get()
                if reads is not None:
                    reads["state"] = candidate
                self.last_motion_readback_source = "push"
                self.last_motion_readback_duration_s = time.monotonic() - started
                return candidate
        after = self._fetch_state(refresh=True)
        verify(after)
        self.last_motion_readback_source = "query"
        self.last_motion_readback_duration_s = time.monotonic() - started
        return after

    def _hardware_move_relative(
        self, block_cancellation: bool = False, **kwargs: int
    ) -> None:
        self.last_motion_interval = None
        with self._hardware_lock, self._state_scope():
            state = self._fetch_state()
            reference = self._validate_reference(state)
            target, speed, accel, settle, timeout = self._check_target(
                state, reference, kwargs
            )
            if not target:
                return
            if not block_cancellation:
                lt.cancellable_sleep(0)
            # This is the final known-no-command boundary.  All controller reads,
            # reference checks, target checks and cancellation preflight above may
            # block.  Nothing that can block remains before the single POST.
            self._check_motion_pre_post()
            self._prepared_approach = None
            self._completed_scan_preload = None
            self.moving = True
            self.last_motion_readback_source = "none"
            self.last_motion_readback_duration_s = None
            try:
                self._execute_move(state, target, speed, accel, timeout)

                def verify(after: ControllerState) -> None:
                    self._ready(after)
                    if (
                        after.pid != state.pid
                        or after.eventtime < state.eventtime
                        or after.homing_origin != state.homing_origin
                    ):
                        raise StageSafetyError(
                            "Controller reference changed during move"
                        )
                    for i, axis in enumerate(self.axis_names):
                        expected = target.get(axis, state.gcode_position[i])
                        expected_machine = (
                            state.machine_position[i]
                            + expected
                            - state.gcode_position[i]
                        )
                        if (
                            abs(after.gcode_position[i] - expected)
                            > self._hardware.position_tolerance_mm
                            or abs(after.machine_position[i] - expected_machine)
                            > self._hardware.position_tolerance_mm
                        ):
                            raise StageSafetyError(
                                "Final controller position differs from commanded target"
                            )

                after = self._post_motion_state(state, verify)
                reference.expected = after
                self._hardware_update_position()
                self._motion_revision += 1
                self.moving = False
            except Exception as exc:
                self.last_motion_interval = None
                self._invalidate(
                    "Move outcome uncertain; inspect controller, do not retry"
                )
                # Do not claim that an unacknowledged move has stopped.
                raise StageSafetyError(self._fault) from exc
            try:
                if block_cancellation:
                    time.sleep(settle)
                else:
                    lt.cancellable_sleep(settle)
            finally:
                # Even a zero settle is a command boundary: the next move must
                # freshly detect external motion, origin changes or motor release.
                self._clear_state_reads()

    def _hardware_move_absolute(
        self, block_cancellation: bool = False, **kwargs: int
    ) -> None:
        with self._hardware_lock, self._state_scope():
            self._hardware_update_position()
            self._hardware_move_relative(
                block_cancellation=block_cancellation,
                **{
                    axis: value - self._hardware_position[axis]
                    for axis, value in kwargs.items()
                },
            )

    def _focus_motion_snapshot(
        self,
        experiment_envelope_um: Mapping[str, tuple[float, float]],
        state: ControllerState,
        reference: Reference,
    ) -> StageMotionSnapshot:
        """Build the pure planner input from one validated live readback."""
        if set(experiment_envelope_um) != set(self.axis_names):
            raise StageSafetyError("Focus experiment requires XYZ envelopes")
        profiles = []
        current_um = []
        for index, axis in enumerate(self.axis_names):
            name = cast(AxisName, axis)
            spec = self._axis_settings(name)
            limits = getattr(self.motion_limits, axis)
            if not spec.enabled:
                raise StageSafetyError(f"Focus planning requires enabled axis {axis}")
            if (
                not limits.travel_limit_enabled
                or limits.min_mm is None
                or limits.max_mm is None
                or not limits.single_move_limit_enabled
            ):
                raise StageSafetyError(
                    "Focus planning requires enabled travel and single-move limits"
                )
            local_um = (
                (state.gcode_position[index] - reference.origin[index])
                * spec.direction_sign
                * 1000
            )
            controller_ends = sorted(
                (
                    local_um
                    + (bound - state.machine_position[index])
                    * spec.direction_sign
                    * 1000
                )
                for bound in (
                    state.axis_minimum[index],
                    state.axis_maximum[index],
                )
            )
            experiment = experiment_envelope_um[axis]
            profiles.append(
                AxisMotionProfile(
                    axis=name,
                    units_per_mm=spec.units_per_mm,
                    direction_sign=spec.direction_sign,
                    travel=AxisEnvelope(
                        minimum_um=limits.min_mm * 1000,
                        maximum_um=limits.max_mm * 1000,
                    ),
                    controller=AxisEnvelope(
                        minimum_um=controller_ends[0],
                        maximum_um=controller_ends[1],
                    ),
                    experiment=AxisEnvelope(
                        minimum_um=float(experiment[0]),
                        maximum_um=float(experiment[1]),
                    ),
                    maximum_single_move_um=limits.max_move_mm * 1000,
                    velocity_um_s=spec.speed_mm_s * 1000,
                    acceleration_um_s2=spec.accel_mm_s2 * 1000,
                    move_timeout_s=spec.move_timeout_s,
                    settle_s=spec.settle_ms / 1000,
                )
            )
            current_um.append(local_um)
        return StageMotionSnapshot(
            reference_id=reference.identifier,
            motion_revision=self._motion_revision,
            reference_origin_commanded_mm=reference.origin,
            current_commanded_xyz_um=cast(
                tuple[float, float, float], tuple(current_um)
            ),
            axes=cast(
                tuple[AxisMotionProfile, AxisMotionProfile, AxisMotionProfile],
                tuple(profiles),
            ),
            controller_max_velocity_um_s=state.max_velocity * 1000,
            controller_max_acceleration_um_s2=state.max_accel * 1000,
            request_timeout_s=self._hardware.request_timeout_s,
        )

    def focus_motion_snapshot(
        self, experiment_envelope_um: Mapping[str, tuple[float, float]]
    ) -> StageMotionSnapshot:
        """Return a read-only physical snapshot for the pure focus planner."""
        with self._hardware_lock, self._state_scope():
            state = self._fetch_state()
            reference = self._validate_reference(state)
            self._check_target(state, reference, {})
            return self._focus_motion_snapshot(experiment_envelope_um, state, reference)

    def validate_path(
        self, targets: list[dict[str, int]], relative_to_start: bool = False
    ) -> None:
        """Check all endpoints before any command; travel bounds are convex.

        Relative targets are offsets from the same start, not incremental moves.
        Single-command limits still apply to each segment during execution.
        """
        with self._hardware_lock:
            state = self._fetch_state()
            reference = self._validate_reference(state)
            self._check_target(state, reference, {})
            for target in targets:
                if set(target) - set(self.axis_names):
                    raise StageSafetyError("Only X, Y and Z are supported")
                for axis, value in target.items():
                    if not math.isfinite(value):
                        raise StageSafetyError("Non-finite trajectory coordinate")
                    spec = self._hardware.axes[cast(AxisName, axis)]
                    displacement = value / spec.units_per_mm
                    if not relative_to_start:
                        index = self.axis_names.index(axis)
                        displacement -= (
                            state.gcode_position[index] - reference.origin[index]
                        ) * spec.direction_sign
                    self._axis_target(
                        state, reference, axis, displacement, enforce_single_limit=False
                    )

    def _absolute_segment_targets(
        self,
        start: Mapping[str, int],
        xyz_pos: tuple[int, int, int],
        state: ControllerState,
        reference: Reference,
    ) -> tuple[dict[str, int], ...]:
        """Return the same rounded single-command targets used for execution."""
        segments = 1
        for axis, value in zip(self.axis_names, xyz_pos, strict=True):
            delta = value - start[axis]
            if not delta:
                continue
            spec = self._hardware.axes[cast(AxisName, axis)]
            self._axis_target(
                state,
                reference,
                axis,
                delta / spec.units_per_mm,
                enforce_single_limit=False,
            )
            limits = getattr(self.motion_limits, axis)
            if not limits.single_move_limit_enabled:
                continue
            step_units = math.floor(limits.max_move_mm * spec.units_per_mm)
            if step_units < 1:
                raise StageSafetyError("Single move limit is below one stage unit")
            segments = max(segments, math.ceil(abs(delta) / step_units))
        return tuple(
            {
                axis: start[axis] + round((value - start[axis]) * index / segments)
                for axis, value in zip(self.axis_names, xyz_pos, strict=True)
            }
            for index in range(1, segments + 1)
        )

    def _preflight_absolute_sequence(
        self,
        xyz_targets: tuple[tuple[int, int, int], ...],
        state: ControllerState,
        reference: Reference,
        start: Mapping[str, int],
    ) -> tuple[dict[str, int], ...]:
        """Validate every rounded segment and its dynamics before the first POST."""
        self._check_target(state, reference, {})
        planned_state = state
        planned_position = dict(start)
        all_targets: list[dict[str, int]] = []
        for xyz_pos in xyz_targets:
            targets = self._absolute_segment_targets(
                planned_position, xyz_pos, planned_state, reference
            )
            for target in targets:
                segment_delta = {
                    axis: target[axis] - planned_position[axis]
                    for axis in self.axis_names
                }
                checked, *_ = self._check_target(
                    planned_state, reference, segment_delta
                )
                next_gcode = tuple(
                    checked.get(axis, planned_state.gcode_position[index])
                    for index, axis in enumerate(self.axis_names)
                )
                next_machine = tuple(
                    planned_state.machine_position[index]
                    + next_gcode[index]
                    - planned_state.gcode_position[index]
                    for index in range(3)
                )
                planned_state = planned_state.model_copy(
                    update={
                        "gcode_position": next_gcode,
                        "machine_position": next_machine,
                    }
                )
                planned_position = target
                all_targets.append(target)
        return tuple(all_targets)

    def move_absolute_in_segments(
        self, xyz_pos: tuple[int, int, int], block_cancellation: bool = False
    ) -> None:
        """Execute a checked transit as bounded commands, never an automatic retry.

        This Python-only helper does not change the manual action limit. Validate
        the whole straight path before its first segment; every segment still
        performs the normal reference, M400, idle, position and settling checks.
        """
        with self._hardware_lock, self._state_scope():
            self._hardware_update_position()
            start = dict(self._hardware_position)
            state = self._fetch_state()
            reference = self._validate_reference(state)
            targets = self._preflight_absolute_sequence(
                (xyz_pos,), state, reference, start
            )
            # Each actual move still verifies fresh controller/reference state.
            for target in targets:
                self.move_absolute(
                    **target,
                    block_cancellation=block_cancellation,
                    backlash_compensation=None,
                )

    def _focus_execution_targets(
        self,
        plan: FocusMotionPlan,
        current_binding: FocusBinding,
        state: ControllerState,
        reference: Reference,
    ) -> tuple[tuple[tuple[int, int, int], ...], tuple[float, ...]]:
        """Recheck live identity and preflight the exact canonical segmentation."""
        experiment: dict[str, tuple[float, float]] = {
            axis.axis: (axis.experiment.minimum_um, axis.experiment.maximum_um)
            for axis in plan.stage_snapshot.axes
        }
        live = self._focus_motion_snapshot(experiment, state, reference)
        if live != plan.stage_snapshot:
            raise StageSafetyError(
                "Focus plan no longer matches live reference, history or profile"
            )
        binding_check = check_focus_binding(plan.binding, current_binding)
        if not binding_check.compatible or not binding_check.usable:
            raise StageSafetyError("Focus binding changed before execution")
        if plan.purpose in ("direct_correction", "reapproach_correction") and (
            plan.required_preparation is None
            or self._prepared_approach != plan.required_preparation
        ):
            raise StageSafetyError(
                "Correction requires the matching stage-issued preparation"
            )
        targets = tuple(segment.target_stage_units for segment in plan.segments)
        start = dict(
            zip(self.axis_names, plan.stage_snapshot.current_stage_units, strict=True)
        )
        preflight = self._preflight_absolute_sequence(targets, state, reference, start)
        preflight_units = tuple(
            cast(
                tuple[int, int, int],
                tuple(target[axis] for axis in self.axis_names),
            )
            for target in preflight
        )
        if preflight_units != targets:
            raise StageSafetyError(
                "Focus plan does not match live single-command segmentation"
            )
        z_deltas = []
        previous_z = plan.stage_snapshot.current_quantized_xyz_um[2]
        for segment in plan.segments:
            z_deltas.append(abs(segment.target_xyz_um[2] - previous_z))
            previous_z = segment.target_xyz_um[2]
        return targets, tuple(z_deltas)

    @staticmethod
    def _focus_runtime_elapsed_s(
        budget: FocusFieldBudget,
        execution_started: float,
        elapsed_at_start: float,
    ) -> float:
        now = time.monotonic()
        return max(
            budget.elapsed_at(now),
            elapsed_at_start + max(0.0, now - execution_started),
        )

    def _check_focus_runtime_budget(
        self,
        *,
        plan: FocusMotionPlan,
        execution_started: float,
        elapsed_at_start: float,
        z_deltas: tuple[float, ...],
        next_segment: int,
        used_z_um: float,
    ) -> float:
        """Require enough live time/travel for all unsent motion and its reserve."""
        budget = plan.budget_before
        elapsed_s = self._focus_runtime_elapsed_s(
            budget, execution_started, elapsed_at_start
        )
        remaining_time_s = sum(
            segment.estimated_duration_s for segment in plan.segments[next_segment:]
        )
        if (
            elapsed_s + remaining_time_s + plan.reserved_motion_time_s
            > budget.maximum_elapsed_s
        ):
            raise StageSafetyError(
                "Focus field time budget cannot cover the remaining path"
            )
        if (
            budget.z_travel_um
            + used_z_um
            + sum(z_deltas[next_segment:])
            + plan.reserved_z_travel_um
            > budget.maximum_z_travel_um
        ):
            raise StageSafetyError(
                "Focus field Z-travel budget cannot cover the remaining path"
            )
        return elapsed_s

    def execute_focus_plan(
        self, plan: FocusMotionPlan, current_binding: FocusBinding
    ) -> PreparedApproach:
        """Execute one pure focus plan and return stage-owned preparation evidence.

        A plan is only arithmetic.  This boundary rechecks the live reference,
        motion history, commanded XYZ, axis profiles, all late segment dynamics,
        and every normal M400/idle/readback/settle condition.  No public boolean
        can substitute for the returned ``PreparedApproach``.
        """
        execution_started = time.monotonic()
        checked_plan = FocusMotionPlan.model_validate(plan.model_dump())
        checked_binding = FocusBinding.model_validate(current_binding)
        if checked_plan.status != "planned":
            raise StageSafetyError("Only a planned focus motion can be executed")
        try:
            validate_executable_focus_plan(checked_plan)
        except ValueError as exc:
            raise StageSafetyError(f"Invalid focus plan semantics: {exc}") from exc
        with self._hardware_lock, self._state_scope():
            state = self._fetch_state()
            reference = self._validate_reference(state)
            # This catches semantic or unsafe late parts before an early POST.
            targets, z_deltas = self._focus_execution_targets(
                checked_plan, checked_binding, state, reference
            )
            budget = checked_plan.budget_before
            elapsed_at_start = budget.elapsed_at(execution_started)
            used_z_um = 0.0
            for index, target in enumerate(targets):
                self._check_focus_runtime_budget(
                    plan=checked_plan,
                    execution_started=execution_started,
                    elapsed_at_start=elapsed_at_start,
                    z_deltas=z_deltas,
                    next_segment=index,
                    used_z_um=used_z_um,
                )

                def check_before_post(
                    segment_index: int = index,
                    z_used_um: float = used_z_um,
                ) -> None:
                    lt.raise_if_cancelled()
                    self._check_focus_runtime_budget(
                        plan=checked_plan,
                        execution_started=execution_started,
                        elapsed_at_start=elapsed_at_start,
                        z_deltas=z_deltas,
                        next_segment=segment_index,
                        used_z_um=z_used_um,
                    )

                with self.motion_pre_post_check(check_before_post):
                    self.move_absolute_in_segments(target)
                used_z_um += z_deltas[index]
                # A slow but confirmed command stays a confirmed stop.  Exhaustion
                # blocks the next command without invalidating the local reference.
                self._check_focus_runtime_budget(
                    plan=checked_plan,
                    execution_started=execution_started,
                    elapsed_at_start=elapsed_at_start,
                    z_deltas=z_deltas,
                    next_segment=index + 1,
                    used_z_um=used_z_um,
                )
            self._hardware_update_position()
            after = self._fetch_state()
            final_reference = self._validate_reference(after)
            final = cast(
                tuple[int, int, int],
                tuple(self._hardware_position[axis] for axis in self.axis_names),
            )
            if final != checked_plan.final_stage_units:
                self._invalidate("Focus plan final readback changed unexpectedly")
                raise StageSafetyError(self._fault)
            # Final readback can consume time too.  Do not issue preparation
            # evidence after spending its correction/verification reserve.
            final_elapsed_s = self._check_focus_runtime_budget(
                plan=checked_plan,
                execution_started=execution_started,
                elapsed_at_start=elapsed_at_start,
                z_deltas=z_deltas,
                next_segment=len(targets),
                used_z_um=used_z_um,
            )
            actual_budget_after = budget.model_copy(
                update={
                    "elapsed_s": final_elapsed_s,
                    "z_travel_um": budget.z_travel_um + used_z_um,
                }
            )
            prepared = PreparedApproach(
                source_plan_id=checked_plan.plan_id,
                reference_id=final_reference.identifier,
                motion_revision=self._motion_revision,
                xyz_stage_units=final,
                binding=checked_plan.binding,
                policy=checked_plan.policy,
                budget_after=actual_budget_after,
                progress=checked_plan.progress,
                approach_sign=checked_plan.binding.approach_parameters.approach_sign,
            )
            self._prepared_approach = prepared
            return prepared

    def move_z_with_preload(self, z: int, preload: int, approach_sign: int) -> None:
        """Measure a candidate Z preload without activating global compensation.

        This Python-only path belongs to the existing stage adapter. Every leg
        keeps the normal bounds, reference, M400/readback and settling checks.
        Cancellation or an ambiguous command stops the sequence, with no return.
        """
        if any(type(value) is not int for value in (z, preload, approach_sign)):
            raise StageSafetyError("Z/preload/direction must be integer stage units")
        if preload <= 0 or approach_sign not in (-1, 1):
            raise StageSafetyError("Invalid candidate Z preload or approach")
        with self._hardware_lock:
            before = z - approach_sign * preload
            self.validate_path([{"z": before}, {"z": z}])
            position = self.get_xyz_position()
            for target_z in (before, z):
                self.move_absolute_in_segments((position[0], position[1], target_z))

    def move_to_scan_preload(
        self,
        target_xyz: tuple[int, int, int],
        preload: int,
        approach_sign: int,
    ) -> PreparedScanPreload:
        """Move XY and Z to a scan field's preload point in one transit.

        The final approach is preflighted before the transit starts but deliberately
        left for ``complete_scan_preload``.  Its opaque receipt prevents callers from
        skipping the physical preload or replaying an old preparation.
        """
        if (
            len(target_xyz) != 3
            or any(type(value) is not int for value in target_xyz)
            or type(preload) is not int
            or type(approach_sign) is not int
        ):
            raise StageSafetyError(
                "Scan target/preload/direction must be integer units"
            )
        if preload <= 0 or approach_sign not in (-1, 1):
            raise StageSafetyError("Invalid scan Z preload or approach")
        preload_xyz = (
            target_xyz[0],
            target_xyz[1],
            target_xyz[2] - approach_sign * preload,
        )
        with self._hardware_lock, self._state_scope():
            self._hardware_update_position()
            start = dict(self._hardware_position)
            state = self._fetch_state()
            reference = self._validate_reference(state)
            self._preflight_absolute_sequence(
                (preload_xyz, target_xyz), state, reference, start
            )
            self.move_absolute_in_segments(preload_xyz)
            if (
                tuple(self._hardware_position[axis] for axis in self.axis_names)
                != preload_xyz
            ):
                self._invalidate("Scan transit did not reach its preload point")
                raise StageSafetyError(self._fault)
            final_reference = self._reference
            if final_reference is None:
                raise StageSafetyError(self._fault)
            return PreparedScanPreload(
                reference_id=final_reference.identifier,
                motion_revision=self._motion_revision,
                target_xyz=target_xyz,
                preload_xyz=preload_xyz,
                preload_units=preload,
                approach_sign=approach_sign,
            )

    def _checked_atomic_path(
        self,
        targets: tuple[dict[str, int], ...],
        state: ControllerState,
        reference: Reference,
        start: Mapping[str, int],
    ) -> CheckedMotionPath:
        """Resolve dynamics and a conservative timeout for one preflighted path."""
        planned_state = state
        planned_position = dict(start)
        commands = []
        motion_duration = 0.0
        timeouts = []
        settles = []
        for target in targets:
            delta = {
                axis: target[axis] - planned_position[axis] for axis in self.axis_names
            }
            checked, speed, accel, settle, timeout = self._check_target(
                planned_state, reference, delta
            )
            planned_position = target
            if not checked:
                continue
            components = [
                abs(
                    checked.get(axis, planned_state.gcode_position[index])
                    - planned_state.gcode_position[index]
                )
                for index, axis in enumerate(self.axis_names)
            ]
            distance = math.hypot(*components)
            motion_duration += distance / speed + 2 * speed / accel
            timeouts.append(timeout)
            settles.append(settle)
            commands.append(CheckedMotionCommand(checked, speed, accel))
            next_gcode = tuple(
                checked.get(axis, planned_state.gcode_position[index])
                for index, axis in enumerate(self.axis_names)
            )
            next_machine = tuple(
                planned_state.machine_position[index]
                + next_gcode[index]
                - planned_state.gcode_position[index]
                for index in range(3)
            )
            planned_state = planned_state.model_copy(
                update={
                    "gcode_position": next_gcode,
                    "machine_position": next_machine,
                }
            )
        if not commands:
            raise StageSafetyError("Scan preload path contains no movement")
        timeout = min(timeouts)
        required = motion_duration + self._hardware.request_timeout_s
        if timeout <= required:
            raise StageSafetyError(
                "Configured move timeout is too short for the preload path: "
                f"requires {required:.3f} s, configured {timeout:.3f} s"
            )
        return CheckedMotionPath(
            commands=tuple(commands),
            final_state=planned_state,
            timeout_s=timeout,
            settle_s=max(settles, default=0.0),
        )

    @staticmethod
    def _atomic_path_script(state: ControllerState, path: CheckedMotionPath) -> str:
        """Serialize checked segments with an explicit stop after every endpoint."""
        lines = [
            "SAVE_GCODE_STATE NAME=FAST_OFM_OPENFLEXURE",
            "G90",
            "M220 S100",
        ]
        for command in path.commands:
            words = " ".join(
                f"{axis.upper()}{value:.6f}" for axis, value in command.target.items()
            )
            lines.extend(
                (
                    f"SET_VELOCITY_LIMIT VELOCITY={command.speed:.6f} "
                    f"ACCEL={command.accel:.6f}",
                    f"G1 {words} F{command.speed * 60:.6f}",
                    "M400",
                )
            )
        lines.extend(
            (
                "RESTORE_GCODE_STATE NAME=FAST_OFM_OPENFLEXURE",
                f"SET_VELOCITY_LIMIT VELOCITY={state.max_velocity:.6f} "
                f"ACCEL={state.max_accel:.6f}",
            )
        )
        return "\n".join(lines)

    def _verify_atomic_path_completion(
        self,
        before: ControllerState,
        expected: ControllerState,
        after: ControllerState,
    ) -> None:
        """Require the same identity, idle state and XYZ result as a normal move."""
        self._ready(after)
        if (
            after.pid != before.pid
            or after.eventtime < before.eventtime
            or after.homing_origin != before.homing_origin
        ):
            raise StageSafetyError("Controller reference changed during move")
        for index in range(len(self.axis_names)):
            if (
                abs(after.gcode_position[index] - expected.gcode_position[index])
                > self._hardware.position_tolerance_mm
                or abs(after.machine_position[index] - expected.machine_position[index])
                > self._hardware.position_tolerance_mm
            ):
                raise StageSafetyError(
                    "Final controller position differs from commanded target"
                )

    def _execute_atomic_path(
        self,
        state: ControllerState,
        reference: Reference,
        path: CheckedMotionPath,
    ) -> None:
        """Submit once, then require a conclusive push or fresh final readback."""
        self.moving = True
        self.last_motion_readback_source = "none"
        self.last_motion_readback_duration_s = None
        try:
            if self._client is None:
                raise StageSafetyError("Stage HTTP client is not open")
            started_at = time.time()
            response = self._client.post(
                "/printer/gcode/script",
                json={"script": self._atomic_path_script(state, path)},
                timeout=path.timeout_s,
            )
            acknowledged_at = time.time()
            response.raise_for_status()
            if response.json().get("result") != "ok":
                raise StageSafetyError("Controller did not acknowledge completion")
            self.last_motion_interval = (started_at, acknowledged_at)
            after = self._post_motion_state(
                state,
                lambda candidate: self._verify_atomic_path_completion(
                    state, path.final_state, candidate
                ),
            )
            reference.expected = after
            self._hardware_update_position()
            self._motion_revision += 1
            self.moving = False
        except Exception as exc:
            self.last_motion_interval = None
            self._invalidate("Move outcome uncertain; inspect controller, do not retry")
            raise StageSafetyError(self._fault) from exc

    def move_to_scan_target_with_preload(
        self,
        target_xyz: tuple[int, int, int],
        preload: int,
        approach_sign: int,
    ) -> CompletedScanPreload:
        """Execute the same preload and final approach in one acknowledged script.

        Both endpoints and every rounded segment are validated before the single
        POST.  Klipper receives an ``M400`` after the preload and after the final
        approach, so this only removes the intermediate HTTP/readback cycle; it
        does not merge or skip the physical stops.
        """
        if (
            len(target_xyz) != 3
            or any(type(value) is not int for value in target_xyz)
            or type(preload) is not int
            or type(approach_sign) is not int
        ):
            raise StageSafetyError(
                "Scan target/preload/direction must be integer units"
            )
        if preload <= 0 or approach_sign not in (-1, 1):
            raise StageSafetyError("Invalid scan Z preload or approach")
        preload_xyz = (
            target_xyz[0],
            target_xyz[1],
            target_xyz[2] - approach_sign * preload,
        )
        with self._hardware_lock, self._state_scope():
            self._hardware_update_position()
            start = dict(self._hardware_position)
            state = self._fetch_state()
            reference = self._validate_reference(state)
            targets = self._preflight_absolute_sequence(
                (preload_xyz, target_xyz), state, reference, start
            )
            path = self._checked_atomic_path(targets, state, reference, start)
            lt.cancellable_sleep(0)
            self._check_motion_pre_post()
            self._prepared_approach = None
            self._completed_scan_preload = None
            self._execute_atomic_path(state, reference, path)
            try:
                lt.cancellable_sleep(path.settle_s)
            finally:
                self._clear_state_reads()
            completed = CompletedScanPreload(
                reference_id=reference.identifier,
                motion_revision=self._motion_revision,
                target_xyz=target_xyz,
                preload_units=preload,
                approach_sign=approach_sign,
            )
            self._completed_scan_preload = completed
            return completed

    def consume_completed_scan_preload(
        self, completed: CompletedScanPreload
    ) -> CompletedScanPreload:
        """Consume one immediately preceding atomic preload path without more I/O."""
        if not isinstance(completed, CompletedScanPreload):
            raise StageSafetyError("A completed scan preload receipt is required")
        with self._hardware_lock:
            current = tuple(self._hardware_position[axis] for axis in self.axis_names)
            reference = self._reference
            if (
                self._completed_scan_preload != completed
                or reference is None
                or reference.identifier != completed.reference_id
                or self._motion_revision != completed.motion_revision
                or current != completed.target_xyz
            ):
                raise StageSafetyError(
                    "Completed scan preload no longer matches the stage"
                )
            self._completed_scan_preload = None
            return completed

    def complete_scan_preload(
        self, prepared: PreparedScanPreload
    ) -> CompletedScanPreload:
        """Consume a current scan-preload receipt and make only the final Z approach."""
        if not isinstance(prepared, PreparedScanPreload):
            raise StageSafetyError("A stage-issued scan preload receipt is required")
        with self._hardware_lock, self._state_scope():
            state = self._fetch_state()
            reference = self._validate_reference(state)
            position = self._position_from_state(state, reference)
            current = cast(
                tuple[int, int, int],
                tuple(position[axis] for axis in self.axis_names),
            )
            if (
                reference.identifier != prepared.reference_id
                or self._motion_revision != prepared.motion_revision
                or current != prepared.preload_xyz
            ):
                raise StageSafetyError(
                    "Scan preload receipt no longer matches the stage"
                )
            self._preflight_absolute_sequence(
                (prepared.target_xyz,), state, reference, position
            )
            self.move_absolute_in_segments(prepared.target_xyz)
            final = cast(
                tuple[int, int, int],
                tuple(self._hardware_position[axis] for axis in self.axis_names),
            )
            if final != prepared.target_xyz:
                self._invalidate("Scan preload approach did not reach its target")
                raise StageSafetyError(self._fault)
            final_reference = self._reference
            if final_reference is None:
                raise StageSafetyError(self._fault)
            return CompletedScanPreload(
                reference_id=final_reference.identifier,
                motion_revision=self._motion_revision,
                target_xyz=prepared.target_xyz,
                preload_units=prepared.preload_units,
                approach_sign=prepared.approach_sign,
            )

    def _move_with_backlash_correction(
        self,
        block_cancellation: bool,
        relative: bool,
        backlash_compensation: BacklashCompensation,
        **kwargs: int,
    ) -> None:
        # Until OF-013 supplies validated complete-path planning, reject any
        # nonzero correction BEFORE allowing the first leg of an overshoot.
        if any(self.backlash_steps.values()):
            raise StageSafetyError("Backlash path is not commissioned; no move sent")
        super()._move_with_backlash_correction(
            block_cancellation, relative, backlash_compensation, **kwargs
        )

    @lt.action
    def move_manual(
        self,
        relative: bool = True,
        x: int | None = None,
        y: int | None = None,
        z: int | None = None,
    ) -> None:
        """Make a bounded operator move with its separate settling policy.

        Completion still requires M400 acknowledgement and verified idle/position.
        Normal move_relative/move_absolute keep their automatic settling delays.
        """
        if not self._hardware.allow_operator_controls:
            raise StageSafetyError(
                "Operator controls are disabled in the deployment profile"
            )
        moves = {
            axis: value
            for axis, value in (("x", x), ("y", y), ("z", z))
            if value is not None
        }
        token = self._manual_control.set(True)
        try:
            if relative:
                super().move_relative(
                    block_cancellation=False, backlash_compensation=None, **moves
                )
            else:
                super().move_absolute(
                    block_cancellation=False, backlash_compensation=None, **moves
                )
        finally:
            self._manual_control.reset(token)

    @lt.action(use_global_lock=False)
    def jog(self, stop: bool = False, **kwargs: int) -> None:
        """Reject continuous jogging; use explicit short move_relative actions instead.

        M112 is an emergency shutdown, not an implementation of jog-stop.
        Cancelling a bounded move waits for its current command; it is not instant.
        """
        operation = "stop" if stop else f"jog {kwargs}"
        raise StageSafetyError(
            f"Continuous {operation} is unsupported; use bounded moves"
        )


def move_absolute_transit(
    stage: BaseStage,
    *,
    block_cancellation: bool = False,
    backlash_compensation: BacklashCompensation | None = None,
    **target: int,
) -> None:
    """Bound Moonraker transits; preserve stock-stage movement/backlash behaviour."""
    if isinstance(stage, MoonrakerStage):
        with stage._hardware_lock, stage._state_scope():
            if set(target) - set(stage.axis_names):
                raise StageSafetyError("Only X, Y and Z are supported")
            if any(stage.backlash_steps.values()):
                raise StageSafetyError(
                    "Backlash path is not commissioned; no move sent"
                )
            current = stage.get_xyz_position()
            xyz_target = tuple(
                round(target.get(axis, current[i]))
                for i, axis in enumerate(stage.axis_names)
            )
            stage.move_absolute_in_segments(
                cast(tuple[int, int, int], xyz_target), block_cancellation
            )
    else:
        stage.move_absolute(
            block_cancellation=block_cancellation,
            backlash_compensation=backlash_compensation,
            **target,
        )


def move_relative_transit(
    stage: BaseStage,
    *,
    backlash_compensation: BacklashCompensation | None = None,
    **delta: int,
) -> None:
    """Check a non-measuring transit before its first segment."""
    if isinstance(stage, MoonrakerStage):
        with stage._hardware_lock, stage._state_scope():
            if set(delta) - set(stage.axis_names):
                raise StageSafetyError("Only X, Y and Z are supported")
            current = dict(zip(stage.axis_names, stage.get_xyz_position(), strict=True))
            move_absolute_transit(
                stage,
                block_cancellation=False,
                backlash_compensation=backlash_compensation,
                **{axis: current[axis] + value for axis, value in delta.items()},
            )
    else:
        stage.move_relative(
            block_cancellation=False,
            backlash_compensation=backlash_compensation,
            **delta,
        )
