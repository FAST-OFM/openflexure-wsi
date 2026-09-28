"""Test bridge to the separately packaged R/G measurement implementation.

The production GPL adapter reaches these operations through the JSON/artifact
subprocess boundary.  Pure numerical tests import the independently packaged
implementation here so the GPL source tree does not carry a second copy.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "fast_ofm_core",
    reason="R/G measurement tests require the optional core package",
)

from fast_ofm_core.focus.rg.rg_focus_core import (  # noqa: E402
    PatchMeasurement,
    RGFocusCoreSettings,
)
from fast_ofm_core.focus.rg.rg_focus_estimator import (  # noqa: E402
    RGFocusEstimatorSettings,
    RGFocusInputs,
    _summarise_inliers,
    estimate_rg_shift,
)
from fast_ofm_core.focus.rg.rg_focus_field import (  # noqa: E402
    FrameReference,
    TissueFieldError,
    TissueFieldSettings,
    geometry_fingerprint,
    parse_frame_geometry,
    prepare_tissue_field,
    require_tissue_field_for_pair,
)

__all__ = [
    "FrameReference",
    "PatchMeasurement",
    "RGFocusCoreSettings",
    "RGFocusEstimatorSettings",
    "RGFocusInputs",
    "TissueFieldError",
    "TissueFieldSettings",
    "_summarise_inliers",
    "estimate_rg_shift",
    "geometry_fingerprint",
    "parse_frame_geometry",
    "prepare_tissue_field",
    "require_tissue_field_for_pair",
]
