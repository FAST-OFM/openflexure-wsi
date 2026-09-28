"""Scan workflows set different ways that smart scan can behave.

This module contains the base ``ScanWorkflow`` class that all workflows should subclass,
as well as specific workflows.
"""

import json
import math
import time
from copy import deepcopy
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import (
    Any,
    Generic,
    Literal,
    Mapping,
    Optional,
    Protocol,
    TypeVar,
    cast,
)

import numpy as np
from pydantic import BaseModel, Field

import labthings_fastapi as lt

from openflexure_microscope_server.focus.focus_scan import (
    EvidenceWriter,
    FocusFieldRuntime,
    FocusScanSession,
    FocusScanSettings,
    FrozenRGFocusSettings,
)
from openflexure_microscope_server.focus.focus_surface import (
    FocusBinding,
    check_focus_binding,
)
from openflexure_microscope_server.focus.sparse_focus import (
    SparseFocusScheduler,
    SparseFocusSettings,
)
from openflexure_microscope_server.integrations.fast_ofm_core.client import (
    FastOFMCoreBlockingProcess,
    FastOFMCoreError,
)
from openflexure_microscope_server.integrations.fast_ofm_core.focus import (
    predict_sparse_focus,
)
from openflexure_microscope_server.scanning.scan_planners import (
    FrozenGridPlanner,
    RegularGridPlanner,
    ScanPlanner,
    SmartSpiral,
)
from openflexure_microscope_server.stitching.stitching import (
    TARGET_STITCHING_DIMENSION,
    StitchingSettings,
)
from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things.camera import BaseCamera, CaptureParams
from openflexure_microscope_server.things.focus.autofocus import (
    MAX_TEST_IMAGE_COUNT,
    MIN_TEST_IMAGE_COUNT,
    AutofocusParams,
    AutofocusThing,
    NoFocusFoundError,
    SmartStackParams,
    StackParams,
    resolve_focus_range,
    validate_stack_spacing,
)
from openflexure_microscope_server.things.focus.rg_focus import RGFocus
from openflexure_microscope_server.things.focus.rg_simultaneous import RGSimultaneous
from openflexure_microscope_server.things.scanning.background_detect import (
    ChannelDeviationLUV,
)
from openflexure_microscope_server.things.stage import BaseStage
from openflexure_microscope_server.things.stage.camera_stage_mapping import (
    CameraStageMapper,
)
from openflexure_microscope_server.things.stage.moonraker import (
    CompletedScanPreload,
    MoonrakerStage,
    PreparedScanPreload,
)
from openflexure_microscope_server.ui import (
    UI_ELEMENT_RESPONSE,
    Accordion,
    Container,
    HeaderBlock,
    HelpBlock,
    PropertyControl,
    UIElementList,
    action_button_for,
    property_control_for,
)

SettingModelType = TypeVar("SettingModelType", bound=BaseModel)
AutofocusMethod = Literal["none", "openflexure", "led", "simultaneous_rg"]


class FocusScanCapability(BaseModel):
    """Read-only next-scan readiness assembled from the accepted runtime owners."""

    supported: bool
    available: bool
    reasons: tuple[str, ...] = ()
    autofocus_method: AutofocusMethod | None = None
    focus_strategy: Literal["single_autofocus", "smart_stack"] | None = None
    fast_ofm_background_skipping: bool = False
    current_binding: FocusBinding | None = None
    saved_binding_current: bool | None = None
    saved_binding_reason: str | None = None
    saved_binding_mismatched_fields: tuple[str, ...] = ()
    on_focus_failure: Literal["pause"] = "pause"
    maximum_white_searches_per_field: Literal[1] = 1


class WorkflowStartError(lt.exceptions.InvocationError):
    """The scan workflow cannot start, as the requested configuration is invalid."""


def _compact_focus_outcome(result: dict[str, Any]) -> dict[str, Any]:
    """Copy scalar provenance, not RAW, patches, or a growing report, into a field."""
    keys = (
        "status",
        "focus_method",
        "id",
        "result_id",
        "report_ref",
        "simultaneous_result_id",
        "simultaneous_report_ref",
        "fallback_reason",
        "simultaneous_fallback_reason",
        "sparse_degraded",
        "correction_count",
        "correction_um",
        "inferred_defocus_um",
        "final_estimated_error_um",
    )
    outcome = {key: result[key] for key in keys if key in result}
    method = result.get("focus_method")
    mixed_count = result.get("mixed_capture_count")
    if mixed_count is not None and (type(mixed_count) is not int or mixed_count < 0):
        raise RuntimeError("Invalid RG capture count in focus result")
    outcome["mixed_capture_count"] = mixed_count
    outcome["rg_probe_attempted"] = (
        method == "rg_simultaneous" or result.get("simultaneous_attempted") is True
    )
    outcome["rg_attempted"] = (
        mixed_count > 0
        if mixed_count is not None
        else method == "rg_simultaneous" or result.get("rg_attempted") is True
    )
    outcome["fallback_count"] = (
        1
        if method in ("white_fallback", "white_precheck")
        else result.get("fallback_count", 0)
    )
    peripheral = result.get("peripheral_search")
    outcome["peripheral_search"] = (
        {
            key: deepcopy(peripheral[key])
            for key in (
                "attempted",
                "used",
                "status",
                "evaluated_patch_count",
                "peripheral_patch_count",
                "central_anchor_count",
                "stop_reason",
                "elapsed_s",
            )
            if key in peripheral
        }
        if isinstance(peripheral, dict)
        else None
    )
    return outcome


class ScanWorkflow(Generic[SettingModelType], lt.Thing):
    """A base class for all Scanworkflows.

    Scan workflows set the behaviour of a scan, including the background detection,
    scan planning, acquisition routine.
    """

    _class_settings = {"validate_properties_on_set": True}

    display_name: str = lt.property(default="Base Workflow", readonly=True)
    ui_blurb: str = lt.property(
        default="If you see this message, something is wrong.", readonly=True
    )

    _settings_model: type[SettingModelType]

    # All workflows must have a set class for scan planning
    _planner_cls: type[ScanPlanner]

    # All workflows set a save resolution
    capture_mode: str = lt.setting(default="standard")
    """A tuple of the image resolution to capture."""

    # CSM may not be set, and isn't required for a workflow. Allow for it to exist or be None
    _csm: Optional[CameraStageMapper] = lt.thing_slot()
    # Camera, stage and autofocus are all required by any scan workflow
    _cam: BaseCamera = lt.thing_slot()
    _stage: BaseStage = lt.thing_slot()
    _autofocus: AutofocusThing = lt.thing_slot()
    _rg_focus: Optional[RGFocus] = lt.thing_slot()
    _rg_simultaneous: Optional[RGSimultaneous] = lt.thing_slot()

    def __init__(
        self,
        thing_server_interface: lt.ThingServerInterface,
        default_settings: Optional[dict] = None,
    ) -> None:
        """Seed deployment defaults; saved operator settings are loaded afterwards.

        Values use the existing API's units. For the deployed Moonraker profile,
        1000 units/mm means one internal unit is one micrometre, not one motor step.
        """
        super().__init__(thing_server_interface)
        for name, value in (default_settings or {}).items():
            if name not in self.settings:
                raise ValueError(f"Unknown workflow default: {name}")
            setattr(self, name, value)

    def _um_per_unit(self, axis: str) -> float:
        """Convert the stage's logical units, independently for each physical axis."""
        if not isinstance(self._stage, MoonrakerStage):
            raise ValueError("This stage has no configured physical distance scale")
        axes = self._stage.hardware_settings["axes"]
        return 1000.0 / axes[axis]["units_per_mm"]

    def _distance_control(
        self, name: str, label: str, physical_step: float, native_step: float = 1
    ) -> PropertyControl:
        """Use physical views of the same persisted settings on Moonraker only."""
        physical = isinstance(self._stage, MoonrakerStage)
        return property_control_for(
            self,
            name + "_um" if physical else name,
            label=label + (" (µm)" if physical else " (stage units)"),
            step=physical_step if physical else native_step,
            read_back=physical,
            read_back_delay=0,
        )

    def _distance_to_units(self, distance: float, axis: str) -> int:
        """Round a positive physical request to the stage's integer resolution."""
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError("Distance must be finite and positive")
        return max(1, round(distance / self._um_per_unit(axis)))

    # The noqa statement is because scan_name is unused but is needed for equivalence
    # with other workflows that may want to validate the scan name.
    def check_before_start(self, scan_name: str) -> None:  # noqa: ARG002
        """Check before the scan starts. Throw an error if the scan shouldn't start.

        The scan_name is passed to this function to enable workflows to validate the
        scan name if needed.
        """
        if self.capture_mode not in self._cam.capture_modes:
            cam_name = type(self._cam).__name__
            raise WorkflowStartError(
                f"{cam_name} has no capure mode {self.capture_mode}"
            )

    @lt.property
    def ready(self) -> bool:
        """Whether this scanworkflow is ready to start."""
        raise NotImplementedError(
            "Each specific ScanWorkflow must implement a ready property."
        )

    def all_settings(
        self, images_dir: RelativeDataPath
    ) -> tuple[SettingModelType, Optional[StitchingSettings], tuple[int, int]]:
        """Return the scan settings and the stitching settings.

        - The specific settings for this scan workflow are returned as a Base Model of
            the type set when defining the class.
        - Stitiching settings are returned either as a StitchingSettings object or None
            is returned if it is not possible to stitch the scan.
        - The save resolution as determined by a test image.
        """
        raise NotImplementedError(
            "Each specific ScanWorkflow must implement a `all_settings` method."
        )

    def _get_save_resolution(self) -> tuple[int, int]:
        """Return the final on-disk resolution for the selected capture mode."""
        mode = self._cam.capture_modes[self.capture_mode]
        configured_resolution = getattr(mode, "save_resolution", None)
        if configured_resolution is not None:
            return configured_resolution
        # Capture an example image.
        image = self._cam._capture_image(capture_mode=self.capture_mode)
        # Modes without a configured resize are saved at their capture size.
        return image.size

    def pre_scan_routine(self, settings: SettingModelType) -> None:
        """Overload to set the routine that happens before each scan."""
        raise NotImplementedError(
            "Each specific ScanWorkflow must implement a pre-scan routine."
        )

    def new_scan_planner(
        self, settings: SettingModelType, position: Mapping[str, int]
    ) -> ScanPlanner:
        """Return the a new scan planner object for a scan."""
        raise NotImplementedError(
            "Each specific ScanWorkflow must implement a ``new_scan_planner`` method."
        )

    def acquisition_routine(
        self,
        settings: SettingModelType,
        xyz_pos: tuple[int, int, int],
        focus_field: FocusFieldRuntime | None = None,
        scan_preload: PreparedScanPreload | CompletedScanPreload | None = None,
    ) -> tuple[bool, Optional[int], int]:
        """Overload to set the acquisition routine that happens at each scan site.

        :param settings: The settings for this scan, which should be a SettingModelType
        :param xyz_pos: The current position as a tuple or 3 ints.
        :return: A tuple of whether an image was taken, the z-position for focus, and
            the number of images captured at this site.
            If failed to find focus, returns for the focus z-position.
        """
        raise NotImplementedError(
            "Each specific ScanWorkflow must implement an acquisition routine"
        )

    def prepare_scan_preload(
        self, settings: SettingModelType, target_xyz: tuple[int, int, int]
    ) -> PreparedScanPreload | CompletedScanPreload | None:
        """Combine a simultaneous-R/G Z preload with the normal XY transit."""
        if (
            getattr(settings, "focus_strategy", None) != "single_autofocus"
            or getattr(settings, "autofocus_method", None) != "simultaneous_rg"
        ):
            return None
        if self._rg_simultaneous is None or not isinstance(self._stage, MoonrakerStage):
            raise WorkflowStartError(
                "Simultaneous R/G scan preload requires the Moonraker stage"
            )
        preload, approach_sign = self._rg_simultaneous.scan_preload_parameters()
        return self._stage.move_to_scan_preload(target_xyz, preload, approach_sign)

    def prepare_scan_target_z(
        self,
        settings: SettingModelType,
        target_xy: tuple[int, int],
        route_z_estimate: int | None,
        current_z: int,
    ) -> int | None:
        """Allow a workflow to replace the route planner's Z before XY transit."""
        del settings, target_xy, current_z
        return route_z_estimate

    def preferred_camera_mode(self, settings: SettingModelType) -> str | None:
        """Return an optional per-field streaming mode selected from frozen settings."""
        del settings
        return None

    def complete_scan_preload(
        self, prepared: PreparedScanPreload | CompletedScanPreload | None
    ) -> CompletedScanPreload | None:
        """Complete the final Z approach before imaging at a prepared scan field."""
        if prepared is None:
            return None
        if not isinstance(self._stage, MoonrakerStage):
            raise WorkflowStartError("Prepared scan preload requires Moonraker stage")
        if isinstance(prepared, CompletedScanPreload):
            return self._stage.consume_completed_scan_preload(prepared)
        return self._stage.complete_scan_preload(prepared)

    def _autofocus_and_capture(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        xyz_pos: tuple[int, int, int],
        dz: int,
        images_dir: RelativeDataPath,
        capture_mode: str,
        autofocus_method: AutofocusMethod,
        *,
        focus_field: FocusFieldRuntime | None = None,
        led_precheck: dict[str, object] | None = None,
        scan_preload: CompletedScanPreload | None = None,
        focus_outcome: dict[str, Any] | None = None,
        white_fallback_reason: str | None = None,
    ) -> tuple[bool, Optional[int], int]:
        """Autofocus and then capture, this can be used as an acquisition routine.

        :param dz: The dz for autofocus.
        :param images_dir: The path to the directory for saving images.
        :param capture_mode: The name of the camera capture mode.

        :return: A tuple ready to pass out of acquisition routine. In this method,
            image is always taken, so first return is True, and the last is 1.

        """
        pending_rg_result = None
        if autofocus_method == "openflexure":
            self._autofocus.fast_autofocus(dz=dz)
        elif autofocus_method in ("led", "simultaneous_rg"):
            self._require_camera_mode("default")
            if self._rg_focus is None:
                raise WorkflowStartError("LED autofocus component is unavailable")
            if white_fallback_reason is not None:
                if autofocus_method != "simultaneous_rg" or focus_field is not None:
                    raise WorkflowStartError("Invalid sparse WHITE fallback context")
                pending_rg_result = (
                    self._rg_focus.autofocus_white_sparse_fallback_for_scan(
                        white_dz=dz, reason=white_fallback_reason
                    )
                )
            elif led_precheck is not None:
                if focus_field is not None:
                    raise WorkflowStartError(
                        "Weak-tissue WHITE routing is incompatible with focus surface"
                    )
                pending_rg_result = self._rg_focus.autofocus_white_precheck_for_scan(
                    white_dz=dz,
                    precheck=led_precheck,
                )
            elif autofocus_method == "simultaneous_rg":
                if focus_field is not None:
                    raise WorkflowStartError(
                        "Simultaneous R/G does not support focus-surface mode"
                    )
                if self._rg_simultaneous is None:
                    raise WorkflowStartError(
                        "Simultaneous R/G autofocus component is unavailable"
                    )
                pending_rg_result = self._rg_simultaneous.autofocus_for_scan(
                    white_dz=dz,
                    scan_preload=scan_preload,
                )
            else:
                pending_rg_result = (
                    self._rg_focus.autofocus_with_white_fallback_for_scan(white_dz=dz)
                    if focus_field is None
                    else self._rg_focus.autofocus_for_scan(focus_field)
                )
            if focus_outcome is not None:
                focus_outcome.update(_compact_focus_outcome(pending_rg_result))
            if pending_rg_result.get("status") != "focused":
                raise NoFocusFoundError(
                    "Selected autofocus method did not return a focused result"
                )
            if focus_field is not None:
                # Close the focus-specific R/G deadline here.  The distinct saved
                # tile is still bounded by the overall field deadline, and this
                # validation does not publish an observation to the surface.
                focus_field.validate_rg_result(pending_rg_result)
        elif autofocus_method != "none":
            raise WorkflowStartError("Unknown autofocus method in frozen scan settings")
        result_position = (
            None
            if pending_rg_result is None
            else pending_rg_result.get("final_position_units")
        )
        focus_height = (
            result_position[2]
            if isinstance(result_position, list)
            and len(result_position) == 3
            and type(result_position[2]) is int
            else self._stage.get_xyz_position()[2]
        )
        filename = f"img_{xyz_pos[0]}_{xyz_pos[1]}_{focus_height}.jpeg"
        if focus_outcome is not None:
            focus_outcome.update(
                focus_height_units=focus_height,
                final_position_units=[xyz_pos[0], xyz_pos[1], focus_height],
                image_file=filename,
            )
        if focus_field is not None:
            focus_field.before_main_capture()
        try:
            self._cam.capture_and_save_to_path(
                path=images_dir.join(filename),
                capture_mode=capture_mode,
            )
            if autofocus_method in ("led", "simultaneous_rg"):
                self._require_camera_mode("default")
            if focus_field is not None:
                focus_field.sync_after_effect("white_tile_capture")
            if focus_field is not None and autofocus_method == "led":
                if pending_rg_result is None:
                    raise RuntimeError("LED focus result is unavailable after capture")
                focus_field.accept_rg_result()
        except BaseException as exc:
            if focus_field is not None:
                focus_field.session.stop(focus_field, exc)
            raise

        return True, focus_height, 1

    def _require_camera_mode(self, mode: str) -> None:
        """Require a known mode after a temporary scan capture transition."""
        if not self._cam.stream_active or self._cam.streaming_mode != mode:
            raise WorkflowStartError(
                f"Camera streaming mode {mode} was not restored; "
                "camera state is unknown"
            )

    def _current_focus_binding(
        self, frozen: FrozenRGFocusSettings | None = None
    ) -> FocusBinding:
        """Return the live cross-owner identity used by an enabled focus scan."""
        if self._rg_focus is None or self._csm is None:
            raise WorkflowStartError(
                "Focus surface requires R/G focus and camera mapping"
            )
        return self._rg_focus.focus_surface_binding(
            self._csm.last_calibration, frozen=frozen
        )

    def _focus_capability_binding(
        self, supported: bool
    ) -> tuple[FocusBinding | None, str | None]:
        """Read the binding only when all of its read-only owners are present."""
        if (
            not supported
            or not isinstance(self._stage, MoonrakerStage)
            or self._rg_focus is None
            or self._csm is None
        ):
            return None, None
        try:
            return self._current_focus_binding(), None
        except Exception as exc:
            return None, f"Current focus binding is unavailable: {exc}"

    @staticmethod
    def _saved_focus_binding_status(
        saved: FocusScanSettings | None, binding: FocusBinding | None
    ) -> tuple[bool | None, str | None, tuple[str, ...]]:
        """Compare an enabled saved setup without mutating or refreshing it."""
        if saved is None or not saved.run.surface.enabled or saved.run.binding is None:
            return None, None, ()
        if binding is None:
            return (
                False,
                "Saved focus binding cannot be checked against current owners",
                (),
            )
        check = check_focus_binding(saved.run.binding, binding)
        return (
            check.compatible and check.usable,
            check.reason,
            check.mismatched_fields,
        )

    @lt.endpoint("get", "focus_scan_capability")
    def focus_scan_capability(self) -> FocusScanCapability:
        """Report exact enablement gates without capturing, moving or persisting."""
        supported = isinstance(self, SmartStackMixin)
        mixin = cast("SmartStackMixin", self)
        method = cast(AutofocusMethod, mixin.autofocus_method) if supported else None
        strategy = (
            cast(
                Literal["single_autofocus", "smart_stack"],
                mixin.focus_strategy,
            )
            if supported
            else None
        )
        fast_ofm_background = isinstance(self, FastOFMWorkflow) and self.skip_background
        checks = (
            (not supported, "This workflow does not support focus prediction"),
            (supported and method != "led", "Autofocus method must be Tissue R/G LED"),
            (
                supported and strategy != "single_autofocus",
                "Focus strategy must be single autofocus",
            ),
            (
                fast_ofm_background,
                "Fast OFM background skipping is not proven near predicted focus",
            ),
            (
                supported and not isinstance(self._stage, MoonrakerStage),
                "Focus prediction requires the Moonraker stage",
            ),
            (
                supported and self._rg_focus is None,
                "R/G focus component is unavailable",
            ),
            (
                supported and self._csm is None,
                "Camera-stage mapping component is unavailable",
            ),
        )
        reasons = [reason for unavailable, reason in checks if unavailable]
        binding, binding_reason = self._focus_capability_binding(supported)
        if binding_reason is not None:
            reasons.append(binding_reason)
        saved = cast(FocusScanSettings, mixin.focus_scan) if supported else None
        saved_current, saved_reason, saved_mismatches = (
            self._saved_focus_binding_status(saved, binding)
        )

        return FocusScanCapability(
            supported=supported,
            available=supported and not reasons and binding is not None,
            reasons=tuple(reasons),
            autofocus_method=method,
            focus_strategy=strategy,
            fast_ofm_background_skipping=fast_ofm_background,
            current_binding=binding,
            saved_binding_current=saved_current,
            saved_binding_reason=saved_reason,
            saved_binding_mismatched_fields=saved_mismatches,
        )

    def validate_focus_scan(self, settings: SettingModelType) -> None:
        """Reject an unsupported active contract before the scan can move."""
        focus_scan = getattr(settings, "focus_scan", None)
        if (
            not isinstance(focus_scan, FocusScanSettings)
            or not focus_scan.run.surface.enabled
        ):
            return
        if not isinstance(self._stage, MoonrakerStage):
            raise WorkflowStartError("Focus surface requires the Moonraker stage")
        expected = focus_scan.run.binding
        if expected is None:
            raise WorkflowStartError("Enabled focus surface omitted its frozen binding")
        current = self._current_focus_binding()
        check = check_focus_binding(expected, current)
        if not check.compatible or not check.usable:
            raise WorkflowStartError(
                f"Focus surface binding is not current: {check.reason}"
            )
        if isinstance(settings, FastOFMSettingsModel) and settings.skip_background:
            raise WorkflowStartError(
                "Focus surface with Fast OFM background skipping is not yet proven near focus"
            )

    def new_focus_session(
        self,
        settings: SettingModelType,
        *,
        scan_id: str,
        evidence_writer: EvidenceWriter,
    ) -> FocusScanSession | None:
        """Create one optional scan-scoped runtime without owning the route loop."""
        focus_scan = getattr(settings, "focus_scan", None)
        if (
            not isinstance(focus_scan, FocusScanSettings)
            or not focus_scan.run.surface.enabled
        ):
            return None
        self.validate_focus_scan(settings)
        if self._rg_focus is None or not isinstance(self._stage, MoonrakerStage):
            raise WorkflowStartError("Enabled focus surface owners are unavailable")
        binding = self._current_focus_binding()
        frozen_rg = self._rg_focus.freeze_scan_focus(binding)
        return FocusScanSession(
            scan_id=scan_id,
            settings=focus_scan,
            stage=self._stage,
            rg_focus=self._rg_focus,
            autofocus=self._autofocus,
            frozen_rg=frozen_rg,
            current_binding=lambda: self._current_focus_binding(frozen_rg),
            surface_predictor=self._rg_focus.external_focus_prediction,
            evidence_writer=evidence_writer,
        )

    @lt.endpoint("get", "settings_ui", responses=UI_ELEMENT_RESPONSE)
    def settings_ui(self) -> UIElementList:
        """Return the UI for the workflow's settings in the scan tab."""
        raise NotImplementedError(
            "Each scan workflow must implement a settings_ui method."
        )


class RectGridSettingsModel(BaseModel):
    """Base setting model for all RectGrid workflows."""

    overlap: float
    dx: int
    dy: int
    capture_params: CaptureParams
    autofocus_params: AutofocusParams
    physical_geometry: Optional[dict] = None


RectGridSettingModelType = TypeVar(
    "RectGridSettingModelType", bound=RectGridSettingsModel
)


class RectGridWorkflow(
    ScanWorkflow[RectGridSettingModelType], Generic[RectGridSettingModelType]
):
    """A generic workflow for any scan that captures images on a rectilinear grid."""

    # Redefine _csm Thing Slot, as CSM is required for any RectGridWorkflow
    _csm: CameraStageMapper = lt.thing_slot()

    overlap: float = lt.setting(default=0.35, ge=0.1, le=0.7)
    """The fraction that adjacent images should overlap in x and y.

    This must be between 0.1 and 0.7.
    """

    autofocus_dz: int = lt.setting(default=1000, ge=1, le=3000)
    """The z distance to perform an autofocus in steps.

    These are logical stage units, not necessarily motor microsteps. Deployment
    defaults must match the configured stage scale. Hardware limits and the stage's
    actual backlash settings remain authoritative; there is no fixed 200-step offset.
    """

    @lt.property
    def autofocus_dz_um(self) -> float:
        """Total range of one autofocus sweep in micrometres, not +/- this value."""
        return self.autofocus_dz * self._um_per_unit("z")

    @autofocus_dz_um.setter
    def _set_autofocus_dz_um(self, value: float) -> None:
        units = self._distance_to_units(value, "z")
        resolve_focus_range(self._stage, units)
        self.autofocus_dz = units

    @lt.property
    def scan_geometry(self) -> dict:
        """Read-only physical dimensions from the stored calibration, without a capture.

        Image coordinates are [y, x] row vectors. Matrix columns are stage X/Y.
        Pixel scale always names its resolution: preview pixels are not saved pixels.
        Axis extents are bounding spans, not a promise of coverage for rotated cameras.
        """
        if not isinstance(self._stage, MoonrakerStage):
            return {"available": False, "reason": "Stage physical scale is unavailable"}
        matrix = self._csm.image_to_stage_displacement_matrix
        resolution = self._csm.image_resolution
        if matrix is None or resolution is None:
            return {"available": False, "reason": "Camera Stage Mapping is required"}
        m = np.asarray(matrix, dtype=float)
        res = np.asarray(resolution, dtype=float)
        if (
            m.shape != (2, 2)
            or res.shape != (2,)
            or not np.isfinite(m).all()
            or not np.isfinite(res).all()
            or (res <= 0).any()
            or abs(np.linalg.det(m)) < 1e-12
        ):
            return {"available": False, "reason": "Camera Stage Mapping is invalid"}
        scales = np.array([self._um_per_unit("x"), self._um_per_unit("y")])
        edges = res[:, None] * m * scales
        field_y, field_x = np.linalg.norm(edges, axis=1)
        span_x, span_y = np.abs(edges).sum(axis=0)
        dx, dy = self._calc_displacement_from_overlap(self.overlap)
        if isinstance(self, FastOFMWorkflow) and self.equal_distances:
            dx = dy = min(abs(dx), abs(dy))
        pitch_x, pitch_y = abs(dx) * scales[0], abs(dy) * scales[1]
        if min(pitch_x, pitch_y) <= 0:
            return {"available": False, "reason": "Tile displacement rounds to zero"}
        result = {
            "available": True,
            "field_width_um": float(field_x),
            "field_height_um": float(field_y),
            "field_x_span_um": float(span_x),
            "field_y_span_um": float(span_y),
            "tile_pitch_x_um": float(pitch_x),
            "tile_pitch_y_um": float(pitch_y),
            "calibration_resolution_px": res.tolist(),
            "calibration_matrix": m.tolist(),
            "overlap_percent": self.overlap * 100,
            "units_per_mm": {
                axis: 1000 / self._um_per_unit(axis) for axis in ("x", "y", "z")
            },
        }
        mode = self._cam.capture_modes.get(self.capture_mode)
        save_res = getattr(mode, "save_resolution", None)
        if save_res is not None:
            result["save_resolution_px"] = list(save_res)
            result["saved_pixel_width_um"] = float(field_x / save_res[0])
            result["saved_pixel_height_um"] = float(field_y / save_res[1])
        if isinstance(self, RegularGridWorkflow):
            result.update(
                columns=self.x_count,
                rows=self.y_count,
                coverage_x_um=float(span_x + (self.x_count - 1) * pitch_x),
                coverage_y_um=float(span_y + (self.y_count - 1) * pitch_y),
            )
        if isinstance(self, FastOFMWorkflow):
            if self.scan_pattern == "rectangle_snake":
                columns, rows = self.rectangle_columns, self.rectangle_rows
                result.update(
                    columns=columns,
                    rows=rows,
                    coverage_x_um=float(span_x + (columns - 1) * pitch_x),
                    coverage_y_um=float(span_y + (rows - 1) * pitch_y),
                )
            else:
                result["centre_limit_x_um"] = self.max_range * scales[0]
                result["centre_limit_y_um"] = self.max_range * scales[1]
        return result

    def _geometry_ui(self) -> list[HelpBlock]:
        """Show scale and actual rounded grid extent in the existing setup panel."""
        if not isinstance(self._stage, MoonrakerStage):
            return []
        g = self.scan_geometry
        if not g["available"]:
            return [
                HelpBlock(
                    label="Current geometry",
                    text=g["reason"] + "; physical coverage is unavailable.",
                )
            ]
        text = (
            f"Calibrated field: {g['field_width_um']:.1f} × "
            f"{g['field_height_um']:.1f} µm. "
            f"Tile pitch X / Y: {g['tile_pitch_x_um']:.1f} / "
            f"{g['tile_pitch_y_um']:.1f} µm."
        )
        if "save_resolution_px" in g:
            w, h = g["save_resolution_px"]
            text += (
                f" Saved image: {w} × {h} px "
                f"({g['saved_pixel_width_um']:.3f} × "
                f"{g['saved_pixel_height_um']:.3f} µm/px)."
            )
        if "columns" in g:
            text += (
                f" Grid: {g['columns']} × {g['rows']} fields; "
                f"actual X / Y span: {g['coverage_x_um']:.1f} × "
                f"{g['coverage_y_um']:.1f} µm. Sizes round up to whole fields."
            )
        if "centre_limit_x_um" in g:
            text += (
                f" Maximum centre offsets X / Y: ±{g['centre_limit_x_um']:.1f} / "
                f"±{g['centre_limit_y_um']:.1f} µm; frame edges extend beyond these."
            )
        text += (
            " Based on the saved mapping; recheck after changing optics or crop."
            " Z autofocus range is per sweep; automatic retries can shift its centre."
        )
        return [HelpBlock(label="Current geometry", text=text)]

    @lt.property
    def overlap_percent(self) -> float:
        """Adjacent-image overlap in percent; the persisted API setting stays a fraction."""
        return self.overlap * 100

    @overlap_percent.setter
    def _set_overlap_percent(self, value: float) -> None:
        self.overlap = value / 100

    def _overlap_control(self) -> PropertyControl:
        if isinstance(self._stage, MoonrakerStage):
            return property_control_for(
                self, "overlap_percent", label="Image overlap (%)", step=5
            )
        return property_control_for(
            self, "overlap", label="Image Overlap (0.1-0.7)", step=0.05
        )

    def check_before_start(self, scan_name: str) -> None:
        """Before starting a scan, check that camera-stage-mapping is set.

        Raise error if:
          - camera stage mapping is not set
        """
        super().check_before_start(scan_name)
        if isinstance(self, SmartStackMixin):
            self._check_autofocus_method(self.autofocus_method, self.focus_strategy)
            if self.autofocus_method == "openflexure":
                resolve_focus_range(self._stage, self.autofocus_dz)
                if self.focus_strategy == "smart_stack":
                    validate_stack_spacing(self._stage, self.stack_dz)
        else:
            resolve_focus_range(self._stage, self.autofocus_dz)
        if self._csm.calibration_required:
            raise WorkflowStartError("Camera Stage Mapping is not calibrated.")

    def _calc_displacement_from_overlap(self, overlap: float) -> tuple[int, int]:
        """Use camera stage mapping to calculate x and y displacement from given overlap.

        :param overlap: The desired overlap as a fraction of the image. i.e. 0.5 means
            that each image should overlap its nearest neighbour by 50%.

        :returns: (dx, dy) - the x and y displacements in steps

        :raises RuntimeError: If there is no camera stage mapper Thing available or if CMS isn't calibrated.
        """
        csm_image_res = self._csm.image_resolution
        if csm_image_res is None:
            raise RuntimeError("CSM not set. Scan shouldn't have progressed this far.")

        # Calculate displacements in image coordinates
        dx_img = csm_image_res[1] * (1 - overlap)
        dy_img = csm_image_res[0] * (1 - overlap)

        x_move_stage = self._csm.convert_image_to_stage_coordinates(x=dx_img, y=0)
        y_move_stage = self._csm.convert_image_to_stage_coordinates(x=0, y=dy_img)

        # Assume no rotation or skew and take only the aligned axis of vector.
        # Coerce to positive integer, but correct if x and y are flipped
        if abs(x_move_stage["x"]) > abs(x_move_stage["y"]):
            return x_move_stage["x"], y_move_stage["y"]
        # If not use the other stage axes. Note "dx" will be the movement in camera y.

        self.logger.info(
            f"Based on an overlap of {self.overlap}, the stage will make steps of "
            f"{y_move_stage['x']}, {x_move_stage['y']}"
        )
        return y_move_stage["x"], x_move_stage["y"]

    def log_movement_distances(self, scan_settings: RectGridSettingModelType) -> None:
        """Log the dx and dy movements for the started scan."""
        self.logger.info(
            f"Scanning with steps of dx={scan_settings.dx} and dy={scan_settings.dy}."
        )

    def _get_stitching_settings_model(
        self, save_resolution: tuple[int, int]
    ) -> StitchingSettings:
        """Return a stitching settings model based on current settings."""
        width, height = save_resolution
        # Target area in pixels
        target_area = TARGET_STITCHING_DIMENSION**2
        # Find N so that (width/N) * (height/N) ~ target_area
        # N^2 ~ (width * height) / target_area
        downsample_factor = max(1, round((width * height / target_area) ** 0.5))
        correlation_resize = 1 / downsample_factor

        return StitchingSettings(
            overlap=self.overlap,
            correlation_resize=correlation_resize,
        )

    def _build_scan_settings(self, base_kwargs: dict) -> RectGridSettingModelType:
        """Construct the _settings_model."""
        # Developer Note: This needs to be overridden if the settings model for this
        # class contains extra keys.
        return self._settings_model(**base_kwargs)

    def all_settings(
        self, images_dir: RelativeDataPath
    ) -> tuple[RectGridSettingModelType, Optional[StitchingSettings], tuple[int, int]]:
        """Return scan settings and the stitching settings.

        :param images_dir: The directory that images are to be written to.
        :return: A tuple containing the settings model for this workflow, the
            settings model for stitching, and the save resolution.
        """
        save_resolution = self._get_save_resolution()
        # Developer Note: When subclassing RectGridWorkflow rather than override
        # this method first consider overriding _build_scan_settings
        stitching_settings = self._get_stitching_settings_model(save_resolution)
        dx, dy = self._calc_displacement_from_overlap(self.overlap)

        base_kwargs = {
            "overlap": self.overlap,
            "dx": dx,
            "dy": dy,
            "capture_params": CaptureParams(
                images_dir=images_dir, capture_mode=self.capture_mode
            ),
            "autofocus_params": AutofocusParams(dz=self.autofocus_dz),
        }

        if isinstance(self._stage, MoonrakerStage):
            base_kwargs["physical_geometry"] = self.scan_geometry
        scan_settings = self._build_scan_settings(base_kwargs)

        # Log the dx, dy once scan_settings are finalised
        self.log_movement_distances(scan_settings)

        return scan_settings, stitching_settings, save_resolution

    @lt.property
    def ready(self) -> bool:
        """Whether this scanworkflow is ready to start."""
        return not self._csm.calibration_required


class SmartStackCompatibleSettings(Protocol):
    """A protocol for the minimum settings needed for smart stack to work."""

    capture_params: CaptureParams
    autofocus_params: AutofocusParams
    smart_stack_params: SmartStackParams
    focus_strategy: Literal["single_autofocus", "smart_stack"]
    autofocus_method: AutofocusMethod
    focus_scan: FocusScanSettings


class SmartStackMixin:
    """Selectable single-frame acquisition or optional smart Z stacking."""

    focus_strategy: Literal["single_autofocus", "smart_stack"] = lt.setting(
        default="smart_stack"
    )
    """How each sample field is focused and acquired; frozen at scan start."""

    autofocus_method: AutofocusMethod = lt.setting(default="openflexure")
    """Exact focus implementation frozen into each scan's saved settings."""

    focus_scan: FocusScanSettings = lt.setting(default_factory=FocusScanSettings)
    """Run-frozen surface/budget contract; disabled for all old saved settings."""

    stack_attempts: int = lt.setting(default=3, ge=1, le=3)
    """Maximum complete stack attempts per field."""

    stack_extra_images: int = lt.setting(default=15, ge=0, le=15)
    """Additional test planes allowed after the minimum stack."""

    stack_refocus_attempts: int = lt.setting(default=10, ge=1, le=10)
    """Maximum refocus sweeps before/between stack attempts."""

    stack_undershoot_images: int = lt.setting(default=5, ge=0, le=5)
    """Extra starting offset below the stack centre, in stack-plane spacings."""

    stack_images_to_save: int = lt.setting(default=1, ge=1, le=9)
    """The number of images to save in a stack.

    Defaults to 1 unless you need to see either side of focus
    """

    stack_min_images_to_test: int = lt.setting(
        default=9, ge=MIN_TEST_IMAGE_COUNT, le=MAX_TEST_IMAGE_COUNT
    )
    """The minimum number of images to capture in a stack.

    This many images are captured and tested for focus, if the focus is not central
    enough more images may be captured. After new images are captured, this value sets
    the number of images used for checking if focus is achieved.

    Defaults to 9 which balances reliability and speed.
    """

    stack_dz: int = lt.setting(default=50, ge=1, le=400)
    """Distance in steps between images in a z-stack.

    Suggested values:

    * 50 for 60-100x
    * 100 for 40x
    * 200 for 20x
    """

    @lt.property  # type: ignore[type-var]  # Mixin is attached only to a Thing.
    def stack_dz_um(self) -> float:
        """Spacing between stack planes in micrometres."""
        return self.stack_dz * self.as_workflow._um_per_unit("z")

    @stack_dz_um.setter
    def _set_stack_dz_um(self, value: float) -> None:
        units = self.as_workflow._distance_to_units(value, "z")
        validate_stack_spacing(self.as_workflow._stage, units)
        self.stack_dz = units

    @property
    def as_workflow(self) -> ScanWorkflow:
        """Return self as a ScanWorkflow.

        Ensures this mixin is only used with ScanWorkflow instances,
        raising TypeError otherwise.
        """
        if not isinstance(self, ScanWorkflow):
            raise TypeError("SmartStackMixin must be mixed into a ScanWorkflow")
        return self

    def create_smart_stack_params(self, save_on_failure: bool) -> SmartStackParams:
        """Set up the parameters used for all smart stacks in a scan.

        :returns: A StackSmartParams object with the required parameters.
        """
        # Coerce min_images_to_test parameter
        min_images_to_test = self.stack_min_images_to_test
        if min_images_to_test % 2 == 0:
            min_images_to_test += 1
            self.as_workflow.logger.warning(
                "Minimum number of images to test should be odd, setting to "
                f"{min_images_to_test}."
            )
        # Set the Thing property to the coerced value
        self.stack_min_images_to_test = min_images_to_test

        # Coerce the images to save parameter to be odd, and less than
        # min_images_to_save
        images_to_save = self.stack_images_to_save
        if images_to_save > min_images_to_test:
            self.as_workflow.logger.warning(
                f"Cannot save {images_to_save} images as this above the minimum "
                f"number to test. Setting images to save to {MAX_TEST_IMAGE_COUNT}."
            )
            images_to_save = min_images_to_test
        elif images_to_save % 2 == 0:
            images_to_save += 1
            self.as_workflow.logger.warning(
                f"Images to save should be odd, setting to {images_to_save}."
            )
        # Set the Thing property to the coerced value
        self.stack_images_to_save = images_to_save

        return SmartStackParams(
            stack_dz=self.stack_dz,
            images_to_save=self.stack_images_to_save,
            min_images_to_test=self.stack_min_images_to_test,
            save_on_failure=save_on_failure,
            max_attempts=self.stack_attempts,
            extra_images=self.stack_extra_images,
            refocus_max_attempts=self.stack_refocus_attempts,
            img_undershoot=self.stack_undershoot_images,
        )

    def _pre_scan_focus(self, settings: SmartStackCompatibleSettings) -> None:
        """No duplicate first-field AF in single mode; bounded retries in stack mode."""
        if settings.focus_strategy == "smart_stack":
            self._check_autofocus_method(
                settings.autofocus_method, settings.focus_strategy
            )
            self.as_workflow._autofocus.looping_autofocus(
                dz=settings.autofocus_params.dz,
                start="centre",
                max_attempts=settings.smart_stack_params.refocus_max_attempts,
            )

    def _acquire_with_focus(
        self,
        settings: SmartStackCompatibleSettings,
        xyz_pos: tuple[int, int, int],
        focus_field: FocusFieldRuntime | None = None,
        *,
        led_precheck: dict[str, object] | None = None,
        scan_preload: CompletedScanPreload | None = None,
        focus_outcome: dict[str, Any] | None = None,
        white_fallback_reason: str | None = None,
    ) -> tuple[bool, Optional[int], int]:
        """Dispatch using the immutable settings for this run, not the live form."""
        if focus_field is not None and focus_field.no_tissue:
            policy = settings.focus_scan.run.no_tissue_mode
            if policy == "pause":
                raise NoFocusFoundError(
                    "Fresh WHITE tissue check is uncertain or empty"
                )
            if policy == "skip_tile":
                return False, None, 0
            focus_field.before_main_capture()
            self.as_workflow._cam.capture_and_save_to_path(
                path=settings.capture_params.images_dir.join(
                    f"img_{xyz_pos[0]}_{xyz_pos[1]}_{xyz_pos[2]}.jpeg"
                ),
                capture_mode=settings.capture_params.capture_mode,
            )
            focus_field.sync_after_effect("white_tile_capture")
            return True, None, 1
        if settings.focus_strategy == "single_autofocus":
            self.as_workflow.logger.info("Single autofocus and capture at %s", xyz_pos)
            return self.as_workflow._autofocus_and_capture(
                xyz_pos,
                settings.autofocus_params.dz,
                settings.capture_params.images_dir,
                settings.capture_params.capture_mode,
                settings.autofocus_method,
                focus_field=focus_field,
                led_precheck=led_precheck,
                scan_preload=scan_preload,
                focus_outcome=focus_outcome,
                white_fallback_reason=white_fallback_reason,
            )
        return self._perform_smart_stack(settings, xyz_pos)

    def _check_autofocus_method(
        self,
        method: AutofocusMethod,
        strategy: Literal["single_autofocus", "smart_stack"],
    ) -> None:
        """Reject unsupported/unavailable selection before a scan can move."""
        if method == "openflexure":
            return
        if strategy != "single_autofocus":
            raise WorkflowStartError(
                f"Autofocus method {method} supports only single-image acquisition"
            )
        if method == "none":
            return
        if method == "led":
            if self.as_workflow._rg_focus is None:
                raise WorkflowStartError("LED autofocus component is unavailable")
            try:
                self.as_workflow._rg_focus.checked_profile()
            except Exception as exc:
                raise WorkflowStartError(f"LED autofocus unavailable: {exc}") from exc
            return
        if method == "simultaneous_rg":
            if self.as_workflow._rg_simultaneous is None:
                raise WorkflowStartError(
                    "Simultaneous R/G autofocus component is unavailable"
                )
            try:
                self.as_workflow._rg_simultaneous.checked_focus_profile()
            except Exception as exc:
                raise WorkflowStartError(
                    f"Simultaneous R/G autofocus unavailable: {exc}"
                ) from exc
            return
        raise WorkflowStartError(f"Unknown autofocus method: {method}")

    def focus_property_controls(self) -> list[PropertyControl | HelpBlock]:
        """Keep stack-only settings out of single-frame acquisition."""
        controls: list[PropertyControl | HelpBlock] = [
            property_control_for(
                self.as_workflow,
                "autofocus_method",
                label="Focus method",
                options={
                    "Off": "none",
                    "Standard (WHITE)": "openflexure",
                    "R/G + fallback": "led",
                    "Fast R/G + fallback": "simultaneous_rg",
                },
                read_back=True,
                read_back_delay=0,
            ),
            property_control_for(
                self.as_workflow,
                "focus_strategy",
                label="Focus and capture",
                options={
                    "One focused image": "single_autofocus",
                    "Z stack": "smart_stack",
                },
                read_back=True,
                read_back_delay=0,
            ),
        ]
        if self.autofocus_method == "none":
            controls.append(
                HelpBlock(
                    label="Autofocus disabled",
                    text="No autofocus call will run during this scan.",
                )
            )
        elif self.autofocus_method == "led":
            status = (
                self.as_workflow._rg_focus.calibration_status
                if self.as_workflow._rg_focus is not None
                else {"status": "unavailable", "reason": "component is not configured"}
            )
            controls.append(
                HelpBlock(
                    label="R/G readiness",
                    text=f"LED profile: {status.get('status')}: {status.get('reason')}",
                )
            )
        elif self.autofocus_method == "simultaneous_rg":
            status = (
                self.as_workflow._rg_simultaneous.focus_status
                if self.as_workflow._rg_simultaneous is not None
                else {"status": "unavailable", "reason": "component is not configured"}
            )
            controls.append(
                HelpBlock(
                    label="Simultaneous R/G readiness",
                    text=(
                        f"One-frame profile: {status.get('status')}: "
                        f"{status.get('reason')}"
                    ),
                )
            )
        if self.focus_strategy == "single_autofocus":
            controls.append(
                HelpBlock(
                    label="Single-image focus",
                    text=(
                        "One autofocus per sample field, then one saved image. "
                        "No preliminary autofocus, Z stack or automatic refocus retries."
                    ),
                )
            )
        else:
            controls.extend(self.smart_stack_property_controls())
            controls.extend(
                property_control_for(self.as_workflow, name, label=label)
                for name, label in (
                    ("stack_extra_images", "Maximum extra test images"),
                    ("stack_attempts", "Maximum stack attempts"),
                    ("stack_refocus_attempts", "Maximum refocus sweeps"),
                    (
                        "stack_undershoot_images",
                        "Extra starting offset (stack intervals)",
                    ),
                )
            )
            controls.append(
                HelpBlock(
                    label="Smart stack images",
                    text=(
                        "Test images are used to select focus; only the requested images are saved. "
                        "A failed stack is never evidence that the field is empty."
                    ),
                )
            )
        return controls

    def _perform_smart_stack(
        self, settings: SmartStackCompatibleSettings, xyz_pos: tuple[int, int, int]
    ) -> tuple[bool, Optional[int], int]:
        """Perform acquisition a smart stack.

        :param settings: The settings for this scan as a FastOFMSettingsModel
        :param xyz_pos: The current position as a tuple or 3 ints.
        :return: A tuple of whether an image was taken, and the z-position for focus.
            If failed to find focus, returns for the focus z-position.
        """
        self._check_autofocus_method(settings.autofocus_method, settings.focus_strategy)
        focus_height: Optional[int]
        focused, focus_height, image_count = (
            self.as_workflow._autofocus.run_smart_stack(
                stack_parameters=settings.smart_stack_params,
                capture_parameters=settings.capture_params,
                autofocus_parameters=settings.autofocus_params,
            )
        )
        # An image was captured if we are focussed or we are not skipping background.
        imaged = focused or settings.smart_stack_params.save_on_failure

        if not imaged:
            raise NoFocusFoundError(
                f"Focus search failed at {xyz_pos}. No image saved. "
                "This is not a background classification; check focus or change the focus strategy."
            )

        # run_smart_stage always returns a focus height for the sharpest image even
        # if it failed to find a good focus. Set to None if not focussed.
        if not focused:
            focus_height = None

        return (
            imaged,
            focus_height,
            settings.smart_stack_params.images_to_save if imaged else 0,
        )

    def smart_stack_property_controls(self) -> list[PropertyControl]:
        """Return smart stack property controls for the UI."""
        return [
            property_control_for(
                self.as_workflow,
                "stack_images_to_save",
                label="Images in Stack to Save",
            ),
            property_control_for(
                self.as_workflow,
                "stack_min_images_to_test",
                label="Minimum number of images to test for focus",
            ),
            self.as_workflow._distance_control("stack_dz", "Z stack spacing", 1, 5),
        ]


class FastOFMSettingsModel(RectGridSettingsModel):
    """The settings for a scan with the FastOFMWorkflow.

    This includes settings calculated when starting. This will be held by smart scan
    during a scan and serialised to disk.
    """

    max_dist: int
    scan_pattern: Literal["tissue_spiral", "rectangle_snake"] = "tissue_spiral"
    rectangle_columns: int = Field(default=1, ge=1)
    rectangle_rows: int = Field(default=1, ge=1)
    skip_background: bool
    refine_background_boundaries: bool = False
    precheck_rg_tissue: bool = True
    smart_stack_params: SmartStackParams
    focus_strategy: Literal["single_autofocus", "smart_stack"] = "smart_stack"
    autofocus_method: AutofocusMethod = "openflexure"
    focus_scan: FocusScanSettings = Field(default_factory=FocusScanSettings)
    sparse_focus: SparseFocusSettings = Field(default_factory=SparseFocusSettings)


class FastOFMWorkflow(RectGridWorkflow[FastOFMSettingsModel], SmartStackMixin):
    """A workflow optimised for scanning Histopathology samples.

    This workflow can discover tissue from the centre or follow a fixed centred snake.
    """

    display_name: str = lt.property(default="Fast OFM Scan", readonly=True)
    ui_blurb: str = lt.property(
        default=(
            "This scan workflow is optimised for scanning H&E stained biopsies. "
            "Choose tissue discovery from the centre or a fixed rectangular snake."
        ),
        readonly=True,
    )

    _settings_model = FastOFMSettingsModel
    _planner_cls = SmartSpiral
    # Thing Slots
    _background_detector: ChannelDeviationLUV = lt.thing_slot()

    def _route_core_process(self) -> FastOFMCoreBlockingProcess:
        """Return one warm core process dedicated to hardware-free route planning."""
        existing = getattr(self, "_fast_ofm_route_core_process", None)
        if isinstance(existing, FastOFMCoreBlockingProcess):
            return existing
        process = FastOFMCoreBlockingProcess()
        object.__setattr__(self, "_fast_ofm_route_core_process", process)
        return process

    def _external_rectangle_route(
        self, settings: FastOFMSettingsModel, position: Mapping[str, int]
    ) -> tuple[tuple[int, int], ...]:
        """Request the exact signed grid from core and validate its stage-unit image."""
        x_scale = self._um_per_unit("x")
        y_scale = self._um_per_unit("y")
        payload = {
            "traversal": "exact_grid_serpentine",
            "grid": {
                "origin_um": {
                    "x_um": position["x"] * x_scale,
                    "y_um": position["y"] * y_scale,
                },
                "step_um": {
                    "x_um": settings.dx * x_scale,
                    "y_um": settings.dy * y_scale,
                },
                "columns": settings.rectangle_columns,
                "rows": settings.rectangle_rows,
            },
        }
        try:
            response = self._route_core_process().request(
                "planning.route", payload, timeout_s=5.0
            )
        except FastOFMCoreError as error:
            raise WorkflowStartError(
                f"Fast OFM Core could not plan the frozen scan route: {error}"
            ) from error
        result = response.get("result")
        actions = result.get("actions") if isinstance(result, dict) else None
        if not isinstance(actions, list):
            raise WorkflowStartError("Fast OFM Core returned no route actions")
        locations: list[tuple[int, int]] = []
        for action in actions:
            if not isinstance(action, dict) or action.get("kind") == "component_start":
                continue
            point = action.get("position_um")
            if not isinstance(point, dict):
                raise WorkflowStartError(
                    "Fast OFM Core returned an invalid route point"
                )
            try:
                locations.append(
                    (
                        round(float(point["x_um"]) / x_scale),
                        round(float(point["y_um"]) / y_scale),
                    )
                )
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise WorkflowStartError(
                    "Fast OFM Core returned a non-finite route point"
                ) from error
        expected = {
            (
                position["x"] + column * settings.dx,
                position["y"] + row * settings.dy,
            )
            for row in range(settings.rectangle_rows)
            for column in range(settings.rectangle_columns)
        }
        if (
            not locations
            or locations[0] != (position["x"], position["y"])
            or len(locations) != len(expected)
            or set(locations) != expected
        ):
            raise WorkflowStartError(
                "Fast OFM Core route does not match the frozen rectangular grid"
            )
        return tuple(locations)

    # Scan settings

    scan_pattern: Literal["tissue_spiral", "rectangle_snake"] = lt.setting(
        default="tissue_spiral"
    )
    """Use adaptive tissue discovery or a deterministic centred rectangular snake."""

    skip_background: bool = lt.setting(default=True)
    """Whether to detect and skip empty fields of view.

    This uses the settings from the ``BackgroundDetectThing``.
    """

    refine_background_boundaries: bool = lt.setting(default=False)
    """Probe half-grid positions between tissue and background fields."""

    precheck_rg_tissue: bool = lt.setting(default=True)
    """Route weak fresh WHITE fields to one standard focus before R/G blinks."""

    sparse_focus_enabled: bool = lt.setting(default=False)
    """Use measured R/G anchors and bounded surface predictions between them."""

    sparse_focus_anchor_interval: int = lt.setting(default=8, ge=1, le=64)
    """Number of sample fields between hardware autofocus anchors."""

    rectangle_columns: int = lt.setting(default=6, ge=1)
    """Number of columns in a known-area snake scan."""

    rectangle_rows: int = lt.setting(default=14, ge=1)
    """Number of rows in a known-area snake scan."""

    max_range: int = lt.setting(default=45000, ge=0)
    """The maximum distance in steps from the centre of the scan."""

    @lt.property
    def max_range_um(self) -> float:
        """Maximum permitted centre offset in either axis, in micrometres.

        The upstream planner uses one scalar in logical units. For unequal axis
        scales, convert conservatively so neither axis exceeds the physical request.
        """
        return self.max_range * max(self._um_per_unit(a) for a in ("x", "y"))

    @max_range_um.setter
    def _set_max_range_um(self, value: float) -> None:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Distance must be finite and positive")
        scale = max(self._um_per_unit(a) for a in ("x", "y"))
        if value < scale:
            raise ValueError("Distance is below the stage resolution")
        self.max_range = math.floor(value / scale)

    equal_distances: bool = lt.setting(default=False)
    """Make the distances in x and y equal in motor steps, rather than in overlap.

    Uses the shorter distance (usually dy) as both dx and dy"""

    @staticmethod
    def _rectangle_shape(max_dist: int, dx: int, dy: int) -> tuple[int, int]:
        """Return the odd grid shape whose centres stay inside the declared limit."""
        if max_dist < 0 or dx == 0 or dy == 0:
            raise ValueError("Rectangle requires finite non-zero grid spacing")
        return (
            2 * (max_dist // abs(dx)) + 1,
            2 * (max_dist // abs(dy)) + 1,
        )

    # The noqa statement is because scan_name is unused but is needed for equivalence
    # with other workflows that may want to validate the scan name.
    def check_before_start(self, scan_name: str) -> None:  # noqa: ARG002
        """Before starting a scan, check that background and CSM are set.

        Raise error if:
          - background is to be skipped but is not set
          - camera stage mapping is not set

        Raise warning if not using background detect that scan will go on until max
        steps reached.
        """
        super().check_before_start(scan_name)
        if self.sparse_focus_enabled and (
            self.autofocus_method != "simultaneous_rg"
            or self.focus_strategy != "single_autofocus"
            or not isinstance(self._stage, MoonrakerStage)
        ):
            raise WorkflowStartError(
                "Sparse focus requires simultaneous R/G single autofocus "
                "and the Moonraker stage"
            )
        if self._csm.calibration_required:
            raise WorkflowStartError("Camera Stage Mapping is not calibrated.")

        if self.skip_background:
            if not self._background_detector.ready:
                raise WorkflowStartError(
                    "Background is not set: you need to calibrate background detection."
                )
        elif self.scan_pattern == "tissue_spiral":
            self.logger.warning(
                "This scan will run in a spiral from the starting point "
                f"until it has moved by {self.max_range} steps in every direction."
            )

    @lt.property
    def ready(self) -> bool:
        """Whether this scanworkflow is ready to start."""
        if self._csm.calibration_required:
            return False
        if not self.skip_background:
            return True
        return self._background_detector.ready

    def _build_scan_settings(self, base_kwargs: dict) -> FastOFMSettingsModel:
        """Construct the SettingModel for all_settings.

        Adjust dx and dy to be equal if `equal_distances` is set.
        """
        # Make dx and dy equal if requested
        if self.equal_distances:
            dx = abs(base_kwargs.get("dx", 0))
            dy = abs(base_kwargs.get("dy", 0))
            min_displacement = min(dx, dy)
            base_kwargs["dx"] = min_displacement
            base_kwargs["dy"] = min_displacement

        if self.scan_pattern == "rectangle_snake":
            columns, rows = self.rectangle_columns, self.rectangle_rows
        else:
            columns, rows = self._rectangle_shape(
                self.max_range, base_kwargs["dx"], base_kwargs["dy"]
            )

        return FastOFMSettingsModel(
            **base_kwargs,
            max_dist=self.max_range,
            scan_pattern=self.scan_pattern,
            rectangle_columns=columns,
            rectangle_rows=rows,
            focus_strategy=self.focus_strategy,
            autofocus_method=self.autofocus_method,
            focus_scan=self.focus_scan.with_selection(
                self.autofocus_method, self.focus_strategy
            ),
            skip_background=self.skip_background,
            refine_background_boundaries=self.refine_background_boundaries,
            precheck_rg_tissue=self.precheck_rg_tissue,
            sparse_focus=SparseFocusSettings(
                enabled=self.sparse_focus_enabled,
                anchor_interval=self.sparse_focus_anchor_interval,
                maximum_recovery_anchors=3
                if self.scan_pattern == "rectangle_snake"
                else None,
            ),
            smart_stack_params=self.create_smart_stack_params(
                save_on_failure=not self.skip_background
            ),
        )

    def pre_scan_routine(self, settings: FastOFMSettingsModel) -> None:
        """Autofocus before starting the scan.

        :param settings: The settings for this scan as a FastOFMSettingsModel
        """
        self._sparse_focus_scheduler: SparseFocusScheduler | None = None
        self._sparse_scan_preload_parameters: tuple[int, int] | None = None
        self._focus_telemetry_path: Path | None = None
        self._focus_telemetry_counts: dict[str, int] = {}
        if (
            settings.autofocus_method == "simultaneous_rg"
            and settings.focus_strategy == "single_autofocus"
        ):
            self._start_focus_telemetry(settings.capture_params.images_dir)
        sparse_settings = getattr(settings, "sparse_focus", None)
        if isinstance(sparse_settings, SparseFocusSettings) and sparse_settings.enabled:
            if (
                settings.autofocus_method != "simultaneous_rg"
                or settings.focus_strategy != "single_autofocus"
                or not isinstance(self._stage, MoonrakerStage)
            ):
                raise WorkflowStartError(
                    "Sparse focus requires simultaneous R/G single autofocus "
                    "and the Moonraker stage"
                )
            core_process = self._route_core_process()
            try:
                capabilities = core_process.capabilities(timeout_s=5.0)
            except FastOFMCoreError as error:
                raise WorkflowStartError(
                    f"Fast OFM Core is required for sparse focus: {error}"
                ) from error
            operations = capabilities.get("operations")
            if (
                not isinstance(operations, list)
                or "focus.surface.predict" not in operations
            ):
                raise WorkflowStartError(
                    "Fast OFM Core does not advertise focus.surface.predict"
                )
            self._sparse_focus_scheduler = SparseFocusScheduler(
                sparse_settings,
                x_um_per_unit=self._um_per_unit("x"),
                y_um_per_unit=self._um_per_unit("y"),
                z_um_per_unit=self._um_per_unit("z"),
                predictor=partial(predict_sparse_focus, core_process),
            )
        self._pre_scan_focus(settings)

    def prepare_scan_target_z(
        self,
        settings: FastOFMSettingsModel,
        target_xy: tuple[int, int],
        route_z_estimate: int | None,
        current_z: int,
    ) -> int | None:
        """Prepare one sparse-focus decision before the combined XYZ transit."""
        del settings
        scheduler = getattr(self, "_sparse_focus_scheduler", None)
        if scheduler is None:
            return route_z_estimate
        if self._sparse_scan_preload_parameters is None:
            if self._rg_simultaneous is None:
                raise WorkflowStartError(
                    "Simultaneous R/G autofocus component is unavailable"
                )
            # Freeze this while the camera is still in the calibrated default RAW
            # geometry.  Predicted fields may subsequently hold full_resolution,
            # but their physical approach must remain the accepted scan-start one.
            self._sparse_scan_preload_parameters = (
                self._rg_simultaneous.scan_preload_parameters()
            )
        decision = scheduler.prepare(
            target_xy[0], target_xy[1], current_z, route_z_estimate
        )
        self.logger.info(
            "Sparse focus %s at (%s, %s): %s (%s support); reason=%s; "
            "target_z=%s; fit_residual_um=%s; validation_error_um=%s; "
            "completed anchors=%s predictions=%s background=%s white_fallback=%s",
            "WHITE fallback"
            if decision.white_only
            else "anchor"
            if decision.requires_anchor
            else "prediction",
            target_xy[0],
            target_xy[1],
            decision.estimate.source,
            decision.estimate.support_count,
            decision.estimate.reason,
            decision.target_z_units,
            decision.estimate.fit_residual_um,
            decision.estimate.validation_error_um,
            scheduler.anchor_fields,
            scheduler.predicted_fields,
            scheduler.background_fields,
            scheduler.white_fallback_fields,
        )
        return decision.target_z_units

    def preferred_camera_mode(self, settings: FastOFMSettingsModel) -> str | None:
        """Keep sparse predicted WHITE captures in the full-resolution stream.

        Simultaneous R/G anchors require the calibrated default RAW geometry.  A run
        of predicted fields only needs WHITE JPEG captures, so holding the camera in
        ``full_resolution`` avoids a full stop/configure/start/restore cycle for each
        tile.  SmartScan still restores ``default`` on every exit path.
        """
        scheduler = getattr(self, "_sparse_focus_scheduler", None)
        if scheduler is None or not settings.sparse_focus.enabled:
            return None
        pending = scheduler.pending
        if pending is None:
            raise RuntimeError("Sparse focus camera mode requested without a field")
        return "default" if pending.requires_anchor else "full_resolution"

    def prepare_scan_preload(
        self, settings: FastOFMSettingsModel, target_xyz: tuple[int, int, int]
    ) -> PreparedScanPreload | CompletedScanPreload | None:
        """Use the scan-frozen approach while sparse predictions hold camera mode."""
        if settings.sparse_focus.enabled:
            if not isinstance(self._stage, MoonrakerStage):
                raise WorkflowStartError(
                    "Simultaneous R/G scan preload requires the Moonraker stage"
                )
            parameters = getattr(self, "_sparse_scan_preload_parameters", None)
            if parameters is None:
                raise RuntimeError("Sparse scan preload was not frozen before movement")
            preload, approach_sign = parameters
            return self._stage.move_to_scan_target_with_preload(
                target_xyz, preload, approach_sign
            )
        return super().prepare_scan_preload(settings, target_xyz)

    def new_scan_planner(
        self, settings: FastOFMSettingsModel, position: Mapping[str, int]
    ) -> ScanPlanner:
        """Return a new scan planner object.

        :param settings: The settings for this scan as a FastOFMSettingsModel
        :param position: The starting position as a mapping of axes names to int.
        """
        if settings.scan_pattern == "rectangle_snake":
            locations = self._external_rectangle_route(settings, position)
            return FrozenGridPlanner(
                initial_position=(position["x"], position["y"]),
                planner_settings={
                    "dx": settings.dx,
                    "dy": settings.dy,
                    "locations": locations,
                },
            )
        planner_settings = {
            "dx": settings.dx,
            "dy": settings.dy,
            "max_dist": settings.max_dist,
            "refine_background_boundaries": settings.refine_background_boundaries,
        }
        return self._planner_cls(
            initial_position=(position["x"], position["y"]),
            planner_settings=planner_settings,
        )

    def _start_focus_telemetry(self, images_dir: RelativeDataPath) -> None:
        """Open a new scan-local append-only journal, never reuse another scan's file."""
        path = Path(images_dir.abs_data_path) / "focus-telemetry.jsonl"
        with path.open("x", encoding="utf-8"):
            pass
        self._focus_telemetry_path = path
        self._focus_telemetry_counts = {}
        self.logger.info("Per-field focus telemetry: %s", path)

    def _record_focus_field(self, record: dict[str, Any]) -> None:
        """Persist one terminal field and cumulative counts with bounded memory.

        A peripheral search is counted once per field, independently of whether
        it rescued RG. Patch counts are work, not additional searches or frames.
        Hardware failures lacking a capture report stay unknown, not invented zero.
        """
        path = getattr(self, "_focus_telemetry_path", None)
        if path is None:
            return
        counts = self._focus_telemetry_counts.copy()
        peripheral = record.get("peripheral_search") or {}
        attempted = record.get("rg_attempted")
        increments = {
            "fields": 1,
            "captured_fields": int(record.get("imaged") is True),
            "failed_fields": int(record.get("outcome") == "failed"),
            "background_fields": int(record.get("focus_method") == "background"),
            "prediction_fields": int(record.get("focus_method") == "prediction"),
            "rg_probe_fields": int(record.get("rg_probe_attempted") is True),
            "rg_attempt_fields": int(attempted is True),
            "rg_attempt_unknown_fields": int(attempted is None),
            "rg_mixed_captures": record.get("mixed_capture_count") or 0,
            "rg_focused_fields": int(
                record.get("focus_method") == "rg_simultaneous"
                and record.get("status") == "focused"
            ),
            "white_fallback_fields": int(record.get("fallback_count") == 1),
            "white_degraded_fields": int(record.get("sparse_degraded") is True),
            "peripheral_attempt_fields": int(peripheral.get("attempted") is True),
            "peripheral_used_fields": int(peripheral.get("used") is True),
            "surface_anchor_fields": int(record.get("surface_anchor_added") is True),
        }
        for name, value in increments.items():
            counts[name] = counts.get(name, 0) + value
        payload = {
            "schema_version": 1,
            "field_index": counts["fields"] - 1,
            **record,
            "cumulative": counts,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, allow_nan=False) + "\n")
        self._focus_telemetry_counts = counts
        self.logger.info("Focus field telemetry: %s", json.dumps(payload))

    def acquisition_routine(
        self,
        settings: FastOFMSettingsModel,
        xyz_pos: tuple[int, int, int],
        focus_field: FocusFieldRuntime | None = None,
        scan_preload: PreparedScanPreload | CompletedScanPreload | None = None,
    ) -> tuple[bool, Optional[int], int]:
        """Keep one field's provenance through capture and every terminal outcome."""
        started = time.monotonic()
        outcome: dict[str, Any] = {
            "requested_position_units": list(xyz_pos),
            "requested_focus_method": settings.autofocus_method,
            "surface_anchor_added": False,
            "rg_attempted": False,
            "rg_probe_attempted": False,
            "mixed_capture_count": 0,
        }
        scheduler = getattr(self, "_sparse_focus_scheduler", None)
        if scheduler is not None and scheduler.pending is not None:
            decision = asdict(scheduler.pending)
            decision["estimate"] = {
                key: None
                if isinstance(value, float) and not math.isfinite(value)
                else value
                for key, value in decision["estimate"].items()
            }
            outcome["sparse_decision"] = decision
        try:
            result = self._acquire_fast_ofm_field(
                settings, xyz_pos, focus_field, scan_preload, focus_outcome=outcome
            )
        except BaseException as exc:
            reported = getattr(exc, "scan_focus_outcome", None)
            if isinstance(reported, dict):
                outcome.update(_compact_focus_outcome(reported))
            outcome.update(
                outcome="failed",
                error_type=type(exc).__name__,
                error=str(exc),
                elapsed_s=time.monotonic() - started,
            )
            try:
                self._record_focus_field(outcome)
            except Exception:
                self.logger.exception(
                    "Focus telemetry write failed during field failure"
                )
            raise
        outcome.update(
            outcome="completed",
            imaged=result[0],
            image_count=result[2],
            elapsed_s=time.monotonic() - started,
        )
        self._record_focus_field(outcome)
        return result

    def _acquire_fast_ofm_field(  # noqa: C901, PLR0912, PLR0915
        self,
        settings: FastOFMSettingsModel,
        xyz_pos: tuple[int, int, int],
        focus_field: FocusFieldRuntime | None = None,
        scan_preload: PreparedScanPreload | CompletedScanPreload | None = None,
        *,
        focus_outcome: dict[str, Any],
    ) -> tuple[bool, Optional[int], int]:
        """Overload to set the acquisition routine that happens at each scan site.

        :param settings: The settings for this scan, which should be a SettingModelType
        :param xyz_pos: The current position as a tuple or 3 ints.
        :return: A tuple of whether an image was taken, the z-position for focus, and
            the number of images captured at this site.
            If failed to find focus, returns for the focus z-position.
        """
        phase_started = time.monotonic()
        completed_preload = self.complete_scan_preload(scan_preload)
        preload_elapsed = time.monotonic() - phase_started
        scheduler = getattr(self, "_sparse_focus_scheduler", None)
        sparse_anchor = scheduler is None
        if scheduler is not None:
            pending = scheduler.pending
            if pending is None or (pending.x_units, pending.y_units) != xyz_pos[:2]:
                raise RuntimeError(
                    "Sparse focus field does not match its prepared move"
                )
            sparse_anchor = pending.requires_anchor
        led_precheck: dict[str, object] | None = None
        background_started = time.monotonic()
        if settings.skip_background:
            # If skipping background, take an image to check if current field of view
            # is background
            image_array = self._cam.grab_as_array(stream_name="lores")
            capture_image, bg_message = self._background_detector.image_is_sample(
                image_array
            )

            if not capture_image:
                del image_array
                if scheduler is not None:
                    scheduler.skip_background()
                focus_outcome.update(
                    focus_method="background",
                    rg_attempted=False,
                    rg_probe_attempted=False,
                    mixed_capture_count=0,
                    fallback_count=0,
                )
                # Return early if the image is background.
                msg = f"Skipping {xyz_pos} as it is {bg_message}."
                self.logger.info(msg)
                return False, None, 0

            if (
                settings.precheck_rg_tissue
                and sparse_anchor
                and settings.autofocus_method == "led"
                and settings.focus_strategy == "single_autofocus"
            ):
                if self._rg_focus is None:
                    raise WorkflowStartError("LED autofocus component is unavailable")
                precheck = self._rg_focus.precheck_scan_white(image_array)
                if precheck.get("status") != "ready":
                    led_precheck = precheck
                    self.logger.info(
                        "Routing %s directly to WHITE focus after R/G tissue "
                        "precheck: %s: %s",
                        xyz_pos,
                        precheck.get("status"),
                        precheck.get("reason"),
                    )
                else:
                    self.logger.debug("R/G tissue precheck passed at %s", xyz_pos)
            del image_array
        background_elapsed = time.monotonic() - background_started

        if scheduler is not None and not sparse_anchor:
            focus_outcome.update(
                focus_method="prediction",
                rg_attempted=False,
                rg_probe_attempted=False,
                mixed_capture_count=0,
                fallback_count=0,
            )
            capture_started = time.monotonic()
            imaged, _focus_height, image_count = self._autofocus_and_capture(
                xyz_pos,
                settings.autofocus_params.dz,
                settings.capture_params.images_dir,
                settings.capture_params.capture_mode,
                "none",
                focus_outcome=focus_outcome,
            )
            capture_elapsed = time.monotonic() - capture_started
            scheduler.complete_prediction()
            self.logger.info(
                "Sparse prediction phases at (%s, %s): approach=%.3fs "
                "background=%.3fs capture=%.3fs",
                xyz_pos[0],
                xyz_pos[1],
                preload_elapsed,
                background_elapsed,
                capture_elapsed,
            )
            return imaged, None, image_count

        focus_capture_started = time.monotonic()
        pending = None if scheduler is None else scheduler.pending
        white_only = pending is not None and pending.white_only
        if white_only:
            focus_outcome.update(
                sparse_degraded=True,
                rg_attempted=False,
                rg_probe_attempted=False,
                mixed_capture_count=0,
            )
        elif settings.autofocus_method == "simultaneous_rg":
            focus_outcome["rg_probe_attempted"] = True
            focus_outcome["rg_attempted"] = None
            focus_outcome["mixed_capture_count"] = None
        result = self._acquire_with_focus(
            settings,
            xyz_pos,
            focus_field,
            led_precheck=led_precheck,
            scan_preload=completed_preload,
            focus_outcome=focus_outcome,
            white_fallback_reason=(
                f"Sparse model unavailable; bounded WHITE mode: {pending.estimate.reason}"
                if white_only and pending is not None
                else None
            ),
        )
        focus_capture_elapsed = time.monotonic() - focus_capture_started
        if scheduler is not None:
            focus_height = result[1]
            if focus_height is None:
                raise NoFocusFoundError("Sparse focus anchor returned no focus height")
            if (
                focus_outcome.get("focus_method") == "rg_simultaneous"
                and focus_outcome.get("fallback_count") == 0
            ):
                scheduler.complete_anchor(focus_height, focus_method="rg_simultaneous")
                focus_outcome["surface_anchor_added"] = True
            elif focus_outcome.get("focus_method") == "white_fallback":
                scheduler.complete_white_fallback(rg_attempted=not white_only)
            else:
                raise RuntimeError("Sparse focused result has unknown focus provenance")
            self.logger.info(
                "Sparse %s phases at (%s, %s): approach=%.3fs "
                "background=%.3fs focus_and_capture=%.3fs",
                "WHITE fallback"
                if focus_outcome.get("focus_method") == "white_fallback"
                else "RG anchor",
                xyz_pos[0],
                xyz_pos[1],
                preload_elapsed,
                background_elapsed,
                focus_capture_elapsed,
            )
        return result

    @lt.action
    def check_background(self) -> str:
        """Check if sample is background.

        This action is a pre-run check for feeding back to the user.
        """
        image_array = self._cam.grab_as_array(stream_name="lores")
        is_sample, bg_message = self._background_detector.image_is_sample(image_array)
        label = "sample" if is_sample else "background"

        return f"Current image is {label} ({bg_message})"

    @lt.action
    def set_background(self) -> None:
        """Set the background for this background detector.

        This sets the background for this workflow's background detector as opposed to
        the active background detector for the camera.
        """
        image_array = self._cam.grab_as_array(stream_name="lores")
        self._background_detector.set_background(image_array)

    @lt.endpoint("get", "settings_ui", responses=UI_ELEMENT_RESPONSE)
    def settings_ui(self) -> UIElementList:
        """Return the UI for the workflow's settings in the scan tab."""
        focus_controls = self.focus_property_controls()
        skip_background_control = property_control_for(
            self, "skip_background", label="Skip empty fields"
        )
        essential_settings = UIElementList(
            [
                *(
                    [
                        property_control_for(
                            self, "rectangle_columns", label="Columns"
                        ),
                        property_control_for(self, "rectangle_rows", label="Rows"),
                    ]
                    if self.scan_pattern == "rectangle_snake"
                    else [
                        self._distance_control(
                            "max_range", "Scan radius from start", 100, 1000
                        )
                    ]
                ),
                focus_controls[0],
                skip_background_control,
            ]
        )
        area_settings = UIElementList(
            [
                *self._geometry_ui(),
                self._overlap_control(),
                property_control_for(
                    self,
                    "scan_pattern",
                    label="Path",
                    options={
                        "Find tissue (spiral)": "tissue_spiral",
                        "Known area (snake)": "rectangle_snake",
                    },
                ),
                property_control_for(
                    self, "equal_distances", label="Equal X/Y spacing"
                ),
                property_control_for(
                    self, "rectangle_columns", label="Known-area columns"
                ),
                property_control_for(self, "rectangle_rows", label="Known-area rows"),
            ]
        )
        focus_settings = UIElementList(
            [
                *focus_controls[1:],
                property_control_for(
                    self,
                    "sparse_focus_enabled",
                    label="Use sparse R/G focus anchors",
                ),
                property_control_for(
                    self,
                    "sparse_focus_anchor_interval",
                    label="R/G anchor interval (sample fields)",
                ),
                self._distance_control("autofocus_dz", "Autofocus sweep range", 5, 200),
                property_control_for(
                    self,
                    "precheck_rg_tissue",
                    label="Use WHITE focus on weak R/G tissue",
                ),
            ]
        )
        background_ui = self._background_detector.settings_ui()
        set_bg_button = action_button_for(
            self,
            "set_background",
            poll_interval=0.1,
            submit_label="Set Background",
            can_terminate=False,
            notify_on_success=True,
            success_message="Background image has been updated",
            update_interface_on_response=True,
        )
        check_bg_button = action_button_for(
            self,
            "check_background",
            poll_interval=0.1,
            submit_label="Check Current Image",
            disabled=not self._background_detector.ready,
            can_terminate=False,
            notify_on_success=True,
            response_is_success_message=True,
        )

        background_ui.root = [
            property_control_for(
                self,
                "refine_background_boundaries",
                label="Refine tissue boundaries with half-steps",
            ),
            *background_ui.root,
            set_bg_button,
            check_bg_button,
        ]

        return UIElementList(
            [
                Container(
                    css_class="fast-ofm-wizard-essential",
                    children=essential_settings,
                ),
                Container(
                    css_class="fast-ofm-wizard-advanced",
                    children=UIElementList(
                        [
                            HelpBlock(label="About this workflow", text=self.ui_blurb),
                            Accordion(title="Area and path", children=area_settings),
                            Accordion(title="Focus details", children=focus_settings),
                            Accordion(
                                title="Background calibration", children=background_ui
                            ),
                        ]
                    ),
                ),
            ]
        )


class RegularGridSettingsModel(RectGridSettingsModel):
    """The settings for a scan with a regular grid of dx and dy for x_count, y_count steps.

    This includes settings calculated when starting. This will be held by smart scan
    during a scan and serialised to disk.
    """

    x_count: int
    y_count: int
    smart_stack_params: SmartStackParams
    style: Literal["snake", "raster"]
    focus_strategy: Literal["single_autofocus", "smart_stack"] = "smart_stack"
    autofocus_method: AutofocusMethod = "openflexure"
    focus_scan: FocusScanSettings = Field(default_factory=FocusScanSettings)


RegGridSettingModelType = TypeVar(
    "RegGridSettingModelType", bound=RegularGridSettingsModel
)


class RegularGridWorkflow(
    RectGridWorkflow[RegGridSettingModelType],
    SmartStackMixin,
    Generic[RegGridSettingModelType],
):
    """A base workflow for any workflow that uses a regular rectangular grid."""

    x_count: int = lt.setting(default=3, ge=1)
    """The number of columns in the scan."""
    y_count: int = lt.setting(default=2, ge=1)
    """The number of rows in the scan."""

    _settings_model: type[RegGridSettingModelType]
    _planner_cls = RegularGridPlanner
    _grid_style: Literal["snake", "raster"]

    def _coverage_um(self, axis: str) -> float:
        geometry = self.scan_geometry
        if not geometry["available"]:
            raise ValueError(geometry["reason"])
        return round(geometry[f"coverage_{axis}_um"], 1)

    def _set_coverage_um(self, value: float, axis: str) -> None:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Coverage must be finite and positive")
        geometry = self.scan_geometry
        if not geometry["available"]:
            raise ValueError(geometry["reason"])
        span = geometry[f"field_{axis}_span_um"]
        pitch = geometry[f"tile_pitch_{axis}_um"]
        # Half the displayed 0.1 µm precision keeps rounded readbacks idempotent.
        count = max(1, 1 + math.ceil((value - span - 0.05) / pitch))
        setattr(self, f"{axis}_count", count)

    @lt.property
    def scan_width_um(self) -> float:
        """Actual X bounding span of whole fields, including the first image."""
        return self._coverage_um("x")

    @scan_width_um.setter
    def _set_scan_width_um(self, value: float) -> None:
        self._set_coverage_um(value, "x")

    @lt.property
    def scan_height_um(self) -> float:
        """Actual Y bounding span of whole fields, including the first image."""
        return self._coverage_um("y")

    @scan_height_um.setter
    def _set_scan_height_um(self, value: float) -> None:
        self._set_coverage_um(value, "y")

    def _grid_controls(self) -> list[PropertyControl]:
        if isinstance(self._stage, MoonrakerStage) and self.scan_geometry["available"]:
            return [
                property_control_for(
                    self,
                    name,
                    label=label,
                    step=100,
                    read_back=True,
                    read_back_delay=0,
                )
                for name, label in (
                    ("scan_width_um", "Scan X span (µm)"),
                    ("scan_height_um", "Scan Y span (µm)"),
                )
            ]
        return [
            property_control_for(self, "x_count", label="Number of columns"),
            property_control_for(self, "y_count", label="Number of rows"),
        ]

    def _build_scan_settings(self, base_kwargs: dict) -> RegGridSettingModelType:
        """Construct the SettingModel for all_settings."""
        return self._settings_model(
            **base_kwargs,
            x_count=self.x_count,
            y_count=self.y_count,
            style=self._grid_style,
            focus_strategy=self.focus_strategy,
            autofocus_method=self.autofocus_method,
            focus_scan=self.focus_scan.with_selection(
                self.autofocus_method, self.focus_strategy
            ),
            smart_stack_params=self.create_smart_stack_params(save_on_failure=True),
        )

    def pre_scan_routine(self, settings: RegGridSettingModelType) -> None:
        """Perform these steps before starting the scan.

        In this case, only autofocus.

        :param settings: The settings for this scan as as the relevant SettingsModel type.
        """
        self._pre_scan_focus(settings)

    def new_scan_planner(
        self, settings: RegGridSettingModelType, position: Mapping[str, int]
    ) -> ScanPlanner:
        """Return a new scan planner object.

        :param settings: The settings for this scan as the relevant SettingsModel type.
        :param position: The starting position as a mapping of axes names to int.
        """
        planner_settings = {
            "dx": settings.dx,
            "dy": settings.dy,
            "x_count": settings.x_count,
            "y_count": settings.y_count,
            "style": settings.style,
        }
        return self._planner_cls(
            initial_position=(position["x"], position["y"]),
            planner_settings=planner_settings,
        )

    def acquisition_routine(
        self,
        settings: RegGridSettingModelType,
        xyz_pos: tuple[int, int, int],
        focus_field: FocusFieldRuntime | None = None,
        scan_preload: PreparedScanPreload | CompletedScanPreload | None = None,
    ) -> tuple[bool, Optional[int], int]:
        """Autofocus and capture.

        :param settings: The settings for this scan as the relevant SettingsModel type.
        :param xyz_pos: The current position as a tuple or 3 ints.
        :return: A tuple of whether an image was taken, the z-position for focus, and
            the number of images captured at this site.
            If failed to find focus, returns for the focus z-position.
        """
        completed_preload = self.complete_scan_preload(scan_preload)
        return self._acquire_with_focus(
            settings,
            xyz_pos,
            focus_field,
            scan_preload=completed_preload,
        )

    @lt.endpoint("get", "settings_ui", responses=UI_ELEMENT_RESPONSE)
    def settings_ui(self) -> UIElementList:
        """Return the UI for the workflow's settings in the scan tab."""
        area_settings = UIElementList(
            [
                *self._geometry_ui(),
                self._overlap_control(),
                *self._grid_controls(),
            ]
        )
        focus_settings = UIElementList(
            [
                *self.focus_property_controls(),
                self._distance_control("autofocus_dz", "Autofocus sweep range", 5),
            ]
        )
        return UIElementList(
            [
                HeaderBlock(text=self.display_name, level=4),
                HelpBlock(label="About this workflow", text=self.ui_blurb),
                Accordion(title="Area", children=area_settings),
                Accordion(title="Focus", children=focus_settings),
            ]
        )


class SnakeWorkflow(RegularGridWorkflow[RegularGridSettingsModel]):
    """A workflow optimised for snaking around samples.

    This workflow generates a list of coordinates in a rectangle, and snakes
    around them from the top left (assuming positive dx and dy).
    """

    display_name: str = lt.property(default="Snake Scan", readonly=True)
    ui_blurb: str = lt.property(
        default=(
            "This scan workflow is optimised for scanning over a rectangle. It "
            "snakes down and right from the starting point, over a defined grid."
        ),
        readonly=True,
    )
    _settings_model = RegularGridSettingsModel
    _grid_style = "snake"


class RasterWorkflow(RegularGridWorkflow[RegularGridSettingsModel]):
    """A workflow optimised for snaking around samples.

    This workflow generates a list of coordinates in a rectangle, and always
    moves right across a row, then moves down a row while moving to the starting
    column (assuming positive dx and dy).
    """

    display_name: str = lt.property(default="Raster Scan", readonly=True)
    ui_blurb: str = lt.property(
        default=(
            "This scan workflow is optimised for performing a raster scan over a rectangle. It "
            "always moves down and right from the starting point, over a defined grid."
        ),
        readonly=True,
    )
    _settings_model = RegularGridSettingsModel
    _grid_style = "raster"


class CChipScanSettingsModel(RectGridSettingsModel):
    """The settings for a scan with the CChipWorkflow.

    This includes settings calculated when starting. This will be held by smart scan
    during a scan and serialised to disk.
    """

    x_count: int
    y_count: int
    stack_params: StackParams
    style: Literal["snake", "raster"]


class CChipWorkflow(RectGridWorkflow[CChipScanSettingsModel]):
    """A workflow optimised for scanning the well of a CChip.

    This workflow generates a list of coordinates in a rectangle, and snakes
    around them from the top left (assuming positive dx and dy), stacking the
    grid and above.
    """

    display_name: str = lt.property(default="C-Chip Scan", readonly=True)
    ui_blurb: str = lt.property(
        default=(
            "This scan workflow is optimised for scanning a C-Chip. It focuses on "
            "a grid, then stacks images above the grid to complete a volumetric scan."
        ),
        readonly=True,
    )
    _grid_style: Literal["snake"] = "snake"
    _planner_cls = RegularGridPlanner
    _settings_model = CChipScanSettingsModel

    overlap: float = lt.setting(default=0.1, ge=0.1, le=0.7)
    """The fraction that adjacent images should overlap in x and y.

    This must be between 0.1 and 0.7.
    """
    x_count: int = lt.setting(default=5, readonly=False)
    """The number of columns in the scan."""
    y_count: int = lt.setting(default=7, readonly=False)
    """The number of rows in the scan."""

    stack_images_to_save: int = lt.setting(default=9, readonly=False)
    """The number of images to save in a stack.

    Defaults to 1 unless you need to see either side of focus
    """

    stack_dz: int = lt.setting(default=500, readonly=False)
    """Distance in steps between images in a z-stack."""

    def create_stack_params(
        self,
    ) -> StackParams:
        """Set up the parameters used for all stacks in a scan.

        :returns: A StackSmartParams object with the required parameters.
        """
        return StackParams(
            stack_dz=self.stack_dz, images_to_save=self.stack_images_to_save
        )

    def _build_scan_settings(self, base_kwargs: dict) -> CChipScanSettingsModel:
        """Construct the SettingModel for all_settings."""
        stack_params = self.create_stack_params()

        return self._settings_model(
            **base_kwargs,
            x_count=self.x_count,
            y_count=self.y_count,
            style=self._grid_style,
            stack_params=stack_params,
        )

    def new_scan_planner(
        self, settings: CChipScanSettingsModel, position: Mapping[str, int]
    ) -> ScanPlanner:
        """Return a new scan planner object.

        :param settings: The settings for this scan as the relevant SettingsModel type.
        :param position: The starting position as a mapping of axes names to int.
        """
        planner_settings = {
            "dx": settings.dx,
            "dy": settings.dy,
            "x_count": settings.x_count,
            "y_count": settings.y_count,
            "style": settings.style,
        }
        return self._planner_cls(
            initial_position=(position["x"], position["y"]),
            planner_settings=planner_settings,
        )

    def pre_scan_routine(self, settings: CChipScanSettingsModel) -> None:
        """No autofocus, a looping autofocus on a CChip could corrupt the entire scan."""
        pass

    def acquisition_routine(
        self,
        settings: CChipScanSettingsModel,
        xyz_pos: tuple[int, int, int],  # noqa: ARG002
        focus_field: FocusFieldRuntime | None = None,
        scan_preload: PreparedScanPreload | CompletedScanPreload | None = None,
    ) -> tuple[bool, Optional[int], int]:
        """Autofocus and capture a z-stack starting at the focused position.

        The routine performs a fast autofocus using the provided ``dz``.
        The focused z-height becomes the starting position for the stack
        and is also the height of the first captured image.

        A stack of ``self.stack_images_to_save`` images is then acquired.
        Each subsequent image is captured after moving the stage upward
        by ``self.stack_dz`` steps in z.

        :param xyz_pos: The (x, y, z) position associated with this acquisition.
        :param settings: The settings for this scan as a CChipSettingsModel

        :return: (True, focus_height, image_count) where focus_height is the
            autofocus z-position and the height of the first image in the stack,
            and image_count is the number of images captured at this site.
        """
        if focus_field is not None:
            raise WorkflowStartError("C-Chip workflow does not support focus surface")
        if scan_preload is not None:
            raise WorkflowStartError("C-Chip workflow does not support scan preload")
        # Perform autofocus
        self._autofocus.fast_autofocus(dz=settings.autofocus_params.dz)
        focus_height = self._stage.get_xyz_position()[2]
        self._autofocus.run_basic_stack(settings.stack_params, settings.capture_params)

        return True, focus_height, settings.stack_params.images_to_save

    @lt.property
    def settings_ui(self) -> list[PropertyControl]:
        """A list of PropertyControl objects to create the settings in the scan tab."""
        return [
            property_control_for(self, "overlap", label="Image Overlap (0.1-0.7)"),
            property_control_for(self, "x_count", label="Number of columns"),
            property_control_for(self, "y_count", label="Number of rows"),
            property_control_for(self, "autofocus_dz", label="Autofocus Range (steps)"),
            property_control_for(
                self, "stack_dz", label="Distance in z between images in stack (steps)"
            ),
            property_control_for(
                self, "stack_images_to_save", label="Images to save per xy site"
            ),
        ]
