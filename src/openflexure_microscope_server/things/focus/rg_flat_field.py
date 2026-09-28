"""R/G flat-field actions in the existing OFM camera/settings lifecycle."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, cast
from uuid import UUID, uuid4

import numpy as np
from fastapi import HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import StrictInt

import labthings_fastapi as lt

from openflexure_microscope_server.fast_ofm_contracts import (
    CalibrationManifest,
    CalibrationStage,
)
from openflexure_microscope_server.focus.rg.rg_flat_field import (
    JPEG_DOMAIN,
    FlatFieldSettings,
    Mode,
    channel_indices,
    check_processing,
    common_measurement_plane,
    processing_binding,
)
from openflexure_microscope_server.integrations.fast_ofm_core import (
    FastOFMCoreBlockingProcess,
    FastOFMCoreError,
)
from openflexure_microscope_server.integrations.fast_ofm_core.artifacts import (
    read_flat_field_result,
    write_flat_field_input,
)

from .. import OFMThing
from ..camera import BaseCamera
from ..illumination import LightState, MoonrakerIllumination
from ..stage.moonraker import ControllerState, MoonrakerStage

CALIBRATION_METHOD = "phase_matched_processed_jpeg_measured_dark"


def _crop_raw_planes(
    planes: np.ndarray, roi: tuple[int, int, int, int]
) -> np.ndarray:
    """Copy one checked native-plane rectangle for an owned capture artifact."""
    value = np.asarray(planes)
    if (
        value.ndim != 3
        or value.shape[0] != 4
        or value.size == 0
        or not np.isfinite(value).all()
        or np.min(value) < 0
    ):
        raise ValueError("Expected four finite nonnegative RAW Bayer planes")
    value = value.astype(np.float32, copy=False)
    x, y, width, height = roi
    if min(x, y) < 0 or min(width, height) < 16:
        raise ValueError("RAW processing ROI is invalid")
    if y + height > value.shape[1] or x + width > value.shape[2]:
        raise ValueError("RAW plane ROI exceeds the captured planes")
    return np.array(value[:, y : y + height, x : x + width], copy=True)


@dataclass(frozen=True)
class RGFocusJPEGHandoff:
    """Owned same-request JPEG ROI inputs released only after WHITE cleanup."""

    capture: dict[str, Any]
    metadata: dict[str, dict[str, Any]]
    white_rgb: np.ndarray
    red_source_jpeg8: np.ndarray
    green_source_jpeg8: np.ndarray


def _owned_readonly(value: np.ndarray) -> np.ndarray:
    """Detach one acquisition array and prevent downstream mutation."""
    result = np.array(value, copy=True, order="C")
    result.setflags(write=False)
    return result


def write_json(path: Path, value: dict) -> None:
    """Atomically publish one owned JSON file; partial writes cannot replace it."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def file_hash(path: Path) -> str:
    """Hash maps on save/use, not on every UI status poll."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@contextmanager
def acquisition_session(owner: Any, prepared: bool) -> Iterator[Acquisition]:
    """Share one camera/light safety boundary between separate calibration Things."""
    if not prepared:
        raise ValueError("Confirm an empty field and exclusive microscope control")
    controller, light = owner._readiness_snapshot()
    run = Acquisition(owner, initial_controller=controller, initial_light=light)
    failure: BaseException | None = None
    try:
        yield run
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            with run.timed("white_restore"):
                run.restore_white()
                run.report["white_restored"] = True
        except BaseException as cleanup:
            run.report["restore_error"] = str(cleanup)
            if failure is None:
                failure = cleanup
                raise
            failure.add_note(f"WHITE/position restoration check: {cleanup}")
            owner.logger.error("R/G cleanup failed: %s", cleanup)
        finally:
            run.report["error"] = None if failure is None else str(failure)
            run.report["elapsed_s"] = time.monotonic() - run.started
            try:
                write_json(run.directory / "acquisition.json", run.report)
            except BaseException as report_error:
                if failure is None:
                    failure = report_error
                    raise
                failure.add_note(
                    f"Acquisition report persistence failed: {report_error}"
                )
                owner.logger.error(
                    "Cannot save R/G acquisition report: %s", report_error
                )
            finally:
                owner.progress = {
                    "phase": "stopped" if failure else "captured",
                    "frames": len(run.report["frames"]),
                    "id": run.id,
                }


class RGFlatField(OFMThing):
    """Calibrate separate empirical JPEG maps; never replace WHITE or sensor FOV."""

    _class_settings = {"validate_properties_on_set": True}
    _cam: BaseCamera = lt.thing_slot()
    _stage: MoonrakerStage = lt.thing_slot()
    _illumination: MoonrakerIllumination = lt.thing_slot()

    parameters: FlatFieldSettings = lt.setting(
        default_factory=FlatFieldSettings, readonly=True
    )
    profiles: dict = lt.setting(default_factory=dict, readonly=True)
    last_check: dict = lt.setting(default_factory=dict, readonly=True)
    disabled_modes: list[Mode] = lt.setting(default_factory=list, readonly=True)

    @property
    def measurement_domain(self) -> str:
        """The only implemented acquisition domain, including before preset migration."""
        return JPEG_DOMAIN

    progress: dict = lt.property(
        default_factory=lambda: {"phase": "idle", "frames": 0}, readonly=True
    )

    def _core_process(self) -> FastOFMCoreBlockingProcess:
        """Return one warm standalone core process scoped to this owner's data."""
        root = Path(self.data_dir).resolve()
        existing = getattr(self, "_fast_ofm_core_process", None)
        existing_root = getattr(self, "_fast_ofm_core_root", None)
        if isinstance(existing, FastOFMCoreBlockingProcess) and existing_root == root:
            return existing
        if isinstance(existing, FastOFMCoreBlockingProcess):
            existing.close()
        process = FastOFMCoreBlockingProcess(artifact_roots=(root,))
        object.__setattr__(self, "_fast_ofm_core_process", process)
        object.__setattr__(self, "_fast_ofm_core_root", root)
        return process

    def _external_flat_fit(
        self,
        directory: Path,
        label: str,
        flat: np.ndarray,
        dark: np.ndarray,
        settings: FlatFieldSettings,
    ) -> tuple[dict[str, np.ndarray], dict]:
        """Fit empirical maps in the separately installed core."""
        exchange = directory / f"core-{label}-fit"
        artifact = write_flat_field_input(
            exchange, "flat-field-fit-input", flat=flat, dark=dark
        )
        response = self._core_process().request(
            "rg.flat_field.fit",
            {"input": artifact, "settings": settings.model_dump(mode="json")},
            timeout_s=max(1.0, settings.frame_timeout_s * 4),
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("report"), dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Flat-field fit result is incomplete"
            )
        maps = read_flat_field_result(
            result.get("maps"),
            directory=exchange,
            expected_arrays={"dark", "gain", "valid"},
        )
        return maps, result["report"]

    def _external_flat_apply(
        self, image: np.ndarray, maps: dict[str, np.ndarray]
    ) -> tuple[np.ndarray, np.ndarray]:
        """Apply one empirical map bundle in the separately installed core."""
        root = Path(self.data_dir)
        with tempfile.TemporaryDirectory(prefix=".core-flat-apply-", dir=root) as name:
            exchange = Path(name)
            artifact = write_flat_field_input(
                exchange,
                "flat-field-apply-input",
                image=image,
                dark=maps["dark"],
                gain=maps["gain"],
                valid=maps["valid"],
            )
            response = self._core_process().request(
                "rg.flat_field.apply",
                {"input": artifact},
                timeout_s=max(1.0, self.parameters.frame_timeout_s * 4),
            )
            result = response.get("result")
            if not isinstance(result, dict):
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Flat-field apply result is incomplete"
                )
            arrays = read_flat_field_result(
                result.get("corrected"),
                directory=exchange,
                expected_arrays={"corrected", "valid"},
            )
        return arrays["corrected"], arrays["valid"]

    def _external_flat_validate(  # noqa: PLR0917
        self,
        directory: Path,
        label: str,
        heldout: np.ndarray,
        maps: dict[str, np.ndarray],
        fit_report: dict,
        settings: FlatFieldSettings,
    ) -> dict:
        """Evaluate independent holdouts in the separately installed core."""
        exchange = directory / f"core-{label}-validate"
        artifact = write_flat_field_input(
            exchange,
            "flat-field-validation-input",
            heldout=heldout,
            dark=maps["dark"],
            gain=maps["gain"],
            valid=maps["valid"],
        )
        response = self._core_process().request(
            "rg.flat_field.validate",
            {
                "input": artifact,
                "settings": settings.model_dump(mode="json"),
                "fit_report": fit_report,
            },
            timeout_s=max(1.0, settings.frame_timeout_s * 4),
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("quality"), dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Flat-field validation result is incomplete"
            )
        return result["quality"]

    def save_settings(self) -> None:
        """Persist native OFM settings atomically, including the active profile pointer."""
        if not self._disable_saving_settings:
            write_json(
                Path(self._thing_server_interface.settings_file_path),
                self.settings.model_instance.model_dump(mode="json"),
            )

    def _commit(self, name: str, value: object) -> None:
        old = getattr(self, name)
        try:
            setattr(self, name, value)
        except BaseException:
            disabled = self._disable_saving_settings
            self._disable_saving_settings = True
            try:
                setattr(self, name, old)
            finally:
                self._disable_saving_settings = disabled
            raise

    @lt.action
    def set_parameters(self, parameters: FlatFieldSettings) -> FlatFieldSettings:
        """Save validated parameters only; no camera, stage or light changes."""
        if parameters.measurement_domain == JPEG_DOMAIN:
            configuration = getattr(self._cam, "jpeg_measurement_configuration", None)
            if not isinstance(configuration, dict):
                raise ValueError(
                    "Camera does not provide JPEG measurement configuration"
                )
            processing_binding(configuration, parameters)
        if self.parameters.measurement_domain != parameters.measurement_domain:
            write_json(
                Path(self.data_dir) / f"parameters-before-jpeg-{uuid4()}.json",
                self.parameters.model_dump(mode="json"),
            )
        self._commit("parameters", parameters)
        return self.parameters

    @lt.action
    def use_jpeg_preset(
        self, processing_roi: tuple[StrictInt, StrictInt, StrictInt, StrictInt]
    ) -> FlatFieldSettings:
        """Explicitly save JPEG8 DN defaults and the chosen common ROI; no capture/light."""
        values = self.parameters.model_dump(mode="json")
        values.update(
            measurement_domain=JPEG_DOMAIN,
            processing_roi=processing_roi,
            minimum_signal_dn=8,
            maximum_dark_signal_dn=32,
        )
        return self.set_parameters(FlatFieldSettings.model_validate(values))

    @lt.action
    def set_enabled(self, mode: Mode, enabled: bool) -> None:
        """Enable/disable one map without deleting its data or changing illumination."""
        if mode not in ("red", "green"):
            raise ValueError("Choose RED or GREEN")
        disabled = set(self.disabled_modes)
        if enabled:
            disabled.discard(mode)
        else:
            disabled.add(mode)
        self._commit("disabled_modes", sorted(disabled))

    @lt.action
    def reset_profile(self, mode: Mode) -> None:
        """Remove only one active pointer; preserve all historical map/check artifacts."""
        if mode not in ("red", "green"):
            raise ValueError("Choose RED or GREEN")
        self._commit(
            "profiles",
            {key: value for key, value in self.profiles.items() if key != mode},
        )

    def _binding(self) -> dict:
        self.parameters.require_jpeg()
        configuration = getattr(self._cam, "jpeg_measurement_configuration", None)
        if not isinstance(configuration, dict):
            raise ValueError(
                "Camera does not support fixed-mode processed JPEG capture"
            )
        return json.loads(
            json.dumps(
                {
                    "camera": processing_binding(configuration, self.parameters),
                    "optics_id": self.parameters.optics_id,
                    "illumination_id": self.parameters.illumination_id,
                }
            )
        )

    @lt.property
    def readiness(self) -> dict:
        """No zero or motor enable is required for an empty-field light/camera test."""
        try:
            self._readiness_snapshot()
        except Exception as exc:
            return {"ready": False, "reason": str(exc)}
        return {"ready": True, "reason": "Remove the slide; no stage movement"}

    def _readiness_snapshot(self) -> tuple[ControllerState, LightState]:
        """Return the exact WHITE snapshot accepted by the session preflight."""
        self._binding()
        controller, light = self._illumination._read_controller_and_light()
        if controller.state != "ready" or not controller.idle:
            raise ValueError("Wait for an idle controller")
        if not light.available or light.mode != "white":
            raise ValueError("Select WHITE before calibration")
        return controller, light

    def _directory(self, profile: dict) -> Path:
        return Path(self.data_dir) / str(UUID(profile["id"]))

    def _compatible(self, profile: dict, mode: Mode) -> Path:
        if (
            profile.get("mode") != mode
            or profile.get("validated") is not True
            or profile.get("method") != CALIBRATION_METHOD
            or profile.get("binding") != self._binding()
        ):
            raise ValueError(
                "Camera, exposure, geometry, optics or illumination changed"
            )
        check_processing(profile.get("processing_reference", {}))
        path = self._directory(profile) / f"{mode}.npz"
        if not path.is_file() or path.stat().st_size != profile["maps_bytes"]:
            raise ValueError("Calibration maps are missing or changed")
        return path

    def _failed_check(self, mode: Mode, profile: dict) -> str | None:
        check = self.last_check.get(mode, {})
        if check.get("profile_id") == profile["id"] and check.get("status") == "error":
            return check.get("error", "Independent validation failed")
        return None

    @lt.property
    def calibration_status(self) -> dict:
        """Show separate RED/GREEN status, without capturing or switching light."""
        result = {}
        for mode in ("red", "green"):
            profile = self.profiles.get(mode)
            if profile is None:
                result[mode] = {"status": "not_calibrated", "reason": "No saved map"}
                continue
            if mode in self.disabled_modes:
                result[mode] = {
                    "status": "disabled",
                    "reason": "Correction disabled; saved map retained",
                }
                continue
            try:
                self._compatible(profile, mode)
                failed = self._failed_check(mode, profile)
                if failed:
                    result[mode] = {"status": "validation_failed", "reason": failed}
                    continue
                result[mode] = {
                    "status": "valid",
                    "reason": "JPEG holdout passed; actual processing rechecked on every use",
                }
            except Exception as exc:
                result[mode] = {"status": "incompatible", "reason": str(exc)}
        return result

    @lt.property
    def manifests(self) -> dict:
        """Two instances of one descriptive calibration contract, not another workflow engine."""
        stages = [
            (
                "preflight",
                "Empty field",
                ["prepared", "parameters", "camera", "illumination"],
                ["binding"],
                False,
            ),
            (
                "dark",
                "Measured dark",
                ["camera", "illumination"],
                ["dark_frames"],
                True,
            ),
            (
                "flat",
                "Phase-matched fit cycles",
                ["dark_frames", "parameters"],
                ["flat_frames", "maps"],
                True,
            ),
            (
                "validation",
                "Independent phase-matched cycles",
                ["maps", "parameters"],
                ["holdout_frames", "quality"],
                True,
            ),
            ("restore", "Restore WHITE", ["illumination"], ["white_readback"], True),
            (
                "save",
                "Save validated profile",
                ["quality", "white_readback"],
                ["profiles"],
                False,
            ),
        ]
        return {
            mode: CalibrationManifest(
                id=f"{mode}_flat_field",
                name=f"{mode.upper()} flat-field",
                description="Empirical measured-dark JPEG8 map inside a common processing ROI; WHITE preserved.",
                prerequisites=[
                    "Empty uniformly illuminated field",
                    "Idle stage",
                    "Manual exposure/gain",
                    "Exclusive control",
                ],
                parameter_refs=["rg_flat_field.parameters", "illumination.settle_ms"],
                stages=[
                    CalibrationStage(
                        id=phase,
                        name=name,
                        description=name,
                        inputs=inputs,
                        outputs=outputs,
                        action="rg_flat_field.calibrate",
                        success_criterion="Checked inputs, metadata and quality",
                        timeout_setting="parameters.timeout_s",
                        cancellation="Stop acquisition, bounded WHITE restore, preserve saved maps",
                        hardware_required=hardware,
                    )
                    for phase, name, inputs, outputs, hardware in stages
                ],
                results=[
                    "profiles",
                    "JPEG ROI maps/mask",
                    "holdout metrics",
                    "frame metadata",
                    "diagnostic preview",
                ],
                acceptance_criteria=[
                    "Fresh same-request JPEG exposures",
                    "Signal/saturation/mask/texture pass",
                    "Unused holdout frames pass",
                    "WHITE restored",
                    "Prior profile survives failure",
                ],
                invalidated_by=["Camera/exposure/ROI", "optics_id", "illumination_id"],
            ).model_dump(mode="json")
            for mode in ("red", "green")
        }

    @lt.endpoint("get", "preview/{mode}.png")
    def preview(self, mode: Mode) -> FileResponse:
        """Return before/after processed holdout planes, not the full colour preview."""
        profile = self.profiles.get(mode)
        if profile is None:
            raise HTTPException(404, "No saved calibration")
        if profile.get("method") != CALIBRATION_METHOD:
            raise HTTPException(
                409, "Historical RAW profile is not a current JPEG calibration"
            )
        path = self._directory(profile) / f"{mode}-validation.png"
        if not path.is_file():
            raise HTTPException(404, "Diagnostic image is missing")
        return FileResponse(path, media_type="image/png")

    @lt.endpoint("get", "diagnostic/{identifier}/{mode}.png")
    def diagnostic_preview(self, identifier: UUID, mode: Mode) -> FileResponse:
        """Serve one explicitly captured manual-light frame, never the normal stream."""
        path = Path(self.data_dir) / str(identifier) / f"{mode}-diagnostic.png"
        if not path.is_file():
            raise HTTPException(404, "Diagnostic frame is missing")
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @contextmanager
    def _session(self, prepared: bool) -> Iterator[Acquisition]:
        with acquisition_session(self, prepared) as run:
            yield run

    @lt.action
    def capture_pair(self, prepared: bool = False) -> dict:
        """Save exact fresh WHITE/RED/GREEN JPEG diagnostics, without claiming focus."""
        with self._session(prepared) as run:
            for mode in ("white", "red", "green"):
                run.select(mode)
                run.frame(mode)
        return {
            "id": run.id,
            "directory": str(run.directory),
            "flat_field_applied": False,
            "frames": run.report["frames"],
            "white_restored": True,
        }

    def _capture_focus_pair(
        self,
        prepared: bool = False,
        *,
        persist_colour_frames: bool = False,
    ) -> RGFocusJPEGHandoff:
        """Return owned JPEG8 ROI inputs, archiving colour JPEGs only on request."""
        metadata: dict[str, dict[str, Any]] = {}
        white_rgb: np.ndarray | None = None
        red_source: np.ndarray | None = None
        green_source: np.ndarray | None = None
        with self._session(prepared) as run:
            run.select("white", verified_reuse=True)
            for mode, next_mode in (
                ("white", "red"),
                ("red", "green"),
                ("green", "white"),
            ):
                planes, rgb, info = run.frame(
                    mode,
                    transition_after=cast(Literal["white", "red", "green"], next_mode),
                    persist_jpeg=mode == "white" or persist_colour_frames,
                )
                metadata[mode] = copy.deepcopy(info)
                if mode == "white":
                    white_rgb = _owned_readonly(rgb)
                elif mode == "red":
                    red_source = _owned_readonly(planes[channel_indices("red")[0]])
                else:
                    green_source = _owned_readonly(planes[channel_indices("green")[0]])
        if white_rgb is None or red_source is None or green_source is None:
            raise RuntimeError("JPEG focus acquisition omitted a required colour frame")
        capture = {
            "id": run.id,
            "directory": str(run.directory),
            "flat_field_applied": False,
            "frames": copy.deepcopy(run.report["frames"]),
            "white_restored": True,
        }
        return RGFocusJPEGHandoff(
            capture=capture,
            metadata=metadata,
            white_rgb=white_rgb,
            red_source_jpeg8=red_source,
            green_source_jpeg8=green_source,
        )

    @lt.action
    def calibrate(
        self, prepared: bool = False, modes: list[Mode] | None = None
    ) -> dict:
        """Fit and independently validate selected maps, replacing profiles only after success."""
        modes = ["red", "green"] if modes is None else modes
        if (
            not modes
            or len(set(modes)) != len(modes)
            or any(m not in ("red", "green") for m in modes)
        ):
            raise ValueError("Choose RED, GREEN or both once")
        pending = {}
        with self._session(prepared) as run:
            run.select("white")
            run.frame("reference")
            run.select("off")
            dark = run.average("dark", run.settings.dark_frames)
            np.savez_compressed(run.directory / "dark.npz", planes=dark)
            fit_frames = run.phase_matched_average(
                "fit", modes, run.settings.average_frames
            )
            for mode in modes:
                indices = channel_indices(mode)
                flat = fit_frames[mode][indices]
                np.savez_compressed(run.directory / f"{mode}-fit.npz", planes=flat)
                maps, report = self._external_flat_fit(
                    run.directory, mode, flat, dark[indices], run.settings
                )
                np.savez_compressed(
                    run.directory / f"{mode}.npz",
                    dark=maps["dark"],
                    gain=maps["gain"],
                    valid=maps["valid"],
                )
                pending[mode] = (maps, report)
            holdout_frames = run.phase_matched_average(
                "validation", modes, run.settings.validation_frames
            )
            profiles = {}
            for mode in modes:
                maps, fit = pending.pop(mode)
                heldout = holdout_frames[mode][channel_indices(mode)]
                np.savez_compressed(
                    run.directory / f"{mode}-holdout.npz", planes=heldout
                )
                quality = self._external_flat_validate(
                    run.directory,
                    mode,
                    heldout,
                    maps,
                    fit,
                    run.settings,
                )
                run.save_preview(mode, heldout, maps)

                path = run.directory / f"{mode}.npz"
                profiles[mode] = {
                    "id": run.id,
                    "mode": mode,
                    "method": CALIBRATION_METHOD,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "validated": True,
                    "binding": run.binding,
                    "parameters": run.settings.model_dump(mode="json"),
                    "acquisition_sequence": {
                        "order": run.phase_matched_order(modes),
                        "fit_cycles": run.settings.average_frames,
                        "validation_cycles": run.settings.validation_frames,
                    },
                    "exposure": run.exposure,
                    "processing_reference": run.processing,
                    "fit": fit,
                    "validation": quality,
                    "maps_sha256": file_hash(path),
                    "maps_bytes": path.stat().st_size,
                    "geometry": run.binding["camera"]["geometry"],
                }
                write_json(run.directory / f"{mode}-profile.json", profiles[mode])
        lt.raise_if_cancelled()
        self._commit("profiles", {**self.profiles, **profiles})
        self.progress = {
            "phase": "complete",
            "frames": len(run.report["frames"]),
            "id": run.id,
        }
        return profiles

    @lt.action
    def measure_stability(
        self, prepared: bool = False, mode: Mode = "red", samples: int = 45
    ) -> dict:
        """Measure a bounded light-on brightness curve; never replace calibration maps."""
        if mode not in ("red", "green") or not 5 <= samples <= 90:
            raise ValueError("Choose RED/GREEN and 5..90 samples")
        with self._session(prepared) as run:
            run.select("white")
            run.frame("reference")
            run.select(mode)
            for index in range(samples):
                started = time.monotonic()
                run.frame(f"{mode}-stability")
                if index < samples - 1:
                    lt.cancellable_sleep(max(0, 1 - (time.monotonic() - started)))
        return {"id": run.id, "frames": run.report["frames"], "white_restored": True}

    def _load_maps(self, mode: Mode) -> tuple[dict, dict]:
        if mode in self.disabled_modes:
            raise ValueError(f"{mode.upper()} flat-field is disabled")
        profile = self.profiles.get(mode)
        if profile is None:
            raise ValueError(f"No saved {mode.upper()} flat-field")
        path = self._compatible(profile, mode)
        if file_hash(path) != profile["maps_sha256"]:
            raise ValueError("Calibration map checksum mismatch")
        with np.load(path, allow_pickle=False) as archive:
            maps = {key: archive[key] for key in ("dark", "gain", "valid")}
        return profile, maps

    def correct_frame(
        self,
        mode: Mode,
        planes: np.ndarray,
        metadata: dict,
        *,
        timings: dict[str, float] | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Apply a compatible profile on the common decoded JPEG ROI grid."""
        started = time.monotonic()
        profile, maps = self._load_maps(mode)
        loaded = time.monotonic()
        failed = self._failed_check(mode, profile)
        if failed:
            raise ValueError(f"Saved flat-field failed its latest check: {failed}")
        if (
            metadata.get("light") != mode
            or metadata.get("illumination", {}).get("mode") != mode
        ):
            raise ValueError("Frame illumination does not match its flat-field mode")
        if (
            metadata["binding"] != profile["binding"]["camera"]
            or {key: metadata[key] for key in ("exposure_time_us", "analogue_gain")}
            != profile["exposure"]
        ):
            raise ValueError(
                "Frame does not match the saved flat-field exposure/geometry"
            )
        check_processing(metadata["processing"], profile["processing_reference"])
        expected = profile["geometry"]["plane_size"]
        if planes.shape == (expected[1], expected[0]):
            selected = planes[np.newaxis, ...]
        elif planes.shape == (3, expected[1], expected[0]):
            selected = planes[channel_indices(mode)]
        else:
            raise ValueError("Frame does not match the processed JPEG ROI dimensions")
        validated = time.monotonic()
        selected_at = time.monotonic()
        corrected, mask = self._external_flat_apply(selected, maps)
        result = common_measurement_plane(corrected, mask)
        completed = time.monotonic()
        if timings is not None:
            timings.update(
                map_load_s=loaded - started,
                validation_s=validated - loaded,
                channel_select_s=selected_at - validated,
                map_application_s=completed - selected_at,
                total_s=completed - started,
            )
        return result

    @staticmethod
    def _render_diagnostic(
        mode: Mode, plane: np.ndarray, valid: np.ndarray
    ) -> tuple[Image.Image, list[float]]:
        """Render a labelled pseudocolour view; calculations retain processed DN values."""
        values = plane[valid & np.isfinite(plane)]
        if values.size == 0:
            raise ValueError("Diagnostic frame has no valid corrected pixels")
        low, high = np.percentile(values, [0.5, 99.5]).tolist()
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            high = low + 1
        level = np.clip((np.nan_to_num(plane, nan=low) - low) / (high - low), 0, 1)
        level[~valid] = 0
        byte = np.rint(level * 255).astype(np.uint8)
        dim = np.rint(level * 28).astype(np.uint8)
        rgb = np.zeros((*byte.shape, 3), dtype=np.uint8)
        if mode == "red":
            rgb[..., 0], rgb[..., 1], rgb[..., 2] = byte, dim, dim
        else:
            rgb[..., 0], rgb[..., 1], rgb[..., 2] = dim, byte, dim
        image = Image.fromarray(rgb)
        image.thumbnail((1000, 1000))
        return image, [float(low), float(high)]

    @lt.action
    def select_diagnostic_mode(
        self, mode: Literal["white", "red", "green", "off"]
    ) -> dict:
        """Select a manual light and capture one honest R/G diagnostic snapshot.

        WHITE uses the normal ISP-calibrated stream. RED/GREEN stay out of that
        stream and are rendered from the same-owner processed JPEG request. A valid,
        successfully checked mode map is applied when available; otherwise the
        snapshot is explicitly marked uncorrected. The selected manual light is
        retained until the operator changes it again.
        """
        if mode not in ("white", "red", "green", "off"):
            raise ValueError("Unknown diagnostic illumination mode")
        if mode in ("white", "off"):
            # Operator light controls must remain usable before JPEG migration,
            # with invalid camera geometry, and without creating a capture session.
            preview = getattr(self._cam, "_measurement_preview", None)
            if preview is not None:
                preview.transition()
            state = self._illumination.set_mode(mode)
            if not state.available or state.mode != mode:
                raise ValueError("Light mode was not confirmed")
            if preview is not None:
                preview.confirmed(mode, time.monotonic_ns(), finished=mode == "white")
            return {
                "id": str(uuid4()),
                "mode": mode,
                "flat_field_applied": False,
                "profile_id": None,
                "image_href": None,
                "selected_mode_retained": True,
            }
        run = Acquisition(self)
        result: dict[str, Any] = {
            "id": run.id,
            "mode": mode,
            "flat_field_applied": False,
            "profile_id": None,
            "image_href": None,
        }
        failure: BaseException | None = None
        try:
            run.select(mode)
            if mode in ("red", "green"):
                planes, _rgb, info = run.frame(f"{mode}-diagnostic")
                correction_reason = ""
                correction_timing: dict[str, float] = {}
                try:
                    profile = self.profiles.get(mode)
                    corrected, valid = self.correct_frame(
                        mode, planes, info, timings=correction_timing
                    )
                    result.update(
                        flat_field_applied=True,
                        profile_id=profile["id"] if profile else None,
                        correction_timing=correction_timing,
                    )
                    run.report["correction_timing"] = {
                        "clock": "time.monotonic",
                        "mode": mode,
                        "total_s_scope": "inclusive map load, validation, channel selection and map application; excludes capture/decode/ROI extraction",
                        "phases": correction_timing,
                    }
                except (ValueError, OSError) as exc:
                    correction_reason = str(exc)
                    indices = channel_indices(mode)
                    signal = planes[indices].astype(np.float32)
                    corrected, valid = common_measurement_plane(
                        signal, np.isfinite(signal)
                    )
                image, display_range = self._render_diagnostic(
                    cast(Mode, mode), corrected, valid
                )
                path = run.directory / f"{mode}-diagnostic.png"
                image.save(path)
                result.update(
                    image_href=f"/rg_flat_field/diagnostic/{run.id}/{mode}.png",
                    sensor_timestamp_ns=info["sensor_timestamp_ns"],
                    display_range_dn=display_range,
                    valid_fraction=float(np.mean(valid)),
                    correction_reason=correction_reason,
                    display="processed JPEG ROI pseudocolour, per-frame 0.5–99.5% display scale",
                )
            run.check_position()
            result["selected_mode_retained"] = True
            run.report["diagnostic"] = result
            return result
        except BaseException as exc:
            failure = exc
            raise
        finally:
            run.report["error"] = None if failure is None else str(failure)
            run.report["elapsed_s"] = time.monotonic() - run.started
            run.report["selected_mode_retained"] = mode
            write_json(run.directory / "acquisition.json", run.report)
            self.progress = {
                "phase": "diagnostic" if failure is None else "stopped",
                "frames": len(run.report["frames"]),
                "id": run.id,
            }

    @lt.action
    def validate(self, prepared: bool = False, modes: list[Mode] | None = None) -> dict:
        """Check existing profiles against another fresh empty-field acquisition; never refit."""
        modes = ["red", "green"] if modes is None else modes
        if (
            not modes
            or len(set(modes)) != len(modes)
            or any(m not in ("red", "green") for m in modes)
        ):
            raise ValueError("Choose RED, GREEN or both once")
        loaded = {mode: self._load_maps(mode) for mode in modes}
        quality: dict = {}
        checks = {}
        try:
            with self._session(prepared) as run:
                run.required_processing = [
                    profile["processing_reference"]
                    for profile, _maps in loaded.values()
                ]
                try:
                    heldout_frames = run.phase_matched_average(
                        "recheck", modes, run.settings.validation_frames
                    )
                except BaseException as exc:
                    affected = [run.mode] if run.mode in modes else modes
                    for mode in affected:
                        profile, _maps = loaded[mode]
                        checks[mode] = {
                            "id": run.id,
                            "profile_id": profile["id"],
                            "status": "cancelled"
                            if isinstance(exc, lt.exceptions.InvocationCancelledError)
                            else "error",
                            "error": str(exc),
                        }
                    raise
                for mode in modes:
                    profile, maps = loaded[mode]
                    try:
                        heldout = heldout_frames[mode][channel_indices(mode)]
                        np.savez_compressed(
                            run.directory / f"{mode}-recheck.npz", planes=heldout
                        )
                        quality[mode] = self._external_flat_validate(
                            run.directory,
                            f"{mode}-recheck",
                            heldout,
                            maps,
                            profile["fit"],
                            run.settings,
                        )
                        run.save_preview(mode, heldout, maps)
                    except BaseException as exc:
                        checks[mode] = {
                            "id": run.id,
                            "profile_id": profile["id"],
                            "status": "cancelled"
                            if isinstance(exc, lt.exceptions.InvocationCancelledError)
                            else "error",
                            "error": str(exc),
                        }
                        raise
                    checks[mode] = {
                        "id": run.id,
                        "profile_id": profile["id"],
                        "status": "passed",
                        "quality": quality[mode],
                    }
                write_json(run.directory / "validation.json", quality)
        finally:
            if checks:
                if not run.report.get("white_restored"):
                    for check in checks.values():
                        if check["status"] == "passed":
                            check.update(
                                status="error",
                                error="WHITE restoration was not confirmed",
                            )
                # Cancelling an inspection must not erase an earlier known failure.
                merged = dict(self.last_check)
                merged.update(
                    {m: c for m, c in checks.items() if c["status"] != "cancelled"}
                )
                self._commit("last_check", merged)
        return {"id": run.id, "validation": quality, "white_restored": True}


class Acquisition:
    """One action's camera/light sequence under OFM's existing global lock."""

    def __init__(
        self,
        owner: Any,
        initial_light: LightState | None = None,
        initial_controller: ControllerState | None = None,
    ) -> None:
        """Snapshot settings/position; never establish zero or enable motors."""
        self.owner = owner
        self.preview = getattr(owner._cam, "_measurement_preview", None)
        self.settings = owner.parameters
        self.binding = owner._binding()
        self.id = str(uuid4())
        self.directory = Path(owner.data_dir) / self.id
        self.directory.mkdir()
        self.started = time.monotonic()
        self.last_start = -1
        self.processing: dict | None = None
        self.raw_binding: dict | None = None
        self.required_processing: list[dict] = []
        self.exposure: dict | None = None
        self.timings: dict[str, dict[str, float | int]] = {
            phase: {"seconds": 0.0, "count": 0}
            for phase in (
                "controller_readback",
                "light_readback",
                "combined_readback",
                "light_select",
                "verified_light_reuse",
                "light_extinguish",
                "light_transition",
                "camera_frame",
                "jpeg_save",
                "json_save",
                "owned_copy",
                "white_restore",
            )
        }
        if (initial_light is None) != (initial_controller is None):
            raise ValueError("Initial controller and light must be supplied together")
        if initial_light is None or initial_controller is None:
            self.initial_state = self.state()
            self._verified_controller: ControllerState | None = None
            self._verified_light: LightState | None = None
        else:
            # The readiness snapshot is already one fresh atomic controller+light
            # read. Reuse it as the acquisition boundary instead of immediately
            # issuing the same hardware query a second time.
            if initial_controller.state != "ready" or not initial_controller.idle:
                raise ValueError("Controller is not ready and idle")
            if not initial_light.available or initial_light.mode != "white":
                raise ValueError("Session did not start with confirmed WHITE")
            self.initial_state = initial_controller.model_dump()
            self._verified_controller = initial_controller
            self._verified_light = initial_light
        self.mode: Literal["white", "red", "green", "off", "mixed"] = "white"
        self.report: dict = {
            "id": self.id,
            "binding": self.binding,
            "frames": [],
            "light": [],
            "storage": {
                "jpeg_files_bytes": 0,
                "raw_files_bytes": 0,
                "decoded_array_bytes": 0,
            },
            "timing": {
                "clock": "time.monotonic",
                "counts": "attempted calls, including failed calls",
                "elapsed_s_scope": "inclusive from initial state through cleanup; excludes acquisition.json persistence",
                "inclusive_phases": {
                    "white_restore": [
                        "light_select",
                        "verified_light_reuse",
                        "controller_readback",
                        "combined_readback",
                    ],
                },
                "readback_scope": "direct Acquisition state/light reads only; nested transport reads remain inside light_select/light_transition/camera_frame",
                "save_scope": "every accepted full-source JPEG and frame JSON; excludes derived maps/previews/report files",
                "phases": self.timings,
            },
        }
        if initial_light is not None:
            if not initial_light.available or initial_light.mode != "white":
                raise ValueError("Session did not start with confirmed WHITE")
            self.report["light"].append(
                {
                    "mode": "white",
                    "reason": "session_preflight",
                    "confirmed_ns": self.preview_confirmed("white"),
                    "readback": initial_light.model_dump(mode="json"),
                }
            )

    @contextmanager
    def timed(self, phase: str) -> Iterator[None]:
        """Accumulate attempted operation time locally, including failed operations."""
        started = time.monotonic()
        try:
            yield
        finally:
            result = self.timings.setdefault(phase, {"seconds": 0.0, "count": 0})
            result["seconds"] += time.monotonic() - started
            result["count"] += 1

    def state(self) -> dict:
        """Force a controller read so external movement cannot hide behind a cached position."""
        with self.timed("controller_readback"), self.owner._stage._hardware_lock:
            state = self.owner._stage._fetch_state(refresh=True)
        if state.state != "ready" or not state.idle:
            raise ValueError("Controller is not ready and idle")
        return state.model_dump()

    def check_position(self) -> dict:
        """Position/motor changes reject the run; no restoration movement is sent."""
        state = self.state()
        self.validate_position(state)
        return state

    def check_position_and_light(self) -> tuple[dict, LightState]:
        """Validate controller and light from one fresh post-exposure response."""
        with self.timed("combined_readback"):
            controller, light = self.owner._illumination._read_controller_and_light()
        state = controller.model_dump()
        self.validate_position(state)
        self._verified_controller = controller
        self._verified_light = light
        return state, light

    def validate_position(self, state: dict) -> None:
        """Reject a controller snapshot that differs from the action start."""
        keys = (
            "pid",
            "gcode_position",
            "machine_position",
            "homing_origin",
            "enabled",
            "homed_axes",
        )
        if any(state[key] != self.initial_state[key] for key in keys):
            raise ValueError("External stage change during light/camera acquisition")

    def exposure_start_state(self) -> dict:
        """Reuse the stage's own verified post-light state when a zero exists."""
        expected = self.owner._stage._expected_reference_state()
        if expected is None:
            return self.check_position()
        state = expected.model_dump()
        self.validate_position(state)
        return state

    def check(self) -> None:
        """Bound time and cancellation, and reject changing camera configuration."""
        lt.raise_if_cancelled()
        if time.monotonic() - self.started > self.settings.timeout_s:
            raise TimeoutError("R/G acquisition exceeded its time limit")
        if self.binding != self.owner._binding():
            raise ValueError("Measurement camera/settings changed")

    def preview_transition(self) -> None:
        """Hold publication before changing illumination."""
        if self.preview is not None:
            self.preview.transition()

    def preview_confirmed(self, mode: str, *, finished: bool = False) -> int:
        """Record the settled light boundary for capture and preview."""
        boundary = time.monotonic_ns()
        if self.preview is not None:
            self.preview.confirmed(mode, boundary, finished=finished)
        return boundary

    def select(
        self,
        mode: Literal["white", "red", "green", "off"],
        *,
        force: bool = False,
        finished: bool = False,
        verified_reuse: bool = False,
    ) -> bool:
        """Use the existing break-before-make switch and verify its readback."""
        self.check()
        self.preview_transition()
        if (
            not force
            and self.report["light"]
            and self.mode == mode
            and self.report["light"][-1]["mode"] == mode
        ):
            # The session owns this verified point-in-time state. A following
            # frame checks both live light and controller activity; cleanup also
            # forces a controller read and falls back to a real WHITE selection
            # if any external command intervened.
            current = None
            with self.timed("verified_light_reuse"):
                if verified_reuse:
                    current = LightState.model_validate(
                        self.report["light"][-1]["readback"]
                    )
                else:
                    try:
                        with self.timed("light_readback"):
                            current = self.owner._illumination._read_state()
                    except lt.exceptions.InvocationCancelledError:
                        raise
                    except Exception:
                        # The normal selector below owns bounded recovery/cleanup.
                        current = None
            if current is not None and current.available and current.mode == mode:
                self.report["light"].append(
                    {
                        "mode": mode,
                        "reason": "reuse_session_verified_mode",
                        "confirmed_ns": self.preview_confirmed(mode, finished=finished),
                        "readback": current.model_dump(mode="json"),
                    }
                )
                return False
        with self.timed("light_select"):
            state = self.owner._illumination.set_mode(mode)
        if not state.available or state.mode != mode:
            raise ValueError("Light mode was not confirmed")
        self.mode = mode
        self.report["light"].append(
            {
                "mode": mode,
                "confirmed_ns": self.preview_confirmed(mode, finished=finished),
                "readback": state.model_dump(mode="json"),
            }
        )
        return True

    def select_red_green_probe(self) -> None:
        """Select both colour gates only inside the explicit RAW probe action."""
        self.check()
        self.preview_transition()
        with self.timed("light_select"):
            if (
                self._verified_controller is not None
                and self._verified_light is not None
                and self._verified_light.mode == "white"
                and self.mode == "white"
                and self.owner._stage._expected_reference_state() is not None
            ):
                state = self.owner._illumination._select_red_green_probe_after_verified(
                    self._verified_light, self._verified_controller
                )
            else:
                state = self.owner._illumination._select_red_green_probe()
        expected = {"white": False, "red": True, "green": True}
        if not state.available or state.mode != "mixed" or state.channels != expected:
            raise ValueError("Combined RED/GREEN light state was not confirmed")
        self.mode = "mixed"
        self._verified_controller = self.owner._stage._expected_reference_state()
        self._verified_light = state
        self.report["light"].append(
            {
                "mode": "mixed",
                "reason": "simultaneous_raw_probe_only",
                "confirmed_ns": self.preview_confirmed("mixed"),
                "readback": state.model_dump(mode="json"),
            }
        )

    def raw_frame(
        self,
        phase: str,
        *,
        persist_raw: bool = True,
        plane_roi: tuple[int, int, int, int] | None = None,
    ) -> tuple[np.ndarray, dict]:
        """Capture one fresh RAW request; optionally retain only an owned ROI in RAM."""
        self.check()
        capture_mode = self.mode
        light_boundary = self.report["light"][-1]["confirmed_ns"]
        before = self.exposure_start_state()
        capture = cast(Any, self.owner._cam).capture_linear_frame
        with self.timed("camera_frame"):
            planes, _rgb, info = capture(self.settings.frame_timeout_s)
        after, light = self.check_position_and_light()
        if (
            after["print_time"] != before["print_time"]
            or not light.available
            or light.mode != capture_mode
        ):
            raise ValueError("External controller/light command during RAW exposure")
        start = info["exposure_start_ns"]
        if start <= self.last_start or start < light_boundary:
            raise ValueError("Repeated or transitional RAW light exposure")
        binding = info.get("binding")
        geometry = info.get("geometry")
        if not isinstance(binding, dict) or geometry != binding.get("geometry"):
            raise ValueError("RAW frame binding or geometry is missing")
        if self.raw_binding is None:
            self.raw_binding = copy.deepcopy(binding)
        elif binding != self.raw_binding:
            raise ValueError("RAW camera configuration changed during probe")
        width, height = geometry["plane_size"]
        if (
            planes.dtype != np.uint16
            or planes.shape != (4, height, width)
            or not np.isfinite(planes).all()
        ):
            raise ValueError("RAW Bayer planes changed format or geometry")
        retained = planes if plane_roi is None else _crop_raw_planes(planes, plane_roi)
        exposure = {key: info[key] for key in ("exposure_time_us", "analogue_gain")}
        if self.exposure is not None and exposure != self.exposure:
            raise ValueError("Exposure/gain changed during RAW probe")
        stem = f"raw-{len(self.report['frames']) + 1:04d}-{capture_mode}"
        frame = {
            **info,
            "phase": phase,
            "light": capture_mode,
            "raw_retention": "compressed_file" if persist_raw else "memory_only",
            "processing_plane_roi": list(plane_roi) if plane_roi is not None else None,
            "processing_plane_size": [retained.shape[2], retained.shape[1]],
            "illumination": {
                "mode": capture_mode,
                "source": "controller_readback_and_exposure_window",
                "confirmed_ns": light_boundary,
            },
            "channel_mean_dn": np.mean(retained, axis=(1, 2)).tolist(),
            "channel_saturation_fraction": np.mean(
                retained >= geometry["white_level"], axis=(1, 2)
            ).tolist(),
            "completed_ns": time.monotonic_ns(),
        }
        if persist_raw:
            path = self.directory / f"{stem}.npz"
            with self.timed("raw_save"):
                np.savez_compressed(path, planes=retained)
            frame.update(
                raw_file=path.name,
                raw_sha256=file_hash(path),
                raw_size_bytes=path.stat().st_size,
            )
            self.report["storage"]["raw_files_bytes"] += path.stat().st_size
        metadata_path = self.directory / f"{stem}.json"
        frame["metadata_file"] = metadata_path.name
        with self.timed("json_save"):
            write_json(metadata_path, frame)
        self.exposure = exposure
        self.last_start = start
        self.report["storage"]["decoded_array_bytes"] += retained.nbytes
        self.report["frames"].append(frame)
        self.owner.progress = {
            "phase": phase,
            "frames": len(self.report["frames"]),
            "id": self.id,
        }
        self.owner.logger.info(
            "R/G %s: RAW frame %s", phase, len(self.report["frames"])
        )
        self.check()
        return retained, frame

    def raw_average(
        self,
        phase: str,
        count: int,
        plane_roi: tuple[int, int, int, int],
    ) -> np.ndarray:
        """Average bounded RAW ROIs without compressing full sensor frames to disk."""
        if count < 1:
            raise ValueError("At least one RAW frame is required")
        total: np.ndarray | None = None
        for _ in range(count):
            planes, _metadata = self.raw_frame(
                phase, persist_raw=False, plane_roi=plane_roi
            )
            if total is None:
                total = planes.astype(np.float32)
            else:
                total += planes
        if total is None:
            raise RuntimeError("RAW average did not capture any frame")
        return total / count

    def restore_white(self) -> None:
        """Verify WHITE and the stationary stage with one final combined read."""
        if (
            self.mode == "mixed"
            and self._verified_controller is not None
            and self._verified_light is not None
            and self._verified_light.mode == "mixed"
            and self.owner._stage._expected_reference_state() is not None
        ):
            self.preview_transition()
            with self.timed("light_select"):
                state = self.owner._illumination._transition_after_verified(
                    self._verified_light, self._verified_controller, "white"
                )
            self.mode = "white"
            self._verified_controller = self.owner._stage._expected_reference_state()
            self._verified_light = state
            self.report["light"].append(
                {
                    "mode": "white",
                    "reason": "after_simultaneous_raw_probe",
                    "confirmed_ns": self.preview_confirmed("white", finished=True),
                    "readback": state.model_dump(mode="json"),
                }
            )
            selected = True
        else:
            selected = self.select("white", finished=True, verified_reuse=True)
        restoration_attempted = False
        try:
            _, restored_light = self.check_position_and_light()
            if restored_light.available and restored_light.mode == "white":
                return
            if selected:
                raise ValueError("WHITE restoration was not confirmed")
            restoration_attempted = True
            self.select("white", force=True, finished=True)
            _, restored_light = self.check_position_and_light()
            if not restored_light.available or restored_light.mode != "white":
                raise ValueError("WHITE restoration was not confirmed")
        except BaseException as position_error:
            # A reused confirmation is followed by this fresh combined check.
            # If an external light change raced it, make one bounded restore;
            # never replay an already attempted or uncertain ON command.
            if not selected and not restoration_attempted:
                try:
                    self.select("white", force=True, finished=True)
                except BaseException as restore_error:
                    position_error.add_note(
                        f"Forced WHITE restoration failed: {restore_error}"
                    )
            raise

    def finish_exposure_illumination(
        self,
        capture_mode: Literal["white", "red", "green", "off"],
        light: LightState,
        light_boundary: int,
        *,
        extinguish_after: bool,
        transition_after: Literal["white", "red", "green"] | None,
    ) -> dict:
        """Return provenance and end a focus exposure before processing."""
        illumination: dict[str, Any] = {
            "mode": capture_mode,
            "source": "controller_readback_and_exposure_window",
            "confirmed_ns": light_boundary,
        }
        if extinguish_after and transition_after is not None:
            raise ValueError("Choose either extinguish or a direct light transition")
        if transition_after is not None:
            self.preview_transition()
            with self.timed("light_transition"):
                after = self.owner._illumination._transition_after_verified(
                    light,
                    self.owner._stage._expected_reference_state(),
                    transition_after,
                )
            if not after.available or after.mode != transition_after:
                raise ValueError("Next focus light was not confirmed after exposure")
            self.mode = transition_after
            transitioned_ns = self.preview_confirmed(transition_after)
            self.report["light"].append(
                {
                    "mode": transition_after,
                    "reason": f"after_{capture_mode}_focus_exposure",
                    "confirmed_ns": transitioned_ns,
                    "readback": after.model_dump(mode="json"),
                }
            )
            illumination.update(
                {
                    "transitioned_to": transition_after,
                    "transition_confirmed_ns": transitioned_ns,
                }
            )
            if capture_mode in ("red", "green"):
                illumination["confirmed_on_window_ms"] = (
                    transitioned_ns - light_boundary
                ) / 1_000_000
            return illumination
        if not extinguish_after:
            return illumination
        if capture_mode not in ("red", "green"):
            raise ValueError(
                "Only a RED/GREEN focus exposure may be extinguished early"
            )
        with self.timed("light_extinguish"):
            off = self.owner._illumination._extinguish_after_verified(
                light, self.owner._stage._expected_reference_state()
            )
        if not off.available or off.mode != "off":
            raise ValueError("Colour light was not confirmed OFF after exposure")
        self.mode = "off"
        extinguished_ns = self.preview_confirmed("off")
        self.report["light"].append(
            {
                "mode": "off",
                "reason": f"after_{capture_mode}_focus_exposure",
                "confirmed_ns": extinguished_ns,
                "readback": off.model_dump(mode="json"),
            }
        )
        illumination.update(
            {
                "extinguished_ns": extinguished_ns,
                "confirmed_on_window_ms": (extinguished_ns - light_boundary)
                / 1_000_000,
            }
        )
        return illumination

    def frame(
        self,
        phase: str,
        *,
        extinguish_after: bool = False,
        transition_after: Literal["white", "red", "green"] | None = None,
        persist_jpeg: bool = True,
    ) -> tuple[np.ndarray, np.ndarray, dict]:
        """Capture only after light settling, then verify no controller command intervened."""
        self.check()
        capture_mode = self.mode
        if capture_mode == "mixed":
            raise ValueError("Combined RED/GREEN is restricted to the RAW probe")
        light_boundary = self.report["light"][-1]["confirmed_ns"]
        before = self.exposure_start_state()
        capture = cast(Any, self.owner._cam).capture_jpeg_frame
        with self.timed("camera_frame"):
            jpeg, full_rgb, info = capture(self.settings.frame_timeout_s)
        after, light = self.check_position_and_light()
        if (
            after["print_time"] != before["print_time"]
            or not light.available
            or light.mode != self.mode
        ):
            raise ValueError("External controller/light command during exposure")
        start = info["exposure_start_ns"]
        if start <= self.last_start or start < light_boundary:
            raise ValueError("Repeated or transitional light exposure")
        illumination = self.finish_exposure_illumination(
            capture_mode,
            light,
            light_boundary,
            extinguish_after=extinguish_after,
            transition_after=transition_after,
        )
        derived_binding = processing_binding(info["binding"], self.settings)
        if (
            derived_binding != self.binding["camera"]
            or info["geometry"] != info["binding"]["geometry"]
        ):
            raise ValueError("Captured frame configuration changed")
        width, height = info["binding"]["geometry"]["image_size"]
        if full_rgb.dtype != np.uint8 or full_rgb.shape != (height, width, 3):
            raise ValueError("Captured JPEG RGB8 geometry changed")
        if (
            not isinstance(jpeg, bytes)
            or hashlib.sha256(jpeg).hexdigest() != info["jpeg_sha256"]
            or len(jpeg) != info["jpeg_size_bytes"]
        ):
            raise ValueError("Captured JPEG checksum/size changed")
        exposure = {key: info[key] for key in ("exposure_time_us", "analogue_gain")}
        if self.exposure is not None and exposure != self.exposure:
            raise ValueError("Exposure/gain changed during acquisition")
        processing = info["processing"]
        check_processing(processing, self.processing)
        for reference in self.required_processing:
            check_processing(processing, reference)
        if self.processing is None:
            self.processing = copy.deepcopy(processing)
        x, y, roi_width, roi_height = derived_binding["processing_roi"]
        with self.timed("owned_copy"):
            rgb = full_rgb[y : y + roi_height, x : x + roi_width].copy()
            planes = np.moveaxis(rgb, 2, 0)
        self.exposure, self.last_start, stem = (
            exposure,
            start,
            f"frame-{len(self.report['frames']) + 1:04d}-{capture_mode}",
        )
        info = {
            **info,
            "source_binding": info["binding"],
            "source_geometry": info["geometry"],
            "binding": derived_binding,
            "geometry": derived_binding["geometry"],
            "jpeg_file": f"{stem}.jpg" if persist_jpeg else None,
            "jpeg_persisted": persist_jpeg,
            "metadata_file": f"{stem}.json",
            "phase": phase,
            "light": capture_mode,
            "illumination": illumination,
            "channel_mean_dn": np.mean(planes, axis=(1, 2)).tolist(),
            "completed_ns": time.monotonic_ns(),
        }
        info["channel_saturation_fraction"] = np.mean(
            planes >= 255 * self.settings.saturation_level_fraction, axis=(1, 2)
        ).tolist()
        self._persist_frame(jpeg, info, stem, persist_jpeg)
        self.report["storage"]["decoded_array_bytes"] += full_rgb.nbytes
        self.report["frames"].append(info)
        self.owner.progress = {
            "phase": phase,
            "frames": len(self.report["frames"]),
            "id": self.id,
        }
        self.owner.logger.info("R/G %s: frame %s", phase, len(self.report["frames"]))
        self.check()
        return planes, rgb, info

    def _persist_frame(
        self, jpeg: bytes, info: dict[str, Any], stem: str, persist_jpeg: bool
    ) -> None:
        """Persist provenance and only the JPEGs selected by the caller's policy."""
        if persist_jpeg:
            with (
                self.timed("jpeg_save"),
                (self.directory / f"{stem}.jpg").open("wb") as handle,
            ):
                handle.write(jpeg)
            self.report["storage"]["jpeg_files_bytes"] += len(jpeg)
        with self.timed("json_save"):
            write_json(self.directory / info["metadata_file"], info)

    def average(self, phase: str, count: int) -> np.ndarray:
        """Average decoded JPEG samples, retaining every full source and provenance."""
        total = None
        for _ in range(count):
            planes, _rgb, info = self.frame(phase)
            if (
                self.mode in ("red", "green")
                and max(
                    info["channel_saturation_fraction"][i]
                    for i in channel_indices(cast(Mode, self.mode))
                )
                > self.settings.maximum_saturation_fraction
            ):
                raise ValueError("An individual flat-field exposure is saturated")
            if total is None:
                total = planes.astype(np.float32)
            else:
                total += planes
        if total is None:
            raise ValueError("At least one exposure is required")
        return total / count

    @staticmethod
    def phase_matched_order(modes: list[Mode]) -> list[str]:
        """Return the actual autofocus light order, including conditioning frames."""
        return ["white", "red", "green"] if "green" in modes else ["white", "red"]

    def phase_matched_average(
        self, phase: str, modes: list[Mode], count: int
    ) -> dict[Mode, np.ndarray]:
        """Average disjoint short cycles matching WHITE-to-RED-to-GREEN autofocus."""
        selected = set(modes)
        totals: dict[Mode, np.ndarray] = {}
        order = self.phase_matched_order(modes)
        for cycle in range(1, count + 1):
            self.select("white")
            self.frame(f"{phase}-cycle-{cycle}-white")
            for light in order[1:]:
                mode = cast(Mode, light)
                self.select(mode)
                planes, _rgb, info = self.frame(f"{phase}-cycle-{cycle}-{mode}")
                if mode not in selected:
                    continue
                if (
                    max(
                        info["channel_saturation_fraction"][i]
                        for i in channel_indices(mode)
                    )
                    > self.settings.maximum_saturation_fraction
                ):
                    raise ValueError("An individual flat-field exposure is saturated")
                if mode not in totals:
                    totals[mode] = planes.astype(np.float32)
                else:
                    totals[mode] += planes
        if set(totals) != selected:
            raise ValueError("At least one phase-matched cycle is required")
        self.report.setdefault("phase_matched_acquisitions", []).append(
            {
                "phase": phase,
                "cycles": count,
                "order": order,
                "measured_modes": modes,
            }
        )
        return {mode: total / count for mode, total in totals.items()}

    def save_preview(self, mode: Mode, heldout: np.ndarray, maps: dict) -> None:
        """Save uncorrected/corrected JPEG ROI planes with one display scale."""
        corrected, _valid = self.owner._external_flat_apply(heldout, maps)
        before = np.mean(heldout - maps["dark"], axis=0)
        after = np.nanmean(corrected, axis=0)
        scale = max(
            float(np.nanpercentile(before, 99.5)),
            float(np.nanpercentile(after, 99.5)),
            1,
        )
        pair = np.hstack((before, after))
        image = np.clip(np.nan_to_num(pair) / scale * 240, 0, 255).astype(np.uint8)
        preview = Image.fromarray(image)
        preview.thumbnail((1000, 500))
        preview.save(self.directory / f"{mode}-validation.png")
