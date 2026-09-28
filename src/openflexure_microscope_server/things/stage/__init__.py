"""A package for stage control Things.

`BaseStage` is the base class that provides core stage functionality, but
no hardware interface control. To create a stage Thing to control a specific
piece of hardware the BaseStage should be subclassed, and any method raising
a NotImplementedError should be created.

As the object will be used as a context manager create the hardware connection in
``__enter__`` (not in ``__init__``), and close the connection with ``__exit__``.
"""

from __future__ import annotations

import enum
import queue
import threading
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Optional, overload

import labthings_fastapi as lt


class RedefinedBaseMovementError(RuntimeError):
    """The subclass of BaseStage has overridden ``move_relative`` or ``move_absolute``.

    Overriding ``move_relative`` or ``move_absolute`` can be problematic as these use the
    external position not the hardware position. It is recommended to override
    ``_hardware_move_relative`` and ``_hardware_move_absolute`` instead.

    The BaseStage will raise this on ``__init__``, it is the last thing ``__init__``
    does. As such, this exception can be captured by ``try`` if a stage needs to
    override these for a specific reason.
    """


class BacklashCompensation(enum.Enum):
    """The axes to apply backlash compensation to.

    This will perform a correction step - if necessary - on every
    chosen axis, even if that axis position isn't set to move in
    the calling movement

    * MOVEMENT_AXES - Only the axes that are moving.
    * ALL_AXES - All axes
    * XY_ONLY - Only the x and y axes.
    * Z_ONLY - The z-axis only.
    """

    MOVEMENT_AXES = enum.auto()
    ALL_AXES = enum.auto()
    XY_ONLY = enum.auto()
    Z_ONLY = enum.auto()


class JogCommand:
    """A base class for jog operations."""

    def __init__(self, displacement: Optional[Sequence[int]]) -> None:
        """Initialise a JogCommand.

        :param displacement: The distances as a sequence of moves for each axis.
            None for stop motion.
        """
        super().__init__()
        self.displacement = None if displacement is None else tuple(displacement)

    def __repr__(self) -> str:
        """Represent the command as a string."""
        class_name = type(self).__name__
        if self.displacement is None:
            return f"<{class_name}>STOP"
        return f"<{class_name}>{self.displacement}"


class JogQueue(queue.Queue[JogCommand]):
    """A class queue for JogCommands. This always returns the most recent command."""

    def __init__(self) -> None:
        """Set up a queue with a max size of 1."""
        super().__init__(maxsize=1)

    def put(
        self, item: JogCommand, block: bool = False, timeout: Optional[float] = None
    ) -> None:
        """Put the next command into the queue, bumping anything already there."""
        try:
            # First remove existing item if present
            self.get_nowait()
        except queue.Empty:
            pass
        super().put(item, block=block, timeout=timeout)


class BaseStage(lt.Thing):
    """A base stage class for OpenFlexure translation stages.

    This can't be used directly but should reduce boilerplate code when
    implementing new stages.

    Note that the coordinate system used for the microscope may need to have different
    axis direction as those used by the underlying stage controller.

    A minimal working stage must implement ``_hardware_move_relative``
    and ``_hardware_move_absolute`` actions, which update the ``_hardware_position``
    attribute on completion, and also should implement ``set_zero_position``.
    """

    _axis_names = ("x", "y", "z")
    _class_settings = {"validate_properties_on_set": True}

    def __init__(self, thing_server_interface: lt.ThingServerInterface) -> None:
        """Initialise the stage.

        :raises RedefinedBaseMovementError: if ``move_relative`` and/or
            ``move_absolute`` are overridden. It is recommended to override
            ``_hardware_move_relative`` and/or ``_hardware_move_absolute`` instead so
            that all code in the child class uses the hardware reference frame.
        """
        super().__init__(thing_server_interface)
        self._hardware_lock = threading.RLock()
        self._jog_lock = threading.Lock()
        self._jog_queue = JogQueue()
        self._jog_thread: Optional[threading.Thread] = None
        self._hardware_position: Mapping[str, int] = dict.fromkeys(self._axis_names, 0)
        # Backlash state is a dict as we mutate it during moves.
        self._backlash_state: dict[str, float] = dict.fromkeys(self._axis_names, 0)

        # This must be the last thing the function does in case it is caught in a try.
        if (
            self.__class__.move_relative.func is not BaseStage.move_relative.func
            or self.__class__.move_absolute.func is not BaseStage.move_absolute.func
        ):
            raise RedefinedBaseMovementError(
                "move_relative and/or move_absolute has been overridden. This may "
                "cause issues as the base methods implement converting from program "
                "coordinates to hardware coordinates. Consider overriding "
                "_hardware_move_relative and/or _hardware_move_absolute instead."
            )

    @lt.property
    def axis_names(self) -> Sequence[str]:
        """The names of the stage's axes, in order."""
        return self._axis_names

    @lt.property
    def position(self) -> Mapping[str, int]:
        """Current position of the stage."""
        return self._apply_axis_direction(self._hardware_position)

    backlash_steps: dict[str, int] = lt.setting(
        default_factory=lambda: {"x": 200, "y": 200, "z": 200}, readonly=True
    )
    """The number of steps to elimate backlash. The sign sets the direction.

    A positive number sets the direction of the second move in a backlash correction.
    For example, consider z=200. If the previous move was more than +200 in z, no
    correction is needed. If the last movement was negative then a move of -200,
    followed by +200 will wipe out backlash.
    """

    moving: bool = lt.property(default=False, readonly=True)
    """Whether the stage is in motion."""

    axis_inverted: dict[str, bool] = lt.setting(
        default_factory=lambda: {"x": False, "y": False, "z": False}, readonly=True
    )
    """Used to convert coordinates between the program frame and the hardware frame."""

    @lt.property
    def calibration_required(self) -> bool:
        """Whether the stage requires calibration.

        Currently the only calibration method for the stage is the z-gear rotation
        direction in the sangaboard stage.
        """
        return False

    @lt.property
    def can_calibrate(self) -> bool:
        """Whether the Thing permits calibration."""
        return False

    def update_position(self) -> None:
        """Update the position property from the stage."""
        # Copy the position before the move.
        pos_before = dict(self.position)
        self._hardware_update_position()
        for axis in self._axis_names:
            # The state delta should be 1 for a move equal to backlash steps.
            # moving as this will move the state from 0 (fully disengaged) to
            # 1, fully the other.
            if self.backlash_steps[axis] == 0:
                continue
            delta = (self.position[axis] - pos_before[axis]) / self.backlash_steps[axis]
            # apply value and clamp to within range from 0 to 1
            self._backlash_state[axis] = max(
                0, min(1, self._backlash_state[axis] + delta)
            )

    def _hardware_update_position(self) -> None:
        """Read position from the stage and set internal attribute _hardware_position.

        _hardware_position should only be set in this function.
        """
        raise NotImplementedError(
            "StageThings must define their own _hardware_update_position method"
        )

    @overload
    def _apply_axis_direction(self, position: list[int] | tuple[int]) -> list[int]: ...

    @overload
    def _apply_axis_direction(
        self, position: Mapping[str, int]
    ) -> Mapping[str, int]: ...

    def _apply_axis_direction(
        self, position: list[int] | tuple[int] | Mapping[str, int]
    ) -> list[int] | Mapping[str, int]:
        if isinstance(position, (list, tuple)):
            return [
                -int(pos) if inverted else int(pos)
                for pos, inverted in zip(
                    position, self.axis_inverted.values(), strict=True
                )
            ]
        if isinstance(position, Mapping):
            try:
                return {
                    ax: -int(position[ax])
                    if self.axis_inverted[ax]
                    else int(position[ax])
                    for ax in position
                }
            except KeyError as e:
                raise KeyError(
                    f"One or more axis in {position.keys()} is not defined."
                ) from e
        raise TypeError(
            "Position must be a sequence of positions or a mapping from axis to position."
        )

    @property
    def thing_state(self) -> Mapping[str, Any]:
        """Summary metadata describing the current state of the stage."""
        return {"position": self.position}

    @lt.action
    def invert_axis_direction(self, axis: Literal["x", "y", "z"]) -> None:
        """Invert the direction setting of the given axis.

        :param axis: The axis name (x, y or z) to invert.
        """
        # Not mutating in place so that setting is saved on change.
        direction = self.axis_inverted
        try:
            direction[axis] = not direction[axis]
        except KeyError as e:
            raise KeyError(f"The axis {axis} is not defined.") from e
        self.axis_inverted = direction

    @lt.action
    def move_relative(
        self,
        block_cancellation: bool = False,
        backlash_compensation: Optional[BacklashCompensation] = None,
        **kwargs: int,
    ) -> None:
        """Make a relative move. Keyword arguments should be axis names."""
        if backlash_compensation is not None:
            self._move_with_backlash_correction(
                block_cancellation=block_cancellation,
                relative=True,
                backlash_compensation=backlash_compensation,
                **kwargs,
            )
        else:
            self._hardware_move_relative(
                block_cancellation=block_cancellation,
                **self._apply_axis_direction(kwargs),
            )

    def _hardware_move_relative(
        self, block_cancellation: bool = False, **kwargs: int
    ) -> None:
        """Make a relative move in the coordinate system used by the physical hardware.

        Make sure to use and update ``self._hardware_position`` not ``self.position``.
        """
        raise NotImplementedError(
            "StageThings must define their own _hardware_move_relative method"
        )

    @lt.action
    def move_absolute(
        self,
        block_cancellation: bool = False,
        backlash_compensation: Optional[BacklashCompensation] = None,
        **kwargs: int,
    ) -> None:
        """Make an absolute move. Keyword arguments should be axis names."""
        if backlash_compensation is not None:
            self._move_with_backlash_correction(
                block_cancellation=block_cancellation,
                relative=False,
                backlash_compensation=backlash_compensation,
                **kwargs,
            )
        else:
            self._hardware_move_absolute(
                block_cancellation=block_cancellation,
                **self._apply_axis_direction(kwargs),
            )

    @lt.action
    def move_to_origin(
        self,
        block_cancellation: bool = False,
        backlash_compensation: Optional[BacklashCompensation] = None,
    ) -> None:
        """Move to position (0,0,0), calling move_absolute."""
        self.move_absolute(
            block_cancellation=block_cancellation,
            backlash_compensation=backlash_compensation,
            x=0,
            y=0,
            z=0,
        )

    def _hardware_move_absolute(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make a absolute move in the coordinate system used by the physical hardware.

        Make sure to use and update ``self._hardware_position`` not ``self.position``.
        """
        raise NotImplementedError(
            "StageThings must define their own _hardware_move_absolute method"
        )

    def _backlash_state_after_move(
        self, move: Mapping[str, int], axes_to_check: tuple[str, ...]
    ) -> Mapping[str, float]:
        """Return the backlash state after a given move.

        This is used to determine if a correction is to be applied, so it is not clipped
        to the 0-1 range.
        """
        state = {}
        for ax in axes_to_check:
            if self.backlash_steps[ax] == 0:
                # If an axis has no backlash then it is always engaged so return 1.
                state[ax] = 1.0
            else:
                # Calculate the size of the movement relative to the correction.
                move_rel_to_correction = move[ax] / self.backlash_steps[ax]
                # Apply correction
                state[ax] = self._backlash_state[ax] + move_rel_to_correction
        return state

    def _move_with_backlash_correction(
        self,
        block_cancellation: bool,
        relative: bool,
        backlash_compensation: BacklashCompensation,
        **kwargs: int,
    ) -> None:
        """Make a movement with backlash correction.

        :param block_cancellation: True to prevent the move being cancelled.
        :param relative: True if the kwargs are relative moves. False if they are
            absolute.
        :param backlash_compensation: A BacklashCompensation which sets which axes to
            apply backalsh compensation to.
        :param kwargs: A mapping of axis name to integer for the movement.
        """
        # Calculate relative movement
        if relative:
            move = {ax: kwargs.get(ax, 0) for ax in self._axis_names}
        else:
            move = {ax: kwargs.get(ax, pos) - pos for ax, pos in self.position.items()}

        # Depending on the backlash method decide which axes to check
        check_axes: tuple[str, ...]
        match backlash_compensation:
            case BacklashCompensation.MOVEMENT_AXES:
                check_axes = tuple(ax for ax, move in move.items() if move != 0)
            case BacklashCompensation.ALL_AXES:
                check_axes = self._axis_names
            case BacklashCompensation.XY_ONLY:
                check_axes = ("x", "y")
            case BacklashCompensation.Z_ONLY:
                check_axes = ("z",)
            case _:
                raise ValueError(
                    f"Unknown backlash compensation method {backlash_compensation}"
                )

        # Check the backlash state at the end of the move, no need to clip to range of
        # 0-1
        final_state = self._backlash_state_after_move(move, check_axes)

        # If the state is 1 or greater the motors are engaged in the preferred
        # direction, if not a correction move is needed.
        correction = {
            ax: self.backlash_steps[ax]
            for ax, state in final_state.items()
            if state < 1
        }

        if correction:
            # If there is a correction to apply move in two goes
            first_move = {
                ax: move[ax] - correction.get(ax, 0) for ax in self._axis_names
            }
            self._hardware_move_relative(
                block_cancellation=block_cancellation,
                **self._apply_axis_direction(first_move),
            )
            self._hardware_move_relative(
                block_cancellation=block_cancellation,
                **self._apply_axis_direction(correction),
            )
        else:
            # Else just complete the relative move
            self._hardware_move_relative(
                block_cancellation=block_cancellation,
                **self._apply_axis_direction(move),
            )

    def _hardware_start_move_relative(self, displacement: Sequence[int]) -> None:
        """Start a relative move."""
        raise NotImplementedError(
            "StageThings must define their own _hardware_start_move_relative method"
        )

    def _hardware_stop(self) -> None:
        raise NotImplementedError(
            "StageThings must define their own _hardware_stop method"
        )

    def _poll_moving(self) -> bool:
        """Determine if the stage is still moving."""
        raise NotImplementedError(
            "StageThings must define their own _poll_moving method"
        )

    def _estimate_move_duration(self, displacement: Sequence[int]) -> float:
        """Calculate the expected duration of a move with the given displacement."""
        raise NotImplementedError(
            "StageThings must define their own _estimate_move_duration method"
        )

    @lt.action(use_global_lock=False)
    def jog(self, stop: bool = False, **kwargs: int) -> None:
        """Make a relative move that may be interrupted by a future ``jog``.

        This action makes a relative move. If another ``jog`` action is called while
        a ``jog`` is already in progress, the first will be stopped and the second
        will start immediately. This allows for responsive manual control of the
        stage, for example with a joystick.

        :param stop: if this is set to ``True`` the jog will be terminated.
        :param kwargs: Keyword arguments should be axis names.
        """
        if stop:
            self._send_jog_command(JogCommand(None))
            return

        hardware_moves = self._apply_axis_direction(kwargs)
        move = [hardware_moves.get(axis, 0) for axis in self.axis_names]
        if all(ax == 0 for ax in move):
            self.logger.warning(
                "Requested jog movement is is empty. Sending STOP instead."
            )
            self._send_jog_command(JogCommand(None))
        else:
            self._send_jog_command(JogCommand(move))

    def _send_jog_command(self, command: JogCommand) -> None:
        """Send a jog command to the background jog thread.

        This function will start the background thread if it is not running.
        This function acquires ``_jog_lock`` and uses the ``_jog_send`` event to signal
        the thread to read the next command. As commands interrupt each other, this
        function should never block for a long time.

        :param command: the jog command to send.
        """
        if not self._jog_lock.acquire(timeout=0.1):
            self.logger.warning(
                "Could not send a jog message, this indicates a lock error."
            )
            return
        try:
            # Make sure the queue exists.
            # Check the background thread is running, and restart it if not.
            if self._jog_thread is None or not self._jog_thread.is_alive():
                self.logger.debug("Starting background thread for jog commands")
                self._jog_queue = JogQueue()
                self._jog_thread = threading.Thread(
                    target=self._jog_loop, args=(command,)
                )
                self._jog_thread.start()
            else:
                self._jog_queue.put(command)
        finally:
            self._jog_lock.release()

    def _jog_loop(self, first_command: JogCommand) -> None:
        """Execute jog commands in a background thread.

        This function is intended to be run in a background thread. It will look at
        ``self._jog_command`` when the ``self._jog_send`` event is set.
        """
        # Timeout for checking queue
        timeout = 0.1
        command: Optional[JogCommand] = first_command

        # prevent others using the stage while jogging.
        with self._thing_server_interface.hold_global_lock(), self._hardware_lock:
            while command is not None:
                if command.displacement is not None:
                    self._hardware_start_move_relative(command.displacement)
                    timeout = self._estimate_move_duration(command.displacement)
                else:
                    self._hardware_stop()
                    # Next iteration, we will probably time out.
                    timeout = 0.1
                self.update_position()
                command = self._get_from_jog_queue(timeout)

    def _get_from_jog_queue(self, timeout: float) -> Optional[JogCommand]:
        """Get the next JogCommand from the jog queue.

        :param timeout: The estimtated time the move will take for the queue timeout.
        :return: The jog command or None if the stage stops before a command is
            received.
        """
        while True:
            try:
                return self._jog_queue.get(timeout=timeout)
            except queue.Empty:
                if not self._poll_moving():
                    # The stage is no longer moving, return None
                    return None
            # If we reached here then the stage is still moving. Shorten timeout and
            # check again.
            timeout = 0.1

    @lt.action
    def set_zero_position(self) -> None:
        """Make the current position zero in all axes.

        This action does not move the stage, but resets the position to zero.
        It is intended for use after manually or automatically recentring the
        stage.
        """
        raise NotImplementedError(
            "StageThings must define their own set_zero_position method"
        )

    @lt.action
    def get_xyz_position(self) -> tuple[int, int, int]:
        """Return a tuple containing (x, y, z) position.

        :raises KeyError: if this stage does not have axes named "x", "y", and "z".

        This method provides the interface expected by the camera_stage_mapping.
        """
        position_dict = self.position
        return (position_dict["x"], position_dict["y"], position_dict["z"])

    @lt.action
    def move_to_xyz_position(self, xyz_pos: tuple[int, int, int]) -> None:
        """Move to the location specified by an (x, y, z) tuple.

        :param xyz_pos: The (x, y, z) position to move to.

        :raises KeyError: if this stage does not have axes named "x", "y", and "z".

        This method provides the interface expected by the camera_stage_mapping.
        """
        self.move_absolute(x=xyz_pos[0], y=xyz_pos[1], z=xyz_pos[2])
