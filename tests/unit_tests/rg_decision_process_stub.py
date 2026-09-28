"""Test bridge to the separately licensed Fast OFM Core decision policy.

GPL workflow tests use this bridge only after replacing the real subprocess
method.  The policy itself stays tested and implemented in ``fast-ofm-core``;
this file merely translates the two packages' wire-compatible Pydantic values.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "fast_ofm_core",
    reason="R/G process-boundary workflow tests require the optional core package",
)

from fast_ofm_core.focus.rg.rg_focus_control import (
    RGFocusControlSettings as CoreControlSettings,
)
from fast_ofm_core.focus.rg.rg_focus_control import decide_correction
from fast_ofm_core.focus.rg.rg_focus_estimator import (
    RGFocusMeasurement as CoreMeasurement,
)
from fast_ofm_core.focus.rg.rg_focus_model import RGFocusModelProfile as CoreProfile

from openflexure_microscope_server.focus.rg.rg_focus_control import RGFocusDecision


def external_rg_decision(profile, measurement, settings, **kwargs) -> RGFocusDecision:
    """Return a decision through the core package's public value boundary."""
    result = decide_correction(
        CoreProfile.model_validate(profile.model_dump(mode="json")),
        CoreMeasurement.model_validate(measurement.model_dump(mode="json")),
        CoreControlSettings.model_validate(settings.model_dump(mode="json")),
        **kwargs,
    )
    return RGFocusDecision.model_validate(result.model_dump(mode="json"))
