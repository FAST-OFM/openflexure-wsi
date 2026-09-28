"""Test bridge to the separately packaged flat-field implementation."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip(
    "fast_ofm_core",
    reason="Flat-field process-boundary tests require the optional core package",
)

from fast_ofm_core.focus.rg.rg_flat_field import (
    FlatFieldSettings as CoreFlatFieldSettings,
)
from fast_ofm_core.focus.rg.rg_flat_field import (
    apply_native_flat_field as core_apply_native_flat_field,
)
from fast_ofm_core.focus.rg.rg_flat_field import fit_flat_field as core_fit_flat_field
from fast_ofm_core.focus.rg.rg_flat_field import (
    validate_flat_field as core_validate_flat_field,
)


def _settings(value) -> CoreFlatFieldSettings:
    return CoreFlatFieldSettings.model_validate(value.model_dump(mode="json"))


def fit_flat_field(flat, dark, settings):
    """Fit with the core implementation while preserving GPL contract values."""
    return core_fit_flat_field(flat, dark, _settings(settings))


def apply_native_flat_field(image, maps):
    """Apply maps with the core implementation."""
    return core_apply_native_flat_field(image, maps)


def validate_flat_field(heldout, maps, fit_report, settings):
    """Validate held-out frames with the core implementation."""
    return core_validate_flat_field(heldout, maps, fit_report, _settings(settings))


def external_flat_fit(_self, _directory, _label, flat, dark, settings):
    """Match the GPL Thing's process-adapter method in integration tests."""
    return fit_flat_field(flat, dark, settings)


def external_flat_apply(_self, image, maps) -> tuple[np.ndarray, np.ndarray]:
    """Match the GPL Thing's process-adapter method in integration tests."""
    return apply_native_flat_field(image, maps)


def external_flat_validate(
    _self, _directory, _label, heldout, maps, fit_report, settings
):
    """Match the GPL Thing's process-adapter method in integration tests."""
    return validate_flat_field(heldout, maps, fit_report, settings)
