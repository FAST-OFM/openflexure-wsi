"""Standalone Z procedure tests use a play/backlash model, never a real controller."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

import labthings_fastapi as lt
from labthings_fastapi.invocation_contexts import (
    fake_invocation_context,
    get_cancel_event,
)
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.z_backlash_calibration import (
    FocusPeak,
    FocusSample,
    ZCalibrationSettings,
    abba_observation,
    focus_peak,
)
from openflexure_microscope_server.things.focus.z_backlash import ZBacklashCalibration


class PlayModel:
    """Ideal command/physical play with a four-micron reversal deadband."""

    def __init__(self):
        """Start near focus without establishing any real reference."""
        self.x = 0
        self.y = 0
        self.z = 0
        self.origin_units = 0
        self.physical = 0.0
        self.backlash = 4.0
        self.moves = []
        self.bound = 1000
        self.ready = True
        self.exposure = 1_000_000_000
        self.captures = 0
        self.flat = False
        self.on_capture = None
        self.hardware = {
            "axes": {
                "z": {
                    "units_per_mm": 1000.0,
                    "direction_sign": 1,
                    "speed_mm_s": 0.02,
                    "accel_mm_s2": 0.1,
                    "settle_ms": 1000.0,
                }
            }
        }

    def validate(self, targets, **_kwargs):
        """Enforce the fake reference and total allowed trajectory."""
        if not self.ready:
            raise ValueError("No operator reference")
        if any(abs(target.get("z", self.z)) > self.bound for target in targets):
            raise ValueError("Trajectory outside bound")

    def move(self, xyz):
        """Update play state after a completed, recorded command."""
        self.validate([{"z": xyz[2]}])
        self.x, self.y, self.z = xyz
        z_um = (self.z - self.origin_units) / (
            self.hardware["axes"]["z"]["units_per_mm"] / 1000
        )
        self.physical = float(
            np.clip(
                self.physical,
                z_um - self.backlash / 2,
                z_um + self.backlash / 2,
            )
        )
        self.moves.append(xyz)

    def preloaded(self, z, preload, approach):
        """Use the same two-leg sequence as the tested production stage helper."""
        self.validate([{"z": z - approach * preload}, {"z": z}])
        self.move((self.x, self.y, z - approach * preload))
        self.move((self.x, self.y, z))

    def capture(self, _timeout):
        """Return an RGB texture whose sharpness peaks at physical Z=0."""
        self.exposure += 1_000_000_000
        self.captures += 1
        y, x = np.mgrid[:64, :64]
        checker = ((x // 2 + y // 2) % 2) * 2 - 1
        contrast = 0.01 + 0.8 * np.exp(-0.5 * (self.physical / 3.0) ** 2)
        image = np.clip(128 + 80 * contrast * checker, 0, 255).astype(np.uint8)
        if self.flat:
            image[:] = 128
        metadata = {
            "exposure_start_ns": self.exposure,
            "sensor_timestamp_ns": self.exposure + 500_000,
            "exposure_time_us": 500,
            "analogue_gain": 1.0,
            "colour_gains": [1.0, 1.0],
            "scaler_crop": [0, 0, 64, 64],
            "image_size": [64, 64],
        }
        if self.on_capture:
            self.on_capture(metadata)
        return np.repeat(image[:, :, None], 3, axis=2), metadata


@pytest.fixture
def calibration(tmp_path):
    """Attach mocked slots to the actual action and actual atomic settings store."""
    settings = tmp_path / "settings"
    settings.mkdir()
    thing = create_thing_without_server(
        ZBacklashCalibration,
        mock_all_slots=True,
        settings_folder=str(settings),
    )
    thing.load_settings()
    data = tmp_path / "data"
    data.mkdir()
    thing._data_dir = str(data)
    model = PlayModel()
    thing._stage.hardware_settings = model.hardware
    thing._stage.validate_path.side_effect = model.validate
    thing._stage.get_xyz_position.side_effect = lambda: (model.x, model.y, model.z)
    thing._stage.move_absolute_in_segments.side_effect = model.move
    thing._stage.move_z_with_preload.side_effect = model.preloaded
    thing._cam.stream_active = True
    thing._cam.focus_configuration = {"sensor": "synthetic", "roi": [0, 0, 64, 64]}
    thing._cam.capture_settled_frame = model.capture
    thing._illumination.state = SimpleNamespace(mode="white", available=True)
    with fake_invocation_context():
        yield thing, model


@pytest.mark.parametrize("approach", [-1, 1])
def test_complete_white_procedure_saves_and_returns(calibration, approach):
    """Known play is measured and independently preloaded from both starting sides."""
    thing, model = calibration
    thing.set_parameters(ZCalibrationSettings(preferred_final_approach_sign=approach))
    result = thing.calibrate(prepared=True)
    assert result["estimate"]["backlash_um"] == pytest.approx(4, abs=0.05)
    assert result["maximum_residual_um"] <= 0.05
    assert result["validated"]
    assert result["frame_count"] == 99
    assert result["returned_to_commanded_start"]
    assert model.z == 0
    assert all(x == y == 0 for x, y, _z in model.moves)
    assert thing.calibration_status["status"] == "valid"
    assert not thing.calibration_status["compensation_enabled"]
    report = Path(thing.data_dir) / result["data_path"] / "report.json"
    assert len(json.loads(report.read_text())["curves"]) == 10
    assert len(list(report.parent.glob("frame-*.png"))) == 99
    assert (report.parent / "reference.png").is_file()
    assert thing.last_calibration == result
    assert thing.progress["phase"] == "complete"


def test_no_confirmation_no_motion(calibration):
    """A direct API caller must confirm preparation just like the UI."""
    thing, model = calibration
    with pytest.raises(ValueError, match="Confirm"):
        thing.calibrate()
    assert not model.moves
    assert not model.captures


@pytest.mark.parametrize("reason", ["reference", "light", "bounds"])
def test_preflight_refusal_sends_no_moves(calibration, reason):
    """Invalid reference, wrong light or an impossible full path refuses before motion."""
    thing, model = calibration
    if reason == "reference":
        model.ready = False
    elif reason == "light":
        thing._illumination.state.mode = "red"
    else:
        model.bound = 10
    with pytest.raises(ValueError, match="reference|WHITE|bound"):
        thing.calibrate(prepared=True)
    assert not model.moves


def test_empty_reference_is_not_a_calibration(calibration):
    """Operator confirmation alone cannot turn an untextured field into evidence."""
    thing, model = calibration
    model.flat = True
    with pytest.raises(ValueError, match="texture"):
        thing.calibrate(prepared=True)
    assert not model.moves
    assert thing.last_calibration is None
    assert len(list(Path(thing.data_dir).glob("*/reference.png"))) == 1
    assert len(list(Path(thing.data_dir).glob("*/reference.json"))) == 1


def test_failed_repeat_preserves_last_profile(calibration):
    """Failure leaves the previously verified profile in memory and on disk."""
    thing, model = calibration
    old = thing.calibrate(prepared=True)
    settings_file = Path(thing._thing_server_interface.settings_file_path)
    before = settings_file.read_bytes()
    model.flat = True
    with pytest.raises(ValueError, match="texture"):
        thing.calibrate(prepared=True)
    assert thing.last_calibration == old
    assert settings_file.read_bytes() == before


def test_restart_restores_result_but_not_reference(calibration):
    """Native settings roundtrip does not arm or restore the stage reference."""
    thing, model = calibration
    old = thing.calibrate(prepared=True)
    settings_file = Path(thing._thing_server_interface.settings_file_path)
    restarted = create_thing_without_server(
        ZBacklashCalibration,
        mock_all_slots=True,
        settings_folder=str(settings_file.parent),
    )
    restarted.load_settings()
    assert restarted.last_calibration == old
    assert restarted.parameters == thing.parameters
    restarted._stage.validate_path.side_effect = ValueError("No reference")
    assert not restarted.readiness["ready"]
    assert model.z == 0


def test_atomic_save_failure_retains_previous_file_and_memory(calibration, monkeypatch):
    """A failed final rename cannot replace the successful calibration."""
    thing, model = calibration
    old = thing.calibrate(prepared=True)
    target = Path(thing._thing_server_interface.settings_file_path)
    before = target.read_bytes()
    import openflexure_microscope_server.things.focus.z_backlash as module

    replace = module.os.replace

    def fail_settings(source, destination):
        if Path(destination) == target:
            raise OSError("disk failure")
        return replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_settings)
    with pytest.raises(OSError, match="disk failure"):
        thing.calibrate(prepared=True)
    assert thing.last_calibration == old
    assert target.read_bytes() == before
    assert model.z == 0
    assert len(list(target.parent.iterdir())) == 1


def test_cancellation_stops_without_return_or_profile(calibration):
    """Cancel does not queue a return movement or save partial evidence as valid."""
    thing, model = calibration

    def cancel(_metadata):
        if model.captures == 2:
            get_cancel_event().set()

    model.on_capture = cancel
    with pytest.raises(lt.exceptions.InvocationCancelledError):
        thing.calibrate(prepared=True)
    assert model.z == -10
    assert model.captures == 2
    assert thing.last_calibration is None
    assert thing.progress["phase"] == "stopped"


@pytest.mark.parametrize("change", ["light", "duplicate", "exposure", "geometry"])
def test_changed_frame_conditions_stop_without_retry(calibration, change):
    """A captured frame must still correspond to the frozen WHITE configuration."""
    thing, model = calibration

    def change_frame(metadata):
        if model.captures != 2:
            return
        if change == "light":
            thing._illumination.state.mode = "green"
        elif change == "duplicate":
            metadata["exposure_start_ns"] = 2_000_000_000
        elif change == "exposure":
            metadata["exposure_time_us"] = 1000
        else:
            metadata["scaler_crop"] = [1, 0, 64, 64]

    model.on_capture = change_frame
    with pytest.raises(ValueError, match="WHITE|geometry"):
        thing.calibrate(prepared=True)
    assert model.captures == 2
    assert model.z == -10
    assert thing.last_calibration is None


def test_no_automatic_preload_expansion(calibration):
    """An undersized experiment refuses instead of growing the allowed travel."""
    thing, model = calibration
    thing.set_parameters(ZCalibrationSettings(preload_um=2.0))
    with pytest.raises(ValueError, match="no automatic expansion"):
        thing.calibrate(prepared=True)
    thing._stage.move_z_with_preload.assert_not_called()
    assert thing.last_calibration is None
    assert max(abs(z) for _x, _y, z in model.moves) <= 12


def test_changed_camera_makes_saved_profile_incompatible(calibration):
    """A saved result survives but is not silently reused for another geometry."""
    thing, _model = calibration
    old = thing.calibrate(prepared=True)
    thing._cam.focus_configuration = {"sensor": "synthetic", "roi": [0, 0, 80, 64]}
    assert thing.calibration_status["status"] == "incompatible"
    assert thing.last_calibration == old


def test_manifest_matches_actual_parameters_and_action(calibration):
    """One shared manifest describes the independent action, without fake endpoints."""
    thing, model = calibration
    manifest = thing.manifest
    assert manifest.id == "z_backlash"
    assert set(manifest.parameter_refs) == set(ZCalibrationSettings.model_fields)
    assert [stage.id for stage in manifest.stages] == [
        "preflight",
        "reference",
        "measurement",
        "estimate",
        "validation",
        "save",
    ]
    assert all(stage.action == "calibrate" for stage in manifest.stages)
    assert not model.moves


@pytest.mark.parametrize(
    "changes",
    [
        {"span_um": 21.0},
        {"step_um": 0.0},
        {"cycles": 1},
        {"roi": {"x": 0.8, "width": 0.5}},
        {"maximum_frames": 10},
        {"span_um": float("inf")},
        {"preferred_final_approach_sign": True},
    ],
)
def test_bad_sampling_parameters_rejected(changes):
    """API settings cannot create invalid or unbounded sampling."""
    with pytest.raises(ValidationError):
        ZCalibrationSettings(**changes)


@pytest.mark.parametrize(
    "scores",
    [
        [1, 1, 1, 1, 1],
        [5, 4, 3, 2, 1],
        [1, 2, 3, 4, 5],
    ],
)
def test_unresolved_focus_curves_rejected(scores):
    """Flat curves and edge maxima are not focus references."""
    samples = [
        FocusSample(
            z_um=float(i), time_s=float(i), score=float(score), frame=f"{i}.png"
        )
        for i, score in enumerate(scores)
    ]
    with pytest.raises(ValueError, match="focus|texture"):
        focus_peak(samples, ZCalibrationSettings())


def test_abba_removes_linear_drift_and_checks_order():
    """Matched interpolation cancels linear common drift in the four WHITE peaks."""
    settings = ZCalibrationSettings()
    peaks = [
        FocusPeak(z_um=z, time_s=t, uncertainty_um=0.1, quality_score=0.9)
        for z, t in [(2.0, 1.0), (-1.8, 2.0), (-1.6, 3.0), (2.6, 4.0)]
    ]
    observation = abba_observation("cycle", peaks, settings)
    assert observation.direction_effect_um == pytest.approx(-4)
    with pytest.raises(ValueError, match="order"):
        abba_observation("bad", list(reversed(peaks)), settings)


@pytest.mark.parametrize("units_per_mm", [1000.0, 2000.0, 10000.0])
def test_calibration_uses_physical_units_and_keeps_xyz_start(calibration, units_per_mm):
    """Changing controller scale must not change the measured physical backlash."""
    thing, model = calibration
    model.hardware["axes"]["z"]["units_per_mm"] = units_per_mm
    model.x, model.y, model.z = 123, -42, 90
    model.origin_units = model.z
    result = thing.calibrate(prepared=True)
    assert result["estimate"]["backlash_um"] == pytest.approx(4, abs=0.05)
    assert result["start_position_units"] == [123, -42, 90]
    assert (model.x, model.y, model.z) == (123, -42, 90)
    assert all(x == 123 and y == -42 for x, y, _z in model.moves)


def test_competing_focus_peaks_are_rejected():
    """Two distinct focus maxima cannot become a precise backlash calibration."""
    samples = [
        FocusSample(z_um=float(i), time_s=float(i), score=float(score), frame=str(i))
        for i, score in enumerate([1, 5, 2, 5, 1])
    ]
    with pytest.raises(ValueError, match="competing"):
        focus_peak(samples, ZCalibrationSettings())


def test_abba_rejects_inconsistent_negative_repeats():
    """Large opposite errors in the middle pair must not cancel in their mean."""
    peaks = [
        FocusPeak(z_um=z, time_s=float(i), uncertainty_um=1.0, quality_score=0.9)
        for i, z in enumerate([2.0, -8.0, 4.0, 2.0])
    ]
    with pytest.raises(ValueError, match="negative approach"):
        abba_observation("cycle", peaks, ZCalibrationSettings())
