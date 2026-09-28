"""Contract tests for the GPL-to-core focus adapter."""

from unittest.mock import MagicMock

import pytest

from openflexure_microscope_server.focus.sparse_focus import (
    FocusAnchor,
    SparseFocusSettings,
)
from openflexure_microscope_server.integrations.fast_ofm_core.client import (
    FastOFMCoreError,
)
from openflexure_microscope_server.integrations.fast_ofm_core.focus import (
    predict_sparse_focus,
)


def test_prediction_payload_and_result_are_process_neutral() -> None:
    """Only JSON values cross the process boundary and map back to a DTO."""
    process = MagicMock()
    process.request.return_value = {
        "result": {
            "status": "predicted",
            "model": "plane",
            "reason": "bounded local focus plane",
            "support_count": 4,
            "predicted_z_um": 12.5,
            "fit_residual_um": 0.25,
            "slope_um_per_um": 0.002,
            "extrapolation_um": 0.0,
            "validation_error_um": 0.4,
        }
    }
    result = predict_sparse_focus(
        process,
        (FocusAnchor(1.0, 2.0, 3.0),),
        target_x_um=4.0,
        target_y_um=5.0,
        current_z_um=6.0,
        settings=SparseFocusSettings(enabled=True),
    )
    assert result.usable
    assert result.target_z_um == 12.5
    operation, payload = process.request.call_args.args
    assert operation == "focus.surface.predict"
    assert payload["anchors"][0]["position_um"] == {
        "x_um": 1.0,
        "y_um": 2.0,
        "z_um": 3.0,
    }
    assert process.request.call_args.kwargs == {"timeout_s": 5.0}


def test_refusal_has_no_motor_target() -> None:
    """A refused external model cannot accidentally supply a stage target."""
    process = MagicMock()
    process.request.return_value = {
        "result": {
            "status": "refused",
            "reason": "no nearby measured anchor",
            "support_count": 0,
            "refusal_code": "FOCUS_SURFACE_UNAVAILABLE",
        }
    }
    result = predict_sparse_focus(
        process,
        (),
        target_x_um=0,
        target_y_um=0,
        current_z_um=0,
        settings=SparseFocusSettings(),
    )
    assert not result.usable
    assert result.target_z_um is None


@pytest.mark.parametrize(
    "result",
    [
        {"status": "maybe", "reason": "x", "support_count": 0},
        {
            "status": "predicted",
            "reason": "x",
            "support_count": 1,
            "predicted_z_um": float("nan"),
        },
    ],
)
def test_malformed_core_result_fails_closed(result) -> None:
    """Malformed or non-finite results never reach stage coordination."""
    process = MagicMock()
    process.request.return_value = {"result": result}
    with pytest.raises(FastOFMCoreError, match="focus|Focus|finite"):
        predict_sparse_focus(
            process,
            (),
            target_x_um=0,
            target_y_um=0,
            current_z_um=0,
            settings=SparseFocusSettings(),
        )
