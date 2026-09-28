"""Test bridge to the separately packaged WHITE tissue-field implementation."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip(
    "fast_ofm_core",
    reason="Tissue-field process-boundary tests require the optional core package",
)

from fast_ofm_core.focus.rg.rg_focus_core import (  # noqa: E402
    RGFocusCoreSettings as CoreSettings,
)
from fast_ofm_core.focus.rg.rg_focus_field import (  # noqa: E402
    FrameReference as CoreReference,
)
from fast_ofm_core.focus.rg.rg_focus_field import (  # noqa: E402
    TissueFieldSettings as CoreFieldSettings,
)
from fast_ofm_core.focus.rg.rg_focus_field import (  # noqa: E402
    prepare_tissue_field as core_prepare_tissue_field,
)

from openflexure_microscope_server.focus.rg.rg_focus_core import PatchBox
from openflexure_microscope_server.focus.rg.rg_focus_field import (
    FrameReference,
    TissueField,
    TissueFieldMetrics,
    parse_frame_geometry,
)


def external_tissue_field(
    _self,
    white_frame,
    reference,
    geometry_value,
    policy,
    _exchange,
) -> TissueField:
    """Match the GPL Thing method while evaluating the core implementation."""
    core = core_prepare_tissue_field(
        white_frame,
        CoreReference.model_validate(reference.model_dump(mode="json")),
        geometry_value,
        CoreFieldSettings.model_validate(policy.tissue_field.model_dump(mode="json")),
        CoreSettings.model_validate(policy.core.model_dump(mode="json")),
    )
    arrays = {
        "mask": np.array(core.mask, copy=True),
        "white_common": np.array(core.white_common, copy=True),
        "overlay": np.array(core.overlay, copy=True),
    }
    for value in arrays.values():
        value.setflags(write=False)
    return TissueField(
        reference=FrameReference.model_validate(core.reference.model_dump(mode="json")),
        geometry=parse_frame_geometry(core.geometry.model_dump(mode="json")),
        mask=arrays["mask"],
        white_common=arrays["white_common"],
        boxes=tuple(
            PatchBox.model_validate(value.model_dump(mode="json"))
            for value in core.boxes
        ),
        metrics=TissueFieldMetrics.model_validate(core.metrics.model_dump(mode="json")),
        overlay=arrays["overlay"],
    )
