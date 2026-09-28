"""Test the stage without creating a full HTTP server and socket connection."""

import itertools
import logging
import threading
import time
from dataclasses import dataclass

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import labthings_fastapi as lt
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.camera.simulation import SimulatedCamera
from openflexure_microscope_server.things.stage import (
    BacklashCompensation,
    BaseStage,
    JogCommand,
    JogQueue,
    RedefinedBaseMovementError,
)
from openflexure_microscope_server.things.stage.dummy import DummyStage

from ..shared_utils import get_method_name
from ..shared_utils.lt_test_utils import LabThingsTestEnv

# Keep the size and number of moves fairly small or the tests can take forever
point3d = st.tuples(
    st.integers(min_value=-100, max_value=100),
    st.integers(min_value=-100, max_value=100),
    st.integers(min_value=-100, max_value=100),
)

path3d = st.lists(point3d, min_size=5, max_size=10)


@pytest.fixture
def dummy_stage():
    """Return a dummy stage with a very low step time."""
    return create_thing_without_server(DummyStage, step_time=0.0001)


def test_not_implemented_methods():
    """Check all methods a child class should implement raise NotImplemented."""
    stage = create_thing_without_server(BaseStage)
    methods_and_args = [
        (stage._hardware_update_position, ()),
        (stage._hardware_move_relative, ()),
        (stage._hardware_move_absolute, ()),
        (stage._hardware_start_move_relative, ((1, 2, 3),)),
        (stage._hardware_stop, ()),
        (stage._poll_moving, ()),
        (stage._estimate_move_duration, ((1, 2, 3),)),
        (stage.set_zero_position, ()),
    ]
    for method, args in methods_and_args:
        method_name = get_method_name(method)
        msg = f"StageThings must define their own {method_name} method"
        with pytest.raises(NotImplementedError, match=msg):
            method(*args)


def test_override_base_movement():
    """Child classes of stage should implement functions in the hardware reference frame.

    ``move_absolute`` and ``move_relative`` are in the program reference frame and
    convert to the hardware frame. It is recommended to override
    ``_hardware_move_relative`` and ``_hardware_move_absolute`` instead.

    Check that the expected error is raised if the methods are overridden.
    """

    class BadStage1(BaseStage):
        @lt.action
        def move_relative(self, block_cancellation: bool = False, **kwargs: int):
            pass

    with pytest.raises(RedefinedBaseMovementError):
        create_thing_without_server(BadStage1)

    class BadStage2(BaseStage):
        @lt.action
        def move_absolute(self, block_cancellation: bool = False, **kwargs: int):
            pass

    with pytest.raises(RedefinedBaseMovementError):
        create_thing_without_server(BadStage2)


def test_apply_axis_direction_all_pos(dummy_stage):
    """Test the apply axis direction function behaves as expected when axis +v3."""
    # Directly create a stage not through a ThingServer to access private methods
    dummy_stage.axis_inverted = {"x": False, "y": False, "z": False}

    # A list of positions to try
    positions = [
        [1, 2, 3],  # list
        {"x": 1, "y": 2, "z": 3},  # mapping
        {"x": 1, "z": 2, "y": 3},  # mapping out of order
        {"x": 3},  # Mapping with only 1 value
        {"x": 1, "z": 2},  # Mapping with only 2 values
    ]
    for pos in positions:
        assert dummy_stage._apply_axis_direction(pos) == pos

    # Check tuple separately as it gets converted to list
    assert dummy_stage._apply_axis_direction((1, 2, 0)) == [1, 2, 0]


def test_apply_axis_direction_mixed(dummy_stage):
    """Test the apply axis direction function behaves as expected when axis dirs are mixed."""
    # Make x and z negative
    dummy_stage.axis_inverted = {"x": True, "y": False, "z": True}

    # A list of (input position, output position) to try
    position_pairs = [
        ([1, 2, 3], [-1, 2, -3]),  # list
        ([1, "2", "3"], [-1, 2, -3]),  # list with strings
        ((1, 2, 0), [-1, 2, 0]),  # tuple (gets converted to list)
        ({"x": 1, "y": 2, "z": 3}, {"x": -1, "y": 2, "z": -3}),  # mapping
        ({"x": 1, "z": 2, "y": 3}, {"x": -1, "z": -2, "y": 3}),  # mapping out of order
        ({"x": 1, "y": "2", "z": "3"}, {"x": -1, "y": 2, "z": -3}),  # mapping w strings
        ({"x": 3}, {"x": -3}),  # Mapping with only 1 value
        ({"x": 1, "z": 2}, {"x": -1, "z": -2}),  # Mapping with only 2 values
    ]
    for pos, expected_pos in position_pairs:
        assert dummy_stage._apply_axis_direction(pos) == expected_pos


def test_apply_axis_errors(dummy_stage):
    """Test the apply axis direction returns appropriate errors."""
    with pytest.raises(TypeError):
        dummy_stage._apply_axis_direction(None)
    with pytest.raises(TypeError):
        dummy_stage._apply_axis_direction("Onwards!")
    with pytest.raises(KeyError):
        dummy_stage._apply_axis_direction({"x": -1, "y": 2, "z": 4, "up": -3})
    with pytest.raises(KeyError):
        dummy_stage._apply_axis_direction({"x": -1, "y": 2, "up": -3})


def test_default_values(dummy_stage):
    """Check the default values for the dummy stage."""
    # axes are x, y, z (note that going through the thing, client the tuple is
    # converted to a list.
    assert dummy_stage.axis_names == ("x", "y", "z")
    # position starts at 0, 0, 0
    assert dummy_stage.position == {"x": 0, "y": 0, "z": 0}
    # axis direction starts is -1, 1, 1 for the dummy stage
    assert dummy_stage.axis_inverted == {"x": True, "y": False, "z": False}
    # And check the thing state
    assert dummy_stage.thing_state == {"position": {"x": 0, "y": 0, "z": 0}}


def test_direction_inversion(dummy_stage):
    """Check axes invert as expected when called."""
    # Check initial value
    assert dummy_stage.axis_inverted == {"x": True, "y": False, "z": False}
    # Start inverting
    dummy_stage.invert_axis_direction(axis="x")
    assert dummy_stage.axis_inverted == {"x": False, "y": False, "z": False}
    dummy_stage.invert_axis_direction(axis="x")
    assert dummy_stage.axis_inverted == {"x": True, "y": False, "z": False}
    dummy_stage.invert_axis_direction(axis="y")
    assert dummy_stage.axis_inverted == {"x": True, "y": True, "z": False}
    dummy_stage.invert_axis_direction(axis="y")
    assert dummy_stage.axis_inverted == {"x": True, "y": False, "z": False}
    dummy_stage.invert_axis_direction(axis="z")
    assert dummy_stage.axis_inverted == {"x": True, "y": False, "z": True}
    dummy_stage.invert_axis_direction(axis="z")
    assert dummy_stage.axis_inverted == {"x": True, "y": False, "z": False}


def test_direction_errors_local_and_http():
    """Check for expected errors both locally and over http."""
    thing_conf = {"camera": SimulatedCamera, "stage": DummyStage}
    with LabThingsTestEnv(things=thing_conf) as test_env:
        dummy_stage = test_env.get_thing_by_type(DummyStage)
        stage_client = test_env.get_thing_client("stage")

        assert stage_client.axis_inverted == {"x": True, "y": False, "z": False}
        # Can't set an arbitrary value via a client as read only:
        with pytest.raises(lt.exceptions.ClientPropertyError):
            stage_client.axis_inverted = {"x": 2, "y": 1, "z": 1}

        # ... and should not modify the initial value
        assert stage_client.axis_inverted == {"x": True, "y": False, "z": False}

        # Should error if axis doesn't exist, this is a KeyError in the server
        with pytest.raises(KeyError):
            dummy_stage.invert_axis_direction(axis="theta")
        # But a FailedToInvokeActionError over HTTP
        with pytest.raises(lt.exceptions.FailedToInvokeActionError):
            stage_client.invert_axis_direction(axis="theta")


def _test_move_relative(dummy_stage, axis_inverted, path):
    """Test moving relative, ensuring position and hardware position behave as expected.

    :param axis_inverted: Is used to set the inversion.
    :param path: The 3d path to move over, generated by hypothesis.
    """
    dummy_stage.axis_inverted = axis_inverted
    # Explicitly do axes calculation here to check logic in main code.
    x_dir = -1 if axis_inverted["x"] else 1
    y_dir = -1 if axis_inverted["y"] else 1
    z_dir = -1 if axis_inverted["z"] else 1

    # Enter the stage to start the movement thread.
    with dummy_stage:
        assert dummy_stage._move_thread.is_alive()
        position = list(dummy_stage.position.values())
        for movement in path:
            dummy_stage.move_relative(x=movement[0], y=movement[1], z=movement[2])
            position = [
                pos + move for pos, move in zip(position, movement, strict=True)
            ]
            stage_pos = dummy_stage.get_xyz_position()
            hw_pos = dummy_stage._hardware_position
            assert position[0] == stage_pos[0] == hw_pos["x"] * x_dir
            assert position[1] == stage_pos[1] == hw_pos["y"] * y_dir
            assert position[2] == stage_pos[2] == hw_pos["z"] * z_dir


@given(path=path3d)
@settings(
    max_examples=3,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=10000,
)
def test_move_relative(dummy_stage, path):
    """Loop over different inversion options and check that the stage moves as expected.

    3 paths are tried for each case of axis inversion. This checks both that the
    reported position changes as is input in path, and that the hardware position is
    inverted when appropriate.
    """
    # Note that the fixture is not reset. This is fine, because the stage_should work
    # no matter the starting position.

    axis_names = dummy_stage.axis_names
    # Create every combination of True/False for x,y,z.
    inversion_combinations = [
        dict(zip(axis_names, inverted, strict=True))
        for inverted in itertools.product([True, False], repeat=len(axis_names))
    ]
    print(path)
    for axis_inverted in inversion_combinations:
        _test_move_relative(
            dummy_stage=dummy_stage,
            axis_inverted=axis_inverted,
            path=path,
        )


def _test_move_absolute(dummy_stage, axis_inverted, path):
    """Test moving relative, ensuring position and hardware position behave as expected.

    :param axis_inverted: Is used to set the inversion.
    :param path: The 3d path to move over, generated by hypothesis.
    """
    dummy_stage.axis_inverted = axis_inverted
    # Explicitly do axes calculation here to check logic in main code.
    x_dir = -1 if axis_inverted["x"] else 1
    y_dir = -1 if axis_inverted["y"] else 1
    z_dir = -1 if axis_inverted["z"] else 1
    position = list(dummy_stage.position.values())

    # Enter the stage to start the movement thread.
    with dummy_stage:
        assert dummy_stage._move_thread.is_alive()
        for move_to in path:
            dummy_stage.move_absolute(x=move_to[0], y=move_to[1], z=move_to[2])
            position = move_to
            stage_pos = dummy_stage.get_xyz_position()
            hw_pos = dummy_stage._hardware_position
            assert position[0] == stage_pos[0] == hw_pos["x"] * x_dir
            assert position[1] == stage_pos[1] == hw_pos["y"] * y_dir
            assert position[2] == stage_pos[2] == hw_pos["z"] * z_dir


@given(path=path3d)
@settings(
    max_examples=3,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=10000,
)
def test_move_absolute(dummy_stage, path):
    """Loop over different inversion options and check that the stage moves as expected.

    3 paths are tried for each case of axis inversion. This checks both that the
    reported position changes as is input in path, and that the hardware position is
    inverted when appropriate.
    """
    # Note that the fixture is not reset. This is fine, because the stage_should work
    # no matter the starting position.

    axis_names = dummy_stage.axis_names
    # Create every combination of True/False for x,y,z.
    inversion_combinations = [
        dict(zip(axis_names, inverted, strict=True))
        for inverted in itertools.product([True, False], repeat=len(axis_names))
    ]
    for axis_inverted in inversion_combinations:
        _test_move_absolute(
            dummy_stage=dummy_stage,
            axis_inverted=axis_inverted,
            path=path,
        )


def test_backlash_state(dummy_stage):
    """Test that the backlash state updates as the stage moves."""
    # Use with to start the movement thread.
    with dummy_stage:
        assert dummy_stage._backlash_state == {"x": 0, "y": 0, "z": 0}
        assert dummy_stage.backlash_steps == {"x": 200, "y": 200, "z": 200}

        # Move 100 steps to centre of x backlash range
        dummy_stage.move_relative(x=100)
        assert dummy_stage._backlash_state == {"x": 0.5, "y": 0, "z": 0}
        # Move 50 more steps to halfway between centre and "engaged"
        dummy_stage.move_relative(x=50)
        assert dummy_stage._backlash_state == {"x": 0.75, "y": 0, "z": 0}
        # Move back 50 steps to the centre
        dummy_stage.move_relative(x=-50)
        assert dummy_stage._backlash_state == {"x": 0.5, "y": 0, "z": 0}
        # Move 500 steps, now fully engaged
        dummy_stage.move_relative(x=500)
        assert dummy_stage._backlash_state == {"x": 1, "y": 0, "z": 0}
        # Move back 100 steps in the centre of backash range again.
        dummy_stage.move_relative(x=-100)
        assert dummy_stage._backlash_state == {"x": 0.5, "y": 0, "z": 0}
        # Move forward a load to re-engage
        dummy_stage.move_relative(x=500)
        assert dummy_stage._backlash_state == {"x": 1, "y": 0, "z": 0}
        # Move back 200 to be fully at the far side of the backlash range
        dummy_stage.move_relative(x=-200)
        assert dummy_stage._backlash_state == {"x": 0, "y": 0, "z": 0}
        # Further motion changes nothing in state
        dummy_stage.move_relative(x=-200)
        assert dummy_stage._backlash_state == {"x": 0, "y": 0, "z": 0}


@dataclass()
class BacklashMoveTestCase:
    """Structured information for each test of backlash compensation."""

    position: dict[str, float]
    backlash_state: dict[str, float]
    backlash_steps: dict[str, int]
    movement: dict[str, int]
    relative: bool
    backlash_compensation: BacklashCompensation
    result: list[dict[str, int]]

    @property
    def rel_move(self):
        """The relative movement."""
        if self.relative:
            return self.movement

        return {
            ax: self.movement.get(ax, pos) - pos for ax, pos in self.position.items()
        }

    def validate(self):
        """Check that the total movement in the result is the relative movement requested."""
        total = {ax: sum(move[ax] for move in self.result) for ax in self.position}
        expected = self.rel_move
        assert total == expected


BACKLASH_TEST_MOVEMENTS = [
    # Moving 1 step in x against preferred movement direction
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": -1, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # There are 2 moves as it is against the direction
        result=[{"x": -201, "y": 0, "z": 0}, {"x": 200, "y": 0, "z": 0}],
    ),
    # Moving 1 step in the preferred movement direction
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 1, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # There are 2 moves as 1 step doesn't move the backlash state to engaged.
        result=[{"x": -199, "y": 0, "z": 0}, {"x": 200, "y": 0, "z": 0}],
    ),
    # Moving 1 step in the preferred movement direction (already engaged)
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 1, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 1, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # Already engaged
        result=[{"x": 1, "y": 0, "z": 0}],
    ),
    # Moving 100 steps in the preferred movement direction partially engaged
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0.7, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 100, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # Already 70% to engaged 100 steps creates engagement
        result=[{"x": 100, "y": 0, "z": 0}],
    ),
    # Moving 100 steps in the preferred movement direction partially engaged (again)
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0.3, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 100, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # Only 30% to engaged 100 steps doesn't engage, so 2 moves
        result=[{"x": -100, "y": 0, "z": 0}, {"x": 200, "y": 0, "z": 0}],
    ),
    ## Absolute move versions of the same tests
    # Moving 1 step in x against preferred movement direction
    BacklashMoveTestCase(
        position={"x": 123, "y": 456, "z": 789},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 122},
        relative=False,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # There are 2 moves as it is against the direction
        result=[{"x": -201, "y": 0, "z": 0}, {"x": 200, "y": 0, "z": 0}],
    ),
    # Moving 1 step in the preferred movement direction
    BacklashMoveTestCase(
        position={"x": 123, "y": 456, "z": 789},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 124},
        relative=False,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # There are 2 moves as 1 step doesn't move the backlash state to engaged.
        result=[{"x": -199, "y": 0, "z": 0}, {"x": 200, "y": 0, "z": 0}],
    ),
    # Moving 1 step in the preferred movement direction (already engaged)
    BacklashMoveTestCase(
        position={"x": 123, "y": 456, "z": 789},
        backlash_state={"x": 1, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 124},
        relative=False,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # Already engaged
        result=[{"x": 1, "y": 0, "z": 0}],
    ),
    # Moving 100 steps in the preferred movement direction partially engaged
    BacklashMoveTestCase(
        position={"x": 123, "y": 456, "z": 789},
        backlash_state={"x": 0.7, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 223},
        relative=False,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # Already 70% to engaged 100 steps creates engagement
        result=[{"x": 100, "y": 0, "z": 0}],
    ),
    # Moving 100 steps in the preferred movement direction partially engaged (again)
    BacklashMoveTestCase(
        position={"x": 123, "y": 456, "z": 789},
        backlash_state={"x": 0.3, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 223},
        relative=False,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # Only 30% to engaged 100 steps doesn't engage, so 2 moves
        result=[{"x": -100, "y": 0, "z": 0}, {"x": 200, "y": 0, "z": 0}],
    ),
    ## Checking axes correct even when not moving if asked to
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 0, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.ALL_AXES,
        # All move forward and back
        result=[{"x": -200, "y": -200, "z": -200}, {"x": 200, "y": 200, "z": 200}],
    ),
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 0, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.XY_ONLY,
        # x and y correct
        result=[{"x": -200, "y": -200, "z": 0}, {"x": 200, "y": 200, "z": 0}],
    ),
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": 200, "z": 200},
        movement={"x": 0, "y": 0, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.Z_ONLY,
        # z corrects
        result=[{"x": 0, "y": 0, "z": -200}, {"x": 0, "y": 0, "z": 200}],
    ),
    ## Checking reversed backlash directions (on y)
    # In preferred direction
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": -150, "z": 200},
        movement={"x": 0, "y": -300, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # Only 1 move as preferred direction
        result=[{"x": 0, "y": -300, "z": 0}],
    ),
    # against preferred direction
    BacklashMoveTestCase(
        position={"x": 0, "y": 0, "z": 0},
        backlash_state={"x": 0, "y": 0, "z": 0},
        backlash_steps={"x": 200, "y": -150, "z": 200},
        movement={"x": 0, "y": 300, "z": 0},
        relative=True,
        backlash_compensation=BacklashCompensation.MOVEMENT_AXES,
        # 2 moves as against preferred direction
        result=[{"x": 0, "y": 450, "z": 0}, {"x": 0, "y": -150, "z": 0}],
    ),
]


@pytest.mark.parametrize("test_case", BACKLASH_TEST_MOVEMENTS)
def test_backlash_compensated_moves(test_case, dummy_stage, mocker):
    """Test that backlash correction is applied correctly.

    See each test case for an explanation.
    """
    test_case.validate()
    mock_hw_move_rel = mocker.patch.object(dummy_stage, "_hardware_move_relative")
    # Set position
    dummy_stage._hardware_position = dummy_stage._apply_axis_direction(
        test_case.position
    )
    # check position is set
    assert dummy_stage.position == test_case.position

    dummy_stage._backlash_state = test_case.backlash_state
    dummy_stage.backlash_steps = test_case.backlash_steps

    if test_case.relative:
        dummy_stage.move_relative(
            **test_case.movement,
            block_cancellation=False,
            backlash_compensation=test_case.backlash_compensation,
        )
    else:
        dummy_stage.move_absolute(
            **test_case.movement,
            block_cancellation=False,
            backlash_compensation=test_case.backlash_compensation,
        )
    assert mock_hw_move_rel.call_count == len(test_case.result)
    for res, call in zip(
        test_case.result, mock_hw_move_rel.call_args_list, strict=True
    ):
        applied = call.kwargs
        # Remove the block_cancellation argument and add in any axes that are 0
        applied.pop("block_cancellation")
        applied = {ax: applied.get(ax, 0) for ax in res}
        # Check the expected result was applied for each move.
        assert applied == dummy_stage._apply_axis_direction(res)


def test_thing_description_equivalence(dummy_stage, mocker):
    """Stop extra actions getting added to child classes without explicit approval.

    To add an extra action to a stage this test needs to be updated, highlighting it at
    review. This tests should explain why the action isn't on the general base class.
    """
    mock_sangaboard = mocker.Mock()
    mocker.patch.dict("sys.modules", {"sangaboard": mock_sangaboard})
    from openflexure_microscope_server.things.stage.sangaboard import SangaboardThing

    base_td = create_thing_without_server(BaseStage).thing_description()
    base_actions = set(base_td.actions.keys())
    base_properties = set(base_td.properties.keys())

    dummy_td = dummy_stage.thing_description()
    dummy_actions = set(dummy_td.actions.keys())
    dummy_properties = set(dummy_td.properties.keys())

    sanga_td = create_thing_without_server(SangaboardThing).thing_description()

    # Sangaboard has one extra action for calibrating z-motor direction. This is
    # handled in the webapp calibration wizard, and an extra setting for persisting it
    sanga_extra_actions = {"calibrate_z_direction"}
    sanga_extra_properties = {"z_axis_calibrated"}
    sanga_actions = set(sanga_td.actions.keys()) - sanga_extra_actions
    sanga_properties = set(sanga_td.properties.keys()) - sanga_extra_properties

    assert sanga_actions == dummy_actions == base_actions
    assert sanga_properties == dummy_properties == base_properties


def test_jog_command_repr():
    """Test when printing a jog command the result is as expected.

    This tests the ``__repr__`` method of ``JogCommand``.
    """
    # Jog should represent itself the same whether displacement is set with a list or a
    # tuple.
    assert str(JogCommand([1, 2, 3])) == "<JogCommand>(1, 2, 3)"
    assert str(JogCommand((1, 2, 3))) == "<JogCommand>(1, 2, 3)"
    # Stop should be very clear.
    assert str(JogCommand(None)) == "<JogCommand>STOP"


def test_empty_jog_sends_stop(dummy_stage, mocker, caplog):
    """Check that calling ``jog`` with no args, warns then sends STOP."""
    mock_send = mocker.patch.object(dummy_stage, "_send_jog_command")
    with caplog.at_level(logging.WARNING):
        dummy_stage.jog()
    # This should warn as stop should be sent explicitly
    assert len(caplog.records) == 1
    assert mock_send.call_count == 1
    # Check it is a stop command (displacement is None)
    assert mock_send.call_args.args[0].displacement is None


def test_jog_commands_are_sent(dummy_stage, mocker, caplog):
    """Check that ``jog`` forwards commands to ``_send_jog_command``."""
    mock_send = mocker.patch.object(dummy_stage, "_send_jog_command")
    with caplog.at_level(logging.INFO):
        dummy_stage.jog(x=1, y=0, z=0)
        dummy_stage.jog(x=0, y=2, z=0)
        dummy_stage.jog(x=0, y=0, z=3)
        dummy_stage.jog(stop=True)
    # Normal jogging operation shouldn't be filling up the logs.
    assert len(caplog.records) == 0
    # All 4 commands sent
    assert mock_send.call_count == 4
    # Check commands are as expected
    command_0 = mock_send.call_args_list[0].args[0]
    # -1 as axis inversion is applied
    assert command_0.displacement == (-1, 0, 0)
    command_1 = mock_send.call_args_list[1].args[0]
    assert command_1.displacement == (0, 2, 0)
    command_2 = mock_send.call_args_list[2].args[0]
    assert command_2.displacement == (0, 0, 3)
    command_3 = mock_send.call_args_list[3].args[0]
    assert command_3.displacement is None


def test_send_jog_commands(dummy_stage, mocker, caplog):
    """Check that the jog command acts as expected."""

    # Create a way to make mock threads.
    def mock_thread_factory(*_args, **_kwargs):
        """Return a mock thread instance that claims to be alive."""
        mock_instance = mocker.Mock()
        mock_instance.is_alive.return_value = True
        return mock_instance

    # Mock both the Queue class and threading.Thread to incercept calls.
    mock_queue = mocker.patch(
        "openflexure_microscope_server.things.stage.JogQueue", side_effect=mocker.Mock
    )
    mock_thread = mocker.patch(
        "openflexure_microscope_server.things.stage.threading.Thread",
        side_effect=mock_thread_factory,
    )

    commands = [
        JogCommand([1, 1, 1]),
        JogCommand([2, 2, 2]),
        JogCommand([3, 3, 3]),
        JogCommand([4, 4, 4]),
    ]
    # First call, will create a new thread and a new queue
    dummy_stage._send_jog_command(commands[0])

    # Both a new queue and a new thread are created
    assert mock_queue.call_count == 1
    assert mock_thread.call_count == 1
    # The thread target is the jog loop
    assert mock_thread.call_args.kwargs["target"] == dummy_stage._jog_loop
    # Args are just the first command
    thread_args = mock_thread.call_args.kwargs["args"]
    assert len(thread_args) == 1
    assert thread_args[0] is commands[0]
    # Nothing yet put in the thread
    assert dummy_stage._jog_queue.put.call_count == 0

    # Send second command:
    dummy_stage._send_jog_command(commands[1])

    # No new queue or thread created
    assert mock_queue.call_count == 1
    assert mock_thread.call_count == 1
    # Put is used instead
    assert dummy_stage._jog_queue.put.call_count == 1
    # Called with the second command
    assert dummy_stage._jog_queue.put.call_args.args[0] is commands[1]

    # Make it so the thread has finished
    dummy_stage._jog_thread.is_alive.return_value = False
    assert not dummy_stage._jog_thread.is_alive()

    # call with the 3rd command, will create a new thread and a new queue
    dummy_stage._send_jog_command(commands[2])

    # Thread is alive again
    assert dummy_stage._jog_thread.is_alive()
    # Both a new queue and a new thread are created
    assert mock_queue.call_count == 2
    assert mock_thread.call_count == 2
    # The thread target is the jog loop
    assert mock_thread.call_args.kwargs["target"] == dummy_stage._jog_loop
    # Args are just the first command
    thread_args = mock_thread.call_args.kwargs["args"]
    assert len(thread_args) == 1
    assert thread_args[0] is commands[2]
    # New queue is never used
    assert dummy_stage._jog_queue.put.call_count == 0

    # Finally acquire the jog lock
    with dummy_stage._jog_lock:
        # and check a warning is thrown
        with caplog.at_level(logging.WARNING):
            dummy_stage._send_jog_command(commands[3])
        assert len(caplog.records) == 1
        # No new thread or queue created
        assert mock_queue.call_count == 2
        assert mock_thread.call_count == 2
        # And still nothing added to the queue
        assert dummy_stage._jog_queue.put.call_count == 0


def test_get_jog_from_queue_most_recent(dummy_stage):
    """Test that the jog queue gives the most recent Jog Command."""
    # Try to stack 4 moves in the queue, only 1 should be queued.
    for i in range(4):
        dummy_stage._jog_queue.put(JogCommand([i, i, i]))

    command = dummy_stage._get_from_jog_queue(0.001)
    assert isinstance(command, JogCommand)
    # Should be the last one queued
    assert command.displacement == (3, 3, 3)
    # Nothing else is queued
    assert dummy_stage._get_from_jog_queue(0.001) is None


def _setup_jog_loop(command, dummy_stage, mocker):
    """Set up a jog loop in a thread and return.

    :return: The thread, and the mocks for ``_hardware_start_move_relative``,
        ``_hardware_stop``, and ``_poll_moving``.
    """
    mocker.patch.object(dummy_stage, "_estimate_move_duration", return_value=0.01)
    mock_poll = mocker.patch.object(dummy_stage, "_poll_moving", return_value=True)

    def stop_mock_move(*_args, **_kwargs):
        """Set the return value of poll to false on stop."""
        mock_poll.return_value = False

    mock_move = mocker.patch.object(dummy_stage, "_hardware_start_move_relative")
    mock_stop = mocker.patch.object(
        dummy_stage, "_hardware_stop", side_effect=stop_mock_move
    )

    dummy_stage._jog_queue = JogQueue()
    thread = threading.Thread(target=dummy_stage._jog_loop, args=(command,))
    thread.start()
    return thread, mock_move, mock_stop, mock_poll


def test_jog_loop_jog_once_only(dummy_stage, mocker):
    """Check if jogging once the jog loops breaks when the move ends.

    This checks there is no need for an explicit stop command.
    """
    command = JogCommand([1, 1, 0])
    thread, mock_move, mock_stop, mock_poll = _setup_jog_loop(
        command, dummy_stage, mocker
    )
    time.sleep(0.05)
    assert thread.is_alive()
    # Move was called
    assert mock_move.call_count == 1
    # Claim the motors have stopped
    mock_poll.return_value = False
    # Wait longer than 0.1s
    time.sleep(0.15)
    assert not thread.is_alive()
    # Move never called again
    assert mock_move.call_count == 1
    # Stop never called.
    assert mock_stop.call_count == 0


def test_jog_loop_jog_twice_then_stop(dummy_stage, mocker):
    """Check if jogging twice then calling stop."""
    command = JogCommand([1, 0, 0])
    command2 = JogCommand([2, 0, 0])
    command3 = JogCommand(None)
    thread, mock_move, mock_stop, mock_poll = _setup_jog_loop(
        command, dummy_stage, mocker
    )
    time.sleep(0.05)
    assert thread.is_alive()
    # Move was called
    assert mock_move.call_count == 1
    assert mock_stop.call_count == 0

    dummy_stage._jog_queue.put(command2)
    time.sleep(0.05)
    assert thread.is_alive()
    assert mock_move.call_count == 2
    assert mock_stop.call_count == 0

    dummy_stage._jog_queue.put(command3)
    time.sleep(0.15)
    assert not thread.is_alive()
    assert mock_move.call_count == 2
    assert mock_stop.call_count == 1
