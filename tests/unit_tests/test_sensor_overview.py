"""One temporary full-sensor HQ request and restoration, without hardware."""

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from labthings_fastapi.testing import create_thing_without_server


@pytest.fixture
def overview_camera(mock_picam_thing, mocker, tmp_path):
    """Keep real mode/configure/restore methods; fake only Picamera2 IO."""
    from openflexure_microscope_server.things.camera import picamera

    assert mock_picam_thing is not None
    owner = create_thing_without_server(picamera.PiCameraHQ)
    owner._data_dir = str(tmp_path)
    owner._exposure_time = 100
    owner._analogue_gain = 1.0
    owner._colour_gains = (1.875, 1.482)
    cam = mocker.Mock(
        started=False, camera_controls={}, camera_properties={"Model": "imx477"}
    )
    owner._picamera = cam
    current = {}

    def configure(value):
        current.clear()
        current.update(copy.deepcopy(value))

    def video_configuration(**kwargs):
        return {
            **copy.deepcopy(kwargs),
            "raw": {"size": (4056, 3040), "format": "SBGGR12_CSI2P"},
            "transform": "<libcamera.Transform 'identity'>",
        }

    cam.create_video_configuration.side_effect = video_configuration
    cam.configure.side_effect = configure

    def set_controls(controls):
        current["controls"].update(controls)

    cam.set_controls.side_effect = set_controls
    cam.camera_configuration.side_effect = lambda: copy.deepcopy(current)
    cam.start.side_effect = lambda: setattr(cam, "started", True)
    cam.start_recording.side_effect = lambda *_args, **_kwargs: setattr(
        cam, "started", True
    )
    cam.stop.side_effect = lambda: setattr(cam, "started", False)
    cam.stop_recording.side_effect = lambda: setattr(cam, "started", False)
    cam.capture_metadata.side_effect = AssertionError(
        "Must not read temporary metadata through settings getters"
    )
    mocker.patch.object(picamera.time, "sleep")
    owner._start_streaming()
    assert owner.stream_active
    frozen = json.loads(json.dumps(owner.camera_configuration))
    cam.reset_mock()
    request = mocker.Mock()
    request.make_image.return_value = Image.new(
        "RGBX", (4056, 3040), (90, 120, 150, 255)
    )
    metadata = {
        "ExposureTime": 100,
        "AnalogueGain": 1.0,
        "DigitalGain": 1.000046,
        "ColourGains": [1.875, 1.482],
        "ColourCorrectionMatrix": [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0],
        "FrameDuration": 85335,
        "ScalerCrop": [0, 0, 4056, 3040],
    }
    request.get_metadata.side_effect = lambda: copy.deepcopy(metadata)

    def dispatch(**kwargs):
        metadata["SensorTimestamp"] = kwargs["flush"] + 200_000
        request.config = copy.deepcopy(current)
        kwargs["signal_function"](SimpleNamespace(get_result=lambda: request))

    cam.capture_request.side_effect = dispatch
    save_settings = mocker.patch.object(owner, "save_settings")
    return SimpleNamespace(
        owner=owner,
        cam=cam,
        request=request,
        metadata=metadata,
        frozen=frozen,
        module=picamera,
        save_settings=save_settings,
    )


def assert_restored(context):
    """Preview uses exactly its original mode/configuration and private settings."""
    assert context.owner.stream_active
    assert context.cam.started
    assert json.loads(json.dumps(context.owner.camera_configuration)) == context.frozen
    assert context.owner.streaming_mode == "default"
    assert context.owner._exposure_time == 100
    assert context.owner._colour_gains == (1.875, 1.482)
    context.cam.capture_metadata.assert_not_called()
    context.save_settings.assert_not_called()


def test_sensor_overview_uses_one_full_request_and_restores(overview_camera):
    """JPEG and full metadata share one request; saved hashes/configs are verifiable."""
    context = overview_camera
    result = context.owner.capture_sensor_overview(prepared=True)
    assert_restored(context)
    context.request.release.assert_called_once()
    context.request.make_image.assert_called_once_with("main")
    context.request.make_array.assert_not_called()
    context.cam.capture_request.assert_called_once()
    assert context.cam.capture_request.call_args.kwargs["wait"] is False
    assert result["request_metadata"] == context.metadata
    assert result["image_size"] == [4056, 3040]
    assert result["scaler_crop"] == [0, 0, 4056, 3040]
    assert result["frame_duration_us"] == 85335
    assert result["requested_mode"]["buffer_count"] == 2
    assert (
        result["frozen_configuration"]
        == result["restored_configuration"]
        == context.frozen
    )
    directory = Path(result["directory"])
    image = directory / "sensor-overview.jpg"
    with Image.open(image) as saved:
        assert saved.size == (4056, 3040)
        assert saved.layer == [(1, 1, 1, 0), (2, 1, 1, 1), (3, 1, 1, 1)]
    assert hashlib.sha256(image.read_bytes()).hexdigest() == result["image_sha256"]
    assert json.loads((directory / "sensor-overview.json").read_text()) == json.loads(
        json.dumps(result)
    )


@pytest.mark.parametrize(
    "defect", ["crop", "image_size", "sensor", "exposure", "encode", "allocate"]
)
def test_sensor_overview_failure_releases_and_restores(overview_camera, mocker, defect):
    """Invalid geometry, encoding or allocation fails once and restores the preview."""
    context = overview_camera
    if defect == "crop":
        context.metadata["ScalerCrop"] = [628, 120, 2800, 2800]
    elif defect == "image_size":
        context.request.make_image.return_value = Image.new("RGB", (700, 700))
    elif defect == "sensor":
        original = context.cam.capture_request.side_effect

        def wrong_sensor(**kwargs):
            original(**kwargs)
            context.request.config["sensor"]["output_size"] = (2028, 1520)

        context.cam.capture_request.side_effect = wrong_sensor
    elif defect == "exposure":
        context.metadata["ExposureTime"] = 101
    elif defect == "encode":
        mocker.patch.object(
            Image.Image, "save", side_effect=OSError("JPEG save failed")
        )
    else:
        configure = context.cam.configure.side_effect

        def failed_allocation(value):
            if value["main"]["size"] == (4056, 3040):
                raise MemoryError("CMA allocation failed")
            return configure(value)

        context.cam.configure.side_effect = failed_allocation
    with pytest.raises((ValueError, OSError, MemoryError)):
        context.owner.capture_sensor_overview(prepared=True)
    assert_restored(context)
    if defect == "allocate":
        context.cam.capture_request.assert_not_called()
        context.request.release.assert_not_called()
    else:
        context.cam.capture_request.assert_called_once()
        context.request.release.assert_called_once()


@pytest.mark.parametrize("defect", ["cancel", "timeout"])
def test_sensor_overview_pending_ownership_and_restore(overview_camera, mocker, defect):
    """Cancellation/timeout never queue a second capture and release late requests."""
    from openflexure_microscope_server.acquisition.raw_capture import PendingRequest

    context = overview_camera
    if defect == "cancel":
        cancelled = context.module.lt.exceptions.InvocationCancelledError()
        mocker.patch.object(
            context.module.lt, "raise_if_cancelled", side_effect=[None, cancelled]
        )
        expected = type(cancelled)
    else:
        context.cam.capture_request.side_effect = None
        original_take = PendingRequest.take
        mocker.patch.object(
            PendingRequest,
            "take",
            lambda pending, _timeout, check: original_take(pending, 0.001, check),
        )
        expected = TimeoutError
    with pytest.raises(expected):
        context.owner.capture_sensor_overview(prepared=True)
    assert_restored(context)
    context.cam.capture_request.assert_called_once()
    if defect == "timeout":
        with pytest.raises(RuntimeError, match="still pending"):
            context.owner.capture_sensor_overview(prepared=True)
        context.cam.capture_request.assert_called_once()
        callback = context.cam.capture_request.call_args.kwargs["signal_function"]
        callback(SimpleNamespace(get_result=lambda: context.request))
    context.request.release.assert_called_once()


def test_sensor_overview_requires_preparation_before_effects(overview_camera):
    """Unconfirmed and wrong-sensor actions never stop preview or dispatch capture."""
    context = overview_camera
    with pytest.raises(ValueError, match="Confirm"):
        context.owner.capture_sensor_overview()
    context.cam.camera_properties["Model"] = "imx219"
    with pytest.raises(RuntimeError, match="IMX477"):
        context.owner.capture_sensor_overview(prepared=True)
    context.cam.stop_recording.assert_not_called()
    context.cam.configure.assert_not_called()
    context.cam.capture_request.assert_not_called()


def test_sensor_overview_preserves_primary_and_restore_failure(overview_camera):
    """A failed restore is explicit and cannot turn an invalid capture into success."""
    context = overview_camera
    context.metadata["ScalerCrop"] = [628, 120, 2800, 2800]
    context.cam.start_recording.side_effect = RuntimeError("preview encoder failed")
    with pytest.raises(ValueError, match="crop/size/mode") as caught:
        context.owner.capture_sensor_overview(prepared=True)
    assert any("restoration check failed" in note for note in caught.value.__notes__)
    assert not context.owner.stream_active
    context.request.release.assert_called_once()
    context.cam.capture_metadata.assert_not_called()
    context.save_settings.assert_not_called()


def test_sensor_overview_failed_stop_does_not_read_temporary_settings(overview_camera):
    """Frozen controls also prevent getters from running when the first stop fails."""
    context = overview_camera
    context.metadata["ScalerCrop"] = [628, 120, 2800, 2800]
    stops = []

    def stop():
        stops.append(True)
        if len(stops) == 1:
            raise RuntimeError("temporary stop failed")
        context.cam.started = False

    context.cam.stop.side_effect = stop
    with pytest.raises(ValueError, match="crop/size/mode") as caught:
        context.owner.capture_sensor_overview(prepared=True)
    assert any(
        "Temporary camera stop failed" in note for note in caught.value.__notes__
    )
    assert_restored(context)
    context.cam.capture_request.assert_called_once()
    context.request.release.assert_called_once()
