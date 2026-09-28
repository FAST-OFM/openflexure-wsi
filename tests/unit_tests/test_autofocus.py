"""Tests for the autofoucs logic.

This doesn't check the behaviour of the JPEG shaprness monitor.
"""

from dataclasses import dataclass
from unittest.mock import MagicMock

import numpy as np
import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.focus.autofocus import (
    AutofocusThing,
    JPEGSharpnessMonitor,
    NoFocusFoundError,
    SharpnessMethod,
)


def fake_sharpness_data(
    dz: int, start_z: int, max_loc: int, length: int = 41
) -> tuple[list[float], np.ndarray, np.ndarray]:
    """Create some fake data for the shapeness.

    The highest returned sharpness is closest to max_loc
    """
    # Some fake timestamps
    times = [i / 10 + 100000 for i in range(length)]
    img_dz = dz / (length - 1)
    heights = [round(start_z + i * img_dz) for i in range(length)]
    # Sharpnesses fall off linearly in this model.
    sharpnesses = [10 * dz - abs(max_loc - h) for h in heights]
    return times, np.array(heights), np.array(sharpnesses)


@dataclass
class LoopingAutofocusTestCase:
    """Parameters for the test looping autofocus function."""

    start_z: int
    """Starting z position."""
    max_loc: int
    """Location of the maximum."""
    centre: int
    """Whether the starting position is the centre of the looping autofocus."""
    attempts_expected: int
    """Expected number of autofocus attempts."""
    passes: int
    """Whether looping autofocus is expected to exit without an error."""


# To complete, the max must be in the central 1200, so -600 to 600 when looping
# from -1000 to 1000
LOOPING_AUTOFOCUS_TEST_CASES = [
    # Case 0: Found in loop1 from -1000 to 1000
    LoopingAutofocusTestCase(
        start_z=0, max_loc=550, centre=True, attempts_expected=1, passes=True
    ),
    # Case 1: Just outside the limit in loop1
    LoopingAutofocusTestCase(
        start_z=0, max_loc=650, centre=True, attempts_expected=2, passes=True
    ),
    # Case 2: Found in loop2 from 0 to 2000
    LoopingAutofocusTestCase(
        start_z=0, max_loc=1300, centre=True, attempts_expected=2, passes=True
    ),
    # Case 3: Found in loop1 from 0 to 2000 (as start="base")
    LoopingAutofocusTestCase(
        start_z=0, max_loc=1300, centre=False, attempts_expected=1, passes=True
    ),
    # Case 4: Found in loop2 from -2000 to 0
    LoopingAutofocusTestCase(
        start_z=0, max_loc=-1300, centre=True, attempts_expected=2, passes=True
    ),
    # Case 5: Found in loop8 from 6000 to 8000
    LoopingAutofocusTestCase(
        start_z=0, max_loc=7300, centre=True, attempts_expected=8, passes=True
    ),
    # Case 6: Found in loop10 from 8000 to 10000
    LoopingAutofocusTestCase(
        start_z=0, max_loc=9300, centre=True, attempts_expected=10, passes=True
    ),
    # Case 7: Found in loop10 from 8000 to 10000
    LoopingAutofocusTestCase(
        start_z=0, max_loc=9900, centre=True, attempts_expected=10, passes=False
    ),
]


@pytest.mark.parametrize("testcase", LOOPING_AUTOFOCUS_TEST_CASES)
def test_looping_autofocus(testcase, mocker):
    """Test the high level looping autofocus algorithm."""
    dz = 2000
    autofocus_thing = create_thing_without_server(AutofocusThing, mock_all_slots=True)

    # Make a mock stage where move_absolute abs and relative updates the position counter.
    autofocus_thing._stage.position = {"x": 0, "y": 0, "z": testcase.start_z}

    def set_pos(**kwargs: int) -> None:
        """Move absolute should update position. So make a side effect for the mock."""
        for axis, value in kwargs.items():
            autofocus_thing._stage.position[axis] = value

    def adjust_pos(**kwargs: int) -> None:
        """Move relative should update position. So make a side effect for the mock."""
        # Remove backlash compensation from the dict if it exists.
        kwargs.pop("backlash_compensation", None)
        for axis, value in kwargs.items():
            autofocus_thing._stage.position[axis] += value

    autofocus_thing._stage.move_absolute.side_effect = set_pos
    autofocus_thing._stage.move_relative.side_effect = adjust_pos

    # Make a mock sharpness monitor that can generate sharpness data.
    sharpness_monitor = mocker.MagicMock()
    sharpness_monitor.focus_rel.return_value = (0, 0)

    def return_sharpness(*_args) -> tuple[list[float], np.ndarray, np.ndarray]:
        """Generate sharpnesses based on parameterised input, and mock stage position."""
        return fake_sharpness_data(
            dz=dz,
            start_z=autofocus_thing._stage.position["z"],
            max_loc=testcase.max_loc,
        )

    sharpness_monitor.move_data.side_effect = return_sharpness

    # Mock the context manager call so out MagicMock sharpness monitor is returned.
    mock_context = mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus.JPEGSharpnessMonitor"
    )
    mock_context.return_value.__enter__.return_value = sharpness_monitor
    mock_context.return_value.__exit__.return_value = None

    start = "centre" if testcase.centre else "base"
    if testcase.passes:
        autofocus_thing.looping_autofocus(dz=dz, start=start)
    else:
        with pytest.raises(NoFocusFoundError):
            autofocus_thing.looping_autofocus(dz=dz, start=start)

    assert sharpness_monitor.focus_rel.call_count == testcase.attempts_expected
    assert abs(testcase.max_loc - autofocus_thing._stage.position["z"]) < dz / 40


@pytest.fixture
def mock_camera(mocker):
    """Mock a camera to return fom properties."""
    camera = mocker.MagicMock()
    camera.supports_focus_fom = True
    camera.focus_fom = 123
    return camera


@pytest.fixture
def mock_stage(mocker):
    """Mock a stage to return position."""
    stage = mocker.MagicMock()
    stage.position = {"x": 0, "y": 0, "z": 0}
    return stage


def test_record_defaults_to_method(mock_stage, mock_camera):
    """If record=None, only the selected method is recorded."""
    monitor = JPEGSharpnessMonitor(
        mock_stage,
        mock_camera,
        method=SharpnessMethod.FOCUS_FOM,
    )

    assert monitor.record == SharpnessMethod.FOCUS_FOM


def test_zero_record_defaults_to_method(mock_stage, mock_camera):
    """If record=0, only the selected method is recorded."""
    monitor = JPEGSharpnessMonitor(
        mock_stage,
        mock_camera,
        method=SharpnessMethod.FOCUS_FOM,
        record=0,
    )

    assert monitor.record == SharpnessMethod.FOCUS_FOM


def test_record_must_include_selected_method(mock_stage, mock_camera):
    """The selected autofocus metric must also be recorded."""
    with pytest.raises(ValueError, match="not being recorded"):
        JPEGSharpnessMonitor(
            mock_stage,
            mock_camera,
            method=SharpnessMethod.FOCUS_FOM,
            record=SharpnessMethod.JPEG,
        )


def test_focus_fom_requires_camera_support(mock_stage, mock_camera):
    """FocusFoM recording requires camera support."""
    mock_camera.supports_focus_fom = False

    with pytest.raises(ValueError, match="doesn't support"):
        JPEGSharpnessMonitor(
            mock_stage,
            mock_camera,
            method=SharpnessMethod.FOCUS_FOM,
        )


def test_jpeg_sizes_property_requires_recording(mock_stage, mock_camera):
    """JPEG sizes should error if JPEG recording disabled."""
    monitor = JPEGSharpnessMonitor(
        mock_stage,
        mock_camera,
        method=SharpnessMethod.FOCUS_FOM,
    )

    with pytest.raises(ValueError, match="JPEG sizes are not being recorded"):
        _ = monitor.jpeg_sizes


def test_focus_fom_property_requires_recording(mock_stage, mock_camera):
    """FocusFoM values should error if FOM recording disabled."""
    monitor = JPEGSharpnessMonitor(
        mock_stage,
        mock_camera,
        method=SharpnessMethod.JPEG,
    )

    with pytest.raises(ValueError, match="FocusFoM values are not being recorded"):
        _ = monitor.focus_foms


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (SharpnessMethod.JPEG, [100, 200, 300]),
        (SharpnessMethod.FOCUS_FOM, [1, 5, 2]),
    ],
)
def test_move_data_uses_selected_method(
    method,
    expected,
    mock_stage,
    mock_camera,
):
    """move_data should select the correct sharpness metric."""
    monitor = JPEGSharpnessMonitor(
        mock_stage,
        mock_camera,
        method=method,
        record=SharpnessMethod.JPEG + SharpnessMethod.FOCUS_FOM,
    )

    monitor._jpeg_times = [1.0, 2.0, 3.0]
    monitor._jpeg_sizes = [100, 200, 300]
    monitor._focus_foms = [1, 5, 2]

    monitor._stage_times = [0.5, 3.5]
    monitor._stage_positions = [
        {"z": 0},
        {"z": 100},
    ]

    _, _, sharpnesses = monitor.move_data(0)

    assert sharpnesses.tolist() == expected


def test_monitor_records_both_metrics(mock_stage, mock_camera):
    """Both JPEG and FocusFoM metrics can be recorded together."""
    monitor = JPEGSharpnessMonitor(
        mock_stage,
        mock_camera,
        method=SharpnessMethod.JPEG,
        record=SharpnessMethod.JPEG + SharpnessMethod.FOCUS_FOM,
    )

    monitor._jpeg_sizes.append(999)
    monitor._focus_foms.append(321)

    assert monitor.jpeg_sizes == [999]
    assert monitor.focus_foms == [321]


def test_move_data_uses_focus_fom(mock_stage, mock_camera):
    """Test that move_data returns the chosen metric."""
    monitor = JPEGSharpnessMonitor(
        mock_stage,
        mock_camera,
        method=SharpnessMethod.FOCUS_FOM,
        record=SharpnessMethod.JPEG + SharpnessMethod.FOCUS_FOM,
    )

    # Record both sharpness metrics
    monitor._jpeg_times = [1.0, 2.0, 3.0]
    monitor._jpeg_sizes = [100, 200, 300]
    monitor._focus_foms = [10, 99, 20]

    monitor._stage_times = [0.5, 3.5]
    monitor._stage_positions = [
        {"z": 0},
        {"z": 100},
    ]

    # sharpnesses chosen by the chosen method
    _, _, sharpnesses = monitor.move_data(0)

    # ensure sharpnesses matches focus_foms
    assert sharpnesses.tolist() == [10, 99, 20]


@pytest.mark.parametrize("method", [SharpnessMethod.JPEG, SharpnessMethod.FOCUS_FOM])
@pytest.mark.parametrize(
    ("peak", "expected"),
    [(-7.4588626, -7), (7.4588626, 7), (-7.8, -8), (7.8, 8), (-7.0, -7), (7.0, 7)],
)
def test_sharpest_z_returns_nearest_python_integer(
    mock_stage, mock_camera, method, peak, expected
):
    """Quantize absolute interpolated Z, retaining the selected native metric."""
    monitor = JPEGSharpnessMonitor(mock_stage, mock_camera, method=method)
    monitor._stage_times = [0.0, 1.0]
    monitor._stage_positions = [{"z": -10}, {"z": 10}]
    monitor._jpeg_times = [(peak + 10) / 20]
    monitor._jpeg_sizes = [100]
    monitor._focus_foms = [100.0]

    target = monitor.sharpest_z_on_move(0)

    assert type(target) is int
    assert target == expected


@pytest.mark.parametrize(("start", "stop"), [(1, 3), (3, 1), (-3, -1), (-1, -3)])
@pytest.mark.parametrize("peak_time", [0.01, 0.99])
def test_sharpest_z_rounding_stays_inside_integer_move_endpoints(
    mock_stage, mock_camera, start, stop, peak_time
):
    """A sub-step image near either edge cannot round outside the sampled move."""
    monitor = JPEGSharpnessMonitor(mock_stage, mock_camera)
    monitor._stage_times = [0.0, 1.0]
    monitor._stage_positions = [{"z": start}, {"z": stop}]
    monitor._jpeg_times = [peak_time]
    monitor._jpeg_sizes = [100]

    target = monitor.sharpest_z_on_move(0)

    assert type(target) is int
    assert min(start, stop) <= target <= max(start, stop)


def test_sharpest_z_replays_recorded_white_sweep(mock_stage, mock_camera):
    """Recorded OF-062 sweep must request +1 from base -8, not truncate +0.541."""
    # Exact 15 in-sweep samples from of062-white-check.XjoxfI/white-focus.json;
    # embedded here so the regression needs neither Pi nor an external artifact.
    monitor = JPEGSharpnessMonitor(mock_stage, mock_camera)
    monitor._stage_times = [1788521416.8868577, 1788521418.2110958]
    monitor._stage_positions = [{"z": -8}, {"z": 8}]
    monitor._jpeg_times = [
        1788521416.931645,
        1788521417.016976,
        1788521417.102307,
        1788521417.187642,
        1788521417.272971,
        1788521417.358306,
        1788521417.443637,
        1788521417.528967,
        1788521417.614302,
        1788521417.699637,
        1788521417.784968,
        1788521417.870298,
        1788521417.955633,
        1788521418.040963,
        1788521418.126299,
    ]
    monitor._jpeg_sizes = [
        115912,
        115896,
        115683,
        115010,
        113929,
        113907,
        110647,
        109977,
        107361,
        102031,
        102306,
        100582,
        98086,
        99218,
        99083,
    ]
    _, heights, sharpness = monitor.move_data(0)
    assert len(heights) == 15
    assert np.argmax(sharpness) == 0
    assert heights[0] == pytest.approx(-7.4588626)

    target = monitor.sharpest_z_on_move(0)
    move_to_peak = target - (-8)

    assert type(target) is int
    assert target == -7
    assert type(move_to_peak) is int
    assert move_to_peak == 1


def test_bounded_fast_autofocus_checks_before_each_native_move(mocker):
    """The scan-only hook guards sweep, return and peak without changing the algorithm."""
    autofocus = create_thing_without_server(AutofocusThing, mock_all_slots=True)
    monitor = MagicMock()
    monitor.__enter__.return_value = monitor
    monitor.__exit__.return_value = None
    monitor.focus_rel.side_effect = [(1, 20), (2, 10), (3, 15)]
    monitor.sharpest_z_on_move.return_value = 15
    expected = MagicMock()
    monitor.data_to_array.return_value = expected
    mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus.JPEGSharpnessMonitor",
        return_value=monitor,
    )
    boundaries = []
    confirmed = []
    result = autofocus.fast_autofocus_bounded(
        dz=20,
        start="base",
        before_move=boundaries.append,
        after_move=confirmed.append,
    )
    assert result is expected
    assert boundaries == [
        "white_sweep_up",
        "white_return_to_base",
        "white_move_to_peak",
    ]
    assert confirmed == boundaries
    assert monitor.focus_rel.call_count == 3


def test_bounded_fast_autofocus_exhaustion_sends_no_next_move(mocker):
    """A confirmed slow sweep is not retried and cannot authorize the return leg."""
    autofocus = create_thing_without_server(AutofocusThing, mock_all_slots=True)
    monitor = MagicMock()
    monitor.__enter__.return_value = monitor
    monitor.__exit__.return_value = None
    monitor.focus_rel.return_value = (1, 20)
    monitor.sharpest_z_on_move.return_value = 15
    mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus.JPEGSharpnessMonitor",
        return_value=monitor,
    )
    boundaries = []

    def record_boundary(phase):
        boundaries.append(phase)

    def stop_after_slow_move(phase):
        if phase == "white_sweep_up":
            raise TimeoutError("field deadline")

    with pytest.raises(TimeoutError, match="field deadline"):
        autofocus.fast_autofocus_bounded(
            dz=20,
            start="base",
            before_move=record_boundary,
            after_move=stop_after_slow_move,
        )
    assert boundaries == ["white_sweep_up"]
    monitor.focus_rel.assert_called_once_with(20, block_cancellation=True)
