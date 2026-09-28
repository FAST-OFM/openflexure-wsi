"""Fixed-mode HQ RAW capture with an existing mocked camera owner."""

from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from openflexure_microscope_server.acquisition.raw_capture import hq_geometry


@pytest.fixture
def camera_request(mock_picam_thing, mocker):
    """Borrow a request with real RAW12 bytes; never instantiate a second camera."""
    from openflexure_microscope_server.things.camera import picamera

    owner = mock_picam_thing
    config = {
        "raw": {"format": "SBGGR12_CSI2P", "size": [4056, 3040], "stride": 6112},
        "sensor": {"output_size": [4056, 3040], "bit_depth": 12},
        "main": {"size": [64, 64]},
        "transform": "<libcamera.Transform 'identity'>",
    }
    geometry = hq_geometry(config, [0, 0, 64, 64])
    binding = {
        "geometry": geometry,
        "exposure_time_us": 100,
        "analogue_gain": 1.0,
        "colour_gains": [1.875, 1.482],
    }
    mocker.patch.object(
        type(owner),
        "raw_measurement_configuration",
        new=property(lambda _self: binding),
    )
    data = np.zeros((3040, 6112), dtype=np.uint8)
    data[:64, :96:3] = 1000 >> 4
    data[:64, 1:96:3] = 1000 >> 4
    data[:64, 2:96:3] = (1000 & 15) | ((1000 & 15) << 4)
    request = mocker.Mock()
    request.config = config
    request.get_metadata.return_value = {
        "SensorTimestamp": 1_200_000,
        "ExposureTime": 100,
        "AnalogueGain": 1.0,
        "DigitalGain": 1.000046,
        "ColourGains": [1.875, 1.482],
        "ColourCorrectionMatrix": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        "ColourTemperature": 4500,
        "FrameDuration": 85335,
        "ScalerCrop": [0, 0, 64, 64],
    }
    request.make_array.return_value = data
    request.make_image.return_value = Image.new("RGBX", (64, 64), (10, 20, 30, 255))
    cam = mocker.Mock()

    def dispatch(**kwargs):
        kwargs["signal_function"](SimpleNamespace(get_result=lambda: request))

    cam.capture_request.side_effect = dispatch

    @contextmanager
    def stream():
        yield cam

    mocker.patch.object(owner, "_streaming_picamera", stream)
    mocker.patch.object(picamera.time, "monotonic_ns", return_value=1_000_000)
    return owner, cam, request


def test_raw_rgb_metadata_share_request_and_keep_preview(camera_request):
    """Decode in-place streaming geometry and release the only borrowed buffer."""
    owner, cam, request = camera_request
    planes, rgb, info = owner.capture_linear_frame(1.0)
    np.testing.assert_array_equal(planes, 1000)
    assert planes.shape == (4, 32, 32)
    np.testing.assert_array_equal(rgb[0, 0], [10, 20, 30])
    assert info["geometry"]["sensor_crop"] == [0, 0, 64, 64]
    assert cam.capture_request.call_args.kwargs["flush"] == 1_000_000
    assert cam.capture_request.call_args.kwargs["wait"] is False
    request.release.assert_called_once()
    cam.configure.assert_not_called()
    cam.stop.assert_not_called()


@pytest.mark.parametrize(
    "defect", ["stale", "exposure", "mode", "crop", "format", "rgb"]
)
def test_invalid_capture_releases_request(camera_request, defect):
    """Metadata failures and conversion errors release the buffer exactly once."""
    owner, _cam, request = camera_request
    if defect == "stale":
        request.get_metadata.return_value["SensorTimestamp"] = 800_000
    elif defect == "exposure":
        request.get_metadata.return_value["ExposureTime"] = 150
    elif defect == "mode":
        request.config["sensor"]["output_size"] = [2028, 1520]
    elif defect == "crop":
        request.get_metadata.return_value["ScalerCrop"] = [2, 0, 64, 64]
    elif defect == "format":
        request.config["raw"]["format"] = "SBGGR16"
    else:
        request.make_image.return_value = Image.new("L", (64, 64))
    with pytest.raises(ValueError, match="RAW|exposure|geometry|crop|reference"):
        owner.capture_linear_frame(1.0)
    request.release.assert_called_once()


def test_timeout_rejects_second_capture_and_releases_late_request(camera_request):
    """A pending callback cannot accumulate more measurement jobs or leak a late frame."""
    owner, cam, request = camera_request
    cam.capture_request.side_effect = None
    with pytest.raises(TimeoutError):
        owner.capture_linear_frame(0.001)
    with pytest.raises(RuntimeError, match="still pending"):
        owner.capture_linear_frame(0.001)
    assert cam.capture_request.call_count == 1
    callback = cam.capture_request.call_args.kwargs["signal_function"]
    callback(SimpleNamespace(get_result=lambda: request))
    request.release.assert_called_once()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DigitalGain", float("nan")),
        ("ColourGains", [1.0, 1.0]),
        ("ColourCorrectionMatrix", [0.0] * 8),
    ],
)
def test_processing_is_measured_not_inferred_from_ui(camera_request, key, value):
    """Incorrect/missing processing metadata cannot become a valid reference frame."""
    owner, _cam, request = camera_request
    request.get_metadata.return_value[key] = value
    with pytest.raises(ValueError, match="processing"):
        owner.capture_linear_frame(1.0)
    request.release.assert_called_once()
