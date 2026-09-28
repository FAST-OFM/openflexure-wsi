"""Test the functionality in the scan_directories module."""

import json
from copy import copy
from datetime import datetime, timedelta
from math import floor

from pydantic import BaseModel

from openflexure_microscope_server.scanning.scan_directories import HistoricScanData
from openflexure_microscope_server.stitching.stitching import StitchingSettings
from openflexure_microscope_server.things.scanning.smart_scan import ActiveScanData

MOCK_START_TIME = datetime(
    year=2024,
    month=12,
    day=25,
    hour=11,
    minute=0,
    second=0,
    microsecond=1234,
)

MOCK_END_TIME = datetime(
    year=2024,
    month=12,
    day=25,
    hour=12,
    minute=29,
    second=3,
    microsecond=5678,
)


def _fake_legacy_scan_data(**kwargs) -> HistoricScanData:
    """Make fake legacy scan data.

    :param **kwargs: Key word arguments can be used to override other values.
    """
    data_dict = {
        "scan_name": "fake_scan_0001",
        "starting_position": {"x": 123, "y": 456, "z": 789},
        "overlap": 0.1,
        "max_dist": 100000,
        "dx": 100,
        "dy": 100,
        "autofocus_dz": 1000,
        "autofocus_on": True,
        "start_time": copy(MOCK_START_TIME),
        "skip_background": True,
        "stitch_automatically": True,
        "correlation_resize": 0.25,
        "save_resolution": (1000, 1000),
    }
    for key, value in kwargs.items():
        data_dict[key] = value
    return HistoricScanData(**data_dict)


class MockWorkflowSettingModel(BaseModel):
    """A mock model to check that ActiveScanData can hold arbitrary models."""

    setting_1: int
    setting_2: int
    setting_3: str


def fake_active_scan_data():
    """Fake scan data for and active scan.

    The start time is now. Final properties are not added.
    """
    return ActiveScanData(
        schema_version=2,
        scan_name="fake_scan_0001",
        starting_position={"x": 123, "y": 456, "z": 789},
        start_time=copy(MOCK_START_TIME),
        stitch_automatically=True,
        save_resolution=(1000, 1000),
        stitching_settings=StitchingSettings(correlation_resize=0.25, overlap=0.1),
        workflow="MockWorkflow",
        workflow_settings=MockWorkflowSettingModel(
            setting_1=1,
            setting_2=2,
            setting_3="three",
        ),
    )


def assert_active_and_historic_data_equivalent(active_data, historic_data):
    """Raise and error if active and historic scan data is not equivalent."""
    assert isinstance(active_data, ActiveScanData)
    assert isinstance(historic_data, HistoricScanData)
    # For the round trip to be equal we must remove microseconds from the start
    # time as they are not saved
    active_data.start_time = active_data.start_time.replace(microsecond=0)
    for key in active_data.model_fields:
        if key == "workflow_settings":
            # For workflow_settings check the base model serialises to the historic
            # data.
            active_wf_setting_dict = active_data.workflow_settings.model_dump()
            assert historic_data.workflow_settings == active_wf_setting_dict
            continue

        assert getattr(active_data, key) == getattr(historic_data, key)


def test_legacy_data_validates():
    """Check that legacy scan data validates."""
    scan_data = _fake_legacy_scan_data()
    assert isinstance(scan_data, HistoricScanData)
    assert scan_data.image_count == 0
    assert scan_data.duration is None
    assert scan_data.scan_result is None
    # Most importantly legacy stitching data should now be in the StitchingSettings
    # model
    assert scan_data.stitching_settings.correlation_resize == 0.25
    assert scan_data.stitching_settings.overlap == 0.1


def test_set_final_data():
    """Check that adding final data to a ActiveScanData object works as expected."""
    scan_data = fake_active_scan_data()

    assert scan_data.image_count == 0
    assert scan_data.duration is None
    assert scan_data.scan_result is None

    # Quickly take 123 images!
    scan_data.image_count += 123
    # Should set duration based on finishing at datetime.now()
    scan_data.set_final_data(result="Success")
    expected_duration = datetime.now() - MOCK_START_TIME
    expected_duration_s = expected_duration.total_seconds()
    scan_duration_s = scan_data.duration.total_seconds()

    assert scan_data.image_count == 123
    assert expected_duration_s - 1 < scan_duration_s < expected_duration_s + 1
    assert scan_data.scan_result == "Success"


def test_custom_serialisation():
    """Check that the custom serialisation in ActiveScanData works as expected."""
    scan_data = fake_active_scan_data()
    # Serialise to string then load directly as json
    scan_data_dict = json.loads(scan_data.model_dump_json())
    assert scan_data_dict["start_time"] == "2024-12-25_11:00:00"
    assert scan_data_dict["image_count"] == 0
    assert scan_data_dict["duration"] == "Unknown"
    assert scan_data_dict["scan_result"] == "Unknown"

    scan_data.image_count += 123
    scan_data.set_final_data(result="Success")
    # Can't mock datetime.now as datetime is immutable. So just replace duration
    scan_data.duration = MOCK_END_TIME - scan_data.start_time
    scan_data_dict = json.loads(scan_data.model_dump_json())
    assert scan_data_dict["image_count"] == 123
    assert scan_data_dict["duration"] == "1:29:03"
    assert scan_data_dict["scan_result"] == "Success"


def test_round_trip_not_finalised():
    """Check that ActiveScanData without final data can be serialised and deserialised."""
    scan_data = fake_active_scan_data()
    scan_data_dict = json.loads(scan_data.model_dump_json())
    scan_data_reloaded = HistoricScanData(**scan_data_dict)

    assert_active_and_historic_data_equivalent(scan_data, scan_data_reloaded)


def test_round_trip_finalised():
    """Check that finalised HistoricScanData can be serialised and deserialised."""
    scan_data = fake_active_scan_data()
    # Finalise the data.
    scan_data.image_count += 123
    scan_data.set_final_data(result="Success")

    scan_data_dict = json.loads(scan_data.model_dump_json())
    scan_data_reloaded = HistoricScanData(**scan_data_dict)

    # For the round trip to be equal we must remove microseconds from the start
    # time and duration as they are not saved
    scan_data.model_copy(update={})
    scan_data.start_time = scan_data.start_time.replace(microsecond=0)
    scan_data.duration = timedelta(seconds=floor(scan_data.duration.total_seconds()))

    assert_active_and_historic_data_equivalent(scan_data, scan_data_reloaded)
