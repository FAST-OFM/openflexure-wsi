"""Test the the simulated camera.

This includes both unit tests for functions specific to the simulation camera or
base camera functionality that is not camera specific, but requires the camera to
start and take images.
"""

import logging
import os
import time
from io import BytesIO

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from PIL import Image
from pydantic import ValidationError

import labthings_fastapi as lt

from openflexure_microscope_server.things.camera import simulation
from openflexure_microscope_server.things.camera.simulation import SimulatedCamera
from openflexure_microscope_server.things.scanning.background_detect import (
    ChannelDeviationLUV,
)
from openflexure_microscope_server.things.stage.dummy import DummyStage

from ..shared_utils.lt_test_utils import LabThingsTestEnv


@pytest.fixture
def test_env() -> LabThingsTestEnv:
    """Yield a test environment with the Simulated Camera and Dummy Stage."""
    thing_conf = {
        "camera": SimulatedCamera,
        "stage": DummyStage,
        "bg_channel_deviations_luv": ChannelDeviationLUV,
    }

    with LabThingsTestEnv(things=thing_conf) as env:
        yield env


@pytest.fixture
def camera(test_env) -> lt.Thing:
    """Return the SimulatedCamera Thing set up in the test environment."""
    return test_env.get_thing_by_type(SimulatedCamera)


@pytest.fixture
def stage(test_env) -> lt.Thing:
    """Return the DummyStage Thing set up in the test environment."""
    return test_env.get_thing_by_type(DummyStage)


def test_snapshot(test_env) -> lt.Thing:
    """Check the snapshot GET request returns an image."""
    http_client = test_env.get_thing_client("camera").client
    response = http_client.get("camera/snapshot")
    response.raise_for_status()
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(BytesIO(response.content))

    assert image.format == "JPEG"
    assert image.width == 320
    assert image.height == 240


def test_downsample_shape_2d():
    """Test downsampling for 2D array."""
    shape_2d = (100, 80)
    result_2d = simulation._downsample_shape(shape_2d, simulation.DOWNSAMPLE)
    assert len(result_2d) == 2
    assert result_2d == (100 // simulation.DOWNSAMPLE, 80 // simulation.DOWNSAMPLE)


def test_downsample_shape_3d():
    """Test downsampling for 3D array, should not affect 3rd axis or shape."""
    shape_3d = (120, 60, 3)
    result_3d = simulation._downsample_shape(shape_3d, simulation.DOWNSAMPLE)
    assert len(result_3d) == 3
    assert result_3d == (120 // simulation.DOWNSAMPLE, 60 // simulation.DOWNSAMPLE, 3)


def test_downsample_shape_invalid_length():
    """Shapes that are not length 2 or 3 should raise ValueError."""
    with pytest.raises(ValueError, match="Shape should be a 2 or 3 element tuple."):
        simulation._downsample_shape((1,), simulation.DOWNSAMPLE)

    with pytest.raises(ValueError, match="Shape should be a 2 or 3 element tuple."):
        simulation._downsample_shape((1, 2, 3, 4), simulation.DOWNSAMPLE)


def all_colours_present(
    col_str: str, colours: list[tuple[int, int, int]], tries: int = 100
) -> bool:
    """Check that for a given colour string that all listed colours are returned.

    A helper function for testing simulation.colour_str_to_colour. As the result is
    randomised the test just tries multiple times. In theory could fail, so pick
    tries high enough if there are lots of colours in col_str.
    """
    found = [False for _c in colours]
    for _i in range(tries):
        col_tuple = simulation.colour_str_to_colour(col_str)
        if col_tuple not in colours:
            raise ValueError("Unexpected colour returned.")
        found[colours.index(col_tuple)] = True
        # Don't exit early or we don't confirm that extra colours are not returned.
    return all(found)


def test_colour_str_to_colour():
    """Test colour_str_to_colour with some basic predefined test cases."""
    # A basic test to convert a single str to the expected colour
    assert simulation.colour_str_to_colour("#123456") == (0x12, 0x34, 0x56)
    # A basic test with a trailing semicolon and surrounding spacing
    assert simulation.colour_str_to_colour("  #123456 ; ") == (0x12, 0x34, 0x56)
    # A 2 colour test
    assert all_colours_present(
        "#123456; #654321", [(0x12, 0x34, 0x56), (0x65, 0x43, 0x21)]
    )
    # A failure_test (to check the helper function works!)
    with pytest.raises(ValueError, match="Unexpected colour returned."):
        assert all_colours_present(
            "#123456; #654321", [(0x12, 0x34, 0x56), (0x11, 0x11, 0x11)]
        )
    # And some incorrect strings that should fire an error in colour_str_to_colour
    bad_colours = ["foobar", "pink", "#123", "#123456, #654321"]
    for colour_str in bad_colours:
        with pytest.raises(
            ValueError, match=r".*not a valid colour. Please use HTML hex notation."
        ):
            simulation.colour_str_to_colour(colour_str)


# Regex generation is intentionally expensive under architecture emulation;
# preserve the generated examples and assertions while disabling only its timer.
@settings(suppress_health_check=[HealthCheck.too_slow])
@given(st.from_regex(simulation.COLOUR_LIST_REGEX, fullmatch=True))
def test_colour_list_regex(colour_str):
    """Check that anything matching the regex doesn't error when generating colours.

    This will error if splitting colour_str into individual colours produces
    an incorrect colour. Trying 100 times for each colour_str as the returned colour
    is randomised. Hypothesis will try to create the strings that match the regex but
    break the test.
    """
    for _ in range(100):
        simulation.colour_str_to_colour(colour_str)


def test_canvas_regeneration(camera, caplog):
    """Check canvas is regenerated if blob density or colour are changed."""
    cached_canvas = camera.canvas
    original_colour = camera.colour

    # First try a bad colour string
    with caplog.at_level(logging.WARNING):
        camera.colour = "foobar"
    assert len(caplog.messages) == 1
    assert caplog.messages[0] == "foobar is not a valid colour string."
    # Value and canvas unchanged
    assert camera.colour == original_colour
    assert camera.canvas is cached_canvas

    # Set a valid colour
    camera.colour = "#123456"
    assert camera.colour == "#123456"
    # canvas updated
    assert camera.canvas is not cached_canvas

    # Cache again
    cached_canvas = camera.canvas
    camera.blob_density = 321
    assert camera.blob_density == 321
    # Canvas updated again
    assert camera.canvas is not cached_canvas


def test_infinite_sample(camera, stage):
    """Check that setting camera.repeating makes the sample infinite."""
    # Turn off noise to make comparison easier
    camera.noise_level = 0
    assert not camera.repeating
    cached_canvas = camera.canvas
    array_not_repeating = camera.capture_as_array()
    camera.repeating = True
    time.sleep(0.2)  # Ensure frame regenerates
    # Canvas shouldn't regenerate
    assert camera.canvas is cached_canvas
    array_repeating = camera.capture_as_array()
    # Images are identical whether or not repeating
    assert np.array_equal(array_not_repeating, array_repeating)

    # Move outside the non-repeating sample area
    stage._hardware_position["x"] = 100_000_000

    camera.repeating = False
    time.sleep(0.2)  # Ensure frame regenerates
    # If not repeating the array is just background
    assert np.all(camera.capture_as_array() == simulation.BG_COLOR)

    # Turn on repeating
    camera.repeating = True
    time.sleep(0.2)  # Ensure frame regenerates
    # Sample is now infinite, so not all background
    assert not np.all(camera.capture_as_array() == simulation.BG_COLOR)


def test_simulation_cam_calibration(camera):
    """Test that the simulated camera can be calibrated and reports calibration correctly."""
    assert camera.calibration_required
    camera.full_auto_calibrate()
    assert not camera.calibration_required
    assert camera.background_detector.ready


def test_objective_getter_setter(camera):
    """Verify that the objective property can be set and read.

    - Defaults to 40x.
    - Accepts only valid magnification values (4, 10, 20, 40, 60, 100).
    - Raises ValueError for invalid magnifications.
    """
    # Default value
    assert camera.objective == 40

    # Valid values
    for val in (4, 10, 20, 40, 60, 100):
        camera.objective = val
        assert camera.objective == val

    err_msg = "Objective must be one of 4, 10, 20, 40, 60, 100."
    # Invalid values should raise
    with pytest.raises(ValueError, match=err_msg):
        camera.objective = 15
    with pytest.raises(ValueError, match=err_msg):
        camera.objective = 0
    with pytest.raises(ValidationError):
        camera.objective = "twenty"


def test_generate_image_changes_with_objective(camera):
    """Changing the objective should change the generated image.

    Higher magnification should produce a more zoomed-in image
    (different pixel content compared to lower magnification).
    """
    pos = (0, 0, 0)
    camera.noise_level = 0  # eliminate randomness

    # Generate images at 3 magnifications
    camera.objective = 10
    img_10 = np.array(camera.generate_image(pos))

    camera.objective = 40
    img_40 = np.array(camera.generate_image(pos))

    camera.objective = 100
    img_100 = np.array(camera.generate_image(pos))

    # Images at different objectives should not be identical
    assert not np.array_equal(img_10, img_40)
    assert not np.array_equal(img_40, img_100)

    # Generate 3 more images at these 3 magnifications
    camera.objective = 10
    img_10_im2 = np.array(camera.generate_image(pos))

    camera.objective = 40
    img_40_im2 = np.array(camera.generate_image(pos))

    camera.objective = 100
    img_100_im2 = np.array(camera.generate_image(pos))

    # Image should return to an identical value
    assert np.array_equal(img_10, img_10_im2)
    assert np.array_equal(img_40, img_40_im2)
    assert np.array_equal(img_100, img_100_im2)


def test_generate_image_output_size(camera):
    """Generated image doesn't change with objective."""
    pos = (0, 0, 0)

    for objective in (4, 10, 20, 40, 60, 100):
        camera.objective = objective
        img = camera.generate_image(pos)

        assert img.size == (camera.shape[1], camera.shape[0])


def test_gallery_responses(camera, mocker, caplog):
    """Check camera responds to gallery as expected."""
    mock_raise_if_cancelled = mocker.patch(
        "openflexure_microscope_server.things.camera.lt.raise_if_cancelled",
    )

    with caplog.at_level(logging.INFO):
        # When we start there are no captures
        assert camera.gallery_db_engine.get_all_paths() == []

        filenames = [
            "doe.png",
            "ray.png",
            "me.jpg",
            "far.jpeg",
            "so.txt",
            "la.json",
            "tea.leaf",
        ]
        images = filenames[:4]
        non_images = filenames[4:]

        for filename in filenames:
            with open(os.path.join(camera._data_dir, filename), "w") as f_obj:
                f_obj.write("mock")

        # Sync gallery with file system
        camera.gallery_db_engine.sync_db_with_file_system()

        # Check all files written
        assert set(os.listdir(camera._data_dir)) == set(filenames + ["gallery.db"])

        # Check only images are returned by get_data_for_galler
        captures = camera.gallery_db_engine.get_all_paths()
        assert set(captures) == set(images)

        # delete everything, this should automatically update th database
        camera.delete_all_gallery_items()

        # No captures remain
        assert camera.gallery_db_engine.get_all_paths() == []

        # Non-images not deleted
        assert set(os.listdir(camera._data_dir)) == set(non_images + ["gallery.db"])

        assert len(caplog.records) == len(images)
        for record in caplog.records:
            assert record.message.startswith("Deleting: ")
        assert mock_raise_if_cancelled.call_count == len(images)


def test_delete_capture(test_env, caplog):
    """Check camera deletes images when requested."""
    camera = test_env.get_thing_by_type(SimulatedCamera)
    http_client = test_env.get_thing_client("camera").client

    cam_dir = camera._data_dir
    root_data_dir = os.path.dirname(cam_dir)

    filenames = [
        os.path.join(cam_dir, "foo.png"),
        # Make an image outside the camera dir
        os.path.join(root_data_dir, "external.png"),
        os.path.join(cam_dir, "bar.txt"),
    ]

    for filename in filenames:
        with open(filename, "w") as f_obj:
            f_obj.write("mock")
    camera.gallery_db_engine.sync_db_with_file_system()

    # Check files created.
    assert "foo.png" in os.listdir(camera._data_dir)
    assert "bar.txt" in os.listdir(camera._data_dir)
    assert os.path.isfile(os.path.join(root_data_dir, "external.png"))

    response = http_client.delete("camera/capture/foo.png")
    assert response.status_code == 200  # OK
    # File is gone
    assert "foo.png" not in os.listdir(camera._data_dir)
    # No logs
    assert len(caplog.records) == 0

    # Try to be evil and delete the file outside the cameras data dir"
    response = http_client.delete(r"camera/capture/%2E%2E%2Ffoo.png")
    assert response.status_code == 404  # Route not allowed by fastAPI
    # No records
    assert len(caplog.records) == 0

    # Try to delete an image that doesn't exist
    response = http_client.delete("camera/capture/fake.png")
    assert response.status_code == 400  # Error deleting as it doesn't exist
    # A new log
    assert len(caplog.records) == 1

    # Try to delete something that isn't an image.
    response = http_client.delete("camera/capture/bar.txt")
    assert response.status_code == 400  # Error deleting as it isn't and image
    # File still exists.
    assert "bar.txt" in os.listdir(camera._data_dir)
    # A new log
    assert len(caplog.records) == 2
