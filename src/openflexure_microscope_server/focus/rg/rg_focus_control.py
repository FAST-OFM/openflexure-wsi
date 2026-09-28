"""Wire contracts for bounded tissue-aware R/G autofocus decisions.

The correction policy is owned by ``fast-ofm-core`` and is reached through the
``rg.decide`` process operation.  This GPL-side module intentionally contains
only the values required by the hardware orchestration layer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...fast_ofm_contracts import StrictModel


class RGFocusControlSettings(StrictModel):
    """Bound measurement count, elapsed time and every correction envelope."""

    maximum_iterations: int = Field(default=3, ge=1, le=5)
    timeout_s: float = Field(default=120.0, gt=0, le=300)
    minimum_capture_budget_s: float = Field(default=30.0, gt=0, le=60)
    focus_tolerance_um: float = Field(default=1.5, gt=0, le=4)
    cross_track_envelope_multiplier: float = Field(
        default=1.0,
        ge=1.0,
        le=10.0,
        description=(
            "Scale the calibrated cross-track envelope for tissue-dependent "
            "demo operation; axial range and correction limits remain independent."
        ),
    )
    cross_track_measurement_mad_multiplier: float = Field(default=1.0, ge=0.0, le=3.0)
    maximum_single_correction_um: float = Field(default=32.0, gt=0, le=32)
    maximum_total_correction_um: float = Field(default=32.0, gt=0, le=32)
    maximum_absolute_z_excursion_um: float = Field(
        default=42.0,
        gt=0,
        le=48,
        description=(
            "Full standalone autofocus Z path, including preload, relative to its "
            "starting position. Scan paths use their separate field envelope and budget."
        ),
    )


class RGFocusDecision(StrictModel):
    """One non-hardware decision made from a strict measurement and saved model."""

    status: Literal["focused", "move", "refused"]
    reason: str
    inferred_z_position_um: float | None = None
    inferred_z_error_um: float | None = None
    correction_um: float | None = None
    empirical_prediction_error_um: float | None = None
    effective_residual_tolerance_um: float | None = None
    cross_track_residual_px: float | None = None
    cross_track_empirical_limit_px: float | None = None
    cross_track_measurement_mad_px: float | None = None
    cross_track_limit_px: float | None = None
    cross_track_roundoff_px: float | None = None
