"""Use the Simulated camera to test base camera functionality.

For tests of functionality specific to the simulated camera see
test_simulated_camera.py and for testing the consistency of camera APIs see
test_cameras.py.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import piexif
import pytest
from PIL import Image

import labthings_fastapi as lt
from openflexure_stitching.loading import OFSImageSet

from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things import camera as camera_module
from openflexure_microscope_server.things.camera import CaptureMode
from openflexure_microscope_server.things.camera.simulation import SimulatedCamera
from openflexure_microscope_server.things.stage.dummy import DummyStage

from ..shared_utils.lt_test_utils import LabThingsTestEnv


@pytest.fixture
def test_env() -> LabThingsTestEnv:
    """Yield a test environment with the Simulated Camera and Dummy Stage."""
    thing_conf = {"camera": SimulatedCamera, "stage": DummyStage}
    with LabThingsTestEnv(things=thing_conf) as env:
        yield env


def test_handle_broken_frame(test_env):
    """Monkey patch the the mjpeg steam so 1 in 5 frames are broken, then test operation.

    This simulates the very occasional broken frames that can occur when grabbing
    directly from the MJPEG stream.
    """
    camera = test_env.get_thing_by_type(SimulatedCamera)

    # Money patch the mjpeg_stream grab_frame to break 1 in 5 frames.
    frame_number = 0
    original_grabber = camera.mjpeg_stream.grab_frame

    async def flaky_grabber():
        """Break 1 in 5 frames."""
        # Use a non-local variable to know the frame count.
        nonlocal frame_number
        frame = await original_grabber()
        if frame_number % 5 == 2:
            # Make a weird broken frame
            frame = frame[:2000] + frame[:2000]
        frame_number += 1
        return frame

    camera.mjpeg_stream.grab_frame = flaky_grabber

    # Check that this does cause broken frames.
    # The noqa is because we don't know exactly when the error is thrown so we
    # can't have a single simple statement in the pytest raises.
    with pytest.raises(OSError, match="broken data stream when reading image file"):  # noqa PT012
        for _i in range(15):
            jpeg = camera.grab_jpeg()
            np.asarray(Image.open(jpeg.open()))

    # Check that grab_as_array handles the broken frames and completes without
    # the same error.
    for _i in range(15):
        array = camera.grab_as_array()
        assert isinstance(array, np.ndarray)


@dataclass
class MemorySaveTestCase:
    """Inputs and expected outputs for testing ``save_from_memory``.

    The default save kwargs assume a jpeg.
    """

    filename: str = "foobar.jpeg"
    save_resolution: Optional[tuple[int, int]] = None
    resize_needed: bool = False
    convert_needed: bool = False
    save_kwargs: dict[str, int] = field(
        default_factory=lambda: {"quality": 95, "subsampling": 0}
    )


SAVE_TEST_CASES = [
    # Default test case is a jpeg, check it works with all extensions.
    MemorySaveTestCase("foobar.jpeg"),
    MemorySaveTestCase("foobar.jpg"),
    MemorySaveTestCase("foobar.JPEG"),
    MemorySaveTestCase("foobar.JPG"),
    MemorySaveTestCase("foobar.png.jpeg"),
    MemorySaveTestCase("foobar.png", save_kwargs={}, convert_needed=True),
    MemorySaveTestCase("foobar.PNG", save_kwargs={}, convert_needed=True),
    MemorySaveTestCase("foobar.jpeg.png", save_kwargs={}, convert_needed=True),
    MemorySaveTestCase(save_resolution=None, resize_needed=False),
    MemorySaveTestCase(save_resolution=(1000, 1200), resize_needed=False),
    MemorySaveTestCase(save_resolution=(2000, 2400), resize_needed=True),
]


@pytest.mark.parametrize("test_case", SAVE_TEST_CASES)
def test_save_from_memory(test_case, test_env, mocker, tmp_path):
    """Check the correct image is retrieved and saved with correct settings."""
    camera = test_env.get_thing_by_type(SimulatedCamera)
    camera._memory_buffer = mocker.Mock()
    camera._add_metadata_to_capture = mocker.Mock()

    mode = CaptureMode(description="foo", save_resolution=test_case.save_resolution)
    capture_modes_mock = mocker.PropertyMock(return_value={"standard": mode})
    mocker.patch.object(type(camera), "capture_modes", capture_modes_mock)

    mock_image = mocker.Mock()
    # Make resize and convert return itself so we can track further calls of the Image
    # object after a resize
    mock_image.resize.return_value = mock_image
    mock_image.convert.return_value = mock_image
    mock_image.size = (1000, 1200)
    mock_image.mode = "RGBX"

    camera._memory_buffer.get_image.return_value = (
        mock_image,
        {"meta": "data"},
        "standard",
    )

    camera._data_dir = str(tmp_path)

    camera.save_from_memory(RelativeDataPath(test_case.filename), 33)

    assert camera._memory_buffer.get_image.call_count == 1
    assert camera._memory_buffer.get_image.call_args.args == (33,)
    is_jpeg = test_case.filename.lower().endswith((".jpeg", ".jpg"))
    assert camera._add_metadata_to_capture.call_count == int(is_jpeg)
    assert mock_image.resize.call_count == (1 if test_case.resize_needed else 0)
    assert mock_image.convert.call_count == (1 if test_case.convert_needed else 0)
    assert mock_image.save.call_count == 1
    assert mock_image.save.call_args.kwargs == {
        **test_case.save_kwargs,
        "format": "JPEG" if is_jpeg else "PNG",
    }
    assert mock_image.save.call_args.args[0].endswith(".pending")
    assert sorted(p.name for p in tmp_path.iterdir()) == [test_case.filename]


def test_capture_not_visible_before_metadata(test_env, tmp_path, monkeypatch):
    """A reader at the exact JPEG/EXIF boundary must not see a partial capture."""
    camera = test_env.get_thing_by_type(SimulatedCamera)
    camera._data_dir = str(tmp_path)
    metadata = camera._collect_ofm_metadata()
    camera._memory_buffer.add_image(Image.new("RGB", (64, 48)), metadata, "standard")
    destination = tmp_path / "img_1_0_0.jpeg"
    add_metadata = camera._add_metadata_to_capture
    observed = []

    def inspect_then_add(path, data):
        observed.append(destination.exists())
        add_metadata(path, data)

    monkeypatch.setattr(camera, "_add_metadata_to_capture", inspect_then_add)
    camera.save_from_memory(RelativeDataPath(destination.name))
    assert observed == [False], "Preview could see the JPEG before its EXIF was ready"
    with Image.open(destination) as image:
        assert image.size == (64, 48)
    assert sorted(p.name for p in tmp_path.iterdir()) == [destination.name]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("phase", ["encode", "metadata", "publish", "cancel"])
def test_failed_capture_is_not_published(
    phase, existing, test_env, tmp_path, monkeypatch
):
    """Failures/cancellation neither publish a partial file nor destroy an old one."""
    camera = test_env.get_thing_by_type(SimulatedCamera)
    camera._data_dir = str(tmp_path)
    metadata = camera._collect_ofm_metadata()
    image = Image.new("RGB", (64, 48))
    camera._memory_buffer.add_image(image, metadata, "standard")
    destination = tmp_path / "img_1_0_0.jpeg"
    if existing:
        camera.save_from_memory(RelativeDataPath(destination.name))
        camera._memory_buffer.add_image(image, metadata, "standard")
    before = destination.read_bytes() if existing else None

    def fail_encode(_image, path, **_kwargs):
        Path(path).write_bytes(b"partial image")
        raise OSError("injected encode failure")

    def fail_metadata(_path, _data):
        raise ValueError("injected metadata failure")

    def fail_publish(_source, _destination):
        raise OSError("injected publish failure")

    def cancel():
        raise lt.exceptions.InvocationCancelledError("injected cancellation")

    if phase == "encode":
        monkeypatch.setattr(Image.Image, "save", fail_encode)
    elif phase == "metadata":
        monkeypatch.setattr(camera, "_add_metadata_to_capture", fail_metadata)
    elif phase == "publish":
        monkeypatch.setattr(camera_module.os, "replace", fail_publish)
    else:
        monkeypatch.setattr(camera_module.lt, "raise_if_cancelled", cancel)
    expected = lt.exceptions.InvocationCancelledError if phase == "cancel" else OSError
    with pytest.raises(expected):
        camera.save_from_memory(RelativeDataPath(destination.name))
    assert (destination.read_bytes() if destination.exists() else None) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == (
        [destination.name] if existing else []
    )


@pytest.mark.parametrize("filename", ["capture.jpeg", "capture.png"])
def test_published_capture_format_and_metadata(filename, test_env, tmp_path):
    """Pixel format/dimensions stay intact and JPEG has its full EXIF at publication."""
    camera = test_env.get_thing_by_type(SimulatedCamera)
    camera._data_dir = str(tmp_path)
    metadata = camera._collect_ofm_metadata()
    metadata["things_states"]["capture_marker"] = "complete"
    camera._memory_buffer.add_image(Image.new("RGB", (64, 48)), metadata, "standard")
    camera.save_from_memory(RelativeDataPath(filename))
    with Image.open(tmp_path / filename) as image:
        assert image.size == (64, 48)
        assert image.format == ("JPEG" if filename.endswith("jpeg") else "PNG")
        image.load()
    if filename.endswith("jpeg"):
        exif = piexif.load(str(tmp_path / filename))
        comment = json.loads(exif["Exif"][piexif.ExifIFD.UserComment])
        assert comment == metadata["things_states"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [filename]


def test_preview_csm_consistent_at_publication_boundary(
    test_env, tmp_path, monkeypatch
):
    """Use the real stitcher loader while another capture is awaiting its EXIF."""
    camera = test_env.get_thing_by_type(SimulatedCamera)
    camera._data_dir = str(tmp_path)
    metadata = camera._collect_ofm_metadata()
    metadata["things_states"]["camera_stage_mapping"] = {
        "image_to_stage_displacement_matrix": [[0, -2], [2, 0]],
        "image_resolution": [48, 64],
    }
    metadata["things_states"]["stage"]["position"] = {"x": 0, "y": 0, "z": 0}
    for x in range(3):
        metadata["things_states"]["stage"]["position"]["x"] = x
        camera._memory_buffer.add_image(
            Image.new("RGB", (64, 48)), metadata, "standard"
        )
        camera.save_from_memory(RelativeDataPath(f"img_{x}_0_0.jpeg"))
    metadata["things_states"]["stage"]["position"]["x"] = 3
    camera._memory_buffer.add_image(Image.new("RGB", (64, 48)), metadata, "standard")
    original_add = camera._add_metadata_to_capture
    seen_counts = []
    errors = []

    def read_preview_then_add(path, data):
        try:
            seen_counts.append(len(OFSImageSet(str(tmp_path))))
        except RuntimeError as error:
            errors.append(str(error))
        original_add(path, data)

    monkeypatch.setattr(camera, "_add_metadata_to_capture", read_preview_then_add)
    camera.save_from_memory(RelativeDataPath("img_3_0_0.jpeg"))
    assert errors == [], errors
    assert seen_counts == [3]
    assert len(OFSImageSet(str(tmp_path))) == 4
