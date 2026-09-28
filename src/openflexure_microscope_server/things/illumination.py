"""A module to handle the illumination in LabThings."""

import math
import os
import time
from threading import RLock
from types import TracebackType
from typing import Literal, Self
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

import labthings_fastapi as lt

from .camera.simulation import SimulatedCamera
from .stage.moonraker import ControllerState, MoonrakerStage
from .stage.moonraker_status import LIGHT_PINS
from .stage.sangaboard import SangaboardThing


class Illumination(lt.Thing):
    """Base class for an illumination controller."""

    _class_settings = {"validate_properties_on_set": True}

    @lt.action
    def set_led(self, led_on: bool = True) -> None:
        """Set the LED to on or off."""
        raise NotImplementedError(
            "Turning the LED on and off should be implemented by child classes."
        )

    @lt.action
    def flash(self, number_of_flashes: int = 10, dt: float = 0.5) -> None:
        """Flash the illumination source.

        :param number_of_flashes: Number of times to flash the LED.
        :param dt: Flash duration in seconds.
        """
        for _ in range(number_of_flashes):
            self.set_led(False)
            time.sleep(dt)
            self.set_led(True)
            time.sleep(dt)


class SangaIllumination(Illumination):
    """Illumination driven by a Sangaboard."""

    _stage: SangaboardThing = lt.thing_slot()

    @lt.action
    def set_led(
        self,
        led_on: bool = True,
        led_channel: Literal["cc"] = "cc",
    ) -> None:
        """Set the LED to on or off."""
        self._stage.set_led(led_on, led_channel)


class SimulatorIllumination(Illumination):
    """Illumination control in the simulator."""

    _cam: SimulatedCamera = lt.thing_slot()

    @lt.action
    def set_led(self, led_on: bool = True) -> None:
        """Set the LED to on or off."""
        self._cam.set_led(led_on)


class IlluminationError(lt.exceptions.InvocationError):
    """A light change was not confirmed by the controller."""


class LightState(BaseModel):
    """Controller-reported gate states, not an optical light measurement."""

    available: bool
    mode: Literal["white", "red", "green", "off", "mixed", "unknown"]
    channels: dict[str, bool] | None = None
    eventtime: float | None = None
    error: str = ""


class MoonrakerIllumination(lt.Thing):
    """Select one existing binary MKS light gate without touching Arduino."""

    _stage: MoonrakerStage = lt.thing_slot()

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        moonraker_url: str = "http://127.0.0.1:7125",
        request_timeout_s: float = 3.0,
        settle_ms: float = 100.0,
    ) -> None:
        """Validate settings without connecting or switching any outputs."""
        super().__init__(thing_server_interface)
        parsed = urlparse(moonraker_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("moonraker_url must be an HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("Use FAST_OFM_MOONRAKER_API_KEY, not URL credentials")
        if not math.isfinite(request_timeout_s) or not 0 < request_timeout_s <= 5:
            raise ValueError("request_timeout_s must be in (0, 5]")
        if not math.isfinite(settle_ms) or not 0 <= settle_ms <= 5000:
            raise ValueError("settle_ms must be in [0, 5000]")
        self._url = moonraker_url.rstrip("/")
        self._timeout = request_timeout_s
        self._settle_ms = settle_ms
        self._client: httpx.Client | None = None
        self._switch_lock = RLock()
        self._fault = ""

    def __enter__(self) -> Self:
        """Open transport only; preserve the current illumination on startup."""
        headers = {}
        api_key = os.getenv("FAST_OFM_MOONRAKER_API_KEY")
        if api_key:
            headers["X-Api-Key"] = api_key
        self._client = httpx.Client(
            base_url=self._url, timeout=self._timeout, headers=headers, trust_env=False
        )
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close transport without silently changing the selected light."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def _read_state(self, subscribed: bool = False) -> LightState:
        if self._client is None:
            raise IlluminationError("Illumination connection is closed")
        result = self._stage.subscribed_status(self._url) if subscribed else None
        if result is None or any(
            "output_pin " + pin not in result["status"] for pin in LIGHT_PINS.values()
        ):
            response = self._client.get(
                "/printer/objects/query?webhooks"
                + "".join("&output_pin%20" + pin for pin in LIGHT_PINS.values())
            )
            response.raise_for_status()
            result = response.json()["result"]
            self._stage.record_light_readback(self._url, result)
        return self._decode_state(result)

    def _decode_state(self, result: dict) -> LightState:
        """Decode one controller response containing every configured light gate."""
        status = result["status"]
        if status["webhooks"]["state"] != "ready":
            raise IlluminationError("Klipper is not ready")
        channels = {}
        for channel, pin in LIGHT_PINS.items():
            value = status["output_pin " + pin]["value"]
            if type(value) not in (int, float) or value not in (0, 1):
                raise IlluminationError(f"Invalid {channel} output readback")
            channels[channel] = bool(value)
        eventtime = result["eventtime"]
        if type(eventtime) not in (int, float) or not math.isfinite(eventtime):
            raise IlluminationError("Invalid controller timestamp")
        active = [channel for channel, on in channels.items() if on]
        mode = "off" if not active else active[0] if len(active) == 1 else "mixed"
        return LightState.model_validate(
            {
                "available": True,
                "mode": mode,
                "channels": channels,
                "eventtime": eventtime,
                "error": self._fault,
            }
        )

    def _read_controller_and_light(self) -> tuple[ControllerState, LightState]:
        """Read stage and light from one fresh post-exposure Moonraker response."""
        if self._client is None:
            raise IlluminationError("Illumination connection is closed")
        if self._stage._url != self._url:
            raise IlluminationError("Light and stage controller URLs must match")
        objects = tuple("output_pin " + pin for pin in LIGHT_PINS.values())
        with self._stage._hardware_lock, self._switch_lock:
            controller, result = self._stage._fetch_state_with_objects(objects)
            return controller, self._decode_state(result)

    @lt.property
    def state(self) -> LightState:
        """Read current gates, including external Fluidd changes; never guess OFF."""
        with self._switch_lock:
            try:
                return self._read_state(subscribed=True)
            except Exception as exc:
                return LightState(available=False, mode="unknown", error=str(exc))

    def _write(self, script: str) -> None:
        if self._client is None:
            raise IlluminationError("Illumination connection is closed")
        response = self._client.post(
            "/printer/gcode/script", json={"script": script + "\nM400"}
        )
        response.raise_for_status()
        if response.json().get("result") != "ok":
            raise IlluminationError("Light command was not acknowledged")

    def _extinguish_after_verified(
        self, verified: LightState, controller: ControllerState | None
    ) -> LightState:
        """Turn a just-verified colour gate off without another pre-read.

        This private path is only for the acquisition owner immediately after it
        has verified both the exposure window and the live light readback. OFF
        remains fail-safe and stage-accounted; no ON command is ever replayed.
        """
        if not verified.available or verified.mode not in ("red", "green"):
            raise IlluminationError("A live RED/GREEN readback is required")
        checked: list[LightState] = []

        def check_after(result: dict) -> None:
            after = self._decode_state(result)
            if after.mode != "off":
                raise IlluminationError("All-off state was not confirmed")
            checked.append(after)

        try:
            objects = tuple("output_pin " + pin for pin in LIGHT_PINS.values())
            with (
                self._stage._light_operation(
                    self._url,
                    controller,
                    extra_objects=objects,
                    after_check=check_after,
                ),
                self._switch_lock,
            ):
                self._write("ALL_OFF")
            self._fault = ""
            return checked[0].model_copy(update={"error": ""})
        except (Exception, lt.exceptions.InvocationCancelledError) as exc:
            cleanup = "state unknown"
            try:
                self._write("ALL_OFF")
                cleanup = f"readback {self._read_state().mode}"
            except Exception as cleanup_error:
                cleanup += f": {cleanup_error}"
            self._fault = f"Light extinguish failed: {exc}; cleanup: {cleanup}"
            if isinstance(exc, lt.exceptions.InvocationCancelledError):
                raise
            raise IlluminationError(self._fault) from exc

    def _transition_after_verified(
        self,
        verified: LightState,
        controller: ControllerState | None,
        mode: Literal["white", "red", "green"],
    ) -> LightState:
        """Switch one verified exposure directly to the next focus colour.

        The script preserves break-before-make ordering inside one acknowledged
        Klipper transaction. This private path is only entered immediately after
        a fresh exposure-window and light readback, so it needs neither another
        preflight query nor an intermediate OFF network round trip. The final
        selected output and M400 boundary are still read back before capture.
        """
        if (
            not verified.available
            or verified.mode not in ("white", "red", "green", "mixed")
            or mode == verified.mode
        ):
            raise IlluminationError("A different live focus light is required")
        checked: list[LightState] = []

        def check_after(result: dict) -> None:
            after = self._decode_state(result)
            if after.mode != mode:
                raise IlluminationError("Selected focus light state was not confirmed")
            checked.append(after)

        try:
            objects = tuple("output_pin " + pin for pin in LIGHT_PINS.values())
            with (
                self._stage._light_operation(
                    self._url,
                    controller,
                    extra_objects=objects,
                    after_check=check_after,
                ),
                self._switch_lock,
            ):
                self._write(f"ALL_OFF\n{mode.upper()}_ON")
                lt.cancellable_sleep(self._settle_ms / 1000)
            self._fault = ""
            return checked[0].model_copy(update={"error": ""})
        except (Exception, lt.exceptions.InvocationCancelledError) as exc:
            cleanup = "state unknown"
            try:
                self._write("ALL_OFF")
                cleanup = f"readback {self._read_state().mode}"
            except Exception as cleanup_error:
                cleanup += f": {cleanup_error}"
            self._fault = f"Light transition failed: {exc}; cleanup: {cleanup}"
            if isinstance(exc, lt.exceptions.InvocationCancelledError):
                raise
            raise IlluminationError(self._fault) from exc

    def _select_red_green_probe(self) -> LightState:
        """Select RED and GREEN together for the bounded RAW feasibility probe.

        This intentionally remains private: the normal UI and autofocus keep the
        proven one-colour-at-a-time contract.  The probe still uses break-before-
        make, controller readback, M400, settling and a single fail-safe cleanup.
        """
        with self._stage._light_operation(self._url), self._switch_lock:
            before = self._read_state()
            expected = {"white": False, "red": True, "green": True}
            if before.mode == "mixed" and before.channels == expected:
                self._fault = ""
                return before.model_copy(update={"error": ""})
            try:
                if before.mode != "off":
                    self._write("ALL_OFF")
                    if self._read_state().mode != "off":
                        raise IlluminationError("All-off state was not confirmed")
                self._write("RED_ON\nGREEN_ON")
                lt.cancellable_sleep(self._settle_ms / 1000)
                after = self._read_state()
                if after.mode != "mixed" or after.channels != expected:
                    raise IlluminationError(
                        "Combined RED/GREEN state was not confirmed"
                    )
                self._fault = ""
                return after.model_copy(update={"error": ""})
            except (Exception, lt.exceptions.InvocationCancelledError) as exc:
                cleanup = "state unknown"
                try:
                    self._write("ALL_OFF")
                    cleanup = f"readback {self._read_state().mode}"
                except Exception as cleanup_error:
                    cleanup += f": {cleanup_error}"
                self._fault = (
                    f"Combined RED/GREEN probe failed: {exc}; cleanup: {cleanup}"
                )
                if isinstance(exc, lt.exceptions.InvocationCancelledError):
                    raise
                raise IlluminationError(self._fault) from exc

    def _select_red_green_probe_after_verified(
        self, verified: LightState, controller: ControllerState | None
    ) -> LightState:
        """Select the mixed probe from a fresh WHITE acquisition boundary.

        The acquisition owner has already read stage and light atomically. Keep
        break-before-make ordering in one acknowledged script and retain one
        combined controller/light readback after M400.
        """
        expected = {"white": False, "red": True, "green": True}
        if (
            not verified.available
            or verified.mode != "white"
            or verified.channels != {"white": True, "red": False, "green": False}
        ):
            raise IlluminationError("A live WHITE readback is required")
        checked: list[LightState] = []

        def check_after(result: dict) -> None:
            after = self._decode_state(result)
            if after.mode != "mixed" or after.channels != expected:
                raise IlluminationError(
                    "Combined RED/GREEN light state was not confirmed"
                )
            checked.append(after)

        try:
            objects = tuple("output_pin " + pin for pin in LIGHT_PINS.values())
            with (
                self._stage._light_operation(
                    self._url,
                    controller,
                    extra_objects=objects,
                    after_check=check_after,
                ),
                self._switch_lock,
            ):
                self._write("ALL_OFF\nRED_ON\nGREEN_ON")
                lt.cancellable_sleep(self._settle_ms / 1000)
            self._fault = ""
            return checked[0].model_copy(update={"error": ""})
        except (Exception, lt.exceptions.InvocationCancelledError) as exc:
            cleanup = "state unknown"
            try:
                self._write("ALL_OFF")
                cleanup = f"readback {self._read_state().mode}"
            except Exception as cleanup_error:
                cleanup += f": {cleanup_error}"
            self._fault = (
                f"Combined RED/GREEN transition failed: {exc}; cleanup: {cleanup}"
            )
            if isinstance(exc, lt.exceptions.InvocationCancelledError):
                raise
            raise IlluminationError(self._fault) from exc

    @lt.action
    def set_mode(self, mode: Literal["white", "red", "green", "off"]) -> LightState:
        """Break-before-make, verify outputs, and retain the selected manual mode.

        The native global lock excludes scan/calibration actions. Failure or
        cancellation makes one bounded ALL_OFF cleanup attempt, never retries ON,
        and retains both the initial error and cleanup result. Settling here does
        not certify that a subsequent camera buffer belongs to this illumination.
        """
        if mode not in ("white", "red", "green", "off"):
            raise ValueError("Unknown illumination mode")
        with self._stage._light_operation(self._url), self._switch_lock:
            before = self._read_state()
            if before.mode == mode:
                self._fault = ""
                return before.model_copy(update={"error": ""})
            try:
                if before.mode != "off":
                    self._write("ALL_OFF")
                    if self._read_state().mode != "off":
                        raise IlluminationError("All-off state was not confirmed")
                if mode != "off":
                    self._write(mode.upper() + "_ON")
                    lt.cancellable_sleep(self._settle_ms / 1000)
                after = self._read_state()
                if after.mode != mode:
                    raise IlluminationError("Selected light state was not confirmed")
                self._fault = ""
                return after.model_copy(update={"error": ""})
            except (Exception, lt.exceptions.InvocationCancelledError) as exc:
                cleanup = "state unknown"
                try:
                    self._write("ALL_OFF")
                    cleanup = f"readback {self._read_state().mode}"
                except Exception as cleanup_error:
                    cleanup += f": {cleanup_error}"
                self._fault = f"Light change failed: {exc}; cleanup: {cleanup}"
                if isinstance(exc, lt.exceptions.InvocationCancelledError):
                    raise
                raise IlluminationError(self._fault) from exc
