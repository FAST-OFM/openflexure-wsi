"""Run-local sparse-focus orchestration around an external prediction service.

This GPL module owns scan cadence and hardware provenance only. It deliberately
contains no surface fitting or focus-prediction mathematics; those are supplied
through :class:`FocusPredictor` by the separately installed Fast OFM Core process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SparseFocusSettings:
    """Frozen limits for one scan's sparse autofocus schedule."""

    enabled: bool = False
    anchor_interval: int = 8
    minimum_plane_points: int = 4
    maximum_neighbors: int = 12
    neighbor_radius_um: float = 6000.0
    maximum_extrapolation_um: float = 3000.0
    maximum_prediction_delta_um: float = 40.0
    maximum_fit_residual_um: float = 4.0
    maximum_plane_slope_um_per_um: float = 0.05
    maximum_condition_number: float = 1000.0
    maximum_recovery_anchors: int | None = None

    def __post_init__(self) -> None:
        """Reject incomplete or internally inconsistent scan settings."""
        if self.anchor_interval < 1:
            raise ValueError("Anchor interval must be positive")
        if (
            self.maximum_recovery_anchors is not None
            and self.maximum_recovery_anchors < 1
        ):
            raise ValueError("Recovery anchor limit must be positive")
        if self.minimum_plane_points < 4:
            raise ValueError("A robust local plane needs at least four anchors")
        if self.maximum_neighbors < self.minimum_plane_points:
            raise ValueError("Maximum neighbors cannot be below plane support")
        positive = (
            self.neighbor_radius_um,
            self.maximum_prediction_delta_um,
            self.maximum_fit_residual_um,
            self.maximum_plane_slope_um_per_um,
            self.maximum_condition_number,
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("Sparse focus limits must be finite and positive")
        if (
            not math.isfinite(self.maximum_extrapolation_um)
            or self.maximum_extrapolation_um < 0
            or self.maximum_extrapolation_um > self.neighbor_radius_um
        ):
            raise ValueError("Extrapolation must fit inside the neighbor radius")


@dataclass(frozen=True)
class FocusAnchor:
    """One autofocus result measured by hardware during this scan."""

    x_um: float
    y_um: float
    z_um: float


@dataclass(frozen=True)
class FocusEstimate:
    """A bounded target height returned by the external prediction service."""

    usable: bool
    target_z_um: float | None
    source: str
    reason: str
    support_count: int = 0
    fit_residual_um: float | None = None
    slope_um_per_um: float | None = None
    extrapolation_um: float | None = None
    validation_error_um: float | None = None


class FocusPredictor(Protocol):
    """Process-neutral callable used by the GPL scan-state coordinator."""

    def __call__(
        self,
        anchors: tuple[FocusAnchor, ...],
        *,
        target_x_um: float,
        target_y_um: float,
        current_z_um: float,
        settings: SparseFocusSettings,
    ) -> FocusEstimate:
        """Return one bounded estimate without touching microscope hardware."""
        ...


@dataclass(frozen=True)
class SparseFocusDecision:
    """One prepared field decision, frozen until acquisition finishes."""

    x_units: int
    y_units: int
    target_z_units: int
    requires_anchor: bool
    estimate: FocusEstimate
    white_only: bool = False


class SparseFocusScheduler:
    """Track measured anchors and apply externally computed focus estimates."""

    def __init__(
        self,
        settings: SparseFocusSettings,
        *,
        x_um_per_unit: float,
        y_um_per_unit: float,
        z_um_per_unit: float,
        predictor: FocusPredictor,
    ) -> None:
        """Create an empty run-local coordinator with explicit axis scales."""
        self.settings = settings
        self._scale = (x_um_per_unit, y_um_per_unit, z_um_per_unit)
        self._predictor = predictor
        self.anchors: list[FocusAnchor] = []
        self.fields_since_anchor = 0
        self.pending: SparseFocusDecision | None = None
        self.anchor_fields = 0
        self.predicted_fields = 0
        self.background_fields = 0
        self.white_fallback_fields = 0
        self.fields_since_rg_probe = 0
        self._initial_row_y_units: int | None = None
        self._recovery_anchors = 0

    def prepare(
        self,
        x_units: int,
        y_units: int,
        current_z_units: int,
        route_z_units: int | None,
    ) -> SparseFocusDecision:
        """Freeze the next field's target and whether hardware AF is required."""
        if self.pending is not None:
            raise RuntimeError("Previous sparse focus decision is still pending")
        if self._initial_row_y_units is None:
            self._initial_row_y_units = y_units
        estimate = self._predictor(
            tuple(self.anchors),
            target_x_um=x_units * self._scale[0],
            target_y_um=y_units * self._scale[1],
            current_z_um=current_z_units * self._scale[2],
            settings=self.settings,
        )
        warmup = len(self.anchors) < self.settings.minimum_plane_points
        cadence_due = self.fields_since_rg_probe >= self.settings.anchor_interval - 1
        validated_prediction = estimate.usable and estimate.source in ("plane", "row")
        requires_anchor = warmup or cadence_due or not validated_prediction
        recovery_limit = self.settings.maximum_recovery_anchors
        white_only = (
            not validated_prediction
            and self._initial_row_y_units is not None
            and y_units != self._initial_row_y_units
            and recovery_limit is not None
            and self._recovery_anchors >= recovery_limit
            and self.fields_since_rg_probe < self.settings.anchor_interval - 1
        )
        fallback_z = (
            current_z_units if white_only or route_z_units is None else route_z_units
        )
        target_z = (
            fallback_z
            if white_only or not estimate.usable or estimate.target_z_um is None
            else round(estimate.target_z_um / self._scale[2])
        )
        self.pending = SparseFocusDecision(
            x_units=x_units,
            y_units=y_units,
            target_z_units=target_z,
            requires_anchor=requires_anchor,
            estimate=estimate,
            white_only=white_only,
        )
        return self.pending

    def skip_background(self) -> None:
        """Discard a prepared field without advancing the sample cadence."""
        if self.pending is None:
            raise RuntimeError("No sparse focus decision is pending")
        self.background_fields += 1
        self.pending = None

    def _complete_rg_probe(self) -> None:
        """Advance recovery for an R/G attempt, even when it handed off to WHITE."""
        if self.pending is None:
            raise RuntimeError("No sparse focus decision is pending")
        self.fields_since_rg_probe = 0
        if self.pending.y_units == self._initial_row_y_units or (
            self.pending.estimate.usable
            and self.pending.estimate.source in ("plane", "row")
        ):
            self._recovery_anchors = 0
        else:
            self._recovery_anchors += 1

    def complete_anchor(
        self, z_units: int, *, focus_method: str = "rg_simultaneous"
    ) -> FocusAnchor:
        """Record only a successful measured R/G result, never WHITE/prediction."""
        if (
            self.pending is None
            or not self.pending.requires_anchor
            or self.pending.white_only
        ):
            raise RuntimeError("Pending field is not a focus anchor")
        if focus_method != "rg_simultaneous":
            raise ValueError("Only measured simultaneous RG can train this surface")
        self._complete_rg_probe()
        anchor = FocusAnchor(
            x_um=self.pending.x_units * self._scale[0],
            y_um=self.pending.y_units * self._scale[1],
            z_um=z_units * self._scale[2],
        )
        self.anchors.append(anchor)
        self.anchor_fields += 1
        self.fields_since_anchor = 0
        self.pending = None
        return anchor

    def complete_white_fallback(self, *, rg_attempted: bool) -> None:
        """Finish a WHITE field without promoting its Z or resetting R/G cadence."""
        if self.pending is None or not self.pending.requires_anchor:
            raise RuntimeError("Pending field does not require measured focus")
        if self.pending.white_only and rg_attempted:
            raise RuntimeError("WHITE-only field unexpectedly attempted RG")
        if rg_attempted:
            self._complete_rg_probe()
        else:
            self.fields_since_rg_probe += 1
        self.white_fallback_fields += 1
        self.fields_since_anchor += 1
        self.pending = None

    def complete_prediction(self) -> None:
        """Finish one image at predicted Z without promoting it to an anchor."""
        if self.pending is None or self.pending.requires_anchor:
            raise RuntimeError("Pending field is not a predicted focus field")
        self.predicted_fields += 1
        self.fields_since_anchor += 1
        self.fields_since_rg_probe += 1
        self._recovery_anchors = 0
        self.pending = None
