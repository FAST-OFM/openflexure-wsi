"""Only fresh fully WHITE exposures may advance the preview during measurements."""

import pytest

from openflexure_microscope_server.acquisition.measurement_preview import (
    MeasurementPreview,
)


def frame(gate, sensor_ns, exposure_us=100):
    """Register the camera's pre-encoder metadata and ask about its encoded frame."""
    gate.record({"SensorTimestamp": sensor_ns, "ExposureTime": exposure_us})
    return gate.allows(sensor_ns // 1000)


def test_manual_preview_is_unchanged_before_measurement():
    """Preserve normal preview until a measurement owns illumination."""
    gate = MeasurementPreview()
    assert gate.allows(None)
    assert not gate.status["holding"]


def test_hold_excludes_nonwhite_and_transition_and_does_not_relabel_frames():
    """Publish only fully WHITE exposures, never queued colour frames."""
    gate = MeasurementPreview()
    gate.transition()
    assert not frame(gate, 2_000_000)
    for mode in ("off", "red", "green"):
        gate.confirmed(mode, 3_000_000)
        assert not frame(gate, 4_000_000)
    assert gate.status["published_white_frames"] == 0
    assert gate.status["last_white_sensor_timestamp_us"] is None
    gate.transition()
    gate.confirmed("white", 5_000_000, finished=True)
    assert not gate.allows(None)
    assert not gate.allows(4000)  # queued colour frame
    assert not frame(gate, 5_050_000)  # readout after switch, exposure begins before
    assert gate.status["holding"]
    assert not frame(gate, 5_200_000)  # drain queued camera/encoder requests
    assert not frame(gate, 5_400_000)
    assert frame(gate, 5_600_000)
    assert not gate.status["holding"]
    assert gate.status["last_white_sensor_timestamp_us"] == 5600
    assert not gate.allows(4000)  # queued colour stays blocked after release
    assert gate.status["published_white_frames"] == 1


def test_failed_restore_remains_held_until_later_confirmed_white():
    """Keep failed restoration visible and closed until a verified retry."""
    gate = MeasurementPreview()
    gate.transition()
    gate.confirmed("red", 1_000_000)
    gate.transition()  # attempted restore fails: no confirmed() call
    assert not frame(gate, 2_000_000)
    assert gate.status["active"]
    assert gate.status["holding"]
    gate.confirmed("white", 3_000_000, finished=True)
    assert not frame(gate, 4_000_000)
    assert not frame(gate, 5_000_000)
    assert frame(gate, 6_000_000)


@pytest.mark.parametrize(
    "metadata",
    [{}, {"SensorTimestamp": "x"}, {"SensorTimestamp": 2, "ExposureTime": -1}],
)
def test_invalid_metadata_is_safe_and_not_publishable(metadata):
    """Reject bad metadata without terminating the camera producer."""
    gate = MeasurementPreview()
    gate.confirmed("white", 0)
    gate.record(metadata)
    assert not gate.allows(0)


def test_metadata_history_is_bounded():
    """Drop old matches rather than accumulating camera requests."""
    gate = MeasurementPreview()
    gate.confirmed("white", 0)
    for i in range(130):
        gate.record({"SensorTimestamp": (i + 1) * 1_000_000, "ExposureTime": 100})
    assert not gate.allows(1000)
    assert not gate.allows(128000)
    assert not gate.allows(129000)
    assert gate.allows(130000)


def test_white_release_drains_two_post_boundary_pipeline_frames():
    """Do not expose colour residue merely because its timestamp is post-boundary."""
    gate = MeasurementPreview()
    gate.transition()
    gate.confirmed("red", 1_000_000)
    gate.transition()
    gate.confirmed("white", 2_000_000, finished=True)

    assert not frame(gate, 3_000_000)
    assert gate.status["white_release_frames_remaining"] == 1
    assert not frame(gate, 4_000_000)
    assert gate.status["white_release_frames_remaining"] == 0
    assert frame(gate, 5_000_000)
    assert not gate.allows(None)
