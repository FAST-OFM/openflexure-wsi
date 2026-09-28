"""A malformed preview buffer must not terminate Picamera2's producer thread."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime
from threading import Event, RLock, current_thread
from unittest.mock import Mock

import pytest

from labthings_fastapi.testing import create_thing_without_server


@pytest.fixture
def output(mock_picam_thing):
    """Use a real output class with only the platform Output base stubbed."""
    from openflexure_microscope_server.things.camera.picamera import (
        PicameraStreamOutput,
    )

    assert mock_picam_thing is not None
    assert isinstance(PicameraStreamOutput, type)
    return PicameraStreamOutput(Mock(), Mock(firsttimestamp=1000000))


@pytest.mark.parametrize(
    "frame", [b"", b"x", b"\xff\xd8", b"wrong", b"\xff\xd8truncated", b"bad\xff\xd9"]
)
def test_bad_buffer_is_dropped_and_next_frame_survives(output, frame):
    """Never publish an invalid buffer or replay a cached valid frame."""
    output.outputframe(frame, timestamp=1)
    output.stream.add_frame.assert_not_called()
    assert output.dropped_jpeg_frames == 1
    good = b"\xff\xd8valid-test-payload\xff\xd9"
    output.outputframe(good, timestamp=2)
    args = output.stream.add_frame.call_args.args
    assert args[0] == good
    assert isinstance(args[1], datetime)


def test_bad_buffer_logs_are_bounded(output, caplog):
    """Persistent failure remains visible without logging every video frame."""
    for _ in range(100):
        output.outputframe(b"bad")
    assert output.dropped_jpeg_frames == 100
    assert len(caplog.records) == 7  # powers of two: 1 through 64
    output.stream.add_frame.assert_not_called()


def test_missing_encoder_timestamp_uses_stream_fallback(output):
    """Keep the upstream timestamp fallback for valid images."""
    good = b"\xff\xd8payload\xff\xd9"
    output.outputframe(good)
    output.stream.add_frame.assert_called_once_with(good, None)


def test_held_preview_does_not_publish_or_replay(output):
    """The real stream boundary sees no add_frame call until the new WHITE frame."""
    from openflexure_microscope_server.acquisition.measurement_preview import (
        MeasurementPreview,
    )

    gate = output.preview = MeasurementPreview()
    payload = b"\xff\xd8payload\xff\xd9"
    gate.transition()
    output.outputframe(payload, timestamp=1)
    output.stream.add_frame.assert_not_called()
    gate.confirmed("white", 1_000_000_000, finished=True)
    for timestamp in (1_001_000_000, 1_002_000_000, 1_003_000_000):
        gate.record({"SensorTimestamp": timestamp, "ExposureTime": 100})
        output.outputframe(payload, timestamp=timestamp // 1000 - 1_000_000)
    output.stream.add_frame.assert_called_once()
    assert gate.status["last_white_sensor_timestamp_us"] == 1_003_000


def test_different_size_main_preview_keeps_separate_software_encoder(
    mock_picam_thing, mocker
):
    """Other camera profiles retain their larger main preview without resizing."""
    from openflexure_microscope_server.things.camera import picamera

    camera = mock_picam_thing
    mode = "default"
    assert camera.streaming_modes[mode].main_resolution != (
        camera.streaming_modes[mode].lores_resolution
    )
    picam = Mock(started=False)
    mocker.patch.object(camera, "_streaming_picamera", return_value=nullcontext(picam))
    configure = mocker.patch.object(camera, "_configure_picamera")
    software = mocker.patch.object(picamera, "JpegEncoder")
    hardware = mocker.patch.object(picamera, "MJPEGEncoder")
    camera.preview_jpeg_quality = 87
    controls = {"ExposureTime": 4477}

    camera._start_streaming(mode, controls=controls)

    configure.assert_called_once_with(
        picam=picam, controls=controls, mode_info=camera.streaming_modes[mode]
    )
    software.assert_called_once_with(num_threads=1, q=87)
    hardware.assert_called_once_with(100000000)
    preview_encoder, preview_output = picam.start_recording.call_args.args
    assert preview_encoder is software.return_value
    assert preview_output.encoder is preview_encoder
    assert preview_output.stream is camera.mjpeg_stream
    assert preview_output.preview is camera._measurement_preview
    assert picam.start_recording.call_args.kwargs == {"name": "main"}
    autofocus_encoder, autofocus_output = picam.start_encoder.call_args.args
    assert autofocus_encoder is hardware.return_value
    assert autofocus_output.encoder is autofocus_encoder
    assert autofocus_output.stream is camera.lores_mjpeg_stream
    assert autofocus_output.preview is None
    assert picam.start_encoder.call_args.kwargs == {"name": "lores"}
    assert camera.stream_active is True


@pytest.mark.parametrize("mode", ["default", "full_resolution"])
@pytest.mark.parametrize("started", [False, True])
def test_hq_keeps_quality_preview_separate_from_hardware_autofocus(
    mock_picam_thing, mocker, mode, started
):
    """HQ modes keep one hardware autofocus encoder and an independent q90 preview."""
    from openflexure_microscope_server.things.camera import picamera

    assert mock_picam_thing is not None  # installs platform stubs
    camera = create_thing_without_server(picamera.PiCameraHQ)
    picam = Mock(started=started)
    mocker.patch.object(camera, "_streaming_picamera", return_value=nullcontext(picam))
    configure = mocker.patch.object(camera, "_configure_picamera")
    software = mocker.patch.object(picamera, "JpegEncoder")
    hardware = mocker.patch.object(picamera, "MJPEGEncoder")
    assert camera.preview_jpeg_quality == 90
    controls = {"ExposureTime": 4477}

    camera._start_streaming(mode, controls=controls)

    configure.assert_called_once_with(
        picam=picam, controls=controls, mode_info=camera.streaming_modes[mode]
    )
    assert picam.stop.call_count == int(started)
    assert picam.stop_encoder.call_count == int(started)
    software.assert_called_once_with(num_threads=1, q=90)
    hardware.assert_called_once_with(100000000)
    picam.start_recording.assert_called_once()
    encoder, output = picam.start_recording.call_args.args
    assert encoder is software.return_value
    assert output.encoder is encoder
    assert output.stream is camera.mjpeg_stream
    assert output.ungated_stream is None
    assert output.preview is camera._measurement_preview
    expected_source = "lores" if mode == "full_resolution" else "main"
    assert picam.start_recording.call_args.kwargs == {"name": expected_source}
    picam.start_encoder.assert_called_once()
    autofocus_encoder, autofocus_output = picam.start_encoder.call_args.args
    assert autofocus_encoder is hardware.return_value
    assert autofocus_output.encoder is autofocus_encoder
    assert autofocus_output.stream is camera.lores_mjpeg_stream
    assert autofocus_output.preview is None
    assert autofocus_output.ungated_stream is None
    assert picam.start_encoder.call_args.kwargs == {"name": "lores"}
    assert camera.stream_active is True


@pytest.mark.parametrize("failing_method", ["start_recording", "start_encoder"])
def test_stream_start_failure_is_reported_and_clears_active(
    mock_picam_thing, mocker, failing_method
):
    """A failed encoder start must not masquerade as an active stream."""
    camera = mock_picam_thing
    camera.stream_active = True
    picam = Mock(started=False)
    getattr(picam, failing_method).side_effect = RuntimeError("codec unavailable")
    mocker.patch.object(camera, "_streaming_picamera", return_value=nullcontext(picam))
    mocker.patch.object(camera, "_configure_picamera")
    with pytest.raises(RuntimeError, match="codec unavailable"):
        camera._start_streaming("full_resolution", controls={})
    assert camera.stream_active is False


@pytest.fixture
def shared_output(output):
    """Add an ungated autofocus destination to the real JPEG output boundary."""
    output.ungated_stream = Mock()
    return output


@pytest.mark.parametrize("timestamp", [None, 0, 42])
def test_shared_jpeg_and_timestamp_are_identical(shared_output, timestamp):
    """Fan out the same bytes and timestamp, not a second encoding or cached image."""
    good = b"\xff\xd8payload\xff\xd9"
    shared_output.outputframe(good, timestamp=timestamp)
    autofocus_call = shared_output.ungated_stream.add_frame.call_args
    assert shared_output.stream.add_frame.call_args == autofocus_call
    assert autofocus_call.args[0] is good


def test_shared_malformed_buffer_reaches_neither_destination(shared_output):
    """The JPEG framing check applies before either consumer sees the buffer."""
    shared_output.outputframe(b"bad")
    shared_output.stream.add_frame.assert_not_called()
    shared_output.ungated_stream.add_frame.assert_not_called()
    assert shared_output.dropped_jpeg_frames == 1


def test_shared_white_gate_never_holds_autofocus(shared_output):
    """R/G and WHITE settling frames go to focus, but only fresh WHITE to preview."""
    from openflexure_microscope_server.acquisition.measurement_preview import (
        MeasurementPreview,
    )

    gate = shared_output.preview = MeasurementPreview()
    payload = b"\xff\xd8payload\xff\xd9"
    gate.transition()
    shared_output.outputframe(payload, timestamp=1)
    shared_output.stream.add_frame.assert_not_called()
    gate.confirmed("white", 1_000_000_000, finished=True)
    for timestamp in (1_001_000_000, 1_002_000_000, 1_003_000_000):
        gate.record({"SensorTimestamp": timestamp, "ExposureTime": 100})
        shared_output.outputframe(payload, timestamp=timestamp // 1000 - 1_000_000)
    assert shared_output.ungated_stream.add_frame.call_count == 4
    shared_output.stream.add_frame.assert_called_once()
    assert shared_output.stream.add_frame.call_args == (
        shared_output.ungated_stream.add_frame.call_args
    )


@pytest.mark.parametrize("failure", ["delivery", "gate"])
def test_shared_preview_errors_do_not_stop_focus(shared_output, caplog, failure):
    """Bound error logging and recover preview without killing the encoder thread."""
    gate = shared_output.preview = Mock()
    failing_call = (
        shared_output.stream.add_frame if failure == "delivery" else gate.allows
    )
    failing_call.side_effect = RuntimeError("preview error")
    payload = b"\xff\xd8payload\xff\xd9"
    for i in range(100):
        shared_output.outputframe(payload, timestamp=i)
    assert shared_output.ungated_stream.add_frame.call_count == 100
    assert shared_output.preview_delivery_errors == 100
    assert len(caplog.records) == 7
    failing_call.side_effect = None
    shared_output.outputframe(payload, timestamp=100)
    assert shared_output.ungated_stream.add_frame.call_count == 101
    assert shared_output.stream.add_frame.call_args == (
        shared_output.ungated_stream.add_frame.call_args
    )


def test_shared_autofocus_delivery_error_is_not_hidden(shared_output):
    """Only preview failure is isolated; an autofocus delivery error stays visible."""
    shared_output.ungated_stream.add_frame.side_effect = RuntimeError("focus error")
    with pytest.raises(RuntimeError, match="focus error"):
        shared_output.outputframe(b"\xff\xd8payload\xff\xd9", timestamp=1)
    shared_output.stream.add_frame.assert_not_called()


@pytest.mark.parametrize("quality", [0, 101])
def test_preview_quality_rejects_out_of_range_values(mock_picam_thing, quality):
    """Invalid quality must not reach the software encoder."""
    with pytest.raises(ValueError, match="(greater than|less than)"):
        mock_picam_thing.preview_jpeg_quality = quality


class ObservedCameraLock:
    """Signal a selected thread's lock attempt without changing RLock semantics."""

    def __init__(self, thread_prefix, attempted):
        """Wrap a real reentrant lock and a test-only arrival notification."""
        self._lock = RLock()
        self._thread_prefix = thread_prefix
        self._attempted = attempted

    def __enter__(self):
        """Notify before blocking, then acquire the unchanged underlying lock."""
        if current_thread().name.startswith(self._thread_prefix):
            self._attempted.set()
        return self._lock.__enter__()

    def __exit__(self, *args):
        """Release the same underlying lock."""
        return self._lock.__exit__(*args)


@pytest.fixture
def hq_snapshot_camera(mock_picam_thing, mocker):
    """Keep the real camera lock/getter/mode switch with only hardware stubbed."""
    from openflexure_microscope_server.things.camera import picamera

    assert mock_picam_thing is not None
    camera = create_thing_without_server(picamera.PiCameraHQ)
    hardware = Mock(started=True, camera_properties={"Model": "imx477"})
    camera._picamera = hardware
    camera.streaming_mode = "default"
    camera.stream_active = True
    configuration = {}

    def configure(*, picam, controls, mode_info):
        nonlocal configuration
        assert picam is hardware
        configuration = {
            "main": {"size": mode_info.main_resolution, "format": "XBGR8888"},
            "sensor": {"output_size": (4056, 3040), "bit_depth": 12},
            "transform": "<libcamera.Transform 'identity'>",
            "controls": {"AeEnable": False, "AwbEnable": False, **controls},
        }

    configure(picam=hardware, controls={}, mode_info=camera.streaming_modes["default"])
    hardware.camera_configuration.side_effect = lambda: configuration
    hardware.stop.side_effect = lambda: setattr(hardware, "started", False)
    hardware.start_recording.side_effect = lambda *_args, **_kwargs: setattr(
        hardware, "started", True
    )
    mocker.patch.object(camera, "_configure_picamera", side_effect=configure)
    return camera, hardware, configure


@pytest.mark.parametrize(
    ("initial", "target"),
    [("default", "full_resolution"), ("full_resolution", "default")],
)
def test_jpeg_configuration_waits_for_inflight_mode_switch(
    hq_snapshot_camera, mocker, initial, target
):
    """A GET during stop/configure waits for the new live binding, not a false fault."""
    camera, hardware, configure = hq_snapshot_camera
    camera._start_streaming(initial, controls={})
    configuring, finish_configure, reader_reached = Event(), Event(), Event()
    camera._picamera_lock = ObservedCameraLock("metadata-reader", reader_reached)

    def blocked_configure(**kwargs):
        configuring.set()
        assert finish_configure.wait(3)
        configure(**kwargs)

    def read_configuration():
        try:
            return camera.jpeg_measurement_configuration
        finally:
            # On the unfixed getter the pre-lock check raises before attempting
            # the lock. On the fixed getter ObservedCameraLock signals instead.
            reader_reached.set()

    mocker.patch.object(camera, "_configure_picamera", side_effect=blocked_configure)
    with (
        ThreadPoolExecutor(1, thread_name_prefix="mode-switch") as writers,
        ThreadPoolExecutor(1, thread_name_prefix="metadata-reader") as readers,
    ):
        writer = writers.submit(camera._start_streaming, target, controls={})
        try:
            assert configuring.wait(3)
            assert not hardware.started
            reader = readers.submit(read_configuration)
            assert reader_reached.wait(3)
        finally:
            finish_configure.set()
        writer.result(timeout=3)
        binding = reader.result(timeout=3)
    assert binding["camera"]["streaming_mode"] == target
    assert binding["geometry"]["image_size"] == list(
        camera.streaming_modes[target].main_resolution
    )
    assert camera.stream_active
    hardware.capture_request.assert_not_called()
    hardware.capture_metadata.assert_not_called()


@pytest.mark.parametrize(
    ("initial", "target"),
    [("default", "full_resolution"), ("full_resolution", "default")],
)
def test_jpeg_configuration_cannot_mix_new_mode_with_old_hardware(
    hq_snapshot_camera, mocker, initial, target
):
    """A mode writer waiting on the getter's lock must not publish half its state."""
    camera, hardware, _configure = hq_snapshot_camera
    camera._start_streaming(initial, controls={})
    reading_config, finish_read, writer_reached = Event(), Event(), Event()
    camera._picamera_lock = ObservedCameraLock("mode-switch", writer_reached)
    original_configuration = hardware.camera_configuration.side_effect

    def blocked_configuration():
        reading_config.set()
        assert finish_read.wait(3)
        return original_configuration()

    mocker.patch.object(
        hardware, "camera_configuration", side_effect=blocked_configuration
    )
    with (
        ThreadPoolExecutor(1, thread_name_prefix="metadata-reader") as readers,
        ThreadPoolExecutor(1, thread_name_prefix="mode-switch") as writers,
    ):
        reader = readers.submit(lambda: camera.jpeg_measurement_configuration)
        try:
            assert reading_config.wait(3)
            writer = writers.submit(camera._start_streaming, target, controls={})
            assert writer_reached.wait(3)
        finally:
            finish_read.set()
        binding = reader.result(timeout=3)
        writer.result(timeout=3)
    assert binding["camera"]["streaming_mode"] == initial
    assert binding["geometry"]["image_size"] == list(
        camera.streaming_modes[initial].main_resolution
    )
    # A subsequent GET is fresh, not a cached copy of the now-old binding.
    current = camera.jpeg_measurement_configuration
    assert current["camera"]["streaming_mode"] == target
    assert current["geometry"]["image_size"] == list(
        camera.streaming_modes[target].main_resolution
    )


def test_jpeg_configuration_hardware_failure_is_not_retried(hq_snapshot_camera):
    """A real backend read error propagates unchanged, without cached fallback."""
    camera, hardware, _configure = hq_snapshot_camera
    previous = camera.jpeg_measurement_configuration
    assert previous["camera"]["streaming_mode"] == "default"
    hardware.camera_configuration.reset_mock()
    failure = OSError("camera disconnected")
    hardware.camera_configuration.side_effect = failure
    with pytest.raises(OSError, match="camera disconnected") as caught:
        _ = camera.jpeg_measurement_configuration
    assert caught.value is failure
    hardware.camera_configuration.assert_called_once_with()
    hardware.capture_request.assert_not_called()


@pytest.mark.parametrize("fault", ["inactive", "stopped", "sensor", "dimensions", "ae"])
def test_jpeg_configuration_rejects_invalid_live_state(hq_snapshot_camera, fault):
    """The synchronized getter keeps the existing running/sensor/geometry/ISP gates."""
    camera, hardware, _configure = hq_snapshot_camera
    previous = camera.jpeg_measurement_configuration
    assert previous["geometry"]["image_size"] == [1014, 760]
    if fault == "inactive":
        camera.stream_active = False
    elif fault == "stopped":
        hardware.started = False
    elif fault == "sensor":
        hardware.camera_properties["Model"] = "imx219"
    elif fault == "dimensions":
        hardware.camera_configuration()["main"]["size"] = (2028, 1520)
    else:
        hardware.camera_configuration()["controls"]["AeEnable"] = True
    reason = {
        "inactive": "requires a running HQ camera",
        "stopped": "requires a running HQ camera",
        "sensor": "requires the IMX477 sensor",
        "dimensions": "dimensions differ from the configured mode",
        "ae": "requires fixed manual AE/AWB controls",
    }[fault]
    with pytest.raises(ValueError, match=reason):
        _ = camera.jpeg_measurement_configuration


def test_failed_mode_switch_does_not_serve_previous_jpeg_binding(
    hq_snapshot_camera, mocker
):
    """A failed configure remains failed, even though a previous binding was valid."""
    camera, hardware, _configure = hq_snapshot_camera
    assert (
        camera.jpeg_measurement_configuration["camera"]["streaming_mode"] == "default"
    )
    mocker.patch.object(
        camera, "_configure_picamera", side_effect=RuntimeError("configure failed")
    )
    with pytest.raises(RuntimeError, match="configure failed"):
        camera._start_streaming("full_resolution", controls={})
    assert not camera.stream_active
    assert not hardware.started
    with pytest.raises(ValueError, match="requires a running HQ camera"):
        _ = camera.jpeg_measurement_configuration
