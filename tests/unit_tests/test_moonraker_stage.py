"""Exercise Moonraker requests and safety failures without physical hardware."""

import json
import re
from types import SimpleNamespace

import httpx
import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.fast_ofm_contracts import MotionSettings
from openflexure_microscope_server.things.stage import BacklashCompensation
from openflexure_microscope_server.things.stage.moonraker import (
    MoonrakerStage,
    StageSafetyError,
)
from openflexure_microscope_server.things.stage.moonraker_status import StatusCache


class Controller:
    """A deterministic HTTP-level fake, including post-completion position readback."""

    def __init__(self):
        """Create a ready, referenced controller without any movement."""
        self.pid = 10
        self.eventtime = 100.0
        self.scripts = []
        self.object_queries = []
        self.timeout = False
        self.wrong_target = False
        self.velocity_after_move = 0.0
        self.status = {
            "webhooks": {"state": "ready"},
            "toolhead": {
                "homed_axes": "xyz",
                "position": [0.0] * 4,
                "axis_minimum": [-10.0] * 4,
                "axis_maximum": [10.0] * 4,
                "print_time": 1.0,
                "estimated_print_time": 10.0,
                "max_velocity": 20.0,
                "max_accel": 50.0,
            },
            "gcode_move": {"gcode_position": [0.0] * 4, "homing_origin": [0.0] * 4},
            "stepper_enable": {
                "steppers": {"stepper_x": True, "stepper_y": True, "stepper_z": True}
            },
            "motion_report": {"live_velocity": 0.0},
        }

    def handle(self, request):
        """Serve the minimal real API shape and record each submitted script once."""
        if request.url.path == "/printer/info":
            return httpx.Response(200, json={"result": {"process_id": self.pid}})
        if request.url.path == "/printer/objects/query":
            self.object_queries.append(str(request.url))
            self.eventtime += 0.01
            return httpx.Response(
                200,
                json={"result": {"eventtime": self.eventtime, "status": self.status}},
            )
        if request.url.path == "/printer/gcode/script":
            script = json.loads(request.content)["script"]
            self.scripts.append(script)
            if self.timeout:
                raise httpx.ReadTimeout("Acknowledgement lost")
            self.apply_operator_script(script)
            moves = [line for line in script.splitlines() if line.startswith("G1 ")]
            for move in moves:
                for axis, value in re.findall(r"([XYZ])(-?[0-9.]+)", move):
                    index = "XYZ".index(axis)
                    target = float(value) + (1 if self.wrong_target else 0)
                    offset = (
                        self.status["toolhead"]["position"][index]
                        - self.status["gcode_move"]["gcode_position"][index]
                    )
                    self.status["gcode_move"]["gcode_position"][index] = target
                    self.status["toolhead"]["position"][index] = target + offset
            self.status["toolhead"]["print_time"] += 1
            self.status["toolhead"]["estimated_print_time"] = max(
                self.status["toolhead"]["estimated_print_time"],
                self.status["toolhead"]["print_time"] + 1,
            )
            if moves:
                self.status["motion_report"]["live_velocity"] = self.velocity_after_move
            return httpx.Response(200, json={"result": "ok"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def apply_operator_script(self, script):
        """Apply the fake's explicit motor and reference commands, without movement."""
        for axis in "xyz":
            if f"SET_STEPPER_ENABLE STEPPER=stepper_{axis} ENABLE=1" in script:
                self.status["stepper_enable"]["steppers"]["stepper_" + axis] = True
        if "M84" in script:
            self.status["stepper_enable"]["steppers"] = {
                "stepper_" + axis: False for axis in "xyz"
            }
            self.status["toolhead"]["homed_axes"] = ""
        zero = next(
            (
                line
                for line in script.splitlines()
                if line.startswith("SET_KINEMATIC_POSITION ")
            ),
            "",
        )
        if zero:
            for axis in "xyz":
                if f"{axis.upper()}=0" in zero:
                    index = "xyz".index(axis)
                    offset = self.status["gcode_move"]["homing_origin"][index]
                    self.status["toolhead"]["position"][index] = 0.0
                    self.status["gcode_move"]["gcode_position"][index] = -offset
                    if axis not in self.status["toolhead"]["homed_axes"]:
                        self.status["toolhead"]["homed_axes"] += axis


@pytest.fixture
def setup_stage(mocker):
    """Create a fresh stage/fake-controller pair, with sleeping replaced in tests."""
    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    controller = Controller()
    stage = create_thing_without_server(MoonrakerStage, hardware={"allow_motion": True})
    stage._client = httpx.Client(
        base_url="http://controller", transport=httpx.MockTransport(controller.handle)
    )
    yield stage, controller
    stage._client.close()


def reference(stage):
    """Explicitly establish the fake operator's local zero."""
    stage.set_zero_position(confirmed_manual_zero=True, exclusive_control=True)


def test_no_startup_reference_or_writes(setup_stage):
    """Reading the stage must not home, arm, move or fabricate a reference."""
    stage, controller = setup_stage
    assert stage.controller_state["reference_valid"] is False
    assert stage.thing_state["position_source"] == "commanded_not_encoder"
    with pytest.raises(StageSafetyError, match="reference"):
        stage.move_relative(x=100)
    with pytest.raises(StageSafetyError, match="reference"):
        _ = stage.position
    assert controller.scripts == []


def test_controller_and_extra_objects_share_one_fresh_query(setup_stage):
    """Acquisition may merge light fields without caching the safety readback."""
    stage, controller = setup_stage
    state, result = stage._fetch_state_with_objects(
        ("output_pin led_white_d0", "output_pin led_red_wifi")
    )
    assert state.pid == controller.pid
    assert result["status"] == controller.status
    assert len(controller.object_queries) == 1
    assert "output_pin%20led_white_d0" in controller.object_queries[0]
    assert "output_pin%20led_red_wifi" in controller.object_queries[0]


def test_reference_identity_is_new_for_every_successful_zero(setup_stage):
    """Repeated Set zero at identical XYZ replaces the non-persistent identity."""
    stage, controller = setup_stage
    assert stage.controller_state["reference_id"] is None
    reference(stage)
    first = stage.controller_state["reference_id"]
    reference(stage)
    second = stage.controller_state["reference_id"]
    assert isinstance(first, str)
    assert isinstance(second, str)
    assert first != second
    assert stage.controller_state["motion_revision"] == 0
    stage.release_control()
    assert stage.controller_state["reference_id"] is None
    assert not controller.scripts


def test_motion_revision_records_return_to_same_xyz(setup_stage):
    """A physical there-and-back history cannot masquerade as no intervening move."""
    stage, _controller = setup_stage
    reference(stage)
    stage.move_relative(x=10)
    stage.move_absolute(x=0)
    state = stage.controller_state
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    assert state["motion_revision"] == 2


@pytest.mark.parametrize("residual", [-3.469446951953614e-18, 3.469446951953614e-18])
def test_completed_move_accepts_velocity_roundoff(setup_stage, residual):
    """Keep a valid reference after acknowledged motion with numerical zero velocity."""
    stage, controller = setup_stage
    reference(stage)
    controller.velocity_after_move = residual
    stage.move_relative(x=50)
    state = stage.controller_state
    assert state["reference_valid"] is True
    assert state["motion_idle"] is True
    assert state["live_velocity"] == residual
    assert len(controller.scripts) == 1


@pytest.mark.parametrize("velocity", [-1e-8, 1e-8, 0.1])
def test_idle_still_rejects_nonzero_motion(setup_stage, velocity):
    """Do not broaden the idle gate to slow but real reported motion."""
    stage, controller = setup_stage
    controller.status["motion_report"]["live_velocity"] = velocity
    assert stage.controller_state["motion_idle"] is False
    assert controller.scripts == []


def test_numerical_zero_does_not_bypass_pending_queue(setup_stage):
    """A stopped axis with queued future commands is still not idle."""
    stage, controller = setup_stage
    controller.status["motion_report"]["live_velocity"] = -3.469446951953614e-18
    controller.status["toolhead"]["print_time"] = 20.0
    assert stage.controller_state["motion_idle"] is False


def test_explicit_confirmations_and_readonly_profile(setup_stage):
    """Neither a plain zero click nor a read-only deployment can acquire control."""
    stage, controller = setup_stage
    with pytest.raises(StageSafetyError, match="Confirm"):
        stage.set_zero_position()
    stage._hardware = MotionSettings()
    with pytest.raises(StageSafetyError, match="disabled"):
        reference(stage)
    assert controller.scripts == []


def allow_operator(stage):
    """Opt into explicit motor and reference commands for a test."""
    stage._hardware = stage._hardware.model_copy(
        update={"allow_operator_controls": True}
    )


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_manual_settle_is_separate_from_automatic(setup_stage, mocker, axis):
    """Manual relative/absolute moves still verify M400, without changing auto settle."""
    from pathlib import Path

    stage, controller = setup_stage
    hardware = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/prototype_motion_profile.json"
        ).read_text()
    )["things"]["stage"]["kwargs"]["hardware"]
    stage._hardware = MotionSettings.model_validate(hardware)
    reference(stage)
    sleep = mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    stage.move_manual(**{axis: 5})
    assert stage.position[axis] == 5
    assert "M400" in controller.scripts[-1]
    assert all(call.args == (0,) for call in sleep.call_args_list)
    assert stage.controller_state["motion_idle"]
    stage.move_manual(relative=False, **{axis: 0})
    assert stage.position[axis] == 0
    assert all(call.args == (0,) for call in sleep.call_args_list)
    sleep.reset_mock()
    stage.move_relative(**{axis: 5})
    sleep.assert_any_call(0.0)
    sleep.reset_mock()
    stage.move_absolute(**{axis: 0})
    sleep.assert_any_call(0.0)


def test_manual_delay_is_configurable_and_context_local(setup_stage, mocker):
    """An explicit short manual delay cannot change the automatic policy."""
    from concurrent.futures import ThreadPoolExecutor

    stage, _ = setup_stage
    allow_operator(stage)
    reference(stage)
    stage._hardware = stage._hardware.model_copy(update={"manual_settle_ms": 5.0})
    sleep = mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    actual_execute = stage._execute_move
    other_contexts = []

    def execute(*args):
        with ThreadPoolExecutor(max_workers=1) as pool:
            other_contexts.append(pool.submit(stage._manual_control.get).result())
        actual_execute(*args)

    mocker.patch.object(stage, "_execute_move", side_effect=execute)
    stage.move_manual(x=5)
    sleep.assert_any_call(0.005)
    assert other_contexts == [False]
    assert stage._manual_control.get() is False


@pytest.mark.parametrize("fault", ["timeout", "wrong_target", "velocity_after_move"])
def test_manual_failure_never_retries_or_leaks_policy(setup_stage, mocker, fault):
    """Even HTTP 200 is insufficient if readback is wrong or movement remains."""
    stage, controller = setup_stage
    allow_operator(stage)
    reference(stage)
    setattr(controller, fault, 0.1 if fault == "velocity_after_move" else True)
    with pytest.raises(StageSafetyError, match="uncertain"):
        stage.move_manual(x=5)
    assert len(controller.scripts) == 1
    assert stage._manual_control.get() is False
    assert not stage.controller_state["reference_valid"]
    setattr(controller, fault, 0.0 if fault == "velocity_after_move" else False)
    controller.status["motion_report"]["live_velocity"] = 0.0
    reference(stage)
    sleep = mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    stage.move_relative(x=5)
    sleep.assert_any_call(0.75)


def test_manual_move_retains_operator_and_motion_gates(setup_stage):
    """No manual shortcut around operator permission, reference, axes or bounds."""
    stage, controller = setup_stage
    with pytest.raises(StageSafetyError, match="Operator controls"):
        stage.move_manual(x=5)
    allow_operator(stage)
    with pytest.raises(StageSafetyError, match="reference"):
        stage.move_manual(x=5)
    reference(stage)
    for move in ({"x": 251}, {"z": 5}):
        with pytest.raises(StageSafetyError, match="maximum|disabled"):
            stage.move_manual(**move)
    assert controller.scripts == []


def test_enable_disable_motors_without_motion(setup_stage):
    """Buttons only energise/release motors and discard any prior reference."""
    stage, controller = setup_stage
    allow_operator(stage)
    reference(stage)
    stage.disable_motors(confirmed_release=True)
    assert not stage.controller_state["reference_valid"]
    assert not any(controller.status["stepper_enable"]["steppers"].values())
    stage.enable_motors(exclusive_control=True)
    assert all(controller.status["stepper_enable"]["steppers"].values())
    assert not stage.controller_state["reference_valid"]
    assert all(
        "G1 " not in script and "G28" not in script for script in controller.scripts
    )
    assert controller.status["toolhead"]["position"] == [0.0] * 4


@pytest.mark.parametrize(
    ("action", "confirm"),
    [
        ("enable_motors", {"exclusive_control": True}),
        ("disable_motors", {"confirmed_release": True}),
    ],
)
def test_operator_controls_require_profile_confirmation_and_idle(
    setup_stage, action, confirm
):
    """Missing opt-in/confirmation or an active queue must not send a command."""
    stage, controller = setup_stage
    with pytest.raises(StageSafetyError, match="disabled"):
        getattr(stage, action)(**confirm)
    allow_operator(stage)
    with pytest.raises(StageSafetyError, match="Confirm"):
        getattr(stage, action)()
    controller.status["motion_report"]["live_velocity"] = 0.1
    with pytest.raises(StageSafetyError, match="idle"):
        getattr(stage, action)(**confirm)
    assert controller.scripts == []


def test_operator_timeout_invalidates_reference_without_retry(setup_stage):
    """An ambiguous motor command cannot silently preserve a usable zero."""
    stage, controller = setup_stage
    allow_operator(stage)
    reference(stage)
    controller.timeout = True
    with pytest.raises(StageSafetyError, match="do not retry"):
        stage.disable_motors(confirmed_release=True)
    assert len(controller.scripts) == 1
    assert not stage.controller_state["reference_valid"]


def test_explicit_controller_zero_leaves_disabled_z_untouched(setup_stage):
    """Initial XY reference works from boot without homing or changing Z."""
    stage, controller = setup_stage
    allow_operator(stage)
    controller.status["toolhead"]["homed_axes"] = ""
    controller.status["toolhead"]["position"][2] = 2.0
    controller.status["gcode_move"]["gcode_position"][2] = 2.0
    stage.set_zero_position(
        confirmed_manual_zero=True, exclusive_control=True, initialise_controller=True
    )
    assert controller.scripts == ["SET_KINEMATIC_POSITION X=0 Y=0 SET_HOMED=xy\nM400"]
    assert controller.status["toolhead"]["position"][2] == 2.0
    assert stage.position == {"x": 0, "y": 0, "z": 0}


def test_zero_requires_enabled_motors(setup_stage):
    """No firmware coordinate change occurs before all XYZ motors hold."""
    stage, controller = setup_stage
    allow_operator(stage)
    controller.status["stepper_enable"]["steppers"]["stepper_z"] = False
    with pytest.raises(StageSafetyError, match="Enable motors"):
        stage.set_zero_position(
            confirmed_manual_zero=True,
            exclusive_control=True,
            initialise_controller=True,
        )
    assert controller.scripts == []


def test_deployed_xyz_profile_small_z_step(setup_stage):
    """The user's broad travel setting does not expand an individual Z button step."""
    from pathlib import Path

    stage, controller = setup_stage
    config = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/prototype_motion_profile.json"
        ).read_text()
    )
    stage._hardware = MotionSettings.model_validate(
        config["things"]["stage"]["kwargs"]["hardware"]
    )
    assert stage._hardware.axes["z"].min_mm == -10
    assert stage._hardware.axes["z"].max_mm == 20
    stage.set_zero_position(
        confirmed_manual_zero=True, exclusive_control=True, initialise_controller=True
    )
    assert "Z=0 SET_HOMED=xyz" in controller.scripts[0]
    stage.move_relative(z=5)
    assert stage.position["z"] == 5
    assert "Z0.005000 F6.000000" in controller.scripts[-1]
    assert "ACCEL=1.000000" in controller.scripts[-1]
    with pytest.raises(StageSafetyError, match="maximum"):
        stage.move_relative(z=51)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_deployed_xy_one_mm_limit(setup_stage, axis, sign):
    """Allow the requested 1000 micron XY step, preserving dynamics and Z limits."""
    from pathlib import Path

    stage, controller = setup_stage
    config = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/prototype_motion_profile.json"
        ).read_text()
    )
    stage._hardware = MotionSettings.model_validate(
        config["things"]["stage"]["kwargs"]["hardware"]
    )
    reference(stage)
    assert stage._hardware.axes[axis].max_move_mm == 1.0
    assert stage._hardware.axes[axis].ui_step_mm == 0.05
    assert stage._hardware.axes["z"].max_move_mm == 0.05
    with pytest.raises(StageSafetyError, match="maximum"):
        stage.move_relative(**{axis: sign * 1001})
    assert controller.scripts == []
    stage.move_relative(**{axis: sign * 1000})
    assert stage.position[axis] == sign * 1000
    assert f"{axis.upper()}{sign * 1.0:.6f}" in controller.scripts[0]
    assert "F240.000000" in controller.scripts[0]
    assert "ACCEL=40.000000" in controller.scripts[0]
    stage.move_absolute(**{axis: 0})
    assert stage.position[axis] == 0
    import openflexure_microscope_server.things.stage.moonraker as module

    module.lt.cancellable_sleep.assert_any_call(0.0)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_relative_absolute_and_settle(setup_stage, mocker, axis, sign):
    """Units, signs, completion and settling work in both XY directions."""
    stage, controller = setup_stage
    reference(stage)
    stage.move_relative(**{axis: sign * 100})
    assert stage.position[axis] == sign * 100
    assert f"{axis.upper()}{sign * 0.1:.6f}" in controller.scripts[0]
    assert "F15.000000" in controller.scripts[0]
    assert "ACCEL=2.000000" in controller.scripts[0]
    assert controller.scripts[0].index("M400") < controller.scripts[0].index(
        "RESTORE_GCODE_STATE"
    )
    assert "MOVE=1" not in controller.scripts[0]
    stage.move_absolute(**{axis: 0})
    assert stage.position[axis] == 0
    sleep = mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    stage.move_relative(**{axis: 10})
    sleep.assert_any_call(0.75)


@pytest.mark.parametrize("movement", [{"x": 251}, {"z": 1}, {"x": 5001}])
def test_limits_reject_before_post(setup_stage, movement):
    """Disabled Z and excessive single moves never reach the controller."""
    stage, controller = setup_stage
    reference(stage)
    with pytest.raises(StageSafetyError, match="disabled|envelope|maximum"):
        stage.move_relative(**movement)
    assert not controller.scripts


def test_full_backlash_path_rejected_before_first_leg(setup_stage):
    """Uncommissioned nonzero backlash must not send even an initial overshoot."""
    stage, controller = setup_stage
    reference(stage)
    stage.backlash_steps = {"x": 200, "y": 0, "z": 0}
    with pytest.raises(StageSafetyError, match="Backlash path"):
        stage.move_relative(
            x=-100, backlash_compensation=BacklashCompensation.MOVEMENT_AXES
        )
    assert not controller.scripts


@pytest.mark.parametrize(
    "fault",
    [
        "pid",
        "unhome",
        "disarm",
        "offset",
        "external_move",
        "external_return",
        "busy",
        "missing",
    ],
)
def test_reference_invalidated_on_controller_change(setup_stage, fault):
    """Restart, loss of readiness, external writes and incomplete replies stop motion."""
    stage, controller = setup_stage
    reference(stage)
    if fault == "pid":
        controller.pid += 1
    elif fault == "unhome":
        controller.status["toolhead"]["homed_axes"] = ""
    elif fault == "disarm":
        controller.status["stepper_enable"]["steppers"]["stepper_x"] = False
    elif fault == "offset":
        controller.status["gcode_move"]["homing_origin"][0] = 1.0
    elif fault == "external_move":
        controller.status["gcode_move"]["gcode_position"][0] = 1.0
    elif fault == "external_return":
        controller.status["toolhead"]["print_time"] += 1
    elif fault == "busy":
        controller.status["motion_report"]["live_velocity"] = 0.1
    else:
        del controller.status["gcode_move"]["gcode_position"]
    with pytest.raises(StageSafetyError):
        stage.move_relative(x=100)
    assert stage._reference is None
    assert not controller.scripts


def test_ambiguous_timeout_is_not_retried(setup_stage):
    """A lost acknowledgement poisons the reference, including for future requests."""
    stage, controller = setup_stage
    reference(stage)
    controller.timeout = True
    for _ in range(2):
        with pytest.raises(StageSafetyError, match="uncertain"):
            stage.move_relative(x=100)
    assert len(controller.scripts) == 1
    assert stage._reference is None
    assert stage.moving  # Unknown outcome must not be advertised as a confirmed stop.


def test_incorrect_final_position_invalidates(setup_stage):
    """HTTP success alone is insufficient evidence of a completed move."""
    stage, controller = setup_stage
    reference(stage)
    controller.wrong_target = True
    with pytest.raises(StageSafetyError, match="uncertain"):
        stage.move_relative(x=100)
    assert stage._reference is None


@pytest.mark.parametrize(
    ("action", "kwargs"),
    [
        ("move_relative", {"x": 10}),
        ("move_absolute", {"x": 10}),
        ("move_manual", {"x": 10}),
        ("move_to_xyz_position", {"xyz_pos": (10, 0, 0)}),
    ],
)
def test_reference_has_no_time_limit(setup_stage, mocker, action, kwargs):
    """Idle time and long-running manual/calibration sessions do not expire zero."""
    stage, controller = setup_stage
    clock = mocker.patch("openflexure_microscope_server.things.stage.moonraker.time")
    clock.monotonic.return_value = 1000.0
    allow_operator(stage)
    reference(stage)
    original_reference = stage._reference
    clock.monotonic.return_value += 24 * 60 * 60
    assert stage.controller_state["reference_valid"]
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    getattr(stage, action)(**kwargs)
    clock.monotonic.return_value += 7 * 24 * 60 * 60
    assert stage.position["x"] == 10
    assert stage.controller_state["reference_valid"]
    assert stage._reference is original_reference
    assert len(controller.scripts) == 1


def test_explicit_zero_replaces_reference_without_motion(setup_stage):
    """The operator can choose a new origin; reading state never re-zeros it."""
    stage, controller = setup_stage
    reference(stage)
    original_reference = stage._reference
    stage.move_relative(x=100)
    assert stage.position["x"] == 100
    reference(stage)
    assert stage._reference is not original_reference
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    assert len(controller.scripts) == 1
    assert controller.status["gcode_move"]["gcode_position"][0] == 0.1
    stage.move_relative(x=-50)
    assert stage.position["x"] == -50
    assert controller.status["gcode_move"]["gcode_position"][0] == 0.05


@pytest.mark.parametrize("close_connection", [False, True])
def test_release_or_connection_close_does_not_restore_zero(
    setup_stage, close_connection
):
    """Release and shutdown discard zero; a reconnect must not restore it."""
    stage, controller = setup_stage
    reference(stage)
    if close_connection:
        stage.__exit__(None, None, None)
        stage._client = httpx.Client(
            base_url="http://controller",
            transport=httpx.MockTransport(controller.handle),
        )
    else:
        stage.release_control()
    assert not stage.controller_state["reference_valid"]
    with pytest.raises(StageSafetyError):
        stage.move_relative(x=10)
    assert not controller.scripts


def test_no_continuous_jog(setup_stage):
    """Unsupported jog-stop does not transmit G-code even with a valid zero."""
    stage, controller = setup_stage
    reference(stage)
    for stop in [False, True]:
        with pytest.raises(StageSafetyError, match="unsupported"):
            stage.jog(stop=stop, x=10)
    assert not controller.scripts


def test_negative_direction_and_controller_limits(setup_stage):
    """A configured sign maps both commands/readback; controller bounds also apply."""
    stage, controller = setup_stage
    hardware = stage.hardware_settings
    hardware["axes"]["x"]["direction_sign"] = -1
    stage._hardware = MotionSettings.model_validate(hardware)
    reference(stage)
    stage.move_relative(x=100)
    assert stage.position["x"] == 100
    assert "X-0.100000" in controller.scripts[0]
    controller.status["toolhead"]["axis_minimum"][0] = -0.11
    with pytest.raises(StageSafetyError, match="controller limits"):
        stage.move_relative(x=100)
    assert len(controller.scripts) == 1


def test_local_boundary_and_disabled_z_noop(setup_stage):
    """Check the relative-to-zero envelope even when the controller range is larger."""
    stage, controller = setup_stage
    reference(stage)
    controller.status["gcode_move"]["gcode_position"][0] = 4.95
    controller.status["toolhead"]["position"][0] = 4.95
    stage._reference.expected = stage._fetch_state()
    with pytest.raises(StageSafetyError, match="local envelope"):
        stage.move_relative(x=100)
    stage.move_relative(z=0)
    assert not controller.scripts


def test_offline_metadata_does_not_break_camera(setup_stage):
    """A controller outage must be recorded without preventing independent capture."""
    stage, controller = setup_stage
    del controller.status["motion_report"]
    metadata = stage.thing_state
    assert metadata["reference_valid"] is False
    assert metadata["position"] is None
    assert not controller.scripts


@pytest.mark.parametrize("axis", [0, 1])
@pytest.mark.parametrize("sign", [-1, 1])
def test_segmented_mapping_return_preserves_single_command_limit(
    setup_stage, axis, sign
):
    """A 1.061 mm transit is split, without increasing the manual or physical limit."""
    stage, controller = setup_stage
    reference(stage)
    target = [0, 0, 0]
    target[axis] = sign * 1061
    stage.move_absolute_in_segments(tuple(target))
    assert stage.position["xy"[axis]] == sign * 1061
    assert len(controller.scripts) == 5  # Default fake profile: 0.25 mm/command.
    values = [float(re.search(r"G1 [XY](-?[0-9.]+)", s)[1]) for s in controller.scripts]
    assert all(
        abs(b - a) <= 0.25 for a, b in zip([0, *values[:-1]], values, strict=True)
    )
    stage.move_absolute_in_segments((0, 0, 0))
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    with pytest.raises(StageSafetyError, match="requested.*limit"):
        stage.move_relative(x=1061)


@pytest.mark.parametrize("target", [(5001, 0, 0), (0, 0, 1)])
def test_segmented_mapping_checks_entire_path_before_first_command(setup_stage, target):
    """Segmentation must not partially execute an invalid destination or disabled Z."""
    stage, controller = setup_stage
    reference(stage)
    with pytest.raises(StageSafetyError):
        stage.move_absolute_in_segments(target)
    assert not controller.scripts


def test_segmented_mapping_obeys_controller_limit_before_first_command(setup_stage):
    """A reachable local coordinate may still be outside Klipper's physical bounds."""
    stage, controller = setup_stage
    reference(stage)
    controller.status["toolhead"]["axis_maximum"][1] = 0.5
    with pytest.raises(StageSafetyError, match="controller limits"):
        stage.move_absolute_in_segments((0, 1061, 0))
    assert not controller.scripts


def test_segmented_mapping_timeout_does_not_send_later_segments(setup_stage):
    """Do not retry or continue a partially executed trajectory after ambiguous I/O."""
    stage, controller = setup_stage
    reference(stage)
    controller.timeout = True
    with pytest.raises(StageSafetyError, match="uncertain"):
        stage.move_absolute_in_segments((0, 1061, 0))
    assert len(controller.scripts) == 1
    assert not stage.controller_state["reference_valid"]


def test_segmented_mapping_cancellation_stops_between_commands(setup_stage, mocker):
    """Cancellation does not enqueue the remainder or lose a confirmed position."""
    import labthings_fastapi as lt

    stage, controller = setup_stage
    reference(stage)
    sleep = mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep",
        side_effect=[None, lt.exceptions.InvocationCancelledError()],
    )
    with pytest.raises(lt.exceptions.InvocationCancelledError):
        stage.move_absolute_in_segments((0, 1061, 0))
    assert len(controller.scripts) == 1
    assert stage.controller_state["reference_valid"]
    sleep.side_effect = None
    mocker.patch("openflexure_microscope_server.things.stage.moonraker.time.sleep")
    stage.move_absolute_in_segments((0, 0, 0), block_cancellation=True)
    assert stage.position["y"] == 0


@pytest.mark.parametrize(("axis", "sign"), [(0, 1), (0, -1), (1, 1), (1, -1)])
@pytest.mark.parametrize("scale", [0.4, 4.0])
def test_native_mapping_with_checked_images_and_real_stage_adapter(
    setup_stage, axis, sign, scale
):
    """Run the unmodified calibration/FFT tracker against translated texture and fake HTTP."""
    import numpy as np
    from scipy.ndimage import shift

    from camera_stage_mapping.camera_stage_calibration_1d import calibrate_backlash_1d

    from openflexure_microscope_server.fast_ofm_contracts import MappingMotionChecks
    from openflexure_microscope_server.things.stage.camera_stage_mapping import (
        RecordedMove,
    )
    from openflexure_microscope_server.things.stage.mapping_motion import (
        CheckedTracker,
        MappingMotionCheck,
    )

    stage, controller = setup_stage
    reference(stage)
    texture = np.random.default_rng(42).normal(size=(384, 384))
    frames = []

    def capture():
        position = stage.get_xyz_position()
        frames.append(position)
        translated = shift(
            texture, (-position[1] * scale, -position[0] * scale), order=1
        )
        return translated[128:256, 128:256]

    check = MappingMotionCheck((0, 0, 0), axis, sign, 1000.0, MappingMotionChecks())
    tracker = CheckedTracker(
        capture, stage.get_xyz_position, settle=lambda: None, check=check
    )
    move = RecordedMove(stage, check)
    direction = np.zeros(3)
    direction[axis] = sign
    result = calibrate_backlash_1d(tracker, move, direction)
    assert abs(result["pixels_per_step"]) == pytest.approx(scale, rel=0.1)
    assert check.result()["pixels_per_mm"] == pytest.approx(scale * 1000, rel=0.15)
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    assert stage.controller_state["reference_valid"]
    assert len(frames) > 10
    assert all("M400" in script for script in controller.scripts)


def mapper_for_stage(stage):
    """Connect a mapper Thing to the fake-controller stage and translated texture."""
    import numpy as np
    from scipy.ndimage import shift

    from labthings_fastapi.testing import (
        manually_connect_thing_slot,
        mock_thing_instance,
    )

    from openflexure_microscope_server.things.camera import BaseCamera
    from openflexure_microscope_server.things.stage.camera_stage_mapping import (
        CameraStageMapper,
    )

    mapper = create_thing_without_server(CameraStageMapper)
    camera = mock_thing_instance(BaseCamera)
    camera.downsampled_array_factor = 2
    texture = np.random.default_rng(42).normal(size=(384, 384))

    def capture():
        position = stage.get_xyz_position()
        return shift(texture, (-position[1] * 0.4, -position[0] * 0.4), order=1)[
            128:256, 128:256
        ]

    camera.capture_downsampled_array.side_effect = capture
    manually_connect_thing_slot(mapper, "_stage", stage)
    manually_connect_thing_slot(mapper, "_cam", camera)
    return mapper, camera


def test_mapping_thing_saves_both_axes_and_checked_scales(setup_stage):
    """The same calibrate_xy action stores the native result plus scale evidence."""
    stage, _ = setup_stage
    reference(stage)
    mapper, _camera = mapper_for_stage(stage)
    result = mapper.calibrate_xy()
    assert mapper.last_calibration == result
    for axis in ("x", "y"):
        assert result["linear_calibration_" + axis]["motion_check"][
            "pixels_per_mm"
        ] == pytest.approx(400, rel=0.15)
    assert stage.position == {"x": 0, "y": 0, "z": 0}


@pytest.mark.parametrize("return_fails", [False, True])
def test_mapping_fit_failure_keeps_diagnosis_and_previous_calibration(
    setup_stage, mocker, return_fails
):
    """A >1 mm finally-return cannot hide the fit error or retry uncertain motion."""
    import numpy as np

    from camera_stage_mapping.exceptions import MappingError

    stage, controller = setup_stage
    reference(stage)
    mapper, _camera = mapper_for_stage(stage)
    mapper.last_calibration = {"previous": "keep"}

    def failed_fit(_tracker, move, _direction, **_kwargs):
        move.check.scale_vector = np.array([0.1, 0])
        move.check.frame_pixels = 700
        move(np.array([0, 1061, 0]))
        try:
            raise MappingError("fit residuals too large")
        finally:
            controller.timeout = return_fails
            move(np.zeros(3))

    mocker.patch(
        "openflexure_microscope_server.things.stage.camera_stage_mapping.calibrate_backlash_1d",
        side_effect=failed_fit,
    )
    with pytest.raises(
        (MappingError, StageSafetyError), match="fit residuals too large"
    ):
        mapper.calibrate_xy()
    assert mapper.last_calibration == {"previous": "keep"}
    if return_fails:
        assert len(controller.scripts) == 6
        assert not stage.controller_state["reference_valid"]
    else:
        assert len(controller.scripts) == 10
        assert stage.position["y"] == 0


def test_mapping_cancel_returns_in_bounded_segments_without_saving(setup_stage, mocker):
    """Cancel returns by checked commands and preserves the previous calibration."""
    import numpy as np

    import labthings_fastapi as lt

    stage, controller = setup_stage
    reference(stage)
    mapper, _camera = mapper_for_stage(stage)
    mapper.last_calibration = {"previous": "keep"}
    mocker.patch("openflexure_microscope_server.things.stage.moonraker.time.sleep")

    def cancel(_tracker, move, _direction, **_kwargs):
        move.check.scale_vector = np.array([0.1, 0])
        move.check.frame_pixels = 700
        move(np.array([0, 1061, 0]))
        raise lt.exceptions.InvocationCancelledError

    mocker.patch(
        "openflexure_microscope_server.things.stage.camera_stage_mapping.calibrate_backlash_1d",
        side_effect=cancel,
    )
    with pytest.raises(lt.exceptions.InvocationCancelledError):
        mapper.calibrate_xy()
    assert mapper.last_calibration == {"previous": "keep"}
    assert len(controller.scripts) == 10
    assert stage.position["y"] == 0


def test_light_queue_change_preserves_existing_zero(setup_stage):
    """A confirmed light-only command advances the expected queue, not the origin."""
    stage, controller = setup_stage
    reference(stage)
    original = stage._reference
    original_revision = stage.controller_state["motion_revision"]
    with stage._light_operation(stage._url):
        controller.status["toolhead"]["print_time"] += 1
    assert stage._reference is original
    assert stage.controller_state["reference_valid"] is True
    assert stage.controller_state["motion_revision"] == original_revision
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    assert not controller.scripts


def test_verified_light_scope_reuses_owned_reference_snapshot(setup_stage, mocker):
    """A post-exposure OFF skips only the duplicate pre-read."""
    stage, controller = setup_stage
    reference(stage)
    verified = stage._expected_reference_state()
    assert verified is not None
    assert verified is not stage._reference.expected
    read = mocker.spy(stage, "_fetch_state")
    with stage._light_operation(stage._url, verified):
        controller.status["toolhead"]["print_time"] += 1
    assert read.call_count == 1
    assert read.call_args.kwargs == {"refresh": True}
    assert stage.controller_state["reference_valid"] is True


def test_combined_light_check_failure_invalidates_reference(setup_stage):
    """A failed merged light check remains inside the fail-closed stage scope."""
    stage, controller = setup_stage
    reference(stage)
    verified = stage._expected_reference_state()
    assert verified is not None

    def reject(result):
        assert result["status"] == controller.status
        raise RuntimeError("wrong light")

    with (
        pytest.raises(RuntimeError, match="wrong light"),
        stage._light_operation(
            stage._url,
            verified,
            extra_objects=("output_pin led_white_d0",),
            after_check=reject,
        ),
    ):
        controller.status["toolhead"]["print_time"] += 1
    assert stage._reference is None


def test_light_does_not_create_or_repair_zero(setup_stage):
    """OFF/ON remains usable before arm/zero without automatically arming anything."""
    stage, controller = setup_stage
    with stage._light_operation(stage._url):
        controller.status["toolhead"]["print_time"] += 1
    assert stage._reference is None
    reference(stage)
    controller.status["toolhead"]["print_time"] += 1  # external activity BEFORE light
    with stage._light_operation(stage._url):
        controller.status["toolhead"]["print_time"] += 1
    assert stage._reference is None
    assert not controller.scripts


@pytest.mark.parametrize("changed", ["position", "origin", "motors", "pid", "busy"])
def test_light_scope_rejects_controller_changes(setup_stage, changed):
    """The narrow queue allowance must not waive position/readiness checks."""
    stage, controller = setup_stage
    reference(stage)

    def change_during_light():
        with stage._light_operation(stage._url):
            controller.status["toolhead"]["print_time"] += 1
            if changed == "position":
                controller.status["gcode_move"]["gcode_position"][0] = 0.001
            elif changed == "origin":
                controller.status["gcode_move"]["homing_origin"][0] = 1.0
            elif changed == "motors":
                controller.status["stepper_enable"]["steppers"]["stepper_x"] = False
            elif changed == "pid":
                controller.pid += 1
            else:
                controller.status["motion_report"]["live_velocity"] = 0.1

    with pytest.raises(StageSafetyError, match="changed during light"):
        change_during_light()
    assert stage._reference is None


def test_failed_light_cannot_preserve_trust(setup_stage):
    """Ambiguous light transport or cleanup must not restore reference."""
    stage, _controller = setup_stage
    reference(stage)
    with (
        pytest.raises(RuntimeError, match="lost acknowledgement"),
        stage._light_operation(stage._url),
    ):
        raise RuntimeError("lost acknowledgement")
    assert stage._reference is None


def test_light_rejects_different_controller(setup_stage):
    """Do not attribute another controller's activity to this stage."""
    stage, _controller = setup_stage
    with (
        pytest.raises(StageSafetyError, match="URLs must match"),
        stage._light_operation("http://elsewhere"),
    ):
        pytest.fail("Scope must not be entered")


def physical_focus_stage(stage, **overrides):
    """Configure only the fake Z axis to match the deployed physical scale."""
    profile = stage.hardware_settings
    profile["axes"]["z"].update(
        enabled=True,
        min_mm=-0.5,
        max_mm=0.5,
        max_move_mm=0.05,
        ui_step_mm=0.005,
        settle_ms=1000,
        units_per_mm=1000,
        **overrides,
    )
    stage._hardware = MotionSettings.model_validate(profile)
    reference(stage)


def test_transit_helpers_keep_manual_limits_and_backlash_gate(setup_stage):
    """Permit long transits without permitting an oversized manual move."""
    from openflexure_microscope_server.things.stage.moonraker import (
        move_absolute_transit,
    )

    stage, controller = setup_stage
    reference(stage)
    move_absolute_transit(stage, x=1160, y=-1156)
    assert stage.position == {"x": 1160, "y": -1156, "z": 0}
    move_absolute_transit(stage, x=0, y=0)
    assert stage.position == {"x": 0, "y": 0, "z": 0}
    count = len(controller.scripts)
    with pytest.raises(StageSafetyError, match="maximum single move"):
        stage.move_relative(x=1160)
    with pytest.raises(StageSafetyError, match="Only X"):
        move_absolute_transit(stage, bad_axis=1)
    stage.backlash_steps = {"x": 1, "y": 0, "z": 0}
    with pytest.raises(StageSafetyError, match="Backlash"):
        move_absolute_transit(stage, x=1)
    assert len(controller.scripts) == count


def test_scan_preload_combines_xy_with_outward_z_and_consumes_receipt(setup_stage):
    """Two commands cover XY+outward Z and the final approach, with two reads each."""
    stage, controller = setup_stage
    physical_focus_stage(stage)
    controller.object_queries.clear()

    prepared = stage.move_to_scan_preload((100, -80, 20), 24, 1)

    assert prepared.target_xyz == (100, -80, 20)
    assert prepared.preload_xyz == (100, -80, -4)
    assert len(controller.scripts) == 1
    assert "X0.100000 Y-0.080000 Z-0.004000" in controller.scripts[0]
    # One fresh pre-command and one mandatory post-command readback.
    assert len(controller.object_queries) == 2
    assert stage.position == {"x": 100, "y": -80, "z": -4}
    assert len(controller.object_queries) == 3

    controller.object_queries.clear()
    completed = stage.complete_scan_preload(prepared)

    assert completed.target_xyz == (100, -80, 20)
    assert len(controller.scripts) == 2
    assert "Z0.020000" in controller.scripts[1]
    assert len(controller.object_queries) == 2
    assert stage.position == {"x": 100, "y": -80, "z": 20}
    assert len(controller.object_queries) == 3

    count = len(controller.scripts)
    with pytest.raises(StageSafetyError, match="no longer matches"):
        stage.complete_scan_preload(prepared)
    assert len(controller.scripts) == count


def test_scan_preload_preflights_final_approach_before_xy_motion(setup_stage):
    """An unsafe final focus target is rejected before the combined transit starts."""
    stage, controller = setup_stage
    physical_focus_stage(stage)
    controller.scripts.clear()

    with pytest.raises(StageSafetyError, match="envelope"):
        stage.move_to_scan_preload((1000, -800, 600), 24, 1)

    assert controller.scripts == []


def test_atomic_scan_preload_keeps_two_stops_and_one_final_readback(setup_stage):
    """One script preserves both M400 stops and yields a single-use final receipt."""
    stage, controller = setup_stage
    physical_focus_stage(stage)
    controller.object_queries.clear()

    completed = stage.move_to_scan_target_with_preload((100, -80, 20), 24, 1)

    assert completed.target_xyz == (100, -80, 20)
    assert len(controller.scripts) == 1
    lines = controller.scripts[0].splitlines()
    preload_line = next(i for i, line in enumerate(lines) if "Z-0.004000" in line)
    final_line = next(i for i, line in enumerate(lines) if "Z0.020000" in line)
    assert preload_line < lines.index("M400", preload_line) < final_line
    assert lines.index("M400", final_line) > final_line
    assert len(controller.object_queries) == 2
    assert stage.position == {"x": 100, "y": -80, "z": 20}
    assert stage.consume_completed_scan_preload(completed) is completed
    with pytest.raises(StageSafetyError, match="no longer matches"):
        stage.consume_completed_scan_preload(completed)


def test_atomic_scan_preload_accepts_exact_post_command_push(setup_stage):
    """The scan hot path keeps its fresh preflight but avoids a duplicate query."""
    stage, controller = setup_stage
    physical_focus_stage(stage)
    cache = StatusCache()
    generation = cache.begin()
    cache.initialise(
        generation,
        controller.pid,
        {"eventtime": controller.eventtime, "status": controller.status},
    )
    stage._status_stream = SimpleNamespace(cache=cache)
    stage._status_generation = generation

    stage._client.close()

    def handle_and_publish(request):
        response = controller.handle(request)
        if request.url.path == "/printer/gcode/script":
            controller.eventtime += 0.01
            cache.update(
                generation,
                {"eventtime": controller.eventtime, "status": controller.status},
            )
        return response

    stage._client = httpx.Client(
        base_url="http://controller", transport=httpx.MockTransport(handle_and_publish)
    )
    controller.object_queries.clear()

    completed = stage.move_to_scan_target_with_preload((100, -80, 20), 24, 1)

    assert completed.target_xyz == (100, -80, 20)
    assert len(controller.object_queries) == 1
    assert stage.last_motion_readback_source == "push"
    assert stage.position == {"x": 100, "y": -80, "z": 20}


def test_path_validation_rejects_late_invalid_target_before_any_motion(setup_stage):
    """Check all endpoints, not just the first reachable point."""
    stage, controller = setup_stage
    reference(stage)
    with pytest.raises(StageSafetyError, match="envelope"):
        stage.validate_path([{"x": 100}, {"x": 6000}])
    assert not controller.scripts
    stage.validate_path([{"x": 1160, "y": -1156}, {"x": 0, "y": 0}])
    assert not controller.scripts


def test_relative_path_uses_one_start_and_signed_axis_scale(setup_stage):
    """Relative envelope endpoints are not accumulated as successive moves."""
    stage, controller = setup_stage
    profile = stage.hardware_settings
    profile["axes"]["x"].update(direction_sign=-1, units_per_mm=2000)
    stage._hardware = MotionSettings.model_validate(profile)
    reference(stage)
    stage.validate_path([{"x": 9000}, {"x": -9000}], relative_to_start=True)
    with pytest.raises(StageSafetyError, match="envelope"):
        stage.validate_path([{"x": 10001}], relative_to_start=True)
    assert not controller.scripts


@pytest.mark.parametrize("units", [1000, 2000, 10000])
def test_physical_autofocus_defaults_and_limits(setup_stage, units):
    """Resolve 50 micrometres for every logical scale, not 2000 motor steps."""
    from openflexure_microscope_server.things.focus.autofocus import resolve_focus_range

    stage, controller = setup_stage
    physical_focus_stage(stage)
    profile = stage.hardware_settings
    profile["axes"]["z"]["units_per_mm"] = units
    stage._hardware = MotionSettings.model_validate(profile)
    assert resolve_focus_range(stage, None) == units * 0.05
    assert resolve_focus_range(stage, None, native_default=800) == units * 0.05
    with pytest.raises(ValueError, match="single-move"):
        resolve_focus_range(stage, int(units * 0.051))
    with pytest.raises(ValueError, match="positive"):
        resolve_focus_range(stage, 0)
    assert not controller.scripts


def test_autofocus_checks_full_sweep_before_first_move(setup_stage):
    """A legal single step must not permit a sweep outside the local envelope."""
    from openflexure_microscope_server.things.focus.autofocus import AutofocusThing

    stage, controller = setup_stage
    physical_focus_stage(stage)
    profile = stage.hardware_settings
    profile["axes"]["z"]["min_mm"] = -0.02
    stage._hardware = MotionSettings.model_validate(profile)
    af = create_thing_without_server(AutofocusThing, mock_all_slots=True)
    AutofocusThing._stage.connect(af, {"stage": stage})
    with pytest.raises(StageSafetyError, match="envelope"):
        af.fast_autofocus(dz=50)
    assert not controller.scripts


def test_smart_stack_failure_returns_in_segments(setup_stage, mocker):
    """Reproduce the failed 5 um/9-image stack without a 120 um reset command."""
    from openflexure_microscope_server.things.focus.autofocus import (
        AutofocusThing,
        CaptureInfo,
        SmartStackParams,
    )

    stage, controller = setup_stage
    physical_focus_stage(stage)
    af = create_thing_without_server(AutofocusThing, mock_all_slots=True)
    AutofocusThing._stage.connect(af, {"stage": stage})
    counter = 0

    def capture(**_kwargs):
        nonlocal counter
        counter += 1
        return CaptureInfo(
            buffer_id=counter, position=stage.position, sharpness=counter
        )

    mocker.patch.object(af, "capture_stack_image", side_effect=capture)
    mocker.patch("openflexure_microscope_server.things.focus.autofocus.time.sleep")
    params = SmartStackParams(stack_dz=5, min_images_to_test=9, images_to_save=1)
    success, captures, _ = af.smart_z_stack(params, True)
    assert not success
    assert len(captures) == 24
    assert stage.position["z"] == captures[-1].position["z"] == 70
    mocker.patch.object(af, "looping_autofocus")
    af.reset_stack(captures[0].position["z"], 50)
    assert stage.position["z"] == -45
    values = [
        float(re.search(r"G1 Z(-?[0-9.]+)", script)[1]) for script in controller.scripts
    ]
    assert all(
        abs(b - a) <= 0.050001 for a, b in zip([0, *values[:-1]], values, strict=True)
    )


def test_stack_checks_worst_case_before_capture(setup_stage, mocker):
    """Do not partially run a stack whose extra search frames exceed the envelope."""
    from openflexure_microscope_server.things.focus.autofocus import (
        AutofocusThing,
        SmartStackParams,
    )

    stage, controller = setup_stage
    physical_focus_stage(stage)
    profile = stage.hardware_settings
    profile["axes"]["z"]["max_mm"] = 0.06
    stage._hardware = MotionSettings.model_validate(profile)
    af = create_thing_without_server(AutofocusThing, mock_all_slots=True)
    AutofocusThing._stage.connect(af, {"stage": stage})
    capture = mocker.patch.object(af, "capture_stack_image")
    with pytest.raises(StageSafetyError, match="envelope"):
        af.smart_z_stack(
            SmartStackParams(stack_dz=5, min_images_to_test=9, images_to_save=1), True
        )
    assert not controller.scripts
    capture.assert_not_called()


def test_sharpness_timestamps_exclude_readback_and_settle(setup_stage, mocker):
    """Map a mid-move frame to mid-Z, excluding the following one-second settle."""
    from openflexure_microscope_server.things.focus.autofocus import (
        JPEGSharpnessMonitor,
    )

    stage, controller = setup_stage
    physical_focus_stage(stage)
    now = [100.0]
    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.time.time",
        side_effect=lambda: now[0],
    )

    def advance(seconds):
        now[0] += seconds

    original_post = stage._client.post

    def post(*args, **kwargs):
        advance(2.5)
        return original_post(*args, **kwargs)

    mocker.patch.object(stage._client, "post", side_effect=post)
    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep",
        side_effect=advance,
    )
    monitor = JPEGSharpnessMonitor(stage, mocker.Mock())
    monitor.focus_rel(50)
    assert now[0] == 103.5
    assert monitor.stage_times == [100.0, 102.5]
    monitor._jpeg_times = [100.01, 101.25, 102.5, 103.49]
    monitor._jpeg_sizes = [1, 100, 10, 10]
    times, heights, _ = monitor.move_data(0)
    assert times[-1] <= 102.5
    assert heights[1] == pytest.approx(25)


@pytest.mark.parametrize("moves", [[10, 51], [40] * 13])
def test_measurement_series_checks_all_moves_before_start(setup_stage, mocker, moves):
    """Reject a late oversized leg or cumulative travel before any measurement."""
    from openflexure_microscope_server.things.focus.autofocus import AutofocusThing

    stage, controller = setup_stage
    physical_focus_stage(stage)
    af = create_thing_without_server(AutofocusThing, mock_all_slots=True)
    AutofocusThing._stage.connect(af, {"stage": stage})
    monitor = mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus.JPEGSharpnessMonitor"
    )
    with pytest.raises((StageSafetyError, ValueError)):
        af.z_move_and_measure_sharpness(dz=moves)
    assert not controller.scripts
    monitor.assert_not_called()


@pytest.mark.parametrize(
    "settings",
    [
        {"x_count": 3, "y_count": 3, "dx": 3000, "dy": -580},
        {"max_dist": 6000},
    ],
)
def test_scan_preflight_rejects_full_xy_envelope_before_autofocus(
    setup_stage, mocker, settings
):
    """Reject unreachable far grid corners and Fast OFM radii before the scan starts."""
    from openflexure_microscope_server.things.scanning.smart_scan import SmartScanThing

    stage, controller = setup_stage
    reference(stage)
    scan = create_thing_without_server(
        SmartScanThing, default_workflow="mock-_all_workflows", mock_all_slots=True
    )
    SmartScanThing._stage.connect(scan, {"stage": stage})
    scan._ongoing_scan = mocker.Mock(images_dir="images")
    mocker.patch.object(
        scan, "create_data_path", return_value="/tmp/unused-scan-images"
    )
    workflow = mocker.Mock()
    captured_settings = mocker.Mock()
    captured_settings.model_dump.return_value = settings
    workflow.all_settings.return_value = (captured_settings, None, (1400, 1400))
    with scan._scan_lock, pytest.raises(StageSafetyError, match="envelope"):
        scan._collect_scan_data(workflow)
    assert scan._scan_data is None
    assert not controller.scripts
    workflow.check_before_start.assert_not_called()


def editable_limits(stage):
    """Enable operator settings in the fake profile without sending a motor command."""
    stage._hardware = stage._hardware.model_copy(
        update={"allow_operator_controls": True}
    )
    return stage.motion_limits.model_dump()


def editable_dynamics(stage):
    """Enable operator settings and return the complete effective timing profile."""
    stage._hardware = stage._hardware.model_copy(
        update={"allow_operator_controls": True}
    )
    return stage.motion_dynamics.model_dump()


def test_dynamics_save_without_motion_preserves_reference_and_applies_to_moves(
    setup_stage, mocker
):
    """One persisted profile drives G-code and settle without moving on Apply."""
    stage, controller = setup_stage
    values = editable_dynamics(stage)
    reference(stage)
    old_reference = stage._reference
    values["x"].update(speed_mm_s=1.0, accel_mm_s2=10.0, settle_ms=100.0)
    reads_before = len(controller.object_queries)
    saved = stage.set_motion_dynamics(values)
    assert saved.x.speed_mm_s == 1.0
    assert saved.x.accel_mm_s2 == 10.0
    assert stage.hardware_settings["axes"]["x"]["settle_ms"] == 100.0
    assert stage._reference is old_reference
    assert len(controller.object_queries) == reads_before + 1
    assert not controller.scripts

    sleep = mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    stage.move_relative(x=100)
    assert "VELOCITY=1.000000 ACCEL=10.000000" in controller.scripts[-1]
    sleep.assert_any_call(0.1)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"speed_mm_s": 21.0}, "speed exceeds"),
        ({"accel_mm_s2": 51.0}, "acceleration exceeds"),
        ({"move_timeout_s": 3.1}, "timeout is too short"),
    ],
)
def test_dynamics_reject_controller_or_timeout_violations_atomically(
    setup_stage, update, message
):
    """Invalid timing never persists partially and never sends G-code."""
    stage, controller = setup_stage
    values = editable_dynamics(stage)
    values["x"].update(update)
    with pytest.raises(StageSafetyError, match=message):
        stage.set_motion_dynamics(values)
    assert stage.saved_motion_dynamics is None
    assert not controller.scripts


def test_dynamics_update_requires_idle_operator_profile(setup_stage):
    """Deployment permission and idle readback gate timing changes."""
    stage, controller = setup_stage
    values = stage.motion_dynamics.model_dump()
    with pytest.raises(StageSafetyError, match="Operator controls"):
        stage.set_motion_dynamics(values)
    editable_dynamics(stage)
    controller.status["motion_report"]["live_velocity"] = 0.1
    with pytest.raises(StageSafetyError, match="Stop motion"):
        stage.set_motion_dynamics(values)
    assert stage.saved_motion_dynamics is None
    assert not controller.scripts


def test_operator_dynamics_persist_without_restoring_zero(setup_stage, tmp_path):
    """Standard settings lifecycle restores timing but never a local reference."""
    stage, controller = setup_stage
    hardware = {"allow_motion": True, "allow_operator_controls": True}
    first = create_thing_without_server(
        MoonrakerStage, settings_folder=str(tmp_path), hardware=hardware
    )
    first.load_settings()
    first._client = stage._client
    values = first.motion_dynamics.model_dump()
    values["x"].update(speed_mm_s=1.0, accel_mm_s2=10.0, settle_ms=100.0)
    first.set_motion_dynamics(values)
    restarted = create_thing_without_server(
        MoonrakerStage, settings_folder=str(tmp_path), hardware=hardware
    )
    restarted.load_settings()
    assert restarted.motion_dynamics.model_dump() == values
    assert restarted.hardware_settings["axes"]["x"]["speed_mm_s"] == 1.0
    assert restarted._reference is None
    assert not controller.scripts


def test_limits_default_to_deployment_without_persisting_or_moving(setup_stage):
    """Loading settings is not an implicit write or an unbounded profile."""
    stage, controller = setup_stage
    limits = stage.motion_limits
    assert limits.x.travel_limit_enabled
    assert limits.x.single_move_limit_enabled
    assert (limits.x.min_mm, limits.x.max_mm, limits.x.max_move_mm) == (-5, 5, 0.25)
    assert stage.saved_motion_limits is None
    assert not controller.scripts


def test_limits_save_without_motion_preserves_reference(setup_stage):
    """All clients see one persisted setting; coordinate reference is not changed."""
    stage, controller = setup_stage
    values = editable_limits(stage)
    reference(stage)
    old_reference = stage._reference
    values["x"]["max_move_mm"] = 0.3
    saved = stage.set_motion_limits(values)
    assert saved.x.max_move_mm == 0.3
    assert stage.hardware_settings["axes"]["x"]["max_move_mm"] == 0.3
    assert stage._reference is old_reference
    assert not controller.scripts


def test_travel_and_single_limits_are_independent(setup_stage):
    """Disabling one software guard does not silently disable the other one."""
    stage, controller = setup_stage
    values = editable_limits(stage)
    reference(stage)
    values["x"].update(min_mm=-0.2, max_mm=0.2)
    stage.set_motion_limits(values)
    with pytest.raises(StageSafetyError, match="envelope"):
        stage.move_relative(x=250)
    values["x"]["travel_limit_enabled"] = False
    with pytest.raises(StageSafetyError, match="Confirm disabling"):
        stage.set_motion_limits(values)
    stage.set_motion_limits(values, confirm_disable=True)
    stage.move_relative(x=250)
    with pytest.raises(StageSafetyError, match="maximum single move"):
        stage.move_relative(x=300)
    values["x"]["single_move_limit_enabled"] = False
    stage.set_motion_limits(values, confirm_disable=True)
    stage.move_relative(x=300)
    assert stage.position["x"] == 550
    assert len(controller.scripts) == 2
    # Re-enabling travel around an excluded current position is rejected, not a move.
    values["x"]["travel_limit_enabled"] = True
    with pytest.raises(StageSafetyError, match="outside the proposed"):
        stage.set_motion_limits(values, confirm_disable=True)
    assert not stage.motion_limits.x.travel_limit_enabled


def test_disabled_software_limits_keep_controller_bounds_and_zero(setup_stage):
    """Off is not a bypass of controller bounds, reference, or axis availability."""
    stage, controller = setup_stage
    values = editable_limits(stage)
    reference(stage)
    values["x"].update(travel_limit_enabled=False, single_move_limit_enabled=False)
    stage.set_motion_limits(values, confirm_disable=True)
    with pytest.raises(StageSafetyError, match="controller limits"):
        stage.move_relative(x=11000)
    with pytest.raises(StageSafetyError, match="disabled"):
        stage.move_relative(z=1)
    stage.release_control()
    with pytest.raises(StageSafetyError, match="released stage control"):
        stage.move_relative(x=1)
    assert not controller.scripts


@pytest.mark.parametrize("change", ["moving", "queue", "not_ready"])
def test_limit_updates_rejected_while_busy_without_partial_save(setup_stage, change):
    """Settings cannot change the rules while a controller move is executing."""
    stage, controller = setup_stage
    values = editable_limits(stage)
    values["x"]["max_move_mm"] = 0.5
    if change == "moving":
        controller.status["motion_report"]["live_velocity"] = 1.0
    elif change == "queue":
        controller.status["toolhead"]["print_time"] = 11.0
    else:
        controller.status["webhooks"]["state"] = "shutdown"
    with pytest.raises(StageSafetyError, match="Stop motion"):
        stage.set_motion_limits(values)
    assert stage.saved_motion_limits is None
    assert not controller.scripts


@pytest.mark.parametrize(
    "update",
    [
        {"min_mm": 1.0},
        {"max_mm": float("inf")},
        {"max_move_mm": 0.0},
        {"single_move_limit_enabled": "false"},
        {"travel_limit_enabled": True, "min_mm": None},
        {"unexpected": True},
    ],
)
def test_invalid_limit_settings_are_atomic(setup_stage, update):
    """Bad fields must never produce a partly applied set of axis limits."""
    from pydantic import ValidationError

    stage, controller = setup_stage
    values = editable_limits(stage)
    values["x"].update(update)
    with pytest.raises(ValidationError):
        stage.set_motion_limits(values, confirm_disable=True)
    assert stage.saved_motion_limits is None
    assert not controller.scripts


def test_limits_cannot_silently_reduce_below_one_unit(setup_stage):
    """A positive but unrepresentable command limit must be rejected."""
    stage, _controller = setup_stage
    values = editable_limits(stage)
    values["x"]["max_move_mm"] = 0.0001
    with pytest.raises(StageSafetyError, match="below one stage unit"):
        stage.set_motion_limits(values)


def test_autofocus_and_transits_respect_disabled_single_limit(setup_stage):
    """The switch applies to AF API and long transits, not just manual arrows."""
    from openflexure_microscope_server.things.focus.autofocus import resolve_focus_range
    from openflexure_microscope_server.things.stage.moonraker import (
        move_absolute_transit,
    )

    stage, controller = setup_stage
    physical_focus_stage(stage)
    values = editable_limits(stage)
    values["z"]["max_move_mm"] = 0.02
    stage.set_motion_limits(values)
    assert resolve_focus_range(stage, None) == 20
    with pytest.raises(ValueError, match="single-move"):
        resolve_focus_range(stage, 100)
    values["z"]["single_move_limit_enabled"] = False
    values["x"]["single_move_limit_enabled"] = False
    stage.set_motion_limits(values, confirm_disable=True)
    assert resolve_focus_range(stage, None) == 50
    assert resolve_focus_range(stage, 100) == 100
    move_absolute_transit(stage, x=1000)
    assert len(controller.scripts) == 1
    assert stage.position["x"] == 1000


def test_operator_limits_persist_on_server_without_restoring_zero(
    setup_stage, tmp_path
):
    """Recreating the stage restores settings, but never an operator reference."""
    stage, controller = setup_stage
    hardware = {"allow_motion": True, "allow_operator_controls": True}
    first = create_thing_without_server(
        MoonrakerStage, settings_folder=str(tmp_path), hardware=hardware
    )
    first.load_settings()  # The server performs this step before exposing a Thing.
    first._client = stage._client
    values = first.motion_limits.model_dump()
    values["x"].update(min_mm=-3.0, max_mm=7.0, single_move_limit_enabled=False)
    first.set_motion_limits(values, confirm_disable=True)
    restarted = create_thing_without_server(
        MoonrakerStage, settings_folder=str(tmp_path), hardware=hardware
    )
    restarted.load_settings()
    assert restarted.motion_limits.model_dump() == values
    assert (
        restarted.hardware_settings["axes"]["x"]["single_move_limit_enabled"] is False
    )
    assert restarted._reference is None
    assert not controller.scripts
