"""Test the server without creating a full HTTP server and socket connection.

Rather than spinning up a full uvicorn webserver for each test these tests use
the FastAPI ``TestClient`` or directly communicate with the underlying
LabThings-FastAPI code. This increases speed of testing significantly.

For tests that require a full running server see the ``lifecycle_test``
directory in the tests directory.

This file used to be called test_dummy_server when it was in the unit_test suite, but
it has been moved as it artificaially inflated coverage.
"""

import json
import logging

import numpy as np
import piexif
import pytest
from PIL import Image

import labthings_fastapi as lt


def test_autofocus(simulation_test_env):
    """Test Fast Autofocus can run doesn't raise an exception."""
    stage = simulation_test_env.get_thing_client("stage")
    autofocus = simulation_test_env.get_thing_client("autofocus")
    camera = simulation_test_env.get_thing_by_name("camera")
    camera.noise_level = 0  # Keep this integration test deterministic.
    assert stage.position["z"] == 0
    # Autofocus 5 times and check each ends within 500 steps
    for i in range(5):
        autofocus.fast_autofocus()
        assert abs(stage.position["z"]) < 500, f"Autofocus failed on iteration {i}"


def test_grab_jpeg(simulation_test_env):
    """Check that grab_jpeg returns a blob that can be opened."""
    camera = simulation_test_env.get_thing_client("camera")
    blob = camera.grab_jpeg()
    image = Image.open(blob.open())
    assert image.size == (820, 616)


@pytest.mark.parametrize(
    "image_format",
    [
        "jpeg",
        pytest.param(
            "png", marks=pytest.mark.xfail(reason="piexif does not support PNG EXIF")
        ),
    ],
)
def test_capture_and_metadata(simulation_test_env, image_format, caplog):
    """Capture an image and check a attributes.

    - Check that the position is encoded into the image metadata
    - Check the dimensions
    - Check the format
    """
    camera = simulation_test_env.get_thing_client("camera")
    with caplog.at_level(logging.WARNING):
        blob = camera.capture(image_format=image_format)
    assert len(caplog.messages) == 0
    image = Image.open(blob.open())
    exif_dict = piexif.load(image.info["exif"])
    encoded_metadata = exif_dict["Exif"][piexif.ExifIFD.UserComment]
    metadata = json.loads(encoded_metadata)
    assert "position" in metadata["stage"]
    assert metadata["stage"]["position"] == {"x": 0, "y": 0, "z": 0}
    assert image.size == (820, 616)
    assert image.format == image_format.upper()


def test_stage(simulation_test_env):
    """Test moving the stage forwards and backwards."""
    stage = simulation_test_env.get_thing_client("stage")
    start = stage.position
    move = {"x": 1, "y": 2, "z": 3}
    move_back = {"x": -1, "y": -2, "z": -3}
    stage.move_relative(**move)
    pos = stage.position

    assert start["x"] + move["x"] == pos["x"]
    assert start["y"] + move["y"] == pos["y"]
    assert start["z"] + move["z"] == pos["z"]

    # Move back
    stage.move_relative(**move_back)
    pos = stage.position

    # Check nack at start.
    assert start["x"] == pos["x"]
    assert start["y"] == pos["y"]
    assert start["z"] == pos["z"]


def test_capture_as_array(simulation_test_env):
    """Capture array from simulation and check the size is as expected."""
    camera = simulation_test_env.get_thing_client("camera")
    array = np.asarray(camera.capture_as_array())
    assert array.shape == (616, 820, 3)


def test_camera_stage_mapping_calibration(simulation_test_env):
    """Check that camera stage mapping runs and returns the expected result."""
    camera = simulation_test_env.get_thing_client("camera")
    # Remove camera settling time for speed.
    camera.settling_time = 0
    csm = simulation_test_env.get_thing_client("camera_stage_mapping")
    stage = simulation_test_env.get_thing_client("stage")

    # Check it starts uncalibrated
    assert csm.calibration_required
    assert csm.image_to_stage_displacement_matrix is None
    assert csm.last_calibration is None
    # And therefore that actions error
    with pytest.raises(
        lt.exceptions.ServerActionError,
        match=(
            r"Camera Stage Mapping is missing or incompatible with the current "
            r"camera geometry\. Run the calibration routine on tissue\."
        ),
    ):
        csm.move_in_image_coordinates(x=10, y=0)

    # Calibrate
    csm.calibrate_xy()

    assert not csm.calibration_required
    assert csm.image_to_stage_displacement_matrix is not None
    assert isinstance(csm.last_calibration, dict)
    # Check it returned home
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    csm.move_in_image_coordinates(x=10, y=0)
    assert stage.position == {"x": -5, "y": 0, "z": 0}
    csm_matrix = csm.image_to_stage_displacement_matrix
    expected_matrix = [[0, 0.5], [-0.5, 0]]
    assert isinstance(csm_matrix, list)
    # Check CSM is as expected to an absolute tolerance of 1e-3
    assert np.allclose(csm_matrix, expected_matrix, atol=1e-3)


def test_measure_settling_time(simulation_test_env):
    """Test measure_settling_time captures data while moving and holding."""
    autofocus = simulation_test_env.get_thing_client("autofocus")
    stage = simulation_test_env.get_thing_client("stage")

    start_z = stage.position["z"]

    delay = 1
    dz = 1000

    data = autofocus.measure_settling_time(delay=delay, dz=dz)

    # Check returned arrays contain data
    assert len(data["jpeg_times"]) > 0
    assert len(data["jpeg_sizes"]) > 0
    assert len(data["stage_times"]) == 4
    assert len(data["stage_positions"]) == 4

    # Check the stage moved down then back up
    z_positions = [p["z"] for p in data["stage_positions"]]

    assert z_positions[0] == start_z
    assert z_positions[1] == start_z - dz
    assert z_positions[2] == start_z - dz
    assert z_positions[3] == start_z

    # Check final stage position returned to start
    assert stage.position["z"] == start_z

    # Check frames continued to be collected after the final move completed
    final_move_time = data["stage_times"][-1]
    jpeg_times_after_final_move = [t for t in data["jpeg_times"] if t > final_move_time]

    assert len(jpeg_times_after_final_move) > 0


def test_measure_settling_time_hold_duration(simulation_test_env):
    """Test measure_settling_time continues capturing during hold."""
    autofocus = simulation_test_env.get_thing_client("autofocus")

    short_data = autofocus.measure_settling_time(delay=0, dz=400)
    long_data = autofocus.measure_settling_time(delay=1, dz=400)

    # Longer delay should collect more JPEG frames
    assert len(long_data["jpeg_times"]) > len(short_data["jpeg_times"])
    assert len(long_data["jpeg_sizes"]) > len(short_data["jpeg_sizes"])
