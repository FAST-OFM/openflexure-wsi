"""OpenFlexure Microscope API extension for stage calibration.

This file contains the HTTP API for camera/stage calibration. It
includes calibration functions that measure the relationship between
stage coordinates and camera coordinates, as well as functions that
move by a specified displacement in pixels, perform closed-loop moves,
and return the calibration data.

This module is only intended to be called from the OpenFlexure Microscope
server, and depends on that server and its underlying LabThings library.
"""

import json
import time
from typing import Any, List, Mapping, NamedTuple, Optional, Tuple, cast

import numpy as np

import labthings_fastapi as lt
from camera_stage_mapping.camera_stage_calibration_1d import (
    calibrate_backlash_1d,
    image_to_stage_displacement_from_1d,
)
from camera_stage_mapping.camera_stage_tracker import Tracker
from camera_stage_mapping.exceptions import MappingError
from labthings_fastapi.types.numpy import DenumpifyingDict

from openflexure_microscope_server.fast_ofm_contracts import MappingMotionChecks

from ..camera import BaseCamera
from . import BaseStage
from .mapping_motion import CheckedTracker, MappingMotionCheck
from .moonraker import MoonrakerStage, StageSafetyError


class MoveHistory(NamedTuple):
    """A named tuple containing the position over time for a single move.

    This a named tuple with elements:

    * ``times``
    * ``stage_positions``
    """

    times: List[float]
    stage_positions: List[tuple[int, int, int]]


def _array_to_stage_tuple(pos: np.ndarray) -> tuple[int, int, int]:
    """Convert a numpy array into a tuple of ints.

    :param pos: Input position array must be 3 elements long.
    :return: a tuple of 3 integers
    :raises ValueError: If the array is not of length 3.
    """
    pos_tuple = tuple(int(i) for i in pos)
    if len(pos_tuple) == 3:
        return cast(tuple[int, int, int], pos_tuple)
    raise ValueError("Input array was not 3 elements long.")


def _serialise_numpy_in_dict(dict_with_numpy: dict) -> dict:
    serialised = json.loads(DenumpifyingDict(dict_with_numpy).model_dump_json())
    if not isinstance(serialised, dict):
        raise TypeError(f"Expecting a dictionary to serialise not a {type(serialised)}")
    return serialised


class RecordedMove:
    """Call stage movement and maintain a record of position and time.

    This class is callable, the callable wraps stage.move_to_xyz_position.

    The class records a list of all moves made and how long they took. This is useful
    for calibrating the stage as it allows measuring how long moves take.
    """

    def __init__(
        self, stage: BaseStage, check: MappingMotionCheck | None = None
    ) -> None:
        """Set the stage client used for for movement.

        :param stage: the stage client to be used. ``stage.move_to_xyz_position`` will
            be called whenever the instance is called.
        """
        self._stage = stage
        self.check = check
        self._current_position: Optional[tuple[int, int, int]] = None
        self._history: List[Tuple[float, tuple[int, int, int]]] = []

    def __call__(self, new_position: np.ndarray) -> None:
        """Move to a new position, and record it."""
        self.move(new_position)

    def move(self, new_position: np.ndarray, block_cancellation: bool = False) -> None:
        """Record a checked native measurement or a bounded return to the start."""
        new_stage_pos = _array_to_stage_tuple(new_position)
        starting_pos = self._current_position
        if starting_pos is not None:
            self._history.append((time.time(), starting_pos))
        if self.check is not None and isinstance(self._stage, MoonrakerStage):
            self.check.before_move(
                np.asarray(self._stage.get_xyz_position()), np.asarray(new_stage_pos)
            )
            self._stage.move_absolute_in_segments(new_stage_pos, block_cancellation)
        elif not block_cancellation:
            self._stage.move_to_xyz_position(xyz_pos=new_stage_pos)
        else:
            self._stage.move_absolute(
                **dict(zip(("x", "y", "z"), new_stage_pos, strict=True)),
                block_cancellation=block_cancellation,
                backlash_compensation=None,
            )
        self._current_position = new_stage_pos
        self._history.append((time.time(), new_stage_pos))

    @property
    def history(self) -> MoveHistory:
        """The history, as a numpy array of times and another of positions."""
        times: List[float] = [t for t, p in self._history]
        positions: List[tuple[int, int, int]] = [p for t, p in self._history]
        return MoveHistory(times, positions)

    def clear_history(self) -> None:
        """Reset our history to be an empty list."""
        self._history = []


class CSMUncalibratedError(lt.exceptions.InvocationError):
    """An Exception raised if camera stage mapping data is needed but unavailable.

    Camera Stage Mapping data is needed to convert from distances specified in fractions
    of the field of view to distances in motor steps. This is used when clicking on the
    live preview to move, or when performing a scan.
    """


class CameraStageMapper(lt.Thing):
    """A Thing to manage mapping between image and stage coordinates.

    To use this Thing, the stage must have axes named "x", "y", and "z", or must
    override the ``get_xyz_position()`` and ``move_to_xyz_position()`` methods.
    """

    _class_settings = {"validate_properties_on_set": True}

    _cam: BaseCamera = lt.thing_slot()
    _stage: BaseStage = lt.thing_slot()

    motion_checks: MappingMotionChecks = lt.setting(default_factory=MappingMotionChecks)
    """Physical/image checks for Moonraker mapping, performed inside the same action."""

    def calibrate_1d(self, direction: Tuple[int, int, int]) -> dict:
        """Move a microscope's stage in 1D, and figure out the relationship with the camera."""
        starting_position = self._stage.position
        starting_xyz = self._stage.get_xyz_position()
        check = None
        if isinstance(self._stage, MoonrakerStage):
            if direction not in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)):
                raise MappingError("Moonraker mapping requires one X or Y direction")
            axis = next(i for i, value in enumerate(direction) if value)
            axis_name = ("x", "y", "z")[axis]
            units = self._stage.hardware_settings["axes"][axis_name]["units_per_mm"]
            check = MappingMotionCheck(
                starting_xyz, axis, direction[axis], units, self.motion_checks
            )
        recorded_move = RecordedMove(self._stage, check)
        tracker_args = (
            self._cam.capture_downsampled_array,
            self._stage.get_xyz_position,
        )
        tracker = (
            CheckedTracker(*tracker_args, settle=self._cam.settle, check=check)
            if check is not None
            else Tracker(*tracker_args, settle=self._cam.settle)
        )
        direction_array: np.ndarray = np.array(direction)

        try:
            result: dict = calibrate_backlash_1d(
                tracker, recorded_move, direction_array, logger=self.logger
            )
        except (MappingError, lt.exceptions.InvocationCancelledError) as error:
            self.logger.info(
                "Returning to starting position after mapping stopped: %s", error
            )
            try:
                if check is not None:
                    recorded_move.move(
                        np.asarray(starting_xyz), block_cancellation=True
                    )
                else:
                    self._stage.move_absolute(
                        **starting_position,
                        block_cancellation=True,
                        backlash_compensation=None,
                    )
            except Exception as return_error:
                raise StageSafetyError(
                    f"Mapping stopped: {error}. Return to start also failed: {return_error}"
                ) from return_error
            raise
        except StageSafetyError as error:
            # The upstream finally-return may fail while another exception is
            # active. Keep that original diagnosis; never retry an uncertain move.
            context: BaseException | None = error
            seen: set[int] = set()
            while context is not None and id(context) not in seen:
                seen.add(id(context))
                if isinstance(context, MappingError):
                    raise StageSafetyError(
                        f"Mapping stopped: {context}. Return failed: {error}"
                    ) from error
                context = context.__cause__ or context.__context__
            raise
        if check is not None:
            result["motion_check"] = check.result(float(result["pixels_per_step"]))
        result["move_history"] = recorded_move.history
        result["image_resolution"] = self._cam.capture_downsampled_array().shape[:2]
        return result

    @lt.action
    def calibrate_xy(self) -> dict:
        """Move the microscope's stage in X and Y, to calibrate its relationship to the camera.

        This performs two 1d calibrations in x and y, then combines their results.
        """
        downsampling_factor = self._cam.downsampled_array_factor
        geometry = self._mapping_geometry()
        if geometry is not None and any(
            size % downsampling_factor for size in geometry["image_resolution"]
        ):
            raise MappingError(
                "CSM downsampling must divide the camera image dimensions"
            )
        # Calibrate y-axis first as it is more likely to fail.
        # The x-y difference is due to the camera aspect ratio, not the stage hardware.
        self.logger.info("Calibrating Y axis:")
        cal_y: dict = self.calibrate_1d((0, 1, 0))
        self._check_calibration_geometry(geometry, cal_y, downsampling_factor)
        self.logger.info("Calibrating X axis:")
        cal_x: dict = self.calibrate_1d((1, 0, 0))
        self._check_calibration_geometry(geometry, cal_x, downsampling_factor)
        self.logger.info("Calibration complete, updating metadata.")

        # Combine X and Y calibrations to make a 2D calibration
        cal_xy: dict = image_to_stage_displacement_from_1d([cal_x, cal_y])
        # Correct the result for downsampling performed in the hardware interface
        # (this may be to speed up correlation, or to avoid debayering artifacts)
        cal_xy["image_to_stage_displacement"] /= downsampling_factor
        corrected_resolution = tuple(
            r * downsampling_factor for r in cal_x["image_resolution"]
        )

        csm_matrix = cal_xy["image_to_stage_displacement"]
        csm_as_string = (
            f"[{round(csm_matrix[0][0], 2)}, {round(csm_matrix[0][1], 2)},],"
            f"[{round(csm_matrix[1][0], 2)}, {round(csm_matrix[1][1], 2)}]"
        )
        self.logger.info(f"CSM matrix is {csm_as_string}.")

        data = {
            "camera_stage_mapping_calibration": cal_xy,
            "linear_calibration_x": cal_x,
            "linear_calibration_y": cal_y,
            "downsampled_image_resolution": cal_x["image_resolution"],
            "image_resolution": corrected_resolution,
            "downsampling": downsampling_factor,
        }

        data = _serialise_numpy_in_dict(data)
        if geometry is not None:
            data["camera_geometry"] = geometry
        self.last_calibration = data

        return data

    def _mapping_geometry(self) -> dict | None:
        """Read the optional camera geometry without hardware or settings effects."""
        geometry = self._cam.mapping_geometry
        return (
            json.loads(json.dumps(geometry, allow_nan=False))
            if isinstance(geometry, dict)
            else None
        )

    def _check_calibration_geometry(
        self, geometry: dict | None, result: dict, downsampling: int
    ) -> None:
        """Reject changed geometry or unexpected frame size before saving a new fit."""
        if geometry is None:
            return
        if (
            geometry != self._mapping_geometry()
            or downsampling != self._cam.downsampled_array_factor
            or [size * downsampling for size in result["image_resolution"]]
            != geometry["image_resolution"]
        ):
            raise MappingError("Camera geometry changed during Camera Stage Mapping")

    def _geometry_scale_from_calibration(self) -> float | None:
        """Return the uniform output-pixel scale for compatible camera geometry.

        CSM is calibrated in stage units per image pixel.  The HQ ``default`` and
        ``full_resolution`` streams use the same sensor mode and ROI, but sample that
        field with different output pixel counts.  Such a change is safe when both
        image axes scale equally: divide the calibrated matrix by that scale.  A crop,
        sensor, bit-depth, aspect-ratio, or malformed-calibration change remains stale.
        """
        geometry = self._mapping_geometry()
        if geometry is None:
            return 1.0
        if self.last_calibration is None:
            return None
        calibrated_geometry = self.last_calibration.get("camera_geometry")
        calibrated_resolution = self.last_calibration.get("image_resolution")
        identity_keys = ("camera", "sensor", "sensor_resolution", "bit_depth", "roi")
        geometry_matches = (
            isinstance(calibrated_geometry, dict)
            and calibrated_resolution == calibrated_geometry.get("image_resolution")
            and all(
                calibrated_geometry.get(key) == geometry.get(key)
                for key in identity_keys
            )
        )
        current_resolution = geometry.get("image_resolution")
        resolutions_are_valid = (
            isinstance(calibrated_resolution, (list, tuple))
            and isinstance(current_resolution, (list, tuple))
            and len(calibrated_resolution) == len(current_resolution) == 2
            and all(type(value) is int and value > 0 for value in calibrated_resolution)
            and all(type(value) is int and value > 0 for value in current_resolution)
        )
        if not geometry_matches or not resolutions_are_valid:
            return None
        calibrated_size = cast(tuple[int, int], calibrated_resolution)
        current_size = cast(tuple[int, int], current_resolution)
        scales = [
            current / calibrated
            for current, calibrated in zip(current_size, calibrated_size, strict=True)
        ]
        return (
            scales[0] if np.isclose(scales[0], scales[1], rtol=1e-9, atol=0) else None
        )

    def _geometry_matches_calibration(self) -> bool:
        """Keep historical data but never use an incompatible HQ matrix."""
        return self._geometry_scale_from_calibration() is not None

    @lt.property
    def image_to_stage_displacement_matrix(self) -> Optional[List[List[float]]]:
        """A 2x2 matrix that converts displacement in image coordinates to stage coordinates.

        Note that this matrix is defined using "matrix coordinates", i.e. image coordinates
        may be (y,x). This is an artifact of the way numpy, opencv, etc. define images. If
        you are making use of this matrix in your own code, you will need to take care of
        that conversion.

        It is often helpful to give a concrete example: to make a move in image coordinates
        (``dy``, ``dx``), where ``dx`` is horizontal, i.e. the longer dimension of the image, you
        should move the stage by:

        .. code-block:: python

            stage_disp = np.dot(
                np.array([dy,dx]),
                np.array(image_to_stage_displacement_matrix),
            )

        """
        if self.last_calibration is None:
            return None
        scale = self._geometry_scale_from_calibration()
        if scale is None:
            return None
        displacement_matrix = self.last_calibration["camera_stage_mapping_calibration"][
            "image_to_stage_displacement"
        ]
        return (np.array(displacement_matrix) / scale).tolist()

    last_calibration: Optional[dict] = lt.setting(default=None, readonly=True)
    """The most recent CSM calibration."""

    @lt.property
    def image_resolution(self) -> Optional[Tuple[float, float]]:
        """The image size used to calibrate the image_to_stage_displacement_matrix."""
        if self.last_calibration is None or not self._geometry_matches_calibration():
            return None
        geometry = self._mapping_geometry()
        return (
            self.last_calibration["image_resolution"]
            if geometry is None
            else geometry["image_resolution"]
        )

    @lt.property
    def calibration_required(self) -> bool:
        """Whether the camera stage mapper needs calibrating."""
        return self.image_to_stage_displacement_matrix is None

    def assert_calibration(self) -> List[List[float]]:
        """Return image_to_stage_displacement matrix or raise error if it's not set."""
        matrix = self.image_to_stage_displacement_matrix
        if matrix is None:
            raise CSMUncalibratedError(
                "Camera Stage Mapping is missing or incompatible with the current "
                "camera geometry. Run the calibration routine on tissue."
            )
        return matrix

    @lt.property
    def can_calibrate(self) -> bool:
        """Whether the Thing permits calibration."""
        return True

    @lt.action
    def move_in_image_coordinates(self, x: float, y: float) -> None:
        """Move by a given number of pixels on the camera.

        NB x and y here refer to what is usually understood to be the horizontal and
        vertical axes of the image. In many toolkits, "matrix indices" are used, which
        swap the order of these coordinates. This includes opencv and PIL. So, don't be
        surprised if you find it necessary to swap x and y around.

        As a general rule, ``x`` usually corresponds to the longer dimension of the image,
        and ``y`` to the shorter one. Checking what shape your chosen toolkit reports for
        an image usually helps resolve any ambiguity.
        """
        self._stage.move_relative(
            **self.convert_image_to_stage_coordinates(x=x, y=y),
            block_cancellation=False,
            backlash_compensation=None,
        )

    @lt.action
    def convert_image_to_stage_coordinates(
        self, x: float, y: float, **_kwargs: float
    ) -> Mapping[str, int]:
        """Convert image coordinates to stage coordinates. Only x and y are returned."""
        csm_matrix = self.assert_calibration()
        return csm_img_to_stage(csm_matrix, x=x, y=y)

    @lt.action
    def convert_stage_to_image_coordinates(
        self, x: int, y: int, **_kwargs: int
    ) -> Mapping[str, float]:
        """Convert stage coordinates to image coordinates. Only x and y are returned."""
        csm_matrix = self.assert_calibration()
        return csm_stage_to_img(csm_matrix, x=x, y=y)

    @lt.property
    def thing_state(self) -> Mapping[str, Any]:
        """Summary metadata describing the current state of the Thing."""
        return {
            k: getattr(self, k)
            for k in ["image_to_stage_displacement_matrix", "image_resolution"]
        }


def csm_img_to_stage(
    matrix: np.ndarray | list[list[float]],
    *,
    x: float | int,
    y: float | int,
    **_kwargs: int,
) -> Mapping[str, int]:
    """Apply any CSM matrix to image coordinates.

    x and y must be kwargs and extra kwargs are ignored, allowing:
    ``csm_img_to_stage(matrix, **position)`` to run for a mapping position.

    Note that x and y are the actual (x, y) of the image, not the (m, n) indices used
    by numpy

    :param matrix: The matrix to use in the calculation
    :param x: the x image coordinate (keyword only)
    :param y: the y image coordinate (keyword only)
    :return: The resulting stage coordinates as a mapping.
    """
    # Note this is (y,x) not (x,y) to put it in numpy image indices
    relative_move: np.ndarray = np.dot(np.array([y, x]), np.array(matrix))
    return {"x": round(relative_move[0]), "y": round(relative_move[1])}


def csm_stage_to_img(
    matrix: np.ndarray | list[list[float]],
    *,
    x: float | int,
    y: float | int,
    **_kwargs: int,
) -> Mapping[str, float]:
    """Apply any CSM matrix to stage coordinates to get image coordinates.

    x and y must be kwargs and extra kwargs are ignored, allowing:
    ``csm_img_to_stage(matrix, **position)`` to run for a mapping position.

    Note that x and y are the actual (x, y) of the image, not the (m, n) indices used
    numpy

    :param matrix: The matrix to use in the calculation
    :param x: the x stage coordinate (keyword only)
    :param y: the y stage coordinate (keyword only)
    :return: The resulting img coordinates as a mapping.
    """
    inverse_matrix = np.linalg.inv(np.array(matrix))
    relative_move = np.dot(np.array([x, y]), inverse_matrix)
    # Note that the relative move is (y, x) as it is from numpy and is in matrix coords.
    return {"x": float(relative_move[1]), "y": float(relative_move[0])}
