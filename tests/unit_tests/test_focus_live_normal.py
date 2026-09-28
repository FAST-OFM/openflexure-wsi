"""Positive native lifecycle with active next-run edits and a fresh second map."""

import copy
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import PropertyMock

import pytest
from PIL import Image

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.focus_scan import FocusScanSettings
from openflexure_microscope_server.scanning.scan_directories import (
    ScanDirectory,
    ScanGalleryDBEngine,
)
from openflexure_microscope_server.things.scanning.smart_scan import (
    ActiveScanData,
    SmartScanThing,
)
from tests.unit_tests.test_focus_approach import focus_stage as _stage_fixture
from tests.unit_tests.test_focus_scan_lifecycle import _pair, _workflow_owners


@pytest.fixture
def normal_stage(mocker):
    """Reuse the accepted native adapter with its fake HTTP controller."""
    yield from _stage_fixture.__wrapped__(mocker)


def test_native_normal_scan_keeps_active_setup_and_new_session_starts_empty(  # noqa: C901, PLR0915
    normal_stage, mocker, tmp_path
):
    """Edit and reset next setup after bounded RG focus but before WHITE save."""
    stage, controller = normal_stage
    workflow, rg, template = _workflow_owners(stage, mocker, tmp_path)
    workflow.focus_scan = template.focus_scan
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
    mocker.patch.object(
        SmartScanThing,
        "latest_preview_stitch_time",
        new_callable=PropertyMock,
        return_value=None,
    )
    scan._gallery_db_engine = ScanGalleryDBEngine(str(root), scan)
    state = {}

    def collect(_workflow):
        images = scan.create_data_path(scan.ongoing_scan.images_dir, absolute=True)
        settings = template.model_copy(
            update={
                "capture_params": template.capture_params.model_copy(
                    update={"images_dir": images}
                )
            }
        )
        state["settings"] = settings
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

    # Same narrow fixture seam as the accepted failure lifecycle: bypass only
    # camera-geometry form assembly, not the action/loop/session/finally owners.
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
    snapshots = mocker.spy(stage, "focus_motion_snapshot")
    deleted = mocker.spy(scan, "_delete_scan")

    def save_white_tile(*, path, capture_mode):  # noqa: PLR0915
        assert capture_mode == "standard"
        session = scan._focus_session
        assert session is not None
        assert not session.observations
        assert session.active_field.stage != "focused"
        assert workflow._cam.streaming_mode == "default"
        state["tile_modes"] = [workflow._cam.streaming_mode]
        state["session"] = session
        state["scan_name"] = session.scan_id
        state["ids"] = tuple(item.observation_id for item in session.observations)
        frozen_settings = session.settings.model_dump(mode="json")
        frozen_rg = session.frozen_rg.model_dump(mode="json")
        original_binding = session.current_binding().model_dump(mode="json")
        profile = copy.deepcopy(rg.profile)
        flat_profiles = copy.deepcopy(rg._rg_flat_field.profiles)
        focus_dir = Path(scan.ongoing_scan.dir_path) / "focus"
        archive_before = {
            entry: entry.read_bytes()
            for entry in [
                focus_dir / "manifest.json",
                *sorted((focus_dir / "observations").glob("*.json")),
            ]
        }
        live = scan.latest_scan_live_details
        frozen_live = copy.deepcopy(live.settings)
        assert live.focus.outcome != "focused"
        assert live.focus.counters.fields_focused == 0
        assert live.focus.counters.fields_captured == 0
        edited = session.settings.model_copy(update={"maximum_field_elapsed_s": 900.0})
        for next_setting in (edited, FocusScanSettings()):
            workflow.focus_scan = next_setting
            if not next_setting.run.surface.enabled:
                workflow.autofocus_method = "none"
            assert workflow.focus_scan == next_setting
            assert session.settings.model_dump(mode="json") == frozen_settings
            assert session.frozen_rg.model_dump(mode="json") == frozen_rg
            assert session.current_binding().model_dump(mode="json") == original_binding
            assert (
                tuple(item.observation_id for item in session.observations)
                == state["ids"]
            )
            assert rg.profile == profile
            assert rg._rg_flat_field.profiles == flat_profiles
            commands = len(controller.scripts)
            reads = snapshots.call_count
            camera_calls = list(scan._cam.mock_calls)
            for _ in range(3):
                active = scan.latest_scan_live_details
                assert active.settings == frozen_live
                assert active.focus == session.runtime_summary
                assert active.focus.selected_method == "led"
                assert active.focus.focus_strategy == "single_autofocus"
                assert active.focus.counters.fields_focused == 0
                assert active.focus.counters.fields_captured == 0
            assert len(controller.scripts) == commands
            assert snapshots.call_count == reads
            assert scan._cam.mock_calls == camera_calls
        assert {entry: entry.read_bytes() for entry in archive_before} == archive_before
        state["frozen_live"] = frozen_live
        destination = Path(path.abs_data_path)
        assert destination.parent == Path(scan.ongoing_scan.images_dir)
        # A genuine JPEG exists: normal capture/gallery/zip isn't a no-op save mock.
        with Image.new("RGB", (16, 16), color=(100, 80, 70)) as picture:
            picture.save(destination, format="JPEG")
        workflow._cam.streaming_mode = "full_resolution"
        state["tile_modes"].append(workflow._cam.streaming_mode)
        workflow._cam.streaming_mode = "default"
        state["tile_modes"].append(workflow._cam.streaming_mode)
        state["image"] = destination

    workflow._cam.capture_and_save_to_path.side_effect = save_white_tile
    try:
        scan.sample_scan("normal-focus")
        session_a = state["session"]
        terminal = scan.latest_scan_live_details.model_copy(deep=True)
        assert scan._focus_session is None
        assert scan._scan_data is None
        assert not scan._scan_lock.locked()
        assert terminal.scan_phase == "Complete"
        assert terminal.image_count == 1
        assert terminal.focus.scan_state == "completed"
        assert terminal.focus.outcome == "captured"
        assert terminal.focus.counters.fields_planned == 1
        assert terminal.focus.counters.fields_focused == 1
        assert terminal.focus.counters.fields_captured == 1
        assert terminal.focus.counters.fields_failed == 0
        assert terminal.focus.counters.fields_cancelled == 0
        assert terminal.focus.counters.fields_unknown == 0
        assert terminal.settings == state["frozen_live"]
        assert state["tile_modes"] == [
            "default",
            "full_resolution",
            "default",
        ]
        assert [
            call.kwargs["mode"]
            for call in workflow._cam.change_streaming_mode.call_args_list
        ] == ["default", "default"]
        confirmed_ids = tuple(item.observation_id for item in session_a.observations)
        # A one-pair correction is sufficient for the demo tile, but it is not an
        # independent final measurement and therefore must not train the surface.
        assert not confirmed_ids
        archived = list(
            (root / state["scan_name"] / "focus" / "observations").glob("*.json")
        )
        assert len(archived) == 1
        assert json.loads(archived[0].read_text())["status"] == "candidate"
        assert not state["ids"]
        assert state["image"].is_file()
        assert state["scan_name"] in scan.gallery_db_engine.get_all_paths()
        assert workflow._cam.capture_and_save_to_path.call_count == 1
        deleted.assert_not_called()
        assert not workflow.focus_scan.run.surface.enabled
        assert workflow.autofocus_method == "none"

        # Explicitly configure a new enabled run; do not inherit the active map or
        # silently reactivate the reset next-run setting. B does not move/capture.
        directory_b = ScanDirectory.new_scan_dir("fresh-focus", str(root))
        workflow.autofocus_method = "led"
        workflow.focus_scan = template.focus_scan
        settings_b = template.model_copy(
            update={
                "capture_params": template.capture_params.model_copy(
                    update={
                        "images_dir": scan.create_data_path(
                            directory_b.images_dir, absolute=True
                        )
                    }
                )
            }
        )
        commands = len(controller.scripts)
        session_b = workflow.new_focus_session(
            settings_b,
            scan_id=directory_b.name,
            evidence_writer=directory_b.save_focus_evidence,
        )
        assert session_b is not None
        assert session_b.scan_id != session_a.scan_id
        assert not session_b.observations
        assert not session_b._observation_ids
        assert session_b.runtime_summary.outcome == "not_started"
        assert all(
            value == 0
            for value in session_b.runtime_summary.counters.model_dump().values()
        )
        assert len(controller.scripts) == commands
        assert workflow._cam.capture_and_save_to_path.call_count == 1
        assert scan.latest_scan_live_details == terminal
        assert (
            tuple(item.observation_id for item in session_a.observations)
            == confirmed_ids
        )
        assert (root / state["scan_name"] / "focus" / "manifest.json").is_file()
        assert (Path(directory_b.dir_path) / "focus" / "manifest.json").is_file()
    finally:
        scan.gallery_db_engine.dispose()
