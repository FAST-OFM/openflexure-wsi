"""Frozen scan owners and no-tile journal lifecycle, without physical hardware."""

import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock

import pytest

from labthings_fastapi.exceptions import InvocationCancelledError
from labthings_fastapi.testing import create_thing_without_server

import openflexure_microscope_server.focus.focus_scan as focus_runtime
from openflexure_microscope_server.scanning.scan_directories import (
    ScanDirectory,
    ScanGalleryDBEngine,
    ScanGalleryInfo,
)
from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things.focus.rg_focus import (
    CapturedRGFocusPair,
    RGFocus,
)
from openflexure_microscope_server.things.scanning.scan_workflows import (
    AutofocusParams,
    CaptureParams,
    RegularGridSettingsModel,
    SmartStackParams,
    SnakeWorkflow,
)
from openflexure_microscope_server.things.scanning.smart_scan import (
    ActiveScanData,
    SmartScanThing,
)
from tests.unit_tests.test_focus_approach import focus_stage as _focus_stage_fixture
from tests.unit_tests.test_focus_scan import enabled_settings, valid_profile
from tests.unit_tests.test_rg_focus_control import measurement
from tests.unit_tests.test_rg_focus_thing import commission_profile

from .focus_surface_process_stub import external_focus_prediction
from .rg_decision_process_stub import external_rg_decision
from .rg_model_process_stub import external_profile_validation


@pytest.fixture
def lifecycle_stage(mocker):
    """Use the existing real Moonraker adapter with only its HTTP controller faked."""
    yield from _focus_stage_fixture.__wrapped__(mocker)


def _workflow_owners(stage, mocker, tmp_path):
    """Connect native workflow/RG owners; hold camera/flat-field hardware fixed."""
    rg = create_thing_without_server(RGFocus, mock_all_slots=True)
    (tmp_path / "rg").mkdir()
    rg._data_dir = str(tmp_path / "rg")
    mocker.patch.object(RGFocus, "_stage", property(lambda _self: stage))
    profile = commission_profile(rg, valid_profile(), {"geometry_id": "geo-1"})
    rg.profile = profile.model_dump(mode="json")
    rg.parameters = profile.settings
    rg.measurement_parameters = profile.measurement_policy
    mocker.patch.object(rg, "_external_decision", side_effect=external_rg_decision)
    mocker.patch.object(
        rg,
        "_external_profile_validation",
        side_effect=external_profile_validation,
    )
    mocker.patch.object(
        rg, "external_focus_prediction", side_effect=external_focus_prediction
    )
    rg._rg_flat_field.calibration_status = {
        "red": {"status": "valid"},
        "green": {"status": "valid"},
    }
    rg._rg_flat_field.profiles = {
        "red": {"id": "red-flat"},
        "green": {"id": "green-flat"},
    }
    # This is the lowest optical hardware compatibility read, not checked_profile,
    # the workflow callback, focus_surface_binding or _frozen_scan_profile.
    mocker.patch(
        "openflexure_microscope_server.things.focus.rg_focus.focus_binding",
        return_value=profile.compatibility,
    )
    workflow = create_thing_without_server(SnakeWorkflow, mock_all_slots=True)
    mocker.patch.object(SnakeWorkflow, "_stage", property(lambda _self: stage))
    mocker.patch.object(SnakeWorkflow, "_rg_focus", property(lambda _self: rg))
    workflow._csm.last_calibration = {"matrix": [[1, 0], [0, 1]]}
    workflow._csm.calibration_required = False
    workflow._cam.capture_modes = ["standard"]
    workflow._cam.stream_active = True
    workflow._cam.streaming_mode = "default"

    def change_mode(*, mode):
        workflow._cam.streaming_mode = mode

    workflow._cam.change_streaming_mode.side_effect = change_mode
    workflow.capture_mode = "standard"
    workflow.autofocus_method = "led"
    workflow.focus_strategy = "single_autofocus"
    bound = workflow._current_focus_binding()
    settings = RegularGridSettingsModel(
        overlap=0.2,
        dx=10,
        dy=20,
        x_count=1,
        y_count=1,
        style="snake",
        capture_params=CaptureParams(
            images_dir=RelativeDataPath("images"), capture_mode="standard"
        ),
        autofocus_params=AutofocusParams(dz=20),
        smart_stack_params=SmartStackParams(
            stack_dz=1, images_to_save=1, min_images_to_test=3
        ),
        focus_strategy="single_autofocus",
        autofocus_method="led",
        focus_scan=enabled_settings(bound),
    )
    return workflow, rg, settings


def _pair(iteration, *, field_id, correcting=True):
    """Declare synthetic optics; the native decision/correction loop remains real."""
    return CapturedRGFocusPair(
        measurement=measurement(dx=6, dy=-0.8)
        if correcting and iteration == 1
        else measurement(),
        capture_id=f"{field_id}-pair-{iteration}",
        field=SimpleNamespace(),
        white_score=100,
        capture_path=f"/flat/{field_id}-pair-{iteration}",
        frame_ids=(
            f"{field_id}-{iteration}-white",
            f"{field_id}-{iteration}-red",
            f"{field_id}-{iteration}-green",
        ),
    )


def _focused_result():
    return {
        "id": "validated-rg-result",
        "status": "focused",
        "report_ref": "rg_focus/validated-rg-result/report.json",
        "total_correction_um": 0,
        "duration_s": 1,
        "iterations": [
            {
                "capture_id": "validated-pair",
                "frame_ids": ["validated-white", "validated-red", "validated-green"],
                "measurement": measurement().model_dump(mode="json"),
                "decision": {
                    "status": "focused",
                    "reason": "within tolerance",
                    "inferred_z_error_um": 0.0,
                    "correction_um": 0.0,
                },
            }
        ],
    }


@pytest.mark.parametrize("expire_overall", [False, True])
def test_rg_deadline_closes_before_slow_tile_but_overall_deadline_does_not(
    lifecycle_stage, mocker, tmp_path, expire_overall
):
    """Slow tile save cannot reopen R/G timing or bypass the whole-field limit."""
    stage, controller = lifecycle_stage
    clock = [time.monotonic()]
    mocker.patch.object(focus_runtime.time, "monotonic", side_effect=lambda: clock[0])
    workflow, rg, settings = _workflow_owners(stage, mocker, tmp_path)
    directory = ScanDirectory.new_scan_dir("deadline-split", str(tmp_path / "scans"))
    session = workflow.new_focus_session(
        settings, scan_id=directory.name, evidence_writer=directory.save_focus_evidence
    )
    assert session is not None
    field = session.prepare_field((0, 0))
    field.focus_deadline_monotonic_s = clock[0] + 31
    mocker.patch.object(rg, "autofocus_for_scan", return_value=_focused_result())
    scripts = len(controller.scripts)

    def slow_standard_capture(**_kwargs):
        assert field.focus_deadline_monotonic_s is None
        assert field.stage == "rg_verified"
        assert not session.observations
        workflow._cam.streaming_mode = "full_resolution"
        clock[0] += 601 if expire_overall else 32
        workflow._cam.streaming_mode = "default"

    workflow._cam.capture_and_save_to_path.side_effect = slow_standard_capture
    if expire_overall:
        with pytest.raises(focus_runtime.FocusScanSafetyError, match="field deadline"):
            workflow._autofocus_and_capture(
                (0, 0, 0),
                20,
                directory.images_dir,
                "standard",
                "led",
                focus_field=field,
            )
        assert not session.observations
        assert not session.return_to_start_allowed
        assert session.runtime_summary.scan_state == "stopped"
    else:
        assert workflow._autofocus_and_capture(
            (0, 0, 0), 20, directory.images_dir, "standard", "led", focus_field=field
        ) == (True, 0, 1)
        assert len(session.observations) == 1
        assert field.stage == "focused"
    assert workflow._cam.streaming_mode == "default"
    assert len(controller.scripts) == scripts
    workflow._autofocus.fast_autofocus.assert_not_called()


@pytest.mark.parametrize("edit", ["measurement", "validation", "approach", "all"])
def test_scan_frozen_callback_ignores_next_run_edits(  # noqa: PLR0915
    lifecycle_stage, mocker, tmp_path, edit
):
    """Live next-run edits cannot stop/reconfigure this native two-pair RG cycle."""
    stage, controller = lifecycle_stage
    workflow, rg, settings = _workflow_owners(stage, mocker, tmp_path)
    directory = ScanDirectory.new_scan_dir("frozen-controls", str(tmp_path / "scans"))
    surface = mocker.spy(rg, "focus_surface_binding")
    frozen_lookup = mocker.spy(rg, "_frozen_scan_profile")
    ordinary_lookup = mocker.spy(rg, "checked_profile")
    session = workflow.new_focus_session(
        settings, scan_id=directory.name, evidence_writer=directory.save_focus_evidence
    )
    assert session is not None
    field = session.prepare_field((0, 0))
    old = session.frozen_rg.model_dump(mode="json")
    ordinary_calls = ordinary_lookup.call_count
    captures = []

    def capture(_directory, iteration, *, field_id, measurement_policy, **_kwargs):
        assert measurement_policy == session.frozen_rg.measurement_policy
        captures.append(iteration)
        if iteration == 1:
            if edit in ("measurement", "all"):
                estimator = rg.measurement_parameters.estimator.model_copy(
                    update={"minimum_confidence": 0.2}
                )
                rg.measurement_parameters = rg.measurement_parameters.model_copy(
                    update={"estimator": estimator}
                )
            if edit in ("validation", "all"):
                rg.parameters = rg.parameters.model_copy(
                    update={"maximum_holdout_error_um": 2.0}
                )
            if edit in ("approach", "all"):
                rg.approach_parameters = rg.approach_parameters.model_copy(
                    update={"preload_um": 16.0}
                )
        return _pair(iteration, field_id=field_id)

    mocker.patch.object(rg, "_capture_pair", side_effect=capture)
    before = len(controller.scripts)
    result = rg.autofocus_for_scan(field)
    field.validate_rg_result(result)
    field.accept_rg_result()
    assert result["status"] == "focused"
    assert captures == [1]
    assert result["measurement_parameters"] == old["measurement_policy"]
    assert result["approach_parameters"] == old["binding"]["approach_parameters"]
    assert session.frozen_rg.model_dump(mode="json") == old
    if edit in ("measurement", "all"):
        assert rg.measurement_parameters.estimator.minimum_confidence == 0.2
    if edit in ("validation", "all"):
        assert rg.parameters.maximum_holdout_error_um == 2.0
    if edit in ("approach", "all"):
        assert rg.approach_parameters.preload_um == 16
    assert ordinary_lookup.call_count == ordinary_calls
    assert surface.call_count > 2
    assert frozen_lookup.call_count > 2
    assert all(
        call.kwargs.get("frozen") is not None for call in surface.call_args_list[3:]
    )
    scripts = controller.scripts[before:]
    assert len(scripts) == 2
    assert result["iterations"][0]["z_path_units"] == [-5, 3]
    sign = stage.hardware_settings["axes"]["z"]["direction_sign"]
    assert f"Z{-0.005 * sign:.6f}" in scripts[0]
    assert f"Z{0.003 * sign:.6f}" in scripts[1]
    assert not session.observations


@pytest.mark.parametrize("change", ["removed", "replaced"])
def test_real_profile_change_stops_frozen_cycle(
    lifecycle_stage, mocker, tmp_path, change
):
    """The frozen callback still refuses actual profile loss/change before new effects."""
    stage, controller = lifecycle_stage
    workflow, rg, settings = _workflow_owners(stage, mocker, tmp_path)
    directory = ScanDirectory.new_scan_dir("profile-change", str(tmp_path / "scans"))
    session = workflow.new_focus_session(
        settings, scan_id=directory.name, evidence_writer=directory.save_focus_evidence
    )
    assert session is not None
    field = session.prepare_field((0, 0))
    captures = []

    def capture(_directory, iteration, *, field_id, **_kwargs):
        captures.append(iteration)
        if change == "removed":
            rg.profile = None
        else:
            rg.profile = session.frozen_rg.profile.model_copy(
                update={"source_series_id": "replacement-series"}
            ).model_dump(mode="json")
        return _pair(iteration, field_id=field_id)

    mocker.patch.object(rg, "_capture_pair", side_effect=capture)
    before = len(controller.scripts)
    with pytest.raises(ValueError, match="profile was removed|profile changed"):
        rg.autofocus_for_scan(field)
    assert captures == [1]
    assert len(controller.scripts) == before
    assert not session.observations
    assert not session.return_to_start_allowed


@pytest.mark.parametrize("has_focus_evidence", [False, True])
def test_automatic_purge_preserves_focus_only_scans(
    mocker, tmp_path, has_focus_evidence
):
    """Only focus-bearing no-tile scans survive automatic empty-scan cleanup."""
    scan = create_thing_without_server(
        SmartScanThing, default_workflow="mock-_all_workflows", mock_all_slots=True
    )
    mocker.patch.object(
        type(scan), "data_dir", new_callable=PropertyMock, return_value=str(tmp_path)
    )
    directory = ScanDirectory.new_scan_dir("no-tiles", str(tmp_path))
    assert Path(scan.data_dir) / directory.name == Path(directory.dir_path)
    if has_focus_evidence:
        directory.save_focus_evidence("manifest.json", {"resume_allowed": False})
    scan._gallery_db_engine = MagicMock()
    info = ScanGalleryInfo(
        duration=1.0,
        number_of_images=0,
        stitch_available=False,
        stitched_jpeg=None,
        dzi=None,
    )
    scan.gallery_db_engine.get_all_entries.return_value = [
        SimpleNamespace(path=directory.name, gallery_info=info.model_dump())
    ]
    deletion = mocker.patch.object(scan, "_delete_scan", return_value=True)
    scan.purge_empty_scans()
    if has_focus_evidence:
        deletion.assert_not_called()
    else:
        deletion.assert_called_once_with(directory.name)


@pytest.mark.parametrize(
    "fault",
    ["first_jpeg", "restore", "cancel_after_approach", "journal_after_approach"],
)
def test_failure_keeps_journal_through_sample_scan_finally(  # noqa: C901, PLR0915
    lifecycle_stage, mocker, tmp_path, fault
):
    """Real action/finally/purge preserves reached evidence without a later move."""
    stage, controller = lifecycle_stage
    workflow, rg, settings = _workflow_owners(stage, mocker, tmp_path)
    scan = create_thing_without_server(
        SmartScanThing, default_workflow="snake", mock_all_slots=True
    )
    root = tmp_path / "scans"
    root.mkdir()
    mocker.patch.object(
        SmartScanThing, "data_dir", new_callable=PropertyMock, return_value=str(root)
    )
    mocker.patch.object(SmartScanThing, "_stage", property(lambda _self: stage))
    mocker.patch.object(SmartScanThing, "_workflow", property(lambda _self: workflow))
    mocker.patch.object(SmartScanThing, "_cam", property(lambda _self: workflow._cam))
    scan._gallery_db_engine = ScanGalleryDBEngine(str(root), scan)

    def collect(_workflow):
        return ActiveScanData(
            scan_name=scan.ongoing_scan.name,
            start_time=datetime.now(),
            starting_position=stage.position,
            save_resolution=(16, 16),
            stitch_automatically=False,
            stitching_settings=None,
            workflow="Snake",
            workflow_settings=settings,
        )

    # Skip only unrelated camera-geometry form assembly. Native action, session
    # construction, loop, planner, AF, capture failure, final details and purge run.
    mocker.patch.object(scan, "_collect_scan_data", side_effect=collect)
    mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.check_free_disk_space"
    )
    mocker.patch(
        "openflexure_microscope_server.scanning.scan_directories.save_invocation_logs",
        return_value=0.0,
    )
    mocker.patch.object(
        rg,
        "_capture_pair",
        side_effect=lambda _directory, iteration, *, field_id, **_kwargs: _pair(
            iteration, field_id=field_id
        ),
    )
    at_failure = {}
    journal_faulted = False
    save_evidence = ScanDirectory.save_focus_evidence

    def remember_boundary():
        at_failure["script_count"] = len(controller.scripts)
        at_failure["position"] = stage.get_xyz_position()
        at_failure["scan_name"] = scan.ongoing_scan.name

    def guarded_evidence(directory, relative_path, payload):
        nonlocal journal_faulted
        if (
            fault == "journal_after_approach"
            and at_failure
            and not journal_faulted
            and relative_path.startswith("events/")
            and payload.get("event") == "rg_validated"
        ):
            journal_faulted = True
            raise OSError("fixture mandatory focus journal failed")
        save_evidence(directory, relative_path, payload)
        if (
            fault != "first_jpeg"
            and relative_path.startswith("fields/")
            and payload.get("event") == "correction_completed"
        ):
            remember_boundary()

    mocker.patch.object(ScanDirectory, "save_focus_evidence", new=guarded_evidence)
    original_cancel_check = focus_runtime.lt.raise_if_cancelled

    def cancellation_boundary():
        if fault == "cancel_after_approach" and at_failure:
            raise InvocationCancelledError("fixture cancel after confirmed approach")
        original_cancel_check()

    mocker.patch.object(
        focus_runtime.lt, "raise_if_cancelled", side_effect=cancellation_boundary
    )

    def capture_tile(**_kwargs):
        assert fault in ("first_jpeg", "restore"), (
            "A failed/cancelled focus must not reach tile capture"
        )
        assert scan._focus_session is not None
        assert scan._focus_session.active_field.stage != "focused"
        assert not scan._focus_session.observations
        remember_boundary()
        if fault == "restore":
            workflow._cam.streaming_mode = "full_resolution"
            return
        raise OSError("fixture first WHITE JPEG failed")

    workflow._cam.capture_and_save_to_path.side_effect = capture_tile
    deletion = mocker.spy(scan, "_delete_scan")
    try:
        error_type = {
            "cancel_after_approach": InvocationCancelledError,
            "restore": RuntimeError,
        }.get(fault, OSError)
        match = "was not restored" if fault == "restore" else "fixture"
        with pytest.raises(error_type, match=match):
            scan.sample_scan("first-tile-failure")
        directory = root / at_failure["scan_name"]
        assert directory.is_dir()
        assert (directory / "focus" / "manifest.json").is_file()
        observations = list((directory / "focus" / "observations").glob("*.json"))
        events = [
            json.loads(path.read_text())
            for path in sorted((directory / "focus" / "events").glob("*.json"))
        ]
        assert any(item["event"] == "correction_completed" for item in events)
        if fault in ("first_jpeg", "restore"):
            assert not observations
            assert not any(item["event"] == "focused" for item in events)
            assert events[-1]["event"] == "stopped"
        else:
            assert not observations
            assert not any(item["event"] == "focused" for item in events)
            assert rg._capture_pair.call_count == 1
            if fault == "cancel_after_approach":
                assert events[-1]["event"] == "stopped"
            else:
                assert journal_faulted
                assert any(item["event"] == "rg_failed" for item in events)
        assert not any(item["event"] == "captured" for item in events)
        assert len(controller.scripts) == at_failure["script_count"]
        assert stage.get_xyz_position() == at_failure["position"]
        deletion.assert_not_called()
        assert at_failure["scan_name"] in scan.gallery_db_engine.get_all_paths()
        assert scan._focus_session is None
        assert scan._ongoing_scan is None
        assert not scan._scan_lock.locked()
    finally:
        scan.gallery_db_engine.dispose()
