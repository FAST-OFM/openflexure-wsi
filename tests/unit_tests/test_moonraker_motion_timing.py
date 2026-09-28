"""Mixed-axis feed planning against a fake controller; never real hardware."""

import json
import math
import re
from pathlib import Path

import httpx
import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.fast_ofm_contracts import MotionSettings
from openflexure_microscope_server.things.stage.moonraker import (
    MoonrakerStage,
    StageSafetyError,
)
from tests.unit_tests.test_moonraker_stage import Controller, reference


@pytest.fixture
def stage_pair(mocker):
    """Use the published prototype profile, with no real network or sleeps."""
    hardware = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/prototype_motion_profile.json"
        ).read_text()
    )["things"]["stage"]["kwargs"]["hardware"]
    stage = create_thing_without_server(MoonrakerStage, hardware=hardware)
    controller = Controller()
    stage._client = httpx.Client(
        base_url="http://controller", transport=httpx.MockTransport(controller.handle)
    )
    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    reference(stage)
    yield stage, controller
    stage._client.close()


def script_dynamics(script):
    """Read the actual six-decimal numbers sent to the controller."""
    velocity, accel = re.search(
        r"SET_VELOCITY_LIMIT VELOCITY=([0-9.]+) ACCEL=([0-9.]+)", script
    ).groups()
    feed = re.search(r"G1 .* F([0-9.]+)", script)[1]
    return min(float(velocity), float(feed) / 60), float(accel)


def assert_axis_limits(stage, delta, speed, accel):
    """Project path feed back onto each physical axis, including unequal units."""
    mm = {
        axis: abs(value / stage._hardware.axes[axis].units_per_mm)
        for axis, value in delta.items()
        if value
    }
    distance = math.hypot(*mm.values())
    for axis, component in mm.items():
        spec = stage._hardware.axes[axis]
        assert speed * component / distance <= spec.speed_mm_s + 1e-12
        assert accel * component / distance <= spec.accel_mm_s2 + 1e-12


@pytest.mark.parametrize("sign", [-1, 1])
def test_failed_scan_transition_and_reverse_use_axis_projections(stage_pair, sign):
    """A tiny Z correction must not impose the Z speed on the whole XY path."""
    stage, controller = stage_pair
    delta = {a: sign * d for a, d in zip("xyz", (580, -289, 3), strict=True)}
    before = stage.hardware_settings
    stage.move_relative(**delta)
    assert stage.position == delta
    assert len(controller.scripts) == 1
    speed, accel = script_dynamics(controller.scripts[0])
    assert 4.469 <= speed <= 4.470
    assert_axis_limits(stage, delta, speed, accel)
    assert "M400" in controller.scripts[0]
    assert stage.hardware_settings == before
    assert stage.controller_state["reference_valid"]


@pytest.mark.parametrize(
    "delta",
    [
        {"x": 580},
        {"y": -578},
        {"z": 50},
        {"x": 580, "y": -289},
        {"x": 1, "z": 50},
        {"x": -800, "y": 400, "z": -30},
        {"x": 1, "y": -1, "z": 1},
    ],
)
def test_actual_command_obeys_each_axis_velocity_and_acceleration(stage_pair, delta):
    """Single axes remain unchanged; Z-heavy and diagonal paths remain bounded."""
    stage, controller = stage_pair
    stage.move_relative(**delta)
    speed, accel = script_dynamics(controller.scripts[-1])
    assert_axis_limits(stage, delta, speed, accel)
    if len(delta) == 1:
        axis = next(iter(delta))
        assert speed == stage._hardware.axes[axis].speed_mm_s
        assert accel == stage._hardware.axes[axis].accel_mm_s2


def test_controller_global_limits_cap_projected_path(stage_pair):
    """Klipper's global path caps are not mistaken for per-axis limits."""
    stage, controller = stage_pair
    controller.status["toolhead"]["max_velocity"] = 0.1
    controller.status["toolhead"]["max_accel"] = 0.2
    delta = {"x": 580, "y": -289, "z": 3}
    stage.move_relative(**delta)
    speed, accel = script_dynamics(controller.scripts[-1])
    assert speed <= 0.1
    assert accel <= 0.2
    assert_axis_limits(stage, delta, speed, accel)


def test_projection_uses_mm_not_integer_units_or_direction(stage_pair):
    """Axis scale and sign may differ without changing physical feed constraints."""
    stage, controller = stage_pair
    config = stage._hardware.model_dump()
    config["axes"]["x"]["units_per_mm"] = 2000.0
    config["axes"]["y"]["units_per_mm"] = 800.0
    config["axes"]["z"]["units_per_mm"] = 10000.0
    config["axes"]["x"]["direction_sign"] = -1
    stage._hardware = MotionSettings.model_validate(config)
    delta = {"x": 1160, "y": -232, "z": 30}
    stage.move_relative(**delta)
    speed, accel = script_dynamics(controller.scripts[-1])
    assert 4.472 <= speed <= 4.473
    assert_axis_limits(stage, delta, speed, accel)
    assert "X-0.580000" in controller.scripts[-1]


def test_genuine_timeout_is_rejected_without_post(stage_pair):
    """The fix does not increase or bypass a too-short timeout."""
    stage, controller = stage_pair
    config = stage._hardware.model_dump()
    config["axes"]["x"]["speed_mm_s"] = 0.25
    config["axes"]["x"]["accel_mm_s2"] = 2.0
    config["axes"]["x"]["move_timeout_s"] = 5.0
    stage._hardware = MotionSettings.model_validate(config)
    with pytest.raises(StageSafetyError, match="Configured move timeout"):
        stage.move_relative(x=580, y=-289, z=3)
    assert not controller.scripts
    assert stage.controller_state["reference_valid"]


def test_feed_below_gcode_precision_is_rejected_before_post(stage_pair):
    """Flooring a projected limit must never produce a zero-speed command."""
    stage, controller = stage_pair
    config = stage._hardware.model_dump()
    config["axes"]["x"]["speed_mm_s"] = 0.0000005
    stage._hardware = MotionSettings.model_validate(config)
    with pytest.raises(StageSafetyError, match="precision"):
        stage.move_relative(x=1)
    assert not controller.scripts


def test_all_rounded_segments_are_preflighted_before_first_post(stage_pair):
    """A later rounded segment may cross the time budget when the first passes."""
    stage, controller = stage_pair
    config = stage._hardware.model_dump()
    config["axes"]["x"]["speed_mm_s"] = 0.25
    config["axes"]["x"]["accel_mm_s2"] = 2.0
    config["axes"]["z"]["speed_mm_s"] = 0.02
    config["axes"]["z"]["accel_mm_s2"] = 0.1
    config["axes"]["x"]["move_timeout_s"] = 5.252
    stage._hardware = MotionSettings.model_validate(config)
    state = stage._fetch_state()
    # First rounded segment (500, 0, 0) fits, second (501, 0, 1) does not.
    stage._check_target(state, stage._reference, {"x": 500})
    with pytest.raises(StageSafetyError, match="Configured move timeout"):
        stage.move_absolute_in_segments((1001, 0, 1))
    assert not controller.scripts
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    assert stage._reference.expected.gcode_position == (0.0, 0.0, 0.0)


def test_segmented_mixed_transit_roundtrip_is_bounded(stage_pair):
    """Preflight and execution use the same rounded path, in both directions."""
    stage, controller = stage_pair
    final = (1580, -1289, 63)
    stage.move_absolute_in_segments(final)
    assert tuple(stage.position[a] for a in "xyz") == final
    stage.move_absolute_in_segments((0, 0, 0))
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    assert len(controller.scripts) == 4
    previous = dict.fromkeys("xyz", 0.0)
    for script in controller.scripts:
        move = next(line for line in script.splitlines() if line.startswith("G1 "))
        position = dict(previous)
        position.update(
            {a.lower(): float(v) for a, v in re.findall(r"([XYZ])(-?[0-9.]+)", move)}
        )
        delta = {a: round((position[a] - previous[a]) * 1000) for a in "xyz"}
        speed, accel = script_dynamics(script)
        assert_axis_limits(stage, delta, speed, accel)
        for axis in "xyz":
            assert (
                abs(position[axis] - previous[axis])
                <= stage._hardware.axes[axis].max_move_mm
            )
        previous = position


def test_actual_scan_return_from_nonzero_position(stage_pair):
    """Reproduce the failed return with the original controller coordinates."""
    stage, controller = stage_pair
    current = [-1.33, -0.039, -0.009, 0.0]
    controller.status["gcode_move"]["gcode_position"] = list(current)
    controller.status["toolhead"]["position"] = list(current)
    stage._reference.expected = stage._fetch_state()
    stage.move_absolute_in_segments((-750, 250, -5))
    assert stage.position == {"x": -750, "y": 250, "z": -5}
    assert len(controller.scripts) == 1
    speed, accel = script_dynamics(controller.scripts[0])
    assert_axis_limits(stage, {"x": 580, "y": 289, "z": 4}, speed, accel)
    assert stage.controller_state["reference_valid"]
