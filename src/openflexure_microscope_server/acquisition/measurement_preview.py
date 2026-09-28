"""Hold the web preview during R/G acquisition without replaying an old frame."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock


class MeasurementPreview:
    """Match encoded timestamps to the exposure metadata of the same camera request."""

    WHITE_RELEASE_FRAMES = 2

    def __init__(self) -> None:
        """Start in normal manual-preview mode with bounded request history."""
        self._lock = RLock()
        self._starts: OrderedDict[int, int] = OrderedDict()
        self._floor: int | None = None
        self._held = False
        self._waiting = False
        self._active = False
        self._mode = "untracked"
        self._release_remaining = 0
        self._dropped = 0
        self._published = 0
        self._last_white_timestamp_us: int | None = None

    def record(self, metadata: dict) -> None:
        """Record before encoding; malformed timing must not crash the camera loop."""
        timestamp, exposure = (
            metadata.get("SensorTimestamp"),
            metadata.get("ExposureTime"),
        )
        if type(timestamp) is not int or type(exposure) is not int or exposure <= 0:
            return
        with self._lock:
            self._starts[timestamp // 1000] = timestamp - exposure * 1000
            while len(self._starts) > 128:
                self._starts.popitem(last=False)

    def transition(self) -> None:
        """Close publication before issuing any light command."""
        with self._lock:
            self._active, self._held, self._waiting = True, True, False
            self._mode = "transition"
            self._release_remaining = 0

    def confirmed(self, mode: str, boundary_ns: int, *, finished: bool = False) -> None:
        """Require a whole fresh WHITE exposure after acknowledged, settled light."""
        with self._lock:
            self._floor = boundary_ns
            self._mode = mode
            self._held = mode != "white"
            self._waiting = mode == "white"
            self._release_remaining = (
                self.WHITE_RELEASE_FRAMES if mode == "white" else 0
            )
            self._active = not finished

    def allows(self, sensor_timestamp_us: int | None) -> bool:
        """No publication, index advance or timestamp rewrite while a frame is held."""
        with self._lock:
            if self._floor is None and not self._held:
                return True  # Normal manual preview is unchanged.
            start = (
                self._starts.get(sensor_timestamp_us)
                if sensor_timestamp_us is not None
                else None
            )
            if (
                self._held
                or start is None
                or self._floor is None
                or start < self._floor
            ):
                self._dropped += 1
                return False
            if self._release_remaining:
                self._release_remaining -= 1
                self._dropped += 1
                return False
            self._waiting = False
            self._published += 1
            self._last_white_timestamp_us = sensor_timestamp_us
            return True

    @property
    def status(self) -> dict:
        """Small read-only UI status; no hardware reads or stream substitution."""
        with self._lock:
            return {
                "active": self._active,
                "holding": self._held or self._waiting,
                "light": self._mode if self._active else None,
                "white_release_frames_remaining": self._release_remaining,
                "dropped_frames": self._dropped,
                "published_white_frames": self._published,
                "last_white_sensor_timestamp_us": self._last_white_timestamp_us,
            }
