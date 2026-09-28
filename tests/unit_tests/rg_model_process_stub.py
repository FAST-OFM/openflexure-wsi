"""Test bridge to the separately packaged R/G calibration implementation."""

from __future__ import annotations

import pytest

pytest.importorskip(
    "fast_ofm_core",
    reason="R/G process-boundary workflow tests require the optional core package",
)

from fast_ofm_core.focus.rg.rg_focus_model import (
    RGFocusCalibrationPoint as CoreCalibrationPoint,
)
from fast_ofm_core.focus.rg.rg_focus_model import (
    RGFocusMeasurementPolicy as CoreMeasurementPolicy,
)
from fast_ofm_core.focus.rg.rg_focus_model import (
    RGFocusModelProfile as CoreModelProfile,
)
from fast_ofm_core.focus.rg.rg_focus_model import (
    RGFocusModelSettings as CoreModelSettings,
)
from fast_ofm_core.focus.rg.rg_focus_model import (
    RGFocusStationaryObservation as CoreStationaryObservation,
)
from fast_ofm_core.focus.rg.rg_focus_model import (
    _approach_check as core_approach_check,
)
from fast_ofm_core.focus.rg.rg_focus_model import estimate_z_um as core_estimate_z_um
from fast_ofm_core.focus.rg.rg_focus_model import (
    fit_rg_focus_model as core_fit_rg_focus_model,
)

from openflexure_microscope_server.focus.rg.rg_focus_model import (
    RGFocusApproachCheck,
    RGFocusModelProfile,
)


def fit_rg_focus_model(points, settings, **kwargs) -> RGFocusModelProfile:
    """Fit via the core package and translate only the public result value."""
    stationary = kwargs.pop("stationary_observations")
    policy = kwargs.pop("measurement_policy", None)
    result = core_fit_rg_focus_model(
        [
            CoreCalibrationPoint.model_validate(row.model_dump(mode="json"))
            for row in points
        ],
        CoreModelSettings.model_validate(settings.model_dump(mode="json")),
        stationary_observations=tuple(
            CoreStationaryObservation.model_validate(row.model_dump(mode="json"))
            for row in stationary
        ),
        measurement_policy=(
            None
            if policy is None
            else CoreMeasurementPolicy.model_validate(policy.model_dump(mode="json"))
        ),
        **kwargs,
    )
    return RGFocusModelProfile.model_validate(result.model_dump(mode="json"))


def estimate_z_um(profile, dx: float, dy: float) -> float:
    """Evaluate the projection with the core package's validated profile."""
    value = CoreModelProfile.model_validate(profile.model_dump(mode="json"))
    return core_estimate_z_um(value, dx, dy)


def cross_track_residual_px(profile, dx: float, dy: float) -> tuple[float, float]:
    """Evaluate cross-track evidence in the separately packaged core."""
    value = CoreModelProfile.model_validate(profile.model_dump(mode="json"))
    return value.cross_track_residual_px(dx, dy)


def effective_focus_tolerance_um(profile, total_tolerance_um: float) -> float:
    """Reserve empirical model error in the separately packaged core."""
    value = CoreModelProfile.model_validate(profile.model_dump(mode="json"))
    return value.effective_focus_tolerance_um(total_tolerance_um)


def _approach_check(points, settings, **kwargs) -> RGFocusApproachCheck:
    """Evaluate the calibration return check in the core package."""
    result = core_approach_check(
        [
            CoreCalibrationPoint.model_validate(row.model_dump(mode="json"))
            for row in points
        ],
        CoreModelSettings.model_validate(settings.model_dump(mode="json")),
        **kwargs,
    )
    return RGFocusApproachCheck.model_validate(result.model_dump(mode="json"))


def external_profile_validation(
    profile_value, focus_tolerance_um: float | None = None
) -> tuple[RGFocusModelProfile, float | None]:
    """Model the ``calibration.validate`` response for GPL workflow tests."""
    local = RGFocusModelProfile.model_validate(profile_value)
    core = CoreModelProfile.model_validate(local.model_dump(mode="json"))
    effective = (
        None
        if focus_tolerance_um is None
        else core.effective_focus_tolerance_um(focus_tolerance_um)
    )
    return (
        RGFocusModelProfile.model_validate(core.model_dump(mode="json")),
        effective,
    )
