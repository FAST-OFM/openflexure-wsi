"""Tests for the smart and fast stacking."""

import logging
from random import randint
from typing import Optional
from unittest.mock import Mock

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image
from pydantic import ValidationError

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.scanning.scan_directories import IMAGE_REGEX
from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things.camera import CaptureParams
from openflexure_microscope_server.things.focus.autofocus import (
    EXTRA_STACK_CAPTURES,
    AutofocusThing,
    CaptureInfo,
    NotAPeakError,
    SmartStackParams,
    StackOrigin,
    StackParams,
    _count_turning_points,
    _get_capture_by_id,
    _get_capture_index_by_id,
    _get_peak_turning_point,
)
from openflexure_microscope_server.things.scanning.scan_workflows import FastOFMWorkflow

RANDOM_GENERATOR = np.random.default_rng()


def odd_integers(min_value=0, max_value=1000):
    """Return a hypothesis strategy for odd integers."""
    min_base = (min_value) // 2
    max_base = (max_value - 1) // 2
    # Ensure the range allows at least one odd number
    if min_base > max_base:
        return st.nothing()
    return st.integers(min_value=min_base, max_value=max_base).map(lambda x: 2 * x + 1)


def even_integers(min_value=0, max_value=1000):
    """Return a hypothesis strategy for even integers."""
    min_base = (min_value + 1) // 2
    max_base = (max_value) // 2
    # Ensure the range allows at least one even number
    if min_base > max_base:
        return st.nothing()
    return st.integers(min_value=min_base, max_value=max_base).map(lambda x: 2 * x)


@given(
    save_ims=odd_integers(min_value=1, max_value=9),
    extra_ims=even_integers(min_value=0, max_value=10),
)
def test_stack_params_validation(save_ims, extra_ims):
    """Test the validation of the image numbers for a stack for valid combinations.

    save_ims is the number to save (must be odd and positive)
    extra_ims is how many more images there are in min_images_to_test than
    images_to_save. (even so that, min_images_to_test is odd and larger than
    images_to_save
    """
    # Coerce min_images_to_test as the max extra ims depends on save_ims so is hard
    # to do automatically in hypothesis. This clamps the number between 3 and 9.
    min_images_to_test = max(min(save_ims + extra_ims, 9), 3)
    SmartStackParams(
        stack_dz=50, images_to_save=save_ims, min_images_to_test=min_images_to_test
    )


@given(
    save_ims=odd_integers(min_value=0, max_value=10),
    extra_ims=even_integers(min_value=-10, max_value=-1),
)
def test_stack_params_not_enough_test_images(save_ims, extra_ims):
    """Test error is raised if min_images_to_test is smaller than images_to_save.

    ``extra_ims`` negative so that min_images_to_test is smaller than images_to_save.

    For arguments see test_stack_params_validation
    """
    # Depending on the values multiple messages are possible
    match = (
        "(Can't test for focus with fewer than 3 images|"
        "Can't save more images than the minimum number tested)"
    )
    with pytest.raises(ValueError, match=match):
        SmartStackParams(
            stack_dz=50,
            images_to_save=save_ims,
            min_images_to_test=save_ims + extra_ims,
        )


@given(
    save_ims=odd_integers(min_value=-10, max_value=-1),
    extra_ims=even_integers(min_value=0, max_value=10),
)
def test_stack_params_negative_images_to_save(save_ims, extra_ims):
    """save_ims is negative so images_to_save is negative, failing validation.

    For arguments see test_stack_params_validation
    """
    # Depending on the values multiple messages are possible
    match = (
        "(Can't test for focus with fewer than 3 images|"
        "Images to save must be positive and odd|"
        "Input should be greater than 0)"
    )
    with pytest.raises(ValueError, match=match):
        SmartStackParams(
            stack_dz=50,
            images_to_save=save_ims,
            min_images_to_test=save_ims + extra_ims,
        )


@given(
    save_ims=odd_integers(min_value=0, max_value=10),
    extra_ims=odd_integers(min_value=0, max_value=10),
)
def test_even_min_images_to_test(save_ims, extra_ims):
    """extra_ims is odd so min_images_to_test is even, failing validation.

    For arguments see test_stack_params_validation
    """
    # Depending on the values multiple messages are possible
    match = (
        "(Can't test for focus with fewer than 3 images|"
        "Testing with more than 9 images is|"  # may give more than 9 which errors first
        "Minimum number of images to test should be positive and odd)"
    )
    with pytest.raises(ValueError, match=match):
        SmartStackParams(
            stack_dz=50,
            images_to_save=save_ims,
            min_images_to_test=save_ims + extra_ims,
        )


@given(
    save_ims=even_integers(min_value=0, max_value=10),
    extra_ims=odd_integers(min_value=0, max_value=10),
)
def test_even_images_to_save(save_ims, extra_ims):
    """save_ims is even so images_to_save is even, failing validation.

    For arguments see test_stack_params_validation
    """
    match = (
        "(Can't test for focus with fewer than 3 images|"
        "Images to save must be positive and odd|"
        "Input should be greater than 0)"
    )
    with pytest.raises(ValueError, match=match):
        SmartStackParams(
            stack_dz=50,
            images_to_save=save_ims,
            min_images_to_test=save_ims + extra_ims,
        )


def test_computed_stack_params():
    """Test SmartStackParams computed properties are as expected.

    Not using hypothesis or we will just copy in the same formulas.
    """
    stack_parameters = SmartStackParams(
        stack_dz=50, images_to_save=5, min_images_to_test=9
    )

    assert stack_parameters.stack_z_range == 8 * 50

    assert stack_parameters.steps_undershoot == stack_parameters.img_undershoot * 50

    assert stack_parameters.max_images_to_test == 9 + 15

    sharpnesses = [50, 77, 234, 324, 390, 496, 569, 454, 333, 222, 178, 70]
    max_ind = np.argmax(sharpnesses)
    slice_to_save = stack_parameters.slice_to_save(max_ind)
    # Check the slice corresponds to the index for the 5 images centred on the sharpest
    assert sharpnesses[slice_to_save] == [390, 496, 569, 454, 333]

    # For a failed smart stack the slice may be truncated. Check it truncates correctly
    sharpnesses = [496, 569, 454, 333, 222, 178, 70, 69, 66, 50, 45, 40]
    max_ind = np.argmax(sharpnesses)
    slice_to_save = stack_parameters.slice_to_save(max_ind)
    # Check the slice corresponds to the index for the first 4 images
    assert sharpnesses[slice_to_save] == [496, 569, 454, 333]

    # And again for sharpest at the end
    sharpnesses = [13, 21, 26, 31, 39, 49, 50, 77, 234, 324, 390, 496, 569]
    max_ind = np.argmax(sharpnesses)
    slice_to_save = stack_parameters.slice_to_save(max_ind)
    # Check the slice corresponds to the index for the final 3 images
    assert sharpnesses[slice_to_save] == [390, 496, 569]


def random_capture(set_id: Optional[int] = None):
    """Create a capture with random values.

    :param set_id: Optional, use to set a fixed id rather than a random one
    """
    buffer_id = set_id if set_id is not None else randint(0, 1000)
    return CaptureInfo(
        buffer_id=buffer_id,
        position={
            "x": randint(-100000, 100000),
            "y": randint(-100000, 100000),
            "z": randint(-100000, 100000),
        },
        sharpness=randint(0, 100000),
    )


def test_capture_filename_matches_regex():
    """For 100 random captures check the image always matches the regex."""
    for _ in range(100):
        assert IMAGE_REGEX.search(random_capture().filename)


@given(st.integers(min_value=0, max_value=5000))
def test_retrieval_of_captures(start):
    """For 20 random captures, check each can be retrieved correctly by id."""
    captures = [random_capture(start + i) for i in range(20)]

    for i, capture in enumerate(captures):
        buffer_id = capture.buffer_id
        assert _get_capture_index_by_id(captures, buffer_id) == i
        assert _get_capture_by_id(captures, buffer_id) is capture

    # Check errors are raised when supplying ids that aren't in the list
    with pytest.raises(ValueError, match="No capture has a buffer id of"):
        _get_capture_index_by_id(captures, start - 1)
    with pytest.raises(ValueError, match="No capture has a buffer id of"):
        _get_capture_index_by_id(captures, start + 21)
    with pytest.raises(ValueError, match="No capture has a buffer id of"):
        _get_capture_by_id(captures, start - 1)
    with pytest.raises(ValueError, match="No capture has a buffer id of"):
        _get_capture_by_id(captures, start + 21)


@pytest.fixture
def autofocus_thing():
    """Return an autofocus thing connected to a server."""
    return create_thing_without_server(AutofocusThing, mock_all_slots=True)


@pytest.fixture
def fast_ofm_scan_workflow():
    """Return an autofocus thing connected to a server."""
    workflow = create_thing_without_server(
        FastOFMWorkflow,
        mock_all_slots=True,
    )

    # Minimal CSM setup so all_settings() works
    workflow._csm.image_resolution = (1000, 1000)
    workflow._csm.calibration_required = False
    workflow._csm.convert_image_to_stage_coordinates = lambda x, y: {"x": x, "y": y}

    # And set up camera
    workflow._cam.capture_modes = {"standard": "Mock"}
    workflow._cam._capture_image.return_value = Image.new("RGB", (1000, 1000))

    return workflow


def test_create_stack(fast_ofm_scan_workflow, caplog):
    """Run create stack with default values and check there is no coercion or logging."""
    initial_min_images_to_test = fast_ofm_scan_workflow.stack_min_images_to_test
    initial_images_to_save = fast_ofm_scan_workflow.stack_images_to_save
    with caplog.at_level(logging.INFO):
        stack_params = fast_ofm_scan_workflow.create_smart_stack_params(
            save_on_failure=not fast_ofm_scan_workflow.skip_background
        )

    assert len(caplog.records) == 0
    assert fast_ofm_scan_workflow.stack_min_images_to_test == initial_min_images_to_test
    assert fast_ofm_scan_workflow.stack_images_to_save == initial_images_to_save
    assert (
        stack_params.min_images_to_test
        == fast_ofm_scan_workflow.stack_min_images_to_test
    )
    assert stack_params.images_to_save == fast_ofm_scan_workflow.stack_images_to_save


@pytest.mark.parametrize(
    ("initial_test_ims", "coerced_test_ims", "expected_log_start"),
    [
        (6, 7, "Minimum number of images to test should be odd"),
    ],
)
def test_coercing_stack_test_ims(
    initial_test_ims,
    coerced_test_ims,
    expected_log_start,
    fast_ofm_scan_workflow,
    caplog,
):
    """Run create stack with images to test set to values requiring coercion, and check result."""
    fast_ofm_scan_workflow.stack_min_images_to_test = initial_test_ims

    with caplog.at_level(logging.WARNING):
        stack_params = fast_ofm_scan_workflow.create_smart_stack_params(
            save_on_failure=not fast_ofm_scan_workflow.skip_background
        )

    assert len(caplog.records) == 1
    assert str(caplog.records[0].msg).startswith(expected_log_start)
    # Check the value is coerced in the stack_params
    assert stack_params.min_images_to_test == coerced_test_ims
    # Check that the setting in the Thing was updated to the coerced value
    assert (
        stack_params.min_images_to_test
        == fast_ofm_scan_workflow.stack_min_images_to_test
    )


@pytest.mark.parametrize(
    ("initial_save_ims", "coerced_save_ims", "expected_log_start"),
    [
        (9, 7, "Cannot save 9 images"),
        (4, 5, "Images to save should be odd, setting to 5"),
    ],
)
def test_coercing_stack_save_ims(
    initial_save_ims,
    coerced_save_ims,
    expected_log_start,
    fast_ofm_scan_workflow,
    caplog,
):
    """Run create stack with images to save set to values requiring coercion, and check result."""
    # First set the min images to test to 7
    fast_ofm_scan_workflow.stack_min_images_to_test = 7
    fast_ofm_scan_workflow.stack_images_to_save = initial_save_ims

    with caplog.at_level(logging.WARNING):
        stack_params = fast_ofm_scan_workflow.create_smart_stack_params(
            save_on_failure=not fast_ofm_scan_workflow.skip_background
        )

    assert len(caplog.records) == 1
    assert str(caplog.records[0].msg).startswith(expected_log_start)
    # Check the value is coerced in the stack_params
    assert stack_params.images_to_save == coerced_save_ims
    # Check that the setting in the Thing was updated to the coerced value
    assert stack_params.images_to_save == fast_ofm_scan_workflow.stack_images_to_save


@pytest.mark.parametrize("pass_on", [1, 2, 3, 4])
def test_run_smart_stack(pass_on, fast_ofm_scan_workflow, autofocus_thing, mocker):
    """Test Running smart stack with the stack passing on different attempts."""
    scan_settings, _, _ = fast_ofm_scan_workflow.all_settings(RelativeDataPath("dummy"))
    assert scan_settings.smart_stack_params.max_attempts == 3

    # Set up returns from z-stack
    fake_captures = [
        CaptureInfo(
            buffer_id="first", position={"x": 0, "y": 0, "z": -99}, sharpness=123
        ),
        CaptureInfo(
            buffer_id="pick_me", position={"x": 0, "y": 0, "z": 555}, sharpness=456
        ),
        CaptureInfo(
            buffer_id="last", position={"x": 0, "y": 0, "z": 999}, sharpness=123
        ),
    ]

    successful_return = (True, fake_captures, "pick_me")
    failed_return = (False, fake_captures, "pick_me")
    return_list = [failed_return] * (pass_on - 1) + [successful_return]

    # Mock smart_z_stack and looping_autofocus
    autofocus_thing.smart_z_stack = mocker.Mock(side_effect=return_list)
    autofocus_thing.looping_autofocus = mocker.Mock()

    # Run it
    success, final_z, image_count = autofocus_thing.run_smart_stack(
        stack_parameters=scan_settings.smart_stack_params,
        capture_parameters=scan_settings.capture_params,
        autofocus_parameters=scan_settings.autofocus_params,
    )

    # Only passes if the attempt it passes on is less than max attempts
    assert success == (pass_on <= scan_settings.smart_stack_params.max_attempts)
    # Final z is the one from the id returned by the stack "pick_me"
    assert final_z == 555
    assert image_count == (1 if pass_on < 4 else 0)

    # smart_z_stack should run up until the time it passes. Running no more than
    # max_attempts
    n_stacks = min(pass_on, scan_settings.smart_stack_params.max_attempts)
    assert autofocus_thing.smart_z_stack.call_count == n_stacks
    # Move absolute should be 1 less time that the number of times z_stack_run
    assert autofocus_thing._stage.move_absolute.call_count == n_stacks - 1
    # As should looping autofocus
    assert autofocus_thing.looping_autofocus.call_count == n_stacks - 1

    # Check rest stack is moving to the first image in the stack.
    if n_stacks > 1:
        assert autofocus_thing._stage.move_absolute.call_args.kwargs["z"] == -99

    # Mock called to save image
    assert autofocus_thing._cam.save_from_memory.call_count == (1 if success else 0)


def setup_and_run_smart_z_stack(
    check_returns, check_turning_points, fast_ofm_scan_workflow, autofocus_thing, mocker
):
    """Set up a smart_z_stack, run it, and return the result.

    :param check_returns: The return values from check_stack_result. Note that if this
        is a list, it will be set as a side effect (and should be a list of tuples of
        results). If it a tuple (or anything else), it is set as a return value.
    """
    stack_params = fast_ofm_scan_workflow.create_smart_stack_params(
        save_on_failure=not fast_ofm_scan_workflow.skip_background
    )
    stack_params.settling_time = 0  # Don't settle or tests take forever.

    # Return predictable CaptureInfo objects so mocked capture_ids are valid.
    autofocus_thing.capture_stack_image = mocker.Mock(
        side_effect=[mock_capture(i, 1) for i in range(100)]
    )
    if isinstance(check_returns, list):
        autofocus_thing.check_stack_result = mocker.Mock(side_effect=check_returns)
    else:
        autofocus_thing.check_stack_result = mocker.Mock(return_value=check_returns)
    return autofocus_thing.smart_z_stack(
        stack_parameters=stack_params,
        check_turning_points=check_turning_points,
    )


def test_z_stack_turning_toggle_passed(fast_ofm_scan_workflow, autofocus_thing, mocker):
    """Check that the toggling of turning points is passed to the check."""
    check_returns = ("success", 2)
    for check_turning in [True, False]:
        setup_and_run_smart_z_stack(
            check_returns,
            check_turning,
            fast_ofm_scan_workflow,
            autofocus_thing,
            mocker,
        )
        check_kwargs = autofocus_thing.check_stack_result.call_args.kwargs
        assert check_kwargs["check_turning_points"] == check_turning


def test_z_stack_returns_on_success_and_restart(
    fast_ofm_scan_workflow, autofocus_thing, mocker
):
    """Check that if the check returns success or restart then the stack exits with correct return value."""
    for result in ["success", "restart"]:
        check_returns = (result, 2)
        ret = setup_and_run_smart_z_stack(
            check_returns, True, fast_ofm_scan_workflow, autofocus_thing, mocker
        )
        assert autofocus_thing.check_stack_result.call_count == 1
        # Check the number of images taken is exactly the call count.
        ims_taken = autofocus_thing.capture_stack_image.call_count
        assert ims_taken == fast_ofm_scan_workflow.stack_min_images_to_test
        # And the result is as expected.
        assert ret[0] == (result == "success")


def test_z_stack_exits_if_focus_never_found(
    fast_ofm_scan_workflow, autofocus_thing, mocker
):
    """Check that if the check returns continue the stack exits eventually with a failure."""
    check_returns = ("continue", "mock_id")
    ret = setup_and_run_smart_z_stack(
        check_returns, True, fast_ofm_scan_workflow, autofocus_thing, mocker
    )

    assert autofocus_thing.check_stack_result.call_count == EXTRA_STACK_CAPTURES + 1
    # Check the number of images taken is the maximum possible, set by the min images to
    # test and the number of extra images that can be taken
    ims_taken = autofocus_thing.capture_stack_image.call_count
    max_ims = fast_ofm_scan_workflow.stack_min_images_to_test + EXTRA_STACK_CAPTURES
    assert ims_taken == max_ims
    # And the result is as expected.
    assert not ret[0]


def test_z_stack_return(fast_ofm_scan_workflow, autofocus_thing, mocker):
    """Check z-stack returns as expected for more complex cases the fixed results above."""
    for i in range(2, EXTRA_STACK_CAPTURES):
        check_returns = [("restart" if j == i - 1 else "continue", j) for j in range(i)]
        ret = setup_and_run_smart_z_stack(
            check_returns, True, fast_ofm_scan_workflow, autofocus_thing, mocker
        )
        # Calculate images taken
        images_taken = fast_ofm_scan_workflow.stack_min_images_to_test + i - 1
        assert autofocus_thing.capture_stack_image.call_count == images_taken
        # Check it reports a failure
        assert not ret[0]

        # Repeat ending with a success rather than a failure
        check_returns = [("success" if j == i - 1 else "continue", j) for j in range(i)]
        ret = setup_and_run_smart_z_stack(
            check_returns, True, fast_ofm_scan_workflow, autofocus_thing, mocker
        )
        # Calculate images taken
        assert autofocus_thing.capture_stack_image.call_count == images_taken
        # Check it reports a success
        assert ret[0]


def test_capture_stack_image(autofocus_thing):
    """Check that capture stack image calls the expected functions and returns the expected data."""
    autofocus_thing._stage.position = {"x": 123, "y": 456, "z": 789}
    autofocus_thing._cam.capture_to_memory.return_value = "fake_buffer_id"
    autofocus_thing._cam.grab_jpeg_size.return_value = 54321
    buffer_max = 11

    info = autofocus_thing.capture_stack_image(buffer_max=buffer_max)
    assert autofocus_thing._cam.capture_to_memory.call_count == 1
    assert autofocus_thing._cam.grab_jpeg_size.call_count == 1
    assert info.buffer_id == "fake_buffer_id"
    assert info.position == {"x": 123, "y": 456, "z": 789}
    assert info.sharpness == 54321


def mock_capture(buffer_id: int, sharpness: int) -> CaptureInfo:
    """Create a CaptureInfo instance with a dummy position."""
    return CaptureInfo(
        buffer_id=buffer_id,
        position={"x": 0, "y": 0, "z": buffer_id},
        sharpness=sharpness,
    )


def test_check_stack_single_image_returns_success(autofocus_thing):
    """A single image is always successful."""
    captures = [mock_capture("mock-id", 10)]
    result, cap_id = autofocus_thing.check_stack_result(
        captures, check_turning_points=False
    )
    assert result == "success"
    assert cap_id == "mock-id"


@pytest.mark.parametrize(
    ("sharpnesses", "expected"),
    [
        ([5, 10, 3], "success"),
        ([10, 4, 2], "restart"),
        ([1, 2, 10], "continue"),
    ],
)
def test_check_stack_three_image_logic(sharpnesses, expected, autofocus_thing):
    """For 3 images, success is the highest one is central."""
    captures = [mock_capture(i, s) for i, s in enumerate(sharpnesses)]
    result, _ = autofocus_thing.check_stack_result(captures, check_turning_points=False)
    assert result == expected


def _run_check_stack_with_good_peak(autofocus_thing, count_turnings=False):
    """Run check stack on a good peak that should pass, and return the result.

    This can be used to check how other mocked results of subfunctions affects the
    result.
    """
    # Create an obvious peak that would normally pass.
    sharpnesses = [1, 2, 4, 7, 12, 7, 4, 2, 1]
    captures = [mock_capture(i, s) for i, s in enumerate(sharpnesses)]

    result, cap_id = autofocus_thing.check_stack_result(
        captures, check_turning_points=count_turnings
    )
    # Nothing a mocked function does should change which is the sharpest image.
    assert cap_id == 4
    return result


def test_check_stack_continues_if_no_tuning_point(autofocus_thing, mocker):
    """Check that continue is returned if no turning point is found."""
    # Mock to simulate not finding a peak
    mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus._get_peak_turning_point",
        side_effect=NotAPeakError,
    )
    result = _run_check_stack_with_good_peak(autofocus_thing)
    # Check that the NotAPeakError causes it to continue instead.
    assert result == "continue"


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (-10, "restart"),  # Restart if lower than 1.5 (halfway between im 2 and 3)
        (-1, "restart"),
        (0, "restart"),
        (1, "restart"),
        (1.49, "restart"),
        (1.5, "success"),  # Success up to 6.5 (as we have 9 images, final index is 8)
        (2.5, "success"),
        (4.5, "success"),
        (6.5, "success"),
        (6.51, "continue"),  # Continue if thrung point is after 6.5
        (7, "continue"),
        (8.1, "continue"),
        (123, "continue"),
    ],
)
def test_check_stack_affected_by_turning_point_location(
    location, expected, autofocus_thing, mocker
):
    """Check that the turning point location affects the return as expected."""
    # Mock to give the turning point location specified
    mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus._get_peak_turning_point",
        return_value=location,
    )
    result = _run_check_stack_with_good_peak(autofocus_thing)
    assert result == expected


def test_check_stack_affected_by_number_of_turning_points(autofocus_thing, mocker):
    """Check that the turning point location affects the return as expected."""
    # Set the turning point to the centre
    mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus._get_peak_turning_point",
        return_value=5,
    )
    mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus._count_turning_points",
        return_value=1,
    )
    result = _run_check_stack_with_good_peak(autofocus_thing, count_turnings=True)
    # Successful with 1 peak
    assert result == "success"

    # Change return to be 2 peaks
    mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus._count_turning_points",
        return_value=2,
    )
    result = _run_check_stack_with_good_peak(autofocus_thing, count_turnings=True)
    # Continue with 2 peaks
    assert result == "continue"
    # Unless this check is turned off
    result = _run_check_stack_with_good_peak(autofocus_thing, count_turnings=False)
    assert result == "success"


def test_get_peak_turning_point():
    """Check that the peak fitting returns expected value (or error)."""
    with pytest.raises(NotAPeakError):
        _get_peak_turning_point(np.ones(9))

    linear = np.arange(9)
    u_shape = 2 * (linear - 4) ** 2 + 17
    peak = -2 * (linear - 4) ** 2 + 55

    with pytest.raises(NotAPeakError):
        _get_peak_turning_point(linear)

    with pytest.raises(NotAPeakError):
        _get_peak_turning_point(u_shape)

    # Should be 4 to within a fitting error
    assert abs(_get_peak_turning_point(peak) - 4) < 1e-7


def test_count_turning_points():
    """Check the turing point count works as expected."""
    linear = np.arange(9)
    u_shape = 2 * (linear - 4) ** 2 + 17
    peak = -2 * (linear - 4) ** 2 + 55
    assert _count_turning_points(np.ones(9)) == 0
    assert _count_turning_points(linear) == 0
    assert _count_turning_points(u_shape) == 1
    assert _count_turning_points(peak) == 1

    assert _count_turning_points(np.array([1, 2, 3, 4, 5, 4, 3, 2, 1])) == 1
    # Double peak is 3 points
    assert _count_turning_points(np.array([1, 2, 3, 4, 2, 4, 3, 2, 1])) == 3
    # But only one if the dip isn't prominent
    assert _count_turning_points(np.array([1, 2, 3, 4, 3.8, 4, 3, 2, 1])) == 1


@pytest.fixture
def fake_capture(autofocus_thing):
    """Return a fake capture function using the current stage Z position."""

    def _fake_capture(*_args, **_kwargs):
        z = autofocus_thing._stage.position["z"]
        return CaptureInfo(
            buffer_id=f"id_{z}",
            position={"x": 0, "y": 0, "z": z},
            sharpness=1,
        )

    return _fake_capture


@pytest.fixture
def fake_move_relative(autofocus_thing):
    """Return a fake move_relative function updating the stage Z position."""

    def _fake_move_relative(z, **_kwargs):
        autofocus_thing._stage.position["z"] += z

    return _fake_move_relative


def test_run_basic_stack_simple(
    autofocus_thing, mocker, fake_capture, fake_move_relative
):
    """Basic stack captures the correct number of images at correct Z positions."""
    # Stack parameters: small 3-image stack, 10-step spacing
    stack_params = StackParams(
        stack_dz=10,
        images_to_save=3,
        settling_time=0,
        origin=StackOrigin.START,
    )

    # Capture parameters for the test
    capture_params = mocker.Mock()
    capture_params.images_dir = "dummy"
    capture_params.save_resolution = (100, 100)

    # Reset stage position
    start_z = 0
    autofocus_thing._stage.position = {"x": 0, "y": 0, "z": start_z}

    # Patch capture and stage movement
    autofocus_thing.capture_stack_image = mocker.Mock(side_effect=fake_capture)
    autofocus_thing._stage.move_relative = mocker.Mock(side_effect=fake_move_relative)
    autofocus_thing._cam.save_from_memory = mocker.Mock()
    autofocus_thing._cam.clear_buffers = mocker.Mock()

    final_z, z_positions = autofocus_thing.run_basic_stack(
        stack_parameters=stack_params,
        capture_parameters=capture_params,
    )

    # Expected Z positions for the stack
    expected_z_positions = [
        start_z + i * stack_params.stack_dz for i in range(stack_params.images_to_save)
    ]
    assert z_positions == expected_z_positions, (
        "Z positions captured do not match expected values"
    )

    # Final Z should be the last captured Z
    expected_final_z = expected_z_positions[-1]
    assert final_z == expected_final_z, "Final Z position is incorrect"

    # Check that capture_stack_image was called exactly images_to_save times
    assert (
        autofocus_thing.capture_stack_image.call_count == stack_params.images_to_save
    ), "Incorrect number of captures"

    # Check that stage moved correctly (should match relative increments)
    moves = [
        call.kwargs["z"] for call in autofocus_thing._stage.move_relative.call_args_list
    ]
    expected_moves = [stack_params.stack_dz] * (stack_params.images_to_save - 1)
    assert moves == expected_moves, (
        "Stage move_relative calls do not match expected increments"
    )


def test_run_basic_stack_center_origin(
    autofocus_thing, mocker, fake_capture, fake_move_relative
):
    """Stack should shift start position when origin is CENTER.

    CENTER should cause the stage to move down by half the z range before
    the stack begins.
    """
    stack_params = StackParams(
        stack_dz=10,
        images_to_save=5,
        settling_time=0,
        origin=StackOrigin.CENTER,
    )

    capture_params = mocker.Mock()
    capture_params.images_dir = "dummy"
    capture_params.save_resolution = (100, 100)

    start_z = 0
    autofocus_thing._stage.position = {"x": 0, "y": 0, "z": start_z}

    # Patch capture and stage movement
    autofocus_thing.capture_stack_image = mocker.Mock(side_effect=fake_capture)
    autofocus_thing._stage.move_relative = mocker.Mock(side_effect=fake_move_relative)
    autofocus_thing._cam.save_from_memory = mocker.Mock()
    autofocus_thing._cam.clear_buffers = mocker.Mock()

    # Run stack
    final_z, z_positions = autofocus_thing.run_basic_stack(
        stack_parameters=stack_params,
        capture_parameters=capture_params,
    )

    # Calculate expected starting offset - half of stack range
    total_range = stack_params.stack_dz * (stack_params.images_to_save - 1)
    expected_offset = -total_range // 2

    # First move should apply center offset
    first_call = autofocus_thing._stage.move_relative.call_args_list[0]
    assert first_call.kwargs["z"] == expected_offset, (
        "Center origin offset not applied correctly"
    )

    # Expected Z positions after CENTER offset
    expected_z_positions = [
        expected_offset + i * stack_params.stack_dz
        for i in range(stack_params.images_to_save)
    ]
    assert z_positions == expected_z_positions, (
        "Z positions captured do not match expected values"
    )

    # Final Z should be last captured Z
    expected_final_z = expected_z_positions[-1]
    assert final_z == expected_final_z, "Final Z position is incorrect"


def test_run_basic_stack_end_origin(
    autofocus_thing, mocker, fake_capture, fake_move_relative
):
    """END origin should shift stack down by full stack height before starting."""
    stack_params = StackParams(
        stack_dz=10,
        images_to_save=4,
        settling_time=0,
        origin=StackOrigin.END,
    )

    capture_params = mocker.Mock()
    capture_params.images_dir = "dummy"
    capture_params.save_resolution = (100, 100)

    start_z = 0
    autofocus_thing._stage.position = {"x": 0, "y": 0, "z": start_z}

    autofocus_thing.capture_stack_image = mocker.Mock(side_effect=fake_capture)
    autofocus_thing._stage.move_relative = mocker.Mock(side_effect=fake_move_relative)
    autofocus_thing._cam.save_from_memory = mocker.Mock()
    autofocus_thing._cam.clear_buffers = mocker.Mock()

    autofocus_thing.run_basic_stack(stack_params, capture_params)

    # Calculate the stack offset based on StackOrigin.END
    total_range = stack_params.stack_dz * (stack_params.images_to_save - 1)
    expected_first_move = -total_range
    first_call = autofocus_thing._stage.move_relative.call_args_list[0]
    assert first_call.kwargs["z"] == expected_first_move, (
        "End origin offset not applied correctly"
    )

    # Check number of captures
    assert (
        autofocus_thing.capture_stack_image.call_count == stack_params.images_to_save
    ), "Incorrect number of captures for END origin"

    # Check final Z is equal to starting Z
    final_z = autofocus_thing._stage.position["z"]
    expected_final_z = start_z
    assert final_z == expected_final_z, "Final Z position for END origin incorrect"


def test_invalid_stack_images_raises():
    """Test basic stack raises expected error for negative or zero image count."""
    for capture_count in [-3, 0]:
        with pytest.raises(ValueError, match="Input should be greater than 0"):
            StackParams(
                stack_dz=10,
                images_to_save=capture_count,
                settling_time=0,
                origin=StackOrigin.START,
            )


def test_invalid_stack_settling_raises():
    """Test basic stack raises expected error for negative settling time."""
    with pytest.raises(ValueError, match="Input should be greater than or equal to 0"):
        StackParams(
            stack_dz=10,
            images_to_save=1,
            settling_time=-1,
            origin=StackOrigin.START,
        )


@pytest.mark.parametrize(
    ("bad_path", "error_type"),
    [
        (None, TypeError),
        (67, TypeError),
        ("../dangerous", ValidationError),
        ("/usr/bin/bash", ValidationError),
    ],
)
def test_invalid_capture_dir_raises(bad_path, error_type):
    """Test basic stack raises expected error for bad image dir paths."""
    with pytest.raises(error_type):
        CaptureParams(images_dir=bad_path, capture_mode="foo")


def test_smart_z_stack_does_not_capture_extra_if_centered(
    fast_ofm_scan_workflow, autofocus_thing, mocker
):
    """Test a z stack with the sharpest image in the centre doesn't capture more images."""
    stack_params = fast_ofm_scan_workflow.create_smart_stack_params(
        save_on_failure=False
    )
    stack_params.images_to_save = 3
    stack_params.min_images_to_test = 5
    stack_params.settling_time = 0

    autofocus_thing.capture_stack_image = mocker.Mock(
        side_effect=[mock_capture(i, 10) for i in range(20)]
    )

    autofocus_thing.check_stack_result = mocker.Mock(return_value=("success", 2))

    autofocus_thing.smart_z_stack(
        stack_parameters=stack_params,
        check_turning_points=False,
    )

    # Only the minimum test images should have been captured
    assert autofocus_thing.capture_stack_image.call_count == 5


def test_smart_z_stack_captures_extra_images_after_success(
    fast_ofm_scan_workflow, autofocus_thing, mocker
):
    """Test that the sharpest image being too close to the end captures more images."""
    stack_params = fast_ofm_scan_workflow.create_smart_stack_params(
        save_on_failure=False
    )
    stack_params.images_to_save = 5
    stack_params.min_images_to_test = 5
    stack_params.settling_time = 0

    captures = [mock_capture(i, 1) for i in range(20)]

    autofocus_thing.capture_stack_image = mocker.Mock(side_effect=captures)

    # fourth image is sharpest
    autofocus_thing.check_stack_result = mocker.Mock(return_value=("success", 3))

    autofocus_thing.smart_z_stack(
        stack_parameters=stack_params,
        check_turning_points=False,
    )

    # 5 initial + 1 additional
    assert autofocus_thing.capture_stack_image.call_count == 6


def test_smart_z_stack_uses_safe_buffer_size(
    fast_ofm_scan_workflow, autofocus_thing, mocker
):
    """Test buffer size is set correctly from images to test and capture."""
    stack_params = fast_ofm_scan_workflow.create_smart_stack_params(
        save_on_failure=False
    )
    stack_params.images_to_save = 5
    stack_params.min_images_to_test = 5
    stack_params.settling_time = 0

    autofocus_thing.capture_stack_image = mocker.Mock(
        side_effect=[mock_capture(i, 1) for i in range(30)]
    )

    autofocus_thing.check_stack_result = mocker.Mock(return_value=("success", 2))

    autofocus_thing.smart_z_stack(
        stack_params,
        check_turning_points=False,
    )

    expected = max(
        stack_params.images_to_save,
        stack_params.min_images_to_test + stack_params.images_to_save // 2,
    )

    for call in autofocus_thing.capture_stack_image.call_args_list:
        assert call.kwargs["buffer_max"] == expected


@st.composite
def smart_stack_scenarios(draw):
    """Generate valid (min_images_to_test, images_to_save, sharpest_index) combos.

    min_images_to_test must be odd and within [MIN_TEST_IMAGE_COUNT, MAX_TEST_IMAGE_COUNT].
    images_to_save must be odd and <= min_images_to_test.
    sharpest_index is the position of the sharpest capture within the first
    min_images_to_test images (i.e. at the moment check_stack_result first fires).
    """
    min_images_to_test = draw(st.sampled_from([3, 5, 7, 9]))
    valid_saves = [n for n in range(1, min_images_to_test + 1) if n % 2 == 1]
    images_to_save = draw(st.sampled_from(valid_saves))
    sharpest_index = draw(st.integers(min_value=0, max_value=min_images_to_test - 1))
    return min_images_to_test, images_to_save, sharpest_index


@settings(max_examples=100, deadline=None)
@given(scenario=smart_stack_scenarios())
def test_smart_z_stack_capture_count_matches_expected(scenario):
    """Test using hypothesis that the right number of images is always captured.

    For any valid (min_images_to_test, images_to_save, sharpest position),
    smart_z_stack must capture exactly min_images_to_test images plus only
    the extra trailing images needed to symmetrically save around the
    sharpest image and must always request a
    buffer large enough to hold the full save window.
    """
    min_images_to_test, images_to_save, sharpest_index = scenario

    # Call the fixtures' underlying functions directly so each Hypothesis
    # example gets a fresh instance
    workflow = fast_ofm_scan_workflow.__wrapped__()
    thing = autofocus_thing.__wrapped__()

    stack_params = workflow.create_smart_stack_params(save_on_failure=False)
    stack_params.images_to_save = images_to_save
    stack_params.min_images_to_test = min_images_to_test
    stack_params.settling_time = 0

    images_each_side = images_to_save // 2
    supply = min_images_to_test + images_each_side + 5

    thing.capture_stack_image = Mock(
        side_effect=[mock_capture(i, 1) for i in range(supply)]
    )
    sharpest_buffer_id = sharpest_index
    thing.check_stack_result = Mock(return_value=("success", sharpest_buffer_id))

    success, captures, capture_id = thing.smart_z_stack(
        stack_parameters=stack_params,
        check_turning_points=False,
    )

    assert success is True
    assert capture_id == sharpest_buffer_id

    current_trailing = (min_images_to_test - 1) - sharpest_index
    shortfall = max(0, images_each_side - current_trailing)
    expected_capture_count = min_images_to_test + shortfall

    assert thing.capture_stack_image.call_count == expected_capture_count
    assert len(captures) == expected_capture_count

    trailing_available = len(captures) - 1 - sharpest_index
    assert trailing_available == max(current_trailing, images_each_side)

    expected_buffer_max = max(images_to_save, min_images_to_test + images_each_side)
    for call in thing.capture_stack_image.call_args_list:
        assert call.kwargs["buffer_max"] == expected_buffer_max
