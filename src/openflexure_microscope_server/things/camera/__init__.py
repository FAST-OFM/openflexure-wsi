"""OpenFlexure Microscope Camera.

This module defines the interface for cameras. Any compatible lt.Thing
should enable the server to work.

See repository root for licensing information.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import time
from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Mapping, Optional, Self

import numpy as np
import piexif
from fastapi import HTTPException, Response
from PIL import Image
from pydantic import BaseModel

import labthings_fastapi as lt
from labthings_fastapi.types.numpy import NDArray

from openflexure_microscope_server.gallery_db import GalleryEntry, OFMGalleryDBEngine
from openflexure_microscope_server.things import OFMThing, RelativeDataPath
from openflexure_microscope_server.things.scanning.background_detect import (
    BackgroundDetectAlgorithm,
)
from openflexure_microscope_server.ui import ActionButton, PropertyControl
from openflexure_microscope_server.utilities import coerce_thing_selector


class ImageFormatInfo(BaseModel):
    """Basic data for image formats."""

    media_type: str
    extension: str
    supported_extensions: tuple[str, ...]
    """All supported extension (lowercase)."""

    def path_matches(self, path: str) -> bool:
        """Return True if path matches one of the supported extensions."""
        return path.lower().endswith(self.supported_extensions)


BASE_IMAGE_FORMATS: dict[str, ImageFormatInfo] = {
    "jpeg": ImageFormatInfo(
        media_type="image/jpeg",
        extension=".jpeg",
        supported_extensions=(".jpeg", ".jpg"),
    ),
    "png": ImageFormatInfo(
        media_type="image/png",
        extension=".png",
        supported_extensions=(".png",),
    ),
}


def _file_is_capture(filename: str) -> bool:
    """Return whether this filename is a capture."""
    return BASE_IMAGE_FORMATS["jpeg"].path_matches(filename) or BASE_IMAGE_FORMATS[
        "png"
    ].path_matches(filename)


class CaptureError(RuntimeError):
    """An error trying to capture from a CameraThing."""


class CaptureParams(BaseModel):
    """A class for capturing at least a single image."""

    images_dir: RelativeDataPath
    capture_mode: str


class NoImageInMemoryError(RuntimeError):
    """An error called if no image is in memory when accessed."""


class CameraMemoryBuffer:
    """A class that holds images in memory. The images are by default PIL images.

    However subclasses of BaseCamera can use this class to store other object types.
    """

    _storage: dict[int, tuple[Any, Mapping[str, Any], str]]

    def __init__(self) -> None:
        """Create the buffer instance."""
        # This dictionary is the main store for data. Dictionaries are ordered since
        # Python 3.6, so the order in the dictionary is the capture order
        self._storage = {}
        # A simple id system where each capture id is just the number of captures since
        # the server starts
        self._latest_id: int = 0

    def add_image(
        self,
        image: Any,
        metadata: Mapping[str, Any],
        mode: str,
        buffer_max: int = 1,
    ) -> int:
        """Add an image to the Memory buffer.

        This will add an image to the memory buffer. By default the buffer will
        be cleared. To allow saving multiple images the buffer_max must be set
        every time an image is added.

        :param image: The image to add. A PIL image is recommended, but cameras
            can choose to use other formats
        :param metadata: Optional, a dictionary of the image metadata.
        :param buffer_max: The maximum number of images that should be in the buffer
            once this images is added. Default is 1.

        :returns: The id in the buffer for this image
        """
        self._latest_id += 1
        self._create_space(buffer_max)
        self._storage[self._latest_id] = (image, metadata, mode)
        return self._latest_id

    def get_image(
        self, buffer_id: Optional[int] = None, remove: bool = True
    ) -> tuple[Any, Mapping[str, Any], str]:
        """Return the image with the given id.

        If no id is given the most recent image is returned. However, the
        buffer is also cleared, otherwise it would be possible to accidentally
        retrieve images out of order.

        :param buffer_id: The buffer id of the image to retrieve
        :param remove: True (default) to remove this image from the buffer, False
            to leave the image in the buffer.
        """
        # No id given
        if buffer_id is None:
            # Get the latest image and metadata tuple from storage
            try:
                image_tuple = list(self._storage.values())[-1]
            except IndexError as e:
                raise NoImageInMemoryError("No image in memory to retrieve.") from e
            # Clear the storage so images don't get retrieved out of order
            self._storage.clear()
            return image_tuple

        try:
            if remove:
                return self._storage.pop(buffer_id)
            return self._storage[buffer_id]
        except KeyError as e:
            raise NoImageInMemoryError(
                "No image with matching id in memory to retrieve."
            ) from e

    def clear(self) -> None:
        """Clear all images from memory."""
        self._storage.clear()

    def _create_space(self, buffer_max: int) -> None:
        """Create space to add an image.

        :param buffer_max: The maximum number of images that should be in the buffer
            once another images is added.
        """
        # If only one image to be stored just clear the storage and return
        if buffer_max <= 1:
            self._storage.clear()
            return

        # Number to remove to get the storage down to 1 less than the buffer length
        to_remove = len(self._storage) - (buffer_max - 1)
        # If if there is space. Nothing to do, just return
        if to_remove < 1:
            return

        keys_to_remove = list(self._storage.keys())[:to_remove]
        for key in keys_to_remove:
            del self._storage[key]


class StreamingMode(BaseModel):
    """Description of streaming modes for the camera.

    Cameras can sub class this to store camera specific information about the mode.
    """

    description: str


class CaptureMode(BaseModel):
    """Description of still capture modes for the camera.

    Cameras can sub class this to store camera specific information about the mode.
    """

    description: str
    save_resolution: Optional[tuple[int, int]] = None
    """The resolution to save the image. Use None to save as captured."""


class CaptureGalleryInfo(BaseModel):
    """Summary information for the UI about an image.

    Currently images have no extra information to transmit.
    """


class CameraGalleryDBEngine(OFMGalleryDBEngine):
    """The database engine to back gallery interactions for the captures."""

    card_type = "Capture"
    gallery_info_model = CaptureGalleryInfo

    def _list_all_items_on_file_system(self) -> list[str]:
        """List all the captures in this Thing's data directory.

        :return: A list of paths.
        """
        files = os.listdir(self._data_dir)
        captures = []
        for filename in files:
            full_path = os.path.join(self._data_dir, filename)
            if os.path.isfile(full_path) and _file_is_capture(filename):
                # Normalise to posix to keep database consistent across OSes
                normalised_path = self.fullpath_to_normpath(full_path)
                captures.append(normalised_path)
        return captures

    def _hash(self, path: str) -> str:
        """Create a unique hash for an item.

        For images in the gallery, this is just checking the image has not
        been updated or replaced. For speed we use a blake2b 8-byte hash of the file
        size and the modified time.

        :param path: The normalised relative posixpath for the item to hash, in this
            case, the capture file. This is the primary key of the GalleryEntry table.

        :return: A unique identifier to check if the item has changed. For large items
            that are a folder of data it is not recommended to hash the entire file as
            this is slow. Instead, hash just enough information about the item to
            confirm that it's unchanged.
        """
        stats = os.stat(self.normpath_to_fullpath(path))

        # create a hash object
        h = hashlib.blake2b(digest_size=8)

        # modify the hash object with the file size and modified time
        h.update(stats.st_size.to_bytes(8, "little"))
        h.update(int(stats.st_mtime).to_bytes(8, "little"))
        return h.hexdigest()

    def _generate_entry(
        self, path: str, gallery_added_data: Optional[dict] = None
    ) -> GalleryEntry:
        """Create a GalleryEntry for an item based on the data on disk.

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        :param gallery_added_data: Any data added by the gallery. This can be used to
            persist gallery added data even if item is modified on disk.

        :return: A GalleryEntry for the item.
        """
        st = os.stat(self.normpath_to_fullpath(path))
        return GalleryEntry(
            path=path,
            hash=self._hash(path),
            created=st.st_ctime,
            modified=st.st_mtime,
            gallery_info={},
            thumbnail_source=path,
            gallery_added_data={} if gallery_added_data is None else gallery_added_data,
        )

    def _mounted_path(self, path: str) -> str:
        """Return the mount point for the normalised path.

        This may be the mount point for an entry or another file in the data directory.

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        """
        return self._thing.name + "/" + path

    def _delete_endpoint(self, path: str) -> str:
        """Return the endpoint for deleting an entry (relative to the base URL).

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        """
        return self._thing.name + "/capture/" + path


class BaseCamera(OFMThing, ABC):
    """The base class for all cameras. All cameras must directly inherit from this class.

    The connection to the camera hardware should be added to the ``__enter__`` method not
    ``__init__`` method of the subclass.
    """

    _class_settings = {"validate_properties_on_set": True}

    _all_background_detectors: Mapping[str, BackgroundDetectAlgorithm] = lt.thing_slot()

    mjpeg_stream = lt.outputs.MJPEGStreamDescriptor()
    lores_mjpeg_stream = lt.outputs.MJPEGStreamDescriptor()
    _memory_buffer = CameraMemoryBuffer()
    supports_focus_fom: bool = False

    supported_image_formats = deepcopy(BASE_IMAGE_FORMATS)

    def __init__(self, thing_server_interface: lt.ThingServerInterface) -> None:
        """Initialise the base camera, this creates the background detectors.

        This must be run by all child camera classes.

        To add a new background detector to the server it must be added to the
        dictionary in this function. Configuration will be added at a later date.
        """
        super().__init__(thing_server_interface)
        # Default is never updated but is used if the value set from settings is
        # incorrect. In the future a better way to set defaults for thing slot mappings
        # would be ideal.
        self._default_background_detector = "bg_channel_deviations_luv"
        self._background_detector_name: Optional[str] = None
        self._framerate_monitor_running = False

        required_modes = ("default", "full_resolution")
        if not all(mode in self.streaming_modes for mode in required_modes):
            raise KeyError(
                f"Camera {type(self).__name__} doesn't define both a 'default' and a "
                "'full_resolution' streaming mode."
            )

    def __enter__(self) -> Self:
        """Open hardware connection when the Thing context manager is opened."""
        super().__enter__()

        self._background_detector_name = coerce_thing_selector(
            thing_mapping=self._all_background_detectors,
            selected=self._background_detector_name,
            default=self._default_background_detector,
        )
        self._gallery_db_engine = CameraGalleryDBEngine(self.data_dir, self)
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Close hardware connection when the Thing context manager is closed."""
        self.gallery_db_engine.dispose()

    @lt.endpoint(
        "get",
        "snapshot",
        responses={
            200: {
                "description": "A snapshot of the microscope stream",
                "content": {"image/jpeg": {}},
            },
        },
    )
    async def snapshot(self) -> Response:
        """Return a snapshot from the microscope."""
        jpeg_data = await self.lores_mjpeg_stream.grab_frame()
        return Response(content=jpeg_data, media_type="image/jpeg")

    # Register with gallery.
    _show_data_in_gallery = True

    _gallery_db_engine: Optional[CameraGalleryDBEngine] = None

    @property
    def gallery_db_engine(self) -> CameraGalleryDBEngine:
        """The database engine."""
        if self._gallery_db_engine is None:
            raise RuntimeError(
                "Gallery engine has not been created yet. Has the LabThings server "
                "started?."
            )
        return self._gallery_db_engine

    def delete_all_gallery_items(self) -> None:
        """Delete all the captures on the microscope.

        Use with extreme caution.
        """
        for path in self.gallery_db_engine.get_all_paths():
            lt.raise_if_cancelled()
            self.logger.info(f"Deleting: {path}")
            fullpath = self.gallery_db_engine.normpath_to_fullpath(path)
            os.remove(fullpath)
            # Remove the database entry too
            self.gallery_db_engine.delete(path)

    def get_gallery_bulk_actions(self) -> list[ActionButton]:
        """Return the bulk gallery actions for cameras.

        By default there are no bulk actions.
        """
        return []

    @lt.endpoint(
        "delete",
        "capture/{name}",
        responses={
            200: {"description": "Successfully deleted capture"},
            400: {"description": "An error occurred while trying to delete capture"},
        },
    )
    def delete_capture(self, name: str) -> None:
        """Delete the specified capture.

        This endpoint allows captures to be deleted from disk.

        :param name: The name of the capture to delete
        """
        if not _file_is_capture(name):
            self.logger.warning(f"{name} is not an image file.")
            raise HTTPException(400, f"{name} is not an image file.")
        full_path = os.path.normpath(os.path.join(self.data_dir, name))

        try:
            os.remove(full_path)
            self.gallery_db_engine.delete(name)
        except IOError as e:
            self.logger.warning(f"Failed to delete {name}.")
            raise HTTPException(
                400, "Couldn't delete capture, check log for details"
            ) from e

    @property
    def focus_fom(self) -> int:
        """Return the focus figure of merit.

        This returns a NotImplementedError if not supported by the camera.
        To use, requires self.supports_focus_fom to be set to True.
        """
        raise NotImplementedError(
            "This camera does not support Figure of Merit focusing."
        )

    @lt.property
    def calibration_required(self) -> bool:
        """Whether the camera needs calibrating.

        This always returns False in BaseCamera. It should be reimplemented by child
        classes if calibration is required.
        """
        return False

    @lt.property
    def can_calibrate(self) -> bool:
        """Whether the Thing permits calibration."""
        return True

    @lt.property
    def streaming_modes(self) -> Mapping[str, StreamingMode]:
        """Modes the camera can stream in."""
        return {
            "default": StreamingMode(
                description=(
                    "The standard mode that balances capture resolution and stream "
                    "size."
                )
            ),
            "full_resolution": StreamingMode(
                description=(
                    "Streaming the camera in full resolution. For this camera, this is "
                    "identical to the default mode."
                )
            ),
        }

    streaming_mode: str = lt.property(default="default", readonly=True)

    @lt.action
    def change_streaming_mode(self, mode: str = "default") -> None:
        """Change the mode the camera is streaming in.

        :param mode: Must be a key from ``streaming_modes``

        When creating a subclass. Subclass the method ``_start_streaming`` rather than
        this method.
        """
        if not self.stream_active:
            raise RuntimeError(
                "Cannot change streaming mode before streaming is started."
            )
        if mode not in self.streaming_modes:
            self.logger.warning(
                f"Streaming mode {mode}, is not known. Expecting a mode from "
                f"{self.streaming_modes.keys()}. Streaming in default mode."
            )
            mode = "default"
        if mode != self.streaming_mode:
            self._start_streaming(mode=mode)

    @abstractmethod
    def _start_streaming(self, mode: str = "default") -> None:
        """Start (or stop and restart) the camera."""

    def kill_mjpeg_streams(self) -> None:
        """Kill the streams now as the server is shutting down.

        This is called when uvicorn gets the a shutdown signal. As this is called from
        the event loop it cannot interact with the our ThingProperties or run
        ``self.mjpeg_stream.stop()`` as the portal cannot be called from this loop.

        Instead we just set the ``_streaming`` value to False. This stops the async frame
        generator when the next frame notifies.
        """
        if self.stream_active:
            self.mjpeg_stream._streaming = False
            self.lores_mjpeg_stream._streaming = False

    async def _monitor_framerate(
        self,
        duration: float,
        sample_interval: float = 0.1,
    ) -> tuple[float, int, list[dict[str, float | int]]]:
        """Asynchronously monitor the timing on incoming frames."""
        start_time = time.time()
        last_sample_time = start_time
        last_sample_frames = 0

        frames = 0
        samples = []

        async for frame in self.mjpeg_stream.frame_async_generator():
            if not self._framerate_monitor_running:
                break

            now = time.time()
            frames += 1

            if now - last_sample_time >= sample_interval:
                interval = now - last_sample_time
                fps = (frames - last_sample_frames) / interval

                samples.append(
                    {
                        "timestamp": now,
                        "frame_count": frames,
                        "frame_size_bytes": len(frame),
                        "instant_fps": fps,
                    }
                )

                last_sample_time = now
                last_sample_frames = frames

            if now - start_time >= duration:
                break

        total_time = time.time() - start_time

        return total_time, frames, samples

    @lt.action
    def record_framerate(
        self,
        duration: float = 5.0,
    ) -> str:
        """Record MJPEG stream framerate statistics."""
        output_dir = os.path.join(
            self.data_dir,
            "characterisation",
            "framerate",
        )
        os.makedirs(output_dir, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        datafile_path = os.path.join(
            output_dir,
            f"framerate_{timestamp}.json",
        )

        self.logger.info(
            "Framerate monitor started -> %s",
            datafile_path,
        )

        self._framerate_monitor_running = True

        # This runs as an async task, which we wait to complete
        try:
            total_time, frames, samples = self._thing_server_interface.call_async_task(
                self._monitor_framerate,
                duration,
            )

        finally:
            self._framerate_monitor_running = False

        avg_fps = frames / total_time if total_time > 0 else 0

        data = {
            "summary": {
                "total_duration": total_time,
                "total_frames": frames,
                "avg_fps": avg_fps,
            },
            "samples": samples,
        }

        self.logger.info(
            ("Framerate monitor results: duration=%.2fs, frames=%d, avg_fps=%.2f"),
            total_time,
            frames,
            avg_fps,
        )

        with open(datafile_path, "w") as f:
            json.dump(data, f, indent=2)

        self.logger.info(
            "Framerate monitor complete -> %s",
            datafile_path,
        )

        return datafile_path

    @abstractmethod
    @lt.property
    def stream_active(self) -> bool:
        """Whether the MJPEG stream is active."""

    @abstractmethod
    @lt.action
    def discard_frames(self) -> None:
        """Discard frames so that the next frame captured is fresh."""

    @lt.action
    def grab_jpeg(
        self,
        stream_name: Literal["main", "lores"] = "main",
    ) -> lt.blob.Blob:
        """Acquire one image from the preview stream and return as blob of JPEG data.

        Note: in rare cases the JPEG stream may be broken. This can cause an OS error
        when loading the image. If loading with PIL, as long as the header data is
        complete, this error will not be raised until the data is accessed. Consider
        using ``grab_jpeg_as_array`` instead.

        This differs from ``capture`` in that it does not pause the MJPEG
        preview stream. Instead, we simply return the next frame from that
        stream (either "main" for the preview stream, or "lores" for the low
        resolution preview). No metadata is returned.
        """
        stream = (
            self.lores_mjpeg_stream if stream_name == "lores" else self.mjpeg_stream
        )
        frame = self._thing_server_interface.call_async_task(stream.grab_frame)
        blob = lt.blob.Blob.from_bytes(frame)
        blob.media_type = BASE_IMAGE_FORMATS["jpeg"].media_type
        return blob

    @lt.action
    def grab_as_array(
        self,
        stream_name: Literal["main", "lores"] = "main",
    ) -> NDArray:
        """Acquire one image from the preview stream and return as an array.

        It works like ``grab_jpeg`` but reliably handles broken streams. Prefer using
        this method over directly grabbing the frame and converting to a numpy array
        via PIL.

        This differs from ``capture_as_array`` in that it does not pause the MJPEG
        preview stream.
        """
        stream = (
            self.lores_mjpeg_stream if stream_name == "lores" else self.mjpeg_stream
        )
        tries = 0
        while tries < 3:
            try:
                frame = self._thing_server_interface.call_async_task(stream.grab_frame)
                return np.asarray(Image.open(io.BytesIO(frame)))
            except OSError:
                tries += 1
        raise OSError("Could not open frames from MJPEG stream.")

    @lt.action
    def grab_jpeg_size(
        self,
        stream_name: Literal["main", "lores"] = "main",
    ) -> int:
        """Acquire one image from the preview stream and return its size."""
        stream = (
            self.lores_mjpeg_stream if stream_name == "lores" else self.mjpeg_stream
        )
        return self._thing_server_interface.call_async_task(stream.next_frame_size)

    @lt.property
    def capture_modes(self) -> Mapping[str, CaptureMode]:
        """Modes the camera can use for capturing.

        Cameras can override this to get more specific modes.
        """
        return {
            "quick": CaptureMode(
                description="Capture without altering the stream settings.",
            ),
            "standard": CaptureMode(description="The standard capture mode."),
        }

    def _validate_capture_mode(self, capture_mode: str) -> str:
        """Check input capture mode exists, always returns a valid mode.

        :param capture_mode: The capture mode to check. If this isn't valid a warning
            will be logged.

        :return: The input capture mode if it is supported, or "standard".
        """
        if capture_mode not in self.capture_modes:
            name = type(self).__name__
            self.logger.warning(
                f"{name} has no capture mode {capture_mode}. Using the 'standard' "
                "capture mode instead."
            )
            capture_mode = "standard"
        return capture_mode

    @abstractmethod
    @lt.action
    def capture_as_array(
        self,
        capture_mode: str = "standard",
        raw: bool = False,
    ) -> NDArray:
        """Acquire one image from the camera and return as an array."""

    downsampled_array_factor: int = lt.property(default=2, ge=1)
    """The downsampling factor when calling capture_downsampled_array."""

    @property
    def mapping_geometry(self) -> dict | None:
        """Optional CSM geometry identity; None preserves unbound camera behaviour.

        Cameras opting in return a JSON-safe identity, including the quick image
        resolution in array order [height, width]. This must not capture or move.
        """
        return None

    @lt.action
    def capture_downsampled_array(self) -> NDArray:
        """Acquire one image from the camera, downsample, and return as an array.

        * The array is downsampled by the thing property `downsampled_array_factor`.
        * The default capture array arguments are used.

        This method provides the interface expected by the camera_stage_mapping.
        """
        img = self.capture_as_array(capture_mode="quick")
        return downsample(self.downsampled_array_factor, img)

    @lt.action
    def capture(
        self,
        capture_mode: str = "standard",
        image_format: str = "jpeg",
        retain_image: bool = True,
    ) -> lt.blob.Blob:
        """Acquire one image from the camera.

        This will use the internal capture image functionally of _capture_image of
        the specific camera being used.

        :param capture_mode: The mode to use, must be one of ``capture_modes``.
        :param image_format: The image format to use, must be one of
            ``supported_image_formats``
        :param retain_image: (Default True) True to save image to the microscope,
            False to only save temporarily for transfer.

        :returns: A LabThings Blob that with access to the captured file.
        """
        format_info = self.supported_image_formats[image_format]
        fname = datetime.now().strftime("%Y-%m-%d-%H%M%S") + format_info.extension

        path = RelativeDataPath(fname)
        tmpdir = None
        if retain_image:
            path.set_saving_thing(self)
        else:
            tmpdir = path.save_to_tempdir()

        self.capture_and_save_to_path(path, capture_mode)

        if retain_image:
            self.gallery_db_engine.add(path)

        if tmpdir is None:
            blob = lt.blob.Blob.from_file(path.abs_data_path)
        else:
            blob = lt.blob.Blob.from_temporary_directory(tmpdir, fname)
        blob.media_type = format_info.media_type
        return blob

    def capture_and_save_to_path(
        self,
        path: RelativeDataPath,
        capture_mode: str = "standard",
    ) -> None:
        """Capture an image and save it to disk.

        This is not an action as it exposes a direct path for saving

        :param path: The path to save the file to, this should be a ``RelativeDataPath``
            object. If the saving Thing is not set for the path, the camera's data
            directory will be used.
        :param capture_mode:  (Optional) The name of the capture mode as defined by the
            camera.
        """
        buffer_id = self.capture_to_memory(capture_mode=capture_mode)
        self.save_from_memory(path=path, buffer_id=buffer_id)

    @lt.action
    def capture_to_memory(
        self, capture_mode: str = "standard", buffer_max: int = 1, max_attempts: int = 5
    ) -> int:
        """Capture an image to memory. This can be saved later with ``save_from_memory``.

        Note that only one image is held in memory so this will overwrite any image
        in memory.

        :param buffer_max: The maximum number of images that should be in the buffer
            once this images is added. Default is 1.
        :param max_attempts: The maximum number of times to attempt the capture.

        :returns: the buffer id of the image captured
        """
        success = False
        for capture_attempts in range(max_attempts):
            try:
                ofm_metadata = self._collect_ofm_metadata()

                image = self._capture_image(capture_mode=capture_mode)
                success = True
                break
            except TimeoutError:
                self.logger.warning(
                    f"Attempt {capture_attempts + 1} to capture image timed out. "
                    "Do you have enough RAM?"
                )

        if not success:
            raise CaptureError(
                f"An error occurred while capturing after {max_attempts} attempts"
            )

        return self._memory_buffer.add_image(
            image, ofm_metadata, capture_mode, buffer_max=buffer_max
        )

    @lt.action
    def save_from_memory(
        self,
        path: RelativeDataPath,
        buffer_id: Optional[int] = None,
    ) -> None:
        """Save an image that has been captured to memory.

        Note this is not exposed as an action as it allows arbitrary paths on disk to
        be written to.

        :param path: The path to save the file to
        :param buffer_id: The buffer id of the image to save, this was returned by
            ``capture_to_memory``
        """
        image, metadata, mode = self._memory_buffer.get_image(buffer_id)

        mode_info = self.capture_modes[mode]
        save_resolution = mode_info.save_resolution

        path.set_saving_thing_if_unset(self)
        resolved_path = path.abs_data_path

        if save_resolution is not None and image.size != save_resolution:
            image = image.resize(save_resolution, Image.Resampling.BOX)
        pending_path: str | None = None
        try:
            save_kwargs: dict[str, Any] = {}
            if BASE_IMAGE_FORMATS["jpeg"].path_matches(resolved_path):
                # Per PIL documentation,
                # (https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html#jpeg)
                # there are two factors when saving a JPEG. Subsampling affects the colour,
                # quality affects the pixels.
                # subsampling = 0 disables subsampling of colour
                # quality = 95 is the maximum recommended - above this, JPEG compression is
                # disabled, file size increases and quality is barely or not affected
                save_kwargs = {"quality": 95, "subsampling": 0}
            if (
                BASE_IMAGE_FORMATS["png"].path_matches(resolved_path)
                and image.mode == "RGBX"
            ):
                image = image.convert("RGB")
            image_format = Image.registered_extensions()[
                os.path.splitext(resolved_path)[1].lower()
            ]
            # Preview stitching scans this directory concurrently. A non-image
            # suffix hides the file until both pixels and EXIF are complete.
            with tempfile.NamedTemporaryFile(
                dir=os.path.dirname(resolved_path),
                prefix=".capture-",
                suffix=".pending",
                delete=False,
            ) as pending:
                pending_path = pending.name
            image.save(pending_path, format=image_format, **save_kwargs)
            if BASE_IMAGE_FORMATS["jpeg"].path_matches(resolved_path):
                self._add_metadata_to_capture(pending_path, dict(metadata))
            lt.raise_if_cancelled()
            os.replace(pending_path, resolved_path)
            pending_path = None
        except lt.exceptions.InvocationCancelledError:
            raise
        except Exception as e:
            raise IOError(f"An error occurred while saving {resolved_path}") from e
        finally:
            if pending_path is not None:
                try:
                    os.unlink(pending_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    self.logger.exception(
                        "Failed to remove unpublished capture %s", pending_path
                    )

    @abstractmethod
    def _capture_image(self, capture_mode: str = "standard") -> Image.Image:
        """Capture a PIL image from the camera.

        This unlike the ``grab_*`` methods this may pause the stream or temporarily
        switch streaming mode to capture the image if required by the mode.
        """

    @lt.action
    def clear_buffers(self) -> None:
        """Clear all images in memory."""
        self._memory_buffer.clear()

    def _collect_ofm_metadata(self) -> dict:
        """Return the metadata for a capture.

        This is information from the thing states, the time, and make/model names.
        """
        metadata = self._thing_server_interface.get_thing_states()
        current_time = datetime.now()
        return {
            "capture_time": current_time.timestamp(),
            "timezone": current_time.astimezone().utcoffset(),
            "make": "OpenFlexure",
            "model": "OpenFlexure Microscope",
            "things_states": metadata,
        }

    def _add_metadata_to_capture(self, path: str, capture_metadata: dict) -> None:
        """Add the EXIF metadata for a JPEG image.

        This adds:
        - UserComment (JSON-encoded metadata from the Things)
        - Capture time (DateTimeOriginal, DateTimeDigitized, 0th DateTime)
        - Camera Make and Model
        """
        # Load existing EXIF
        exif_dict = piexif.load(path)

        user_metadata = capture_metadata["things_states"]
        capture_time = capture_metadata["capture_time"]
        timezone = capture_metadata["timezone"]

        # Convert timezone into bytes with required formatting
        hours = int(timezone.total_seconds() // 3600)
        minutes = int((abs(timezone.total_seconds()) % 3600) // 60)
        sign = "+" if hours >= 0 else "-"
        offset_str = f"{sign}{abs(hours):02d}:{minutes:02d}"

        # Update UserComment with JSON-encoded metadata
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = json.dumps(
            user_metadata
        ).encode("utf-8")

        capture_time_str = datetime.fromtimestamp(capture_time).strftime(
            "%Y:%m:%d %H:%M:%S"
        )

        # Update the three EXIF date fields used as "created" by different platforms
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = capture_time_str
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = capture_time_str
        exif_dict["0th"][piexif.ImageIFD.DateTime] = capture_time_str

        exif_dict["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = offset_str.encode()
        exif_dict["Exif"][piexif.ExifIFD.OffsetTimeDigitized] = offset_str.encode()

        # Update Make and Model
        exif_dict["0th"][piexif.ImageIFD.Make] = capture_metadata["make"]
        exif_dict["0th"][piexif.ImageIFD.Model] = capture_metadata["model"]

        # Write the updated EXIF back to the file
        piexif.insert(piexif.dump(exif_dict), path)

    settling_time: float = lt.setting(default=0.2, ge=0)
    """The settling time when calling the ``settle()`` method."""

    @lt.action
    def settle(self) -> None:
        """Sleep for the settling time, ready to provide a fresh frame.

        This function will sleep for the given time, and clear the buffer after sleeping.
        As such, the next frame captured from the camera after running this function will
        always be captured after settling.

        This method provides the interface expected by the camera_stage_mapping.
        """
        time.sleep(self.settling_time)
        self.discard_frames()

    @lt.property
    def primary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions for both calibration wizard and settings panel."""
        return []

    @lt.property
    def secondary_calibration_actions(self) -> list[ActionButton]:
        """The calibration actions that appear only in settings panel."""
        return []

    @lt.property
    def manual_camera_settings(self) -> list[PropertyControl]:
        """The camera settings to expose as property controls in the settings panel."""
        return []

    # Note that the default detector name is set at init. This is over written if
    # setting is loaded from disk.
    @lt.setting
    def background_detector_name(self) -> Optional[str]:
        """The name of the active background selector."""
        return self._background_detector_name

    @background_detector_name.setter
    def _set_background_detector_name(self, name: Optional[str]) -> None:
        """Validate and set background_detector_name."""
        if name not in self._all_background_detectors:
            self.logger.warning(f"{name} is not a valid background detector name.")
            return
        self._background_detector_name = name

    @property
    def background_detector(self) -> Optional[BackgroundDetectAlgorithm]:
        """The active background detector instance."""
        if self.background_detector_name is None:
            return None
        return self._all_background_detectors[self.background_detector_name]

    @lt.action
    def image_is_sample(self) -> tuple[bool, str]:
        """Label the current image as either background or sample."""
        if self.background_detector is None:
            raise RuntimeError("No background detectors available.")
        current_image = self.grab_as_array(stream_name="lores")
        return self.background_detector.image_is_sample(current_image)

    @lt.action
    def set_background(self) -> None:
        """Grab an image, and use its statistics to set the background.

        This should be run when the microscope is looking at an empty region,
        and will calculate the mean and standard deviation of the pixel values
        in the LUV colourspace. These values will then be used to compare
        future images to the distribution, to determine if each pixel is
        foreground or background.
        """
        if self.background_detector is None:
            raise RuntimeError("No background detectors available.")
        background = self.grab_as_array(stream_name="lores")
        self.background_detector.set_background(background)

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Return camera-specific metadata.

        By default, this just adds the subclass name as the camera type.
        Subclasses can extend by overriding this property and calling super().thing_state.
        """
        return {"camera": self.__class__.__name__}


def downsample(factor: int, image: np.ndarray) -> np.ndarray:
    """Downsample an image by taking the mean of each nxn region.

    This should be very efficient:

    * calculate each pixel as the mean of each ``factor * factor`` square without
        interpolation.
    * If the image is not an integer multiple of the resampling factor, discard
        the left-over pixels to avoid odd edge effects and keep performance quick.
    """
    if factor == 1:
        return image
    new_size = [d // factor for d in image.shape[:2]]
    # First, we ensure we have something that's an integer multiple
    # of `factor`
    cropped = image[: new_size[0] * factor, : new_size[1] * factor, ...]
    reshaped = cropped.reshape(
        (new_size[0], factor, new_size[1], factor) + image.shape[2:]
    )
    return reshaped.mean(axis=(1, 3))
