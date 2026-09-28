"""Read-only Moonraker subscription using the upstream moonraker-api client.

Commands deliberately do not enter this connection: reconnect may re-subscribe,
but can never replay a move, enable motors, or establish an operator reference.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import threading
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp
from moonraker_api import MoonrakerClient, MoonrakerListener
from moonraker_api.const import WEBSOCKET_STATE_STOPPED, WEBSOCKET_STATE_STOPPING

LOGGER = logging.getLogger(__name__)
LIGHT_PINS = {
    "white": "led_white_d0",
    "red": "led_red_wifi",
    "green": "led_green_wifi",
}
STATUS_OBJECTS = dict.fromkeys(
    ("toolhead", "gcode_move", "stepper_enable", "webhooks", "motion_report")
)


class StatusCache:
    """Merge object-property deltas and track connection loss independently of zero."""

    def __init__(self) -> None:
        """Create an unavailable cache without any controller assumptions."""
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._generation = 0
        self._pid: int | None = None
        self._result: dict[str, Any] | None = None
        self._received = 0.0
        self._notification_time = -math.inf
        self._field_times: dict[tuple[str, str], float] = {}
        self._pending: list[dict[str, Any]] = []

    def begin(self) -> int:
        """Begin a new read-only connection; all previous snapshots are unusable."""
        with self._lock:
            self._generation += 1
            self._pid = None
            self._result = None
            self._pending = []
            self._field_times = {}
            self._notification_time = -math.inf
            self._changed.notify_all()
            return self._generation

    def lost(self, generation: int) -> None:
        """Latch connection loss, ignoring delayed callbacks from older clients."""
        with self._lock:
            if generation == self._generation:
                self.begin()

    def health(self) -> tuple[int, bool]:
        """Return an epoch even after reconnect so callers can discard old zero."""
        with self._lock:
            return self._generation, self._pid is not None and self._result is not None

    @staticmethod
    def _validate(result: dict[str, Any]) -> None:
        eventtime = result["eventtime"]
        if (
            isinstance(eventtime, bool)
            or not isinstance(eventtime, (float, int))
            or not math.isfinite(eventtime)
            or not isinstance(result["status"], dict)
            or any(not isinstance(value, dict) for value in result["status"].values())
        ):
            raise ValueError("Malformed Moonraker status")

    def initialise(self, generation: int, pid: int, result: dict[str, Any]) -> None:
        """Apply the full subscription reply before any already-arrived deltas."""
        self._validate(result)
        if not STATUS_OBJECTS.keys() <= result["status"].keys():
            raise ValueError("Incomplete controller subscription")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Missing Klipper process identity")
        with self._lock:
            if generation != self._generation:
                raise ConnectionError("Subscription changed during initialisation")
            self._pid = pid
            self._result = {"eventtime": result["eventtime"], "status": {}}
            self._merge(result)
            self._received = time.monotonic()
            for update in self._pending:
                self._merge(update)
            self._pending = []
            self._changed.notify_all()

    def _merge(self, result: dict[str, Any]) -> None:
        if self._result is None:
            self._pending.append(copy.deepcopy(result))
            return
        # A stage readback can overtake a pending LIGHT delta (and vice versa).
        # Order per property, not by the newest unrelated object's timestamp.
        eventtime = result["eventtime"]
        for name, fields in result["status"].items():
            for field, value in fields.items():
                key = (name, field)
                if eventtime >= self._field_times.get(key, -math.inf):
                    self._result["status"].setdefault(name, {})[field] = copy.deepcopy(
                        value
                    )
                    self._field_times[key] = eventtime
        self._result["eventtime"] = max(self._result["eventtime"], eventtime)

    def update(self, generation: int, result: dict[str, Any]) -> None:
        """Merge ordered notifications; a regressing stream loses its reference."""
        self._validate(result)
        with self._lock:
            if generation != self._generation:
                return
            if result["eventtime"] < self._notification_time:
                self.lost(generation)
                return
            self._notification_time = result["eventtime"]
            self._merge(result)
            self._received = time.monotonic()
            self._changed.notify_all()

    def record_query(self, generation: int, pid: int, result: dict[str, Any]) -> None:
        """Seed UI readback with the exact post-M400 snapshot, never an old delta."""
        self._validate(result)
        with self._lock:
            if generation != self._generation or pid != self._pid:
                self.lost(generation)
                raise ConnectionError("Controller changed during status query")
            self._merge(result)
            self._changed.notify_all()

    def snapshot(self, max_age: float) -> tuple[int, int, dict[str, Any]] | None:
        """Return a detached, age-bounded snapshot; absent data is not zero."""
        with self._lock:
            if (
                self._pid is None
                or self._result is None
                or time.monotonic() - self._received > max_age
            ):
                return None
            return self._generation, self._pid, copy.deepcopy(self._result)

    def wait_for_snapshot(
        self,
        generation: int,
        after_eventtime: float,
        max_age: float,
        timeout: float,
    ) -> tuple[int, int, dict[str, Any]] | None:
        """Wait for a newer subscribed snapshot from the same controller epoch.

        A query merged through :meth:`record_query` deliberately does not advance
        ``_notification_time``.  Consequently this method can only replace a
        readback while the read-only subscription itself is live and advancing.
        """
        deadline = time.monotonic() + max(timeout, 0.0)
        with self._changed:
            while True:
                if generation != self._generation:
                    return None
                if (
                    self._pid is not None
                    and self._result is not None
                    and self._notification_time > after_eventtime
                    and self._result["eventtime"] > after_eventtime
                    and time.monotonic() - self._received <= max_age
                ):
                    return (
                        self._generation,
                        self._pid,
                        copy.deepcopy(self._result),
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._changed.wait(remaining)


class StatusListener(MoonrakerListener):
    """Translate upstream callbacks without acquiring the stage's movement lock."""

    def __init__(self, cache: StatusCache, generation: int) -> None:
        """Bind this client to its own epoch, including delayed notifications."""
        self.cache = cache
        self.generation = generation

    async def state_changed(self, state: str) -> None:
        """Latch disconnects before a later read-only reconnect can hide them."""
        if state in (WEBSOCKET_STATE_STOPPED, WEBSOCKET_STATE_STOPPING):
            self.cache.lost(self.generation)

    async def on_exception(self, _exception: type | BaseException) -> None:
        """Discard controller assumptions after any transport exception."""
        self.cache.lost(self.generation)

    async def on_notification(self, method: str, data: Any) -> None:
        """Apply deltas or invalidate on Klippy lifecycle changes."""
        if method in (
            "notify_klippy_disconnected",
            "notify_klippy_shutdown",
            "notify_klippy_ready",
        ):
            self.cache.lost(self.generation)
        elif method == "notify_status_update":
            try:
                self.cache.update(
                    self.generation, {"status": data[0], "eventtime": data[1]}
                )
            except (KeyError, IndexError, TypeError, ValueError):
                self.cache.lost(self.generation)


class MoonrakerStatusStream:
    """Own one asyncio thread/session for read-only state subscriptions."""

    def __init__(
        self, url: str, api_key: str | None, timeout: float, reconnect_interval: float
    ) -> None:
        """Store connection settings; construction does not contact hardware."""
        self.cache = StatusCache()
        self._url = urlparse(url)
        self._api_key = api_key
        self._timeout = timeout
        self._reconnect_interval = reconnect_interval
        self._loop: asyncio.AbstractEventLoop | None = None
        self._halt: asyncio.Event | None = None
        self._started = threading.Event()
        self._thread = threading.Thread(
            target=self._thread_main, name="moonraker-status", daemon=True
        )

    def start(self) -> None:
        """Start monitoring without making OFM startup depend on controller readiness."""
        self._thread.start()
        if not self._started.wait(self._timeout):
            raise TimeoutError("Moonraker status thread did not start")

    def close(self) -> None:
        """Stop monitoring and release the SDK client, session, and thread."""
        if self._loop is not None and self._halt is not None:
            self._loop.call_soon_threadsafe(self._halt.set)
        self._thread.join(self._timeout * 2 + 1)
        if self._thread.is_alive():
            raise TimeoutError("Moonraker status thread did not stop")

    def _thread_main(self) -> None:
        with asyncio.Runner() as runner:
            self._loop = runner.get_loop()
            self._halt = asyncio.Event()
            self._started.set()
            runner.run(self._run())

    async def _wait(self, seconds: float) -> None:
        if self._halt is None:
            return
        try:
            await asyncio.wait_for(self._halt.wait(), seconds)
        except TimeoutError:
            pass

    async def _run(self) -> None:
        if self._halt is None:
            return
        while not self._halt.is_set():
            generation = self.cache.begin()
            async with aiohttp.ClientSession() as session:
                client = MoonrakerClient(
                    StatusListener(self.cache, generation),
                    host=self._url.hostname,
                    port=self._url.port or (443 if self._url.scheme == "https" else 80),
                    route_prefix=self._url.path or None,
                    api_key=self._api_key,
                    ssl=self._url.scheme == "https",
                    timeout=math.ceil(self._timeout),
                    session=session,
                )
                try:
                    await client.connect()
                    info = await client.call_method("printer.info")
                    available = await client.call_method("printer.objects.list")
                    objects = dict(STATUS_OBJECTS)
                    for pin in LIGHT_PINS.values():
                        name = "output_pin " + pin
                        if name in available["objects"]:
                            objects[name] = None
                    result = await client.call_method(
                        "printer.objects.subscribe", objects=objects
                    )
                    self.cache.initialise(generation, info["process_id"], result)
                    while (
                        not self._halt.is_set()
                        and client.is_connected
                        and self.cache.health()[0] == generation
                    ):
                        await self._wait(0.1)
                except (Exception, asyncio.CancelledError):
                    LOGGER.warning(
                        "Moonraker status unavailable; read-only reconnect pending"
                    )
                finally:
                    self.cache.lost(generation)
                    try:
                        await asyncio.wait_for(client.disconnect(), self._timeout)
                    except (Exception, asyncio.CancelledError):
                        LOGGER.debug(
                            "Status client closed after connection failure",
                            exc_info=True,
                        )
            await self._wait(self._reconnect_interval)
