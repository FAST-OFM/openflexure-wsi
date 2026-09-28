"""Verify the light adapter without motors, camera ownership or Arduino access."""

import json
from contextlib import contextmanager
from threading import Event, Thread
from unittest.mock import ANY, Mock

import httpx
import pytest

import labthings_fastapi as lt
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.illumination import (
    IlluminationError,
    MoonrakerIllumination,
)
from openflexure_microscope_server.things.stage.moonraker import (
    ControllerState,
    MoonrakerStage,
)


class Controller:
    """HTTP-level fake of the existing three light gates."""

    def __init__(self):
        """Start with WHITE, as during normal microscope use."""
        self.values = {"white": 1, "red": 0, "green": 0}
        self.scripts = []
        self.ready = "ready"
        self.eventtime = 123.0
        self.ignore_off = False
        self.fail_on = False
        self.fail_cleanup = False
        self.bad_ack = False
        self.fail_get = False

    def handle(self, request):
        """Apply once, including a lost acknowledgement after ON took effect."""
        if request.method == "GET":
            assert request.url.path == "/printer/objects/query"
            if self.fail_get:
                raise httpx.ReadTimeout("Controller unreachable")
            status = {"webhooks": {"state": self.ready}}
            for channel, pin in (
                ("white", "led_white_d0"),
                ("red", "led_red_wifi"),
                ("green", "led_green_wifi"),
            ):
                if channel in self.values:
                    status["output_pin " + pin] = {"value": self.values[channel]}
            return httpx.Response(
                200, json={"result": {"status": status, "eventtime": self.eventtime}}
            )
        assert request.url.path == "/printer/gcode/script"
        script = json.loads(request.content)["script"]
        assert script.endswith("\nM400")
        script = script.removesuffix("\nM400")
        self.scripts.append(script)
        combined_probe = script.endswith("RED_ON\nGREEN_ON")
        for command in script.splitlines():
            if command == "ALL_OFF":
                if self.fail_cleanup and len(self.scripts) > 1:
                    raise httpx.ReadTimeout("Cleanup unreachable")
                if not self.ignore_off:
                    self.values = dict.fromkeys(self.values, 0)
            else:
                channel = command.removesuffix("_ON").lower()
                assert combined_probe or not any(self.values.values()), (
                    "Outputs must never overlap"
                )
                self.values[channel] = 1
                if self.fail_on:
                    raise httpx.ReadTimeout("ON acknowledgement lost")
        return httpx.Response(200, json={"result": "bad" if self.bad_ack else "ok"})


@pytest.fixture
def lights(monkeypatch):
    """Use real action/property descriptors with a mocked transport."""
    controller = Controller()
    thing = create_thing_without_server(MoonrakerIllumination, settle_ms=0)
    stage = Mock(spec=MoonrakerStage)
    stage.subscribed_status.return_value = None
    MoonrakerIllumination._stage.connect(thing, {"stage": stage})
    client = httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(controller.handle)
    )
    monkeypatch.setattr(
        "openflexure_microscope_server.things.illumination.httpx.Client",
        lambda **_kwargs: client,
    )

    @contextmanager
    def light_operation(_url, _controller=None, *, extra_objects=(), after_check=None):
        yield
        if after_check is not None:
            suffix = "".join("&" + value.replace(" ", "%20") for value in extra_objects)
            response = client.get("/printer/objects/query?webhooks" + suffix)
            after_check(response.json()["result"])

    stage._light_operation.side_effect = light_operation
    with thing:
        yield thing, controller
    assert client.is_closed


def test_lifecycle_preserves_selected_light(lights):
    """Startup and shutdown must not silently change the light."""
    thing, controller = lights
    assert thing.state.mode == "white"
    thing.__exit__(None, None, None)
    assert controller.scripts == []
    assert not thing.state.available
    assert thing.state.mode == "unknown"


@pytest.mark.parametrize("mode", ["white", "red", "green", "off", "mixed"])
def test_reads_actual_outputs_including_external_changes(lights, mode):
    """No cached/optimistic state can hide Fluidd changes."""
    thing, controller = lights
    controller.values = {
        c: int(c == mode or (mode == "mixed" and c != "green"))
        for c in controller.values
    }
    assert thing.state.mode == mode
    assert thing.state.channels == {c: bool(v) for c, v in controller.values.items()}
    assert controller.scripts == []


def test_switches_and_verifies_each_channel(lights):
    """Single-channel commands are break-before-make; active mode is a no-op."""
    thing, controller = lights
    for mode in ("red", "green", "white", "off"):
        assert thing.set_mode(mode).mode == mode
    assert controller.scripts == [
        "ALL_OFF",
        "RED_ON",
        "ALL_OFF",
        "GREEN_ON",
        "ALL_OFF",
        "WHITE_ON",
        "ALL_OFF",
    ]
    count = len(controller.scripts)
    assert thing.set_mode("off").mode == "off"
    assert len(controller.scripts) == count


def test_switch_from_confirmed_off_does_not_repeat_all_off(lights):
    """A fresh OFF readback is sufficient before one bounded ON command."""
    thing, controller = lights
    assert thing.set_mode("off").mode == "off"
    controller.scripts.clear()
    assert thing.set_mode("red").mode == "red"
    assert controller.scripts == ["RED_ON"]


def test_private_combined_probe_is_verified_and_normal_mode_stays_separate(lights):
    """Only the private probe may overlap R/G; normal selection still breaks first."""
    thing, controller = lights
    state = thing._select_red_green_probe()
    assert state.mode == "mixed"
    assert state.channels == {"white": False, "red": True, "green": True}
    assert controller.scripts == ["ALL_OFF", "RED_ON\nGREEN_ON"]
    controller.scripts.clear()
    assert thing._select_red_green_probe().mode == "mixed"
    assert controller.scripts == []
    assert thing.set_mode("white").mode == "white"
    assert controller.scripts == ["ALL_OFF", "WHITE_ON"]


def test_verified_white_selects_combined_probe_in_one_script(lights):
    """The acquisition path reuses its fresh boundary and keeps final readback."""
    thing, controller = lights
    verified = thing._read_state()
    controller_state = Mock(spec=ControllerState)
    state = thing._select_red_green_probe_after_verified(verified, controller_state)
    assert state.mode == "mixed"
    assert state.channels == {"white": False, "red": True, "green": True}
    assert controller.scripts == ["ALL_OFF\nRED_ON\nGREEN_ON"]
    thing._stage._light_operation.assert_called_once_with(
        thing._url,
        controller_state,
        extra_objects=ANY,
        after_check=ANY,
    )


def test_fast_combined_probe_requires_live_white(lights):
    """A stale or non-WHITE boundary cannot authorise both colour gates."""
    thing, controller = lights
    controller.values = {"white": 0, "red": 1, "green": 0}
    with pytest.raises(IlluminationError, match="live WHITE"):
        thing._select_red_green_probe_after_verified(
            thing._read_state(), Mock(spec=ControllerState)
        )
    assert controller.scripts == []


def test_ambiguous_combined_probe_is_not_retried_and_cleans_up(lights):
    """A lost mixed-ON acknowledgement gets one OFF cleanup, never a replay."""
    thing, controller = lights
    controller.fail_on = True
    with pytest.raises(IlluminationError, match="ON acknowledgement lost"):
        thing._select_red_green_probe()
    assert controller.scripts == ["ALL_OFF", "RED_ON\nGREEN_ON", "ALL_OFF"]
    assert thing.state.mode == "off"


def test_verified_colour_is_extinguished_without_a_second_preflight(lights):
    """The focus-only path sends OFF immediately and verifies the result."""
    thing, controller = lights
    assert thing.set_mode("red").mode == "red"
    verified = thing._read_state()
    controller.scripts.clear()
    thing._stage._light_operation.reset_mock()
    controller_state = Mock(spec=ControllerState)
    assert thing._extinguish_after_verified(verified, controller_state).mode == "off"
    assert controller.scripts == ["ALL_OFF"]
    thing._stage._light_operation.assert_called_once_with(
        thing._url,
        controller_state,
        extra_objects=ANY,
        after_check=ANY,
    )


def test_fast_extinguish_requires_live_colour_readback(lights):
    """The private shortcut cannot be used to authorise an arbitrary transition."""
    thing, controller = lights
    with pytest.raises(IlluminationError, match="live RED/GREEN"):
        thing._extinguish_after_verified(
            thing._read_state(), Mock(spec=ControllerState)
        )
    assert controller.scripts == []


def test_verified_exposure_transitions_in_one_break_before_make_script(lights):
    """The focus path avoids an intermediate network trip but still verifies ON."""
    thing, controller = lights
    verified = thing._read_state()
    controller_state = Mock(spec=ControllerState)
    assert (
        thing._transition_after_verified(verified, controller_state, "red").mode
        == "red"
    )
    assert controller.scripts == ["ALL_OFF\nRED_ON"]
    thing._stage._light_operation.assert_called_once_with(
        thing._url,
        controller_state,
        extra_objects=ANY,
        after_check=ANY,
    )


def test_verified_mixed_probe_transitions_directly_to_white(lights):
    """Cleanup uses one break-before-make script from verified mixed light."""
    thing, controller = lights
    mixed = thing._select_red_green_probe()
    controller.scripts.clear()
    thing._stage._light_operation.reset_mock()
    controller_state = Mock(spec=ControllerState)
    assert (
        thing._transition_after_verified(mixed, controller_state, "white").mode
        == "white"
    )
    assert controller.scripts == ["ALL_OFF\nWHITE_ON"]


def test_fast_transition_requires_a_different_live_focus_light(lights):
    """An arbitrary or no-op state cannot authorise the private transition."""
    thing, controller = lights
    verified = thing._read_state()
    with pytest.raises(IlluminationError, match="different live focus light"):
        thing._transition_after_verified(verified, Mock(spec=ControllerState), "white")
    controller.values = dict.fromkeys(controller.values, 0)
    with pytest.raises(IlluminationError, match="different live focus light"):
        thing._transition_after_verified(
            thing._read_state(), Mock(spec=ControllerState), "red"
        )
    assert controller.scripts == []


def test_ambiguous_combined_transition_is_not_retried_and_cleans_up(lights):
    """A lost acknowledgement after ON never replays the colour command."""
    thing, controller = lights
    verified = thing._read_state()
    controller.fail_on = True
    with pytest.raises(IlluminationError, match="ON acknowledgement lost"):
        thing._transition_after_verified(verified, Mock(spec=ControllerState), "red")
    assert controller.scripts == ["ALL_OFF\nRED_ON", "ALL_OFF"]
    assert thing.state.mode == "off"


def test_cancelled_combined_transition_cleans_up(lights, monkeypatch):
    """Cancellation after the combined command retains the fail-safe OFF attempt."""
    thing, controller = lights
    verified = thing._read_state()

    def cancel(_duration):
        raise lt.exceptions.InvocationCancelledError

    monkeypatch.setattr(lt, "cancellable_sleep", cancel)
    with pytest.raises(lt.exceptions.InvocationCancelledError):
        thing._transition_after_verified(verified, Mock(spec=ControllerState), "green")
    assert controller.scripts == ["ALL_OFF\nGREEN_ON", "ALL_OFF"]
    assert thing.state.mode == "off"


@pytest.mark.parametrize("value", [0.5, -1, 2, True, "1", None])
def test_invalid_output_is_unknown_not_off(lights, value):
    """Non-binary, missing or mistyped output data is not a trustworthy state."""
    thing, controller = lights
    controller.values["red"] = value
    assert thing.state.mode == "unknown"
    assert thing.state.channels is None
    with pytest.raises(IlluminationError, match="output readback"):
        thing.set_mode("green")
    assert controller.scripts == []


@pytest.mark.parametrize(
    "failure", ["missing_pin", "timestamp", "not_ready", "network"]
)
def test_read_failure_is_non_mutating(lights, failure):
    """A failed preflight must not send a light command."""
    thing, controller = lights
    if failure == "missing_pin":
        del controller.values["red"]
    elif failure == "timestamp":
        controller.eventtime = "bad"
    elif failure == "not_ready":
        controller.ready = "shutdown"
    else:
        controller.fail_get = True
    assert not thing.state.available
    assert thing.state.mode == "unknown"
    with pytest.raises((KeyError, IlluminationError, httpx.ReadTimeout)):
        thing.set_mode("green")
    assert controller.scripts == []


def test_unconfirmed_off_never_enables_next_channel(lights):
    """Fail closed when ALL_OFF is acknowledged but the gate stays on."""
    thing, controller = lights
    controller.ignore_off = True
    with pytest.raises(IlluminationError, match="All-off state was not confirmed"):
        thing.set_mode("red")
    assert controller.scripts == ["ALL_OFF", "ALL_OFF"]
    assert thing.state.mode == "white"
    assert "cleanup: readback white" in thing.state.error


def test_ambiguous_on_is_not_retried_and_cleanup_is_verified(lights):
    """Lost acknowledgement after an applied ON triggers one OFF cleanup."""
    thing, controller = lights
    controller.fail_on = True
    with pytest.raises(IlluminationError, match="ON acknowledgement lost"):
        thing.set_mode("red")
    assert controller.scripts == ["ALL_OFF", "RED_ON", "ALL_OFF"]
    assert thing.state.mode == "off"
    assert "cleanup: readback off" in thing.state.error
    assert thing.set_mode("off").error == ""


def test_cleanup_failure_does_not_claim_off(lights):
    """Keep the original failure and report actual ON if cleanup also fails."""
    thing, controller = lights
    controller.fail_on = controller.fail_cleanup = True
    with pytest.raises(IlluminationError, match="Cleanup unreachable"):
        thing.set_mode("green")
    assert thing.state.mode == "green"
    assert "ON acknowledgement lost" in thing.state.error
    assert "state unknown" in thing.state.error
    assert controller.scripts == ["ALL_OFF", "GREEN_ON", "ALL_OFF"]


def test_bad_ack_is_not_success(lights):
    """HTTP 200 alone is not command acceptance."""
    thing, controller = lights
    controller.bad_ack = True
    with pytest.raises(IlluminationError, match="not acknowledged"):
        thing.set_mode("red")
    assert controller.scripts == ["ALL_OFF", "ALL_OFF"]


def test_cancelled_change_cleans_up_without_swallowing_cancel(lights, monkeypatch):
    """OFM cancellation after ON still attempts OFF."""
    thing, controller = lights

    def cancel(_duration):
        raise lt.exceptions.InvocationCancelledError

    monkeypatch.setattr(lt, "cancellable_sleep", cancel)
    with pytest.raises(lt.exceptions.InvocationCancelledError):
        thing.set_mode("green")
    assert controller.scripts == ["ALL_OFF", "GREEN_ON", "ALL_OFF"]
    assert thing.state.mode == "off"


def test_scan_lock_blocks_switching_but_not_status(lights):
    """Respect OFM's real global lock, not just a disabled browser button."""
    thing, controller = lights
    held, release = Event(), Event()

    def scan():
        with thing._thing_server_interface.global_lock:
            held.set()
            release.wait(3)

    thread = Thread(target=scan)
    thread.start()
    try:
        assert held.wait(1)
        assert thing.state.mode == "white"
        with pytest.raises(lt.exceptions.GlobalLockBusyError):
            thing.set_mode("red")
        assert controller.scripts == []
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"moonraker_url": "file:///tmp/controller"},
        {"moonraker_url": "http://user:secret@controller"},
        {"request_timeout_s": 0},
        {"request_timeout_s": 6},
        {"request_timeout_s": float("nan")},
        {"settle_ms": -1},
        {"settle_ms": float("inf")},
    ],
)
def test_invalid_settings_rejected(kwargs):
    """Reject unbounded transport/settling and URL credentials."""
    with pytest.raises(
        ValueError, match="moonraker_url|API_KEY|request_timeout_s|settle_ms"
    ):
        create_thing_without_server(MoonrakerIllumination, **kwargs)


def test_invalid_mode_is_rejected_before_commands(lights):
    """Direct Python callers cannot inject G-code as a mode."""
    thing, controller = lights
    with pytest.raises(ValueError, match="Unknown illumination mode"):
        thing.set_mode("red\nM84")
    assert controller.scripts == []


def test_switch_enters_stage_accounting_scope(lights):
    """The light writer cannot bypass stage reference accounting."""
    thing, _controller = lights
    thing.set_mode("red")
    thing._stage._light_operation.assert_called_once_with(thing._url)


def test_display_uses_subscription_but_switch_still_checks_real_outputs(lights):
    """UI polling is cheap, while cached gates never authorise RED/GREEN switching."""
    thing, controller = lights
    cached = {
        "eventtime": 123.0,
        "status": {
            "webhooks": {"state": "ready"},
            "output_pin led_white_d0": {"value": 1},
            "output_pin led_red_wifi": {"value": 0},
            "output_pin led_green_wifi": {"value": 0},
        },
    }
    thing._stage.subscribed_status.return_value = cached
    controller.fail_get = True
    assert thing.state.mode == "white"
    with pytest.raises(httpx.ReadTimeout):
        thing.set_mode("red")
    assert controller.scripts == []


def test_gate_readback_is_published_for_next_ui_read(lights):
    """Fresh verified state seeds the shared RGB/stage subscription cache."""
    thing, _controller = lights
    thing.set_mode("green")
    result = thing._stage.record_light_readback.call_args.args[1]
    assert result["status"]["output_pin led_green_wifi"]["value"] == 1
    assert result["status"]["output_pin led_white_d0"]["value"] == 0
