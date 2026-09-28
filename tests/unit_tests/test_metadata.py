"""Tests that captures have the expected metadata."""

import json
from datetime import datetime
from pathlib import Path

import piexif
import pytest
from PIL import Image

import labthings_fastapi as lt
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.camera import (
    picamera_tuning_file_utils as tf_utils,
)
from openflexure_microscope_server.things.camera.simulation import SimulatedCamera
from openflexure_microscope_server.things.scanning.background_detect import (
    ChannelDeviationLUV,
)
from openflexure_microscope_server.things.stage.dummy import DummyStage

from ..shared_utils.lt_test_utils import LabThingsTestEnv


@pytest.fixture
def temp_jpeg(tmp_path: Path) -> Path:
    """Create a temporary blank JPEG image."""
    jpeg_path = tmp_path / "test.jpg"
    image = Image.new("RGB", (10, 10), color="white")
    image.save(jpeg_path, "jpeg")
    return jpeg_path


def test_add_metadata_to_capture(temp_jpeg):
    """Use a SimulatedCamera to add metadata to a tmp capture and test fields."""
    cam = create_thing_without_server(SimulatedCamera)
    metadata = {
        "Dummy1": 1,
        "Dummy2": "two",
    }

    current_time = datetime.now()
    capture_metadata = {
        "capture_time": current_time.timestamp(),
        "timezone": current_time.astimezone().utcoffset(),
        "make": "OpenFlexure",
        "model": "OpenFlexure Microscope",
        "things_states": metadata,
    }

    cam._add_metadata_to_capture(str(temp_jpeg), capture_metadata)

    # Reload EXIF
    exif_dict = piexif.load(str(temp_jpeg))

    # Assert UserComment
    user_comment_raw = exif_dict["Exif"][piexif.ExifIFD.UserComment]
    assert json.loads(user_comment_raw.decode("utf-8")) == metadata

    # Assert timestamps
    expected_time_str = datetime.fromtimestamp(
        capture_metadata["capture_time"]
    ).strftime("%Y:%m:%d %H:%M:%S")

    assert (
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal].decode() == expected_time_str
    )

    assert (
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized].decode()
        == expected_time_str
    )

    assert exif_dict["0th"][piexif.ImageIFD.DateTime].decode() == expected_time_str

    # Assert timezone offset
    offset_original = exif_dict["Exif"][piexif.ExifIFD.OffsetTimeOriginal].decode()
    offset_digitized = exif_dict["Exif"][piexif.ExifIFD.OffsetTimeDigitized].decode()

    tz = capture_metadata["timezone"]
    hours = int(tz.total_seconds() // 3600)
    minutes = int((abs(tz.total_seconds()) % 3600) // 60)
    sign = "+" if hours >= 0 else "-"
    expected_offset = f"{sign}{abs(hours):02d}:{minutes:02d}"

    assert offset_original == expected_offset
    assert offset_digitized == expected_offset

    # Assert Make and Model
    assert exif_dict["0th"][piexif.ImageIFD.Make].decode() == capture_metadata["make"]
    assert exif_dict["0th"][piexif.ImageIFD.Model].decode() == capture_metadata["model"]


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


def test_add_metadata_to_simulated_capture(camera, temp_jpeg):
    """Test metadata from simulated camera includes expected camera_board: simulator."""
    metadata = camera.thing_state
    current_time = datetime.now()
    capture_metadata = {
        "capture_time": current_time.timestamp(),
        "timezone": current_time.astimezone().utcoffset(),
        "make": "OpenFlexure",
        "model": "OpenFlexure Microscope",
        "things_states": metadata,
    }

    camera._add_metadata_to_capture(str(temp_jpeg), capture_metadata)

    # Reload EXIF
    exif_dict = piexif.load(str(temp_jpeg))

    # Assert UserComment
    user_comment_raw = exif_dict["Exif"][piexif.ExifIFD.UserComment]
    assert json.loads(user_comment_raw.decode("utf-8")) == metadata
    assert json.loads(user_comment_raw.decode("utf-8"))["camera"] == "SimulatedCamera"


def test_picamera_adds_metadata(mock_picam_thing):
    """Test PiCamera adds camera_board and tuning metadata."""
    camera = mock_picam_thing

    # Inject controlled values
    camera._camera_board = "imx219"
    camera.exposure_time = 1234
    camera.colour_gains = (1.1, 1.2)
    camera.analogue_gain = 2.5
    camera.tuning = tf_utils.set_gamma_curve(camera.tuning, [0, 0, 5, 50])

    state = camera.thing_state

    # Assert metadata has been set
    assert state["camera_board"] == "imx219"
    assert state["tuning"] == {
        "exposure_time": 1234,
        "colour_gains": (1.1, 1.2),
        "analogue_gain": 2.5,
        "gamma_correction": [0, 0, 5, 50],
    }


def test_picamera_metadata_written_to_exif(mock_picam_thing, temp_jpeg, mocker):
    """Ensure PiCamera metadata is written into JPEG EXIF."""
    camera = mock_picam_thing

    camera._camera_board = "imx219"
    camera.exposure_time = 1234
    camera.colour_gains = (1.1, 1.2)
    camera.analogue_gain = 2.5
    camera.tuning = tf_utils.set_gamma_curve(camera.tuning, [0, 0, 5, 50])

    # Mock the server interface to return the camera's own state
    mock_interface = mocker.Mock()
    mock_interface.get_thing_states.return_value = camera.thing_state
    camera._thing_server_interface = mock_interface

    ofm_metadata = camera._collect_ofm_metadata()
    assert ofm_metadata["make"] == "OpenFlexure"
    assert ofm_metadata["model"] == "OpenFlexure Microscope"

    camera._add_metadata_to_capture(str(temp_jpeg), ofm_metadata)

    exif_dict = piexif.load(str(temp_jpeg))
    user_comment = json.loads(exif_dict["Exif"][piexif.ExifIFD.UserComment].decode())

    assert user_comment["camera"] == "PiCameraV2"
    assert user_comment["camera_board"] == "imx219"
    # gamma_correction keys are cast to strings
    assert user_comment["tuning"] == {
        "exposure_time": 1234,
        "colour_gains": [1.1, 1.2],
        "analogue_gain": 2.5,
        "gamma_correction": [0, 0, 5, 50],
    }
