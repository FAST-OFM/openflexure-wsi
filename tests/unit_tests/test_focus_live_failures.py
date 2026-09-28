"""Independent LiveScanDetails checks on accepted native failure lifecycles."""

from unittest.mock import PropertyMock

import pytest

from openflexure_microscope_server.things.scanning.smart_scan import SmartScanThing
from tests.unit_tests.test_focus_approach import focus_stage as _stage_fixture
from tests.unit_tests.test_focus_scan_lifecycle import (
    test_failure_keeps_journal_through_sample_scan_finally as _native_failure_case,
)


@pytest.fixture
def live_stage(mocker):
    """Reuse the accepted real stage with a fake HTTP controller, never hardware."""
    yield from _stage_fixture.__wrapped__(mocker)


@pytest.mark.parametrize(
    ("fault", "outcome"),
    [
        ("first_jpeg", "failed"),
        ("cancel_after_approach", "cancelled"),
        ("journal_after_approach", "unknown"),
    ],
)
def test_native_failure_live_snapshot_survives_finally(
    live_stage, mocker, tmp_path, fault, outcome
):
    """Run the existing native case, then inspect the retained public poll state."""
    stage, controller = live_stage
    saved = []
    original_finalize = SmartScanThing._set_final_details_add_to_db

    def finalize(scan):
        original_finalize(scan)
        session = scan._focus_session
        saved.append(
            (
                scan,
                session.runtime_summary,
                session.settings.model_dump(mode="json"),
            )
        )

    mocker.patch.object(SmartScanThing, "_set_final_details_add_to_db", new=finalize)
    # Existing preview-mtime lookup is unrelated to the focus poll contract.
    mocker.patch.object(
        SmartScanThing,
        "latest_preview_stitch_time",
        new_callable=PropertyMock,
        return_value=None,
    )
    snapshot = mocker.spy(stage, "focus_motion_snapshot")
    _native_failure_case(live_stage, mocker, tmp_path, fault)
    assert len(saved) == 1
    scan, terminal, frozen_settings = saved[0]
    assert scan._focus_session is None
    assert scan._scan_data is None
    commands = len(controller.scripts)
    snapshots = snapshot.call_count
    camera_calls = list(scan._cam.mock_calls)
    for _ in range(5):
        details = scan.latest_scan_live_details
        assert details is not None
        assert details.focus == terminal
        assert details.settings["focus_scan"] == frozen_settings
    assert len(controller.scripts) == commands
    assert snapshot.call_count == snapshots
    assert scan._cam.mock_calls == camera_calls
    # Complete is a lifecycle phase, explicitly not a successful outcome.
    assert details.scan_phase == "Complete"
    assert details.image_count == 0
    assert terminal.scan_state == "stopped"
    assert terminal.outcome == outcome
    assert terminal.counters.model_dump() == {
        "fields_planned": 1,
        "fields_no_tissue": 0,
        "fields_focused": 0,
        "fields_captured": 0,
        "fields_failed": int(fault == "first_jpeg"),
        "fields_cancelled": int(fault == "cancel_after_approach"),
        "fields_unknown": int(fault == "journal_after_approach"),
        "white_searches_completed": 0,
    }
    assert terminal.scan_reason
    assert terminal.final_z_um is None
