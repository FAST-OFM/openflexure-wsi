"""Request budgets and real SDK subscription lifecycle, without real hardware."""

import asyncio
import copy
import json
import threading
import time
from collections import Counter
from types import SimpleNamespace

import httpx
import pytest
from aiohttp import web

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.stage.moonraker import (
    MoonrakerStage,
    StageSafetyError,
    move_absolute_transit,
    move_relative_transit,
)
from openflexure_microscope_server.things.stage.moonraker_status import (
    LIGHT_PINS,
    MoonrakerStatusStream,
    StatusCache,
    StatusListener,
)
from tests.unit_tests.test_moonraker_stage import Controller, reference


@pytest.fixture
def counted_stage(mocker):
    """Count HTTP calls through the real adapter, never a real controller."""
    controller = Controller()
    stage = create_thing_without_server(
        MoonrakerStage, hardware={"allow_motion": True, "allow_operator_controls": True}
    )
    calls = []

    def handle(request):
        calls.append(request.method + " " + request.url.path)
        return controller.handle(request)

    stage._client = httpx.Client(
        base_url="http://controller", transport=httpx.MockTransport(handle)
    )
    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    reference(stage)
    calls.clear()
    yield stage, controller, calls
    stage._client.close()


@pytest.mark.parametrize(
    "operation", ["relative", "absolute", "manual", "transit", "relative_transit"]
)
def test_exactly_two_fresh_queries_per_single_move(counted_stage, operation):
    """Nested helpers share preflight; M400 always forces a new readback."""
    stage, controller, calls = counted_stage
    if operation == "relative":
        stage.move_relative(x=100)
    elif operation == "absolute":
        stage.move_absolute(x=100)
    elif operation == "manual":
        stage.move_manual(x=100)
    elif operation == "transit":
        move_absolute_transit(stage, x=100)
    else:
        move_relative_transit(stage, x=100)
    assert Counter(calls) == {
        "GET /printer/info": 2,
        "GET /printer/objects/query": 2,
        "POST /printer/gcode/script": 1,
    }
    assert calls.index("POST /printer/gcode/script") == 2
    assert "M400" in controller.scripts[-1]


def test_segments_and_separate_calls_never_share_preflight(counted_stage):
    """Each segment and every following call refreshes even with zero settle."""
    stage, controller, calls = counted_stage
    move_absolute_transit(stage, x=500)
    assert calls.count("GET /printer/objects/query") == 4
    assert len(controller.scripts) == 2
    calls.clear()
    stage.move_relative(x=-10)
    assert calls.count("GET /printer/objects/query") == 2


def test_external_change_during_settle_prevents_next_segment(counted_stage, mocker):
    """No cached preflight can hide motor release between segmented commands."""
    stage, controller, _calls = counted_stage

    def settle(seconds):
        if seconds:
            controller.status["stepper_enable"]["steppers"]["stepper_x"] = False

    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep",
        settle,
    )
    with pytest.raises(StageSafetyError, match="armed"):
        move_absolute_transit(stage, x=500)
    assert len(controller.scripts) == 1


def initial_status(controller):
    """Snapshot with independent RGB gates in the same Klipper clock domain."""
    status = copy.deepcopy(controller.status)
    for channel, pin in LIGHT_PINS.items():
        status["output_pin " + pin] = {"value": int(channel == "white")}
    return {"eventtime": controller.eventtime, "status": status}


@pytest.fixture
def populated_cache():
    """Create a subscribed cache with explicit identity, not inferred defaults."""
    controller = Controller()
    cache = StatusCache()
    generation = cache.begin()
    cache.initialise(generation, controller.pid, initial_status(controller))
    return cache, generation, controller


def test_partial_updates_and_detached_snapshots(populated_cache):
    """One object/property delta keeps all unrelated stage and gate fields."""
    cache, generation, controller = populated_cache
    cache.update(
        generation,
        {
            "eventtime": 101.0,
            "status": {"gcode_move": {"gcode_position": [1.0, 2.0, 3.0, 0.0]}},
        },
    )
    _, _, result = cache.snapshot(1)
    assert result["status"]["gcode_move"]["homing_origin"] == [0.0] * 4
    assert result["status"]["output_pin led_white_d0"]["value"] == 1
    result["status"]["toolhead"]["position"][0] = 99
    assert cache.snapshot(1)[2]["status"]["toolhead"]["position"][0] == 0
    assert controller.status["toolhead"]["position"][0] == 0


def test_wait_for_snapshot_requires_a_newer_push(populated_cache):
    """A merged HTTP query is not mistaken for a post-command notification."""
    cache, generation, controller = populated_cache
    before = controller.eventtime
    cache.record_query(
        generation,
        controller.pid,
        {"eventtime": before + 1, "status": controller.status},
    )
    assert cache.wait_for_snapshot(generation, before, 1, 0) is None

    cache.update(
        generation,
        {
            "eventtime": before + 2,
            "status": {"toolhead": {"print_time": 2.0}},
        },
    )
    snapshot = cache.wait_for_snapshot(generation, before, 1, 0)
    assert snapshot is not None
    assert snapshot[2]["status"]["toolhead"]["print_time"] == 2.0


def test_wait_for_snapshot_wakes_on_push_and_stops_on_reconnect(populated_cache):
    """The synchronous stage waiter is woken by the SDK thread or epoch loss."""
    cache, generation, controller = populated_cache
    result = []

    def wait():
        result.append(cache.wait_for_snapshot(generation, controller.eventtime, 1, 1))

    thread = threading.Thread(target=wait)
    thread.start()
    cache.update(
        generation,
        {
            "eventtime": controller.eventtime + 1,
            "status": {"motion_report": {"live_velocity": 0.0}},
        },
    )
    thread.join(1)
    assert not thread.is_alive()
    assert result[0] is not None

    result.clear()

    def wait_after_latest():
        result.append(
            cache.wait_for_snapshot(generation, controller.eventtime + 1, 1, 1)
        )

    thread = threading.Thread(target=wait_after_latest)
    thread.start()
    cache.lost(generation)
    thread.join(1)
    assert not thread.is_alive()
    assert result == [None]


def test_gate_delta_is_not_lost_behind_newer_stage_readback(populated_cache):
    """A stage-only query must not suppress a pending RED/GREEN transition."""
    cache, generation, controller = populated_cache
    cache.record_query(
        generation, controller.pid, {"eventtime": 102.0, "status": controller.status}
    )
    cache.update(
        generation,
        {
            "eventtime": 101.0,
            "status": {
                "output_pin led_white_d0": {"value": 0},
                "output_pin led_red_wifi": {"value": 1},
                "toolhead": {"print_time": -1.0},
            },
        },
    )
    result = cache.snapshot(1)[2]
    assert result["status"]["output_pin led_red_wifi"]["value"] == 1
    assert result["status"]["output_pin led_white_d0"]["value"] == 0
    assert result["status"]["toolhead"]["print_time"] == 1.0


def test_notification_before_subscribe_reply_is_merged():
    """The SDK dispatches notification tasks independently of RPC replies."""
    cache = StatusCache()
    generation = cache.begin()
    cache.update(
        generation, {"eventtime": 101.0, "status": {"toolhead": {"print_time": 9.0}}}
    )
    cache.initialise(generation, 10, initial_status(Controller()))
    assert cache.snapshot(1)[2]["status"]["toolhead"]["print_time"] == 9.0


def test_old_callbacks_and_regressing_stream(populated_cache):
    """An old client cannot poison a new one; regression in the active stream fails."""
    cache, old, controller = populated_cache
    generation = cache.begin()
    cache.initialise(generation, 10, initial_status(controller))
    cache.lost(old)
    cache.update(
        old, {"eventtime": 900.0, "status": {"toolhead": {"print_time": 90.0}}}
    )
    assert cache.health() == (generation, True)
    cache.update(
        generation, {"eventtime": 101.0, "status": {"toolhead": {"print_time": 2.0}}}
    )
    cache.update(
        generation, {"eventtime": 99.0, "status": {"toolhead": {"print_time": 1.0}}}
    )
    assert not cache.health()[1]
    assert cache.snapshot(1) is None


def test_stale_cache_and_pid_change(populated_cache, monkeypatch):
    """Freshness belongs to controller data, not a timer on operator zero."""
    cache, generation, controller = populated_cache
    now = time.monotonic()
    monkeypatch.setattr(
        "openflexure_microscope_server.things.stage.moonraker_status.time.monotonic",
        lambda: now + 2,
    )
    assert cache.snapshot(1) is None
    with pytest.raises(ConnectionError, match="changed"):
        cache.record_query(generation, 11, initial_status(controller))
    assert not cache.health()[1]


@pytest.mark.parametrize(
    ("method", "data"),
    [
        ("notify_status_update", []),
        ("notify_status_update", [None, 101.0]),
        ("notify_status_update", [{}, float("nan")]),
        ("notify_klippy_shutdown", None),
        ("notify_klippy_disconnected", None),
        ("notify_klippy_ready", None),
    ],
)
def test_lifecycle_or_malformed_notification_invalidates(populated_cache, method, data):
    """Never turn missing/invalid state into a remembered successful reference."""
    cache, generation, _ = populated_cache
    asyncio.run(StatusListener(cache, generation).on_notification(method, data))
    assert not cache.health()[1]


def attach_cache(stage, controller):
    """Inject only a subscription snapshot; commands still use the fake HTTP path."""
    cache = StatusCache()
    generation = cache.begin()
    cache.initialise(generation, controller.pid, initial_status(controller))
    stage._status_stream = SimpleNamespace(cache=cache)
    stage._status_generation = generation
    return cache, generation


def test_ui_reads_subscription_but_writes_use_fresh_http(counted_stage):
    """A stale-looking cached zero never authorises movement past external activity."""
    stage, controller, calls = counted_stage
    attach_cache(stage, controller)
    assert stage.controller_state["reference_valid"]
    assert stage.position["x"] == 0
    assert calls == []
    controller.status["toolhead"]["print_time"] += 1
    with pytest.raises(StageSafetyError, match="External motion"):
        stage.move_relative(x=10)
    assert not controller.scripts
    assert calls.count("GET /printer/objects/query") == 1


def test_reconnect_does_not_restore_zero(counted_stage):
    """Even identical coordinates after a lost connection require a new operator zero."""
    stage, controller, _ = counted_stage
    cache, old = attach_cache(stage, controller)
    cache.lost(old)
    generation = cache.begin()
    cache.initialise(generation, controller.pid, initial_status(controller))
    with pytest.raises(StageSafetyError, match="fresh local zero"):
        stage.move_relative(x=10)
    assert not controller.scripts
    assert not stage.controller_state["reference_valid"]


def test_disconnect_during_command_never_replays(counted_stage, mocker):
    """A command can take effect despite lost state; the adapter must fail closed."""
    stage, controller, calls = counted_stage
    cache, generation = attach_cache(stage, controller)
    execute = stage._execute_move

    def lose(*args):
        execute(*args)
        cache.lost(generation)

    mocker.patch.object(stage, "_execute_move", side_effect=lose)
    with pytest.raises(StageSafetyError, match="uncertain"):
        stage.move_relative(x=10)
    assert len(controller.scripts) == 1
    assert calls.count("POST /printer/gcode/script") == 1
    assert stage._reference is None


def test_conclusive_motion_push_replaces_only_the_final_query(counted_stage, mocker):
    """Preflight stays fresh while exact post-M400 push avoids duplicate polling."""
    stage, controller, calls = counted_stage
    cache, generation = attach_cache(stage, controller)
    execute = stage._execute_move

    def execute_and_publish(*args):
        execute(*args)
        controller.eventtime += 0.01
        cache.update(generation, initial_status(controller))

    mocker.patch.object(stage, "_execute_move", side_effect=execute_and_publish)
    stage.move_relative(x=10)

    assert calls.count("GET /printer/objects/query") == 1
    assert calls.count("POST /printer/gcode/script") == 1
    assert stage.last_motion_readback_source == "push"
    assert stage.last_motion_readback_duration_s is not None
    assert stage.controller_state["last_motion_readback_source"] == "push"
    assert stage.position == {"x": 10, "y": 0, "z": 0}


def wait_until(predicate, timeout=3):
    """Bound an asynchronous test observation; never a hardware wait."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condition did not arrive")


def websocket_reply(peer, obj):
    """Answer the only three read-only RPCs accepted by this test peer."""
    method = obj["method"]
    peer.calls.append(method)
    if method == "printer.info":
        return {"process_id": peer.controller.pid}
    if method == "printer.objects.list":
        return {"objects": list(initial_status(peer.controller)["status"])}
    if method == "printer.objects.subscribe":
        assert "output_pin led_red_wifi" in obj["params"]["objects"]
        return initial_status(peer.controller)
    raise AssertionError("Subscription sent a non-read-only request")


async def drop_requested_socket(peer, ws):
    """Disconnect one pending request to exercise SDK cancellation handling."""
    if not peer.drop_request:
        return False
    peer.drop_request = False
    await ws.close()
    return True


def websocket_handler(peer):
    """Build the fake peer's WebSocket lifecycle separately from server setup."""

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        peer.sockets.add(ws)
        publisher = None

        async def publish():
            while not ws.closed:
                await asyncio.sleep(0.05)
                peer.controller.eventtime += 0.01
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "method": "notify_status_update",
                        "params": [
                            initial_status(peer.controller)["status"],
                            peer.controller.eventtime,
                        ],
                    }
                )

        try:
            async for message in ws:
                obj = json.loads(message.data)
                if await drop_requested_socket(peer, ws):
                    break
                result = websocket_reply(peer, obj)
                if obj["method"] == "printer.objects.subscribe":
                    publisher = asyncio.create_task(publish())
                await ws.send_json(
                    {"jsonrpc": "2.0", "id": obj["id"], "result": result}
                )
        finally:
            if publisher is not None:
                publisher.cancel()
                await asyncio.gather(publisher, return_exceptions=True)
            peer.sockets.discard(ws)
        return ws

    return handler


@pytest.fixture
def websocket_server():
    """Exercise the actual installed SDK against an in-process Moonraker peer."""
    ready, halt = threading.Event(), threading.Event()
    peer = SimpleNamespace(
        calls=[],
        sockets=set(),
        port=None,
        loop=None,
        controller=Controller(),
        drop_request=False,
    )

    async def serve():
        app = web.Application()
        app.router.add_get("/websocket", websocket_handler(peer))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        peer.loop = asyncio.get_running_loop()
        peer.port = site._server.sockets[0].getsockname()[1]
        ready.set()
        while not halt.is_set():
            await asyncio.sleep(0.01)
        for ws in list(peer.sockets):
            await ws.close()
        await runner.cleanup()

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    assert ready.wait(3)
    yield peer
    halt.set()
    thread.join(3)
    assert not thread.is_alive()


def test_real_sdk_subscribes_reconnects_and_closes_without_writes(websocket_server):
    """Real WebSocket frames, real SDK, RGB + motion updates; no physical commands."""
    peer = websocket_server
    stream = MoonrakerStatusStream(f"http://127.0.0.1:{peer.port}", None, 1.0, 0.05)
    stream.start()
    try:
        wait_until(lambda: stream.cache.health()[1])
        first = stream.cache.health()[0]
        assert (
            stream.cache.snapshot(1)[2]["status"]["output_pin led_white_d0"]["value"]
            == 1
        )

        async def disconnect():
            for ws in list(peer.sockets):
                await ws.close()

        asyncio.run_coroutine_threadsafe(disconnect(), peer.loop).result(2)
        wait_until(
            lambda: stream.cache.health()[0] > first and stream.cache.health()[1]
        )
        assert peer.calls.count("printer.objects.subscribe") == 2
        assert set(peer.calls) == {
            "printer.info",
            "printer.objects.list",
            "printer.objects.subscribe",
        }
    finally:
        stream.close()
    assert not stream._thread.is_alive()
    assert not stream.cache.health()[1]


def test_http_readbacks_cannot_hide_stalled_subscription(populated_cache, monkeypatch):
    """Fresh stage queries must not indefinitely refresh old gate display data."""
    cache, generation, controller = populated_cache
    now = time.monotonic()
    monkeypatch.setattr(
        "openflexure_microscope_server.things.stage.moonraker_status.time.monotonic",
        lambda: now + 2,
    )
    cache.record_query(
        generation, controller.pid, {"eventtime": 102.0, "status": controller.status}
    )
    assert cache.snapshot(1) is None


def test_fresh_light_readback_preserves_newer_stage_state(populated_cache):
    """A confirmed light change updates the UI without clobbering motion metadata."""
    cache, generation, controller = populated_cache
    cache.record_query(
        generation, controller.pid, {"eventtime": 102.0, "status": controller.status}
    )
    cache.record_query(
        generation,
        controller.pid,
        {
            "eventtime": 101.0,
            "status": {
                "output_pin led_green_wifi": {"value": 1},
                "output_pin led_white_d0": {"value": 0},
            },
        },
    )
    data = cache.snapshot(1)[2]["status"]
    assert data["output_pin led_green_wifi"]["value"] == 1
    assert data["toolhead"]["position"] == controller.status["toolhead"]["position"]


def test_real_sdk_recovers_from_disconnect_during_initial_rpc(websocket_server):
    """SDK request cancellation is connection loss, never a fatal monitor exit."""
    peer = websocket_server
    peer.drop_request = True
    stream = MoonrakerStatusStream(f"http://127.0.0.1:{peer.port}", None, 1.0, 0.05)
    stream.start()
    try:
        wait_until(lambda: stream.cache.health()[1])
        assert stream._thread.is_alive()
        assert set(peer.calls) == {
            "printer.info",
            "printer.objects.list",
            "printer.objects.subscribe",
        }
    finally:
        stream.close()
    assert not stream._thread.is_alive()
