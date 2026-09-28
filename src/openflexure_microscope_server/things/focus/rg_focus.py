"""Persist and validate tissue-aware R/G focus profiles in native OFM settings."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import cv2
import numpy as np

import labthings_fastapi as lt

from openflexure_microscope_server.focus.focus_scan import (
    FocusFieldRuntime,
    FrozenRGFocusSettings,
)
from openflexure_microscope_server.focus.focus_surface import (
    FocusAxisScale,
    FocusBinding,
    FocusObservation,
    FocusPrediction,
    FocusPredictionRequest,
)
from openflexure_microscope_server.focus.rg import rg_flat_field as rg_flat_field_module
from openflexure_microscope_server.focus.rg import rg_focus_core as rg_focus_core_module
from openflexure_microscope_server.focus.rg import (
    rg_focus_estimator as rg_focus_estimator_module,
)
from openflexure_microscope_server.focus.rg import (
    rg_focus_field as rg_focus_field_module,
)
from openflexure_microscope_server.focus.rg.rg_flat_field import processing_binding
from openflexure_microscope_server.focus.rg.rg_focus_calibration import (
    DEMO_CALIBRATION_ENVELOPE_UM,
    DEMO_FIT_OFFSETS_UM,
    DEMO_HOLDOUT_OFFSETS_UM,
    DEMO_PAIR_BUDGET,
    DEMO_Z_OFFSETS_UM,
    RGFocusApproachSettings,
    RGFocusCapture,
    RGFocusStackSettings,
    calibration_path,
    capture_plan,
    led_calibration_manifest,
)
from openflexure_microscope_server.focus.rg.rg_focus_control import (
    RGFocusControlSettings,
    RGFocusDecision,
)
from openflexure_microscope_server.focus.rg.rg_focus_estimator import (
    RGFocusInputs,
    RGFocusMeasurement,
)
from openflexure_microscope_server.focus.rg.rg_focus_field import (
    FrameReference,
    IlluminationMode,
    TissueField,
    TissueFieldMetrics,
    geometry_fingerprint,
    parse_frame_geometry,
)
from openflexure_microscope_server.focus.rg.rg_focus_model import (
    JPEG_DRAFT_POLICY_ID,
    JPEG_DRAFT_POLICY_SHA256,
    RGFocusCalibrationPoint,
    RGFocusMeasurementPolicy,
    RGFocusModelProfile,
    RGFocusModelSettings,
    RGFocusStationaryObservation,
    jpeg_measurement_draft_policy,
    measurement_policy_sha256,
)
from openflexure_microscope_server.integrations.fast_ofm_core import (
    FastOFMCoreBlockingProcess,
    FastOFMCoreError,
)
from openflexure_microscope_server.integrations.fast_ofm_core.artifacts import (
    read_tissue_field_result,
    write_calibration_series,
    write_rg_measurement_artifacts,
    write_tissue_field_input,
)
from openflexure_microscope_server.utilities import robust_version_strings

from .. import OFMThing
from ..camera import BaseCamera
from ..stage.moonraker import MoonrakerStage
from .autofocus import AutofocusThing
from .rg_flat_field import RGFlatField, write_json


@dataclass(frozen=True)
class CapturedRGFocusPair:
    """One same-owner pair plus its fresh WHITE evidence."""

    measurement: RGFocusMeasurement
    capture_id: str
    field: TissueField
    white_score: float | None
    capture_path: str
    frame_ids: tuple[str, str, str]


class RGFocusFallbackEligibleError(ValueError):
    """A measured R/G refusal that may safely hand off to one WHITE sweep."""

    def __init__(self, reason: str, *, result_id: str, report_ref: str) -> None:
        """Keep the failed R/G evidence identity attached to the refusal."""
        super().__init__(reason)
        self.result_id = result_id
        self.report_ref = report_ref


MEASUREMENT_IMPLEMENTATION_SCHEMA = "rg-focus-measurement-v1"
EXPECTED_GEOMETRY_ID = (
    "43447352a251fdb38caff36fac296fa0f967588e0fefec30b132d25ca86e6916"
)
EXPECTED_CAMERA_BINDING_SHA256 = (
    "3341f2f61b410c552fb02122c2d24a321c96dc685eaa24a2c5a740a95e49b067"
)
EXPECTED_PROCESSING_REFERENCE_SHA256 = (
    "76c9a67731d501dd8775faef9493e7ce783a5b329b04e101d3a5772f3661ed21"
)
EXPECTED_FLAT_FIELD_CONTRACT_SHA256 = (
    "88cc1a0a9e17b598e36596cc81cd13b9a56b188e039adf63eaa34823b250ccca"
)
EXPECTED_FLAT_FIELD_PROFILE_ID = "0bc3d8e6-95ff-4b1f-9fd9-a199e120d439"
EXPECTED_FLAT_FIELD_MAPS = {
    "red": "27d51a9f3c1a7c06cafaeaa299a4088dc19860bdf18d4ea3b17f1501a6311275",
    "green": "5133df038323bcf5d3b23bb8fc86d7ce4fc3efb7c7146adfa4d1b2cd69b37a4d",
}
EXPECTED_FLAT_FIELD_METHOD = "phase_matched_processed_jpeg_measured_dark"
EXPECTED_OPTICS_ID = "current"
EXPECTED_ILLUMINATION_ID = "arduino-v4-r75-g150-w255"
EXPECTED_GEOMETRY = {
    "source_image_size": [1014, 760],
    "processing_roi": [102, 0, 810, 760],
    "image_size": [810, 760],
    "plane_size": [810, 760],
    "white_size": [810, 760],
    "pixel_to_sensor": [[4.0, 0.0, 409.5], [0.0, 4.0, 1.5]],
    "white_to_sensor": [[4.0, 0.0, 409.5], [0.0, 4.0, 1.5]],
    "common_plane_to_sensor": [[4.0, 0.0, 409.5], [0.0, 4.0, 1.5]],
    "sensor_scale": [4.0, 4.0],
    "sensor_roi": [408, 0, 3240, 3040],
}


def _identity(value: object) -> str:
    """Return a stable identity for canonical content that has no native UUID."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _commissioning_evidence_matches(
    evidence: object,
    *,
    binding_sha256: str,
    geometry_id: object,
    implementation_id: str,
) -> bool:
    """Validate the complete immutable two-pair commissioning record."""
    if not isinstance(evidence, dict) or set(evidence) != {
        "id",
        "status",
        "failures",
        "policy_id",
        "policy_sha256",
        "measurement_implementation_id",
        "binding_sha256",
        "field",
        "capture_ids",
        "stationary_observations",
        "white_mutual_fraction",
        "maximum_white_mutual_fraction",
        "minimum_patch_count",
        "minimum_inlier_fraction",
        "saved_jpeg_replay",
    }:
        return False
    try:
        observations_value = evidence["stationary_observations"]
        if not isinstance(observations_value, list) or len(observations_value) != 2:
            return False
        observations = tuple(
            RGFocusStationaryObservation.model_validate(value)
            for value in observations_value
        )
        first, second = observations
        field = {
            "field_id": first.field_id,
            "geometry_id": first.geometry_id,
            "binding_sha256": first.binding_sha256,
            "mask_sha256": first.mask_sha256,
            "window_ids": list(first.window_ids),
        }
        identities_match = all(
            getattr(first, name) == getattr(second, name)
            for name in (
                "field_id",
                "geometry_id",
                "binding_sha256",
                "mask_sha256",
                "window_ids",
            )
        )
        white_mutual_fraction = abs(first.white_score - second.white_score) / max(
            first.white_score, second.white_score
        )
        evidence_core = {key: value for key, value in evidence.items() if key != "id"}
        return bool(
            evidence["id"] == _identity(evidence_core)
            and evidence["status"] == "passed"
            and evidence["failures"] == []
            and evidence["policy_id"] == JPEG_DRAFT_POLICY_ID
            and evidence["policy_sha256"] == JPEG_DRAFT_POLICY_SHA256
            and evidence["measurement_implementation_id"] == implementation_id
            and evidence["binding_sha256"] == binding_sha256
            and evidence["field"] == field
            and first.binding_sha256 == binding_sha256
            and first.geometry_id == geometry_id
            and evidence["capture_ids"] == [first.capture_id, second.capture_id]
            and first.capture_id != second.capture_id
            and identities_match
            and len(first.window_ids) >= 9
            and len(set(first.window_ids)) == len(first.window_ids)
            and all(
                observation.measurement_status == "ready"
                and observation.accepted_patch_count >= 9
                and observation.inlier_fraction >= 0.6
                for observation in observations
            )
            and evidence["minimum_patch_count"] == 9
            and evidence["minimum_inlier_fraction"] == 0.6
            and evidence["maximum_white_mutual_fraction"] == 0.15
            and evidence["white_mutual_fraction"] == white_mutual_fraction
            and white_mutual_fraction <= 0.15
            and evidence["saved_jpeg_replay"] == {"comparison": "exact", "tolerance": 0}
        )
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def _require_current_commissioning(
    confirmation: object,
    *,
    policy: RGFocusMeasurementPolicy,
    binding: dict[str, object],
    implementation_id: str,
    profile: RGFocusModelProfile | None = None,
) -> str:
    """Share one inexpensive use gate across status, standalone and frozen scan."""
    policy.require_jpeg()
    if (
        not isinstance(confirmation, dict)
        or confirmation.get("state") != "commissioned"
        or confirmation.get("policy_id") != JPEG_DRAFT_POLICY_ID
        or confirmation.get("policy_sha256") != JPEG_DRAFT_POLICY_SHA256
        or measurement_policy_sha256(policy) != JPEG_DRAFT_POLICY_SHA256
        or confirmation.get("measurement_implementation_id") != implementation_id
    ):
        raise ValueError("Current JPEG measurement policy is not commissioned")
    evidence = confirmation.get("commissioning_evidence")
    if not _commissioning_evidence_matches(
        evidence,
        binding_sha256=_identity(binding),
        geometry_id=binding.get("geometry_id"),
        implementation_id=implementation_id,
    ):
        raise ValueError(
            "Current JPEG commissioning evidence is missing, stale or changed"
        )
    evidence = cast(dict[str, Any], evidence)
    if profile is not None and (
        profile.compatibility != binding
        or profile.measurement_policy != policy
        or profile.source_evidence.get("commissioning_evidence_id") != evidence["id"]
        or [
            row.model_dump(mode="json")
            for row in profile.activation_evidence.stationary_observations
        ]
        != evidence["stationary_observations"]
    ):
        raise ValueError("R/G model does not belong to current commissioning evidence")
    return str(evidence["id"])


def _source_sha256(module: object) -> str:
    """Hash one installed operative source module without importing Git state."""
    path = getattr(module, "__file__", None)
    if not isinstance(path, str):
        raise RuntimeError("Measurement implementation source path is unavailable")
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dependency_version(*distribution_names: str) -> str:
    """Return the installed distribution version under its supported package name."""
    for name in distribution_names:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "unavailable"


def measurement_implementation_identity() -> dict[str, object]:
    """Identify only code and numerical dependencies that affect R/G measurement."""
    payload: dict[str, object] = {
        "schema": MEASUREMENT_IMPLEMENTATION_SCHEMA,
        "modules": {
            "rg_focus_core.py": _source_sha256(rg_focus_core_module),
            "rg_focus_estimator.py": _source_sha256(rg_focus_estimator_module),
            "rg_focus_field.py": _source_sha256(rg_focus_field_module),
            "rg_flat_field.py": _source_sha256(rg_flat_field_module),
        },
        "dependencies": {
            "numpy": _dependency_version("numpy"),
            "opencv": _dependency_version(
                "opencv-python-headless", "opencv-python", "opencv-contrib-python"
            ),
            "Pillow": _dependency_version("Pillow"),
        },
    }
    return {"id": _identity(payload), **payload}


def _demo_focus_contract() -> dict[str, object]:
    """Build the complete reviewed, still-uncommissioned demo focus contract."""
    calibration = RGFocusStackSettings(
        z_offsets_um=DEMO_Z_OFFSETS_UM,
        fit_offsets_um=DEMO_FIT_OFFSETS_UM,
        holdout_offsets_um=DEMO_HOLDOUT_OFFSETS_UM,
        step_um=4,
        maximum_pairs=DEMO_PAIR_BUDGET,
        minimum_valid_holdout_points=2,
        maximum_holdout_error_um=2.0,
        maximum_absolute_z_um=DEMO_CALIBRATION_ENVELOPE_UM,
    )
    approach = RGFocusApproachSettings(preload_um=10, approach_sign=1)
    model = RGFocusModelSettings(
        minimum_valid_holdout_points=2,
        maximum_holdout_error_um=2.0,
        maximum_reference_focus_offset_um=12.0,
    )
    control = RGFocusControlSettings(
        maximum_iterations=3,
        focus_tolerance_um=4.0,
        cross_track_envelope_multiplier=8.0,
        cross_track_measurement_mad_multiplier=1.0,
        maximum_single_correction_um=32.0,
        maximum_total_correction_um=32.0,
        maximum_absolute_z_excursion_um=42.0,
    )
    calibration_path(calibration, approach)
    capture_plan(calibration)
    return {
        "measurement_parameters": jpeg_measurement_draft_policy(),
        "parameters": model,
        "control_parameters": control,
        "calibration_parameters": calibration,
        "approach_parameters": approach,
    }


def _setting_snapshot(value: object) -> dict[str, object]:
    """Return a JSON-mode snapshot for one strict persisted setting model."""
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        raise ValueError("Persisted setting is unavailable or invalid")
    result = model_dump(mode="json")
    if not isinstance(result, dict):
        raise ValueError("Persisted setting is not an object")
    return cast(dict[str, object], deepcopy(result))


def focus_binding(
    stage: MoonrakerStage,
    camera: BaseCamera,
    flat_field: RGFlatField,
    approach: RGFocusApproachSettings | None = None,
) -> dict[str, object]:
    """Return every runtime property that can invalidate the calibrated relation."""
    flat_field.parameters.require_jpeg()
    configuration = getattr(camera, "jpeg_measurement_configuration", None)
    if not isinstance(configuration, dict):
        raise ValueError("Camera does not support processed-JPEG measurement")
    configuration = processing_binding(configuration, flat_field.parameters)
    geometry_id = geometry_fingerprint(
        parse_frame_geometry(configuration.get("geometry", {}))
    )
    statuses = flat_field.calibration_status
    profiles = flat_field.profiles
    if any(
        statuses.get(mode, {}).get("status") != "valid" for mode in ("red", "green")
    ):
        raise ValueError("Compatible checked RED and GREEN flat-fields are required")
    if any(mode not in profiles for mode in ("red", "green")):
        raise ValueError("RED or GREEN flat-field profile is missing")
    bindings = [profiles[mode].get("binding") for mode in ("red", "green")]
    if bindings[0] != bindings[1] or not isinstance(bindings[0], dict):
        raise ValueError("RED and GREEN flat-field bindings differ")
    flat_binding = bindings[0]
    if flat_binding.get("camera") != configuration:
        raise ValueError("Camera binding differs from the checked flat-fields")
    processing_references = [
        profiles[mode].get("processing_reference") for mode in ("red", "green")
    ]
    if processing_references[0] != processing_references[1] or not isinstance(
        processing_references[0], dict
    ):
        raise ValueError("RED and GREEN actual JPEG processing references differ")
    return {
        "z_final_approach": (approach or RGFocusApproachSettings()).model_dump(
            mode="json"
        ),
        "geometry_id": geometry_id,
        "camera": json.loads(json.dumps(configuration)),
        "processing_reference": json.loads(json.dumps(processing_references[0])),
        "optics_id": flat_binding.get("optics_id"),
        "illumination_id": flat_binding.get("illumination_id"),
        "flat_fields": {
            mode: {
                "profile_id": profiles[mode].get("id"),
                "method": profiles[mode].get("method"),
                "maps_sha256": profiles[mode].get("maps_sha256"),
            }
            for mode in ("red", "green")
        },
        "z_hardware_settings": json.loads(
            json.dumps(stage.hardware_settings["axes"]["z"])
        ),
    }


class RGFocus(OFMThing):
    """Own the saved model decision; candidate profiles can never drive motion."""

    _class_settings = {"validate_properties_on_set": True}
    _cam: BaseCamera = lt.thing_slot()
    _stage: MoonrakerStage = lt.thing_slot()
    _rg_flat_field: RGFlatField = lt.thing_slot()
    _white_autofocus: AutofocusThing | None = lt.thing_slot()

    parameters: RGFocusModelSettings = lt.setting(
        default_factory=RGFocusModelSettings, readonly=True
    )
    measurement_parameters: RGFocusMeasurementPolicy = lt.setting(
        default_factory=RGFocusMeasurementPolicy, readonly=True
    )
    measurement_policy_confirmation: dict | None = lt.setting(
        default=None, readonly=True
    )
    control_parameters: RGFocusControlSettings = lt.setting(
        default_factory=RGFocusControlSettings, readonly=True
    )
    calibration_parameters: RGFocusStackSettings = lt.setting(
        default_factory=RGFocusStackSettings, readonly=True
    )
    approach_parameters: RGFocusApproachSettings = lt.setting(
        default_factory=RGFocusApproachSettings, readonly=True
    )
    profile: dict | None = lt.setting(default=None, readonly=True)
    progress: dict = lt.property(
        default_factory=lambda: {"phase": "idle", "pairs": 0}, readonly=True
    )

    def _core_process(self) -> FastOFMCoreBlockingProcess:
        """Return the one warm standalone core process for this microscope."""
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

    def _external_measure(
        self,
        inputs: RGFocusInputs,
        policy: RGFocusMeasurementPolicy,
        output_directory: Path,
        iteration: int,
    ) -> RGFocusMeasurement:
        """Run image-domain measurement in the separately installed core."""
        if inputs.field is None:
            raise ValueError("R/G measurement has no WHITE tissue field")
        exchange = output_directory / f"iteration-{iteration:02d}-core"
        artifacts = write_rg_measurement_artifacts(
            exchange,
            white_frame=inputs.field.white_common,
            red_plane=inputs.red_plane,
            green_plane=inputs.green_plane,
            red_source_jpeg8=inputs.red_source_jpeg8,
            green_source_jpeg8=inputs.green_source_jpeg8,
            red_valid=inputs.red_valid,
            green_valid=inputs.green_valid,
            white_reference=inputs.field.reference.model_dump(mode="json"),
            red_reference=inputs.red_reference.model_dump(mode="json"),
            green_reference=inputs.green_reference.model_dump(mode="json"),
            geometry=inputs.geometry_value,
            calibration_id=measurement_policy_sha256(policy),
            measurement_policy=policy.model_dump(mode="json"),
        )
        position = self._stage.get_xyz_position()
        axes = self._stage.hardware_settings["axes"]
        position_um = {
            f"{axis}_um": float(position[index]) / (axes[axis]["units_per_mm"] / 1000)
            for index, axis in enumerate(("x", "y", "z"))
        }
        response = self._core_process().request(
            "rg.measure",
            {
                **artifacts,
                "position_um": position_um,
                "diagnostics": True,
            },
            timeout_s=max(1.0, self.control_parameters.minimum_capture_budget_s),
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("qc"), dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "R/G result omitted measurement diagnostics"
            )
        return RGFocusMeasurement.model_validate(result["qc"])

    def _external_tissue_field(
        self,
        white_frame: np.ndarray,
        reference: FrameReference,
        geometry_value: dict[str, object],
        policy: RGFocusMeasurementPolicy,
        exchange: Path,
    ) -> TissueField:
        """Prepare WHITE support through the standalone core artifact boundary."""
        descriptor = write_tissue_field_input(exchange, white_frame)
        response = self._core_process().request(
            "rg.tissue_field.prepare",
            {
                "input": descriptor,
                "reference": reference.model_dump(mode="json"),
                "geometry": geometry_value,
                "settings": policy.tissue_field.model_dump(mode="json"),
                "core_settings": policy.core.model_dump(mode="json"),
            },
            timeout_s=max(1.0, self.control_parameters.minimum_capture_budget_s),
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("field"), dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Tissue-field result is incomplete"
            )
        field_value = result["field"]
        if set(field_value) != {"reference", "geometry", "boxes", "metrics"}:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Tissue-field contracts changed"
            )
        arrays = read_tissue_field_result(result.get("arrays"), directory=exchange)
        try:
            field = TissueField(
                reference=FrameReference.model_validate(field_value["reference"]),
                geometry=parse_frame_geometry(field_value["geometry"]),
                mask=arrays["mask"],
                white_common=arrays["white_common"],
                boxes=tuple(
                    rg_focus_core_module.PatchBox.model_validate(value)
                    for value in field_value["boxes"]
                ),
                metrics=TissueFieldMetrics.model_validate(field_value["metrics"]),
                overlay=arrays["overlay"],
            )
        except (TypeError, ValueError) as error:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Tissue-field contracts are invalid"
            ) from error
        if field.reference != reference or field.geometry != parse_frame_geometry(
            geometry_value
        ):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Tissue-field provenance changed"
            )
        return field

    def _external_calibration_fit(  # noqa: PLR0913
        self,
        *,
        directory: Path,
        points: list[RGFocusCalibrationPoint],
        settings: RGFocusModelSettings,
        reference_white_score: float,
        stationary_observations: tuple[
            RGFocusStationaryObservation, RGFocusStationaryObservation
        ],
        profile_id: str,
        source_series_id: str,
        compatibility: dict[str, object],
        source_evidence: dict[str, str],
        measurement_policy: RGFocusMeasurementPolicy,
    ) -> RGFocusModelProfile:
        """Fit one calibration series in the standalone core process."""
        series = {
            "schema_version": "1.0",
            "profile_id": profile_id,
            "source_series_id": source_series_id,
            "points": [value.model_dump(mode="json") for value in points],
            "settings": settings.model_dump(mode="json"),
            "reference_white_score": reference_white_score,
            "stationary_observations": [
                value.model_dump(mode="json") for value in stationary_observations
            ],
            "compatibility": compatibility,
            "source_evidence": source_evidence,
            "measurement_policy": measurement_policy.model_dump(mode="json"),
        }
        descriptor = write_calibration_series(directory / "core-fit", series)
        response = self._core_process().request(
            "calibration.fit",
            {"series": descriptor},
            timeout_s=max(1.0, self.calibration_parameters.action_timeout_s),
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("profile"), dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Calibration result omitted its profile"
            )
        return RGFocusModelProfile.model_validate(result["profile"])

    def _external_profile_validation(
        self,
        profile_value: object,
        *,
        focus_tolerance_um: float | None = None,
    ) -> tuple[RGFocusModelProfile, float | None]:
        """Revalidate persisted empirical evidence in the standalone core."""
        payload: dict[str, object] = {"profile": profile_value}
        if focus_tolerance_um is not None:
            payload["focus_tolerance_um"] = focus_tolerance_um
        response = self._core_process().request(
            "calibration.validate",
            payload,
            timeout_s=5.0,
        )
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("profile"), dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Profile validation result is incomplete"
            )
        profile = RGFocusModelProfile.model_validate(result["profile"])
        effective = result.get("effective_residual_tolerance_um")
        if focus_tolerance_um is None:
            if effective is not None:
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Unexpected profile focus budget"
                )
            return profile, None
        if (
            isinstance(effective, bool)
            or not isinstance(effective, (int, float))
            or not np.isfinite(effective)
            or effective <= 0
            or effective > focus_tolerance_um
        ):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Invalid effective profile focus budget"
            )
        return profile, float(effective)

    def _external_decision(
        self,
        profile: RGFocusModelProfile,
        measurement: RGFocusMeasurement,
        settings: RGFocusControlSettings,
        *,
        iteration: int,
        total_correction_um: float,
    ) -> RGFocusDecision:
        """Ask the standalone core for one non-hardware correction decision."""
        response = self._core_process().request(
            "rg.decide",
            {
                "profile": profile.model_dump(mode="json"),
                "measurement": measurement.model_dump(mode="json"),
                "control": settings.model_dump(mode="json"),
                "iteration": iteration,
                "total_correction_um": total_correction_um,
            },
            timeout_s=5.0,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "R/G decision result is not an object"
            )
        return RGFocusDecision.model_validate(result)

    def external_focus_prediction(
        self,
        observations: tuple[FocusObservation, ...],
        request: FocusPredictionRequest,
    ) -> FocusPrediction:
        """Evaluate the exact frozen focus-surface contract in the core process."""
        response = self._core_process().request(
            "focus.surface.predict",
            {
                "observations": [
                    value.model_dump(mode="json") for value in observations
                ],
                "request": request.model_dump(mode="json"),
            },
            timeout_s=5.0,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Focus prediction result is not an object"
            )
        return FocusPrediction.model_validate(result)

    def save_settings(self) -> None:
        """Persist the profile through the native per-Thing OFM settings file."""
        if not self._disable_saving_settings:
            write_json(
                Path(self._thing_server_interface.settings_file_path),
                self.settings.model_instance.model_dump(mode="json"),
            )

    def _commit(self, name: str, value: object) -> None:
        """Keep the previous in-memory setting if its atomic save fails."""
        self._commit_many({name: value})

    def _commit_many(self, values: dict[str, object]) -> None:
        """Validate and persist a coherent group, rolling all members back together."""
        old = {name: deepcopy(getattr(self, name)) for name in values}
        if all(old[name] == value for name, value in values.items()):
            return
        disabled = self._disable_saving_settings
        try:
            self._disable_saving_settings = True
            for name, value in values.items():
                setattr(self, name, value)
            self._disable_saving_settings = disabled
            if not disabled:
                self.save_settings()
        except BaseException:
            self._disable_saving_settings = True
            try:
                for name, value in old.items():
                    setattr(self, name, value)
            finally:
                self._disable_saving_settings = disabled
            raise

    @lt.action
    def set_parameters(self, parameters: RGFocusModelSettings) -> RGFocusModelSettings:
        """Save validation policy without touching camera, light, stage or profile."""
        self._commit("parameters", parameters)
        return self.parameters

    @lt.action
    def set_measurement_parameters(
        self, parameters: RGFocusMeasurementPolicy
    ) -> RGFocusMeasurementPolicy:
        """Save tissue/shift QC policy without changing an existing profile."""
        self._commit_many(
            {
                "measurement_parameters": parameters,
                "measurement_policy_confirmation": None,
            }
        )
        return self.measurement_parameters

    @lt.action
    def use_jpeg_measurement_preset(self) -> RGFocusMeasurementPolicy:
        """Replace the whole legacy value with the reviewed uncommissioned draft."""
        policy = jpeg_measurement_draft_policy()
        implementation = measurement_implementation_identity()
        confirmation = {
            "policy_id": JPEG_DRAFT_POLICY_ID,
            "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
            "measurement_implementation_id": implementation["id"],
            "state": "draft_uncommissioned",
        }
        self._commit_many(
            {
                "measurement_parameters": policy,
                "measurement_policy_confirmation": confirmation,
            }
        )
        return self.measurement_parameters

    @lt.action
    def apply_demo_focus_contract(self) -> dict[str, Any]:
        """Atomically save the complete reviewed policy and bounded demo settings."""
        values = _demo_focus_contract()
        implementation = measurement_implementation_identity()
        values["measurement_policy_confirmation"] = {
            "policy_id": JPEG_DRAFT_POLICY_ID,
            "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
            "measurement_implementation_id": implementation["id"],
            "state": "draft_uncommissioned",
        }
        self._commit_many(values)
        return self.readiness

    @lt.action
    def set_control_parameters(
        self, parameters: RGFocusControlSettings
    ) -> RGFocusControlSettings:
        """Save finite loop limits without moving or capturing."""
        self._commit("control_parameters", parameters)
        return self.control_parameters

    @lt.property
    def calibration_manifest(self) -> dict[str, Any]:
        """Describe the bounded acquisition/fit stages shown by the Settings UI."""
        return led_calibration_manifest(self.calibration_parameters).model_dump(
            mode="json"
        )

    @lt.action
    def set_calibration_parameters(
        self, parameters: RGFocusStackSettings
    ) -> RGFocusStackSettings:
        """Save a predeclared grid only when its complete preload path is bounded."""
        calibration_path(parameters, self.approach_parameters)
        self._commit("calibration_parameters", parameters)
        return self.calibration_parameters

    @lt.action
    def set_approach_parameters(
        self, parameters: RGFocusApproachSettings
    ) -> RGFocusApproachSettings:
        """Save the common approach policy; a changed policy invalidates old models."""
        calibration_path(self.calibration_parameters, parameters)
        self._commit("approach_parameters", parameters)
        return self.approach_parameters

    def _binding(self) -> dict[str, object]:
        """Include the exact approach policy in calibration compatibility."""
        self.measurement_parameters.require_jpeg()
        return focus_binding(
            self._stage, self._cam, self._rg_flat_field, self.approach_parameters
        )

    def _frozen_scan_profile(
        self, frozen: FrozenRGFocusSettings
    ) -> tuple[RGFocusModelProfile, dict[str, object]]:
        """Validate physical owners without re-reading next-run form settings."""
        frozen.measurement_policy.require_jpeg()
        if self.profile is None:
            raise ValueError("The active scan R/G focus profile was removed")
        profile, effective_tolerance = self._external_profile_validation(
            self.profile,
            focus_tolerance_um=frozen.control.focus_tolerance_um,
        )
        if profile != frozen.profile:
            raise ValueError("The active scan R/G focus profile changed")
        compatibility = focus_binding(
            self._stage,
            self._cam,
            self._rg_flat_field,
            frozen.binding.approach_parameters,
        )
        if profile.compatibility != compatibility:
            raise ValueError(
                "Camera, flat-field, optics, illumination or Z profile changed"
            )
        if profile.status != "valid":
            raise ValueError(f"R/G focus profile is candidate-only: {profile.reason}")
        _require_current_commissioning(
            self.measurement_policy_confirmation,
            policy=frozen.measurement_policy,
            binding=compatibility,
            implementation_id=str(measurement_implementation_identity()["id"]),
            profile=profile,
        )
        if effective_tolerance is None:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Core omitted the frozen focus budget"
            )
        if effective_tolerance != frozen.effective_focus_tolerance_um:
            raise FastOFMCoreError("CORE_PROTOCOL_ERROR", "Frozen focus budget changed")
        return profile, compatibility

    def focus_surface_binding(
        self,
        camera_stage_mapping_calibration: dict[str, Any] | None,
        *,
        frozen: FrozenRGFocusSettings | None = None,
    ) -> FocusBinding:
        """Build the live surface identity from checked runtime owners and statuses."""
        if camera_stage_mapping_calibration is None:
            raise ValueError("Camera-stage mapping has no accepted calibration")
        if frozen is None:
            profile = self.checked_profile()
            compatibility = self._binding()
            approach_parameters = self.approach_parameters
        else:
            profile, compatibility = self._frozen_scan_profile(frozen)
            approach_parameters = frozen.binding.approach_parameters
        controller = self._stage.controller_state
        reference_id = controller.get("reference_id")
        if not controller.get("reference_valid") or not isinstance(reference_id, str):
            raise ValueError("A valid live stage reference is required")
        hardware = self._stage.hardware_settings
        statuses = self._rg_flat_field.calibration_status
        profiles = self._rg_flat_field.profiles
        for mode in ("red", "green"):
            profile_value = profiles.get(mode)
            if (
                not isinstance(profile_value, dict)
                or not isinstance(profile_value.get("id"), str)
                or statuses.get(mode, {}).get("status") != "valid"
            ):
                raise ValueError(f"{mode.upper()} flat-field profile is not valid")
        geometry_id = compatibility.get("geometry_id")
        if not isinstance(geometry_id, str) or not geometry_id:
            raise ValueError("R/G geometry identity is unavailable")
        axes = cast(
            tuple[FocusAxisScale, FocusAxisScale, FocusAxisScale],
            tuple(
                FocusAxisScale(
                    axis=axis,
                    units_per_mm=hardware["axes"][axis]["units_per_mm"],
                    direction_sign=hardware["axes"][axis]["direction_sign"],
                )
                for axis in ("x", "y", "z")
            ),
        )
        approach_identity = _identity(
            {
                "parameters": approach_parameters.model_dump(mode="json"),
                "check": profile.approach_check.model_dump(mode="json"),
            }
        )
        return FocusBinding(
            stage_controller_id=_identity(
                {
                    "moonraker_url": self._stage._url,
                    "hardware": hardware,
                }
            ),
            reference_id=reference_id,
            reference_status="valid",
            axis_scales=axes,
            camera_stage_mapping_id=_identity(camera_stage_mapping_calibration),
            camera_stage_mapping_status="valid",
            geometry_id=geometry_id,
            rg_focus_model_id=profile.id,
            rg_focus_model_status="valid",
            red_flat_field_profile_id=str(profiles["red"]["id"]),
            red_flat_field_status="valid",
            green_flat_field_profile_id=str(profiles["green"]["id"]),
            green_flat_field_status="valid",
            approach_profile_id=approach_identity,
            approach_status=(
                "valid" if profile.approach_check.status == "passed" else "candidate"
            ),
            approach_parameters=approach_parameters,
        )

    def freeze_scan_focus(self, binding: FocusBinding) -> FrozenRGFocusSettings:
        """Copy every R/G parameter before the first scan motion."""
        profile = self.checked_profile().model_copy(deep=True)
        validated, effective = self._external_profile_validation(
            profile.model_dump(mode="json"),
            focus_tolerance_um=self.control_parameters.focus_tolerance_um,
        )
        if validated != profile or effective is None:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Core changed or omitted the frozen profile"
            )
        return FrozenRGFocusSettings(
            profile=profile,
            measurement_policy=self.measurement_parameters.model_copy(deep=True),
            control=self.control_parameters.model_copy(deep=True),
            binding=binding.model_copy(deep=True),
            effective_focus_tolerance_um=effective,
        )

    def checked_profile(self, *, require_valid: bool = True) -> RGFocusModelProfile:
        """Validate stored schema and exact runtime compatibility before any use."""
        self.measurement_parameters.require_jpeg()
        if self.profile is None:
            raise ValueError("No saved R/G focus profile")
        stored_profile = RGFocusModelProfile.model_validate(self.profile)
        profile, effective_tolerance = self._external_profile_validation(
            self.profile,
            focus_tolerance_um=(
                self.control_parameters.focus_tolerance_um
                if require_valid and stored_profile.status == "valid"
                else None
            ),
        )
        if profile.settings != self.parameters:
            raise ValueError("Saved profile uses a different validation policy")
        if profile.measurement_policy != self.measurement_parameters:
            raise ValueError("Saved profile uses a different tissue/shift QC policy")
        binding = self._binding()
        if profile.compatibility != binding:
            raise ValueError(
                "Camera, flat-field, optics, illumination or Z profile changed"
            )
        if require_valid and profile.status != "valid":
            raise ValueError(f"R/G focus profile is candidate-only: {profile.reason}")
        if require_valid:
            _require_current_commissioning(
                self.measurement_policy_confirmation,
                policy=self.measurement_parameters,
                binding=binding,
                implementation_id=str(measurement_implementation_identity()["id"]),
                profile=profile,
            )
            if effective_tolerance is None:
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Core omitted the live focus budget"
                )
        return profile

    @lt.property
    def readiness(self) -> dict[str, Any]:  # noqa: C901, PLR0912, PLR0915
        """Return one coherent read-only snapshot; never capture, illuminate or move."""
        captured_at = datetime.now(timezone.utc).isoformat()
        mismatches: list[dict[str, object]] = []

        def mismatch(code: str, path: str, expected: object, actual: object) -> None:
            mismatches.append(
                {
                    "code": code,
                    "path": path,
                    "expected": deepcopy(expected),
                    "actual": deepcopy(actual),
                }
            )

        expected_policy_model = jpeg_measurement_draft_policy()
        expected_policy = expected_policy_model.model_dump(mode="json")
        effective_policy: dict[str, object] | None
        effective_policy_model: RGFocusMeasurementPolicy | None
        effective_policy_sha256: str | None
        policy_error: str | None
        try:
            effective_policy = _setting_snapshot(self.measurement_parameters)
            effective_policy_model = RGFocusMeasurementPolicy.model_validate(
                effective_policy
            )
            effective_policy_sha256 = measurement_policy_sha256(effective_policy_model)
        except Exception as exc:
            effective_policy = None
            effective_policy_model = None
            effective_policy_sha256 = None
            policy_error = str(exc)
        else:
            policy_error = None
        policy_match = (
            effective_policy == expected_policy
            and effective_policy_sha256 == JPEG_DRAFT_POLICY_SHA256
        )
        is_legacy = (
            effective_policy_model is None
            or effective_policy_model.measurement_domain != "processed-jpeg-rgb8"
            or effective_policy_model.legacy_raw_saturation_level is not None
        )
        if is_legacy:
            mismatch(
                "legacy_measurement_policy",
                "measurement_parameters.measurement_domain",
                "processed-jpeg-rgb8",
                None
                if effective_policy_model is None
                else effective_policy_model.measurement_domain,
            )
        if not policy_match:
            mismatch(
                "policy_hash_mismatch",
                "measurement_parameters",
                JPEG_DRAFT_POLICY_SHA256,
                effective_policy_sha256 or policy_error,
            )

        implementation = measurement_implementation_identity()
        version_data = robust_version_strings()
        confirmation = deepcopy(self.measurement_policy_confirmation)
        expected_confirmation = {
            "policy_id": JPEG_DRAFT_POLICY_ID,
            "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
            "measurement_implementation_id": implementation["id"],
            "state": "draft_uncommissioned",
        }
        confirmation_dict = confirmation if isinstance(confirmation, dict) else {}
        confirmation_base_match = bool(confirmation_dict) and not any(
            confirmation_dict.get(key) != expected_confirmation[key]
            for key in ("policy_id", "policy_sha256")
        )
        confirmation_match = bool(
            confirmation_base_match
            and confirmation_dict.get("measurement_implementation_id")
            == implementation["id"]
            and confirmation_dict.get("state")
            in {"draft_uncommissioned", "commissioned"}
        )
        if not confirmation_base_match or (
            isinstance(confirmation, dict)
            and confirmation.get("state")
            not in {"draft_uncommissioned", "commissioned"}
        ):
            mismatch(
                "jpeg_policy_unconfirmed",
                "measurement_policy_confirmation",
                expected_confirmation,
                confirmation,
            )
        implementation_match = (
            isinstance(confirmation, dict)
            and confirmation.get("measurement_implementation_id")
            == implementation["id"]
        )
        if isinstance(confirmation, dict) and not implementation_match:
            mismatch(
                "measurement_implementation_mismatch",
                "measurement_policy_confirmation.measurement_implementation_id",
                implementation["id"],
                confirmation.get("measurement_implementation_id"),
            )
        expected_contract_models = _demo_focus_contract()
        expected_contract = {
            key: _setting_snapshot(value)
            for key, value in expected_contract_models.items()
            if key != "measurement_parameters"
        }
        effective_contract: dict[str, object] = {}
        contract_setting_matches: dict[str, bool] = {}
        for setting_name, mismatch_code in (
            ("parameters", "model_contract_mismatch"),
            ("control_parameters", "control_contract_mismatch"),
            ("calibration_parameters", "calibration_contract_mismatch"),
            ("approach_parameters", "approach_contract_mismatch"),
        ):
            try:
                value: object = _setting_snapshot(getattr(self, setting_name))
            except Exception as exc:
                value = {"error": str(exc)}
            effective_contract[setting_name] = value
            matches = value == expected_contract[setting_name]
            contract_setting_matches[setting_name] = matches
            if not matches:
                mismatch(
                    mismatch_code,
                    setting_name,
                    expected_contract[setting_name],
                    value,
                )
        demo_contract_match = policy_match and all(contract_setting_matches.values())

        try:
            expected_calibration = cast(
                RGFocusStackSettings,
                expected_contract_models["calibration_parameters"],
            )
            expected_approach = cast(
                RGFocusApproachSettings,
                expected_contract_models["approach_parameters"],
            )
            prospective_path = list(
                calibration_path(expected_calibration, expected_approach)
            )
            prospective_pair_count = len(capture_plan(expected_calibration))
        except Exception as exc:  # pragma: no cover - immutable source invariant
            prospective_path = []
            prospective_pair_count = 0
            mismatch(
                "calibration_contract_mismatch",
                "expected_calibration_path",
                "valid bounded path",
                str(exc),
            )

        camera_configuration: object = None
        processed_camera: dict[str, object] | None = None
        geometry_snapshot: dict[str, object] | None = None
        geometry_id: str | None = None
        camera_sha256: str | None = None
        camera_error: str | None = None
        flat_parameters_snapshot: object = None
        try:
            camera_configuration = deepcopy(
                cast(Any, self._cam).jpeg_measurement_configuration
            )
            if not isinstance(camera_configuration, dict):
                raise ValueError("Camera JPEG measurement configuration is unavailable")
            flat_parameters = self._rg_flat_field.parameters
            flat_parameters_snapshot = _setting_snapshot(flat_parameters)
            processed_camera = cast(
                dict[str, object],
                processing_binding(camera_configuration, flat_parameters),
            )
            camera_sha256 = _identity(processed_camera)
            geometry_value = processed_camera.get("geometry")
            if not isinstance(geometry_value, dict):
                raise ValueError("Camera JPEG geometry is unavailable")
            geometry = parse_frame_geometry(geometry_value)
            geometry_id = geometry_fingerprint(geometry)
            geometry_snapshot = geometry.model_dump(mode="json")
            affine = geometry.pixel_to_sensor
            roi_x, roi_y, roi_width, roi_height = geometry.processing_roi
            x_scale, y_scale = affine[0][0], affine[1][1]
            geometry_snapshot.update(
                sensor_scale=[x_scale, y_scale],
                sensor_roi=[
                    round(roi_x * x_scale),
                    round(roi_y * y_scale),
                    round(roi_width * x_scale),
                    round(roi_height * y_scale),
                ],
            )
        except Exception as exc:
            camera_error = str(exc)

        geometry_match = geometry_id == EXPECTED_GEOMETRY_ID and all(
            isinstance(geometry_snapshot, dict) and geometry_snapshot.get(key) == value
            for key, value in EXPECTED_GEOMETRY.items()
        )
        if not geometry_match:
            mismatch(
                "geometry_mismatch",
                "camera.geometry",
                {
                    **EXPECTED_GEOMETRY,
                    "geometry_id": EXPECTED_GEOMETRY_ID,
                },
                (
                    {**geometry_snapshot, "geometry_id": geometry_id}
                    if isinstance(geometry_snapshot, dict)
                    else camera_error
                ),
            )
        camera_match = camera_sha256 == EXPECTED_CAMERA_BINDING_SHA256
        if not camera_match:
            mismatch(
                "camera_binding_mismatch",
                "camera.jpeg_measurement_configuration",
                EXPECTED_CAMERA_BINDING_SHA256,
                camera_sha256 or camera_error,
            )

        flat_statuses: object = None
        flat_profiles: object = None
        flat_fields: dict[str, object] = {}
        processing_reference: dict[str, object] | None = None
        processing_reference_sha256: str | None = None
        flat_valid = False
        flat_profiles_match = False
        processing_match = False
        optics_id: object = None
        illumination_id: object = None
        flat_error: str | None = None
        try:
            flat_statuses = deepcopy(self._rg_flat_field.calibration_status)
            flat_profiles = deepcopy(self._rg_flat_field.profiles)
            if not isinstance(flat_statuses, dict) or not isinstance(
                flat_profiles, dict
            ):
                raise ValueError("Flat-field readiness or profiles are unavailable")
            flat_valid = all(
                isinstance(flat_statuses.get(mode), dict)
                and flat_statuses[mode].get("status") == "valid"
                for mode in ("red", "green")
            )
            mode_profiles: dict[str, dict[str, object]] = {}
            for mode in ("red", "green"):
                candidate = flat_profiles.get(mode)
                if not isinstance(candidate, dict):
                    raise ValueError(f"{mode.upper()} flat-field profile is missing")
                mode_profiles[mode] = candidate
                flat_fields[mode] = {
                    "id": candidate.get("id"),
                    "status": (
                        flat_statuses.get(mode, {}).get("status")
                        if isinstance(flat_statuses.get(mode), dict)
                        else None
                    ),
                    "method": candidate.get("method"),
                    "maps_sha256": candidate.get("maps_sha256"),
                }
            bindings = [mode_profiles[mode].get("binding") for mode in ("red", "green")]
            common_binding = bindings[0]
            if bindings[0] != bindings[1] or not isinstance(common_binding, dict):
                raise ValueError("RED and GREEN flat-field bindings differ")
            optics_id = common_binding.get("optics_id")
            illumination_id = common_binding.get("illumination_id")
            flat_profiles_match = all(
                flat_fields[mode]
                == {
                    "id": EXPECTED_FLAT_FIELD_PROFILE_ID,
                    "status": "valid",
                    "method": EXPECTED_FLAT_FIELD_METHOD,
                    "maps_sha256": EXPECTED_FLAT_FIELD_MAPS[mode],
                }
                for mode in ("red", "green")
            ) and (
                common_binding.get("camera") == processed_camera
                and optics_id == EXPECTED_OPTICS_ID
                and illumination_id == EXPECTED_ILLUMINATION_ID
            )
            references = [
                mode_profiles[mode].get("processing_reference")
                for mode in ("red", "green")
            ]
            if references[0] != references[1] or not isinstance(references[0], dict):
                raise ValueError("RED and GREEN processing references differ")
            processing_reference = cast(dict[str, object], references[0])
            processing_reference_sha256 = _identity(processing_reference)
            processing_match = (
                processing_reference_sha256 == EXPECTED_PROCESSING_REFERENCE_SHA256
            )
        except Exception as exc:
            flat_error = str(exc)
        if not flat_valid:
            mismatch(
                "flat_field_invalid",
                "rg_flat_field.calibration_status",
                {"red": "valid", "green": "valid"},
                flat_statuses or flat_error,
            )
        if not flat_profiles_match:
            mismatch(
                "flat_field_profile_mismatch",
                "rg_flat_field.profiles",
                {
                    mode: {
                        "id": EXPECTED_FLAT_FIELD_PROFILE_ID,
                        "status": "valid",
                        "method": EXPECTED_FLAT_FIELD_METHOD,
                        "maps_sha256": EXPECTED_FLAT_FIELD_MAPS[mode],
                    }
                    for mode in ("red", "green")
                },
                flat_fields or flat_error,
            )
        if not processing_match:
            mismatch(
                "processing_reference_mismatch",
                "rg_flat_field.processing_reference",
                EXPECTED_PROCESSING_REFERENCE_SHA256,
                processing_reference_sha256 or flat_error,
            )

        binding_match = all(
            (
                geometry_match,
                camera_match,
                flat_valid,
                flat_profiles_match,
                processing_match,
            )
        )
        contract_match = policy_match and binding_match
        binding_snapshot = {
            "geometry_id": geometry_id,
            "camera": processed_camera,
            "processing_reference": processing_reference,
            "optics_id": optics_id,
            "illumination_id": illumination_id,
            "flat_fields": flat_fields,
        }
        current_calibration_binding: dict[str, object] | None = None

        def flat_field_value(mode: str, key: str) -> object:
            value = flat_fields.get(mode)
            return value.get(key) if isinstance(value, dict) else None

        try:
            stage_hardware = deepcopy(self._stage.hardware_settings)
            if not isinstance(stage_hardware, dict):
                raise ValueError("Saved stage hardware settings are unavailable")
            current_calibration_binding = {
                "z_final_approach": effective_contract["approach_parameters"],
                "geometry_id": geometry_id,
                "camera": processed_camera,
                "processing_reference": processing_reference,
                "optics_id": optics_id,
                "illumination_id": illumination_id,
                "flat_fields": {
                    mode: {
                        "profile_id": flat_field_value(mode, "id"),
                        "method": flat_field_value(mode, "method"),
                        "maps_sha256": flat_field_value(mode, "maps_sha256"),
                    }
                    for mode in ("red", "green")
                },
                "z_hardware_settings": stage_hardware["axes"]["z"],
            }
        except Exception:
            current_calibration_binding = None
        commissioning_evidence = (
            confirmation.get("commissioning_evidence")
            if isinstance(confirmation, dict)
            else None
        )
        try:
            if current_calibration_binding is None or effective_policy_model is None:
                raise ValueError("Commissioning policy or binding is unavailable")
            _require_current_commissioning(
                confirmation,
                policy=effective_policy_model,
                binding=current_calibration_binding,
                implementation_id=str(implementation["id"]),
            )
        except ValueError:
            commissioning_match = False
        else:
            commissioning_match = True
        if not commissioning_match:
            mismatch(
                "jpeg_policy_uncommissioned",
                "commissioning_evidence",
                "commissioned evidence for this exact policy, implementation, and binding",
                commissioning_evidence,
            )

        focus_profile_snapshot = deepcopy(self.profile)
        profile_use_ready = False
        focus_profile: dict[str, object] = {
            "status": "absent",
            "profile": focus_profile_snapshot,
            "policy_sha256": None,
            "compatibility_match": False,
        }
        if focus_profile_snapshot is None:
            mismatch("focus_profile_missing", "profile", "valid", None)
        else:
            try:
                stored_profile = RGFocusModelProfile.model_validate(
                    focus_profile_snapshot
                )
                parsed_profile, effective_profile_tolerance = (
                    self._external_profile_validation(
                        focus_profile_snapshot,
                        focus_tolerance_um=(
                            RGFocusControlSettings.model_validate(
                                effective_contract["control_parameters"]
                            ).focus_tolerance_um
                            if stored_profile.status == "valid"
                            else None
                        ),
                    )
                )
                profile_policy_sha256 = measurement_policy_sha256(
                    parsed_profile.measurement_policy
                )
                if current_calibration_binding is None:
                    raise ValueError("Saved stage hardware settings are unavailable")
                expected_profile_binding = current_calibration_binding
                compatibility_match = (
                    parsed_profile.compatibility == expected_profile_binding
                    and parsed_profile.settings.model_dump(mode="json")
                    == effective_contract["parameters"]
                    and parsed_profile.measurement_policy.model_dump(mode="json")
                    == effective_policy
                )
                focus_profile = {
                    "status": (
                        parsed_profile.status if compatibility_match else "incompatible"
                    ),
                    "reason": parsed_profile.reason,
                    "profile": focus_profile_snapshot,
                    "policy_sha256": profile_policy_sha256,
                    "compatibility_match": compatibility_match,
                }
                if parsed_profile.status == "valid" and compatibility_match:
                    try:
                        _require_current_commissioning(
                            confirmation,
                            policy=parsed_profile.measurement_policy,
                            binding=current_calibration_binding,
                            implementation_id=str(implementation["id"]),
                            profile=parsed_profile,
                        )
                        if effective_profile_tolerance is None:
                            raise ValueError("Core omitted the effective focus budget")
                        focus_profile["effective_residual_tolerance_um"] = (
                            effective_profile_tolerance
                        )
                    except ValueError as exc:
                        focus_profile["use_refusal"] = str(exc)
                        mismatch(
                            "focus_profile_use_refused",
                            "profile",
                            "current commissioning and positive residual budget",
                            str(exc),
                        )
                    else:
                        profile_use_ready = True
                if parsed_profile.status == "candidate":
                    mismatch(
                        "focus_profile_candidate",
                        "profile.status",
                        "valid",
                        "candidate",
                    )
                elif not compatibility_match:
                    mismatch(
                        "focus_profile_incompatible",
                        "profile.compatibility",
                        expected_profile_binding,
                        parsed_profile.compatibility,
                    )
            except Exception as exc:
                focus_profile = {
                    "status": "incompatible",
                    "reason": str(exc),
                    "profile": focus_profile_snapshot,
                    "policy_sha256": None,
                    "compatibility_match": False,
                }
                mismatch(
                    "focus_profile_incompatible",
                    "profile",
                    "schema-valid compatible profile",
                    str(exc),
                )

        if is_legacy:
            policy_state = "legacy"
        elif not policy_match:
            policy_state = "dirty"
        elif not confirmation_match:
            policy_state = "stale" if not implementation_match else "incompatible"
        elif commissioning_match:
            policy_state = "commissioned"
        else:
            policy_state = "draft_uncommissioned"
        calibration_ready = bool(
            policy_match
            and binding_match
            and demo_contract_match
            and confirmation_match
        )
        measure_ready = bool(calibration_ready and commissioning_match)
        autofocus_ready = bool(
            measure_ready
            and profile_use_ready
            and focus_profile.get("status") == "valid"
            and focus_profile.get("compatibility_match") is True
        )
        return {
            "snapshot_epoch": str(uuid4()),
            "captured_at": captured_at,
            "ready": measure_ready,
            "measure_ready": measure_ready,
            "diagnostic_measure_ready": calibration_ready,
            "calibration_ready": calibration_ready,
            "autofocus_ready": autofocus_ready,
            "policy_state": policy_state,
            "policy_id": JPEG_DRAFT_POLICY_ID,
            "expected_policy_sha256": JPEG_DRAFT_POLICY_SHA256,
            "effective_policy_sha256": effective_policy_sha256,
            "expected_policy": expected_policy,
            "effective_policy": effective_policy,
            "measurement_policy_confirmation": confirmation,
            "measurement_implementation": implementation,
            "server": {
                "version": version_data.version,
                "revision": version_data.version_source,
                "revision_is_display_only": True,
            },
            "policy_match": policy_match,
            "binding_match": binding_match,
            "contract_match": contract_match,
            "commissioning_match": commissioning_match,
            "demo_contract_match": demo_contract_match,
            "expected_parameters": expected_contract,
            "effective_parameters": effective_contract,
            "parameter_hashes": {
                "expected": {
                    key: _identity(value) for key, value in expected_contract.items()
                },
                "effective": {
                    key: _identity(value) for key, value in effective_contract.items()
                },
            },
            "calibration_plan": {
                "path_um": prospective_path,
                "pair_count": prospective_pair_count,
            },
            "geometry": {
                "expected": {**EXPECTED_GEOMETRY, "geometry_id": EXPECTED_GEOMETRY_ID},
                "effective": geometry_snapshot,
                "match": geometry_match,
            },
            "camera": {
                "expected_sha256": EXPECTED_CAMERA_BINDING_SHA256,
                "effective_sha256": camera_sha256,
                "source_configuration": camera_configuration,
                "effective_binding": processed_camera,
                "flat_field_parameters": flat_parameters_snapshot,
                "match": camera_match,
            },
            "flat_fields": {
                "expected_contract_sha256": EXPECTED_FLAT_FIELD_CONTRACT_SHA256,
                "effective_snapshot_sha256": _identity(
                    {
                        "profiles": flat_fields,
                        "processing_reference": processing_reference,
                        "optics_id": optics_id,
                        "illumination_id": illumination_id,
                    }
                ),
                "expected_processing_reference_sha256": (
                    EXPECTED_PROCESSING_REFERENCE_SHA256
                ),
                "effective_processing_reference_sha256": (processing_reference_sha256),
                "profiles": flat_fields,
                "statuses": flat_statuses,
                "processing_reference": processing_reference,
                "optics_id": optics_id,
                "illumination_id": illumination_id,
                "match": flat_valid and flat_profiles_match and processing_match,
            },
            "binding": {
                "snapshot": binding_snapshot,
                "sha256": _identity(binding_snapshot),
            },
            "focus_profile": focus_profile,
            "commissioning": {
                "status": "commissioned" if commissioning_match else "uncommissioned",
                "evidence": commissioning_evidence,
                "reason": (
                    "Exact stationary commissioning evidence matches"
                    if commissioning_match
                    else "No matching stationary commissioning evidence"
                ),
            },
            "mismatches": mismatches,
        }

    @lt.property
    def calibration_status(self) -> dict[str, Any]:
        """Report candidate, valid or incompatible without capturing or moving."""
        if self.profile is None:
            return {"status": "not_calibrated", "reason": "No saved R/G focus profile"}
        try:
            profile = self.checked_profile(require_valid=False)
        except Exception as exc:
            return {"status": "incompatible", "reason": str(exc)}
        return {
            "status": profile.status,
            "reason": profile.reason,
            "profile_id": profile.id,
            "applicable_z_range_um": profile.applicable_z_range_um,
            "focus_z_um": profile.focus_z_um,
            "applicable_error_range_um": (
                profile.applicable_error_range_um
                if profile.focus_z_um is not None
                else None
            ),
            "holdout_max_absolute_error_um": profile.holdout_max_absolute_error_um,
            "approach_check": profile.approach_check.model_dump(mode="json"),
        }

    @lt.action
    def install_profile(self, profile: RGFocusModelProfile) -> dict[str, Any]:
        """Save a compatible offline fit, retaining candidate activation refusal."""
        if profile.settings != self.parameters:
            raise ValueError("Profile settings differ from saved validation policy")
        if profile.measurement_policy != self.measurement_parameters:
            raise ValueError("Profile measurement policy differs from saved settings")
        if profile.compatibility != self._binding():
            raise ValueError("Profile is incompatible with this microscope")
        value = profile.model_dump(mode="json")
        self._commit("profile", value)
        return self.calibration_status

    @staticmethod
    def _reference(
        metadata: dict[str, Any],
        mode: IlluminationMode,
        field_id: str,
        geometry_id: str,
    ) -> FrameReference:
        """Bind one fresh frame to the current capture field and geometry."""
        return FrameReference(
            frame_id=f"{field_id}:{mode}:{metadata['sensor_timestamp_ns']}",
            field_id=field_id,
            sensor_timestamp_ns=metadata["sensor_timestamp_ns"],
            mode=mode,
            geometry_id=geometry_id,
        )

    @staticmethod
    def _white_score(reference: TissueField, current: TissueField) -> float:
        """Score current WHITE detail only inside the fixed reference windows."""
        scores: list[float] = []
        for box in reference.boxes:
            ys = slice(box.y, box.y + box.h)
            xs = slice(box.x, box.x + box.w)
            support = reference.mask[ys, xs].astype(np.uint8)
            support = cv2.erode(support, np.ones((3, 3), np.uint8)).astype(bool)
            if np.count_nonzero(support) >= 16:
                laplacian = cv2.Laplacian(current.white_common[ys, xs], cv2.CV_64F)
                scores.append(float(np.var(laplacian[support])))
        if not scores:
            raise ValueError("Fixed tissue windows contain no WHITE sharpness support")
        return float(np.median(scores))

    def precheck_scan_white(self, white_source_rgb: np.ndarray) -> dict[str, Any]:
        """Classify an existing fresh Fast OFM WHITE frame before any R/G transition.

        This is routing evidence only.  It deliberately does not replace the WHITE
        frame captured with an accepted R/G pair and cannot train the focus model.
        """
        self.checked_profile()
        policy = self.measurement_parameters.model_copy(deep=True)
        policy.require_jpeg()
        configuration = getattr(self._cam, "jpeg_measurement_configuration", None)
        if not isinstance(configuration, dict):
            raise ValueError("Camera JPEG measurement configuration is unavailable")
        processed = processing_binding(configuration, self._rg_flat_field.parameters)
        geometry_value = processed.get("geometry")
        if not isinstance(geometry_value, dict):
            raise ValueError("Camera JPEG geometry is unavailable")
        geometry = parse_frame_geometry(geometry_value)

        source = np.asarray(white_source_rgb)
        source_shape = (
            geometry.source_image_size[1],
            geometry.source_image_size[0],
            3,
        )
        if source.shape != source_shape:
            raise ValueError(
                "Fast OFM WHITE precheck frame does not match the R/G source geometry"
            )
        x, y, width, height = geometry.processing_roi
        white = np.array(source[y : y + height, x : x + width], copy=True)
        precheck_id = f"fast_ofm-white-precheck:{uuid4()}"
        geometry_id = geometry_fingerprint(geometry)
        reference = FrameReference(
            frame_id=precheck_id,
            field_id=precheck_id,
            sensor_timestamp_ns=time.time_ns(),
            mode="white",
            geometry_id=geometry_id,
        )
        with tempfile.TemporaryDirectory(
            prefix=".core-white-precheck-", dir=self.data_dir
        ) as temporary:
            field = self._external_tissue_field(
                white, reference, geometry_value, policy, Path(temporary)
            )
        return {
            "status": field.metrics.status,
            "reason": field.metrics.reason,
            "metrics": field.metrics.model_dump(mode="json"),
            "geometry_id": geometry_id,
            "measurement_policy_sha256": measurement_policy_sha256(policy),
            "source": "fresh_fast_ofm_lores_white",
        }

    def prepare_tissue_field_for_scan(
        self, scan_id: str, field_id: str, policy: RGFocusMeasurementPolicy
    ) -> dict[str, Any]:
        """Capture one fresh same-owner WHITE field before a bounded WHITE search."""
        policy.require_jpeg()
        directory, report = self._new_run("scan_tissue_check")
        with self._rg_flat_field._session(True) as run:
            run.select("white")
            _planes, rgb, metadata = run.frame("scan-tissue-check")
            geometry_value = metadata["geometry"]
            geometry_id = geometry_fingerprint(parse_frame_geometry(geometry_value))
            reference = self._reference(metadata, "white", field_id, geometry_id)
            field = self._external_tissue_field(
                rgb,
                reference,
                geometry_value,
                policy,
                directory / "core-tissue-field",
            )
            cv2.imwrite(
                str(directory / "white-rgb.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            )
            cv2.imwrite(str(directory / "tissue-overlay.png"), field.overlay)
            write_json(directory / "white-frame.json", metadata)
        report.update(
            scan_id=scan_id,
            status=field.metrics.status,
            reason=field.metrics.reason,
            field_id=field_id,
            frame_id=reference.frame_id,
            frame_ref=f"rg_focus/{directory.name}/white-frame.json",
            metrics=field.metrics.model_dump(mode="json"),
            completed_utc=datetime.now(timezone.utc).isoformat(),
        )
        write_json(directory / "report.json", report)
        return {
            "id": directory.name,
            "status": field.metrics.status,
            "reason": field.metrics.reason,
            "frame_id": reference.frame_id,
            "report_ref": f"rg_focus/{directory.name}/report.json",
        }

    def _capture_pair(  # noqa: PLR0915
        self,
        output_directory: Path,
        iteration: int,
        *,
        field_id: str | None = None,
        fixed_field: TissueField | None = None,
        calculate_white_score: bool = False,
        measurement_policy: RGFocusMeasurementPolicy | None = None,
        persist_colour_frames: bool = False,
    ) -> CapturedRGFocusPair:
        """Capture one atomic pair and evaluate it on fresh or fixed tissue windows."""
        policy = measurement_policy or self.measurement_parameters
        policy.require_jpeg()
        checkpoints = {"stage_validation": time.monotonic()}
        failure: BaseException | None = None
        capture_id: str | None = None
        try:
            self._stage.validate_path([])
            checkpoints["acquisition"] = time.monotonic()
            handoff = self._rg_flat_field._capture_focus_pair(
                prepared=True,
                persist_colour_frames=persist_colour_frames,
            )
            output = handoff.capture
            checkpoints["input_load"] = time.monotonic()
            if not output.get("white_restored"):
                raise ValueError("R/G pair did not confirm WHITE restoration")
            capture_id = str(output["id"])
            directory = Path(output["directory"]).resolve()
            if not directory.is_relative_to(
                Path(self._rg_flat_field.data_dir).resolve()
            ):
                raise ValueError("R/G capture path is outside its owner data directory")
            metadata = handoff.metadata
            geometry_value = metadata["white"]["geometry"]
            geometry = parse_frame_geometry(geometry_value)
            geometry_id = geometry_fingerprint(geometry)
            measurement_field_id = field_id or capture_id
            white = handoff.white_rgb
            checkpoints["tissue"] = time.monotonic()
            current_field = self._external_tissue_field(
                white,
                self._reference(
                    metadata["white"], "white", measurement_field_id, geometry_id
                ),
                geometry_value,
                policy,
                output_directory / f"iteration-{iteration:02d}-tissue-field-core",
            )
            field = fixed_field or current_field
            checkpoints["source_planes"] = time.monotonic()
            red_source = handoff.red_source_jpeg8
            green_source = handoff.green_source_jpeg8
            checkpoints["red_correction"] = time.monotonic()
            red, red_valid = self._rg_flat_field.correct_frame(
                "red", red_source, metadata["red"]
            )
            checkpoints["green_correction"] = time.monotonic()
            green, green_valid = self._rg_flat_field.correct_frame(
                "green", green_source, metadata["green"]
            )
            checkpoints["estimate"] = time.monotonic()
            measurement = self._external_measure(
                RGFocusInputs(
                    red_plane=red,
                    green_plane=green,
                    red_source_jpeg8=red_source,
                    green_source_jpeg8=green_source,
                    red_valid=red_valid,
                    green_valid=green_valid,
                    field=field,
                    red_reference=self._reference(
                        metadata["red"], "red", measurement_field_id, geometry_id
                    ),
                    green_reference=self._reference(
                        metadata["green"], "green", measurement_field_id, geometry_id
                    ),
                    geometry_value=geometry_value,
                ),
                policy,
                output_directory,
                iteration,
            )
            checkpoints["overlay"] = time.monotonic()
            cv2.imwrite(
                str(output_directory / f"iteration-{iteration:02d}-overlay.png"),
                field.overlay,
            )
            checkpoints["frame_references"] = time.monotonic()
            red_reference = self._reference(
                metadata["red"], "red", measurement_field_id, geometry_id
            )
            green_reference = self._reference(
                metadata["green"], "green", measurement_field_id, geometry_id
            )
            white_reference = self._reference(
                metadata["white"], "white", measurement_field_id, geometry_id
            )
            white_score = None
            if calculate_white_score:
                checkpoints["white_score"] = time.monotonic()
                white_score = self._white_score(field, current_field)
            return CapturedRGFocusPair(
                measurement=measurement,
                capture_id=capture_id,
                field=field,
                white_score=white_score,
                capture_path=str(directory),
                frame_ids=(
                    white_reference.frame_id,
                    red_reference.frame_id,
                    green_reference.frame_id,
                ),
            )
        except BaseException as exc:
            failure = exc
            raise
        finally:
            ended = time.monotonic()
            phases = list(checkpoints.items())
            timing = {
                "clock": "time.monotonic",
                "capture_id": capture_id,
                "status": "completed" if failure is None else "failed",
                "failed_phase": None if failure is None else phases[-1][0],
                "error": None if failure is None else str(failure),
                "elapsed_s": ended - phases[0][1],
                "scope": "inclusive capture_pair before timing sidecar persistence; phases partition elapsed_s",
                "acquisition_scope": "inclusive owner session/readiness/capture/save/WHITE cleanup; nested details in acquisition.json, do not add twice",
                "phases": {
                    name: {
                        "seconds": (
                            phases[index + 1][1] if index + 1 < len(phases) else ended
                        )
                        - started,
                        "count": 1,
                    }
                    for index, (name, started) in enumerate(phases)
                },
            }
            try:
                write_json(
                    output_directory / f"iteration-{iteration:02d}-timing.json", timing
                )
            except Exception as timing_error:
                # Diagnostic persistence must not replace capture/cleanup failures
                # or turn a successful optical measurement into a retryable failure.
                self.logger.warning("Cannot save R/G phase timing: %s", timing_error)

    def _capture_measurement(
        self, output_directory: Path, iteration: int
    ) -> CapturedRGFocusPair:
        """Capture one owned same-request processed-JPEG pair."""
        return self._capture_pair(output_directory, iteration)

    def _new_run(self, kind: str) -> tuple[Path, dict[str, Any]]:
        """Create one owned evidence directory and initial append-only report."""
        identifier = str(uuid4())
        directory = Path(self.data_dir) / identifier
        directory.mkdir()
        return directory, {
            "id": identifier,
            "kind": kind,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "iterations": [],
        }

    def _approach_move(
        self,
        target: tuple[int, int, int],
        origin_z: int,
        maximum_excursion_um: float,
        approach: RGFocusApproachSettings,
    ) -> list[int]:
        """Check the complete local envelope, then let the stage own both legs."""
        units_per_um = self._stage.hardware_settings["axes"]["z"]["units_per_mm"] / 1000
        preload = approach.preload_um * units_per_um
        if not np.isclose(preload, round(preload)):
            raise ValueError("Final approach is not representable in stage units")
        path = [target[2] - approach.approach_sign * round(preload), target[2]]
        if any(abs(z - origin_z) / units_per_um > maximum_excursion_um for z in path):
            raise ValueError("Final-approach preload exceeds the experiment envelope")
        self._stage.validate_path([{"z": z} for z in path])
        if self._stage.get_xyz_position()[:2] != target[:2]:
            raise ValueError("XY changed before the Z final approach")
        self._stage.move_z_with_preload(
            target[2], round(preload), approach.approach_sign
        )
        if self._stage.get_xyz_position() != target:
            raise ValueError("Final approach did not reach the commanded target")
        return path

    @lt.action
    def measure(self, prepared: bool = False) -> dict[str, Any]:
        """Measure from the owned JPEG handoff without Z motion."""
        self.measurement_parameters.require_jpeg()
        if not prepared:
            raise ValueError("Confirm tissue and exclusive microscope control")
        directory, report = self._new_run("measure")
        pair = self._capture_measurement(directory, 1)
        measurement, capture_id = pair.measurement, pair.capture_id
        report.update(
            status=measurement.status,
            diagnostic_only=True,
            autofocus_authorized=False,
            capture_id=capture_id,
            capture_path=pair.capture_path,
            frame_ids=list(pair.frame_ids),
            measurement=measurement.model_dump(mode="json"),
            completed_utc=datetime.now(timezone.utc).isoformat(),
        )
        write_json(directory / "report.json", report)
        return {**report, "data_path": directory.name}

    @lt.action
    def calibrate(  # noqa: C901, PLR0912, PLR0915
        self, prepared: bool = False
    ) -> dict[str, Any]:
        """Capture, fit and save one bounded XY-fixed R/G Z calibration.

        A failed fit never replaces the prior profile. A completed fit may save a
        candidate, but only a profile passing holdout and two-sided approach checks
        becomes usable. Cancellation stops before the next bounded operation and
        does not claim an automatic return.
        """
        self.measurement_parameters.require_jpeg()
        if not prepared:
            raise ValueError(
                "Confirm focused tissue, Z clearance and exclusive control"
            )
        readiness = self.readiness
        if not readiness["calibration_ready"]:
            raise ValueError("Exact JPEG demo calibration preflight is not ready")
        settings = self.calibration_parameters.model_copy(deep=True)
        approach = self.approach_parameters.model_copy(deep=True)
        model_parameters = self.parameters.model_copy(deep=True)
        measurement_policy = self.measurement_parameters.model_copy(deep=True)
        measurement_policy.require_jpeg()
        common_policy = {
            "minimum_valid_fit_points": settings.minimum_valid_fit_points,
            "minimum_valid_holdout_points": settings.minimum_valid_holdout_points,
            "maximum_holdout_error_um": settings.maximum_holdout_error_um,
            "maximum_approach_white_score_fraction": (
                settings.maximum_approach_white_score_fraction
            ),
        }
        if any(
            getattr(model_parameters, key) != value
            for key, value in common_policy.items()
        ):
            raise ValueError(
                "Calibration plan and saved model validation policy differ"
            )
        self._stage.validate_path([])
        start = self._stage.get_xyz_position()
        binding = self._binding()
        units_per_um = self._stage.hardware_settings["axes"]["z"]["units_per_mm"] / 1000
        offsets = calibration_path(settings, approach)
        if any(
            not np.isclose(offset * units_per_um, round(offset * units_per_um))
            for offset in offsets
        ):
            raise ValueError("Calibration Z grid is not representable in stage units")
        targets = [start[2] + round(offset * units_per_um) for offset in offsets]
        self._stage.validate_path([{"z": target} for target in targets])

        directory, report = self._new_run("calibrate")
        report.update(
            start_position_units=list(start),
            parameters=settings.model_dump(mode="json"),
            approach_parameters=approach.model_dump(mode="json"),
            planned_z_path_um=list(offsets),
            model_parameters=model_parameters.model_dump(mode="json"),
            measurement_parameters=measurement_policy.model_dump(mode="json"),
            binding=binding,
            readiness_epoch=readiness["snapshot_epoch"],
            expected_pair_count=len(capture_plan(settings)) + 2,
            manifest=led_calibration_manifest(settings).model_dump(mode="json"),
            stationary_pairs=[],
            captures=[],
            moves=[],
        )
        write_json(directory / "report.json", report)
        deadline = time.monotonic() + settings.timeout_s
        plan = capture_plan(settings)
        points: list[RGFocusCalibrationPoint] = []
        pair_count = 0
        expected_position = start

        def check(phase: str) -> None:
            lt.raise_if_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("R/G calibration exceeded its total timeout")
            self._stage.validate_path([])
            if self._binding() != binding:
                raise ValueError("Camera, flat-field or Z configuration changed")
            if (
                self.calibration_parameters != settings
                or self.approach_parameters != approach
                or self.parameters != model_parameters
                or self.measurement_parameters != measurement_policy
            ):
                raise ValueError("Frozen calibration settings changed")
            current = self._stage.get_xyz_position()
            if current != expected_position:
                raise ValueError("XYZ changed outside the R/G calibration plan")
            self.progress = {"phase": phase, "pairs": pair_count}

        def move(offset_um: int, *, final_approach: bool = True) -> None:
            nonlocal expected_position
            check("move")
            target = (start[0], start[1], start[2] + round(offset_um * units_per_um))
            if final_approach:
                path = self._approach_move(
                    target, start[2], settings.maximum_absolute_z_um, approach
                )
            else:
                self._stage.validate_path([{"z": target[2]}])
                self._stage.move_absolute_in_segments(target)
                path = [target[2]]
            actual = self._stage.get_xyz_position()
            report["moves"].append(
                {
                    "offset_um": offset_um,
                    "target_units": list(target),
                    "confirmed_units": list(actual),
                    "z_path_units": path,
                    "final_approach": final_approach,
                }
            )
            write_json(directory / "report.json", report)
            if actual != target:
                raise ValueError(
                    "R/G calibration move did not reach its commanded target"
                )
            expected_position = target

        def field_identity(field: TissueField) -> dict[str, object]:
            return {
                "field_id": field.reference.field_id,
                "geometry_id": field.reference.geometry_id,
                "binding_sha256": _identity(binding),
                "mask_sha256": hashlib.sha256(
                    np.ascontiguousarray(field.mask).tobytes()
                ).hexdigest(),
                "window_ids": [box.patch_id for box in field.boxes],
            }

        def stationary_capture(
            index: int, fixed_field: TissueField | None
        ) -> tuple[CapturedRGFocusPair, RGFocusStationaryObservation]:
            nonlocal pair_count
            check("stationary_capture")
            pair = self._capture_pair(
                directory,
                pair_count + 1,
                field_id=directory.name,
                fixed_field=fixed_field,
                calculate_white_score=True,
                measurement_policy=measurement_policy,
                persist_colour_frames=True,
            )
            pair_count += 1
            check("stationary_readback")
            if pair.white_score is None:
                raise RuntimeError("Stationary pair omitted its WHITE score")
            identity = field_identity(pair.field)
            observation = RGFocusStationaryObservation(
                capture_id=pair.capture_id,
                measurement_status=pair.measurement.status,
                dx=pair.measurement.dx,
                dy=pair.measurement.dy,
                white_score=pair.white_score,
                accepted_patch_count=pair.measurement.accepted_patch_count,
                inlier_fraction=pair.measurement.inlier_fraction,
                field_id=str(identity["field_id"]),
                geometry_id=str(identity["geometry_id"]),
                binding_sha256=str(identity["binding_sha256"]),
                mask_sha256=str(identity["mask_sha256"]),
                window_ids=tuple(cast(list[str], identity["window_ids"])),
            )
            report["stationary_pairs"].append(
                {
                    "index": index,
                    "ofm_capture_id": pair.capture_id,
                    "ofm_capture_path": pair.capture_path,
                    "frame_ids": list(pair.frame_ids),
                    "measurement": pair.measurement.model_dump(mode="json"),
                    "white_score": pair.white_score,
                    "position_units": list(self._stage.get_xyz_position()),
                    **identity,
                }
            )
            write_json(directory / "report.json", report)
            return pair, observation

        def capture(
            item: RGFocusCapture, fixed_field: TissueField
        ) -> CapturedRGFocusPair:
            nonlocal pair_count
            check("capture")
            pair = self._capture_pair(
                directory,
                pair_count + 1,
                field_id=directory.name,
                fixed_field=fixed_field,
                calculate_white_score=True,
                measurement_policy=measurement_policy,
                persist_colour_frames=True,
            )
            pair_count += 1
            check("capture_readback")
            measurement = pair.measurement
            if pair.white_score is None:
                raise RuntimeError("Calibration pair omitted its WHITE score")
            record = {
                **item.model_dump(mode="json"),
                "ofm_capture_id": pair.capture_id,
                "ofm_capture_path": pair.capture_path,
                "frame_ids": list(pair.frame_ids),
                "measurement": measurement.model_dump(mode="json"),
                "white_score": pair.white_score,
                "position_units": list(self._stage.get_xyz_position()),
            }
            report["captures"].append(record)
            write_json(directory / "report.json", report)
            self.progress = {"phase": "capture", "pairs": pair_count}
            if item.role != "reference":
                points.append(
                    RGFocusCalibrationPoint(
                        capture_id=item.capture_id,
                        role=item.role,
                        z_um=float(item.z_um),
                        measurement_status=measurement.status,
                        dx=measurement.dx,
                        dy=measurement.dy,
                        confidence=measurement.confidence,
                        white_score=pair.white_score,
                    )
                )
            return pair

        try:
            check("stationary")
            first_stationary, first_observation = stationary_capture(1, None)
            fixed_field = first_stationary.field
            second_stationary, second_observation = stationary_capture(2, fixed_field)
            stationary_observations = (first_observation, second_observation)
            identity_names = (
                "field_id",
                "geometry_id",
                "binding_sha256",
                "mask_sha256",
                "window_ids",
            )
            stationary_failures: list[str] = []
            if fixed_field.metrics.status != "ready":
                stationary_failures.append("stationary_tissue_field_ready")
            if (
                first_stationary.capture_id == second_stationary.capture_id
                or second_stationary.field is not fixed_field
            ):
                stationary_failures.append("stationary_unique_capture_or_field")
            if any(
                getattr(first_observation, name) != getattr(second_observation, name)
                for name in identity_names
            ):
                stationary_failures.append("stationary_identity")
            if any(
                observation.measurement_status != "ready"
                or observation.dx is None
                or observation.dy is None
                for observation in stationary_observations
            ):
                stationary_failures.append("stationary_ready")
            if any(
                observation.accepted_patch_count
                < measurement_policy.core.minimum_patch_count
                for observation in stationary_observations
            ):
                stationary_failures.append("stationary_patch_count")
            if any(
                observation.inlier_fraction
                < measurement_policy.estimator.minimum_inlier_fraction
                for observation in stationary_observations
            ):
                stationary_failures.append("stationary_inlier_fraction")
            stationary_white_fraction = abs(
                first_observation.white_score - second_observation.white_score
            ) / max(first_observation.white_score, second_observation.white_score)
            if (
                stationary_white_fraction
                > model_parameters.maximum_approach_white_score_fraction
            ):
                stationary_failures.append("stationary_white_mutual_fraction")
            implementation = measurement_implementation_identity()
            evidence_core = {
                "status": "failed" if stationary_failures else "passed",
                "failures": stationary_failures,
                "policy_id": JPEG_DRAFT_POLICY_ID,
                "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
                "measurement_implementation_id": implementation["id"],
                "binding_sha256": _identity(binding),
                "field": field_identity(fixed_field),
                "capture_ids": [
                    first_stationary.capture_id,
                    second_stationary.capture_id,
                ],
                "stationary_observations": [
                    value.model_dump(mode="json") for value in stationary_observations
                ],
                "white_mutual_fraction": stationary_white_fraction,
                "maximum_white_mutual_fraction": (
                    model_parameters.maximum_approach_white_score_fraction
                ),
                "minimum_patch_count": (measurement_policy.core.minimum_patch_count),
                "minimum_inlier_fraction": (
                    measurement_policy.estimator.minimum_inlier_fraction
                ),
                "saved_jpeg_replay": {"comparison": "exact", "tolerance": 0},
            }
            commissioning_evidence = {
                "id": _identity(evidence_core),
                **evidence_core,
            }
            report["commissioning"] = {
                "persisted": False,
                "evidence": commissioning_evidence,
            }
            write_json(directory / "report.json", report)
            if stationary_failures:
                raise ValueError(
                    "Stationary commissioning failed: " + ", ".join(stationary_failures)
                )
            self._commit_many(
                {
                    "measurement_policy_confirmation": {
                        "policy_id": JPEG_DRAFT_POLICY_ID,
                        "policy_sha256": JPEG_DRAFT_POLICY_SHA256,
                        "measurement_implementation_id": implementation["id"],
                        "state": "commissioned",
                        "commissioning_evidence": commissioning_evidence,
                    }
                }
            )
            report["commissioning"]["persisted"] = True
            write_json(directory / "report.json", report)

            check("reference")
            reference_pair = capture(plan[0], fixed_field)
            for item in plan[1:]:
                if item.role == "approach_above":
                    move(settings.approach_start_um, final_approach=False)
                    move(0)
                elif item.role == "approach_below":
                    move(-settings.approach_start_um, final_approach=False)
                    move(0)
                else:
                    move(item.z_um)
                capture(item, fixed_field)
            check("fit_holdout")
            profile = self._external_calibration_fit(
                directory=directory,
                points=points,
                settings=model_parameters,
                reference_white_score=cast(float, reference_pair.white_score),
                stationary_observations=stationary_observations,
                profile_id=str(uuid4()),
                source_series_id=directory.name,
                compatibility=binding,
                source_evidence={
                    "report_path": f"rg_focus/{directory.name}/report.json",
                    "commissioning_evidence_id": str(commissioning_evidence["id"]),
                },
                measurement_policy=measurement_policy,
            )
            if self._stage.get_xyz_position() != start:
                raise ValueError("Calibration did not return to the commanded start")
            report.update(
                status=profile.status,
                reason=profile.reason,
                profile=profile.model_dump(mode="json"),
                returned_to_commanded_start=True,
                completed_utc=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "report.json", report)
            self._commit("profile", profile.model_dump(mode="json"))
            self.progress = {"phase": "complete", "pairs": pair_count}
            return {**report, "data_path": directory.name}
        except BaseException as exc:
            returned_to_start = False
            try:
                returned_to_start = self._stage.get_xyz_position() == start
            except BaseException:
                pass
            report.update(
                status="failed",
                error=str(exc),
                failed_phase=self.progress.get("phase"),
                returned_to_commanded_start=returned_to_start,
                completed_utc=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "report.json", report)
            self.progress = {"phase": "stopped", "pairs": pair_count}
            raise

    @lt.action
    def autofocus(self, prepared: bool = False) -> dict[str, Any]:
        """Preserve the standalone public autofocus contract without scan context."""
        return self._autofocus(prepared=prepared, field=None, verify_after_move=True)

    @lt.action
    def autofocus_with_white_fallback(
        self, prepared: bool = False, white_dz: int | None = None
    ) -> dict[str, Any]:
        """Use one modelled R/G correction, or one WHITE sweep on refusal."""
        return self._autofocus_with_white_fallback(
            prepared=prepared,
            white_dz=white_dz,
            verify_after_move=False,
        )

    def autofocus_with_white_fallback_for_scan(
        self, *, white_dz: int | None = None
    ) -> dict[str, Any]:
        """Use one R/G pair and one modelled Z correction for a demo scan field."""
        return self._autofocus_with_white_fallback(
            prepared=True,
            white_dz=white_dz,
            verify_after_move=False,
        )

    def autofocus_white_precheck_for_scan(
        self,
        *,
        white_dz: int | None,
        precheck: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a rejected fresh WHITE tissue precheck directly to one sweep."""
        status = precheck.get("status")
        if status not in {
            "no_tissue",
            "insufficient_tissue",
            "low_signal",
            "saturated",
        }:
            raise ValueError("Direct WHITE routing requires a rejected tissue precheck")
        reason = str(precheck.get("reason") or status)
        return self._run_white_scan_focus(
            kind="autofocus_white_precheck",
            focus_method="white_precheck",
            white_dz=white_dz,
            details={
                "fallback_count": 1,
                "fallback_reason": f"{status}: {reason}",
                "rg_attempted": False,
                "precheck": deepcopy(precheck),
            },
        )

    def autofocus_white_after_simultaneous_refusal(
        self,
        *,
        white_dz: int | None,
        simultaneous_reason: str,
        simultaneous_result_id: str,
        simultaneous_report_ref: str,
        separate_rg_unavailable_reason: str,
        peripheral_search: dict[str, Any] | None = None,
        mixed_capture_count: int = 0,
    ) -> dict[str, Any]:
        """Archive one direct WHITE handoff from a bounded simultaneous refusal.

        The simultaneous owner alone classifies fallback-eligible refusals. It
        supplies the same small diagnostic payload on success and refusal; this
        helper does not reinterpret hardware faults or try separate JPEG R/G.
        """
        return self._run_white_scan_focus(
            kind="autofocus_white_simultaneous_fallback",
            focus_method="white_fallback",
            white_dz=white_dz,
            details={
                "fallback_count": 1,
                "fallback_reason": simultaneous_reason,
                "simultaneous_result_id": simultaneous_result_id,
                "simultaneous_report_ref": simultaneous_report_ref,
                "simultaneous_attempted": True,
                "rg_attempted": mixed_capture_count > 0,
                "mixed_capture_count": mixed_capture_count,
                "peripheral_search": deepcopy(peripheral_search),
                "separate_rg_attempted": False,
                "separate_rg_unavailable_reason": separate_rg_unavailable_reason,
            },
        )

    def autofocus_white_sparse_fallback_for_scan(
        self, *, white_dz: int | None, reason: str
    ) -> dict[str, Any]:
        """Focus a degraded sparse field in WHITE without firing an RG probe."""
        return self._run_white_scan_focus(
            kind="autofocus_white_sparse_fallback",
            focus_method="white_fallback",
            white_dz=white_dz,
            details={
                "fallback_count": 1,
                "fallback_reason": reason,
                "simultaneous_attempted": False,
                "rg_attempted": False,
                "mixed_capture_count": 0,
                "peripheral_search": None,
                "separate_rg_attempted": False,
                "sparse_degraded": True,
            },
        )

    def _run_white_scan_focus(
        self,
        *,
        kind: str,
        focus_method: str,
        white_dz: int | None,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        """Run and archive exactly one explicit WHITE scan-focus handoff."""
        if self._white_autofocus is None:
            raise ValueError("WHITE fallback component is unavailable")
        directory, report = self._new_run(kind)
        report.update(
            status="running",
            focus_method=focus_method,
            white_dz=white_dz,
            start_position_units=list(self._stage.get_xyz_position()),
            **details,
        )
        write_json(directory / "report.json", report)
        try:
            white_result = self._white_autofocus.fast_autofocus(
                dz=white_dz, start="centre"
            )
            final_position = self._stage.get_xyz_position()
            report.update(
                status="focused",
                final_position_units=list(final_position),
                white_result=white_result.model_dump(mode="json"),
                completed_utc=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "report.json", report)
            return {
                **report,
                "data_path": directory.name,
                "report_ref": f"rg_focus/{directory.name}/report.json",
            }
        except BaseException as white_error:
            report.update(
                status="failed",
                white_error_type=type(white_error).__name__,
                white_error=str(white_error),
                completed_utc=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "report.json", report)
            # Preserve the original hardware/cancellation exception type while
            # allowing the acquisition journal to retain this failed handoff.
            white_error.scan_focus_outcome = {  # type: ignore[attr-defined]
                **report,
                "report_ref": f"rg_focus/{directory.name}/report.json",
            }
            raise

    def _autofocus_with_white_fallback(
        self,
        *,
        prepared: bool,
        white_dz: int | None,
        verify_after_move: bool,
    ) -> dict[str, Any]:
        """Run the selected R/G policy and exactly one WHITE fallback if refused."""
        try:
            result = self._autofocus(
                prepared=prepared,
                field=None,
                verify_after_move=verify_after_move,
            )
        except RGFocusFallbackEligibleError as rg_error:
            try:
                return self._run_white_scan_focus(
                    kind="autofocus_white_fallback",
                    focus_method="white_fallback",
                    white_dz=white_dz,
                    details={
                        "fallback_count": 1,
                        "fallback_reason": str(rg_error),
                        "rg_attempted": True,
                        "rg_result_id": rg_error.result_id,
                        "rg_report_ref": rg_error.report_ref,
                    },
                )
            except ValueError as white_error:
                raise white_error from rg_error
        return {**result, "focus_method": "rg", "fallback_count": 0}

    def autofocus_for_scan(self, field: FocusFieldRuntime) -> dict[str, Any]:
        """Use one modelled pair for every bounded demo scan field.

        An already-focused measurement is independently observed at final Z and
        may seed the surface.  A corrected one-shot result is candidate evidence:
        it is saved with the modelled residual but deliberately does not train the
        surface, whether or not a prediction was available before the pair.
        """
        return self._autofocus(
            prepared=True,
            field=field,
            verify_after_move=False,
        )

    def _autofocus(  # noqa: C901, PLR0912, PLR0915
        self,
        *,
        prepared: bool,
        field: FocusFieldRuntime | None,
        verify_after_move: bool,
    ) -> dict[str, Any]:
        """Run bounded R/G focus with optional independent post-move verification."""
        if not prepared:
            raise ValueError(
                "Confirm tissue, Z clearance and exclusive microscope control"
            )
        profile = (
            self.checked_profile()
            if field is None
            else field.frozen.profile.model_copy(deep=True)
        )
        settings = (
            self.control_parameters.model_copy(deep=True)
            if field is None
            else field.frozen.control
        )
        validated_profile, effective_tolerance = self._external_profile_validation(
            profile.model_dump(mode="json"),
            focus_tolerance_um=settings.focus_tolerance_um,
        )
        if validated_profile != profile or effective_tolerance is None:
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Core changed or omitted the autofocus profile"
            )
        self._stage.validate_path([])
        start = self._stage.get_xyz_position()
        approach = (
            self.approach_parameters.model_copy(deep=True)
            if field is None
            else field.frozen.binding.approach_parameters.model_copy(deep=True)
        )
        expected_position = start
        directory, report = self._new_run("autofocus")
        measurement_policy = (
            self.measurement_parameters.model_copy(deep=True)
            if field is None
            else field.frozen.measurement_policy.model_copy(deep=True)
        )
        measurement_policy.require_jpeg()
        report.update(
            profile_id=profile.id,
            scan_id=None if field is None else field.session.scan_id,
            field_id=None if field is None else field.field_id,
            attempt_id=None if field is None else field.attempt_id,
            parameters=settings.model_dump(mode="json"),
            empirical_prediction_error_um=profile.empirical_prediction_error_um,
            effective_residual_tolerance_um=effective_tolerance,
            empirical_cross_track_limit_px=profile.empirical_cross_track_limit_px,
            measurement_parameters=measurement_policy.model_dump(mode="json"),
            approach_parameters=approach.model_dump(mode="json"),
            start_position_units=list(start),
            verification_mode=(
                "independent_pair" if verify_after_move else "single_pair_model"
            ),
        )
        started = time.monotonic()
        total_correction_um = 0.0
        try:
            for iteration in range(1, settings.maximum_iterations + 1):
                remaining = settings.timeout_s - (time.monotonic() - started)
                if remaining < settings.minimum_capture_budget_s:
                    raise ValueError(
                        "R/G autofocus timeout leaves no complete capture budget"
                    )
                if field is None:
                    self.checked_profile()
                else:
                    field.before_effect(
                        f"rg_capture_{iteration}",
                        reserve_s=settings.minimum_capture_budget_s,
                    )
                    # The field gate rechecks live physical identities through
                    # the frozen scan binding, then rechecks the real deadline.
                    # checked_profile() instead reads next-run form settings.
                if self._stage.get_xyz_position() != expected_position:
                    raise ValueError("XYZ changed outside the R/G autofocus plan")
                if field is None and iteration == 1:
                    # A standalone click has no scan planner to establish the
                    # calibrated final approach. Normalise the starting Z to
                    # that same approach before interpreting the first pair;
                    # the net target remains unchanged. Scan focus already
                    # arrives through FocusFieldRuntime's prepared approach.
                    report["initial_z_path_units"] = self._approach_move(
                        start,
                        start[2],
                        settings.maximum_absolute_z_excursion_um,
                        approach,
                    )
                    report["initial_final_approach_confirmed"] = True
                    write_json(directory / "report.json", report)
                    remaining = settings.timeout_s - (time.monotonic() - started)
                    if remaining < settings.minimum_capture_budget_s:
                        raise ValueError(
                            "R/G autofocus timeout leaves no complete capture budget "
                            "after initial final approach"
                        )
                if field is None:
                    pair = self._capture_measurement(directory, iteration)
                    measurement, capture_id = pair.measurement, pair.capture_id
                    capture_path = pair.capture_path
                    frame_ids = pair.frame_ids
                else:
                    pair = self._capture_pair(
                        directory,
                        iteration,
                        field_id=field.field_id,
                        measurement_policy=measurement_policy,
                    )
                    measurement, capture_id = pair.measurement, pair.capture_id
                    capture_path = pair.capture_path
                    frame_ids = pair.frame_ids
                    field.sync_after_effect(f"rg_capture_{iteration}")
                    field.mark_measured(
                        iteration=iteration,
                        capture_id=capture_id,
                        frame_ids=frame_ids,
                        measurement_status=measurement.status,
                    )
                position = self._stage.get_xyz_position()
                if position != expected_position:
                    raise ValueError("XYZ changed during R/G capture")
                decision = self._external_decision(
                    profile,
                    measurement,
                    settings,
                    iteration=iteration,
                    total_correction_um=total_correction_um,
                )
                entry: dict[str, Any] = {
                    "iteration": iteration,
                    "capture_id": capture_id,
                    "capture_path": capture_path,
                    "frame_ids": list(frame_ids),
                    "position_before_units": list(position),
                    "measurement": measurement.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                }
                report["iterations"].append(entry)
                write_json(directory / "report.json", report)
                if time.monotonic() - started >= settings.timeout_s:
                    raise ValueError("R/G autofocus timeout expired after capture")
                if decision.status == "focused":
                    report.update(
                        status="focused",
                        focus_z_units=position[2],
                        total_correction_um=total_correction_um,
                        final_estimated_error_um=decision.inferred_z_error_um,
                        independent_post_move_verification=(total_correction_um > 0),
                        duration_s=time.monotonic() - started,
                        completed_utc=datetime.now(timezone.utc).isoformat(),
                    )
                    write_json(directory / "report.json", report)
                    if time.monotonic() - started >= settings.timeout_s:
                        raise ValueError(
                            "R/G autofocus timeout expired before returning focused"
                        )
                    return {
                        **report,
                        "data_path": directory.name,
                        "report_ref": f"rg_focus/{directory.name}/report.json",
                    }
                if decision.status == "refused" or decision.correction_um is None:
                    raise RGFocusFallbackEligibleError(
                        decision.reason,
                        result_id=str(report["id"]),
                        report_ref=f"rg_focus/{directory.name}/report.json",
                    )
                if time.monotonic() - started >= settings.timeout_s:
                    raise ValueError("R/G autofocus timeout expired before correction")
                z_units_per_um = (
                    self._stage.hardware_settings["axes"]["z"]["units_per_mm"] / 1000
                )
                correction_units = round(decision.correction_um * z_units_per_um)
                if correction_units == 0:
                    raise RGFocusFallbackEligibleError(
                        "Required correction is below stage resolution",
                        result_id=str(report["id"]),
                        report_ref=f"rg_focus/{directory.name}/report.json",
                    )
                actual_correction_um = abs(correction_units / z_units_per_um)
                signed_correction_um = correction_units / z_units_per_um
                inferred_error_um = decision.inferred_z_error_um
                if inferred_error_um is None:
                    raise ValueError("Correction decision omitted inferred error")
                estimated_final_error_um = inferred_error_um + signed_correction_um
                if (
                    not verify_after_move
                    and abs(estimated_final_error_um) > effective_tolerance
                ):
                    raise RGFocusFallbackEligibleError(
                        "One-shot correction would leave the configured focus tolerance",
                        result_id=str(report["id"]),
                        report_ref=f"rg_focus/{directory.name}/report.json",
                    )
                if (
                    actual_correction_um > settings.maximum_single_correction_um
                    or total_correction_um + actual_correction_um
                    > settings.maximum_total_correction_um
                ):
                    raise RGFocusFallbackEligibleError(
                        "Rounded correction exceeds its configured budget",
                        result_id=str(report["id"]),
                        report_ref=f"rg_focus/{directory.name}/report.json",
                    )
                target = (position[0], position[1], position[2] + correction_units)
                if field is None:
                    self.checked_profile()
                    entry["z_path_units"] = self._approach_move(
                        target,
                        start[2],
                        settings.maximum_absolute_z_excursion_um,
                        approach,
                    )
                else:
                    field.current_binding()
                    field.session.plan_correction(field, inferred_error_um)
                    entry["z_path_units"] = list(field.last_correction_z_path_units)
                expected_position = target
                total_correction_um += abs(correction_units / z_units_per_um)
                entry["target_units"] = list(target)
                entry["position_after_units"] = list(self._stage.get_xyz_position())
                write_json(directory / "report.json", report)
                if not verify_after_move:
                    report.update(
                        status="focused",
                        focus_z_units=expected_position[2],
                        total_correction_um=total_correction_um,
                        final_estimated_error_um=estimated_final_error_um,
                        independent_post_move_verification=False,
                        duration_s=time.monotonic() - started,
                        completed_utc=datetime.now(timezone.utc).isoformat(),
                    )
                    write_json(directory / "report.json", report)
                    return {
                        **report,
                        "data_path": directory.name,
                        "report_ref": f"rg_focus/{directory.name}/report.json",
                    }
            raise RGFocusFallbackEligibleError(
                "R/G autofocus exhausted its bounded iterations",
                result_id=str(report["id"]),
                report_ref=f"rg_focus/{directory.name}/report.json",
            )
        except BaseException as exc:
            report.update(
                status="failed",
                error=str(exc),
                total_correction_um=total_correction_um,
                completed_utc=datetime.now(timezone.utc).isoformat(),
            )
            write_json(directory / "report.json", report)
            if field is not None:
                field.record_rg_failure(str(report["id"]), exc)
            if isinstance(exc, FastOFMCoreError):
                raise RGFocusFallbackEligibleError(
                    f"Fast OFM Core {exc.code}: {exc}",
                    result_id=str(report["id"]),
                    report_ref=f"rg_focus/{directory.name}/report.json",
                ) from exc
            raise
