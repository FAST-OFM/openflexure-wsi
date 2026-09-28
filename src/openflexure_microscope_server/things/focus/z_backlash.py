"""Standalone WHITE Z-backlash calibration using the existing camera and stage."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

import labthings_fastapi as lt

from openflexure_microscope_server.fast_ofm_contracts import (
    CalibrationManifest,
    CalibrationStage,
)
from openflexure_microscope_server.focus.z_backlash import (
    ZBacklashEstimate,
    estimate_z_backlash,
)
from openflexure_microscope_server.focus.z_backlash_calibration import (
    FocusPeak,
    FocusSample,
    ZCalibrationSettings,
    abba_observation,
    focus_peak,
)

from .. import OFMThing
from ..camera import BaseCamera
from ..illumination import MoonrakerIllumination
from ..stage.moonraker import MoonrakerStage


def atomic_text(path: Path, contents: str) -> None:
    """Replace one owned file only after its full contents have been flushed."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def measurement_binding(
    stage: MoonrakerStage, camera: BaseCamera, settings: ZCalibrationSettings
) -> dict:
    """Record geometry/dynamics, excluding non-persistent zero and coordinates."""
    z = stage.hardware_settings["axes"]["z"]
    configuration = getattr(camera, "focus_configuration", None)
    if not isinstance(configuration, dict) or not callable(
        getattr(camera, "capture_settled_frame", None)
    ):
        raise ValueError("Camera does not support fresh WHITE calibration captures")
    return {
        "stage_z": {
            key: z[key]
            for key in (
                "units_per_mm",
                "direction_sign",
                "speed_mm_s",
                "accel_mm_s2",
                "settle_ms",
            )
        },
        "camera": configuration,
        "mechanics_id": settings.mechanics_id,
        "optics_id": settings.optics_id,
    }


class ZBacklashCalibration(OFMThing):
    """Calibrate Z from Settings, without depending on R/G or the scan workflow."""

    _class_settings = {"validate_properties_on_set": True}
    _cam: BaseCamera = lt.thing_slot()
    _stage: MoonrakerStage = lt.thing_slot()
    _illumination: MoonrakerIllumination = lt.thing_slot()

    parameters: ZCalibrationSettings = lt.setting(
        default_factory=ZCalibrationSettings, readonly=True
    )
    """Saved measurement parameters; dynamics stay in the existing stage profile."""

    last_calibration: dict | None = lt.setting(default=None, readonly=True)
    """Last independently checked result, never overwritten by a failed attempt."""

    progress: dict = lt.property(
        default_factory=lambda: {"phase": "idle", "frames": 0}, readonly=True
    )
    """Current stage and capture count; progress logs also appear in the action modal."""

    def save_settings(self) -> None:
        """Keep native OFM settings and filename, with atomic replacement for this Thing."""
        if self._disable_saving_settings:
            return
        contents = self.settings.model_instance.model_dump_json(indent=2)
        atomic_text(Path(self._thing_server_interface.settings_file_path), contents)

    def _commit_setting(self, name: str, value: Any) -> None:
        old = getattr(self, name)
        try:
            setattr(self, name, value)
        except Exception:
            disabled = self._disable_saving_settings
            self._disable_saving_settings = True
            try:
                setattr(self, name, old)
            finally:
                self._disable_saving_settings = disabled
            raise

    @lt.action
    def set_parameters(self, parameters: ZCalibrationSettings) -> ZCalibrationSettings:
        """Save validated settings without moving, switching light or changing zero."""
        self._commit_setting("parameters", parameters)
        return self.parameters

    @lt.property
    def readiness(self) -> dict:
        """Report preparation requirements; read-only checks never arm or set zero."""
        try:
            measurement_binding(self._stage, self._cam, self.parameters)
            self._stage.validate_path([])
            light = self._illumination.state
            if not light.available or light.mode != "white":
                raise ValueError("WHITE illumination must be on")
            if not self._cam.stream_active:
                raise ValueError("Camera preview must be running")
        except Exception as exc:
            return {"ready": False, "reason": str(exc)}
        return {"ready": True, "reason": "Confirm tissue, focus and exclusive control"}

    @lt.property
    def calibration_status(self) -> dict:
        """Report saved result compatibility without restoring a reference."""
        profile = self.last_calibration
        if profile is None:
            return {"status": "not_calibrated", "reason": "No saved Z calibration"}
        try:
            estimate = ZBacklashEstimate.model_validate_json(
                json.dumps(profile["estimate"])
            )
            settings = ZCalibrationSettings.model_validate_json(
                json.dumps(profile["parameters"])
            )
            peaks = [
                FocusPeak.model_validate_json(json.dumps(value))
                for value in profile["validation_peaks"]
            ]
            if profile["validated"] is not True or len(peaks) != 2:
                raise ValueError("Independent preload verification is missing")
            if any(
                abs(peak.z_um - profile["target_z_um"]) > settings.maximum_residual_um
                for peak in peaks
            ):
                raise ValueError("Stored preload validation failed")
            if abs(peaks[0].z_um - peaks[1].z_um) > settings.maximum_residual_um:
                raise ValueError("Stored validation starting sides disagree")
            if profile["binding"] != measurement_binding(
                self._stage, self._cam, self.parameters
            ):
                raise ValueError("Camera, mechanics or motion profile changed")
            if profile["preload_um"] < estimate.preload_candidate_um:
                raise ValueError("Stored preload is below the tested estimate")
        except Exception as exc:
            return {"status": "incompatible", "reason": str(exc)}
        return {
            "status": "valid",
            "reason": "Verified calibration saved; no global compensation enabled",
            "compensation_enabled": False,
        }

    @lt.property
    def manifest(self) -> CalibrationManifest:
        """Describe the real action phases, reusing the shared manifest contract."""
        phases = [
            (
                "preflight",
                "Readiness and path",
                ["parameters", "stage", "illumination"],
                ["binding", "start_position"],
                "Whole path is within current limits",
            ),
            (
                "reference",
                "WHITE tissue reference",
                ["camera", "parameters.roi"],
                ["reference_frame"],
                "Operator-confirmed tissue has signal and texture",
            ),
            (
                "measurement",
                "Both Z approaches",
                ["reference_frame", "parameters"],
                ["curves", "observations"],
                "Ordered +/−/−/+ WHITE curves pass peak/drift checks",
            ),
            (
                "estimate",
                "Direction difference",
                ["observations", "parameters"],
                ["estimate"],
                "Uncertainty and proposed preload pass configured limits",
            ),
            (
                "validation",
                "Preload verification",
                ["estimate", "parameters"],
                ["validation_peaks"],
                "Independent starts from both sides pass residual limits",
            ),
            (
                "save",
                "Return and save",
                ["validation_peaks", "binding"],
                ["last_calibration"],
                "Return confirmed; report and setting saved",
            ),
        ]
        return CalibrationManifest(
            id="z_backlash",
            name="Z backlash",
            description="Standalone WHITE calibration with independent preload verification.",
            prerequisites=["operator_prepared", "valid_stage_reference", "white_light"],
            parameter_refs=list(ZCalibrationSettings.model_fields),
            stages=[
                CalibrationStage(
                    id=phase,
                    name=name,
                    description=criterion,
                    inputs=inputs,
                    outputs=outputs,
                    action="calibrate",
                    success_criterion=criterion,
                    timeout_setting="timeout_s",
                    cancellation="Stop after the current bounded operation; preserve prior profile; no automatic return on failure.",
                    hardware_required=phase not in ("estimate",),
                )
                for phase, name, inputs, outputs, criterion in phases
            ],
            results=["last_calibration", "calibration_status"],
            acceptance_criteria=[
                "Both approaches measured independently of R/G",
                "Preload verified from both starting sides",
                "Fresh WHITE frames and configured uncertainty/residual bounds",
                "Prior profile preserved on error or cancellation",
            ],
            invalidated_by=["mechanics_id", "optics_id", "stage_z", "camera"],
        )

    @lt.action
    def calibrate(self, prepared: bool = False) -> dict:
        """Run bounded WHITE measurements; success returns to start and saves the profile.

        The operator must place tissue in the ROI, focus, verify Z clearance and
        avoid all other movement controls. Cancellation/error leaves the last
        confirmed position and preserves the previous calibration.
        """
        if not prepared:
            raise ValueError("Confirm tissue, focus, Z clearance and exclusive control")
        ready = self.readiness
        if not ready["ready"]:
            raise ValueError(ready["reason"])
        settings = self.parameters.model_copy(deep=True)
        directory = Path(self.data_dir) / str(uuid4())
        directory.mkdir()
        run = CalibrationRun(self, settings, directory)
        try:
            result = run.execute()
            lt.raise_if_cancelled()
            self._commit_setting("last_calibration", result)
            self.progress = {"phase": "complete", "frames": run.frame_count}
            self.logger.info(
                "Z calibration saved. Returned to commanded start; motors and zero unchanged."
            )
            return result
        except BaseException as exc:
            self.progress = {"phase": "stopped", "frames": run.frame_count}
            self.logger.warning(
                "Z calibration stopped; previous profile retained. No automatic return: %s",
                exc,
            )
            run.record_failure(str(exc))
            raise


class CalibrationRun:
    """One invocation's bounded data acquisition; not a second workflow engine."""

    def __init__(
        self,
        owner: ZBacklashCalibration,
        settings: ZCalibrationSettings,
        directory: Path,
    ) -> None:
        """Snapshot configuration and coordinates once, before the first move."""
        self.owner = owner
        self.settings = settings
        self.directory = directory
        self.stage = owner._stage
        self.camera = owner._cam
        self.start = self.stage.get_xyz_position()
        self.binding = measurement_binding(self.stage, self.camera, settings)
        self.units_per_um = self.binding["stage_z"]["units_per_mm"] / 1000
        self.deadline = time.monotonic() + settings.timeout_s
        self.frame_count = 0
        self.metadata: dict | None = None
        self.frames: list[dict] = []
        self.curves: list[dict] = []
        self.last_exposure_ns = -1

    def _check(self) -> None:
        lt.raise_if_cancelled()
        if time.monotonic() > self.deadline:
            raise TimeoutError("Z calibration exceeded its total timeout")
        self.stage.validate_path([])
        if self.binding != measurement_binding(self.stage, self.camera, self.settings):
            raise ValueError("Camera or stage configuration changed during calibration")
        light = self.owner._illumination.state
        if not light.available or light.mode != "white":
            raise ValueError("WHITE illumination changed during calibration")

    def _phase(self, phase: str) -> None:
        self._check()
        self.owner.progress = {"phase": phase, "frames": self.frame_count}
        self.owner.logger.info("Z calibration: %s (%s frames)", phase, self.frame_count)

    def _go(self, z_um: float) -> None:
        self._check()
        z_units = self.start[2] + round(z_um * self.units_per_um)
        self.stage.move_absolute_in_segments((self.start[0], self.start[1], z_units))

    def _capture(self, z_um: float) -> FocusSample:
        self._check()
        if self.frame_count >= self.settings.maximum_frames:
            raise ValueError("Z capture budget exhausted")
        timeout = min(self.settings.frame_timeout_s, self.deadline - time.monotonic())
        capture = getattr(self.camera, "capture_settled_frame", None)
        if not callable(capture):
            raise ValueError("Camera lost fresh-capture capability")
        image, metadata = capture(timeout)
        self._check()
        exposure = metadata["exposure_start_ns"]
        if exposure <= self.last_exposure_ns:
            raise ValueError("Duplicate or out-of-order WHITE exposure")
        self.last_exposure_ns = exposure
        identity = {
            key: metadata[key]
            for key in (
                "exposure_time_us",
                "analogue_gain",
                "colour_gains",
                "scaler_crop",
                "image_size",
            )
        }
        if self.metadata is not None and identity != self.metadata:
            raise ValueError("WHITE exposure or image geometry changed")
        self.metadata = identity
        if self.frame_count == 0:
            # Keep the actual reference even when ROI quality rejects the attempt.
            # This supports choosing a valid tissue region without blind repeat runs.
            Image.fromarray(image).save(self.directory / "reference.png")
            atomic_text(
                self.directory / "reference.json",
                json.dumps(
                    {
                        "metadata": metadata,
                        "binding": self.binding,
                        "parameters": self.settings.model_dump(mode="json"),
                    },
                    indent=2,
                    allow_nan=False,
                ),
            )
        roi = self.settings.roi
        height, width = image.shape[:2]
        patch = image[
            round(roi.y * height) : round((roi.y + roi.height) * height),
            round(roi.x * width) : round((roi.x + roi.width) * width),
        ]
        if min(patch.shape[:2]) < 32:
            raise ValueError("Tissue ROI is too small")
        gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY).astype(np.float64) / 255
        if float(np.mean(gray)) < self.settings.minimum_signal:
            raise ValueError("Insufficient WHITE signal in tissue ROI")
        if (
            float(np.mean(np.any(patch >= 254, axis=2)))
            > self.settings.maximum_saturation_fraction
        ):
            raise ValueError("Tissue ROI is saturated")
        score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if not math.isfinite(score) or (
            self.frame_count == 0 and score < self.settings.minimum_texture
        ):
            raise ValueError("Insufficient WHITE texture in tissue ROI")
        frame = f"frame-{self.frame_count:04d}.png"
        Image.fromarray(patch).save(self.directory / frame)
        self.frames.append({"frame": frame, "z_um": z_um, "metadata": metadata})
        self.frame_count += 1
        self.owner.progress = {"phase": "capture", "frames": self.frame_count}
        return FocusSample(z_um=z_um, time_s=exposure / 1e9, score=score, frame=frame)

    def _curve(
        self, positions: list[float], *, preload: float, approach: int
    ) -> FocusPeak:
        self._go(positions[0] - approach * preload)
        samples = []
        for position in positions:
            self._go(position)
            samples.append(self._capture(position))
        peak = focus_peak(samples, self.settings)
        self.curves.append(
            {
                "kind": "measurement",
                "approach": approach,
                "samples": [sample.model_dump() for sample in samples],
                "peak": peak.model_dump(),
            }
        )
        return peak

    def _verify(
        self, target_um: float, preload_units: int, start_side: int
    ) -> FocusPeak:
        settings = self.settings
        n = round(settings.validation_half_range_um / settings.step_um)
        positions = [target_um + i * settings.step_um for i in range(-n, n + 1)]
        samples = []
        for position in positions:
            self._go(position + start_side * settings.preload_um)
            self._check()
            self.stage.move_z_with_preload(
                self.start[2] + round(position * self.units_per_um),
                preload_units,
                settings.preferred_final_approach_sign,
            )
            samples.append(self._capture(position))
        peak = focus_peak(samples, settings)
        self.curves.append(
            {
                "kind": "validation",
                "start_side": start_side,
                "samples": [sample.model_dump() for sample in samples],
                "peak": peak.model_dump(),
            }
        )
        if abs(peak.z_um - target_um) > settings.maximum_residual_um:
            raise ValueError(
                "Independent Z preload residual exceeds the configured limit"
            )
        return peak

    def execute(self) -> dict:
        """Acquire ABBA evidence, verify preload from both sides, then return and save."""
        settings = self.settings
        self._phase("preflight")
        for value in (settings.span_um / 2, settings.step_um, settings.preload_um):
            if not math.isclose(
                value * self.units_per_um, round(value * self.units_per_um)
            ):
                raise ValueError("Z grid must be representable in stage units")
        extent = (
            settings.span_um / 2
            + settings.validation_half_range_um
            + settings.preload_um
        )
        self.stage.validate_path(
            [
                {"z": self.start[2] + round(sign * extent * self.units_per_um)}
                for sign in (-1, 1)
            ]
        )
        self._phase("reference")
        self._capture(0.0)
        self._phase("measurement")
        n = round(settings.span_um / settings.step_um)
        positions = [-settings.span_um / 2 + i * settings.step_um for i in range(n + 1)]
        observations = []
        for cycle in range(settings.cycles):
            peaks = []
            for approach in (1, -1, -1, 1):
                self.owner.logger.info(
                    "Z cycle %s/%s, approach %+d", cycle + 1, settings.cycles, approach
                )
                ordered = positions if approach == 1 else list(reversed(positions))
                peaks.append(
                    self._curve(ordered, preload=settings.preload_um, approach=approach)
                )
            observations.append(abba_observation(f"cycle-{cycle}", peaks, settings))
        self._phase("estimate")
        estimate = estimate_z_backlash(
            observations,
            policy=settings.policy(),
            preferred_final_approach_sign=settings.preferred_final_approach_sign,
        )
        preload_units = math.ceil(estimate.preload_candidate_um * self.units_per_um)
        actual_preload = preload_units / self.units_per_um
        if actual_preload > settings.preload_um:
            raise ValueError(
                "Estimated preload exceeds the tested preparation range; no automatic expansion"
            )
        preferred = [
            sample.positive_focus_um
            if settings.preferred_final_approach_sign == 1
            else sample.negative_focus_um
            for sample in observations
        ]
        target = round(median(preferred) * self.units_per_um) / self.units_per_um
        self._phase("validation")
        validation = [self._verify(target, preload_units, side) for side in (-1, 1)]
        if abs(validation[0].z_um - validation[1].z_um) > settings.maximum_residual_um:
            raise ValueError("Z validation starting sides disagree")
        self._phase("save")
        self._go(0.0)
        self._check()
        result = {
            "id": self.directory.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "method": "white_abba_focus_preload",
            "validated": True,
            "parameters": settings.model_dump(mode="json"),
            "binding": self.binding,
            "frame_configuration": self.metadata,
            "estimate": estimate.model_dump(mode="json"),
            "target_z_um": target,
            "preload_um": actual_preload,
            "validation_peaks": [peak.model_dump() for peak in validation],
            "maximum_residual_um": max(abs(peak.z_um - target) for peak in validation),
            "start_position_units": list(self.start),
            "returned_to_commanded_start": True,
            "data_path": self.directory.name,
            "frame_count": self.frame_count,
        }
        atomic_text(
            self.directory / "report.json",
            json.dumps(
                {
                    "result": result,
                    "curves": self.curves,
                    "frames": self.frames,
                },
                indent=2,
                allow_nan=False,
            ),
        )
        return result

    def record_failure(self, error: str) -> None:
        """Keep partial evidence when possible without hiding the original failure."""
        try:
            atomic_text(
                self.directory / "failure.json",
                json.dumps(
                    {
                        "error": error,
                        "curves": self.curves,
                        "frames": self.frames,
                        "previous_profile_preserved": True,
                        "automatic_return": False,
                    },
                    indent=2,
                    allow_nan=False,
                ),
            )
        except Exception:
            self.owner.logger.exception("Could not save partial Z calibration evidence")
