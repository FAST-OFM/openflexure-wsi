"""Validate native camera-stage mapping moves using its existing tracking frames."""

from typing import Any

import numpy as np

from camera_stage_mapping.camera_stage_tracker import Tracker
from camera_stage_mapping.exceptions import MappingError

from openflexure_microscope_server.fast_ofm_contracts import MappingMotionChecks


class MappingMotionCheck:
    """Keep one axis/run's scale evidence without requesting extra moves or frames."""

    def __init__(
        self,
        start: tuple[int, int, int],
        axis: int,
        direction: int,
        units_per_mm: float,
        settings: MappingMotionChecks,
    ) -> None:
        """Freeze the operator reference, axis and settings for this run."""
        self.start = np.asarray(start, dtype=float)
        self.axis = axis
        self.direction = direction
        self.units_per_mm = units_per_mm
        self.settings = settings
        self.previous: tuple[np.ndarray, np.ndarray] | None = None
        self.vectors: list[np.ndarray] = []
        self.scale_vector: np.ndarray | None = None
        self.frame_pixels: float | None = None

    def before_move(self, current: np.ndarray, target: np.ndarray) -> None:
        """Reject unverified large probes, lost overlap and excursions before motion."""
        if not np.all(np.isfinite(target)) or target.shape != (3,):
            raise MappingError("Mapping target must contain three finite coordinates")
        other_axes = [i for i in range(3) if i != self.axis]
        if np.any(target[other_axes] != self.start[other_axes]):
            raise MappingError("Mapping attempted to change a non-calibrated axis")
        excursion = abs(target[self.axis] - self.start[self.axis]) / self.units_per_mm
        if excursion > self.settings.max_excursion_mm:
            raise MappingError(
                f"Mapping excursion {excursion:.6f} mm exceeds "
                f"{self.settings.max_excursion_mm:.6f} mm from its starting point"
            )
        # Return is transit, not a new measurement. The stage validates the entire
        # path and splits it into bounded commands, including error cleanup.
        if np.array_equal(target, self.start):
            return
        delta = abs(target[self.axis] - current[self.axis])
        if self.scale_vector is None:
            if delta / self.units_per_mm > self.settings.unverified_step_mm:
                raise MappingError(
                    "Image scale is not yet consistent; requested probe "
                    f"{delta / self.units_per_mm:.6f} mm exceeds unverified limit "
                    f"{self.settings.unverified_step_mm:.6f} mm. Check image texture/focus."
                )
        elif self.frame_pixels is not None:
            predicted = delta * float(np.linalg.norm(self.scale_vector))
            limit = self.frame_pixels * self.settings.max_step_frame_fraction
            if predicted > limit:
                raise MappingError(
                    f"Mapping move predicts {predicted:.1f} pixels, above the "
                    f"{limit:.1f}-pixel image-overlap limit"
                )

    def observe(
        self, stage: np.ndarray, image: np.ndarray, frame_shape: tuple[int, ...]
    ) -> None:
        """Reuse a native tracker sample; reversals may contain the measured backlash."""
        stage, image = np.asarray(stage, dtype=float), np.asarray(image, dtype=float)
        if not np.all(np.isfinite(stage)) or not np.all(np.isfinite(image)):
            raise MappingError("Non-finite camera-stage mapping observation")
        self.frame_pixels = float(min(frame_shape[:2]))
        previous, self.previous = self.previous, (stage.copy(), image.copy())
        if previous is None:
            return
        stage_delta = stage[self.axis] - previous[0][self.axis]
        image_delta = image - previous[1]
        shift = float(np.linalg.norm(image_delta))
        if shift > self.frame_pixels * self.settings.max_step_frame_fraction:
            raise MappingError("Observed image shift exceeds the mapping overlap limit")
        if (
            self.scale_vector is not None
            or stage_delta * self.direction <= 0
            or shift < self.settings.min_shift_px
        ):
            return
        self.vectors.append(image_delta / stage_delta)
        self.vectors = self.vectors[-2:]
        if len(self.vectors) == 2:
            centre = np.mean(self.vectors, axis=0)
            magnitude = float(np.linalg.norm(centre))
            smaller = min(float(np.linalg.norm(vector)) for vector in self.vectors)
            if (
                magnitude > 0
                and smaller > 0
                and (
                    np.linalg.norm(self.vectors[1] - self.vectors[0]) / smaller
                    <= self.settings.scale_tolerance
                )
            ):
                self.scale_vector = centre

    def result(self, fitted_scale: float | None = None) -> dict[str, Any]:
        """Report the scale actually checked during this invocation, not an old mapping."""
        if self.scale_vector is None:
            raise MappingError("Camera-stage image scale was not verified in this run")
        scale = float(np.linalg.norm(self.scale_vector))
        if fitted_scale is not None and (
            not np.isfinite(fitted_scale)
            or abs(abs(fitted_scale) - scale) / scale > self.settings.scale_tolerance
        ):
            raise MappingError(
                "Final fitted scale disagrees with the initial mapping frames"
            )
        return {
            "pixels_per_mm": scale * self.units_per_mm,
            "settings": self.settings.model_dump(),
        }


class CheckedTracker(Tracker):
    """Native tracker plus observation validation, without additional frame capture."""

    def __init__(self, *args: Any, check: MappingMotionCheck, **kwargs: Any) -> None:
        """Use the same capture/position/settle callbacks as the native tracker."""
        self.check = check
        super().__init__(*args, **kwargs)

    def append_point(self, settle: bool = True, image: Any = None) -> Any:
        """Validate the point captured by the unmodified native implementation."""
        stage_position, image_position = super().append_point(
            settle=settle, image=image
        )
        self.check.observe(stage_position, image_position, self.image_shape)
        return stage_position, image_position
