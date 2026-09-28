"""Fresh, owned camera requests and decoding for the fixed HQ measurement mode.

CSI-2 RAW12 layout: Linux V4L2 pixfmt-srggb12p documentation.
No camera is opened here, no ISP correction is applied, and black is not subtracted.
"""

from __future__ import annotations

import math
import re
import time
from threading import Condition
from typing import Any, Callable

import numpy as np


class PendingRequest:
    """Transfer a completed request once; release late results after timeout/cancel.

    Picamera2's numeric wait timeout does NOT unschedule its job. A callback owns
    every result, including a frame that arrives after the caller has left.
    """

    def __init__(self) -> None:
        """Create an empty hand-off, without dispatching any camera job."""
        self.condition = Condition()
        self.finished = False
        self.abandoned = False
        self.request: Any = None
        self.error: BaseException | None = None

    def complete(self, job: Any) -> None:
        """Run in Picamera2's event thread; never raise into the encoder loop."""
        with self.condition:
            try:
                request = job.get_result()
                if self.abandoned:
                    request.release()
                else:
                    self.request = request
            except BaseException as exc:
                self.error = exc
            finally:
                self.finished = True
                self.condition.notify_all()

    def take(self, timeout_s: float, check_cancelled: Callable[[], None]) -> Any:
        """Wait cancellably, abandoning and releasing on all unsuccessful exits."""
        deadline = time.monotonic() + timeout_s
        with self.condition:
            try:
                while True:
                    check_cancelled()
                    if self.finished:
                        if self.error is not None:
                            raise self.error
                        result, self.request = self.request, None
                        return result
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Fresh camera frame timed out")
                    self.condition.wait(min(remaining, 0.05))
            except BaseException:
                self.abandoned = True
                if self.request is not None:
                    self.request.release()
                    self.request = None
                raise


def exposure_metadata(metadata: dict, boundary_ns: int) -> dict:
    """Reject old/transition exposures using Picamera2's first-pixel convention."""
    timestamp = metadata.get("SensorTimestamp")
    exposure = metadata.get("ExposureTime")
    gain: Any = metadata.get("AnalogueGain")
    duration = metadata.get("FrameDuration")
    if (
        type(timestamp) is not int
        or type(exposure) is not int
        or exposure <= 0
        or type(gain) not in (int, float)
        or not math.isfinite(gain)
        or gain <= 0
        or type(duration) is not int
        or duration <= 0
    ):
        raise ValueError("Missing or invalid camera exposure metadata")
    start = timestamp - exposure * 1000
    if start < boundary_ns:
        raise ValueError("Stale camera exposure crosses the light boundary")
    return {
        "boundary_ns": boundary_ns,
        "exposure_start_ns": start,
        "sensor_timestamp_ns": timestamp,
        "exposure_time_us": exposure,
        "analogue_gain": float(gain),
        "frame_duration_us": duration,
        "sensor_black_levels_16bit": list(metadata.get("SensorBlackLevels", [])),
    }


def hq_geometry(configuration: dict, crop: list | tuple) -> dict:
    """Describe only the tested full-sensor HQ path, refusing guessed geometry."""
    raw = configuration["raw"]
    match = re.fullmatch(r"S(BGGR|GBRG|GRBG|RGGB)12_CSI2P", raw["format"])
    if match is None:
        raise ValueError("Measurement requires packed HQ RAW12 (CSI2P)")
    if (
        list(raw["size"]) != [4056, 3040]
        or list(configuration["sensor"]["output_size"]) != [4056, 3040]
        or configuration["sensor"]["bit_depth"] != 12
        or str(configuration.get("transform", "")) != "<libcamera.Transform 'identity'>"
    ):
        raise ValueError(
            "Unsupported sensor geometry; use the fixed full-sensor HQ mode"
        )
    if (
        len(crop) != 4
        or any(type(v) is not int or v < 0 or v % 2 for v in crop)
        or crop[2] < 32
        or crop[3] < 32
        or crop[0] + crop[2] > 4056
        or crop[1] + crop[3] > 3040
    ):
        raise ValueError("RAW measurement needs an even, in-bounds sensor crop")
    order = match.group(1)
    offsets = [
        [i % 2, i // 2] for c in "BGR" for i, value in enumerate(order) if value == c
    ]
    width, height = configuration["main"]["size"]
    # Coordinates are pixel centres: sensor index 0 is the first pixel centre.
    return {
        "sensor": "imx477",
        "bit_depth": 12,
        "white_level": 4095,
        "raw_format": raw["format"],
        "raw_size": list(raw["size"]),
        "raw_stride": raw["stride"],
        "bayer_order": order,
        "channel_order": ["B", "G0", "G1", "R"],
        "channel_offsets_xy": offsets,
        "sensor_crop": list(crop),
        "plane_size": [crop[2] // 2, crop[3] // 2],
        "white_size": [width, height],
        "white_to_sensor": [
            [crop[2] / width, 0, crop[0] + crop[2] / width / 2 - 0.5],
            [0, crop[3] / height, crop[1] + crop[3] / height / 2 - 0.5],
        ],
        "common_plane_to_sensor": [[2, 0, crop[0] + 0.5], [0, 2, crop[1] + 0.5]],
    }


def decode_hq_raw(data: np.ndarray, geometry: dict) -> np.ndarray:
    """Unpack RAW12, excluding row padding, to cropped native B/G0/G1/R planes."""
    width, height = geometry["raw_size"]
    stride = geometry["raw_stride"]
    if (
        data.dtype != np.uint8
        or data.ndim != 2
        or data.shape != (height, stride)
        or stride < width * 3 // 2
    ):
        raise ValueError("Packed RAW array does not match configured byte stride")
    x, y, w, h = geometry["sensor_crop"]
    packed = data[y : y + h, x * 3 // 2 : (x + w) * 3 // 2].reshape(h, w // 2, 3)
    pixels = np.empty((h, w), dtype=np.uint16)
    pixels[:, 0::2] = (packed[:, :, 0].astype(np.uint16) << 4) | (packed[:, :, 2] & 15)
    pixels[:, 1::2] = (packed[:, :, 1].astype(np.uint16) << 4) | (packed[:, :, 2] >> 4)
    return np.stack([pixels[dy::2, dx::2] for dx, dy in geometry["channel_offsets_xy"]])
