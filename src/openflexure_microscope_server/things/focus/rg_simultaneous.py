"""Independent calibration owner for simultaneous RED+GREEN RAW autofocus."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, cast
from uuid import UUID, uuid4

import numpy as np
from fastapi import HTTPException
from fastapi.responses import FileResponse
from PIL import Image

import labthings_fastapi as lt

from openflexure_microscope_server.fast_ofm_contracts import (
    CalibrationManifest,
    CalibrationStage,
)
from openflexure_microscope_server.focus.rg.rg_flat_field import Mode
from openflexure_microscope_server.focus.rg.rg_focus_core import PatchBox
from openflexure_microscope_server.focus.rg.rg_simultaneous import (
    SimultaneousCalibrationSettings,
    SimultaneousFocusCurve,
    SimultaneousFocusObservation,
    SimultaneousFocusSearchSettings,
    SimultaneousFocusSettings,
    SimultaneousShiftMeasurement,
)
from openflexure_microscope_server.integrations.fast_ofm_core import (
    FastOFMCoreBlockingProcess,
    FastOFMCoreError,
)
from openflexure_microscope_server.integrations.fast_ofm_core.artifacts import (
    read_simultaneous_result,
    write_simultaneous_input,
)

from .. import OFMThing
from ..camera import BaseCamera
from ..illumination import LightState, MoonrakerIllumination
from ..stage.moonraker import CompletedScanPreload, ControllerState, MoonrakerStage
from .rg_flat_field import (
    Acquisition,
    acquisition_session,
    file_hash,
    write_json,
)
from .rg_focus import RGFocus

CALIBRATION_METHOD = "simultaneous-raw12-spatial-spectral-unmixing"
FOCUS_METHOD = "simultaneous-raw12-spectral-subpixel-shift"


class SimultaneousFocusFallbackEligibleError(ValueError):
    """A bounded simultaneous-focus refusal that may use the proven fallback."""

    def __init__(
        self,
        reason: str,
        *,
        result_id: str,
        report_ref: str,
        peripheral_search: dict | None = None,
        mixed_capture_count: int = 0,
    ) -> None:
        """Attach the failed experiment identity without changing its cause."""
        super().__init__(reason)
        self.result_id = result_id
        self.report_ref = report_ref
        self.peripheral_search = copy.deepcopy(peripheral_search)
        self.mixed_capture_count = mixed_capture_count


class SimultaneousFocusSampleRefusalError(ValueError):
    """A completed, WHITE-restored mixed capture that cannot be interpreted."""


class RGSimultaneous(OFMThing):
    """Own simultaneous RAW settings/maps without touching separate JPEG R/G."""

    _class_settings = {"validate_properties_on_set": True}
    _cam: BaseCamera = lt.thing_slot()
    _stage: MoonrakerStage = lt.thing_slot()
    _illumination: MoonrakerIllumination = lt.thing_slot()
    _rg_focus: RGFocus | None = lt.thing_slot()

    parameters: SimultaneousCalibrationSettings = lt.setting(
        default_factory=SimultaneousCalibrationSettings, readonly=True
    )
    focus_parameters: SimultaneousFocusSettings = lt.setting(
        default_factory=SimultaneousFocusSettings, readonly=True
    )
    profile: dict | None = lt.setting(default=None, readonly=True)
    focus_profile: dict | None = lt.setting(default=None, readonly=True)
    peripheral_focus_enabled: bool = lt.setting(default=False, readonly=True)
    enabled: bool = lt.setting(default=False, readonly=True)
    progress: dict = lt.property(
        default_factory=lambda: {"phase": "idle", "frames": 0}, readonly=True
    )

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        default_parameters: dict | None = None,
        default_focus_parameters: dict | None = None,
    ) -> None:
        """Load deployment defaults; persisted Thing settings remain authoritative."""
        self._focus_maps_cache_key: tuple[object, ...] | None = None
        self._focus_maps_cache: dict[str, np.ndarray] | None = None
        self._verified_maps_cache_key: tuple[object, ...] | None = None
        super().__init__(thing_server_interface)
        if default_parameters is not None:
            self.parameters = SimultaneousCalibrationSettings.model_validate(
                default_parameters
            )
        if default_focus_parameters is not None:
            self.focus_parameters = SimultaneousFocusSettings.model_validate(
                default_focus_parameters
            )

    @lt.property
    def processing_backend(self) -> str:
        """Name the NMI backend actually loaded by this running service."""
        response = self._core_process().request("core.capabilities", {}, timeout_s=5)
        result = response.get("result")
        implementation = (
            result.get("implementation") if isinstance(result, dict) else None
        )
        backend = (
            implementation.get("rg_nmi_backend")
            if isinstance(implementation, dict)
            else None
        )
        if not isinstance(backend, str) or not backend:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Core omitted the R/G processing backend"
            )
        return backend

    def _core_process(self) -> FastOFMCoreBlockingProcess:
        """Return one warm standalone core process for simultaneous operations."""
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

    def _external_calibration(  # noqa: PLR0913, PLR0917
        self,
        directory: Path,
        dark: np.ndarray,
        fit: dict[str, np.ndarray],
        holdout: dict[str, np.ndarray],
        mixed_holdout: np.ndarray,
        offsets: list[list[int]],
        white_level: float,
        settings: SimultaneousCalibrationSettings,
    ) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict, dict]:
        """Fit and validate spectral maps in the standalone core."""
        descriptor = write_simultaneous_input(
            directory,
            "simultaneous-calibration-input",
            dark=dark,
            red_fit=fit["red"],
            green_fit=fit["green"],
            red_holdout=holdout["red"],
            green_holdout=holdout["green"],
            mixed_holdout=mixed_holdout,
        )
        response = self._core_process().request(
            "rg.simultaneous.calibrate",
            {
                "input": descriptor,
                "settings": settings.model_dump(mode="json"),
                "channel_offsets_xy": offsets,
                "white_level": white_level,
            },
            timeout_s=settings.timeout_s,
        )
        result = response.get("result")
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("fit_report"), dict)
            or not isinstance(result.get("validation"), dict)
        ):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Simultaneous calibration result is incomplete"
            )
        arrays = read_simultaneous_result(
            result.get("arrays"),
            directory=directory,
            expected_arrays={
                "dark",
                "red_response",
                "green_response",
                "valid",
                "components",
                "component_valid",
            },
        )
        maps = {
            name: arrays[name]
            for name in ("dark", "red_response", "green_response", "valid")
        }
        return (
            maps,
            arrays["components"],
            arrays["component_valid"],
            result["fit_report"],
            result["validation"],
        )

    def _external_focus_sample(  # noqa: PLR0913, PLR0917
        self,
        directory: Path,
        planes: np.ndarray,
        maps: dict[str, np.ndarray],
        settings: SimultaneousFocusSettings,
        offsets: list[list[int]],
        white_level: float,
        boxes: list[PatchBox] | None,
        *,
        adaptive_window_selection: bool,
        inspect_periphery: bool,
    ) -> dict[str, Any]:
        """Unmix and assess one mixed RAW through the core process."""
        descriptor = write_simultaneous_input(
            directory,
            "simultaneous-focus-input",
            planes=planes,
            **maps,
        )
        response = self._core_process().request(
            "rg.simultaneous.focus.sample",
            {
                "input": descriptor,
                "settings": settings.model_dump(mode="json"),
                "channel_offsets_xy": offsets,
                "white_level": white_level,
                "boxes": (
                    None
                    if boxes is None
                    else [value.model_dump(mode="json") for value in boxes]
                ),
                "adaptive_window_selection": adaptive_window_selection,
                "inspect_periphery": inspect_periphery,
            },
            timeout_s=max(
                settings.minimum_capture_budget_s,
                SimultaneousFocusSearchSettings().timeout_s if inspect_periphery else 0,
            ),
        )
        result = response.get("result")
        if not isinstance(result, dict) or set(result) != {
            "measurement",
            "boxes",
            "residual_p95",
            "valid_fraction",
            "peripheral_search",
        }:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Simultaneous focus result is incomplete"
            )
        try:
            return {
                **result,
                "measurement": SimultaneousShiftMeasurement.model_validate(
                    result["measurement"]
                ),
                "boxes": [PatchBox.model_validate(value) for value in result["boxes"]],
            }
        except (TypeError, ValueError) as error:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Simultaneous focus result is invalid"
            ) from error

    def _external_focus_plan(
        self, settings: SimultaneousFocusSettings
    ) -> tuple[float, ...]:
        """Ask the core for the deterministic signed calibration order."""
        response = self._core_process().request(
            "rg.simultaneous.focus.plan",
            {"settings": settings.model_dump(mode="json")},
            timeout_s=5,
        )
        result = response.get("result")
        order = result.get("capture_order_um") if isinstance(result, dict) else None
        if not isinstance(order, list) or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in order
        ):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Simultaneous focus plan is invalid"
            )
        return tuple(float(value) for value in order)

    def _external_focus_fit(
        self,
        observations: list[SimultaneousFocusObservation],
        settings: SimultaneousFocusSettings,
    ) -> SimultaneousFocusCurve:
        """Fit the signed focus curve in the standalone core."""
        response = self._core_process().request(
            "rg.simultaneous.focus.fit",
            {
                "observations": [
                    value.model_dump(mode="json") for value in observations
                ],
                "settings": settings.model_dump(mode="json"),
            },
            timeout_s=10,
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("curve"), dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Simultaneous focus fit is incomplete"
            )
        return SimultaneousFocusCurve.model_validate(result["curve"])

    def _external_focus_evaluate(
        self,
        curve: SimultaneousFocusCurve,
        measurement: SimultaneousShiftMeasurement,
        settings: SimultaneousFocusSettings,
        peripheral_search: dict | None,
    ) -> dict[str, Any]:
        """Authorize optional peripheral evidence and project signed defocus."""
        response = self._core_process().request(
            "rg.simultaneous.focus.evaluate",
            {
                "curve": curve.model_dump(mode="json"),
                "measurement": measurement.model_dump(mode="json"),
                "settings": settings.model_dump(mode="json"),
                "peripheral_search": peripheral_search,
            },
            timeout_s=5,
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(
            result.get("measurement"), dict
        ):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Simultaneous focus evaluation is incomplete"
            )
        try:
            return {
                **result,
                "measurement": SimultaneousShiftMeasurement.model_validate(
                    result["measurement"]
                ),
            }
        except ValueError as error:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Simultaneous focus evaluation is invalid"
            ) from error

    def save_settings(self) -> None:
        """Persist this mechanism's parameters/profile independently and atomically."""
        if self._disable_saving_settings:
            return
        path = Path(self._thing_server_interface.settings_file_path)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    self.settings.model_instance.model_dump(mode="json"),
                    handle,
                    indent=2,
                    allow_nan=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

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
        if name == "profile":
            self._focus_maps_cache_key = None
            self._focus_maps_cache = None
            self._verified_maps_cache_key = None

    @lt.action
    def set_parameters(
        self, parameters: SimultaneousCalibrationSettings
    ) -> SimultaneousCalibrationSettings:
        """Save this mechanism's declared RAW geometry, PWM identity and gates."""
        configuration = getattr(self._cam, "raw_measurement_configuration", None)
        if not isinstance(configuration, dict):
            raise ValueError("Camera does not provide fixed linear RAW measurements")
        parameters.checked_roi(configuration["geometry"]["plane_size"])
        self._commit("parameters", parameters)
        return self.parameters

    @lt.action
    def set_focus_parameters(
        self, parameters: SimultaneousFocusSettings
    ) -> SimultaneousFocusSettings:
        """Save the independent Z grid, registration and correction policy."""
        parameters.checked_focus_roi(
            (self.parameters.plane_roi[2], self.parameters.plane_roi[3])
        )
        self._commit("enabled", False)
        self._commit("focus_parameters", parameters)
        return self.focus_parameters

    @lt.action
    def set_enabled(self, enabled: bool) -> bool:
        """Arm selection only after both independent profiles remain compatible."""
        if enabled and (
            self.status["status"] != "valid" or self.focus_status["status"] != "valid"
        ):
            raise ValueError(
                "Current simultaneous R/G flat-field and focus calibrations are required"
            )
        self._commit("enabled", enabled)
        return self.enabled

    @lt.action
    def set_peripheral_focus_enabled(self, enabled: bool) -> bool:
        """Opt in to bounded same-frame recovery with calibrated-center witnesses."""
        if enabled:
            self.checked_focus_profile()
            if self.parameters.plane_roi[2] * self.parameters.plane_roi[3] > (
                SimultaneousFocusSearchSettings().maximum_plane_pixels
            ):
                raise ValueError("Peripheral focus exceeds the bounded calibrated area")
        self._commit("peripheral_focus_enabled", enabled)
        return self.peripheral_focus_enabled

    @lt.action
    def reset_profile(self) -> None:
        """Forget the active pointer but preserve historical artifacts on disk."""
        self._commit("enabled", False)
        self._commit("profile", None)

    @lt.action
    def reset_focus_profile(self) -> None:
        """Forget only the focus curve while retaining RAW flat-field maps."""
        self._commit("enabled", False)
        self._commit("focus_profile", None)

    def _binding(self) -> dict:
        configuration = getattr(self._cam, "raw_measurement_configuration", None)
        if not isinstance(configuration, dict):
            raise ValueError("Camera does not provide fixed linear RAW measurements")
        self.parameters.checked_roi(configuration["geometry"]["plane_size"])
        return json.loads(
            json.dumps(
                {
                    "camera": configuration,
                    "plane_roi": list(self.parameters.plane_roi),
                    "optics_id": self.parameters.optics_id,
                    "brightness_id": self.parameters.brightness_id,
                }
            )
        )

    @lt.property
    def readiness(self) -> dict:
        """Calibration needs an empty field and WHITE start, but never stage motion."""
        try:
            self._readiness_snapshot()
        except Exception as exc:
            return {"ready": False, "reason": str(exc)}
        return {"ready": True, "reason": "Remove the slide; no stage movement"}

    def _readiness_snapshot(self) -> tuple[ControllerState, LightState]:
        self._binding()
        controller, light = self._illumination._read_controller_and_light()
        if controller.state != "ready" or not controller.idle:
            raise ValueError("Wait for an idle controller")
        if not light.available or light.mode != "white":
            raise ValueError("Select WHITE before simultaneous R/G calibration")
        return controller, light

    @lt.property
    def focus_readiness(self) -> dict:
        """Require current maps, an idle referenced stage and all motors enabled."""
        try:
            self._focus_hardware_snapshot(require_focus_profile=False)
        except Exception as exc:
            return {"ready": False, "reason": str(exc)}
        return {
            "ready": True,
            "reason": "Place focused tissue and confirm exclusive control",
            "flat_field_profile_id": self.profile["id"] if self.profile else None,
        }

    def _focus_hardware_snapshot(self, *, require_focus_profile: bool) -> dict:
        if self.profile is None:
            raise ValueError("No simultaneous R/G RAW flat-field profile")
        profile = copy.deepcopy(self.profile)
        self._compatible(profile)
        calibration = SimultaneousCalibrationSettings.model_validate(
            profile.get("parameters")
        )
        self.focus_parameters.checked_focus_roi(
            (calibration.plane_roi[2], calibration.plane_roi[3])
        )
        if require_focus_profile:
            if self.focus_profile is None:
                raise ValueError("No simultaneous R/G focus curve")
            self._compatible_focus(self.focus_profile)
        return self._stage_and_light_snapshot()

    def _stage_and_light_snapshot(self) -> dict:
        """Check only physical readiness, without assuming calibration compatibility."""
        state = self._stage.controller_state
        if state.get("state") != "ready" or not state.get("motion_idle"):
            raise ValueError("Wait for an idle controller")
        if not state.get("reference_valid"):
            raise ValueError("Set a current local zero before simultaneous R/G focus")
        enabled = state.get("enabled")
        if not isinstance(enabled, dict) or not all(
            enabled.get(f"stepper_{axis}") is True for axis in ("x", "y", "z")
        ):
            raise ValueError("Enable all stage motors before simultaneous R/G focus")
        light = self._illumination.state
        if not light.available or light.mode != "white":
            raise ValueError("Select WHITE before simultaneous R/G focus")
        return copy.deepcopy(state)

    @staticmethod
    def _snapshot_position(state: dict) -> tuple[int, int, int]:
        """Use the position derived from the same controller snapshot."""
        position = state.get("position")
        if not isinstance(position, dict):
            raise ValueError("Controller snapshot has no referenced XYZ position")
        xyz = tuple(position.get(axis) for axis in ("x", "y", "z"))
        if len(xyz) != 3 or any(type(value) is not int for value in xyz):
            raise ValueError("Stage did not return integer XYZ units")
        return cast(tuple[int, int, int], xyz)

    def scan_preload_parameters(self) -> tuple[int, int]:
        """Return the frozen physical preload in stage units for scan transit planning."""
        settings = self.focus_parameters.model_copy(deep=True)
        binding = self._focus_binding()
        units_per_um = binding["stage_z"]["units_per_mm"] / 1000
        preload_float = settings.preload_um * units_per_um
        if not np.isclose(preload_float, round(preload_float)):
            raise ValueError("Focus preload is not representable in stage units")
        return round(preload_float), settings.approach_sign

    def checked_focus_profile(self) -> dict:
        """Return the enabled compatible curve without camera, light or stage effects."""
        if not self.enabled:
            raise ValueError("Simultaneous R/G focus is disabled")
        if self.profile is None:
            raise ValueError("No simultaneous R/G RAW flat-field profile")
        self._compatible(copy.deepcopy(self.profile))
        if self.focus_profile is None:
            raise ValueError("No simultaneous R/G focus curve")
        self._compatible_focus(self.focus_profile)
        return copy.deepcopy(self.focus_profile)

    @lt.property
    def autofocus_readiness(self) -> dict:
        """Report the exact gates used by the manual one-frame autofocus action."""
        try:
            profile = self.checked_focus_profile()
            self._focus_hardware_snapshot(require_focus_profile=True)
        except Exception as exc:
            return {"autofocus_ready": False, "reason": str(exc)}
        return {
            "autofocus_ready": True,
            "reason": "One mixed RAW capture and at most one Z correction",
            "flat_field_profile_id": self.profile["id"] if self.profile else None,
            "focus_profile_id": profile["id"],
        }

    def _directory(self, profile: dict) -> Path:
        return Path(self.data_dir) / str(UUID(profile["id"]))

    def _map_path(self, profile: dict) -> Path:
        """Validate the profile binding and cheap archive identity fields."""
        if (
            profile.get("validated") is not True
            or profile.get("method") != CALIBRATION_METHOD
            or profile.get("binding") != self._binding()
        ):
            raise ValueError(
                "RAW camera, exposure, geometry, optics or diode levels changed"
            )
        path = self._directory(profile) / "spectral-flat-field.npz"
        if not path.is_file() or path.stat().st_size != profile.get("maps_bytes"):
            raise ValueError("Simultaneous R/G calibration maps are missing or changed")
        return path

    def _compatible(self, profile: dict) -> Path:
        """Validate the archive digest once per immutable filesystem identity."""
        path = self._map_path(profile)
        file_state = path.stat()
        key = (
            str(path),
            profile.get("id"),
            profile.get("maps_sha256"),
            file_state.st_dev,
            file_state.st_ino,
            file_state.st_size,
            file_state.st_mtime_ns,
            file_state.st_ctime_ns,
        )
        if key != self._verified_maps_cache_key:
            if file_hash(path) != profile.get("maps_sha256"):
                self._verified_maps_cache_key = None
                raise ValueError(
                    "Simultaneous R/G calibration maps are missing or changed"
                )
            self._verified_maps_cache_key = key
        return path

    def _read_maps(self, path: Path) -> dict[str, np.ndarray]:
        """Read one already verified flat-field archive without pickle."""
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != {
                "dark",
                "red_response",
                "green_response",
                "valid",
            }:
                raise ValueError("Simultaneous R/G calibration map keys changed")
            maps = {
                "dark": np.array(archive["dark"], dtype=np.float32, copy=True),
                "red_response": np.array(
                    archive["red_response"], dtype=np.float32, copy=True
                ),
                "green_response": np.array(
                    archive["green_response"], dtype=np.float32, copy=True
                ),
                "valid": np.array(archive["valid"], dtype=bool, copy=True),
            }
        shape = maps["dark"].shape
        if (
            len(shape) != 3
            or shape[0] != 4
            or maps["red_response"].shape != shape
            or maps["green_response"].shape != shape
            or maps["valid"].shape != shape[1:]
            or not np.isfinite(maps["dark"]).all()
            or not np.isfinite(maps["red_response"]).all()
            or not np.isfinite(maps["green_response"]).all()
            or not np.any(maps["valid"])
        ):
            raise ValueError("Simultaneous R/G calibration map geometry changed")
        return maps

    def _load_maps(self) -> tuple[dict[str, np.ndarray], dict]:
        """Load one verified full flat-field archive."""
        if self.profile is None:
            raise ValueError("No simultaneous R/G RAW flat-field profile")
        profile = copy.deepcopy(self.profile)
        path = self._compatible(profile)
        return self._read_maps(path), profile

    def _load_focus_maps(
        self, settings: SimultaneousFocusSettings
    ) -> tuple[dict[str, np.ndarray], dict, bool, float]:
        """Cache immutable full maps while rechecking the archive identity."""
        if self.profile is None:
            raise ValueError("No simultaneous R/G RAW flat-field profile")
        profile = copy.deepcopy(self.profile)
        path = self._map_path(profile)
        calibration = SimultaneousCalibrationSettings.model_validate(
            profile.get("parameters")
        )
        settings.checked_focus_roi((calibration.plane_roi[2], calibration.plane_roi[3]))
        file_state = path.stat()
        offsets = profile["actual_raw_binding"]["geometry"]["channel_offsets_xy"]
        key = (
            profile["id"],
            profile["maps_sha256"],
            file_state.st_dev,
            file_state.st_ino,
            file_state.st_size,
            file_state.st_mtime_ns,
            file_state.st_ctime_ns,
            tuple(tuple(pair) for pair in offsets),
        )
        if self._focus_maps_cache_key == key and self._focus_maps_cache is not None:
            return self._focus_maps_cache, profile, True, 0.0
        self._compatible(profile)
        maps = self._read_maps(path)
        settings.checked_focus_roi((maps["valid"].shape[1], maps["valid"].shape[0]))
        prepared = {name: np.array(value, copy=True) for name, value in maps.items()}
        for value in prepared.values():
            value.setflags(write=False)
        self._focus_maps_cache_key = key
        self._focus_maps_cache = prepared
        return prepared, profile, False, 0.0

    def _focus_binding(self) -> dict:
        """Bind signed Z optics to exact RAW maps and logical Z units."""
        if self.profile is None:
            raise ValueError("No simultaneous R/G RAW flat-field profile")
        self._compatible(self.profile)
        hardware = self._stage.hardware_settings
        try:
            z_axis = hardware["axes"]["z"]
            stage_z = {
                "units_per_mm": z_axis["units_per_mm"],
                "direction_sign": z_axis["direction_sign"],
            }
        except (KeyError, TypeError) as exc:
            raise ValueError("Stage Z unit binding is unavailable") from exc
        return json.loads(
            json.dumps(
                {
                    "flat_field_profile_id": self.profile["id"],
                    "flat_field_maps_sha256": self.profile["maps_sha256"],
                    "flat_field_binding": self.profile["binding"],
                    "stage_z": stage_z,
                }
            )
        )

    def _focus_directory(self, profile: dict) -> Path:
        return Path(self.data_dir) / str(UUID(profile["id"]))

    def _compatible_focus(self, profile: dict) -> Path:
        if (
            profile.get("validated") is not True
            or profile.get("method") != FOCUS_METHOD
            or profile.get("binding") != self._focus_binding()
            or profile.get("parameters")
            != self.focus_parameters.model_dump(mode="json")
        ):
            raise ValueError(
                "Simultaneous RAW maps, Z units or focus parameters changed"
            )
        path = self._focus_directory(profile) / "focus-evidence.json"
        if (
            not path.is_file()
            or path.stat().st_size != profile.get("evidence_bytes")
            or file_hash(path) != profile.get("evidence_sha256")
        ):
            raise ValueError("Simultaneous R/G focus evidence is missing or changed")
        SimultaneousFocusCurve.model_validate(profile.get("curve"))
        return path

    @lt.property
    def status(self) -> dict:
        """Report only this mechanism; separate R/G status is owned elsewhere."""
        if self.profile is None:
            return {"status": "not_calibrated", "reason": "No saved RAW maps"}
        try:
            self._compatible(self.profile)
        except Exception as exc:
            return {"status": "incompatible", "reason": str(exc)}
        return {
            "status": "valid",
            "reason": "Separate source holdouts and mixed RAW additivity passed",
            "enabled": self.enabled,
            "profile_id": self.profile["id"],
        }

    @lt.property
    def focus_status(self) -> dict:
        """Report the signed-Z profile separately from spectral flat-field maps."""
        if self.focus_profile is None:
            return {
                "status": "not_calibrated",
                "reason": "No saved simultaneous R/G focus curve",
                "enabled": False,
            }
        try:
            self._compatible_focus(self.focus_profile)
        except Exception as exc:
            return {
                "status": "incompatible",
                "reason": str(exc),
                "enabled": False,
            }
        return {
            "status": "valid",
            "reason": "Two-sided fit and independent holdouts passed",
            "enabled": self.enabled,
            "profile_id": self.focus_profile["id"],
        }

    @lt.property
    def manifest(self) -> dict:
        """Describe the isolated calibration stages, inputs and invalidators."""
        stages = [
            CalibrationStage(
                id="preflight",
                name="Empty field and fixed RAW settings",
                description="Bind camera, crop, optics and diode-level identity.",
                inputs=["prepared", "parameters", "camera", "illumination"],
                outputs=["binding"],
                action="rg_simultaneous.calibrate",
                success_criterion="Fixed RAW mode and WHITE start",
                timeout_setting="parameters.timeout_s",
                cancellation="Restore WHITE; preserve the prior profile",
                hardware_required=False,
            ),
            CalibrationStage(
                id="source_fields",
                name="OFF, RED-only and GREEN-only fields",
                description="Fit four-channel spatial response columns in phase-matched cycles.",
                inputs=["binding", "empty_field"],
                outputs=["dark", "red_response", "green_response"],
                action="rg_simultaneous.calibrate",
                success_criterion="Signal, saturation and conditioning gates pass",
                timeout_setting="parameters.timeout_s",
                cancellation="Restore WHITE; preserve the prior profile",
                hardware_required=True,
            ),
            CalibrationStage(
                id="mixed_holdout",
                name="Independent simultaneous-light validation",
                description="Unmix unused RED, GREEN and RED+GREEN exposures.",
                inputs=["response_maps", "holdout_frames"],
                outputs=["validation", "preview"],
                action="rg_simultaneous.calibrate",
                success_criterion="Leakage, flatness, scale and residual gates pass",
                timeout_setting="parameters.timeout_s",
                cancellation="Restore WHITE; preserve the prior profile",
                hardware_required=True,
            ),
        ]
        return CalibrationManifest(
            id="simultaneous_rg_flat_field",
            name="Simultaneous R/G RAW flat-field",
            description="Independent maps for separating one mixed RAW exposure into RED and GREEN components.",
            prerequisites=[
                "Empty uniformly illuminated field",
                "Fixed manual exposure and gain",
                "Declared stable RED/GREEN PWM levels",
                "Idle stage and exclusive control",
            ],
            parameter_refs=["rg_simultaneous.parameters", "illumination.settle_ms"],
            stages=stages,
            results=["profile", "status", "validation preview"],
            acceptance_criteria=[
                "No RAW saturation",
                "Two well-conditioned source spectra",
                "Independent source-only holdouts remain flat with low leakage",
                "Independent mixed holdout reconstructs within the declared residual",
                "WHITE restored before the profile pointer is replaced",
            ],
            invalidated_by=[
                "RAW camera/exposure/geometry",
                "plane ROI",
                "optics_id",
                "brightness_id",
            ],
        ).model_dump(mode="json")

    @lt.property
    def focus_manifest(self) -> dict:
        """Describe the separate tissue/Z calibration without merging flat-field."""
        stages = [
            CalibrationStage(
                id="preflight",
                name="Focused tissue and current RAW maps",
                description="Freeze stage units, spectral maps and focus parameters.",
                inputs=["prepared", "profile", "focus_parameters", "stage"],
                outputs=["focus_binding", "start_position"],
                action="rg_simultaneous.calibrate_focus",
                success_criterion="Referenced idle stage, motors on and WHITE active",
                timeout_setting="focus_parameters.calibration_timeout_s",
                cancellation="Return only from a verified position; preserve prior curve",
                hardware_required=True,
            ),
            CalibrationStage(
                id="fixed_windows",
                name="Fixed tissue windows",
                description=(
                    "Crop calibrated maps first, then select texture windows once "
                    "at operator focus."
                ),
                inputs=["mixed_raw_at_zero", "spectral_maps", "focus_plane_roi"],
                outputs=["window_ids", "zero_measurement"],
                action="rg_simultaneous.calibrate_focus",
                success_criterion="At least the configured number of tissue windows pass",
                timeout_setting="focus_parameters.calibration_timeout_s",
                cancellation="Restore WHITE and verified start Z",
                hardware_required=True,
            ),
            CalibrationStage(
                id="z_grid",
                name="Two-sided Z grid",
                description="Use one mixed RAW per point with the same final approach.",
                inputs=["window_ids", "calibration_positions_um", "preload_um"],
                outputs=["fit_observations", "holdout_observations"],
                action="rg_simultaneous.calibrate_focus",
                success_criterion="Every declared point passes measurement QC",
                timeout_setting="focus_parameters.calibration_timeout_s",
                cancellation="Return only from a verified position; preserve prior curve",
                hardware_required=True,
            ),
            CalibrationStage(
                id="validation",
                name="Signed curve and holdouts",
                description="Fit the 2D optical axis and validate untouched points.",
                inputs=["fit_observations", "holdout_observations"],
                outputs=["focus_profile", "focus_status"],
                action="rg_simultaneous.calibrate_focus",
                success_criterion="Fit, holdout and cross-track gates pass after safe return",
                timeout_setting="focus_parameters.calibration_timeout_s",
                cancellation="Preserve prior curve",
                hardware_required=False,
            ),
        ]
        return CalibrationManifest(
            id="simultaneous_rg_focus",
            name="Simultaneous R/G focus curve",
            description="Independent signed-Z model from spectrally separated components of one RAW exposure.",
            prerequisites=[
                "Valid simultaneous R/G RAW flat-field",
                "Focused textured tissue",
                "Current local zero and enabled motors",
                "Verified Z clearance for grid plus preload",
            ],
            parameter_refs=["rg_simultaneous.focus_parameters"],
            stages=stages,
            results=["focus_profile", "focus_status", "focus evidence"],
            acceptance_criteria=[
                "Focus ROI is bounded by the accepted spectral flat-field maps",
                "All points use fixed tissue windows",
                "Fit and independent two-sided holdouts pass",
                "No extrapolation beyond the calibrated Z range",
                "WHITE and the commanded start Z are restored before activation",
            ],
            invalidated_by=[
                "simultaneous flat-field profile",
                "RAW camera or brightness binding",
                "stage Z units or direction",
                "focus_parameters",
            ],
        ).model_dump(mode="json")

    @contextmanager
    def _session(self, prepared: bool) -> Iterator[Acquisition]:
        with acquisition_session(self, prepared) as run:
            yield run

    def _capture_focus_sample(
        self,
        phase: str,
        settings: SimultaneousFocusSettings,
        boxes: list[PatchBox] | None = None,
        *,
        adaptive_window_selection: bool = True,
        inspect_periphery: bool = False,
    ) -> tuple[SimultaneousShiftMeasurement, list[PatchBox], dict]:
        """Capture one memory-only mixed RAW and measure its unmixed displacement."""
        processing_started = time.monotonic()
        phase_started = processing_started
        maps, flat_profile, map_cache_hit, map_crop_s = self._load_focus_maps(settings)
        processing_timing = {
            "map_load_s": time.monotonic() - phase_started,
            "map_crop_s": map_crop_s,
            "focus_map_cache_hit": map_cache_hit,
            "nmi_backend": self.processing_backend,
        }
        flat_roi = self.parameters.plane_roi
        focus_roi = settings.focus_plane_roi
        focus_x, focus_y, focus_width, focus_height = focus_roi
        capture_roi = (
            flat_roi[0] + focus_x,
            flat_roi[1] + focus_y,
            focus_width,
            focus_height,
        )
        if inspect_periphery:
            if boxes is not None or not adaptive_window_selection:
                raise ValueError("Fixed Z-calibration windows cannot expand adaptively")
            if (
                flat_roi[2] * flat_roi[3]
                > SimultaneousFocusSearchSettings().maximum_plane_pixels
            ):
                raise ValueError("Peripheral diagnostic exceeds the bounded RAW area")
            capture_roi = flat_roi
        phase_started = time.monotonic()
        with self._session(True) as run:
            run.select_red_green_probe()
            planes, frame = run.raw_frame(
                phase,
                persist_raw=False,
                plane_roi=capture_roi,
            )
            raw_binding = copy.deepcopy(run.raw_binding)
        processing_timing["capture_session_s"] = time.monotonic() - phase_started
        try:
            if raw_binding is None or raw_binding != flat_profile.get(
                "actual_raw_binding"
            ):
                raise ValueError(
                    "Live RAW capture binding differs from flat-field data"
                )
            geometry = raw_binding["geometry"]
            phase_started = time.monotonic()
            result = self._external_focus_sample(
                run.directory / f"{phase}-core",
                planes,
                maps,
                settings,
                geometry["channel_offsets_xy"],
                geometry["white_level"],
                boxes,
                adaptive_window_selection=adaptive_window_selection,
                inspect_periphery=inspect_periphery,
            )
            processing_timing["core_processing_s"] = time.monotonic() - phase_started
            measurement = result["measurement"]
            selected = result["boxes"]
            residual_p95 = result["residual_p95"]
            valid_fraction = result["valid_fraction"]
            peripheral = result["peripheral_search"]
        except (FastOFMCoreError, KeyError, TypeError, ValueError) as exc:
            raise SimultaneousFocusSampleRefusalError(str(exc)) from exc
        details: dict[str, Any] = {
            "capture_id": run.id,
            "capture_path": str(run.directory),
            "frame": copy.deepcopy(frame),
            "measurement": measurement.model_dump(mode="json"),
            "boxes": [box.model_dump(mode="json") for box in selected],
            "flat_field_plane_roi": list(flat_roi),
            "focus_plane_roi": list(focus_roi),
            "capture_plane_roi": list(capture_roi),
            "focus_working_size": [
                focus_width // settings.processing_downsample,
                focus_height // settings.processing_downsample,
            ],
            "residual_p95": residual_p95,
            "valid_fraction": valid_fraction,
            "white_restored": bool(run.report.get("white_restored")),
            "processing_timing": {
                **processing_timing,
                "total_s": time.monotonic() - processing_started,
            },
        }
        if inspect_periphery:
            if not isinstance(peripheral, dict):
                raise SimultaneousFocusSampleRefusalError(
                    "Core omitted peripheral focus diagnostics"
                )
            flat_x, flat_y, _, _ = self.parameters.plane_roi
            for window in peripheral.get("windows", []):
                x, y, width, height = window["native_plane_roi"]
                window["sensor_plane_roi"] = [flat_x + x, flat_y + y, width, height]
            details["peripheral_search"] = {
                **peripheral,
                "attempted": peripheral.get("status") != "not_needed",
                "used": False,
                "same_exposure": True,
                "additional_capture_count": 0,
                "retained_raw_bytes": planes.nbytes,
                "elapsed_s": processing_timing.get("core_processing_s", 0.0),
            }
            details["processing_timing"]["total_s"] = (
                time.monotonic() - processing_started
            )
        return measurement, selected, details

    def _new_focus_run(self, kind: str) -> tuple[Path, dict[str, Any]]:
        identifier = str(uuid4())
        directory = Path(self.data_dir) / identifier
        directory.mkdir()
        return directory, {
            "id": identifier,
            "kind": kind,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    @lt.action
    def measure_focus(
        self, prepared: bool = False, inspect_periphery: bool = False
    ) -> dict:
        """Measure one mixed RAW on tissue without moving Z or changing profiles."""
        if not prepared:
            raise ValueError("Confirm tissue and exclusive microscope control")
        self._focus_hardware_snapshot(require_focus_profile=False)
        settings = self.focus_parameters.model_copy(deep=True)
        directory, report = self._new_focus_run("measure")
        if inspect_periphery:
            measurement, _boxes, details = self._capture_focus_sample(
                "simultaneous-focus-measure", settings, inspect_periphery=True
            )
        else:
            measurement, _boxes, details = self._capture_focus_sample(
                "simultaneous-focus-measure", settings
            )
        report.update(
            status=measurement.status,
            diagnostic_only=True,
            autofocus_authorized=False,
            parameters=settings.model_dump(mode="json"),
            sample=details,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        write_json(directory / "focus-report.json", report)
        return {
            **report,
            "report_ref": f"rg_simultaneous/{directory.name}/focus-report.json",
        }

    def _phase_matched_average(
        self, run: Acquisition, phase: str, cycles: int
    ) -> dict[Mode, np.ndarray]:
        totals: dict[Mode, np.ndarray] = {}
        for cycle in range(cycles):
            for mode in cast(tuple[Mode, Mode], ("red", "green")):
                run.select(mode)
                planes, _metadata = run.raw_frame(
                    f"{phase}-{cycle + 1}-{mode}",
                    persist_raw=False,
                    plane_roi=self.parameters.plane_roi,
                )
                if mode not in totals:
                    totals[mode] = planes.astype(np.float32)
                else:
                    totals[mode] += planes
        return {mode: value / cycles for mode, value in totals.items()}

    @lt.action
    def capture_probe(self, prepared: bool = False) -> dict:
        """Persist OFF/RED/GREEN/MIXED RAW evidence without changing either profile."""
        with self._session(prepared) as run:
            for mode in ("off", "red", "green"):
                run.select(cast(Literal["white", "red", "green", "off"], mode))
                run.raw_frame(f"simultaneous-probe-{mode}")
            run.select_red_green_probe()
            run.raw_frame("simultaneous-probe-mixed")
        return {
            "id": run.id,
            "directory": str(run.directory),
            "measurement_domain": "linear-raw12-bayer",
            "experimental": True,
            "profile_changed": False,
            "frames": copy.deepcopy(run.report["frames"]),
            "white_restored": True,
        }

    @lt.action
    def calibrate(self, prepared: bool = False) -> dict:
        """Fit and accept only this mechanism's maps from unused RAW holdouts."""
        settings = self.parameters
        profile: dict[str, Any]
        with self._session(prepared) as run:
            run.select("off")
            dark = run.raw_average(
                "simultaneous-calibration-dark",
                settings.dark_frames,
                settings.plane_roi,
            )
            fit = self._phase_matched_average(
                run, "simultaneous-calibration-fit", settings.fit_cycles
            )
            holdout = self._phase_matched_average(
                run,
                "simultaneous-calibration-holdout",
                settings.validation_cycles,
            )
            run.select_red_green_probe()
            mixed_holdout = run.raw_average(
                "simultaneous-calibration-mixed-holdout",
                settings.validation_cycles,
                settings.plane_roi,
            )
            if run.raw_binding is None:
                raise RuntimeError("RAW calibration did not record a camera binding")
            geometry = run.raw_binding["geometry"]
            settings.checked_roi(geometry["plane_size"])
            offsets = geometry["channel_offsets_xy"]
            maps, components, valid, fit_report, validation = (
                self._external_calibration(
                    run.directory / "core-calibration",
                    dark,
                    fit,
                    holdout,
                    mixed_holdout,
                    offsets,
                    geometry["white_level"],
                    settings,
                )
            )
            path = run.directory / "spectral-flat-field.npz"
            np.savez_compressed(
                path,
                dark=maps["dark"],
                red_response=maps["red_response"],
                green_response=maps["green_response"],
                valid=maps["valid"],
            )
            panels = []
            for component in components:
                values = component[valid]
                low, high = np.percentile(values, [0.5, 99.5])
                if high <= low:
                    high = low + 1
                panel = np.clip(
                    (np.nan_to_num(component, nan=low) - low) / (high - low), 0, 1
                )
                panel[~valid] = 0
                panels.append(np.rint(panel * 255).astype(np.uint8))
            preview = Image.fromarray(np.hstack(panels))
            preview.thumbnail((1200, 600))
            preview.save(run.directory / "validation.png")
            profile = {
                "id": run.id,
                "method": CALIBRATION_METHOD,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "validated": True,
                "binding": copy.deepcopy(run.binding),
                "actual_raw_binding": copy.deepcopy(run.raw_binding),
                "parameters": settings.model_dump(mode="json"),
                "acquisition_sequence": {
                    "dark_frames": settings.dark_frames,
                    "fit_order": ["red", "green"] * settings.fit_cycles,
                    "holdout_order": ["red", "green"] * settings.validation_cycles,
                    "mixed_holdout_frames": settings.validation_cycles,
                },
                "fit": fit_report,
                "validation": validation,
                "maps_sha256": file_hash(path),
                "maps_bytes": path.stat().st_size,
                "working_separate_rg_changed": False,
            }
            write_json(run.directory / "profile.json", profile)
        lt.raise_if_cancelled()
        self._commit("enabled", False)
        self._commit("profile", profile)
        self.progress = {
            "phase": "complete",
            "frames": len(run.report["frames"]),
            "id": run.id,
        }
        return copy.deepcopy(profile)

    @lt.action
    def calibrate_focus(  # noqa: C901, PLR0912, PLR0915
        self, prepared: bool = False
    ) -> dict:
        """Fit a separate signed-Z curve from one mixed RAW at each fixed-grid point."""
        if not prepared:
            raise ValueError(
                "Confirm focused tissue, Z clearance and exclusive microscope control"
            )
        self._focus_hardware_snapshot(require_focus_profile=False)
        settings = self.focus_parameters.model_copy(deep=True)
        binding = self._focus_binding()
        hardware = self._stage.hardware_settings
        units_per_um = hardware["axes"]["z"]["units_per_mm"] / 1000
        if not np.isfinite(units_per_um) or units_per_um <= 0:
            raise ValueError("Stage Z units per micrometre are invalid")
        preload_units_float = settings.preload_um * units_per_um
        if not np.isclose(preload_units_float, round(preload_units_float)):
            raise ValueError("Focus preload is not representable in stage units")
        preload_units = round(preload_units_float)
        start = tuple(self._stage.get_xyz_position())
        if len(start) != 3 or any(type(value) is not int for value in start):
            raise ValueError("Stage did not return integer XYZ units")
        order = self._external_focus_plan(settings)
        targets: dict[float, int] = {}
        for offset_um in settings.calibration_positions_um:
            offset_units = offset_um * units_per_um
            if not np.isclose(offset_units, round(offset_units)):
                raise ValueError("Focus Z grid is not representable in stage units")
            targets[offset_um] = start[2] + round(offset_units)
        planned_path: list[dict[str, int]] = []
        for offset_um in (*order, 0):
            target_z = targets[offset_um]
            planned_path.extend(
                (
                    {"z": target_z - settings.approach_sign * preload_units},
                    {"z": target_z},
                )
            )
        self._stage.validate_path(planned_path)

        directory, report = self._new_focus_run("calibrate")
        report.update(
            status="running",
            binding=binding,
            parameters=settings.model_dump(mode="json"),
            start_position_units=list(start),
            capture_order_um=list(order),
            planned_z_path_units=[row["z"] for row in planned_path],
            samples=[],
            moves=[],
            returned_to_start=False,
        )
        write_json(directory / "focus-report.json", report)
        deadline = time.monotonic() + settings.calibration_timeout_s
        expected_position = start
        fixed_boxes: list[PatchBox] | None = None
        observations: list[SimultaneousFocusObservation] = []
        curve: SimultaneousFocusCurve | None = None
        failure: BaseException | None = None

        def check(phase: str) -> None:
            lt.raise_if_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("Simultaneous R/G focus calibration timed out")
            self._focus_hardware_snapshot(require_focus_profile=False)
            if self.focus_parameters != settings or self._focus_binding() != binding:
                raise ValueError("Frozen simultaneous focus binding changed")
            if tuple(self._stage.get_xyz_position()) != expected_position:
                raise ValueError("XYZ changed outside the simultaneous focus plan")
            self.progress = {
                "phase": phase,
                "frames": len(observations),
                "id": directory.name,
            }

        def move(offset_um: float) -> None:
            nonlocal expected_position
            check("focus_move")
            target = (start[0], start[1], targets[offset_um])
            self._stage.move_z_with_preload(
                target[2], preload_units, settings.approach_sign
            )
            actual = tuple(self._stage.get_xyz_position())
            if actual != target:
                raise ValueError("Focus move did not reach its commanded target")
            expected_position = target
            report["moves"].append(
                {
                    "offset_um": offset_um,
                    "target_units": list(target),
                    "confirmed_units": list(actual),
                    "preload_units": preload_units,
                    "approach_sign": settings.approach_sign,
                }
            )
            write_json(directory / "focus-report.json", report)

        try:
            for index, offset_um in enumerate(order):
                move(offset_um)
                check("focus_capture")
                measurement, selected, details = self._capture_focus_sample(
                    f"simultaneous-focus-calibration-{index + 1}",
                    settings,
                    fixed_boxes,
                    adaptive_window_selection=False,
                )
                if fixed_boxes is None:
                    fixed_boxes = selected
                if [box.patch_id for box in selected] != [
                    box.patch_id for box in fixed_boxes
                ]:
                    raise ValueError("Focus calibration tissue windows changed")
                details["z_um"] = offset_um
                details["role"] = (
                    "holdout" if offset_um in settings.holdout_positions_um else "fit"
                )
                report["samples"].append(details)
                write_json(directory / "focus-report.json", report)
                if measurement.status != "ready":
                    raise ValueError(
                        f"Focus sample at {offset_um:+g} um refused: "
                        f"{measurement.reason}"
                    )
                observations.append(
                    SimultaneousFocusObservation(
                        capture_id=details["capture_id"],
                        z_um=offset_um,
                        role=cast(Literal["fit", "holdout"], details["role"]),
                        measurement=measurement,
                    )
                )
            curve = self._external_focus_fit(observations, settings)
            report["curve"] = curve.model_dump(mode="json")
            report["status"] = "validated"
        except BaseException as exc:
            failure = exc
            report["status"] = "failed"
            report["error"] = str(exc)
            raise
        finally:
            try:
                current = tuple(self._stage.get_xyz_position())
                if current != start:
                    if current != expected_position:
                        raise ValueError(
                            "Cannot return after an unverified stage position change"
                        )
                    self._focus_hardware_snapshot(require_focus_profile=False)
                    self._stage.move_z_with_preload(
                        start[2], preload_units, settings.approach_sign
                    )
                final = tuple(self._stage.get_xyz_position())
                if final != start:
                    raise ValueError("Focus calibration did not return to its start")
                report["returned_to_start"] = True
                report["final_position_units"] = list(final)
            except BaseException as return_error:
                report["return_error"] = str(return_error)
                if failure is None:
                    raise
                failure.add_note(f"Focus calibration return: {return_error}")
            finally:
                report["completed_at"] = datetime.now(timezone.utc).isoformat()
                write_json(directory / "focus-report.json", report)

        if curve is None or fixed_boxes is None:
            raise RuntimeError("Focus calibration completed without a validated curve")
        evidence = {
            "binding": binding,
            "parameters": settings.model_dump(mode="json"),
            "capture_order_um": list(order),
            "boxes": [box.model_dump(mode="json") for box in fixed_boxes],
            "observations": [row.model_dump(mode="json") for row in observations],
            "curve": curve.model_dump(mode="json"),
        }
        evidence_path = directory / "focus-evidence.json"
        write_json(evidence_path, evidence)
        profile = {
            "id": directory.name,
            "method": FOCUS_METHOD,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "validated": True,
            "binding": binding,
            "parameters": settings.model_dump(mode="json"),
            "curve": curve.model_dump(mode="json"),
            "evidence_sha256": file_hash(evidence_path),
            "evidence_bytes": evidence_path.stat().st_size,
            "working_separate_rg_changed": False,
        }
        write_json(directory / "focus-profile.json", profile)
        lt.raise_if_cancelled()
        self._commit("enabled", False)
        self._commit("focus_profile", profile)
        self.progress = {
            "phase": "focus_complete",
            "frames": len(observations),
            "id": directory.name,
        }
        return copy.deepcopy(profile)

    def _fallback_error(
        self, reason: str, directory: Path, report: dict[str, Any]
    ) -> SimultaneousFocusFallbackEligibleError:
        report.update(
            status="refused",
            error=reason,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        write_json(directory / "focus-report.json", report)
        return SimultaneousFocusFallbackEligibleError(
            reason,
            result_id=directory.name,
            report_ref=f"rg_simultaneous/{directory.name}/focus-report.json",
            peripheral_search=report.get("peripheral_search"),
            mixed_capture_count=report.get("mixed_capture_count", 0),
        )

    def _verified_fallback_error(
        self,
        reason: str,
        start: tuple[int, int, int],
        directory: Path,
        report: dict[str, Any],
        *,
        cause: BaseException | None = None,
    ) -> SimultaneousFocusFallbackEligibleError:
        """Permit fallback only from the unchanged start position under WHITE."""
        try:
            current = self._snapshot_position(self._stage_and_light_snapshot())
            if current != start:
                raise ValueError(
                    "Simultaneous refusal did not leave the stage at its verified start"
                )
        except BaseException as unsafe:
            report.update(
                status="failed",
                error=reason,
                fallback_refused=str(unsafe),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "focus-report.json", report)
            if cause is not None:
                raise unsafe from cause
            raise
        return self._fallback_error(reason, directory, report)

    def _autofocus(  # noqa: C901, PLR0912, PLR0915
        self,
        prepared: bool,
        scan_preload: CompletedScanPreload | None = None,
    ) -> dict[str, Any]:
        """Use one mixed RAW and at most one modelled Z correction."""
        if not prepared:
            raise ValueError("Confirm tissue, Z clearance and exclusive control")
        if not self.enabled:
            raise ValueError("Simultaneous R/G focus is not enabled")
        initial_state = self._stage_and_light_snapshot()
        settings = self.focus_parameters.model_copy(deep=True)
        peripheral_enabled = self.peripheral_focus_enabled
        start = self._snapshot_position(initial_state)
        if scan_preload is not None and (
            initial_state.get("reference_id") != scan_preload.reference_id
            or initial_state.get("motion_revision") != scan_preload.motion_revision
            or start != scan_preload.target_xyz
        ):
            raise ValueError("Completed scan preload no longer matches the live stage")
        directory, report = self._new_focus_run("autofocus")
        report.update(
            status="running",
            parameters=settings.model_dump(mode="json"),
            start_position_units=list(start),
            mixed_capture_count=0,
            correction_count=0,
            peripheral_focus_enabled=peripheral_enabled,
            peripheral_search={
                "attempted": False,
                "used": False,
                "status": "not_attempted",
            },
        )
        write_json(directory / "focus-report.json", report)
        try:
            if self.focus_profile is None:
                raise self._verified_fallback_error(
                    "No simultaneous R/G focus curve", start, directory, report
                )
            try:
                self._compatible_focus(self.focus_profile)
                curve = SimultaneousFocusCurve.model_validate(
                    self.focus_profile["curve"]
                )
                binding = self._focus_binding()
            except (KeyError, TypeError, ValueError) as exc:
                raise self._verified_fallback_error(
                    str(exc), start, directory, report, cause=exc
                ) from exc
            units_per_um = binding["stage_z"]["units_per_mm"] / 1000
            preload_float = settings.preload_um * units_per_um
            if not np.isclose(preload_float, round(preload_float)):
                raise self._verified_fallback_error(
                    "Focus preload is not representable in stage units",
                    start,
                    directory,
                    report,
                )
            preload_units = round(preload_float)
            if scan_preload is not None and (
                scan_preload.preload_units != preload_units
                or scan_preload.approach_sign != settings.approach_sign
            ):
                raise ValueError("Completed scan preload parameters changed")
            deadline = time.monotonic() + settings.autofocus_timeout_s
            if settings.autofocus_timeout_s < settings.minimum_capture_budget_s:
                raise self._verified_fallback_error(
                    "Autofocus timeout leaves no complete capture budget",
                    start,
                    directory,
                    report,
                )
            if scan_preload is None:
                try:
                    self._stage.validate_path(
                        [
                            {"z": start[2] - settings.approach_sign * preload_units},
                            {"z": start[2]},
                        ]
                    )
                except ValueError as exc:
                    raise self._verified_fallback_error(
                        str(exc), start, directory, report, cause=exc
                    ) from exc
                # From the first movement onward, any motion failure is ambiguous and
                # must stop instead of entering another autofocus implementation.
                self._stage.move_z_with_preload(
                    start[2], preload_units, settings.approach_sign
                )
            report["initial_preload"] = {
                "preload_units": preload_units,
                "approach_sign": settings.approach_sign,
                "target_units": list(start),
                "combined_with_xy_transit": scan_preload is not None,
            }
            write_json(directory / "focus-report.json", report)
            if time.monotonic() >= deadline:
                raise self._verified_fallback_error(
                    "Autofocus timeout expired before mixed capture",
                    start,
                    directory,
                    report,
                )
            try:
                if peripheral_enabled:
                    measurement, _boxes, details = self._capture_focus_sample(
                        "simultaneous-focus-autofocus", settings, inspect_periphery=True
                    )
                else:
                    measurement, _boxes, details = self._capture_focus_sample(
                        "simultaneous-focus-autofocus", settings
                    )
            except SimultaneousFocusSampleRefusalError as exc:
                raise self._verified_fallback_error(
                    str(exc), start, directory, report, cause=exc
                ) from exc
            report["mixed_capture_count"] = 1
            report["sample"] = details
            search = details.get("peripheral_search", report["peripheral_search"])
            report["peripheral_search"] = search
            try:
                evaluation = self._external_focus_evaluate(
                    curve,
                    measurement,
                    settings,
                    (
                        search
                        if measurement.status != "ready" and peripheral_enabled
                        else None
                    ),
                )
            except (FastOFMCoreError, ValueError) as exc:
                raise self._verified_fallback_error(
                    str(exc), start, directory, report, cause=exc
                ) from exc
            resolved = evaluation["measurement"]
            verification = evaluation.get("peripheral_validation")
            if isinstance(verification, dict):
                search.update(verification)
                search["autofocus_authorized"] = bool(verification.get("authorized"))
                if resolved.status == "ready":
                    details["central_measurement"] = measurement.model_dump(mode="json")
                    details["measurement"] = resolved.model_dump(mode="json")
                    details["selected_focus_domain"] = "central_witnessed_peripheral"
                    search["diagnostic_only"] = False
                else:
                    search["stop_reason"] = verification.get("reason")
            measurement = resolved
            write_json(directory / "focus-report.json", report)
            if time.monotonic() >= deadline:
                raise self._verified_fallback_error(
                    "Autofocus deadline expired after mixed capture/search",
                    start,
                    directory,
                    report,
                )
            if measurement.status != "ready":
                raise self._verified_fallback_error(
                    measurement.reason, start, directory, report
                )
            defocus_um = evaluation.get("defocus_um")
            cross_track_px = evaluation.get("cross_track_px")
            if (
                isinstance(defocus_um, bool)
                or not isinstance(defocus_um, (int, float))
                or not np.isfinite(defocus_um)
                or isinstance(cross_track_px, bool)
                or not isinstance(cross_track_px, (int, float))
                or not np.isfinite(cross_track_px)
            ):
                exc = ValueError("Core omitted a finite simultaneous focus projection")
                raise self._verified_fallback_error(
                    str(exc), start, directory, report, cause=exc
                ) from exc
            defocus_um = float(defocus_um)
            cross_track_px = float(cross_track_px)
            correction_um = -defocus_um
            report["inferred_defocus_um"] = defocus_um
            report["cross_track_px"] = cross_track_px
            if search.get("attempted"):
                if self._snapshot_position(self._stage_and_light_snapshot()) != start:
                    raise ValueError("Stage changed during peripheral focus search")
                if (
                    self.focus_parameters != settings
                    or self._focus_binding() != binding
                ):
                    raise ValueError(
                        "Focus context changed during peripheral focus search"
                    )
            if self.peripheral_focus_enabled != peripheral_enabled:
                raise ValueError(
                    "Frozen peripheral focus setting changed during capture"
                )
            if abs(defocus_um) <= settings.focus_tolerance_um:
                search["used"] = bool(
                    search.get("authorized") and search.get("peripheral_patch_count", 0)
                )
                report.update(
                    status="focused",
                    correction_um=0,
                    final_estimated_error_um=defocus_um,
                    final_position_units=list(start),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                write_json(directory / "focus-report.json", report)
                return {
                    **report,
                    "focus_method": "rg_simultaneous",
                    "fallback_count": 0,
                    "report_ref": f"rg_simultaneous/{directory.name}/focus-report.json",
                }
            if abs(correction_um) > settings.maximum_correction_um:
                raise self._verified_fallback_error(
                    "Required correction exceeds the configured range",
                    start,
                    directory,
                    report,
                )
            correction_units = round(correction_um * units_per_um)
            if correction_units == 0:
                raise self._verified_fallback_error(
                    "Required correction is below stage resolution",
                    start,
                    directory,
                    report,
                )
            actual_correction_um = correction_units / units_per_um
            estimated_error_um = defocus_um + actual_correction_um
            if abs(estimated_error_um) > settings.focus_tolerance_um:
                raise self._verified_fallback_error(
                    "One-shot correction would leave the focus tolerance",
                    start,
                    directory,
                    report,
                )
            if time.monotonic() >= deadline:
                raise self._verified_fallback_error(
                    "Autofocus timeout expired before correction",
                    start,
                    directory,
                    report,
                )
            current_state = self._stage_and_light_snapshot()
            try:
                if (
                    self.focus_parameters != settings
                    or self._focus_binding() != binding
                    or self.peripheral_focus_enabled != peripheral_enabled
                ):
                    raise ValueError("Frozen simultaneous focus binding changed")
            except ValueError as exc:
                raise self._verified_fallback_error(
                    str(exc), start, directory, report, cause=exc
                ) from exc
            if self._snapshot_position(current_state) != start:
                raise ValueError("XYZ changed outside the simultaneous focus plan")
            target = (start[0], start[1], start[2] + correction_units)
            try:
                self._stage.validate_path(
                    [
                        {"z": target[2] - settings.approach_sign * preload_units},
                        {"z": target[2]},
                    ]
                )
            except ValueError as exc:
                raise self._verified_fallback_error(
                    str(exc), start, directory, report, cause=exc
                ) from exc
            self._stage.move_z_with_preload(
                target[2], preload_units, settings.approach_sign
            )
            final = self._snapshot_position(self._stage_and_light_snapshot())
            if final != target:
                raise ValueError(
                    "Simultaneous focus correction did not reach its target"
                )
            search["used"] = bool(
                search.get("authorized") and search.get("peripheral_patch_count", 0)
            )
            report.update(
                status="focused",
                correction_count=1,
                correction_um=actual_correction_um,
                target_position_units=list(target),
                final_position_units=list(final),
                final_estimated_error_um=estimated_error_um,
                independent_post_move_verification=False,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "focus-report.json", report)
            return {
                **report,
                "focus_method": "rg_simultaneous",
                "fallback_count": 0,
                "report_ref": f"rg_simultaneous/{directory.name}/focus-report.json",
            }
        except SimultaneousFocusFallbackEligibleError:
            raise
        except BaseException as exc:
            report.update(
                status="failed",
                error=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "focus-report.json", report)
            raise

    @lt.action
    def autofocus(
        self, prepared: bool = False, white_dz: int | None = None
    ) -> dict[str, Any]:
        """Use simultaneous R/G, then one explicit WHITE fallback on optical refusal."""
        try:
            return self._autofocus(prepared)
        except SimultaneousFocusFallbackEligibleError as simultaneous_error:
            return self._white_after_refusal(simultaneous_error, white_dz)

    def autofocus_for_scan(
        self,
        *,
        white_dz: int | None = None,
        scan_preload: CompletedScanPreload | None = None,
    ) -> dict[str, Any]:
        """Use simultaneous focus, then WHITE; transport/reference faults still stop."""
        try:
            return self._autofocus(prepared=True, scan_preload=scan_preload)
        except SimultaneousFocusFallbackEligibleError as simultaneous_error:
            return self._white_after_refusal(simultaneous_error, white_dz)

    def _white_after_refusal(
        self, error: SimultaneousFocusFallbackEligibleError, white_dz: int | None
    ) -> dict[str, Any]:
        """One WHITE episode, with the failed RG/periphery evidence attached."""
        if self._rg_focus is None:
            raise ValueError("WHITE autofocus component is unavailable") from error
        fallback = self._rg_focus.autofocus_white_after_simultaneous_refusal(
            white_dz=white_dz,
            simultaneous_reason=str(error),
            simultaneous_result_id=error.result_id,
            simultaneous_report_ref=error.report_ref,
            separate_rg_unavailable_reason=(
                "Direct WHITE fallback selected; separate JPEG R/G was not attempted"
            ),
            peripheral_search=error.peripheral_search,
            mixed_capture_count=error.mixed_capture_count,
        )
        return {
            **fallback,
            "simultaneous_attempted": True,
            "simultaneous_mixed_capture_count": error.mixed_capture_count,
            "simultaneous_fallback_reason": str(error),
            "simultaneous_result_id": error.result_id,
            "simultaneous_report_ref": error.report_ref,
            "peripheral_search": copy.deepcopy(error.peripheral_search),
            "fallback_count": 1,
        }

    @lt.endpoint("get", "preview.png")
    def preview(self) -> FileResponse:
        """Serve the accepted held-out unmixing preview for this mechanism only."""
        if self.profile is None:
            raise HTTPException(404, "No saved simultaneous R/G calibration")
        self._compatible(self.profile)
        path = self._directory(self.profile) / "validation.png"
        if not path.is_file():
            raise HTTPException(404, "Validation preview is missing")
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
