"""OpenFlexure Microscope autofocus module.

This module defines a Thing that is responsible for using the stage and
camera together to perform an autofocus routine, and for collecting stacks
of images (a 'z-stack').

See repository root for licensing information.
"""

import enum
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Literal, Mapping, Optional, Self, Sequence

import numpy as np
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

import labthings_fastapi as lt
from labthings_fastapi.types.numpy import NDArray

from ..camera import BaseCamera, CaptureParams
from ..stage import BacklashCompensation, BaseStage
from ..stage.moonraker import (
    MoonrakerStage,
    move_absolute_transit,
    move_relative_transit,
)

LOGGER = logging.getLogger(__name__)
MIN_TEST_IMAGE_COUNT = 3
MAX_TEST_IMAGE_COUNT = 9
EXTRA_STACK_CAPTURES = 15


def resolve_focus_range(
    stage: BaseStage,
    dz: int | None,
    native_default: int = 2000,
    physical_default_um: float = 50,
) -> int:
    """Resolve native units against the physical profile, not stock motor defaults."""
    if dz is None:
        if isinstance(stage, MoonrakerStage):
            z = stage.hardware_settings["axes"]["z"]
            range_mm = physical_default_um / 1000
            if z.get("single_move_limit_enabled", True):
                range_mm = min(range_mm, z["max_move_mm"])
            dz = max(1, round(range_mm * z["units_per_mm"]))
        else:
            dz = native_default
    if dz <= 0:
        raise ValueError("Autofocus range must be positive")
    if isinstance(stage, MoonrakerStage):
        z = stage.hardware_settings["axes"]["z"]
        if not z["enabled"] or (
            z.get("single_move_limit_enabled", True)
            and dz / z["units_per_mm"] > z["max_move_mm"]
        ):
            raise ValueError(
                f"Autofocus sweep exceeds the Z single-move limit "
                f"({z['max_move_mm'] * 1000:g} µm)"
            )
    return dz


def validate_stack_spacing(stage: BaseStage, dz: int) -> None:
    """Validate a captured stack increment as a measuring move, not a transit."""
    if isinstance(stage, MoonrakerStage):
        resolve_focus_range(stage, dz)


def validate_z_offsets(stage: BaseStage, offsets: list[int]) -> None:
    """Validate a complete Z phase before taking its first step."""
    if isinstance(stage, MoonrakerStage):
        stage.validate_path([{"z": value} for value in offsets], relative_to_start=True)


class NotStreamingError(RuntimeError):
    """No images captured from stream. The camera is almost certainly not streaming."""


class SharpnessMethod(enum.IntFlag):
    """The possible SharpnessMethods for autofocus.

    Use powers of two for the methods so they can be selected bitwise. This allows choosing what to record
    as the sum of methods.
    """

    JPEG = 1
    FOCUS_FOM = 2
    # Next method should be 4 not 3 for bitwise selection.


class AutofocusParams(BaseModel):
    """A class for running autofocus routines."""

    dz: int
    sharpness_method: SharpnessMethod = SharpnessMethod.JPEG


class StackOrigin(enum.Enum):
    """The position of the current location in the stack."""

    START = enum.auto()
    CENTER = enum.auto()
    END = enum.auto()


class StackParams(BaseModel):
    """A class for holding stack parameters, and returning computed ones."""

    stack_dz: int
    images_to_save: int = Field(gt=0)

    # Using docstrings under variables as this is how pdoc would expect
    # attributed to be documented

    settling_time: float = Field(default=0.05, ge=0)
    """Time (in seconds) between moving and capturing an image"""

    origin: StackOrigin = StackOrigin.START
    """Where the stack is positioned relative to the current z position."""


class SmartStackParams(StackParams):
    """A class for holding smart stack parameters, and returning computed ones."""

    min_images_to_test: int

    save_on_failure: bool = False
    """Whether to save an image even if no focus was found."""

    check_turning_points: bool = True
    """
    Whether to check the number of turning points in
    the sharpnesses of the images in the stack is exactly 1.
    (May fail with thick samples)
    """

    stack_height_limit: int = 15
    """
    How many images can be appended to the stack after the predicted peak to test
    for focus before assuming the focus was passed, and restarting the stack
    """

    img_undershoot: int = Field(default=5, ge=0, le=5)
    """
    How far below (in factors of stack_dz) the estimated optimal starting position to
    begin the stack. Better to start slightly too low and require many images, rather
    than too high and needing to autofocus and restart the stack
    """

    max_attempts: int = Field(default=3, ge=1, le=3)
    """Maximum number of times to attempt fast stack"""

    extra_images: int = Field(
        default=EXTRA_STACK_CAPTURES, ge=0, le=EXTRA_STACK_CAPTURES
    )
    """Maximum additional test planes beyond the minimum stack."""

    refocus_max_attempts: int = Field(default=10, ge=1, le=10)
    """Maximum looping autofocus attempts before/between stack attempts."""

    @field_validator("min_images_to_test")
    @classmethod
    def check_images_to_test(cls, min_images_to_test: int) -> int:
        """Verify that the images to test parameter matches various constraints."""
        if min_images_to_test < MIN_TEST_IMAGE_COUNT:
            raise ValueError(
                f"Can't test for focus with fewer than {MIN_TEST_IMAGE_COUNT} images."
            )
        if min_images_to_test > MAX_TEST_IMAGE_COUNT:
            raise ValueError(
                f"Testing with more than {MAX_TEST_IMAGE_COUNT} images is likely to "
                "focus on the cover slip, or strike the sample."
            )
        if min_images_to_test % 2 == 0 or min_images_to_test <= 0:
            raise ValueError(
                "Minimum number of images to test should be positive and odd"
            )
        return min_images_to_test

    @field_validator("images_to_save")
    @classmethod
    def check_images_to_save(cls, images_to_save: int) -> int:
        """Verify that the images to save parameter is positive and odd."""
        if images_to_save % 2 == 0 or images_to_save <= 0:
            raise ValueError("Images to save must be positive and odd")
        return images_to_save

    @model_validator(mode="after")
    def check_image_limits(self) -> "SmartStackParams":
        """Ensure the number of images to save isn't more than the minimum tested."""
        if self.images_to_save > self.min_images_to_test:
            raise ValueError("Can't save more images than the minimum number tested.")
        return self

    # Note MyPy doesn't support decorating properties. See MyPy Pull #16571 and issue #14461.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def stack_z_range(self) -> int:
        """The range of the z stack, in steps.

        Note that this is the range of the minimum number of images captured,
        which is also the range of the images stored in memory that can be
        saved.
        """
        return self.stack_dz * (self.min_images_to_test - 1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def steps_undershoot(self) -> int:
        """The distance to deliberately undershoot the estimated optimal starting point."""
        # Starting too low by "steps_undershoot" makes smart stacking faster.
        # Starting a stack too high requires it to move to the start,
        # autofocus and then re-stack. Starting slightly too low only
        # requires extra +z movements and captures.
        return self.stack_dz * self.img_undershoot

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_images_to_test(self) -> int:
        """The maximum number of images that will be captured and tested in a stack.

        This is the minimum plus the configured additional test-plane budget.
        """
        return self.min_images_to_test + self.extra_images

    def slice_to_save(self, sharpest_index: int) -> slice:
        """Return the slice of images to save given the index of the sharpest image."""
        images_each_side = (self.images_to_save - 1) // 2
        return slice(
            max(sharpest_index - images_each_side, 0),
            sharpest_index + images_each_side + 1,
        )


@dataclass
class CaptureInfo:
    """The information from a capture in a smart_z_stack."""

    buffer_id: int
    position: Mapping[str, int]
    sharpness: int

    @property
    def filename(self) -> str:
        """The filename for this image generated from the position.

        The file name is in the format ``img_{x}_{y}_{z}`` where x, y, and z are the
        positions from the microscope stage.
        """
        return (
            f"img_{self.position['x']}_{self.position['y']}_{self.position['z']}.jpeg"
        )


def _get_capture_by_id(captures: list[CaptureInfo], buffer_id: int) -> CaptureInfo:
    """Return the capture from a list of CaptureInfo objects with the matching id.

    :param captures: A list of capture objects
    :param buffer_id: The buffer id of the image to return

    :returns: the CaptureInfo object of the capture with matching id

    :raises ValueError: if buffer_id does not match the buffer_id of any captures
    """
    return captures[_get_capture_index_by_id(captures, buffer_id)]


def _get_capture_index_by_id(captures: list[CaptureInfo], buffer_id: int) -> int:
    """Return the index of the capture with the matching id.

    :param captures: A list of capture objects
    :param buffer_id: The buffer id of the image to return

    :returns: the list index of the capture with matching id

    :raises ValueError: if buffer_id does not match the buffer_id of any captures
    """
    ids = [capture.buffer_id for capture in captures]
    if buffer_id not in ids:
        raise ValueError(f"No capture has a buffer id of {buffer_id}")
    return ids.index(buffer_id)


class SharpnessDataArrays(BaseModel):
    """A BaseModel with the position and sharpness data from JPEGSharpnessMonitor.

    Each JPEG Size (representing a sharpness metric) has an associated timestamp,
    as does each stage position. The stage positions need to be interpolated so
    they correspond with the image timestamps.
    """

    jpeg_times: NDArray
    jpeg_sizes: NDArray
    focus_foms: NDArray
    stage_times: NDArray
    stage_positions: list[Mapping[str, int]]


class JPEGSharpnessMonitor:
    """A class with direct access to the CameraThing for monitoring the MJPEG stream.

    The autofocus algorithm uses sharpness calculated from the file size of the
    images in the MJPEG stream. This class monitors both the stage position and the
    jpeg sharpness over time.

    The ``run`` context manager is used to start monitoring the camera stream. Position
    monitoring happens during ``focus_rel``. Raw data can be retrieved with
    ``data_dict`` and data with interpolated ``z`` positions can be retrieved with
    move_data.

    """

    def __init__(
        self,
        stage: BaseStage,
        camera: BaseCamera,
        method: SharpnessMethod = SharpnessMethod.JPEG,
        record: Optional[int] = None,
    ) -> None:
        """Initialise a new JPEGSharpnessMonitor. The args are injected automatically.

        :param stage: A direct_thing_client dependency for the the microscope stage.
        :param camera: A raw_thing_client depeendency for the camera. This is a raw
            dependency as required by the underlying class.
        :param method: The sharpness metric used when evaluating autofocus.
        :param record: Bitmask of sharpness metrics to record while monitoring.
            If ``None`` or 0, only the metric specified by ``method`` is recorded.

        :raises ValueError: If ``method`` is not included in ``record``.
        :raises ValueError: If ``SharpnessMethod.FOCUS_FOM`` is requested but
            the camera does not support FocusFoM measurements.
        """
        self.camera = camera
        self.stage = stage
        self.method = method
        self.record = record if record else method

        if not self.method & self.record:
            raise ValueError(
                f"The sharpness metric {self.record} is not being recorded."
            )

        if self.record & SharpnessMethod.FOCUS_FOM and not camera.supports_focus_fom:
            raise ValueError(
                "Cannot record focus FOM as this camera doesn't support it."
            )
        LOGGER.debug(f"Created sharpness monitor with {stage}, {camera}")
        self._stage_positions: list[Mapping[str, int]] = []
        self._stage_times: list[float] = []
        self._jpeg_times: list[float] = []
        self._jpeg_sizes: list[int] = []
        self._focus_foms: list[float] = []

    @property
    def stage_positions(self) -> Sequence[Mapping[str, int]]:
        """The positions recorded for the stage.

        :raises ValueError: If stage position recording is not enabled.
        """
        if not self._stage_positions:
            raise ValueError("Stage positions have not been recorded yet.")
        return self._stage_positions

    @property
    def stage_times(self) -> Sequence[float]:
        """The timestamps recorded for stage position updates."""
        return self._stage_times

    @property
    def jpeg_times(self) -> Sequence[float]:
        """The timestamps recorded for captured JPEG frames."""
        return self._jpeg_times

    @property
    def jpeg_sizes(self) -> Sequence[int]:
        """The recorded JPEG frame sizes used as a sharpness metric.

        :raises ValueError: If JPEG sharpness recording is not enabled.
        """
        if not self.record & SharpnessMethod.JPEG:
            raise ValueError("JPEG sizes are not being recorded.")
        return self._jpeg_sizes

    @property
    def focus_foms(self) -> Sequence[float]:
        """The recorded FocusFoM values.

        :raises ValueError: If FocusFoM recording is not enabled.
        """
        if not self.record & SharpnessMethod.FOCUS_FOM:
            raise ValueError("FocusFoM values are not being recorded.")
        return self._focus_foms

    running = False

    async def monitor_sharpness(self) -> None:
        """Start monitoring sharpness metrics."""
        self.running = True
        while self.running:
            i = await self.camera.lores_mjpeg_stream.next_frame()
            entry = await self.camera.lores_mjpeg_stream.ringbuffer_entry(i)
            self._jpeg_times.append(entry.timestamp.timestamp())

            # JPEG sharpness metric
            if self.record & SharpnessMethod.JPEG:
                self._jpeg_sizes.append(len(entry.frame))

            # FocusFoM metric
            if self.record & SharpnessMethod.FOCUS_FOM:
                self._focus_foms.append(self.camera.focus_fom)

            if not self.running:
                break

    def __enter__(self) -> Self:
        """Start context manager, during which sharpness from the camera is monitored."""
        # Use the cameras _thing_server_interface to get access to the server event loop
        # and start a task.
        self.camera._thing_server_interface.start_async_task_soon(
            self.monitor_sharpness
        )
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Clean up after context manager is closed."""
        self.running = False

    def focus_rel(self, dz: int, block_cancellation: bool = False) -> tuple[int, int]:
        """Move the stage by dz, monitoring the position over time.

        This performs exactly one move. Multiple calls of this method
        will append to the internal position storage for more complex
        autofocus procedures.

        This should be run from within the JPEGSharpnessMonitor.run
        context manager so that sharpness data and timestamps are also
        collected.
        """
        # Store the start time and position
        self._stage_times.append(time.time())
        self._stage_positions.append(self.stage.position)

        # Main move
        self.stage.move_relative(z=dz, block_cancellation=block_cancellation)

        # Store the end time and position
        self._stage_times.append(time.time())
        self._stage_positions.append(self.stage.position)
        if isinstance(self.stage, MoonrakerStage) and self.stage.last_motion_interval:
            # Interpolation still approximates commanded movement, not encoder Z.
            # Stationary readback/settle time must not be mapped onto a moving Z.
            self._stage_times[-2:] = list(self.stage.last_motion_interval)

        # Index of the data for this movement
        data_index: int = len(self.stage_positions) - 2
        # Final z position after move
        final_z_position: int = self.stage_positions[-1]["z"]
        return data_index, final_z_position

    def move_data(
        self, istart: int, istop: Optional[int] = None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Extract sharpness as a function of (interpolated) z."""
        if istop is None:
            istop = istart + 2
        jpeg_times: np.ndarray = np.array(self.jpeg_times)
        # Two sharpness metrics are measured - this chooses which to use to focus
        if self.method == SharpnessMethod.JPEG:
            sharpnesses = np.array(self.jpeg_sizes)
        elif self.method == SharpnessMethod.FOCUS_FOM:
            sharpnesses = np.array(self.focus_foms)

        stage_times: np.ndarray = np.array(self.stage_times)[istart:istop]
        stage_heights: np.ndarray = np.array(
            [p["z"] for p in self.stage_positions[istart:istop]]
        )
        try:
            start: int = int(np.argmax(jpeg_times > stage_times[0]))
            stop: int = int(np.argmax(jpeg_times > stage_times[1]))
        except ValueError as e:
            if np.sum(jpeg_times > stage_times[0]) == 0:
                errmsg = (
                    "No images were captured during the move of the stage. "
                    "Perhaps the camera is not streaming images?"
                )
                raise ValueError(errmsg) from e
            raise e
        if stop < 1:
            stop = len(jpeg_times)
            LOGGER.debug("changing stop to %s", (stop))
        jpeg_times = jpeg_times[start:stop]
        jpeg_heights: np.ndarray = np.interp(jpeg_times, stage_times, stage_heights)
        return jpeg_times, jpeg_heights, sharpnesses[start:stop]

    def sharpest_z_on_move(self, data_index: int) -> int:
        """Return the z position of the sharpest image on a given move."""
        _, jpeg_heights, jpeg_sizes = self.move_data(data_index)
        if len(jpeg_sizes) == 0:
            raise NotStreamingError(
                "No images were captured during the move of the stage. "
                "Perhaps the camera is not streaming images?"
            )
        # Quantize absolute Z before the caller forms an integer relative move.
        return round(float(jpeg_heights[np.argmax(jpeg_sizes)]))

    def data_to_array(self) -> SharpnessDataArrays:
        """Return the gathered data as SharpnessDataArrays."""
        return SharpnessDataArrays(
            jpeg_times=np.array(self._jpeg_times),
            jpeg_sizes=(
                np.array(self._jpeg_sizes)
                if self.record & SharpnessMethod.JPEG
                else np.array([])
            ),
            focus_foms=(
                np.array(self._focus_foms)
                if self.record & SharpnessMethod.FOCUS_FOM
                else np.array([])
            ),
            stage_times=np.array(self._stage_times),
            stage_positions=self._stage_positions,
        )

    def data_dict(self) -> dict:
        """Return the gathered data as dict."""
        return self.data_to_array().model_dump()


class AutofocusThing(lt.Thing):
    """The Thing concerned with combinations of z axis movements and the camera.

    Actions here involve moving a stage in z, and using the camera to either
    capture images (generally, z-stacking) and measuring the sharpness of the
    field of view to assess focus (autofocus and testing the success of a z-stack)
    """

    _class_settings = {"validate_properties_on_set": True}
    _cam: BaseCamera = lt.thing_slot()
    _stage: BaseStage = lt.thing_slot()
    default_range_um: float = lt.setting(default=50, gt=0)
    """Default physical sweep for bounded stages; native stages keep native defaults."""
    max_attempts: int = lt.setting(default=10, ge=1, le=10)
    """Maximum number of attempts in looping autofocus."""

    @lt.action
    def fast_autofocus(
        self,
        dz: int | None = None,
        start: Literal["centre", "base"] = "centre",
        sharpness_metric: SharpnessMethod = SharpnessMethod.JPEG,
        record: Optional[int] = None,
    ) -> SharpnessDataArrays:
        """Sweep the stage up and down, then move to the sharpest point.

        This method will move down by dz/2, sweep up by dz, and then evaluate
        the position where the image was sharpest. We'll then move back down,
        and finally up to the sharpest point.

        :param dz: Total z-range (in steps) used for the autofocus sweep.
        :param start: Starting position of the sweep. "centre" begins at -dz/2,
            while "base" begins from the current position.
        :param sharpness_metric: Sharpness metric used for evaluation. Either
            JPEG file size (1) or inbuilt figure of metric (2).
        :param record: Optional bitmask of sharpness metrics to record during
            acquisition. Sharpness metrics can be selected as the sum of their int
            value (see sharpness_metric).
            If None, defaults to recording only the selected sharpness_metric.

        :returns: SharpnessDataArrays containing all recorded sharpness and
            stage position data for the sweep.
        """
        return self._run_fast_autofocus(
            dz=dz,
            start=start,
            sharpness_metric=sharpness_metric,
            record=record,
            before_move=None,
            after_move=None,
        )

    def fast_autofocus_bounded(
        self,
        *,
        dz: int,
        start: Literal["centre", "base"],
        before_move: Callable[[str], None],
        after_move: Callable[[str], None] | None = None,
        sharpness_metric: SharpnessMethod = SharpnessMethod.JPEG,
        record: Optional[int] = None,
    ) -> SharpnessDataArrays:
        """Run the native single sweep with a field-budget gate before each move.

        This is an internal scan boundary.  It changes neither the public action's
        defaults nor its algorithm, and it never retries a failed WHITE search.
        """
        return self._run_fast_autofocus(
            dz=dz,
            start=start,
            sharpness_metric=sharpness_metric,
            record=record,
            before_move=before_move,
            after_move=after_move,
        )

    def _run_fast_autofocus(
        self,
        *,
        dz: int | None,
        start: Literal["centre", "base"],
        sharpness_metric: SharpnessMethod,
        record: Optional[int],
        before_move: Callable[[str], None] | None,
        after_move: Callable[[str], None] | None,
    ) -> SharpnessDataArrays:
        """Implement the existing four-leg fast autofocus with optional gates."""
        dz = resolve_focus_range(
            self._stage, dz, physical_default_um=self.default_range_um
        )
        base = -dz // 2 if start == "centre" else 0
        validate_z_offsets(self._stage, [base, base + dz])

        def gate(phase: str) -> None:
            if before_move is not None:
                before_move(phase)

        def confirmed(phase: str) -> None:
            if after_move is not None:
                after_move(phase)

        with JPEGSharpnessMonitor(
            self._stage,
            self._cam,
            sharpness_metric,
            record=record,
        ) as sharpness_monitor:
            # Move to (-dz / 2)
            if start == "centre":
                gate("white_move_to_base")
                sharpness_monitor.focus_rel(-dz // 2)
                confirmed("white_move_to_base")
            # Move to dz while monitoring sharpness
            # focus_data_index: Sharpness monitor index for this move
            gate("white_sweep_up")
            focus_data_index, _z = sharpness_monitor.focus_rel(
                dz, block_cancellation=True
            )
            confirmed("white_sweep_up")
            # Get the z position with highest sharpness from the previous move
            peak_z: int = sharpness_monitor.sharpest_z_on_move(focus_data_index)
            # Move all the way to the start so it's consistent
            gate("white_return_to_base")
            _index, base_z = sharpness_monitor.focus_rel(-dz)
            confirmed("white_return_to_base")
            # Move to the target position fz (relative move of (peak - current z))
            gate("white_move_to_peak")
            sharpness_monitor.focus_rel(peak_z - base_z)
            confirmed("white_move_to_peak")
            # Return all focus data
            return sharpness_monitor.data_to_array()

    @lt.action
    def z_move_and_measure_sharpness(
        self,
        dz: Sequence[int],
        wait: float = 0,
        sharpness_metric: SharpnessMethod = SharpnessMethod.JPEG,
        record: Optional[int] = None,
    ) -> SharpnessDataArrays:
        """Make a move (or a series of moves) and monitor sharpness.

        This method will will make a series of relative moves in z, and
        return the sharpness (JPEG size and/or FOM) vs time, along with timestamps
        for the moves. This can be used to calibrate autofocus.

        Each move is relative to the last one, i.e. we will finish at
        ``sum(dz)`` relative to the starting position.

        :param dz: A list of moves to make in z (in steps).
        :param wait: The time to pause between movements, in seconds.
        :param sharpness_metric: Sharpness metric used for evaluation. Either
            JPEG file size (1) or inbuilt figure of metric (2).
        :param record: Optional bitmask of sharpness metrics to record during
            acquisition. Sharpness metrics can be selected as the sum of their int
            value (see sharpness_metric).
            If None, defaults to recording only the selected sharpness_metric.
        """
        if isinstance(self._stage, MoonrakerStage):
            offsets = []
            position = 0
            for displacement in dz:
                if displacement:
                    resolve_focus_range(self._stage, abs(displacement))
                position += displacement
                offsets.append(position)
            validate_z_offsets(self._stage, offsets)
        with JPEGSharpnessMonitor(
            self._stage, self._cam, sharpness_metric, record
        ) as sharpness_monitor:
            for move_index, current_dz in enumerate(dz):
                if move_index > 0 and wait > 0:
                    time.sleep(wait)
                sharpness_monitor.focus_rel(current_dz)
            return sharpness_monitor.data_to_array()

    @lt.action
    def looping_autofocus(
        self,
        dz: int | None = None,
        start: Literal["centre", "base"] = "centre",
        sharpness_metric: SharpnessMethod = SharpnessMethod.JPEG,
        record: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ) -> tuple[list[float], list[float]]:
        """Repeatedly autofocus the stage until it looks focused.

        This action will run the ``fast_autofocus`` action until it settles on a point
        in the middle 3/5 of its range. Such logic can be helpful if the microscope
        is close to focus, but not quite within ``dz/2``. It will attempt to autofocus
        up to 10 times.

        :param dz: Total z-range (in steps) used for the autofocus sweep.
        :param start: Starting position of the sweep. "centre" begins at -dz/2,
            while "base" begins from the current position.
        :param sharpness_metric: Sharpness metric used for evaluation. Either
            JPEG file size (1) or inbuilt figure of metric (2).
        :param record: Optional bitmask of sharpness metrics to record during
            acquisition. Sharpness metrics can be selected as the sum of their int
            value (see sharpness_metric).
            If None, defaults to recording only the selected sharpness_metric.

        :param max_attempts: Per-call retry budget; None uses the saved default.

        :returns: SharpnessDataArrays containing all recorded sharpness and
            stage position data for the sweep.
        """
        dz = resolve_focus_range(
            self._stage, dz, physical_default_um=self.default_range_um
        )
        attempts = self.max_attempts if max_attempts is None else max_attempts
        if (
            isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or not 1 <= attempts <= 10
        ):
            raise ValueError("Autofocus attempts must be an integer from 1 to 10")
        attempt = 0

        with JPEGSharpnessMonitor(
            self._stage,
            self._cam,
            sharpness_metric,
            record=record,
        ) as sharpness_monitor:
            while attempt < attempts:
                attempt += 1
                self.logger.info("Refocus sweep %s/%s", attempt, attempts)
                base = -int(dz / 2) if start == "centre" else 0
                validate_z_offsets(self._stage, [base, base + dz])
                if start == "centre":
                    self._stage.move_relative(
                        x=0,
                        y=0,
                        z=-int(dz / 2),
                        backlash_compensation=BacklashCompensation.Z_ONLY,
                    )

                # Always start centrally for future runs
                start = "centre"

                focus_data_index, _ = sharpness_monitor.focus_rel(
                    dz, block_cancellation=True
                )
                _times, heights, sharpnesses = sharpness_monitor.move_data(
                    focus_data_index
                )

                peak_height = heights[np.argmax(sharpnesses)]
                target_min = np.min(heights) + dz / 5
                target_max = np.max(heights) - dz / 5

                # move to the peak
                self._stage.move_absolute(
                    z=peak_height,
                    backlash_compensation=BacklashCompensation.Z_ONLY,
                )

                if target_min < peak_height < target_max:
                    # If it is within the target range then return
                    return heights.tolist(), sharpnesses.tolist()
        raise NoFocusFoundError(
            "Looping autofocus couldn't converge on a focus location."
        )

    @lt.action
    def run_smart_stack(
        self,
        stack_parameters: SmartStackParams,
        capture_parameters: CaptureParams,
        autofocus_parameters: AutofocusParams,
    ) -> tuple[bool, int, int]:
        """Run a smart stack.

        A smart stack captures images offset in z, testing whether the sharpest image
        is towards the centre of the stack.

        The sharpest image, and optionally images around the sharpest, will be saved
        to the images_dir with their coordinates in the filename.

        :param stack_parameters: A SmartStackParams object containing the required
            parameters to run a stack.

        :returns: A tuple containing:

            * A boolean, True if stack was successfully
            * The z position of the sharpest image
            * The number of images captured in the stack, as an int
        """
        attempt = 0
        while True:
            attempt += 1
            self.logger.info(
                "Z stack attempt %s/%s: %s to %s test images",
                attempt,
                stack_parameters.max_attempts,
                stack_parameters.min_images_to_test,
                stack_parameters.max_images_to_test,
            )
            success, captures, sharpest_id = self.smart_z_stack(
                stack_parameters=stack_parameters,
                check_turning_points=stack_parameters.check_turning_points,
            )

            self.logger.info(
                "Z stack attempt %s: %s after %s test images",
                attempt,
                "focused" if success else "focus not accepted",
                len(captures),
            )
            if success or attempt >= stack_parameters.max_attempts:
                break

            # The z position of the first images in the previous attempt.
            initial_z_pos = captures[0].position["z"]
            # If a stack is not successful, move to the start and autofocus
            try:
                self.reset_stack(
                    initial_z_pos,
                    autofocus_parameters.dz,
                    max_attempts=stack_parameters.refocus_max_attempts,
                )
            except NoFocusFoundError:
                break

        # Save stack_parameters.image_to_save images centred on the sharpest capture.
        # If the smart_stack failed the exact number of images saved may not be
        # stack_parameters.image_to_save
        if success or stack_parameters.save_on_failure:
            self.save_stack(
                sharpest_id=sharpest_id,
                captures=captures,
                stack_parameters=stack_parameters,
                capture_parameters=capture_parameters,
            )

            image_count = stack_parameters.images_to_save
        else:
            image_count = 0

        return (
            success,
            _get_capture_by_id(captures, sharpest_id).position["z"],
            image_count,
        )

    def reset_stack(
        self,
        initial_z_pos: int,
        autofocus_dz: int,
        max_attempts: Optional[int] = None,
    ) -> None:
        """Return to the initial z position and run a looping autofocus.

        :param initial_z_pos: The initial z positions of previous captures
        :param  autofocus_dz: the range in steps to autofocus

        ``stage`` and ``sharpness_monitor`` are Thing dependencies passed through
        from the calling action.
        """
        move_absolute_transit(self._stage, z=initial_z_pos)
        self.looping_autofocus(dz=autofocus_dz, max_attempts=max_attempts)

    def save_stack(
        self,
        sharpest_id: int,
        captures: list[CaptureInfo],
        stack_parameters: SmartStackParams,
        capture_parameters: CaptureParams,
    ) -> int:
        """Save the required captures to disk.

        This will save the sharpest image, and optionally extra images either
        side of focus (see ``stack_parameters.images_to_save``).

        :param sharpest_id: the buffer id index of the sharpest image
        :param captures: a list of captures, including file name, image data and
            metadata
        :param stack_parameters: a SmartStackParams object holding stack parameters
        """
        sharpest_index = _get_capture_index_by_id(captures, sharpest_id)
        slice_to_save = stack_parameters.slice_to_save(sharpest_index)

        # Loop through the range, saving each capture to disk
        for capture in captures[slice_to_save]:
            path = capture_parameters.images_dir.join(capture.filename)
            self._cam.save_from_memory(path=path, buffer_id=capture.buffer_id)

        self._cam.clear_buffers()
        return sharpest_index

    def smart_z_stack(
        self,
        stack_parameters: SmartStackParams,
        check_turning_points: bool,
    ) -> tuple[bool, list[CaptureInfo], int]:
        """Capture a series of images checking that sharpest image central.

        This is part of run_smart_stack. This is the actual z_stackng stacking method
        called by the action run_smart_stack. The action also handles resetting,
        autofocussing, and retrying.

        The images are separated in z offset by stack_parameters.stack_dz, as they
        are captured the last stack_parameters.min_images_to_test images are checked
        to see if the sharpest image is central enough in the stack. If it is the stack
        completes.

        :param stack_parameters: a SmartStackParams object holding stack parameters
        :param check_turning_points: Whether to check the number of turning points in
            the sharpnesses of the images in the stack is exactly 1. (May fail with
            thick samples)

        :returns: A tuple of

            * the stack result (True for successful stack, False for failed stack),
            * a list of CaptureInfo objects,
            * the buffer_id of the sharpest image
        """
        # Move down by the height of the z stack, plus an overshoot
        # Better to start too low and take too many images than too high and need to refocus
        validate_stack_spacing(self._stage, stack_parameters.stack_dz)
        offset = -int(
            stack_parameters.steps_undershoot + stack_parameters.stack_z_range / 2
        )
        max_offset = (
            offset
            + (
                stack_parameters.max_images_to_test
                - 1
                + (stack_parameters.images_to_save - 1) // 2
            )
            * stack_parameters.stack_dz
        )
        validate_z_offsets(self._stage, [offset, max_offset])
        move_relative_transit(
            self._stage,
            z=offset,
            backlash_compensation=BacklashCompensation.Z_ONLY,
        )

        captures: list[CaptureInfo] = []
        # Always check for focus using the the last `min_images_to_test` in the
        # stack so check is fair
        ims_to_check = slice(-stack_parameters.min_images_to_test, None)

        images_each_side = (stack_parameters.images_to_save - 1) // 2
        safe_buffer_max = stack_parameters.min_images_to_test + images_each_side
        # If the sharpest image isn't found within the maximum number of images
        # end the loop and return False indicating the stack failed.
        while len(captures) < stack_parameters.max_images_to_test:
            time.sleep(stack_parameters.settling_time)

            # Append a new image to the stack
            cap_info = self.capture_stack_image(buffer_max=safe_buffer_max)
            captures.append(cap_info)

            # If the number of images is enough to test, test them
            if len(captures) >= stack_parameters.min_images_to_test:
                subset_to_check = captures[ims_to_check]
                result, capture_id = self.check_stack_result(
                    subset_to_check, check_turning_points=check_turning_points
                )

                if result == "success":
                    sharpest_index = _get_capture_index_by_id(captures, capture_id)

                    # Calculate how many trailing images are still needed
                    current_trailing = (len(captures) - 1) - sharpest_index
                    shortfall = images_each_side - current_trailing

                    shortfall = max(0, shortfall)

                    # Capture the shortfall to complete the symmetric slice
                    for _ in range(shortfall):
                        self._stage.move_relative(z=stack_parameters.stack_dz)
                        time.sleep(stack_parameters.settling_time)

                        cap_info = self.capture_stack_image(buffer_max=safe_buffer_max)
                        captures.append(cap_info)

                    return True, captures, capture_id

                if result == "restart":
                    return False, captures, capture_id
                # If reached here the result was "continue"
            if len(captures) < stack_parameters.max_images_to_test:
                self._stage.move_relative(z=stack_parameters.stack_dz)
        return False, captures, capture_id

    def capture_stack_image(self, buffer_max: int) -> CaptureInfo:
        """Capture another image and return the capture information.

        The capture is stored by the camera Thing, and can be saved by ID.

        :param buffer_max: The maximum number of images to tell the camera to keep in memory
            for saving once the stack is complete

        :returns: A CaptureInfo object containing the capture information including its
            camera buffer_id needed for saving.
        """
        stage_location = self._stage.position
        buffer_id = self._cam.capture_to_memory(
            capture_mode="standard", buffer_max=buffer_max
        )
        return CaptureInfo(
            buffer_id=buffer_id,
            position=stage_location,
            sharpness=self._cam.grab_jpeg_size(stream_name="lores"),
        )

    # Silence too many returns and complexity in this situation as refactoring to
    # reduce returns is unlikely to improve readability.
    # This function is basically a complex switch statement, having an explicit
    # return after each option is clear.
    def check_stack_result(  # noqa: PLR0911 C901
        self, captures: list[CaptureInfo], check_turning_points: bool
    ) -> tuple[Literal["success", "continue", "restart"], int]:
        """Check if the sharpest image in a list of captures is central enough.

        :param captures: a list of the capture objects to for testing if the
            sharpness has converged in the centre
        :param check_turning_points: Whether to check the number of turning points in
            the sharpnesses of the images in the stack is exactly 1. (May fail with
            thick samples and stacks with many images)

        :returns: A tuple with two values:

            * result - which is one of three literal values:

                * ``success`` if the sharpest image is towards the centre
                * ``continue`` if the sharpest image is in the final two images of the
                    list
                * ``restart`` if the sharpest image is in the first two images of the
                    list

            * capture_id - the buffer id of the sharpest image
        """
        sharpnesses = np.array([capture.sharpness for capture in captures])
        sharpest_index = np.argmax(sharpnesses)
        # The buffer id of the sharpest image
        capture_id = captures[sharpest_index].buffer_id
        n_imgs = len(captures)

        # If only testing one image, then by definition the sharpest is central
        if n_imgs == 1:
            return "success", capture_id
        # If testing three images, test if the centre is the sharpest
        if n_imgs == 3:
            if sharpest_index == 1:
                return "success", capture_id
            if sharpest_index == 0:
                return "restart", capture_id
            return "continue", capture_id

        # Manually test for monotomically increasing or decreasing sharpnesses, as
        # fitting can struggle with these and their behaviour is simpler to hardcode
        if np.all(sharpnesses[:-1] <= sharpnesses[1:]):
            return "continue", capture_id
        if np.all(sharpnesses[:-1] >= sharpnesses[1:]):
            return "restart", capture_id

        try:
            turning = _get_peak_turning_point(sharpnesses)
        except NotAPeakError:
            return "continue", capture_id

        # For larger stacks, test if the best image is not within two of the edge of
        # the stack ie for a stack of 7 images, best image must be between 3rd and 5th
        edge_size = 2

        # Check both the peak from fitting and the sharpest image are not at the
        # edge of the stack.
        if turning < edge_size - 0.5 or sharpest_index < edge_size:
            return "restart", capture_id
        if turning > n_imgs - edge_size - 0.5 or sharpest_index >= n_imgs - edge_size:
            return "continue", capture_id

        if check_turning_points:
            turning_points = _count_turning_points(sharpnesses)
            if turning_points > 1:
                return "continue", capture_id

        return "success", capture_id

    @lt.action
    def run_basic_stack(
        self,
        stack_parameters: StackParams,
        capture_parameters: CaptureParams,
    ) -> tuple[int, list[int]]:
        """Capture a simple z-stack with no focus testing or fitting.

        This performs a fixed stack of images spaced by `stack_dz`,
        saving all images captured. No sharpness testing, restart
        logic, or autofocus is performed.

        :param stack_parameters: StackParams defining stack spacing,
            and image count.
        :param capture_parameters: CaptureParams defining save
            resolution, images directory.

        :returns:
            - Final z position
            - List of z positions captured
        """
        captures: list[CaptureInfo] = []
        z_positions: list[int] = []

        total_range = stack_parameters.stack_dz * (stack_parameters.images_to_save - 1)

        # Determine starting offset based on stack origin
        if stack_parameters.origin == StackOrigin.CENTER:
            target_offset = -total_range // 2
        elif stack_parameters.origin == StackOrigin.END:
            target_offset = -total_range
        else:
            target_offset = 0
        validate_stack_spacing(self._stage, stack_parameters.stack_dz)
        validate_z_offsets(self._stage, [target_offset, target_offset + total_range])
        if target_offset != 0:
            move_relative_transit(self._stage, z=target_offset)

        # Capture images_to_save images
        for move_count in range(stack_parameters.images_to_save):
            time.sleep(stack_parameters.settling_time)

            capture = self.capture_stack_image(
                buffer_max=stack_parameters.images_to_save
            )
            captures.append(capture)
            z_positions.append(capture.position["z"])

            # Only move stage if not on the last image
            if move_count < stack_parameters.images_to_save - 1:
                self._stage.move_relative(z=stack_parameters.stack_dz)

        # Save all captures
        for capture in captures:
            path = capture_parameters.images_dir.join(capture.filename)
            self._cam.save_from_memory(path=path, buffer_id=capture.buffer_id)

        self._cam.clear_buffers()

        final_z = self._stage.position["z"]
        return final_z, z_positions

    @lt.action
    def measure_settling_time(
        self, delay: float = 2, dz: int | None = None
    ) -> SharpnessDataArrays:
        """Make a Z move down then up by dz, then pause for delay while monitoring sharpness.

        This is useful to see how long to wait for the sharpness value to converge
        """
        dz = resolve_focus_range(
            self._stage,
            dz,
            native_default=800,
            physical_default_um=self.default_range_um,
        )
        validate_z_offsets(self._stage, [-dz, 0])
        with JPEGSharpnessMonitor(self._stage, self._cam) as sharpness_monitor:
            sharpness_monitor.focus_rel(-dz)
            sharpness_monitor.focus_rel(dz)
            # Sleep for the given delay, continuing to measure frames without moving
            time.sleep(delay)
        return sharpness_monitor.data_to_array()


class NotAPeakError(lt.exceptions.InvocationError):
    """The data to fit isn't a peak."""


class NoFocusFoundError(lt.exceptions.InvocationError):
    """No focus found during looping Autofocus."""


def _get_peak_turning_point(sharpnesses: np.ndarray) -> float:
    """Get the turning point for a sharpnesses in a z-stack.

    :param sharpnesses: A numpy array of sharpnesses
    :return: The x value of the turning point where x-axis is 0 to N-1 for the N
        sharpness values

    :raise NotAPeakError: If the fit doesn't have a maximum within 95% confidence.
    """
    # Fit the peak
    x = range(len(sharpnesses))
    coeffs, cov = np.polyfit(x, sharpnesses, deg=2, cov=True)
    # a in the equation a*x^2 + bx + c
    a = float(coeffs[0])
    fit_func = np.poly1d(coeffs)

    # find the peaks's x-position
    turning = float(fit_func.deriv().roots[0])
    # sigma_a the standard error of a
    sigma_a = float(np.sqrt(cov[0, 0]))
    # Estimate the upper 95% confidence bound for the x^2 term
    ci_high = a + 2 * sigma_a
    # If the high 95% confindence interval of the peak is not negative then the
    # fit isn't sure if this is a peak or a U shape, so we continue.
    # -1e-9 is used to guard against weird effects for flat and straight data
    if ci_high > -1e-9:
        raise NotAPeakError("Not a peak to within 95% confidence.")
    return turning


def _count_turning_points(sharpnesses: np.ndarray) -> int:
    """Count the number of turing points, after rejecting those from noise."""
    # Also take the difference of neighbouring points to check for turning points
    d_sharpnesses = sharpnesses[1:] - sharpnesses[:-1]
    # Filter out any points that are not prominent
    prominent = abs(d_sharpnesses) / np.mean(abs(d_sharpnesses)) > 0.5
    d_sharpnesses = d_sharpnesses[prominent]
    # count the sign changes
    return int(np.sum(d_sharpnesses[1:] * d_sharpnesses[:-1] < 0))
