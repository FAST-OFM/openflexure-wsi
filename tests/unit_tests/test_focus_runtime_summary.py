"""Independent OF-068 summary boundaries through native field/session owners.

Only optics, the camera save and the HTTP controller are simulated. No publisher
or runtime-summary method is mocked.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from openflexure_microscope_server.focus.focus_scan import FocusScanSettings
from openflexure_microscope_server.scanning.scan_directories import ScanDirectory
from openflexure_microscope_server.things.scanning.scan_workflows import (
    NoFocusFoundError,
)
from tests.unit_tests.test_focus_approach import focus_stage as _stage_fixture
from tests.unit_tests.test_focus_scan import enabled_settings, native_white_session
from tests.unit_tests.test_focus_scan_lifecycle import _pair, _workflow_owners


@pytest.fixture
def runtime_stage(mocker):
    """Reuse the accepted real-stage/fake-controller fixture."""
    yield from _stage_fixture.__wrapped__(mocker)


def _workflow_session(stage, mocker, tmp_path, *, white=False, policy="keep_z"):
    workflow, rg, settings = _workflow_owners(stage, mocker, tmp_path)
    focus = enabled_settings(workflow._current_focus_binding(), white=white)
    focus = focus.model_copy(
        update={"run": focus.run.model_copy(update={"no_tissue_mode": policy})}
    )
    settings = settings.model_copy(update={"focus_scan": focus})
    directory = ScanDirectory.new_scan_dir("summary", str(tmp_path / "scans"))
    session = workflow.new_focus_session(
        settings, scan_id=directory.name, evidence_writer=directory.save_focus_evidence
    )
    assert session is not None
    return workflow, rg, settings, session, directory


def _synthetic_pairs(mocker, rg):
    def capture(_directory, iteration, *, field_id, **_kwargs):
        return _pair(iteration, field_id=field_id)

    return mocker.patch.object(rg, "_capture_pair", side_effect=capture)


def test_next_field_resets_per_field_evidence_not_scan_counters(
    runtime_stage, mocker, tmp_path
):
    """Observe the actual field_planned publication before later preparation."""
    stage, _controller = runtime_stage
    session, rg, _records = native_white_session(stage, mocker, tmp_path)
    _synthetic_pairs(mocker, rg)
    first = session.prepare_field((10, 0))
    first.validate_rg_result(rg.autofocus_for_scan(first))
    first.accept_rg_result()
    session.finish_field(first, imaged=True)
    old = session.runtime_summary
    assert old.measured_z_um is not None
    assert old.final_z_um is not None
    assert old.white_focus_z_um is not None
    assert old.white_search_count == 1
    assert old.counters.fields_focused == old.counters.fields_captured == 1
    published = []
    native_record = session.record

    def observe_record(field, event, details):
        native_record(field, event, details)
        if event == "field_planned":
            published.append((field, session.runtime_summary))

    mocker.patch.object(session, "record", side_effect=observe_record)
    second = session.prepare_field((20, 0))
    assert len(published) == 1
    planned_field, summary = published[0]
    assert planned_field is second
    assert summary.field_id != old.field_id
    assert summary.field_id == second.field_id
    assert summary.outcome == "planned"
    assert summary.stage == "planned"
    assert {
        "measured_z_um": summary.measured_z_um,
        "final_z_um": summary.final_z_um,
        "white_focus_z_um": summary.white_focus_z_um,
        "white_search_count": summary.white_search_count,
        "reason": summary.reason,
        "scan_reason": summary.scan_reason,
    } == {
        "measured_z_um": None,
        "final_z_um": None,
        "white_focus_z_um": None,
        "white_search_count": 0,
        "reason": second.prediction.reason,
        "scan_reason": None,
    }
    expected = old.counters.model_dump()
    expected["fields_planned"] += 1
    assert summary.counters.model_dump() == expected
    # Detached snapshots are not retroactively updated by a later field.
    assert old.field_id == first.field_id
    assert old.counters.fields_planned == 1


@pytest.mark.parametrize("event", ["focused", "captured"])
def test_second_journal_write_does_not_publish_success(
    runtime_stage, mocker, tmp_path, event
):
    """Event exists, but its failed checkpoint cannot become a UI success."""
    stage, controller = runtime_stage
    workflow, rg, settings, session, directory = _workflow_session(
        stage, mocker, tmp_path
    )
    _synthetic_pairs(mocker, rg)
    field = session.prepare_field((0, 0))
    original_write = session._write
    boundary = {}

    def fail_second_write(path, payload):
        if path.startswith("fields/") and payload.get("event") == event:
            boundary["summary"] = session.runtime_summary
            boundary["commands"] = len(controller.scripts)
            boundary["position"] = stage.get_xyz_position()
            boundary["event_path"] = (
                f"events/{payload['sequence']:08d}-{payload['event_id']}.json"
            )
            raise OSError("independent second journal write failure")
        original_write(path, payload)

    session._write = fail_second_write
    with pytest.raises(OSError, match="independent second journal"):  # noqa: PT012
        imaged, _z, count = workflow._acquire_with_focus(settings, (0, 0, 0), field)
        assert event == "captured"
        assert imaged
        assert count == 1
        session.finish_field(field, imaged=imaged)

    previous = boundary["summary"]
    summary = session.runtime_summary
    assert summary.scan_state == "stopped"
    assert summary.outcome == "unknown"
    assert "journal" in summary.reason.lower()
    assert summary.stage == previous.stage
    for key in (
        "field_id",
        "path",
        "predicted_z_um",
        "measured_z_um",
        "final_z_um",
        "white_focus_z_um",
        "white_search_count",
    ):
        assert getattr(summary, key) == getattr(previous, key)
    expected = previous.counters.model_dump()
    expected["fields_unknown"] += 1
    assert summary.counters.model_dump() == expected
    assert summary.counters.fields_focused == int(event == "captured")
    assert summary.counters.fields_captured == 0
    assert Path(directory.dir_path, "focus", boundary["event_path"]).is_file()
    assert not session.return_to_start_allowed
    assert len(controller.scripts) == boundary["commands"]
    assert stage.get_xyz_position() == boundary["position"]
    # A later exception handler must not count/publish this failure twice.
    session.stop(field, OSError("outer owner observed failed journal"))
    assert session.runtime_summary == summary


@pytest.mark.parametrize("policy", ["keep_z", "skip_tile", "pause"])
def test_no_tissue_policy_labels_counters_and_native_dispatch(
    runtime_stage, mocker, tmp_path, policy
):
    """An empty field is never reported as a focused field, including keep_z."""
    stage, controller = runtime_stage
    workflow, rg, settings, session, _directory = _workflow_session(
        stage, mocker, tmp_path, white=True, policy=policy
    )
    mocker.patch.object(
        rg,
        "prepare_tissue_field_for_scan",
        return_value={
            "id": "empty",
            "status": "no_tissue",
            "reason": "fresh empty field",
            "frame_id": "white-empty",
            "report_ref": "tissue/empty/report.json",
        },
    )
    autofocus = mocker.spy(rg, "autofocus_for_scan")
    field = session.prepare_field((10, 0))
    prepared = session.runtime_summary
    assert prepared.outcome == "no_tissue"
    assert policy in prepared.reason
    commands = len(controller.scripts)
    if policy == "pause":
        with pytest.raises(NoFocusFoundError, match="Fresh WHITE") as raised:
            workflow._acquire_with_focus(settings, (10, 0, 0), field)
        # This is the actual session owner called by the scan's exception handler.
        session.stop(field, raised.value)
    else:
        imaged, _z, count = workflow._acquire_with_focus(settings, (10, 0, 0), field)
        assert imaged is (policy == "keep_z")
        assert count == int(policy == "keep_z")
        session.finish_field(field, imaged=imaged)
        session.complete()
    summary = session.runtime_summary
    assert summary.outcome == "no_tissue"
    assert summary.scan_state == ("stopped" if policy == "pause" else "completed")
    assert summary.field_id == field.field_id
    assert summary.counters.fields_planned == summary.counters.fields_no_tissue == 1
    assert summary.counters.fields_captured == int(policy == "keep_z")
    assert summary.counters.fields_focused == 0
    assert summary.counters.fields_failed == summary.counters.fields_unknown == 0
    assert summary.counters.fields_cancelled == 0
    assert summary.white_search_count == summary.counters.white_searches_completed == 0
    assert (
        summary.final_z_um is summary.measured_z_um is summary.white_focus_z_um is None
    )
    if policy == "keep_z":
        assert "keep_z" in summary.reason
        assert "captured" in summary.reason
    elif policy == "skip_tile":
        assert "skip_tile" in summary.reason
    else:
        assert "Fresh WHITE" in summary.reason
        assert not session.return_to_start_allowed
    autofocus.assert_not_called()
    workflow._autofocus.fast_autofocus_bounded.assert_not_called()
    assert workflow._cam.capture_and_save_to_path.call_count == int(policy == "keep_z")
    assert len(controller.scripts) == commands


def test_next_run_edits_cannot_mutate_live_summary_or_frozen_session(
    runtime_stage, mocker, tmp_path
):
    """Use the real workflow frozen-binding callback, not a guessed UI API."""
    stage, _controller = runtime_stage
    workflow, rg, settings, session, _directory = _workflow_session(
        stage, mocker, tmp_path
    )
    workflow.focus_scan = settings.focus_scan
    field = session.prepare_field((0, 0))
    before = session.runtime_summary
    frozen = session.frozen_rg.model_dump(mode="json")
    run = session.settings.model_dump(mode="json")
    workflow.focus_scan = FocusScanSettings()
    workflow.autofocus_method = "openflexure"
    workflow.focus_strategy = "smart_stack"
    rg.parameters = rg.parameters.model_copy(update={"maximum_holdout_error_um": 2.0})
    rg.measurement_parameters = rg.measurement_parameters.model_copy(
        update={
            "estimator": rg.measurement_parameters.estimator.model_copy(
                update={"minimum_confidence": 0.2}
            )
        }
    )
    rg.approach_parameters = rg.approach_parameters.model_copy(
        update={"preload_um": 16}
    )
    assert session.runtime_summary == before
    assert session.settings.model_dump(mode="json") == run
    assert session.frozen_rg.model_dump(mode="json") == frozen
    detached = session.runtime_summary
    with pytest.raises(ValidationError, match="frozen_instance"):
        detached.counters.fields_planned = 99
    assert session.runtime_summary == before
    _synthetic_pairs(mocker, rg)
    field.validate_rg_result(rg.autofocus_for_scan(field))
    field.accept_rg_result()
    focused = session.runtime_summary
    assert focused.outcome == "focused"
    assert focused.selected_method == "led"
    assert focused.focus_strategy == "single_autofocus"
    assert focused.counters.fields_focused == 1
    assert focused.counters.fields_captured == 0
    assert before.outcome == "planned"
    assert before.counters.fields_focused == 0
