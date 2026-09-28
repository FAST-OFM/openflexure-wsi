"""GPL-side contract tests for the externally evaluated R/G focus decision."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openflexure_microscope_server.focus.rg.rg_focus_control import (
    RGFocusControlSettings,
    RGFocusDecision,
)
from openflexure_microscope_server.focus.rg.rg_focus_estimator import RGFocusMeasurement
from openflexure_microscope_server.focus.rg.rg_focus_model import (
    RGFocusCalibrationPoint,
)


def calibration_point(capture_id, role, z, *, white=None):
    """Create deterministic calibration input for orchestration tests."""
    return RGFocusCalibrationPoint(
        capture_id=capture_id,
        role=role,
        z_um=float(z),
        measurement_status="ready",
        dx=3.0 - z,
        dy=-0.5 + 0.1 * z,
        confidence=0.8,
        white_score=float(100 - abs(z) if white is None else white),
    )


def measurement(dx=3.0, dy=-0.5, status="ready"):
    """Build a compact aggregate; estimator behaviour belongs to the core tests."""
    return RGFocusMeasurement(
        status=status,
        reason="test",
        dx=dx if status == "ready" else None,
        dy=dy if status == "ready" else None,
        confidence=0.8 if status == "ready" else 0,
        median_response=0.8 if status == "ready" else 0,
        median_spectral_correlation=0.8 if status == "ready" else 0,
        dx_mad=0.1 if status == "ready" else 0,
        dy_mad=0.1 if status == "ready" else 0,
        radial_p90=0.2 if status == "ready" else 0,
        candidate_patch_count=20,
        accepted_patch_count=20 if status == "ready" else 0,
        inlier_patch_count=20 if status == "ready" else 0,
        inlier_fraction=1 if status == "ready" else 0,
        support_fraction=0.4,
        rejection_counts={},
        windows=(),
    )


def test_control_defaults_are_finite_and_bounded():
    """The GPL contract preserves the hard movement and runtime envelopes."""
    settings = RGFocusControlSettings()
    assert settings.maximum_iterations == 3
    assert settings.maximum_single_correction_um <= 32
    assert settings.maximum_total_correction_um <= 32
    assert settings.maximum_absolute_z_excursion_um <= 48


def test_demo_cross_track_multiplier_has_a_bounded_extended_range():
    """Commissioning may widen the envelope only inside its declared limit."""
    assert (
        RGFocusControlSettings(
            cross_track_envelope_multiplier=8
        ).cross_track_envelope_multiplier
        == 8
    )
    with pytest.raises(ValidationError):
        RGFocusControlSettings(cross_track_envelope_multiplier=10.01)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_iterations", 0),
        ("timeout_s", 301),
        ("focus_tolerance_um", 4.01),
        ("maximum_single_correction_um", 32.01),
        ("maximum_total_correction_um", 32.01),
        ("maximum_absolute_z_excursion_um", 48.01),
    ],
)
def test_control_contract_rejects_out_of_envelope_values(field, value):
    """Invalid settings are rejected before they cross the process boundary."""
    with pytest.raises(ValidationError):
        RGFocusControlSettings.model_validate({field: value})


@pytest.mark.parametrize("status", ["focused", "move", "refused"])
def test_decision_contract_accepts_protocol_statuses(status):
    """Every status defined by protocol version 1 remains serialisable."""
    decision = RGFocusDecision(status=status, reason="core result")
    assert decision.status == status


def test_decision_contract_rejects_unknown_fields_and_statuses():
    """Unexpected core output fails closed during GPL-side validation."""
    with pytest.raises(ValidationError):
        RGFocusDecision(status="retry", reason="not in protocol")
    with pytest.raises(ValidationError):
        RGFocusDecision(status="focused", reason="ok", hidden_policy=True)
