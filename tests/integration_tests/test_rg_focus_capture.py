"""JPEG acquisition evidence and explicit pre-effects rejection of the old AF domain."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.rg.rg_flat_field import JPEG_DOMAIN
from openflexure_microscope_server.focus.rg.rg_focus_model import (
    JPEG_DRAFT_POLICY_SHA256,
    RGFocusMeasurementPolicy,
    jpeg_measurement_draft_policy,
)
from openflexure_microscope_server.things.focus import rg_flat_field as ff_module
from openflexure_microscope_server.things.focus import rg_focus as focus_module
from openflexure_microscope_server.things.focus.rg_focus import RGFocus
from openflexure_microscope_server.things.illumination import LightState
from tests.integration_tests.test_rg_flat_field_api import prepare_settings

pytest_plugins = ("tests.integration_tests.test_rg_flat_field_api",)


def _run_focus(env, action, **kwargs):
    """Invoke one R/G focus action through the real HTTP lifecycle."""
    return env.poll_action(env.start_action("rg_focus", action, kwargs))


def test_focus_contract_actions_and_readiness_persist_without_effects(optical_server):
    """HTTP migration and atomic contract setup persist while hardware stays idle."""
    server, model = optical_server
    with server() as env:
        before = (model.frames, list(model.commands), list(model.position), model.mode)
        initial = env.client.get("/rg_focus/readiness")
        assert initial.is_success
        assert initial.json()["policy_state"] == "legacy"
        assert not initial.json()["ready"]
        migrated = _run_focus(env, "use_jpeg_measurement_preset")
        assert migrated["status"] == "completed", migrated
        assert migrated["output"] == jpeg_measurement_draft_policy().model_dump(
            mode="json"
        )
        applied = _run_focus(env, "apply_demo_focus_contract")
        assert applied["status"] == "completed", applied
        readiness = env.client.get("/rg_focus/readiness").json()
        assert readiness["effective_policy_sha256"] == JPEG_DRAFT_POLICY_SHA256
        assert readiness["policy_match"]
        assert readiness["demo_contract_match"]
        assert readiness["policy_state"] == "draft_uncommissioned"
        assert not readiness["measure_ready"]
        assert not readiness["calibration_ready"]
        assert not readiness["autofocus_ready"]
        assert readiness["calibration_plan"]["pair_count"] == 20
        assert (model.frames, model.commands, model.position, model.mode) == before
    with server() as restarted:
        readiness = restarted.client.get("/rg_focus/readiness").json()
        assert readiness["policy_match"]
        assert readiness["demo_contract_match"]
        assert readiness["policy_state"] == "draft_uncommissioned"
        assert not readiness["ready"]
        assert (model.frames, model.commands, model.position, model.mode) == before


def test_public_pair_timings_and_owned_roi(optical_server):
    """Archive the full JPEG, return an independent ROI, and time existing calls."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        flat = env.get_thing_by_name("rg_flat_field")
        output = flat.capture_pair(prepared=True)
        assert set(output) == {
            "id",
            "directory",
            "flat_field_applied",
            "frames",
            "white_restored",
        }
        report = json.loads(
            (Path(output["directory"]) / "acquisition.json").read_text()
        )
        timing = report["timing"]
        assert timing["inclusive_phases"] == {
            "white_restore": [
                "light_select",
                "verified_light_reuse",
                "controller_readback",
                "combined_readback",
            ]
        }
        assert {name: value["count"] for name, value in timing["phases"].items()} == {
            "controller_readback": 3,
            "light_readback": 1,
            # Three post-frame checks plus the final WHITE confirmation. The
            # atomic readiness snapshot is reused at the acquisition boundary.
            "combined_readback": 4,
            "light_select": 3,
            "verified_light_reuse": 1,
            "light_extinguish": 0,
            "light_transition": 0,
            "camera_frame": 3,
            "jpeg_save": 3,
            "json_save": 3,
            "owned_copy": 3,
            "white_restore": 1,
        }
        assert (
            sum(
                value["seconds"]
                for name, value in timing["phases"].items()
                if name != "white_restore"
            )
            <= report["elapsed_s"]
        )
        with flat._session(True) as run:
            run.select("red")
            planes, rgb, info = run.frame("owned-roi")
            saved = rgb.copy()
            run.select("green")
            run.frame("next-exposure")
            np.testing.assert_array_equal(rgb, saved)
            np.testing.assert_array_equal(planes, np.moveaxis(saved, 2, 0))
            assert rgb.flags.owndata
            assert info["geometry"]["image_size"] == [64, 64]
        assert model.mode == "white"


def test_reused_white_cleanup_reselects_after_external_light_change(optical_server):
    """A stale owned WHITE state cannot bypass a fresh cleanup readback."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        flat = env.get_thing_by_name("rg_flat_field")
        with flat._session(True) as run:
            run.select("red")
            run.select("white")
            model.mode = "red"
            model.print_time += 1
        assert model.mode == "white"
        assert model.commands == ["red", "white", "white"]


@pytest.mark.parametrize(
    "failure",
    [
        "frame",
        "jpeg_save",
        "metadata_save",
        "report_save",
        "processing",
        "external_move",
        "white_restore",
    ],
)
def test_acquisition_failure_cleanup_preserves_profiles(  # noqa: C901
    optical_server, monkeypatch, failure
):
    """Storage and camera failures retain real WHITE cleanup without recovery motion."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        flat = env.get_thing_by_name("rg_flat_field")
        if failure == "frame":
            model.fail_at = 2
        elif failure == "jpeg_save":
            original_open = Path.open

            def failed_open(path, *args, **kwargs):
                if path.suffix == ".jpg":
                    raise OSError("JPEG disk failure")
                return original_open(path, *args, **kwargs)

            monkeypatch.setattr(Path, "open", failed_open)
        elif failure in ("metadata_save", "report_save"):
            original_save = ff_module.write_json

            def failed_save(path, value):
                if (failure == "metadata_save" and path.name.startswith("frame-")) or (
                    failure == "report_save" and path.name == "acquisition.json"
                ):
                    raise OSError("JSON disk failure")
                return original_save(path, value)

            monkeypatch.setattr(ff_module, "write_json", failed_save)
        elif failure == "processing":
            model.on_capture = lambda: setattr(
                model, "processing_gain", 1 + model.frames / 10
            )
        elif failure == "external_move":
            model.on_capture = lambda: model.position.__setitem__(0, 1.0)
        else:
            original_select = model.select

            def failed_restore(mode):
                if mode == "white" and model.frames == 3:
                    return LightState(available=False, mode="unknown", channels={})
                return original_select(mode)

            monkeypatch.setattr(model, "select", failed_restore)
        with pytest.raises((OSError, ValueError, TimeoutError)):
            flat.capture_pair(prepared=True)
        assert flat.profiles == {}
        if failure != "white_restore":
            assert model.mode == "white"
        if failure != "report_save":
            report = json.loads(
                next(Path(flat.data_dir).glob("*/acquisition.json")).read_text()
            )
            assert report["error"]
            assert report["timing"]["phases"]["white_restore"]["count"] == 1
            assert all(
                math.isfinite(value["seconds"]) and value["seconds"] >= 0
                for value in report["timing"]["phases"].values()
            )


def test_acquisition_report_failure_preserves_an_earlier_capture_failure(
    optical_server, monkeypatch
):
    """Report persistence cannot replace the exact failure already being raised."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        flat = env.get_thing_by_name("rg_flat_field")
        original = TimeoutError("original camera failure")
        monkeypatch.setattr(
            model,
            "capture",
            lambda _timeout: (_ for _ in ()).throw(original),
        )
        original_save = ff_module.write_json
        report_failure = OSError("acquisition report disk failure")

        def failed_report(path, value):
            if path.name == "acquisition.json":
                raise report_failure
            return original_save(path, value)

        monkeypatch.setattr(ff_module, "write_json", failed_report)
        errors = []
        monkeypatch.setattr(flat.logger, "error", lambda *args: errors.append(args))
        with pytest.raises(TimeoutError, match="original camera failure") as caught:
            flat.capture_pair(prepared=True)
        assert caught.value is original
        assert any(
            "Acquisition report persistence failed: acquisition report disk failure"
            in note
            for note in caught.value.__notes__
        )
        assert errors == [("Cannot save R/G acquisition report: %s", report_failure)]
        assert flat.progress["phase"] == "stopped"


def test_lone_acquisition_report_failure_propagates(optical_server, monkeypatch):
    """A report write failure remains fatal when acquisition and cleanup succeeded."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        flat = env.get_thing_by_name("rg_flat_field")
        original_save = ff_module.write_json
        report_failure = OSError("acquisition report disk failure")

        def failed_report(path, value):
            if path.name == "acquisition.json":
                raise report_failure
            return original_save(path, value)

        monkeypatch.setattr(ff_module, "write_json", failed_report)
        with pytest.raises(OSError, match="acquisition report disk failure") as caught:
            flat.capture_pair(prepared=True)
        assert caught.value is report_failure
        assert model.mode == "white"
        assert flat.progress["phase"] == "stopped"


def test_legacy_focus_policy_refuses_before_hardware_or_capture(tmp_path):
    """A historical policy remains readable but cannot enter the JPEG capture path."""
    focus = create_thing_without_server(RGFocus, mock_all_slots=True)
    with pytest.raises(ValueError, match="explicit processed-JPEG"):
        focus._capture_pair(tmp_path, 1)
    assert focus._stage.mock_calls == []
    assert focus._cam.mock_calls == []
    assert focus._rg_flat_field.mock_calls == []
    assert list(tmp_path.iterdir()) == []


def test_private_focus_handoff_is_owned_jpeg_and_one_session(
    optical_server, monkeypatch
):
    """Runtime focus owns JPEG8 buffers without archiving transient colour JPEGs."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        flat = env.get_thing_by_name("rg_flat_field")
        monkeypatch.setattr(
            flat._cam,
            "capture_linear_frame",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("RAW API used")
            ),
            raising=False,
        )
        monkeypatch.setattr(
            flat,
            "capture_pair",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("alternative acquisition route used")
            ),
        )
        handoff = flat._capture_focus_pair(prepared=True)
        assert model.frames == 3
        assert model.commands == ["red", "green", "white"]
        assert handoff.capture["white_restored"] is True
        assert set(handoff.metadata) == {"white", "red", "green"}
        for mode in ("red", "green"):
            illumination = handoff.metadata[mode]["illumination"]
            assert illumination["mode"] == mode
            assert (
                illumination["transition_confirmed_ns"] >= illumination["confirmed_ns"]
            )
            assert illumination["confirmed_on_window_ms"] >= 0
        assert handoff.metadata["white"]["illumination"]["transitioned_to"] == "red"
        assert handoff.metadata["red"]["illumination"]["transitioned_to"] == "green"
        assert handoff.metadata["green"]["illumination"]["transitioned_to"] == "white"
        assert all(
            array.flags.owndata and not array.flags.writeable
            for array in (
                handoff.white_rgb,
                handoff.red_source_jpeg8,
                handoff.green_source_jpeg8,
            )
        )
        directory = Path(handoff.capture["directory"])
        assert len(list(directory.glob("*.jpg"))) == 1
        assert len(list(directory.glob("frame-*.json"))) == 3
        assert handoff.metadata["white"]["jpeg_persisted"] is True
        assert handoff.metadata["white"]["jpeg_file"] is not None
        for mode in ("red", "green"):
            assert handoff.metadata[mode]["jpeg_persisted"] is False
            assert handoff.metadata[mode]["jpeg_file"] is None
        archived = flat._capture_focus_pair(prepared=True, persist_colour_frames=True)
        archive_directory = Path(archived.capture["directory"])
        assert len(list(archive_directory.glob("*.jpg"))) == 3
        assert all(
            archived.metadata[mode]["jpeg_persisted"]
            for mode in ("white", "red", "green")
        )


@pytest.mark.parametrize("timing_save_fails", [False, True])
def test_failed_capture_timing_preserves_original_failure(
    tmp_path, mocker, timing_save_fails
):
    """The existing focus timer cannot mask a future supported capture failure."""
    focus = create_thing_without_server(RGFocus, mock_all_slots=True)
    focus.measurement_parameters = RGFocusMeasurementPolicy(
        measurement_domain=JPEG_DOMAIN
    )
    original = TimeoutError("original capture failure")
    focus._rg_flat_field._capture_focus_pair.side_effect = original
    if timing_save_fails:
        mocker.patch.object(
            focus_module, "write_json", side_effect=OSError("timing save failure")
        )
    with pytest.raises(TimeoutError, match="original capture failure") as caught:
        focus._capture_pair(tmp_path, 1)
    assert caught.value is original
    focus._stage.move_absolute_in_segments.assert_not_called()


def test_acquisition_timers_are_inclusive_and_count_failed_calls(monkeypatch):
    """Nested cleanup is recorded rather than silently double-summed."""
    run = ff_module.Acquisition.__new__(ff_module.Acquisition)
    run.timings = {
        name: {"seconds": 0.0, "count": 0}
        for name in ("white_restore", "light_select", "controller_readback")
    }
    clock = [10.0]
    monkeypatch.setattr(ff_module.time, "monotonic", lambda: clock[0])
    with run.timed("white_restore"):
        with run.timed("light_select"):
            clock[0] += 0.25
        with pytest.raises(TimeoutError), run.timed("controller_readback"):  # noqa: PT012
            clock[0] += 0.5
            raise TimeoutError("readback failed")
        clock[0] += 0.125
    assert run.timings == {
        "white_restore": {"seconds": 0.875, "count": 1},
        "light_select": {"seconds": 0.25, "count": 1},
        "controller_readback": {"seconds": 0.5, "count": 1},
    }
