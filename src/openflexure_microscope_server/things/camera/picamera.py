"""Submodule for interacting with a Raspberry Pi camera using the Picamera2 library.

The Picamera2 library uses LibCamera as the underlying camera stack. This gives us
some control of the GPU pipeline for the image.

The API documentation for PiCamera2 is unfortunately not in a standard auto-generated
website. For documentation of the PiCamera2 API there is a PDF called
"The Picamera2 Library" available at:
https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf

For information on the algorithms used to tune/calibrate the Raspberry Pi Camera see
the guide called "Raspberry Pi Camera Algorithm and Tuning Guide"
Available at:
https://datasheets.raspberrypi.com/camera/raspberry-pi-camera-guide.pdf
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import tempfile
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Iterator,
    Literal,
    Mapping,
    Optional,
    Self,
    cast,
)
from uuid import uuid4

import numpy as np
from picamera2 import Picamera2
from picamera2.encoders import Encoder, JpegEncoder, MJPEGEncoder
from picamera2.outputs import Output
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

import labthings_fastapi as lt
from labthings_fastapi.exceptions import ServerNotRunningError
from labthings_fastapi.types.numpy import NDArray

from openflexure_microscope_server.acquisition.jpeg_capture import (
    JPEGEncoding,
    encode_decode_jpeg,
    jpeg_geometry,
    jpeg_isp_configuration,
    jpeg_processing,
)
from openflexure_microscope_server.acquisition.measurement_preview import (
    MeasurementPreview,
)
from openflexure_microscope_server.acquisition.raw_capture import (
    PendingRequest,
    decode_hq_raw,
    exposure_metadata,
    hq_geometry,
)
from openflexure_microscope_server.things.scanning.background_detect import (
    ChannelBlankError,
)
from openflexure_microscope_server.ui import (
    ActionButton,
    PropertyControl,
    action_button_for,
    property_control_for,
)

from . import BaseCamera, CaptureMode, StreamingMode
from . import picamera_recalibrate_utils as recalibrate_utils
from . import picamera_tuning_file_utils as tf_utils

if TYPE_CHECKING:
    from libcamera import Request

LOGGER = logging.getLogger(__name__)


class PicameraModelError(RuntimeError):
    """There is a problem Picamera sensor model set by the configuration."""


class MissingCalibrationError(RuntimeError):
    """Picamera tuning file is missing or doesn't contain the requested algorithm."""


class PicameraStreamOutput(Output):
    """An Output class that sends frames to a stream."""

    def __init__(
        self,
        stream: lt.outputs.MJPEGStream,
        encoder: Encoder,
        preview: MeasurementPreview | None = None,
        *,
        ungated_stream: lt.outputs.MJPEGStream | None = None,
    ) -> None:
        """Create an output that puts frames in an MJPEGStream.

        :param stream: The labthings MJPEGStream to send frames to.
        :param encoder: The encoder that is encoding the frames. This is needed to get
            the full timestamp.
        :param ungated_stream: Optional autofocus destination. It receives the same
            JPEG and timestamp before the WHITE-only preview gate is applied.
        """
        Output.__init__(self)
        self.stream = stream
        self.encoder = encoder
        self.dropped_jpeg_frames = 0
        self.preview = preview
        self.ungated_stream = ungated_stream
        self.preview_delivery_errors = 0

    def outputframe(
        self,
        frame: bytes,
        _keyframe: Optional[bool] = True,
        timestamp: Optional[int] = None,
        _packet: Any = None,
        _audio: bool = False,
    ) -> None:
        """Publish complete JPEGs without killing the encoder on one bad buffer."""
        if len(frame) < 4 or frame[:2] != b"\xff\xd8" or frame[-2:] != b"\xff\xd9":
            self.dropped_jpeg_frames += 1
            # Bound logging even if the encoder repeatedly emits invalid buffers.
            if self.dropped_jpeg_frames & (self.dropped_jpeg_frames - 1) == 0:
                LOGGER.warning(
                    "Discarded malformed MJPEG buffer: bytes=%s, dropped=%s",
                    len(frame),
                    self.dropped_jpeg_frames,
                )
            return
        sensor_us = (
            None
            if timestamp is None or self.encoder.firsttimestamp is None
            else timestamp + self.encoder.firsttimestamp
        )
        if timestamp is None or self.encoder.firsttimestamp is None:
            frame_timestamp = None
        else:
            # The difference between unix time and the CPU time. Do not cache this
            # number as it can change with clock slew or other time adjustments.
            cpu_dt = time.time() - time.monotonic()
            # Reconstruct full CPU time of the frame in us
            timestamp += self.encoder.firsttimestamp
            # Convert to datetime
            frame_timestamp = datetime.fromtimestamp(cpu_dt + timestamp / 1e6)
        if self.ungated_stream is not None:
            self.ungated_stream.add_frame(frame, frame_timestamp)
        try:
            if self.preview is None or self.preview.allows(sensor_us):
                self.stream.add_frame(frame, frame_timestamp)
        except Exception:
            if self.ungated_stream is None:
                raise
            # A preview delivery/gate exception must not kill the shared encoder's
            # polling thread and thereby stop autofocus too. Do not replay frames.
            self.preview_delivery_errors += 1
            if self.preview_delivery_errors & (self.preview_delivery_errors - 1) == 0:
                LOGGER.exception(
                    "Shared MJPEG preview delivery failed: errors=%s",
                    self.preview_delivery_errors,
                )


class PiCamera2StreamingMode(StreamingMode):
    """Streaming mode configuration for the PiCamera2."""

    main_resolution: tuple[int, int]
    lores_resolution: tuple[int, int]
    sensor_mode_resolution: tuple[int, int]
    bit_depth: int
    use_lores_as_preview: bool
    buffer_count: int = 6
    scaler_crop: Optional[tuple[int, int, int, int]] = None

    @property
    def sensor_mode_dict(self) -> dict[str, Any]:
        """The dictionary expected by PiCamera2 for setting sensor mode."""
        return {"output_size": self.sensor_mode_resolution, "bit_depth": self.bit_depth}


class PiCamera2CaptureMode(CaptureMode):
    """Capture mode configuration for the PiCamera2."""

    streaming_mode: Optional[str]
    """The streaming mode the camera should be in for capture.

    Use None to use the active mode.
    """
    stream_name: Literal["lores", "main"]
    """The Picamera stream name to capture."""
    timeout: float = 5.0
    """The timeout. A number above 10 risks hardlocking the Pi."""


class StreamingPiCamera2(BaseCamera, ABC):
    """A Thing that provides and interface to the Raspberry Pi Camera.

    This is an abstract base class for all picamera models. Use a subclass specific
    to the camera model.
    """

    _focus_fom: int
    supports_focus_fom: bool = True
    tuning: dict = lt.setting(default_factory=dict, readonly=True)
    """The Raspberry PiCamera Tuning File JSON."""

    # Subclasses should defined both of these.
    _camera_board: str
    _sensor_info: recalibrate_utils.SensorInfo

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        camera_num: int = 0,
        tuning_file: Optional[str] = None,
    ) -> None:
        """Initialise the camera with the given camera number.

        This makes no connection to the camera (except to get the default tuning file).

        :param thing_server_interface: The interface between this Thing and the server.
        :param camera_num: The number of the camera. This should generally be left as 0
            as most Raspberry Pi boards only support 1 camera.
        :param tuning_file: Optional platform-specific camera tuning file.
        """
        super().__init__(thing_server_interface)
        self._setting_save_in_progress = False
        self._camera_num = camera_num

        # Check the subclass defined the _camera_board and _sensor_info
        if not hasattr(self, "_camera_board") or not hasattr(self, "_sensor_info"):
            raise AttributeError(
                f"{type(self).__name__} must define both both _camera_board and "
                "_sensor_info attributes"
            )

        self._measurement_preview = MeasurementPreview()
        self._raw_pending: PendingRequest | None = None
        self._picamera_lock = RLock()
        self._picamera = None

        # Load the tuning file for the specified sensor mode.
        self.default_tuning = tf_utils.load_default_tuning(
            self._sensor_info.sensor_model, tuning_path=tuning_file
        )

        # Set tuning to default tuning. This will be overwritten when the Thing is
        # connected to the server if tuning is saved to disk.
        try:
            self.tuning = copy.deepcopy(self.default_tuning)
        except ServerNotRunningError as e:
            # This will throw an error after setting as we are not connected to
            # a server. But we know this, so we ignore the error as long as the
            # tuning data is set.
            if "version" not in self.tuning:
                raise RuntimeError("Tuning file could not be set.") from e

        # Also set the colour gains based on the tuning. Set to _colour_gains to not
        # trigger a ServerNotRunningError
        self._colour_gains = tf_utils.get_colour_gains_from_lst(self.tuning)

    mjpeg_bitrate: Optional[int] = lt.property(default=100000000, readonly=True)
    """Retired preview bitrate, retained read-only for older clients.

    Kept read-only for older clients. It does not configure the independent
    autofocus encoder, whose commissioned bitrate remains unchanged.
    """

    preview_jpeg_quality: int = lt.property(default=90, ge=1, le=100)
    """Software-only preview JPEG quality, applied when streaming next starts.

    It never changes capture JPEGs, RAW, or the commissioned low-resolution
    autofocus encoding.
    """

    stream_active: bool = lt.property(default=False, readonly=True)
    """Whether the MJPEG stream is active."""

    def save_settings(self) -> None:
        """Override save_settings to ensure that camera properties don't recurse.

        This method is run by any Thing when a setting is saved. However, the
        method reads the setting. As reading the setting talks to the
        camera and calls save_settings if the value is not as expected, this could
        cause recursion. Also this means that saving one setting causes all others
        to be read each time.
        """
        try:
            self._setting_save_in_progress = True
            super().save_settings()
        finally:
            self._setting_save_in_progress = False

    @lt.property
    def calibration_required(self) -> bool:
        """Whether the camera needs calibrating."""
        # Check if the lens shading table is calibrated.
        return not tf_utils.lst_calibrated(self.tuning)

    ## Persistent controls! These are settings

    _analogue_gain: float = 1.0

    @lt.setting
    def analogue_gain(self) -> float:
        """The Analogue gain applied by the camera sensor."""
        if not self._setting_save_in_progress and self.streaming:
            with self._streaming_picamera() as cam:
                cam_value = cam.capture_metadata()["AnalogueGain"]
            if cam_value != self._analogue_gain:
                self._analogue_gain = cam_value
                self.save_settings()
        return self._analogue_gain

    @analogue_gain.setter
    def _set_analogue_gain(self, value: float) -> None:
        self._analogue_gain = value
        if self.streaming:
            with self._streaming_picamera() as cam:
                cam.set_controls({"AnalogueGain": value})

    _colour_gains: tuple[float, float] = (1.0, 1.0)

    @lt.setting
    def colour_gains(self) -> tuple[float, float]:
        """The red and blue colour gains, must be between 0.0 and 32.0."""
        if not self._setting_save_in_progress and self.streaming:
            with self._streaming_picamera() as cam:
                cam_value = cam.capture_metadata()["ColourGains"]
            if cam_value != self._colour_gains:
                self._colour_gains = cam_value
                self.save_settings()
        return self._colour_gains

    @colour_gains.setter
    def _set_colour_gains(self, value: tuple[float, float]) -> None:
        self._colour_gains = value
        if self.streaming:
            with self._streaming_picamera() as cam:
                cam.set_controls({"ColourGains": value})

    _exposure_time: int = 500

    @lt.setting
    def exposure_time(self) -> int:
        """The camera exposure time in microseconds.

        When setting this property the camera will adjust the set value
        to the nearest allowed value that is lower than the current setting.
        """
        if not self._setting_save_in_progress and self.streaming:
            with self._streaming_picamera() as cam:
                cam_value = cam.capture_metadata()["ExposureTime"]
            if cam_value != self._exposure_time:
                self._exposure_time = cam_value
                self.save_settings()
        return self._exposure_time

    @exposure_time.setter
    def _set_exposure_time(self, value: int) -> None:
        self._exposure_time = value
        if self.streaming:
            with self._streaming_picamera() as cam:
                # Note: This set a value 1 higher than requested as picamera2 always
                # sets a lower value than requested, even if the requested is allowed
                cam.set_controls({"ExposureTime": value + 1})

    def _get_persistent_controls(self) -> dict:
        if self.streaming:
            self.discard_frames()
        return {
            "AeEnable": False,
            "AnalogueGain": self.analogue_gain,
            "AwbEnable": False,
            "Brightness": 0,
            "ColourGains": self.colour_gains,
            "Contrast": 1,
            # Must also set plus 1 or the exposure drifts with start and stop stream.
            "ExposureTime": self.exposure_time + 1,
            "Saturation": 1,
            "Sharpness": 1,
        }

    @lt.property
    def sensor_resolution(self) -> Optional[tuple[int, int]]:
        """The native resolution of the camera's sensor."""
        with self._streaming_picamera() as cam:
            return cam.sensor_resolution

    def _initialise_picamera(self, check_sensor_model: bool = False) -> None:
        """Acquire the picamera device and store it as ``self._picamera``.

        This duplicates logic in ``Picamera2.__init__`` to provide a tuning file that
        will be read when the camera system initialises.

        :param check_sensor_model: Set to true to check the sensor model is the
            expected sensor model. This is used on ``__enter__`` to confirm that the
            real camera matches the expected camera.

        :raises PicameraModelError: If check_sensor_model is True and the real
            camera sensor model doesn't match the expected sensor model.
        """
        with self._picamera_lock, tempfile.NamedTemporaryFile("w") as tuning_file:
            json.dump(self.tuning, tuning_file)
            tuning_file.flush()  # but leave it open as closing it will delete it
            os.environ["LIBCAMERA_RPI_TUNING_FILE"] = tuning_file.name

            if self._picamera is not None:
                LOGGER.info("Closing picamera object for reinitialisation")
                LOGGER.info(
                    "Camera object already exists, closing for reinitialisation"
                )
                self._picamera.close()
                LOGGER.info("Picamera closed, deleting picamera")
                del self._picamera
                recalibrate_utils.recreate_camera_manager()

            LOGGER.info("Creating new Picamera2 object")
            # Specify tuning file otherwise it will be overwritten with None.
            self._picamera = Picamera2(
                camera_num=self._camera_num,
                tuning=self.tuning,
            )
            if self._picamera is None:
                # Type narrow (error if failure)
                raise RuntimeError("Failed to start Picamera")

            self._picamera.pre_callback = self._on_frame_complete

            if check_sensor_model:
                hw_sensor_model = self._picamera.camera_properties["Model"]
                if hw_sensor_model != self._sensor_info.sensor_model:
                    raise PicameraModelError(
                        f"Wrong Picamera model. Expecting {self._sensor_info.sensor_model}, "
                        f"but found {hw_sensor_model}."
                    )

    @lt.property
    def measurement_preview_status(self) -> dict:
        """Report a held WHITE preview, without pretending the retained image is live."""
        return self._measurement_preview.status

    @property
    def focus_fom(self) -> int:
        """Return the focus figure of merit."""
        return self._focus_fom

    def _on_frame_complete(self, request: Request) -> None:
        md = request.get_metadata()
        self._measurement_preview.record(md)
        fom = md.get("FocusFoM")
        if fom is not None:
            self._focus_fom = fom

    def __enter__(self) -> Self:
        """Start streaming when the Thing context manager is opened.

        This opens the picamera connection, initialises the camera, sets the
        property, and then starts the streams.
        """
        super().__enter__()
        self._initialise_picamera(check_sensor_model=True)
        self._start_streaming()
        return self

    @property
    def streaming(self) -> bool:
        """True if the camera is streaming."""
        return self._picamera is not None and self._picamera.started

    @contextmanager
    def _streaming_picamera(
        self, pause_stream: bool = False, *, restore_controls: dict | None = None
    ) -> Iterator[Picamera2]:
        """Lock access to picamera and return the underlying ``Picamera2`` instance.

        Optionally the stream can be paused to allow updating the camera settings.

        :param pause_stream: If False the ``Picamera2`` instance is simply yielded.
            If True:

                * Stop the MJPEG Stream
                * Yield the ``Picamera2`` instance for function calling the context manager to
                    make changes.
                * On closing of the context manager the stream will restart.
        """
        already_streaming = self.stream_active
        streaming_mode = self.streaming_mode
        with self._picamera_lock:
            if pause_stream and already_streaming:
                self._stop_streaming(stop_web_stream=False)
            try:
                yield self._picamera
            finally:
                if pause_stream and already_streaming:
                    if restore_controls is None:
                        self._start_streaming(streaming_mode)
                    else:
                        self._start_streaming(streaming_mode, controls=restore_controls)

    def __exit__(
        self,
        exc_type: type[BaseException],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Close the picamera connection when the Thing context manager is closed."""
        self._stop_streaming()
        with self._streaming_picamera() as cam:
            cam.close()
        del self._picamera
        super().__exit__(exc_type, exc_value, traceback)

    @abstractmethod
    @lt.property
    def streaming_modes(self) -> Mapping[str, PiCamera2StreamingMode]:
        """Modes the camera can stream in."""

    @abstractmethod
    @lt.property
    def capture_modes(self) -> Mapping[str, PiCamera2CaptureMode]:
        """Modes the camera can use for capturing."""

    def _create_picam_config_from_mode_info(
        self,
        picam: Picamera2,
        controls: dict[str, Any],
        mode_info: PiCamera2StreamingMode,
    ) -> dict[str, Any]:
        """Create the dictionary to pass to ``Picamera2.configure``.

        :param controls: The controls for the camera. Note that running
            ``_get_persistent_controls()`` should be done before getting the picamera
            lock to ensure that the current settings are read from the camera.
        """
        controls = dict(controls)
        if mode_info.scaler_crop is not None:
            controls["ScalerCrop"] = mode_info.scaler_crop

        stream_config = picam.create_video_configuration(
            main={"size": mode_info.main_resolution},
            lores={"size": mode_info.lores_resolution, "format": "YUV420"},
            sensor=mode_info.sensor_mode_dict,
            controls=controls,
        )
        stream_config["buffer_count"] = mode_info.buffer_count
        return stream_config

    def _configure_picamera(
        self, picam: Picamera2, controls: dict, mode_info: PiCamera2StreamingMode
    ) -> None:
        """Configure a mode, then apply crop after Picamera2's aspect-ratio defaults.

        Recent Picamera2 sets ScalerCrops during configure(), overwriting the
        legacy ScalerCrop. Apply both output crops afterwards when supported.
        Older VC4 stacks continue to receive the singular control.
        """
        config = self._create_picam_config_from_mode_info(picam, controls, mode_info)
        picam.configure(config)
        if mode_info.scaler_crop is not None:
            if "ScalerCrops" in picam.camera_controls:
                picam.set_controls({"ScalerCrops": [mode_info.scaler_crop] * 2})
            else:
                picam.set_controls({"ScalerCrop": mode_info.scaler_crop})

    def _start_streaming(
        self, mode: str = "default", *, controls: dict | None = None
    ) -> None:
        """Start the MJPEG stream. This is where persistent controls are sent to camera.

        Sets the camera stream resolutions based on input mode.

        Create two streams:

        * ``lores_mjpeg_stream`` for autofocus at ``lores_resolution``
        * ``mjpeg_stream`` for preview. This is at the ``main_resolution`` unless
            ``use_lores_as_preview`` is True, in which case it uses lores pixels.

        Preview is encoded independently in software to preserve its JPEG quality.
        The hardware lores autofocus encoding remains unchanged in every mode.
        """
        if mode not in self.streaming_modes:
            raise ValueError(f"Unknown mode {mode}")

        # This must be before getting the picamera hardware lock.
        if controls is None:
            controls = self._get_persistent_controls()

        mode_info = self.streaming_modes[mode]
        preview_source = "lores" if mode_info.use_lores_as_preview else "main"

        with self._streaming_picamera() as picam:
            # Publish the mode and its transition state under the same lock as
            # configure(): a metadata reader must not see new mode/old hardware.
            self.streaming_mode = mode
            self.stream_active = False
            try:
                if picam.started:
                    picam.stop()
                    picam.stop_encoder()  # make sure there are no other encoders going
                self._configure_picamera(
                    picam=picam,
                    controls=controls,
                    mode_info=mode_info,
                )
                preview_encoder = JpegEncoder(
                    num_threads=1, q=self.preview_jpeg_quality
                )
                picam.start_recording(
                    preview_encoder,
                    PicameraStreamOutput(
                        self.mjpeg_stream,
                        preview_encoder,
                        self._measurement_preview,
                    ),
                    name=preview_source,
                )
                encoder = MJPEGEncoder(100000000)
                picam.start_encoder(
                    encoder,
                    PicameraStreamOutput(self.lores_mjpeg_stream, encoder),
                    name="lores",
                )
            except Exception as e:
                LOGGER.error(f"Error while starting preview: {e}.")
                raise
            else:
                self.stream_active = True
                LOGGER.debug("Started MJPEG stream in %s mode.", mode)

    def _stop_streaming(self, stop_web_stream: bool = True) -> None:
        """Stop the MJPEG stream."""
        with self._streaming_picamera() as picam:
            try:
                picam.stop_recording()  # This should also stop the extra lores encoder
            except Exception as e:
                LOGGER.info("Stopping recording failed")
                LOGGER.exception(e)
            else:
                self.stream_active = False
                if stop_web_stream:
                    self.mjpeg_stream.stop()
                    self.lores_mjpeg_stream.stop()
                LOGGER.info("Stopped MJPEG stream.")

            # Adding a sleep to prevent camera getting confused by rapid commands
            time.sleep(self._sensor_info.short_pause)

    @lt.action
    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh."""
        with self._streaming_picamera() as cam:
            cam.capture_metadata()

    @contextmanager
    def _ensure_mode_for_capture(
        self, capture_mode_info: PiCamera2CaptureMode
    ) -> Iterator[Picamera2]:
        """Ensure in correct mode for capture.

        If the camera is already in the correct mode, the stream isn't paused and
        this is the same as using ``self._streaming_picamera()``.

        Otherwise, pause stream, and switch mode. Mode is reset and stream
        restarts after the context manager closes.
        """
        required_streaming_mode = capture_mode_info.streaming_mode
        if (
            required_streaming_mode is None
            or required_streaming_mode == self.streaming_mode
        ):
            with self._streaming_picamera() as cam:
                yield cam
        else:
            streaming_mode_info = self.streaming_modes[required_streaming_mode]

            # This must be before getting the picamera hardware lock.
            controls = self._get_persistent_controls()

            with self._streaming_picamera(pause_stream=True) as cam:
                LOGGER.debug("Reconfiguring camera for full resolution capture")
                self._configure_picamera(
                    picam=cam,
                    controls=controls,
                    mode_info=streaming_mode_info,
                )
                cam.start()
                time.sleep(self._sensor_info.short_pause)
                yield cam

    def _capture_image(self, capture_mode: str = "standard") -> Image.Image:
        """Acquire one image from the camera and return it as a PIL Image.

        :param capture_mode: The capture mode to use. See the description field of each
            mode in ``capture_modes`` for more detail.

        :raises TimeoutError: if this time is exceeded during capture.
        """
        capture_mode = self._validate_capture_mode(capture_mode)
        capture_mode_info = self.capture_modes[capture_mode]

        with self._ensure_mode_for_capture(capture_mode_info) as cam:
            return cam.capture_image(
                capture_mode_info.stream_name, wait=capture_mode_info.timeout
            )

    @lt.action
    def capture_as_array(
        self,
        capture_mode: str = "standard",
        raw: bool = False,
    ) -> NDArray:
        """Acquire one image from the camera and return as an array.

        This function will produce a nested list containing an uncompressed RGB image.
        It's likely to be highly inefficient - raw and/or uncompressed captures using
        binary image formats will be added in due course.

        :param capture_mode: (Optional) The name of the capture mode as defined by the
            camera.
        :param raw: Whether to capture RAW data. Capturing RAW data may ignore some
            of the camera mode settings.

        :raises TimeoutError: if this time is exceeded during capture.
        """
        if raw:
            # Raw cannot use _capture_image.
            capture_mode = self._validate_capture_mode(capture_mode)
            capture_mode_info = self.capture_modes[capture_mode]

            with self._ensure_mode_for_capture(capture_mode_info) as cam:
                return cam.capture_array(name="raw", wait=capture_mode_info.timeout)

        # Note that internally the PiCamera creates a PIL image and then converts to
        # numpy with ``np.array(Image.open(io.BytesIO(self.make_buffer(name))))``.
        # As such we use _capture_image to get an Image from the picamera and return
        # as array
        return np.array(self._capture_image(capture_mode))

    @lt.property
    def focus_configuration(self) -> dict:
        """Stable camera/tuning identity for WHITE Z-calibration compatibility."""
        return {
            "sensor": self._sensor_info.sensor_model,
            "streaming_mode": self.streaming_mode,
            "mode": self.streaming_modes[self.streaming_mode].model_dump(mode="json"),
            "tuning_sha256": hashlib.sha256(
                json.dumps(self.tuning, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        }

    def capture_settled_frame(self, timeout_s: float) -> tuple[NDArray, dict]:
        """Capture RGB and metadata from one request exposed after this call.

        Python-only measurement helper, no camera reconfiguration or second owner.
        Picamera2 manual 6.4.1 defines flush relative to the first pixel exposure.
        The request is released even when metadata or conversion is invalid.
        """
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 30:
            raise ValueError("Invalid focus capture timeout")
        with self._streaming_picamera() as cam:
            boundary = time.monotonic_ns()
            request = cam.capture_request(wait=timeout_s, flush=boundary)
            try:
                metadata = dict(request.get_metadata())
                timestamp = metadata.get("SensorTimestamp")
                exposure = metadata.get("ExposureTime")
                gain = metadata.get("AnalogueGain")
                if (
                    type(timestamp) is not int
                    or type(exposure) is not int
                    or exposure <= 0
                    or not isinstance(gain, (int, float))
                    or not math.isfinite(gain)
                    or gain <= 0
                ):
                    raise ValueError("Missing or invalid focus exposure metadata")
                exposure_start = timestamp - exposure * 1000
                if exposure_start < boundary:
                    raise ValueError("Stale focus exposure after stage settling")
                source = request.make_image("main")
                if source.mode not in ("RGB", "RGBX", "RGBA"):
                    raise ValueError("Focus capture requires an RGB8 main stream")
                # Picamera2 uses RGBX/RGBA for the normal XBGR8888 video stream.
                # Drop only the non-colour byte; do not blend, reorder or resize RGB.
                image = np.asarray(source.convert("RGB")).copy()
                if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
                    raise ValueError("Focus capture requires an RGB8 main stream")
                return image, {
                    "exposure_start_ns": exposure_start,
                    "sensor_timestamp_ns": timestamp,
                    "exposure_time_us": exposure,
                    "analogue_gain": float(gain),
                    "colour_gains": list(metadata["ColourGains"]),
                    "scaler_crop": list(metadata["ScalerCrop"]),
                    "image_size": [image.shape[1], image.shape[0]],
                }
            finally:
                request.release()

    @lt.property
    def raw_measurement_configuration(self) -> dict:
        """Read fixed RAW geometry and manual controls, without changing the camera."""
        if self._sensor_info.sensor_model != "imx477" or not self.stream_active:
            raise ValueError("R/G measurement requires a running HQ camera")
        with self._streaming_picamera() as cam:
            config = cam.camera_configuration()
            controls = config.get("controls", {})
            if (
                controls.get("AeEnable") is not False
                or controls.get("AwbEnable") is not False
            ):
                raise ValueError(
                    "Disable auto exposure and auto white balance before R/G"
                )
            crop = self.streaming_modes[self.streaming_mode].scaler_crop
            geometry = hq_geometry(config, list(crop or (0, 0, 4056, 3040)))
        # Avoid property getters that acquire extra frames or save camera settings.
        # Same-request metadata is checked separately against these manual settings.
        return {
            "geometry": geometry,
            "exposure_time_us": self._exposure_time,
            "analogue_gain": self._analogue_gain,
            "colour_gains": list(self._colour_gains),
            "camera": self.focus_configuration,
        }

    def capture_linear_frame(self, timeout_s: float) -> tuple[NDArray, NDArray, dict]:
        """Borrow one fresh request for RAW, RGB and metadata in the current mode.

        Light must already be stable and remain unchanged until this returns.
        CompletedRequest means all sensor rows have finished; first-pixel start is
        checked explicitly. A timed-out job retains a release-only callback, and
        another measurement is refused while that job remains outstanding.
        """
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 10:
            raise ValueError("RAW capture timeout must be in (0, 10] seconds")
        binding = self.raw_measurement_configuration
        with self._streaming_picamera() as cam:
            if self._raw_pending is not None and not self._raw_pending.finished:
                raise RuntimeError("Previous RAW request is still pending")
            boundary = time.monotonic_ns()
            pending = PendingRequest()
            self._raw_pending = pending
            cam.capture_request(
                wait=False, signal_function=pending.complete, flush=boundary
            )
            request = pending.take(timeout_s, lt.raise_if_cancelled)
            try:
                metadata = dict(request.get_metadata())
                info = exposure_metadata(metadata, boundary)
                if info["exposure_time_us"] != binding[
                    "exposure_time_us"
                ] or not math.isclose(
                    info["analogue_gain"], binding["analogue_gain"], rel_tol=1e-5
                ):
                    raise ValueError(
                        "RAW exposure/gain does not match fixed manual settings"
                    )
                digital_gain = float(metadata["DigitalGain"])
                colour_gains = np.asarray(metadata["ColourGains"], dtype=float)
                colour_matrix = np.asarray(
                    metadata["ColourCorrectionMatrix"], dtype=float
                )
                if (
                    not math.isfinite(digital_gain)
                    or digital_gain <= 0
                    or colour_gains.shape != (2,)
                    or not np.allclose(
                        colour_gains, binding["colour_gains"], rtol=1e-5, atol=0
                    )
                    or colour_matrix.shape != (9,)
                    or not np.isfinite(colour_matrix).all()
                ):
                    raise ValueError(
                        "RAW reference processing/gains are not fixed or valid"
                    )
                info["processing"] = {
                    "digital_gain": digital_gain,
                    "colour_gains": colour_gains.tolist(),
                    "colour_correction_matrix": colour_matrix.tolist(),
                    "colour_temperature": metadata.get("ColourTemperature"),
                    "ae_enabled": False,
                    "awb_enabled": False,
                }
                geometry = hq_geometry(request.config, metadata["ScalerCrop"])
                if geometry != binding["geometry"]:
                    raise ValueError("Camera crop/mode changed during RAW capture")
                planes = decode_hq_raw(request.make_array("raw"), geometry)
                source = request.make_image("main")
                if source.mode not in ("RGB", "RGBX", "RGBA"):
                    raise ValueError("WHITE reference requires an RGB8 main stream")
                rgb = np.asarray(source.convert("RGB")).copy()
                if list(rgb.shape[:2][::-1]) != geometry["white_size"]:
                    raise ValueError("WHITE reference geometry changed")
                info.update(geometry=geometry, binding=binding)
                return planes, rgb, info
            finally:
                request.release()

    @lt.property
    def camera_configuration(self) -> Mapping:
        """The "configuration" dictionary of the picamera2 object.

        The "configuration" sets the resolution and format of the camera's streams.
        Together with the "tuning" it determines how the sensor is configured and
        how the data is processed.

        Note that the configuration may be modified when taking still images, and
        this property refers to whatever configuration is currently in force -
        usually the one used for the preview stream.
        """
        with self._streaming_picamera() as cam:
            configuration = dict(cam.camera_configuration())
        # libcamera's pybind objects validate as Any but cannot be encoded as JSON.
        # Keep the camera's own configuration intact: it is reused to configure it.
        for field in ("transform", "colour_space"):
            if field in configuration:
                configuration[field] = str(configuration[field])
        controls = dict(configuration.get("controls", {}))
        if "NoiseReductionMode" in controls:
            controls["NoiseReductionMode"] = int(controls["NoiseReductionMode"])
        configuration["controls"] = controls
        return configuration

    @lt.property
    def capture_metadata(self) -> dict:
        """Return the metadata from the camera."""
        with self._streaming_picamera() as cam:
            return cam.capture_metadata()

    @lt.action
    def auto_expose_from_minimum(
        self,
        target_white_level: Optional[int] = None,
        percentile: float = 99.9,
    ) -> None:
        """Adjust exposure until a the target white level is reached.

        Starting from the minimum exposure, gradually increase exposure until
        the image reaches the specified white level.

        :param target_white_level: Raw target white level, this should be an integer
            within the range set by the bit-depth of the camera sensor (10-bit for
            PiCamera v2, 12 Bit for Picamera HQ. If None the default will be used for
            the current sensor. This is approximately 40% saturated, but after gamma
            curve is applied, the pixel values will have a value around 200.
        :param percentile: The percentile to use instead of maximum. Default 99.9. When
            calculating the brightest pixel, a percentile is used rather than the
            maximum in order to be robust to a small number of noisy/bright pixels.
        """
        if target_white_level is None:
            target_white_level = self._sensor_info.default_target_white_level

        with self._streaming_picamera(pause_stream=True) as cam:
            recalibrate_utils.adjust_shutter_and_gain_from_raw(
                cam,
                self._sensor_info,
                target_white_level=target_white_level,
                percentile=percentile,
            )

    @lt.action
    def calibrate_lens_shading(self) -> None:
        """Take an image and use it for flat-field correction.

        This method requires an empty (i.e. bright) field of view. It will take
        a raw image and effectively divide every subsequent image by the current
        one. This uses the camera's "tuning" file to correct the preview and
        the processed images. It should not affect raw images.
        """
        controls = self._get_persistent_controls()
        with self._streaming_picamera(pause_stream=True) as cam:
            # Suppress lint warning that L, Cr, and Cb are not lowercase, as these are
            # the standard mathematical terms for:
            # luminance (L), red-difference chroma (Cr), and blue-difference chroma
            # (Cb).
            L, Cr, Cb = recalibrate_utils.lst_from_camera(  # noqa: N806
                cam,
                self._sensor_info,
                grid_shape=tf_utils.lens_shading_shape(self.tuning),
                controls=controls,
            )
            self.tuning = tf_utils.set_lst(
                self.tuning,
                luminance=L,
                cr=Cr,
                cb=Cb,
                colour_temp=tf_utils.CALIBRATED_COLOUR_TEMP,
            )

            # Re-initialise the picamera to reload the tuning file.
            self._initialise_picamera()

        self.colour_gains = tf_utils.get_colour_gains_from_lst(self.tuning)

    @lt.action
    def calibration_frame_info(self) -> dict:
        """Inspect unpacked RAW through the camera owner without applying calibration.

        Preview pauses for one full-sensor frame and resumes in its previous mode.
        No tuning, illumination or stage changes are made. Levels include black.
        """
        controls = self._get_persistent_controls()
        with self._streaming_picamera(pause_stream=True) as cam:
            channels, configuration, metadata = (
                recalibrate_utils.capture_calibration_frame(
                    cam, self._sensor_info, controls=controls
                )
            )
        raw = configuration["raw"]
        sensor_bits = configuration["sensor"]["bit_depth"]
        return {
            "raw": raw,
            "sensor_bit_depth": sensor_bits,
            "right_shift": 16 - sensor_bits if raw["format"].endswith("16") else 0,
            "black_level": self._sensor_info.blacklevel,
            "grid_shape": tf_utils.lens_shading_shape(self.tuning),
            "channel_order": "BGGR",
            "channel_min": np.min(channels, axis=(1, 2)).tolist(),
            "channel_max": np.max(channels, axis=(1, 2)).tolist(),
            "channel_median": np.median(channels, axis=(1, 2)).tolist(),
            "channel_p999": np.percentile(channels, 99.9, axis=(1, 2)).tolist(),
            "exposure_time": metadata["ExposureTime"],
            "analogue_gain": metadata["AnalogueGain"],
        }

    @lt.property
    def colour_correction_matrix(
        self,
    ) -> tuple[float, float, float, float, float, float, float, float, float]:
        """The ``colour_correction_matrix`` from the tuning file.

        This is broken out into its own property for convenience and compatibility with
        the micromanager API

        It is a 9 value tuple used to specify the 3x3 matrix that the GPU pipeline uses
        to convert from the camera R,G,B vector to the standard R,G,B.
        """
        return tuple(tf_utils.get_ccm(self.tuning))

    @colour_correction_matrix.setter  # type: ignore
    def colour_correction_matrix(
        self,
        value: tuple[float, float, float, float, float, float, float, float, float],
    ) -> None:
        self.tuning = tf_utils.set_ccm(self.tuning, value)

        if self._picamera is not None:
            with self._streaming_picamera(pause_stream=True):
                self._initialise_picamera()

    @lt.action
    def reset_ccm(self) -> None:
        """Overwrite the colour correction matrix in camera tuning with default values."""
        self.tuning = tf_utils.copy_algo_from_other_tuning(
            algo="rpi.ccm",
            base_tuning_file=self.tuning,
            copy_from=self.default_tuning,
        )

    @lt.property
    def gamma_correction(self) -> list[int]:
        """Return the gamma correction curve from the tuning file."""
        return tf_utils.get_gamma_curve(self.tuning)

    @lt.action
    def set_static_green_equalisation(self, offset: int = 65535) -> None:
        """Set the green equalisation to a static value.

        Green equalisation avoids the debayering algorithm becoming confused
        by the two green channels having different values, which is a problem
        when the chief ray angle isn't what the sensor was designed for, and
        that's the case in e.g. a microscope using camera module v2.

        A value of 0 here does nothing, a value of 65535 is maximum correction.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.set_static_geq(self.tuning, offset)
            self._initialise_picamera()

    @lt.action
    def set_ce_enable_to_off(self) -> None:
        """Set the contrast enhancement to disabled.

        Adaptive contrast enhancement modifies settings to adapt to each field
        of view, causing inconsistent settings when capturing.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.set_ce_to_disabled(self.tuning)
            self._initialise_picamera()

    @lt.action
    def full_auto_calibrate(self) -> None:
        """Perform a full auto-calibration.

        This function will call the other calibration actions in sequence:

        * ``flat_lens_shading`` to disable flat-field
        * ``auto_expose_from_minimum``
        * ``set_static_green_equalisation`` to set geq offset to max
        * ``calibrate_lens_shading`` (also sets colour gains for white balance)
        * ``set_background``
        """
        with self._picamera_lock:
            previous_tuning = copy.deepcopy(self.tuning)
            previous_controls = self._get_persistent_controls()
            try:
                self.flat_lens_shading()
                self.auto_expose_from_minimum()
                self.set_static_green_equalisation()
                self.set_ce_enable_to_off()
                self.calibrate_lens_shading()
                if self.background_detector is not None:
                    for _i in range(3):
                        try:
                            time.sleep(self._sensor_info.long_pause)
                            self.set_background()
                            return
                        except ChannelBlankError:
                            pass
                raise RuntimeError("Couldn't set background")
            except Exception:
                # A failed wizard must not leave flattened/partly calibrated tuning
                # marked as a successful calibration. Keep the previous profile.
                with self._streaming_picamera(pause_stream=True):
                    self._analogue_gain = previous_controls["AnalogueGain"]
                    self._colour_gains = previous_controls["ColourGains"]
                    self._exposure_time = previous_controls["ExposureTime"] - 1
                    self.tuning = previous_tuning
                    self._initialise_picamera()
                self.save_settings()
                raise

    @lt.property
    def primary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions for both calibration wizard and settings panel."""
        return [
            action_button_for(
                self,
                "full_auto_calibrate",
                submit_label="Full Auto-Calibrate",
                can_terminate=False,
                requires_confirmation=True,
                confirmation_message=(
                    "Start recalibration? This may take a while, and the microscope "
                    "will be locked during this time."
                ),
                notify_on_success=True,
                success_message="Finished recalibration.",
            ),
        ]

    @lt.property
    def secondary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions that appear only in settings panel."""
        return [
            action_button_for(
                self,
                "auto_expose_from_minimum",
                submit_label="Auto Gain & Shutter Speed",
                can_terminate=False,
                button_primary=False,
            ),
            action_button_for(
                self,
                "calibrate_lens_shading",
                submit_label="Auto Flat Field Correction",
                can_terminate=False,
                button_primary=False,
                requires_confirmation=True,
                confirmation_message=(
                    "Is the microscope looking at an evenly illuminated, empty field "
                    "of view? If not, the current image will show through in any "
                    "images captured afterwards."
                ),
            ),
            action_button_for(
                self,
                "flat_lens_shading",
                submit_label="Disable Flat Field Correction",
                can_terminate=False,
                button_primary=False,
            ),
            action_button_for(
                self,
                "flat_lens_shading_chrominance",
                submit_label="Disable Flat Field Chrominance",
                can_terminate=False,
                button_primary=False,
            ),
            action_button_for(
                self,
                "reset_lens_shading",
                submit_label="Reset Flat Field Correction",
                can_terminate=False,
                button_primary=False,
            ),
        ]

    @lt.property
    def manual_camera_settings(self) -> list[PropertyControl]:
        """The camera settings to expose as property controls in the settings panel."""
        return [
            property_control_for(
                self,
                "exposure_time",
                label="Exposure Time (0-33251)",
                read_back=True,
                read_back_delay=1000,
            ),
            property_control_for(
                self,
                "analogue_gain",
                label="Analogue Gain",
                read_back=True,
                read_back_delay=1000,
            ),
            property_control_for(
                self,
                "colour_gains",
                label="Colour Gains",
                read_back=True,
                read_back_delay=1000,
            ),
        ]

    @lt.property
    def lens_shading_tables(self) -> Optional[tf_utils.LensShadingModel]:
        """The current lens shading (i.e. flat-field correction).

        Return the current lens shading correction, as three 2D lists each with
        dimensions 12x16 for VC4 or 32x32 for PiSP.

        The colour temperature is returned. If the colour temperature us 5000 then this
        means the lens shading tables have been calibrated (with our illumination which
        has a 5000k colour temperature). Other numbers are set when flatening or
        resetting the table.
        """
        return tf_utils.get_lst(self.tuning)

    @lt.action
    def flat_lens_shading(self) -> None:
        """Disable flat-field correction.

        This method will set a completely flat lens shading table. It is not the
        same as the default behaviour, which is to use an adaptive lens shading
        table.

        This flat table is used to take an image with no lens shading so that the
        correct lens shading table can be calibrated.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.flatten_lst(self.tuning)
            self._initialise_picamera()

    @lt.action
    def flat_lens_shading_chrominance(self) -> None:
        """Disable flat-field correction for colour only.

        This method will set the chrominance of the lens shading table to be
        flat, i.e. we'll correct vignetting of intensity, but not any change in
        colour across the image.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.flatten_lst(self.tuning, keep_luminance=True)
            self._initialise_picamera()

    @lt.action
    def reset_lens_shading(self) -> None:
        """Revert to default lens shading settings.

        This method will restore the default "adaptive" lens shading method used
        by the Raspberry Pi camera.
        """
        with self._streaming_picamera(pause_stream=True):
            self.tuning = tf_utils.copy_algo_from_other_tuning(
                algo="rpi.alsc",
                base_tuning_file=self.tuning,
                copy_from=self.default_tuning,
            )
            self._initialise_picamera()

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Update generic camera metadata with Picamera-specific data."""
        state = dict(super().thing_state)
        state["camera_board"] = self._camera_board
        state["tuning"] = {
            "exposure_time": self.exposure_time,
            "colour_gains": self.colour_gains,
            "analogue_gain": self.analogue_gain,
            "gamma_correction": self.gamma_correction,
        }
        return state


class PiCameraV2(StreamingPiCamera2):
    """A Thing that provides and interface to the Raspberry Pi Camera V2."""

    _camera_board = "picamera_v2"
    _sensor_info = recalibrate_utils.IMX219_SENSOR_INFO

    @lt.property
    def streaming_modes(self) -> Mapping[str, PiCamera2StreamingMode]:
        """Modes the camera can stream in."""
        return {
            "default": PiCamera2StreamingMode(
                description=(
                    "The standard mode that balances capture resolution and stream "
                    "size."
                ),
                main_resolution=(820, 616),
                lores_resolution=(320, 240),
                sensor_mode_resolution=(3280, 2464),
                bit_depth=10,
                use_lores_as_preview=False,
            ),
            "full_resolution": PiCamera2StreamingMode(
                description=(
                    "Streaming the camera in 8MP full resolution. The preview stream "
                    "sent to the UI will be the low resolution (lores) stream. "
                    "This allows better image capture at expense of preview quality."
                ),
                main_resolution=(3280, 2464),
                lores_resolution=(320, 240),
                sensor_mode_resolution=(3280, 2464),
                bit_depth=10,
                use_lores_as_preview=True,
            ),
        }

    @lt.property
    def capture_modes(self) -> Mapping[str, PiCamera2CaptureMode]:
        """Modes the camera can use for capturing."""
        return {
            "standard": PiCamera2CaptureMode(
                description=(
                    "The standard mode, 2MP image created from downsampling an 8MP "
                    "capture."
                ),
                save_resolution=(1640, 1232),
                streaming_mode="full_resolution",
                stream_name="main",
            ),
            "full": PiCamera2CaptureMode(
                description="A full resolution 8MP capture.",
                streaming_mode="full_resolution",
                stream_name="main",
            ),
            "quick": PiCamera2CaptureMode(
                description="Capture without altering the stream settings.",
                streaming_mode=None,
                stream_name="main",
            ),
        }


class HQCameraProfile(BaseModel):
    """One configured HQ field with aspect-preserving output sizes, without binning.

    Supplied through the PiCameraHQ constructor in the server JSON configuration.
    Four default buffers bound CMA use; full-resolution uses two, as verified by
    the one-shot sensor overview. These counts do not change exposure or FPS.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    roi: tuple[StrictInt, StrictInt, StrictInt, StrictInt] = (0, 0, 4056, 3040)
    default_resolution: tuple[StrictInt, StrictInt] = (1014, 760)
    lores_resolution: tuple[StrictInt, StrictInt] = (1014, 760)
    standard_resolution: tuple[StrictInt, StrictInt] = (2028, 1520)
    full_resolution: tuple[StrictInt, StrictInt] = (4056, 3040)
    default_buffer_count: StrictInt = Field(default=4, ge=2, le=4)
    full_buffer_count: StrictInt = Field(default=2, ge=2, le=2)

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        """Reject out-of-sensor crops, odd YUV sizes, stretching, and upscaling."""
        x, y, width, height = self.roi
        if (
            min(x, y) < 0
            or min(width, height) <= 0
            or x + width > 4056
            or y + height > 3040
            or any(value % 2 for value in self.roi)
        ):
            raise ValueError("HQ ROI must be even, positive, and within 4056x3040")
        for size in (
            self.default_resolution,
            self.lores_resolution,
            self.standard_resolution,
            self.full_resolution,
        ):
            if (
                min(size) <= 0
                or any(value % 2 for value in size)
                or size[0] > width
                or size[1] > height
                or size[0] * height != size[1] * width
            ):
                raise ValueError(
                    "HQ output sizes must be even, within ROI, and preserve its aspect ratio"
                )
        if not (
            self.lores_resolution[0]
            <= self.default_resolution[0]
            <= self.standard_resolution[0]
            <= self.full_resolution[0]
        ):
            raise ValueError(
                "HQ sizes must be ordered lores <= default <= standard <= full"
            )
        return self


class PiCameraHQ(StreamingPiCamera2):
    """A Thing that provides and interface to the Raspberry Pi Camera HQ."""

    _camera_board = "picamera_hq"
    _sensor_info = recalibrate_utils.IMX477_SENSOR_INFO

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        camera_num: int = 0,
        tuning_file: Optional[str] = None,
        hq_profile: HQCameraProfile | dict | None = None,
        jpeg_encoding: JPEGEncoding | dict | None = None,
    ) -> None:
        """Configure one validated HQ field; exposure/WB/tuning stay independent."""
        self._hq_profile = HQCameraProfile.model_validate(
            {} if hq_profile is None else hq_profile
        )
        self._jpeg_encoding = JPEGEncoding.model_validate(
            {} if jpeg_encoding is None else jpeg_encoding
        )
        super().__init__(thing_server_interface, camera_num, tuning_file)

    @lt.property
    def jpeg_measurement_configuration(self) -> dict:
        """Fixed processed geometry/ISP/codec identity, without capturing or saving."""
        with self._streaming_picamera() as cam:
            # A mode switch legitimately stops the camera while holding this
            # lock. Check after it finishes, but still reject a stopped/failed
            # camera rather than returning a cached or invented binding.
            if not self.stream_active or not self.streaming:
                raise ValueError("JPEG measurement requires a running HQ camera")
            if cam.camera_properties.get("Model") != "imx477":
                raise ValueError("JPEG measurement requires the IMX477 sensor")
            config = cam.camera_configuration()
            mode = self.streaming_modes[self.streaming_mode]
            geometry = jpeg_geometry(config, mode.scaler_crop or self._hq_profile.roi)
            if list(mode.main_resolution) != geometry["image_size"]:
                raise ValueError(
                    "Current JPEG dimensions differ from the configured mode"
                )
            isp = jpeg_isp_configuration(config)
            gains = np.asarray(self._colour_gains, dtype=float)
            if (
                type(self._exposure_time) is not int
                or self._exposure_time <= 0
                or not math.isfinite(self._analogue_gain)
                or self._analogue_gain <= 0
                or gains.shape != (2,)
                or not np.isfinite(gains).all()
                or (gains <= 0).any()
            ):
                raise ValueError(
                    "JPEG measurement requires valid fixed manual settings"
                )
            return {
                "measurement_space": "processed-jpeg-rgb8",
                "geometry": geometry,
                "exposure_time_us": self._exposure_time,
                "analogue_gain": self._analogue_gain,
                "colour_gains": gains.tolist(),
                "camera": copy.deepcopy(self.focus_configuration),
                "isp": isp,
                "encoding": self._jpeg_encoding.binding(),
            }

    def capture_jpeg_frame(self, timeout_s: float) -> tuple[bytes, NDArray, dict]:
        """Capture one fresh current-mode JPEG and its same-byte decoded RGB8 array.

        The caller owns light stability. Full exposure is checked after a fresh
        boundary; no mode switch, RAW decoding, or buffered MJPEG is involved.
        RAW and JPEG share the outstanding-request guard, including late cleanup.
        """
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 10:
            raise ValueError("JPEG capture timeout must be in (0, 10] seconds")
        with self._streaming_picamera() as cam:
            if self._raw_pending is not None and not self._raw_pending.finished:
                raise RuntimeError("Previous camera request is still pending")
            binding = self.jpeg_measurement_configuration
            lt.raise_if_cancelled()
            boundary = time.monotonic_ns()
            pending = PendingRequest()
            self._raw_pending = pending
            cam.capture_request(
                wait=False, signal_function=pending.complete, flush=boundary
            )
            request = pending.take(timeout_s, lt.raise_if_cancelled)
            try:
                metadata = dict(request.get_metadata())
                info = exposure_metadata(metadata, boundary)
                info.pop("sensor_black_levels_16bit", None)
                if info["exposure_time_us"] != binding[
                    "exposure_time_us"
                ] or not math.isclose(
                    info["analogue_gain"], binding["analogue_gain"], rel_tol=1e-5
                ):
                    raise ValueError(
                        "JPEG exposure/gain does not match fixed manual settings"
                    )
                processing = jpeg_processing(metadata, binding)
                geometry = jpeg_geometry(request.config, metadata["ScalerCrop"])
                if (
                    geometry != binding["geometry"]
                    or jpeg_isp_configuration(request.config) != binding["isp"]
                ):
                    raise ValueError(
                        "JPEG crop, mode or ISP configuration changed during capture"
                    )
                source = request.make_image("main")
                if list(source.size) != geometry["image_size"]:
                    raise ValueError(
                        "JPEG main image dimensions do not match the request"
                    )
                payload, rgb, timings = encode_decode_jpeg(source, self._jpeg_encoding)
                lt.raise_if_cancelled()
                if self.jpeg_measurement_configuration != binding:
                    raise ValueError(
                        "JPEG measurement configuration changed during capture"
                    )
                info.update(
                    geometry=geometry,
                    binding=binding,
                    processing=processing,
                    request_metadata=json.loads(json.dumps(metadata, allow_nan=False)),
                    source_pixel_mode=source.mode,
                    jpeg_sha256=hashlib.sha256(payload).hexdigest(),
                    jpeg_size_bytes=len(payload),
                    timings=timings,
                )
                return payload, rgb, info
            finally:
                request.release()

    @property
    def mapping_geometry(self) -> dict:
        """Return the pixel/field identity used by CSM, without reading hardware."""
        mode = self.streaming_modes[self.streaming_mode]
        return {
            "camera": "PiCameraHQ",
            "sensor": self._sensor_info.sensor_model,
            "sensor_resolution": list(mode.sensor_mode_resolution),
            "bit_depth": mode.bit_depth,
            "roi": list(mode.scaler_crop or self._hq_profile.roi),
            "image_resolution": list(mode.main_resolution[::-1]),
            "preview_resolution": list(
                mode.lores_resolution
                if mode.use_lores_as_preview
                else mode.main_resolution
            ),
        }

    @lt.action
    def capture_sensor_overview(self, prepared: bool = False) -> dict:  # noqa: C901, PLR0912, PLR0915
        """Capture one diagnostic full-sensor JPEG, then restore the existing preview.

        This does not select a production ROI or certify calibration at the edges.
        Light must already be stable; this action never operates stage or lights.
        """
        if not prepared:
            raise ValueError("Confirm stable illumination and exclusive camera control")
        size = (4056, 3040)
        crop = (0, 0, *size)
        mode = PiCamera2StreamingMode(
            description="Temporary full-sensor edge inspection only",
            main_resolution=size,
            lores_resolution=(640, 480),
            sensor_mode_resolution=size,
            bit_depth=12,
            use_lores_as_preview=True,
            buffer_count=2,
            scaler_crop=crop,
        )
        with self._picamera_lock:
            if not self.stream_active or not self.streaming:
                raise ValueError("Sensor overview requires a running HQ preview")
            cam = cast(Picamera2, self._picamera)
            if cam.camera_properties["Model"] != "imx477":
                raise PicameraModelError(
                    "Sensor overview requires the IMX477 HQ camera"
                )
            if self._raw_pending is not None and not self._raw_pending.finished:
                raise RuntimeError("Previous RAW request is still pending")
            frozen = json.loads(json.dumps(self.camera_configuration))
            controls = dict(frozen.get("controls", {}))
            if (
                controls.get("AeEnable") is not False
                or controls.get("AwbEnable") is not False
            ):
                raise ValueError(
                    "Sensor overview requires fixed exposure and white balance"
                )
            exposure = self._exposure_time
            gain = self._analogue_gain
            colour_gains = tuple(self._colour_gains)
            controls.update(
                ExposureTime=exposure + 1,
                AnalogueGain=gain,
                ColourGains=colour_gains,
                AeEnable=False,
                AwbEnable=False,
            )
            focus_configuration = copy.deepcopy(self.focus_configuration)
            identifier = str(uuid4())
            directory = Path(self.data_dir) / identifier
            directory.mkdir()
            image_path = directory / "sensor-overview.jpg"
            failure: BaseException | None = None
            result: dict = {}
            try:
                with self._streaming_picamera(
                    pause_stream=True, restore_controls=controls
                ) as cam:
                    try:
                        self._configure_picamera(cam, controls, mode)
                        cam.start()
                        lt.raise_if_cancelled()
                        boundary = time.monotonic_ns()
                        pending = PendingRequest()
                        self._raw_pending = pending
                        cam.capture_request(
                            wait=False, signal_function=pending.complete, flush=boundary
                        )
                        request = pending.take(
                            self.capture_modes["full"].timeout, lt.raise_if_cancelled
                        )
                        try:
                            metadata = dict(request.get_metadata())
                            info = exposure_metadata(metadata, boundary)
                            configuration = request.config
                            if (
                                tuple(metadata["ScalerCrop"]) != crop
                                or tuple(configuration["main"]["size"]) != size
                                or tuple(configuration["sensor"]["output_size"]) != size
                                or configuration["sensor"]["bit_depth"] != 12
                            ):
                                raise ValueError(
                                    "Full-sensor crop/size/mode was not confirmed"
                                )
                            if (
                                info["exposure_time_us"] != exposure
                                or not math.isclose(
                                    info["analogue_gain"], gain, rel_tol=1e-5
                                )
                                or not np.allclose(
                                    metadata["ColourGains"],
                                    colour_gains,
                                    rtol=1e-5,
                                    atol=0,
                                )
                                or configuration["controls"].get("AeEnable")
                                is not False
                                or configuration["controls"].get("AwbEnable")
                                is not False
                            ):
                                raise ValueError(
                                    "Overview exposure/gains are not the frozen manual controls"
                                )
                            source = request.make_image("main")
                            if source.size != size or source.mode not in (
                                "RGB",
                                "RGBX",
                                "RGBA",
                            ):
                                raise ValueError(
                                    "Full-sensor overview requires unscaled RGB8 output"
                                )
                            source.convert("RGB").save(
                                image_path, format="JPEG", quality=95, subsampling=0
                            )
                            lt.raise_if_cancelled()
                            result = {
                                **info,
                                "request_metadata": json.loads(
                                    json.dumps(metadata, allow_nan=False)
                                ),
                                "image_size": list(source.size),
                                "scaler_crop": list(metadata["ScalerCrop"]),
                                "sensor_mode": copy.deepcopy(configuration["sensor"]),
                                "request_streams": {
                                    name: copy.deepcopy(configuration.get(name))
                                    for name in ("main", "lores", "raw")
                                },
                            }
                        finally:
                            request.release()
                    except BaseException as exc:
                        failure = exc
                        raise
                    finally:
                        # Stop the temporary mode before the existing context restarts preview.
                        try:
                            if cam.started:
                                cam.stop()
                        except Exception as stop_error:
                            if failure is None:
                                raise
                            failure.add_note(
                                f"Temporary camera stop failed: {stop_error}"
                            )
            except BaseException as exc:
                if failure is None:
                    failure = exc
                elif exc is not failure:
                    failure.add_note(f"Preview restoration raised: {exc}")
            try:
                restored = json.loads(json.dumps(self.camera_configuration))
                if (
                    not self.stream_active
                    or not self.streaming
                    or self.focus_configuration != focus_configuration
                    or restored != frozen
                    or self._exposure_time != exposure
                    or self._analogue_gain != gain
                    or tuple(self._colour_gains) != colour_gains
                ):
                    raise RuntimeError(
                        "Original camera preview/configuration was not restored"
                    )
            except BaseException as restore_error:
                if failure is None:
                    raise
                failure.add_note(f"Preview restoration check failed: {restore_error}")
            if failure is not None:
                raise failure
            with image_path.open("rb") as image_file:
                image_sha256 = hashlib.file_digest(image_file, "sha256").hexdigest()
            result.update(
                id=identifier,
                directory=str(directory),
                image_href=f"/data/{self.name}/{identifier}/sensor-overview.jpg",
                metadata_href=f"/data/{self.name}/{identifier}/sensor-overview.json",
                image_sha256=image_sha256,
                jpeg={"quality": 95, "subsampling": 0},
                source="PiCameraHQ same-request ISP RGB; no R/G flat-field or edge-calibration certification",
                requested_mode=mode.model_dump(mode="json"),
                frozen_configuration=frozen,
                restored_configuration=restored,
                focus_configuration=focus_configuration,
                manual_controls=controls,
                preview_restored=True,
            )
            with (directory / "sensor-overview.json").open("w") as output:
                json.dump(result, output, indent=2, allow_nan=False)
            return result

    @lt.property
    def streaming_modes(self) -> Mapping[str, PiCamera2StreamingMode]:
        """Modes the camera can stream in."""
        return {
            "default": PiCamera2StreamingMode(
                description="The configured HQ field, downsampled equally along both axes.",
                main_resolution=self._hq_profile.default_resolution,
                lores_resolution=self._hq_profile.lores_resolution,
                sensor_mode_resolution=(4056, 3040),
                bit_depth=12,
                use_lores_as_preview=False,
                buffer_count=self._hq_profile.default_buffer_count,
                scaler_crop=self._hq_profile.roi,
            ),
            "full_resolution": PiCamera2StreamingMode(
                description="The same HQ field at full output resolution, with a smaller preview.",
                main_resolution=self._hq_profile.full_resolution,
                lores_resolution=self._hq_profile.lores_resolution,
                sensor_mode_resolution=(4056, 3040),
                bit_depth=12,
                use_lores_as_preview=True,
                buffer_count=self._hq_profile.full_buffer_count,
                scaler_crop=self._hq_profile.roi,
            ),
        }

    @lt.property
    def capture_modes(self) -> Mapping[str, PiCamera2CaptureMode]:
        """Modes the camera can use for capturing."""
        return {
            "standard": PiCamera2CaptureMode(
                description="The same HQ field at the configured standard save resolution.",
                save_resolution=self._hq_profile.standard_resolution,
                streaming_mode="full_resolution",
                stream_name="main",
            ),
            "full": PiCamera2CaptureMode(
                description="The same HQ field at the configured full output resolution.",
                streaming_mode="full_resolution",
                stream_name="main",
            ),
            "quick": PiCamera2CaptureMode(
                description="Capture without altering the stream settings.",
                streaming_mode=None,
                stream_name="main",
            ),
        }
