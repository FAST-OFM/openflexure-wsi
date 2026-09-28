"""Test the SmartScanThing *without* connecting it to a LabThings Server.

By testing without connecting to the LabThings server it is possible to
directly poll any properties and to start any methods.

Any methods with LabThings dependency injections will require dependencies
to be passed in manually. Rather than passing in real LabThings clients
it is possible to create test objects that mock these clients. Thus, entirely
isolating one Thing for testing, at the expense of needing to create detailed
mock objects.

For these tests to reliably represent real behaviour the mock Things will need to
be tested for matching signatures with dynamically generated clients.
"""

import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Callable, Optional
from unittest import mock

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from labthings_fastapi.exceptions import InvocationCancelledError
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.focus_scan import (
    FocusRuntimeSummary,
    FocusScanSettings,
)
from openflexure_microscope_server.scanning.scan_directories import (
    ScanDirectory,
    ScanGalleryDBEngine,
)
from openflexure_microscope_server.scanning.scan_planners import (
    RegularGridPlanner,
    SmartSpiral,
)
from openflexure_microscope_server.stitching.stitching import StitchingSettings
from openflexure_microscope_server.things.scanning.scan_workflows import ScanWorkflow
from openflexure_microscope_server.things.scanning.smart_scan import (
    ActiveScanData,
    LiveScanDetails,
    ScanNotRunningError,
    SmartScanThing,
)
from openflexure_microscope_server.utilities import NotEnoughFreeSpaceError

# Use our own dir in the root temp dir not a dynamically generated one so we
# have some control of when it is deleted
SCAN_DIR = os.path.join(tempfile.gettempdir(), "smartscanthing")


def _clear_scan_dir() -> None:
    """Delete the scan dir."""
    if os.path.exists(SCAN_DIR):
        shutil.rmtree(SCAN_DIR)


@pytest.fixture
def entered_smart_scan_thing(smart_scan_thing):
    """Yield a smart scan thing as a fixture that has been entred.

    This will make a scan directory manager. This fixture also clears the scan dir
    """
    _clear_scan_dir()
    with smart_scan_thing:
        yield smart_scan_thing


def custom_smart_scan_thing(default_workflow, all_workflows, mocker):
    """Set up a custom smart scan thing with workflows adjusted.

    This allows setting a default workflow and to adjust all workflows from simple
    single item mock from `mock_all_slots`.
    """
    smart_scan_thing = create_thing_without_server(
        SmartScanThing,
        default_workflow=default_workflow,
        mock_all_slots=True,
    )
    interface_type = type(smart_scan_thing._thing_server_interface)
    interface_type.application_config = mocker.PropertyMock(
        return_value={"data_folder": tempfile.gettempdir()}
    )
    # Pop the existing mock workflow and add specified ones (if any)
    smart_scan_thing._all_workflows.pop("mock-_all_workflows")
    for key, thing in all_workflows.items():
        smart_scan_thing._all_workflows[key] = thing

    return smart_scan_thing


def test_initial_properties(entered_smart_scan_thing):
    """Check the initial values of properties.

    Test properties of SmartScanThing are available without a ThingServer
    and return expected default values.
    """
    smart_scan_thing = entered_smart_scan_thing
    assert isinstance(smart_scan_thing._gallery_db_engine, ScanGalleryDBEngine)
    assert smart_scan_thing._gallery_db_engine._data_dir == SCAN_DIR
    assert smart_scan_thing.latest_scan_live_details is None


def test_repeated_live_focus_poll_reads_only_bounded_memory(smart_scan_thing, mocker):
    """Live polling does not touch stage, camera or any focus evidence archive."""
    summary = FocusRuntimeSummary.from_settings(FocusScanSettings())
    smart_scan_thing._focus_session = SimpleNamespace(runtime_summary=summary)
    smart_scan_thing._latest_scan_live_details = LiveScanDetails(name="test-scan")
    smart_scan_thing._stage.reset_mock()
    smart_scan_thing._cam.reset_mock()
    preview = mocker.patch.object(
        type(smart_scan_thing),
        "latest_preview_stitch_time",
        new_callable=mocker.PropertyMock,
        return_value=None,
    )

    for _ in range(5):
        details = smart_scan_thing.latest_scan_live_details
        assert details is not None
        assert details.focus == summary

    smart_scan_thing._stage.assert_not_called()
    smart_scan_thing._cam.assert_not_called()
    assert preview.call_count == 5


@dataclass
class WorkflowSelectorTestCase:
    """The information from a capture in a smart_z_stack."""

    workflows: dict[str, mock.MagicMock]
    default_wf: str
    expected_wf: Optional[str]
    loaded_wf: Optional[str] = None
    side_effect: Optional[type | int | Iterable[int]] = None
    """The side effect of entering the Thing (None, Logger level number, Error type)"""
    match: Optional[str | Iterable[str]] = None


SELECTOR_CASES = [
    WorkflowSelectorTestCase(
        workflows={},
        default_wf="foo",
        expected_wf=None,
        side_effect=RuntimeError,
        match="Could not set Scan Workflow",
    ),
    WorkflowSelectorTestCase(
        workflows={
            "foo": mock.MagicMock(spec=ScanWorkflow),
            "bar": mock.MagicMock(spec=ScanWorkflow),
        },
        default_wf="foo",
        expected_wf="foo",
        side_effect=None,
    ),
    WorkflowSelectorTestCase(
        workflows={
            "foo": mock.MagicMock(spec=ScanWorkflow),
            "bar": mock.MagicMock(spec=ScanWorkflow),
        },
        default_wf="bar",
        expected_wf="bar",
        side_effect=None,
    ),
    WorkflowSelectorTestCase(
        workflows={
            "foo": mock.MagicMock(spec=ScanWorkflow),
            "bar": mock.MagicMock(spec=ScanWorkflow),
        },
        default_wf="wrong",
        expected_wf="foo",
        side_effect=logging.WARNING,
        match="Could not select default key 'wrong'",
    ),
    WorkflowSelectorTestCase(
        workflows={
            "foo": mock.MagicMock(spec=ScanWorkflow),
            "bar": mock.MagicMock(spec=ScanWorkflow),
        },
        default_wf="wrong",
        loaded_wf="bar",
        expected_wf="bar",
        side_effect=None,
    ),
    WorkflowSelectorTestCase(
        workflows={
            "foo": mock.MagicMock(spec=ScanWorkflow),
            "bar": mock.MagicMock(spec=ScanWorkflow),
        },
        default_wf="wrong",
        loaded_wf="wrong",
        expected_wf="foo",
        side_effect=[logging.WARNING, logging.WARNING],
        match=[
            "Could not select 'wrong' from Thing mapping",
            "Could not select default key 'wrong'",
        ],
    ),
]


@pytest.mark.parametrize("case", SELECTOR_CASES)
def test_workflow_set_on_enter(case, check_side_effect, mocker):
    """Check workflow is set on enter."""
    smart_scan_thing = custom_smart_scan_thing(case.default_wf, case.workflows, mocker)
    with check_side_effect(case.side_effect, match=case.match):
        # Load in "loaded" as the sever would
        smart_scan_thing._workflow_name = case.loaded_wf
        with smart_scan_thing:
            assert smart_scan_thing._workflow_name == case.expected_wf
    # If there was an exception during `__enter__` the database isn't cleaned
    # as __exit__ is never run so manually clean up to stop state leaking to other
    # tests.
    if isinstance(case.side_effect, type(Exception)):
        smart_scan_thing._gallery_db_engine.dispose()


def test_setting_workflows(caplog, mocker):
    """Check that setting workflow works, or warns if incorrect."""
    workflows = {
        "foo": mock.MagicMock(spec=ScanWorkflow),
        "bar": mock.MagicMock(spec=ScanWorkflow),
    }
    smart_scan_thing = custom_smart_scan_thing("foo", workflows, mocker)
    with caplog.at_level(logging.WARNING), smart_scan_thing:
        assert smart_scan_thing._workflow_name == "foo"
        assert smart_scan_thing._workflow is workflows["foo"]
        # Can't set None, raises a ValidationError
        with pytest.raises(ValidationError):
            smart_scan_thing.workflow_name = None
        # Empty string still won't change, but just warns
        smart_scan_thing.workflow_name = ""
        assert len(caplog.records) == 1
        assert smart_scan_thing._workflow_name == "foo"
        assert smart_scan_thing._workflow is workflows["foo"]
        # Can't set a different name
        smart_scan_thing.workflow_name = "wrong"
        assert len(caplog.records) == 2  # another log
        assert smart_scan_thing._workflow_name == "foo"
        assert smart_scan_thing._workflow is workflows["foo"]

        # can set a valid name
        smart_scan_thing.workflow_name = "bar"
        assert len(caplog.records) == 2  # No extra logs
        assert smart_scan_thing._workflow_name == "bar"
        assert smart_scan_thing._workflow is workflows["bar"]


def test_inaccessible_scan_methods(smart_scan_thing):
    """Test that method with @_scan_running decorator is inaccessible.

    The @_scan_running decorator makes these functions inaccessible unless
    a scan is running. Also test properties that raise same error.
    """
    with pytest.raises(ScanNotRunningError):
        smart_scan_thing._run_scan()
    with pytest.raises(ScanNotRunningError):
        smart_scan_thing._manage_stitching_threads()

    # Properties
    with pytest.raises(ScanNotRunningError):
        smart_scan_thing.scan_data
    with pytest.raises(ScanNotRunningError):
        smart_scan_thing.ongoing_scan


def test_private_delete_scan(entered_smart_scan_thing, caplog):
    """Test the private _delete_scan method deletes directories or warns if it can't."""
    smart_scan_thing = entered_smart_scan_thing
    with caplog.at_level(logging.INFO):
        fake_scan_name = "fake_scan_0001"
        fake_scan_path = os.path.join(SCAN_DIR, fake_scan_name)

        # Attempt to delete the fake scan. Expect it to fail and provide a warning
        deleted = smart_scan_thing._delete_scan(fake_scan_name)
        assert not deleted
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "WARNING"
        assert caplog.records[0].name == "labthings_fastapi.things.smartscanthing"

        # Make a dir for the fake scan and delete it.
        os.makedirs(fake_scan_path)
        smart_scan_thing.gallery_db_engine.add(fake_scan_name)
        assert os.path.exists(fake_scan_path)
        assert fake_scan_name in smart_scan_thing.gallery_db_engine.get_all_paths()
        deleted = smart_scan_thing._delete_scan(fake_scan_name)
        assert not os.path.exists(fake_scan_path)
        assert deleted
        # Check no extra logs generated
        assert len(caplog.records) == 1


def test_public_delete_scan(entered_smart_scan_thing, caplog):
    """Test the delete_scan API call deletes directories or warns if it can't."""
    smart_scan_thing = entered_smart_scan_thing
    with caplog.at_level(logging.INFO):
        fake_scan_name = "fake_scan_0001"
        fake_scan_path = os.path.join(SCAN_DIR, fake_scan_name)

        # Attempt to delete the fake scan. Expect it to fail
    with pytest.raises(HTTPException) as exc_info:
        smart_scan_thing.delete_scan(fake_scan_name)
    # Should raise a 400 error if the scan doesn't exist, not a 404 as the server
    # was not expecting to receive the scan files
    assert exc_info.value.status_code == 400
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"
    assert caplog.records[0].name == "labthings_fastapi.things.smartscanthing"

    # Make a dir for the fake scan and delete it.
    os.makedirs(fake_scan_path)
    smart_scan_thing.gallery_db_engine.add(fake_scan_name)
    assert os.path.exists(fake_scan_path)
    assert fake_scan_name in smart_scan_thing.gallery_db_engine.get_all_paths()

    smart_scan_thing.delete_scan(fake_scan_name)
    assert not os.path.exists(fake_scan_path)
    # Check no extra logs generated
    assert len(caplog.records) == 1


def test_delete_all_scans(entered_smart_scan_thing, caplog, mocker):
    """Check the delete_all_scan API really does delete all the scans."""
    mock_raise_if_cancelled = mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.lt.raise_if_cancelled",
    )
    smart_scan_thing = entered_smart_scan_thing
    with caplog.at_level(logging.INFO):
        fake_scan_names = [
            "fake_scan_0001",
            "fake_scan_0002",
            "fake_scan_0003",
            "fake_scan_0004",
        ]
        for fake_scan_name in fake_scan_names:
            fake_scan_path = os.path.join(SCAN_DIR, fake_scan_name)
            os.makedirs(fake_scan_path)
            assert os.path.exists(fake_scan_path)

        # Updated the database as the scans were mad without adding.
        smart_scan_thing.gallery_db_engine.sync_db_with_file_system()

        # Now delete them all
        smart_scan_thing.delete_all_gallery_items()
        for fake_scan_name in fake_scan_names:
            fake_scan_path = os.path.join(SCAN_DIR, fake_scan_name)
            assert not os.path.exists(fake_scan_path)
        # One log generated per scan deleted
        assert len(caplog.records) == 4
        for record in caplog.records:
            assert record.message.startswith("Deleting: fake_scan_000")
            assert record.levelname == "INFO"
        # Check that raise_if_cancelled is called once for each scan.
        assert mock_raise_if_cancelled.call_count == 4


def _run_only_outer_scan(
    smart_scan_thing, mocker, adjust_initial_state: Optional[Callable] = None
):
    """Create a subclass of SmartScanThing to mock _run_scan and run sample_scan.

    This should do all the set up for a scan, move into the mocked
    _run_scan method where this can be tested. Once this is done
    the final scan behaviour can be tested too.

    adjust_initial_state is a callable which accepts the mocked smart scan thing
    as the only variable. It can be used to adjust the initial state of the test


    This seems hard to do with a fixture so it is being done with a private
    function
    """

    def check_locked(*_args, **_kwargs):
        """Check the scan is locked."""
        assert smart_scan_thing._scan_lock.locked()

    mocker.patch.object(smart_scan_thing, "_run_scan", side_effect=check_locked)
    mock_save_logs = mocker.patch(
        "openflexure_microscope_server.scanning.scan_directories.save_invocation_logs"
    )

    if adjust_initial_state is not None:
        adjust_initial_state(smart_scan_thing)

    exec_info = None
    try:
        smart_scan_thing.sample_scan(scan_name="FooBar")

    except Exception as e:
        exec_info = e

    assert not smart_scan_thing._scan_lock.locked()
    assert mock_save_logs.call_count >= 1

    # Return the mock thing for further state testing, and the
    # exec_info of any uncaught exceptions that were raised
    return smart_scan_thing, exec_info


def test_outer_scan(entered_smart_scan_thing, mocker):
    """Test setup and teardown of the scan."""
    mock_ss_thing, exec_info = _run_only_outer_scan(entered_smart_scan_thing, mocker)
    assert exec_info is None
    # Checked the mocked _run_scan was run exactly once
    assert mock_ss_thing._run_scan.call_count == 1


def test_outer_scan_wo_sample_skip(entered_smart_scan_thing, mocker):
    """Test setup and teardown of the scan."""

    def _set_skip_background(mock_ss_thing):
        # As the Thing is not connected to a server we set the setting via the internal
        # __dict__ to avoid triggering property emits that require a server
        mock_ss_thing.__dict__["skip_background"] = False

    mock_ss_thing, exec_info = _run_only_outer_scan(
        entered_smart_scan_thing, mocker, _set_skip_background
    )

    assert exec_info is None
    # Checked the mocked _run_scan was run exactly once
    assert mock_ss_thing._run_scan.call_count == 1


MOCK_SCAN_NAME = "test_name_0001"
MOCK_SCAN_DIR = "scans/test_name_0001/images/"
MOCK_START_POS = {"x": 123, "y": 456, "z": 789}


class MockWorkflowSettingModel(BaseModel):
    """A mock model to check that ActiveScanData can hold arbitrary models."""

    foo: str = "bar"
    bar: str = "foo"
    dx: int = 123
    dy: int = 456
    autofocus_method: str = "openflexure"
    focus_scan: Optional[FocusScanSettings] = None


class MockHistoRectangleSettingModel(MockWorkflowSettingModel):
    """Minimal frozen Fast OFM rectangle used to verify physical path preflight."""

    scan_pattern: str = "rectangle_snake"
    max_dist: int = 45000
    rectangle_columns: int = 6
    rectangle_rows: int = 14
    dx: int = 1101
    dy: int = -825


def _expected_scan_data():
    """Return the expected ActiveScanData object for a SmartScan with default properties."""
    expected_dict = {
        "scan_name": MOCK_SCAN_NAME,
        "starting_position": MOCK_START_POS,
        "save_resolution": (1640, 1232),
        "stitch_automatically": True,
        "stitching_settings": {
            "overlap": 0.35,
            "correlation_resize": 0.5,
        },
        "workflow": "Mock",
        "workflow_settings": MockWorkflowSettingModel(),
    }
    return ActiveScanData(start_time=datetime.now(), **expected_dict)


@pytest.fixture
def scan_thing_mocked_for_scan_data(smart_scan_thing, mocker):
    """Return a scan thing that is mocked so that _collect_scan_data will run."""
    # Set the lock so it thinks the scan is running
    with smart_scan_thing._scan_lock:
        smart_scan_thing._stage.position = MOCK_START_POS

        smart_scan_thing._workflow.all_settings.return_value = (
            MockWorkflowSettingModel(),
            StitchingSettings(correlation_resize=0.5, overlap=0.35),
            (1640, 1232),
        )

        mock_ongoing_scan = mocker.Mock()
        mock_ongoing_scan.name = MOCK_SCAN_NAME
        mock_ongoing_scan.images_dir = MOCK_SCAN_DIR
        smart_scan_thing._ongoing_scan = mock_ongoing_scan
        smart_scan_thing._data_dir = "scans"
        smart_scan_thing._cam.stream_active = True
        smart_scan_thing._cam.streaming_mode = "default"

        def change_mode(*, mode):
            smart_scan_thing._cam.streaming_mode = mode

        smart_scan_thing._cam.change_streaming_mode.side_effect = change_mode

        yield smart_scan_thing


def test_collect_scan_data(scan_thing_mocked_for_scan_data):
    """Run _collect_scan_data, and check the ActiveScanData object has the expected values."""
    scan_thing = scan_thing_mocked_for_scan_data

    data = scan_thing._collect_scan_data(scan_thing._workflow)
    expected_data = _expected_scan_data()
    time_diff = expected_data.start_time - data.start_time
    assert abs(time_diff.total_seconds()) < 1
    # Set times to exactly the same before final comparison
    expected_data.start_time = data.start_time
    assert data == expected_data


def test_collect_fast_ofm_rectangle_validates_exact_corner_grid(
    scan_thing_mocked_for_scan_data,
):
    """Rectangle preflight uses its frozen dimensions, not Fast OFM's spiral radius."""
    scan_thing = scan_thing_mocked_for_scan_data
    settings = MockHistoRectangleSettingModel()

    assert scan_thing._planned_xy_corners(settings, MOCK_START_POS) == [
        {"x": 123, "y": 456},
        {"x": 123, "y": -10269},
        {"x": 5628, "y": 456},
        {"x": 5628, "y": -10269},
    ]


def test_save_final_scan_data(scan_thing_mocked_for_scan_data):
    """Run _save_final_scan_data, check save is called with final results in ActiveScanData."""
    scan_thing = scan_thing_mocked_for_scan_data

    scan_thing._scan_data = scan_thing._collect_scan_data(scan_thing._workflow)
    scan_thing._scan_data.image_count = 44
    scan_thing._save_final_scan_data("Mocked!")
    # _ongoing_scan is a mock so we can check that save_scan data was called and get
    # the value
    scan_thing._ongoing_scan.save_scan_data.assert_called()
    final_data = scan_thing._ongoing_scan.save_scan_data.call_args[0][0]
    assert isinstance(final_data, ActiveScanData)
    assert final_data.scan_result == "Mocked!"
    assert final_data.image_count == 44
    assert final_data.duration.total_seconds() < 1


@pytest.fixture
def scan_thing_mocked_for_run_scan(scan_thing_mocked_for_scan_data, mocker):
    """Return a scan_thing mocked so _run_scan works.

    main_scan_loop(), _return_to_starting_position(), and _perform_final_stitch() are
    Mocks and _cam is a MockCameraThing
    """
    scan_thing = scan_thing_mocked_for_scan_data
    mocker.patch.object(scan_thing, "_main_scan_loop")
    mocker.patch.object(scan_thing, "_return_to_starting_position")
    mocker.patch.object(scan_thing, "_perform_final_stitch")

    return scan_thing


def check_run_scan(
    scan_thing, caplog, expected_exception=None, *, expect_preview_stitcher=True
):
    """Run _run_scan with a mocked scan_thing, capture logs and call counts.

    This can be used to check how run_scan executes in different exit modes.
    A separate tests is above for scan completing with no exception.

    :returns: a tuple of:

        * The final result as reported in scan_data
        * The logging records
        * a dictionary of call counts
    """
    if expected_exception is None:
        with caplog.at_level(logging.WARNING):
            scan_thing._scan_data = scan_thing._run_scan(scan_thing._workflow)
    else:
        with pytest.raises(expected_exception), caplog.at_level(logging.WARNING):
            scan_thing._scan_data = scan_thing._run_scan(scan_thing._workflow)
    if expect_preview_stitcher:
        # The preview stitcher object should still exist. And images dir should be set.
        assert scan_thing._preview_stitcher.images_dir == MOCK_SCAN_DIR
    else:
        assert scan_thing._preview_stitcher is None

    final_scan_data = scan_thing._ongoing_scan.save_scan_data.call_args[0][0]
    calls = {
        "cam_change_streaming_mode_calls": scan_thing._cam.change_streaming_mode.call_count,
        "main_scan_loop_calls": scan_thing._main_scan_loop.call_count,
        "return_to_start_calls": scan_thing._return_to_starting_position.call_count,
        "perform_final_stitch_calls": scan_thing._perform_final_stitch.call_count,
        "save_scan_data_calls": scan_thing._ongoing_scan.save_scan_data.call_count,
    }
    return final_scan_data.scan_result, caplog.records, calls


def test_run_scan(scan_thing_mocked_for_run_scan, caplog):
    """Run _save_final_scan_data, check save is called with final results in ActiveScanData."""
    result, logs, calls = check_run_scan(scan_thing_mocked_for_run_scan, caplog)

    assert result == "success"
    assert len(logs) == 0

    expected_calls_numbers = {
        "cam_change_streaming_mode_calls": 3,
        "main_scan_loop_calls": 1,
        "return_to_start_calls": 1,
        "perform_final_stitch_calls": 1,
        "save_scan_data_calls": 2,
    }
    assert calls == expected_calls_numbers


def test_run_scan_does_not_create_preview_stitcher_when_stitching_disabled(
    scan_thing_mocked_for_run_scan, caplog
):
    """A no-stitch scan must not consume capture resources in a preview worker."""
    scan_thing = scan_thing_mocked_for_run_scan
    scan_thing.__dict__["stitch_automatically"] = False

    result, logs, calls = check_run_scan(
        scan_thing, caplog, expect_preview_stitcher=False
    )

    assert result == "success"
    assert len(logs) == 0
    assert calls["main_scan_loop_calls"] == 1


def test_scan_capture_does_not_build_download_zip_in_critical_path(
    smart_scan_thing, mocker
):
    """Captured JPEGs stay on disk; the download action builds their ZIP lazily."""
    planner = RegularGridPlanner(
        initial_position=(0, 0),
        planner_settings={
            "dx": 10,
            "dy": 20,
            "x_count": 1,
            "y_count": 1,
            "style": "snake",
        },
    )
    workflow = mock.MagicMock(spec=ScanWorkflow)
    workflow.new_scan_planner.return_value = planner
    workflow.prepare_scan_target_z.return_value = None
    workflow.preferred_camera_mode.return_value = None
    workflow.acquisition_routine.return_value = (True, 7, 1)
    smart_scan_thing._scan_data = _expected_scan_data()
    smart_scan_thing._ongoing_scan = mock.MagicMock()
    smart_scan_thing._preview_stitcher = None
    smart_scan_thing._data_dir = tempfile.gettempdir()
    smart_scan_thing._stage.position = {"x": 0, "y": 0, "z": 7}
    mocker.patch.object(smart_scan_thing, "_move_to_next_point", return_value=(0, 0, 7))
    mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.check_free_disk_space"
    )

    with smart_scan_thing._scan_lock:
        smart_scan_thing._main_scan_loop(workflow)

    assert smart_scan_thing.scan_data.image_count == 1
    smart_scan_thing.ongoing_scan.zip_files.assert_not_called()


@pytest.mark.parametrize(
    ("method", "active_mode", "mode_calls"),
    [
        ("led", "default", ["default", "default"]),
        ("simultaneous_rg", "default", ["default", "default"]),
        (
            "openflexure",
            "full_resolution",
            ["default", "full_resolution", "default"],
        ),
        ("none", "full_resolution", ["default", "full_resolution", "default"]),
    ],
)
def test_run_scan_selects_camera_mode_from_frozen_method(
    scan_thing_mocked_for_run_scan, method, active_mode, mode_calls
):
    """LED stays in measurement geometry; other frozen methods keep full-res."""
    scan = scan_thing_mocked_for_run_scan
    settings = MockWorkflowSettingModel(autofocus_method=method)
    scan._workflow.all_settings.return_value = (
        settings,
        StitchingSettings(correlation_resize=0.5, overlap=0.35),
        (1640, 1232),
    )
    observed = []

    def run_loop(_workflow):
        observed.append(scan._cam.streaming_mode)
        # This is a next-run edit, not authority to change the active scan.
        scan._workflow.autofocus_method = "openflexure" if method == "led" else "led"
        observed.append(scan._cam.streaming_mode)
        return "fixture complete"

    scan._main_scan_loop.side_effect = run_loop
    scan._run_scan(scan._workflow)
    assert observed == [active_mode, active_mode]
    assert [
        call.kwargs["mode"] for call in scan._cam.change_streaming_mode.call_args_list
    ] == mode_calls


def test_startup_camera_mode_failure_precedes_settings_and_motion(
    scan_thing_mocked_for_run_scan,
):
    """An unconfirmed default mode stops before settings can authorize effects."""
    scan = scan_thing_mocked_for_run_scan
    startup_error = OSError("fixture startup camera mode failed")
    cleanup_error = RuntimeError("fixture cleanup mode failed")
    scan._cam.change_streaming_mode.side_effect = [startup_error, cleanup_error]
    with pytest.raises(OSError, match="startup camera mode failed") as caught:
        scan._run_scan(scan._workflow)
    assert caught.value is startup_error
    scan._workflow.all_settings.assert_not_called()
    scan._main_scan_loop.assert_not_called()
    scan._return_to_starting_position.assert_not_called()
    assert scan._focus_return_blocked


def test_final_mode_restore_failure_preserves_primary_scan_exception(
    scan_thing_mocked_for_run_scan,
):
    """Default cleanup blocks later motion without replacing the scan failure."""
    scan = scan_thing_mocked_for_run_scan
    primary = OSError("fixture primary scan failure")
    cleanup = RuntimeError("fixture default restore failed")
    calls = 0

    def change_mode(*, mode):
        nonlocal calls
        calls += 1
        if calls == 3:
            scan._cam.stream_active = False
            raise cleanup
        scan._cam.streaming_mode = mode

    scan._cam.change_streaming_mode.side_effect = change_mode
    scan._main_scan_loop.side_effect = primary
    with pytest.raises(OSError, match="primary scan failure") as caught:
        scan._run_scan(scan._workflow)
    assert caught.value is primary
    scan._return_to_starting_position.assert_not_called()
    scan._perform_final_stitch.assert_not_called()
    assert scan._focus_return_blocked


def test_cleanup_failure_never_writes_success_if_failure_persistence_fails(
    scan_thing_mocked_for_run_scan,
):
    """No success record precedes a restore failure, even when its save also fails."""
    scan = scan_thing_mocked_for_run_scan
    cleanup = RuntimeError("fixture final default restore failed")
    mode_calls = 0

    def change_mode(*, mode):
        nonlocal mode_calls
        mode_calls += 1
        if mode_calls == 3:
            scan._cam.stream_active = False
            raise cleanup
        scan._cam.streaming_mode = mode

    persisted_results = []

    def save_scan_data(data):
        persisted_results.append(data.scan_result)
        if len(persisted_results) == 2:
            raise OSError("fixture failure metadata save failed")

    scan._cam.change_streaming_mode.side_effect = change_mode
    scan._ongoing_scan.save_scan_data.side_effect = save_scan_data
    scan._main_scan_loop.return_value = "fixture complete"

    with pytest.raises(RuntimeError, match="final default restore failed") as caught:
        scan._run_scan(scan._workflow)

    assert caught.value is cleanup
    assert persisted_results[0] is None
    assert "camera default-mode cleanup failed" in persisted_results[1]
    assert "success" not in persisted_results
    scan._return_to_starting_position.assert_not_called()
    scan._perform_final_stitch.assert_not_called()


@pytest.mark.parametrize("focus_enabled", [False, True])
def test_sample_scan_cleanup_failure_replaces_provisional_success(  # noqa: PLR0915
    entered_smart_scan_thing, mocker, focus_enabled
):
    """A sole final mode failure is persisted and blocks every return/stitch effect."""
    from .test_focus_approach import binding
    from .test_focus_scan import enabled_settings

    scan = entered_smart_scan_thing
    workflow = scan._workflow
    scan._stage.position = MOCK_START_POS
    mocker.patch.object(ScanDirectory, "save_scan_log")
    focus_settings = enabled_settings(binding()) if focus_enabled else None
    settings = MockWorkflowSettingModel(
        autofocus_method="led" if focus_enabled else "openflexure",
        focus_scan=focus_settings,
    )
    workflow.all_settings.return_value = (settings, None, (1640, 1232))
    scan._cam.stream_active = True
    scan._cam.streaming_mode = "default"
    cleanup = RuntimeError("fixture final default restore failed")
    mode_calls = 0

    def change_mode(*, mode):
        nonlocal mode_calls
        mode_calls += 1
        final_call = 2 if focus_enabled else 3
        if mode_calls == final_call:
            scan._cam.stream_active = False
            raise cleanup
        scan._cam.streaming_mode = mode

    scan._cam.change_streaming_mode.side_effect = change_mode
    persisted = []
    original_save = ScanDirectory.save_scan_data

    def save_scan_data(directory, data):
        persisted.append(data.model_copy(deep=True))
        return original_save(directory, data)

    mocker.patch.object(ScanDirectory, "save_scan_data", new=save_scan_data)
    move = mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.move_absolute_transit"
    )
    stitch = mocker.patch.object(scan, "_perform_final_stitch")
    fake_session = None
    if focus_enabled:
        assert focus_settings is not None
        summary = FocusRuntimeSummary.from_settings(focus_settings).model_copy(
            update={"scan_state": "completed", "outcome": "captured"}
        )
        fake_session = SimpleNamespace(
            active_field=None,
            return_to_start_allowed=True,
            runtime_summary=summary,
        )

        def stop(_field, exc):
            fake_session.return_to_start_allowed = False
            fake_session.runtime_summary = fake_session.runtime_summary.model_copy(
                update={
                    "scan_state": "stopped",
                    "outcome": "failed",
                    "scan_reason": str(exc),
                    "reason": str(exc),
                }
            )

        fake_session.stop = mock.Mock(side_effect=stop)
        workflow.new_focus_session.return_value = fake_session

    mocker.patch.object(scan, "_main_scan_loop", return_value="fixture complete")
    with pytest.raises(RuntimeError, match="final default restore failed") as caught:
        scan.sample_scan(f"cleanup-failure-{focus_enabled}")

    assert caught.value is cleanup
    assert persisted[-1].scan_result != "success"
    assert "camera default-mode cleanup failed" in persisted[-1].scan_result
    move.assert_not_called()
    stitch.assert_not_called()
    assert not scan._scan_lock.locked()
    assert scan._focus_session is None
    assert scan._ongoing_scan is None
    if focus_enabled:
        assert fake_session is not None
        fake_session.stop.assert_called_once_with(None, cleanup)
        assert scan.latest_scan_live_details.focus.scan_state == "stopped"
        assert scan.latest_scan_live_details.focus.outcome == "failed"
    else:
        workflow.new_focus_session.assert_not_called()


def test_run_scan_err_in_main_loop(scan_thing_mocked_for_run_scan, caplog, mocker):
    """Check correct methods called if main_loop errors."""
    scan_thing = scan_thing_mocked_for_run_scan
    mocker.patch.object(
        scan_thing, "_main_scan_loop", side_effect=FileNotFoundError("mocked")
    )

    result, logs, calls = check_run_scan(scan_thing, caplog, FileNotFoundError)

    assert result.startswith("FileNotFoundError:")
    assert len(logs) == 1
    assert logs[0].levelno == logging.ERROR

    # Main loop not run, nor are return to start, final stitch, or purging of empty
    # scans. Save scan data is still called twice
    expected_calls_numbers = {
        "cam_change_streaming_mode_calls": 3,
        "main_scan_loop_calls": 1,
        "return_to_start_calls": 0,
        "perform_final_stitch_calls": 0,
        "save_scan_data_calls": 2,
    }
    assert calls == expected_calls_numbers


def test_run_scan_cancelled(scan_thing_mocked_for_run_scan, caplog, mocker):
    """Check correct methods called scan is cancelled."""
    scan_thing = scan_thing_mocked_for_run_scan
    mocker.patch.object(
        scan_thing, "_main_scan_loop", side_effect=InvocationCancelledError()
    )

    result, logs, calls = check_run_scan(scan_thing, caplog, InvocationCancelledError)

    assert result == "cancelled by user"
    # No logs at warning level.
    assert len(logs) == 0

    # Main loop not run, nor are return to start, final stitch, or purging of empty
    # scans. Save scan data is still called twice
    expected_calls_numbers = {
        "cam_change_streaming_mode_calls": 3,
        "main_scan_loop_calls": 1,
        "return_to_start_calls": 1,
        "perform_final_stitch_calls": 0,
        "save_scan_data_calls": 2,
    }
    assert calls == expected_calls_numbers


def test_run_scan_fill_disk(scan_thing_mocked_for_run_scan, caplog, mocker):
    """Check correct methods called if disk fills up."""
    scan_thing = scan_thing_mocked_for_run_scan
    mocker.patch.object(
        scan_thing, "_main_scan_loop", side_effect=NotEnoughFreeSpaceError()
    )

    result, logs, calls = check_run_scan(scan_thing, caplog, NotEnoughFreeSpaceError)

    assert result.startswith("NotEnoughFreeSpaceError:")
    assert len(logs) == 1
    assert logs[0].levelno == logging.ERROR

    # Main loop not run, nor are return to start, final stitch, or purging of empty
    # scans. Save scan data is still called twice
    expected_calls_numbers = {
        "cam_change_streaming_mode_calls": 3,
        "main_scan_loop_calls": 1,
        "return_to_start_calls": 0,
        "perform_final_stitch_calls": 0,
        "save_scan_data_calls": 2,
    }
    assert calls == expected_calls_numbers


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        (
            "snake",
            [(0, 0), (10, 0), (20, 0), (20, 20), (10, 20), (0, 20)],
        ),
        (
            "raster",
            [(0, 0), (10, 0), (20, 0), (0, 20), (10, 20), (20, 20)],
        ),
    ],
)
def test_enabled_main_loop_uses_real_regular_planner_next_xy(
    smart_scan_thing, mocker, style, expected
):
    """Snake/Raster row turns reach focus preparation exactly as planner emitted them."""
    planner = RegularGridPlanner(
        initial_position=(0, 0),
        planner_settings={
            "dx": 10,
            "dy": 20,
            "x_count": 3,
            "y_count": 2,
            "style": style,
        },
    )
    _run_enabled_planner_loop(smart_scan_thing, mocker, planner, expected, imaged=True)


def test_enabled_main_loop_uses_real_fast_ofm_dynamic_target(smart_scan_thing, mocker):
    """Fast OFM's actual dynamic target is consumed once; a skipped field adds no route."""
    planner = SmartSpiral(
        initial_position=(-10, 5),
        planner_settings={"dx": 10, "dy": 20, "max_dist": 100},
    )
    _run_enabled_planner_loop(
        smart_scan_thing, mocker, planner, [(-10, 5)], imaged=False
    )


def test_main_loop_applies_workflow_camera_mode_before_field(smart_scan_thing, mocker):
    """A run-frozen per-field preference is confirmed before movement and capture."""
    planner = SmartSpiral(
        initial_position=(0, 0),
        planner_settings={"dx": 10, "dy": 20, "max_dist": 0},
    )
    workflow = mock.MagicMock(spec=ScanWorkflow)
    workflow.new_scan_planner.return_value = planner
    workflow.prepare_scan_target_z.side_effect = (
        lambda _settings, _xy, route_z, _current_z: route_z
    )
    workflow.preferred_camera_mode.return_value = "full_resolution"
    workflow.acquisition_routine.return_value = (False, None, 0)
    smart_scan_thing._scan_data = _expected_scan_data()
    smart_scan_thing._ongoing_scan = mock.MagicMock()
    smart_scan_thing._preview_stitcher = None
    smart_scan_thing._data_dir = tempfile.gettempdir()
    smart_scan_thing._stage.position = {"x": 0, "y": 0, "z": 7}
    smart_scan_thing._cam.streaming_mode = "default"
    smart_scan_thing._cam.stream_active = True

    def change_mode(*, mode):
        smart_scan_thing._cam.streaming_mode = mode

    smart_scan_thing._cam.change_streaming_mode.side_effect = change_mode
    mocker.patch.object(smart_scan_thing, "_move_to_next_point", return_value=(0, 0, 7))
    mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.check_free_disk_space"
    )

    with smart_scan_thing._scan_lock:
        smart_scan_thing._main_scan_loop(workflow)

    smart_scan_thing._cam.change_streaming_mode.assert_called_once_with(
        mode="full_resolution"
    )
    workflow.acquisition_routine.assert_called_once()


def test_no_tissue_keep_z_tile_does_not_expand_fast_ofm_route(smart_scan_thing, mocker):
    """A deliberately saved empty tile remains distinct from sample-route evidence."""
    planner = SmartSpiral(
        initial_position=(-10, 5),
        planner_settings={"dx": 10, "dy": 20, "max_dist": 100},
    )
    _run_enabled_planner_loop(
        smart_scan_thing,
        mocker,
        planner,
        [(-10, 5)],
        imaged=True,
        no_tissue=True,
    )


def test_selected_method_uses_existing_planner_transit(smart_scan_thing, mocker):
    """Unavailable+selected_method keeps the native route Z estimate and no new planner."""
    planner = RegularGridPlanner(
        initial_position=(0, 0),
        planner_settings={
            "dx": 10,
            "dy": 20,
            "x_count": 2,
            "y_count": 1,
            "style": "snake",
        },
    )
    positions = {"x": 0, "y": 0, "z": 7}
    smart_scan_thing._stage.position = positions
    workflow = mock.MagicMock(spec=ScanWorkflow)
    workflow.new_scan_planner.return_value = planner
    workflow.prepare_scan_target_z.side_effect = (
        lambda _settings, _xy, route_z, _current_z: route_z
    )
    workflow.acquisition_routine.side_effect = lambda _settings, xyz, _field: (
        True,
        xyz[2],
        1,
    )
    fields = []
    focus_session = mock.MagicMock()

    def prepare(_target):
        field = mock.MagicMock()
        field.standard_transit = True
        field.no_tissue = False
        field.budget.post_move_verification_reserve_s = 5.0
        fields.append(field)
        return field

    def transit(target, z_estimate):
        positions["x"], positions["y"] = target
        if z_estimate is not None:
            positions["z"] = z_estimate
        return positions["x"], positions["y"], positions["z"]

    focus_session.prepare_field.side_effect = prepare
    smart_scan_thing._focus_session = focus_session
    smart_scan_thing._scan_data = _expected_scan_data()
    smart_scan_thing._ongoing_scan = mock.MagicMock()
    smart_scan_thing._preview_stitcher = None
    smart_scan_thing._data_dir = tempfile.gettempdir()
    move = mocker.patch.object(
        smart_scan_thing, "_move_to_next_point", side_effect=transit
    )
    mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.check_free_disk_space"
    )
    with smart_scan_thing._scan_lock:
        smart_scan_thing._main_scan_loop(workflow)
    assert [call.args for call in move.call_args_list] == [
        ((0, 0), None),
        ((10, 0), 7),
    ]
    for field in fields:
        field.before_effect.assert_called_once_with(
            "selected_method_transit", reserve_s=5.0
        )
        field.sync_after_effect.assert_called_once_with("selected_method_transit")


def test_simultaneous_transit_uses_target_z_once_and_returns_stage_receipt(
    smart_scan_thing,
):
    """The scan loop can delegate one combined XY+Z-preload movement to its workflow."""
    workflow = mock.MagicMock(spec=ScanWorkflow)
    settings = MockWorkflowSettingModel(autofocus_method="simultaneous_rg")
    smart_scan_thing._stage.position = {"x": 0, "y": 0, "z": 7}
    prepared = mock.MagicMock()
    workflow.prepare_scan_preload.return_value = prepared

    with smart_scan_thing._scan_lock:
        target, actual = smart_scan_thing._move_to_next_point_with_preload(
            workflow, settings, (10, -20)
        )

    assert target == (10, -20, 7)
    assert actual is prepared
    workflow.prepare_scan_preload.assert_called_once_with(settings, target)


@pytest.mark.parametrize("has_session", [False, True])
def test_enabled_failure_blocks_blind_return(smart_scan_thing, mocker, has_session):
    """No return command is sent after session-init failure or unfinished evidence."""
    smart_scan_thing._scan_data = _expected_scan_data()
    smart_scan_thing._focus_return_blocked = True
    if has_session:
        smart_scan_thing._focus_session = SimpleNamespace(return_to_start_allowed=False)
    transit = mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.move_absolute_transit"
    )
    with smart_scan_thing._scan_lock:
        smart_scan_thing._return_to_starting_position()
    transit.assert_not_called()


def _run_enabled_planner_loop(
    thing, mocker, planner, expected, *, imaged, no_tissue=False
):
    """Exercise the real SmartScan loop while only hardware/evidence are fakes."""
    positions = {"x": 0, "y": 0, "z": 7}
    thing._stage.position = positions
    thing._stage.get_xyz_position.side_effect = lambda: (
        positions["x"],
        positions["y"],
        positions["z"],
    )
    workflow = mock.MagicMock(spec=ScanWorkflow)
    workflow.new_scan_planner.return_value = planner
    workflow.acquisition_routine.side_effect = lambda _settings, xyz, _field: (
        imaged,
        xyz[2] if imaged and not no_tissue else None,
        1 if imaged else 0,
    )
    prepared_targets = []
    focus_session = mock.MagicMock()

    def prepare(target):
        prepared_targets.append(target)
        positions["x"], positions["y"] = target
        return SimpleNamespace(standard_transit=False, no_tissue=no_tissue)

    focus_session.prepare_field.side_effect = prepare
    thing._focus_session = focus_session
    thing._scan_data = _expected_scan_data()
    thing._ongoing_scan = mock.MagicMock()
    thing._preview_stitcher = None
    thing._data_dir = tempfile.gettempdir()
    mocker.patch(
        "openflexure_microscope_server.things.scanning.smart_scan.check_free_disk_space"
    )
    with thing._scan_lock:
        thing._main_scan_loop(workflow)
    assert prepared_targets == expected
    assert [call.args[0] for call in focus_session.finish_field.call_args_list] == [
        call.args[2] for call in workflow.acquisition_routine.call_args_list
    ]
    focus_session.complete.assert_called_once_with()
