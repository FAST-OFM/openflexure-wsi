"""Functionality for mimicking a stage during simulation and testing."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Sequence
from types import TracebackType
from typing import Any, Mapping, Optional, Self

import labthings_fastapi as lt

from . import BaseStage


class DummyStageMovement:
    """A class used internally to send movements to the dummy stages movement thread."""

    def __init__(self, displacement: Optional[Sequence[int]]) -> None:
        """Initialise with a displacement.

        :param displacement: A sequence of integers or None to stop the motion.
        """
        self.started = threading.Event()
        self.displacement = displacement


class DummyStage(BaseStage):
    """A dummy stage for testing purposes.

    This stage should work similarly to a Sangaboard stage, but without any
    hardware attached.
    """

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        step_time: float = 0.001,
        **kwargs: Any,
    ) -> None:
        """Initialise the Dummy stage, setting the step_time to adjust the speed.

        :param step_time: The time in seconds per "motor" step. The default of 0.001
            works well for the live simulation. For unit testing it is very slow
            so the speed can be increased. Increasing it too far is problematic if
            also doing computationally heavy tasks like simulated image blurring.
        """
        super().__init__(thing_server_interface, **kwargs)
        self._movement_enabled = False
        self._move_thread: Optional[threading.Thread] = None
        self._move_queue = queue.Queue[DummyStageMovement]()
        self._movement_ongoing = False

        self.step_time = step_time
        self.instantaneous_position: Mapping[str, int] = self._hardware_position
        self._inst_pos_lock = threading.Lock()
        self._abort_move = threading.Event()

    def __enter__(self) -> Self:
        """Register the stage position and start move thread running."""
        self.instantaneous_position = self._hardware_position
        self._movement_enabled = True
        self._move_thread = threading.Thread(target=self._move_loop)
        self._move_thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Stop the move thread."""
        if self._move_thread is not None and self._move_thread.is_alive():
            self._movement_enabled = False
            self._move_thread.join()

    axis_inverted: dict[str, bool] = lt.setting(
        default_factory=lambda: {"x": True, "y": False, "z": False}, readonly=True
    )
    """Used to convert coordinates between the program frame and the hardware frame."""

    def _hardware_update_position(self) -> None:
        """Read position from the stage and set internal attribute _hardware_position.

        _hardware_position should only be set in this function.
        """
        self._hardware_position = self.instantaneous_position

    def _set_pos_during_move(
        self, displacement: Sequence[int], fraction_complete: float
    ) -> None:
        """Set the instantaneous position based on the completed fraction of an ongoing move."""
        self.instantaneous_position = {
            ax: self._hardware_position[ax] + int(fraction_complete * disp)
            for ax, disp in zip(self.axis_names, displacement, strict=True)
        }

    def _move_loop(self) -> None:
        """Run the move loop. This should be run in a thread on enter.

        This controls all movement of the dummy stage.
        """
        # Start with no move request
        movement_request: Optional[DummyStageMovement] = None
        # Loop until the server ends.
        while self._movement_enabled:
            # First check for a new movement request. These will interrupt ongoing moves
            movement_request = self._check_for_new_move_request()

            # If there is a new movement.
            if movement_request is not None:
                # Set the hardware position from instantaneous before continuing.
                self.update_position()

                if movement_request.displacement is None:
                    # If it is a stop command, stop moving
                    self._movement_ongoing = False
                    movement_request.started.set()
                else:
                    # Record the displacement for future iterations
                    displacement = movement_request.displacement
                    # If it is a move command, set up the variables for the move
                    self._movement_ongoing = True
                    movement_request.started.set()
                    fraction_complete = 0.0
                    dt = self.step_time
                    max_displacement = max(abs(v) for v in displacement)
                    # Always wait at least dt
                    total_time = max((dt * max_displacement), dt)
                    start_time = time.time()
                    time.sleep(dt)

            # If a movement is ongoing
            if self._movement_ongoing:
                fraction_complete = (time.time() - start_time) / total_time
                if fraction_complete < 1:
                    self._set_pos_during_move(displacement, fraction_complete)
                else:
                    # move is complete
                    fraction_complete = 1.0
                    self._set_pos_during_move(displacement, fraction_complete)
                    self.update_position()
                    self._movement_ongoing = False

    def _check_for_new_move_request(self) -> Optional[DummyStageMovement]:
        """Check for new move request to the move_loop."""
        try:
            # Timeout sets the motor speed time
            timeout = self.step_time * 10 if self._movement_ongoing else 0.1
            return self._move_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _hardware_start_move_relative(self, displacement: Sequence[int]) -> None:
        """Start a relative move.

        This starts the stage moving, but does not wait for the move to complete. It
        sets ``self.moving`` to ``True``: resetting it is the responsibility of the
        calling code.
        """
        with self._hardware_lock:
            self.moving = True
            self._move_queue.put(DummyStageMovement(displacement))

    def _hardware_stop(self) -> None:
        with self._hardware_lock:
            self._move_queue.put(DummyStageMovement(None))
            self.moving = False

    def _poll_moving(self) -> bool:
        """Determine if the stage is still moving."""
        moving = self._movement_ongoing
        if self.moving != moving:
            self.moving = moving
        return moving

    def _estimate_move_duration(self, displacement: Sequence[int]) -> float:
        """Calculate the expected duration of a move with the given displacement."""
        max_displacement = max(abs(d) for d in displacement)
        # This does not yet check the board's speed.
        return max_displacement * self.step_time

    def _hardware_move_relative(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make a relative move. Keyword arguments should be axis names."""
        with self._hardware_lock:
            displacement = [kwargs.get(k, 0) for k in self.axis_names]
            self.moving = True
            move_request = DummyStageMovement(displacement)
            self._move_queue.put(move_request)

            move_request.started.wait(timeout=0.1)
            while self._movement_ongoing:
                try:
                    if block_cancellation:
                        time.sleep(self.step_time * 10)
                    else:
                        lt.cancellable_sleep(self.step_time * 10)

                except lt.exceptions.InvocationCancelledError as e:
                    # If the move has been cancelled, stop it but don't handle the
                    # exception. We need the exception to propagate in order to stop
                    # any calling tasks, and to mark the invocation as "cancelled"
                    # rather than stopped.
                    self._move_queue.put(DummyStageMovement(None))
                    raise e
                finally:
                    self.moving = False

    def _hardware_move_absolute(
        self,
        block_cancellation: bool = False,
        **kwargs: int,
    ) -> None:
        """Make an absolute move. Keyword arguments should be axis names."""
        displacement = {
            axis: int(pos) - self._hardware_position[axis]
            for axis, pos in kwargs.items()
            if axis in self.axis_names
        }
        self._hardware_move_relative(
            block_cancellation=block_cancellation, **displacement
        )

    @lt.action
    def set_zero_position(self) -> None:
        """Make the current position zero in all axes.

        This action does not move the stage, but resets the position to zero.
        It is intended for use after manually or automatically recentring the
        stage.
        """
        with self._hardware_lock:
            self.instantaneous_position = dict.fromkeys(self.axis_names, 0)
            self.update_position()
