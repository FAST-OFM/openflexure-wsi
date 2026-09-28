"""Fresh-exposure capture contract using the existing mocked Picamera owner."""

import json
from contextlib import contextmanager

import numpy as np
import pytest
from PIL import Image


@pytest.fixture
def focus_camera(mock_picam_thing, mocker):
    """Attach one borrowed request without starting a camera."""
    from openflexure_microscope_server.things.camera import picamera

    request = mocker.Mock()
    request.get_metadata.return_value = {
        "SensorTimestamp": 1_200_000,
        "ExposureTime": 100,
        "AnalogueGain": 1.0,
        "ColourGains": [1.0, 1.0],
        "ScalerCrop": [0, 0, 20, 16],
    }
    request.make_image.return_value = Image.fromarray(
        np.ones((16, 20, 3), dtype=np.uint8)
    )
    camera = mocker.Mock()
    camera.capture_request.return_value = request

    @contextmanager
    def streaming():
        yield camera

    mocker.patch.object(mock_picam_thing, "_streaming_picamera", streaming)
    mocker.patch.object(picamera.time, "monotonic_ns", return_value=1_000_000)
    return mock_picam_thing, camera, request


def test_request_has_one_matching_exposure_and_is_released(focus_camera):
    """Flush and an explicit exposure-start check exclude frames from before settling."""
    owner, camera, request = focus_camera
    image, metadata = owner.capture_settled_frame(3.0)
    camera.capture_request.assert_called_once_with(wait=3.0, flush=1_000_000)
    request.release.assert_called_once()
    assert image.shape == (16, 20, 3)
    assert image.flags.owndata
    assert metadata["exposure_start_ns"] == 1_100_000
    assert metadata["image_size"] == [20, 16]
    camera.configure.assert_not_called()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SensorTimestamp", 900_000),
        ("SensorTimestamp", None),
        ("ExposureTime", 1000),
        ("ExposureTime", 0),
        ("AnalogueGain", float("nan")),
        ("AnalogueGain", 0),
    ],
)
def test_invalid_or_stale_metadata_releases_request(focus_camera, key, value):
    """Neither missing metadata nor an old first-pixel exposure leaks a buffer."""
    owner, _camera, request = focus_camera
    request.get_metadata.return_value[key] = value
    with pytest.raises(ValueError, match="exposure|metadata|gain|settled"):
        owner.capture_settled_frame(3.0)
    request.release.assert_called_once()


def test_image_conversion_error_releases_request(focus_camera):
    """Errors converting the same borrowed request do not leak camera buffers."""
    owner, _camera, request = focus_camera
    request.make_image.side_effect = RuntimeError("image failed")
    with pytest.raises(RuntimeError, match="image failed"):
        owner.capture_settled_frame(3.0)
    request.release.assert_called_once()


def test_capture_timeout_is_not_retried(focus_camera):
    """The caller gets a bounded failure, not a stale frame or another request."""
    owner, camera, request = focus_camera
    camera.capture_request.side_effect = TimeoutError("frame timeout")
    with pytest.raises(TimeoutError):
        owner.capture_settled_frame(0.5)
    assert camera.capture_request.call_count == 1
    request.release.assert_not_called()


def test_camera_binding_is_stable_and_tracks_tuning(mock_picam_thing):
    """Fingerprint the real camera mode and JSON tuning without opening a camera."""
    before = mock_picam_thing.focus_configuration
    assert before == mock_picam_thing.focus_configuration
    assert json.loads(json.dumps(before)) == before
    assert len(before["tuning_sha256"]) == 64
    tuning = json.loads(json.dumps(mock_picam_thing.tuning))
    tuning["calibration_identity_test"] = "different"
    mock_picam_thing.tuning = tuning
    assert (
        mock_picam_thing.focus_configuration["tuning_sha256"] != before["tuning_sha256"]
    )


@pytest.mark.parametrize("mode", ["RGB", "RGBX", "RGBA"])
def test_video_padding_channel_is_discarded_without_changing_rgb(focus_camera, mode):
    """Accept the Pi4 XBGR8888 stream without interpreting its fourth byte as colour."""
    owner, camera, request = focus_camera
    values = np.empty((16, 20, 3), dtype=np.uint8)
    values[:] = [17, 103, 221]
    source = Image.fromarray(values).convert(mode)
    if mode == "RGBA":
        source.putalpha(0)
    request.make_image.return_value = source
    actual, _metadata = owner.capture_settled_frame(3.0)
    np.testing.assert_array_equal(actual, values)
    assert actual.flags.owndata
    camera.configure.assert_not_called()
    request.release.assert_called_once()


@pytest.mark.parametrize("mode", ["L", "I;16", "F"])
def test_non_rgb_input_is_not_silently_coerced(focus_camera, mode):
    """Refuse monochrome/high-bit-depth inputs instead of changing the measurement."""
    owner, _camera, request = focus_camera
    request.make_image.return_value = Image.new(mode, (20, 16))
    with pytest.raises(ValueError, match="RGB8"):
        owner.capture_settled_frame(3.0)
    request.release.assert_called_once()
