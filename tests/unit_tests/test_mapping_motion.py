"""In-run scale/overlap checks reuse the native mapping samples and settings."""

from unittest.mock import Mock

import numpy as np
import pytest
from pydantic import ValidationError

from camera_stage_mapping.camera_stage_tracker import Tracker
from camera_stage_mapping.exceptions import MappingError

from openflexure_microscope_server.fast_ofm_contracts import MappingMotionChecks
from openflexure_microscope_server.things.stage.mapping_motion import (
    CheckedTracker,
    MappingMotionCheck,
)


def make_check(axis=1, sign=1, scale=0.4):
    """Establish scale from two existing positive-direction image displacements."""
    check = MappingMotionCheck((0, 0, 0), axis, sign, 1000.0, MappingMotionChecks())
    for value in (0, 10, 30):
        position = np.zeros(3)
        position[axis] = value * sign
        check.observe(position, np.array([value * scale * sign, 0]), (700, 700, 3))
    return check


@pytest.mark.parametrize("axis", [0, 1])
@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("scale", [0.4, 4.0])
def test_scale_checked_for_either_axis_direction_and_motor_ratio(axis, sign, scale):
    """The conversion is derived from this run, not assumed from a motor model."""
    check = make_check(axis, sign, scale)
    assert check.result()["pixels_per_mm"] == pytest.approx(1000 * scale)
    target = np.zeros(3)
    target[axis] = sign * 10
    check.before_move(np.zeros(3), target)


def test_native_183_micron_step_accepted_when_image_overlap_is_good():
    """183 microns is not intrinsically wrong; verify its image displacement."""
    check = make_check()
    check.before_move(np.array([0, 30, 0]), np.array([0, 213, 0]))
    with pytest.raises(MappingError, match="image-overlap"):
        check.before_move(np.zeros(3), np.array([0, 500, 0]))


def test_unknown_scale_cannot_escalate_to_large_probe():
    """Blank/weak images cannot lead to exponentially growing unchecked moves."""
    check = MappingMotionCheck((0, 0, 0), 1, 1, 1000.0, MappingMotionChecks())
    check.before_move(np.zeros(3), np.array([0, 1, 0]))
    with pytest.raises(MappingError, match="not yet consistent"):
        check.before_move(np.zeros(3), np.array([0, 51, 0]))


def test_inconsistent_scale_is_not_accepted():
    """Two incompatible vectors do not qualify a large measurement move."""
    check = MappingMotionCheck((0, 0, 0), 1, 1, 1000.0, MappingMotionChecks())
    for y, image in ((0, 0), (10, 4), (30, 50)):
        check.observe(np.array([0, y, 0]), np.array([image, 0]), (700, 700))
    with pytest.raises(MappingError, match="not verified"):
        check.result()


def test_final_scale_must_agree_with_probe_frames():
    """A seemingly successful fit cannot save a contradictory initial scale."""
    check = make_check()
    check.result(0.41)
    with pytest.raises(MappingError, match="Final fitted scale"):
        check.result(0.8)


def test_observed_jump_and_invalid_data_fail():
    """Bad tracking stops the procedure before another move can be issued."""
    check = make_check()
    with pytest.raises(MappingError, match="Observed image shift"):
        check.observe(np.array([0, 40, 0]), np.array([300, 0]), (700, 700))
    with pytest.raises(MappingError, match="Non-finite"):
        check.observe(np.array([0, 40, 0]), np.array([float("nan"), 0]), (700, 700))


def test_return_is_not_a_measurement_but_excursion_and_other_axes_remain_bounded():
    """Returning 1.061 mm to the same start does not demand image overlap."""
    check = make_check()
    check.before_move(np.array([0, 1061, 0]), np.zeros(3))
    with pytest.raises(MappingError, match="excursion"):
        check.before_move(np.zeros(3), np.array([0, 2001, 0]))
    with pytest.raises(MappingError, match="non-calibrated axis"):
        check.before_move(np.zeros(3), np.array([0, 0, 1]))


def test_checked_tracker_captures_no_extra_frames(mocker):
    """Each append uses exactly the native capture and forwards that same observation."""
    capture = Mock(return_value=np.zeros((100, 100, 3)))
    get_position = Mock(return_value=(0, 0, 0))
    settle = Mock()
    check = Mock()
    tracker = CheckedTracker(capture, get_position, settle=settle, check=check)
    tracker.image_shape = (100, 100, 3)
    tracker.reset_history()
    mocker.patch.object(tracker, "track_image", return_value=np.zeros(2))
    tracker.append_point()
    capture.assert_called_once()
    settle.assert_called_once()
    get_position.assert_called_once()
    check.observe.assert_called_once()
    assert isinstance(tracker, Tracker)


@pytest.mark.parametrize(
    "bad",
    [
        {"unverified_step_mm": 0},
        {"max_excursion_mm": float("inf")},
        {"max_step_frame_fraction": 0.5},
        {"scale_tolerance": 1},
    ],
)
def test_mapping_check_settings_are_validated(bad):
    """Unsafe or nonsensical settings fail schema validation, before calibration."""
    with pytest.raises(ValidationError):
        MappingMotionChecks(**bad)
