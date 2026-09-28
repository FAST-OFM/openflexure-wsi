"""Real OFM HTTP actions/settings with simulated camera and light, no physical devices."""

import copy
import hashlib
import json
import os
import time
from io import BytesIO
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

import labthings_fastapi as lt

from openflexure_microscope_server.acquisition.measurement_preview import (
    MeasurementPreview,
)
from openflexure_microscope_server.focus.rg.rg_flat_field import (
    JPEG_DOMAIN,
    FlatFieldSettings,
    processing_binding,
)
from openflexure_microscope_server.focus.rg.rg_simultaneous import (
    SimultaneousShiftMeasurement,
)
from openflexure_microscope_server.things import OFMThing
from openflexure_microscope_server.things.camera.simulation import SimulatedCamera
from openflexure_microscope_server.things.focus import (
    rg_simultaneous as simultaneous_module,
)
from openflexure_microscope_server.things.focus.rg_flat_field import RGFlatField
from openflexure_microscope_server.things.focus.rg_focus import RGFocus
from openflexure_microscope_server.things.illumination import (
    IlluminationError,
    LightState,
    MoonrakerIllumination,
)
from openflexure_microscope_server.things.stage.moonraker import MoonrakerStage

from ..shared_utils.lt_test_utils import LabThingsTestEnv
from ..unit_tests.flat_field_process_stub import (
    external_flat_apply,
    external_flat_fit,
    external_flat_validate,
)


class OpticalModel:
    """Only fresh JPEG, light and controller state; any motor call is an error."""

    def __init__(self):
        """Prepare a static empty field and independent frame counter."""
        self.preview = MeasurementPreview()
        self.processing_gain = 1.0
        self.mode = "white"
        self.commands = []
        self.frames = 0
        self.print_time = 0
        self.fail_at = None
        self.on_capture = None
        self.rgb_override = None
        self.motors_enabled = False
        self.reference_valid = False
        self.binding = {
            "measurement_space": JPEG_DOMAIN,
            "geometry": {
                "measurement_space": JPEG_DOMAIN,
                "sensor": "imx477",
                "sensor_resolution": [4056, 3040],
                "sensor_crop": [0, 0, 3040, 3040],
                "plane_size": [64, 64],
                "image_size": [64, 64],
                "white_size": [64, 64],
                "bit_depth": 8,
                "white_level": 255,
                "channel_order": ["R", "G", "B"],
                "array_axes": "height,width,channel",
                "pixel_to_sensor": [[47.5, 0, 23.25], [0, 47.5, 23.25]],
                "white_to_sensor": [[47.5, 0, 23.25], [0, 47.5, 23.25]],
                "common_plane_to_sensor": [[47.5, 0, 23.25], [0, 47.5, 23.25]],
            },
            "exposure_time_us": 4000,
            "analogue_gain": 1.0,
            "colour_gains": [1, 1],
            "camera": {"sensor": "synthetic"},
        }
        self.raw_binding = {
            "geometry": {
                "sensor": "imx477",
                "bit_depth": 12,
                "white_level": 4095,
                "raw_format": "SBGGR12_CSI2P",
                "raw_size": [4056, 3040],
                "raw_stride": 6112,
                "bayer_order": "BGGR",
                "channel_order": ["B", "G0", "G1", "R"],
                "channel_offsets_xy": [[0, 0], [1, 0], [0, 1], [1, 1]],
                "sensor_crop": [0, 0, 64, 64],
                "plane_size": [32, 32],
                "white_size": [64, 64],
            },
            "exposure_time_us": 4000,
            "analogue_gain": 1.0,
            "colour_gains": [1.0, 1.0],
            "camera": {"sensor": "synthetic"},
        }
        self.position = [0.0, 0.0, -0.001]
        yy, xx = np.mgrid[:64, :64]
        self.flat = 100 + 50 * np.exp(-((xx - 32) ** 2 + (yy - 32) ** 2) / 1800)
        self.bad = False
        self.jpeg_frames = []

    def state(self):
        """Klipper coordinates are reported but never modified by this model."""
        result = {
            "state": "ready",
            "pid": 1,
            "gcode_position": self.position.copy(),
            "machine_position": self.position.copy(),
            "homing_origin": [0, 0, 0],
            "enabled": dict.fromkeys(
                ("stepper_x", "stepper_y", "stepper_z"), self.motors_enabled
            ),
            "homed_axes": "",
            "motion_idle": True,
            "print_time": self.print_time,
            "reference_valid": self.reference_valid,
        }
        return SimpleNamespace(state="ready", idle=True, model_dump=lambda: result)

    def light(self):
        """Return gate readback, not a claim about image freshness."""
        channels = {
            name: (
                name in ("red", "green") if self.mode == "mixed" else name == self.mode
            )
            for name in ("red", "green", "white")
        }
        return LightState(
            available=True,
            mode=self.mode,
            channels=channels,
        )

    def select(self, mode):
        """Record existing light intents without stage movement."""
        self.commands.append(mode)
        self.mode = mode
        self.print_time += 1
        return self.light()

    def capture_raw(self, _timeout):
        """Return linear Bayer responses with a well-separated additive mixed frame."""
        self.frames += 1
        yy, xx = np.mgrid[:32, :32]
        texture = 0.7 + 0.3 * ((xx + 2 * yy) % 11) / 10
        dark = np.full((4, 32, 32), 128.0)
        red = np.array([20, 90, 92, 900], dtype=float)[:, None, None] * texture
        green = np.array([12, 720, 710, 80], dtype=float)[:, None, None] * texture
        signal = {
            "off": 0,
            "red": red,
            "green": green,
            "mixed": red + green,
        }.get(self.mode, 0)
        planes = np.rint(dark + signal).astype(np.uint16)
        start = time.monotonic_ns()
        return (
            planes,
            np.zeros((64, 64, 3), dtype=np.uint8),
            {
                "binding": copy.deepcopy(self.raw_binding),
                "geometry": copy.deepcopy(self.raw_binding["geometry"]),
                "exposure_time_us": 4000,
                "analogue_gain": 1.0,
                "processing": {
                    "digital_gain": 1.0,
                    "colour_gains": [1.0, 1.0],
                    "colour_correction_matrix": [
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ],
                },
                "exposure_start_ns": start,
                "sensor_timestamp_ns": start + 4_000_000,
                "boundary_ns": start - 100,
                "frame_duration_us": 33333,
            },
        )

    def capture(self, _timeout):
        """Encode a distinct fresh exposure and decode precisely those JPEG bytes."""
        self.frames += 1
        if self.frames == self.fail_at:
            raise TimeoutError("lost frame")
        if self.on_capture:
            self.on_capture()
        planes = np.full((3, 64, 64), 5, dtype=np.float32)
        if self.mode == "white":
            planes[:] = 220
        elif self.mode == "red":
            planes[0] += self.flat
        elif self.mode == "green":
            planes[1] += self.flat
        if self.bad and self.mode in ("red", "green"):
            planes[:, :32] = 255
        planes += np.random.default_rng(self.frames).normal(0, 0.05, planes.shape)
        rgb = np.moveaxis(np.clip(np.rint(planes), 0, 255).astype(np.uint8), 0, 2)
        if self.rgb_override is not None:
            rgb = np.asarray(self.rgb_override(self.mode, rgb), dtype=np.uint8)
        output = BytesIO()
        Image.fromarray(rgb).save(output, format="JPEG", quality=95, subsampling=0)
        payload = output.getvalue()
        with Image.open(BytesIO(payload)) as image:
            decoded = np.array(image.convert("RGB"), copy=True)
        self.jpeg_frames.append(payload)
        start = time.monotonic_ns()
        return (
            payload,
            decoded,
            {
                "binding": copy.deepcopy(self.binding),
                "geometry": self.binding["geometry"],
                "exposure_time_us": 4000,
                "analogue_gain": 1.0,
                "processing": {
                    "digital_gain": self.processing_gain,
                    "colour_gains": [1.0, 1.0],
                    "colour_correction_matrix": [
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ],
                },
                "exposure_start_ns": start,
                "sensor_timestamp_ns": start + 4_000_000,
                "jpeg_sha256": hashlib.sha256(payload).hexdigest(),
                "jpeg_size_bytes": len(payload),
                "timings": {"encode_s": 0.001, "decode_s": 0.001},
                "boundary_ns": start - 100,
                "frame_duration_us": 85335,
            },
        )


@pytest.fixture
def optical_server(tmp_path, monkeypatch):
    """Use actual actions, global lock, persistence and Thing slots."""
    model = OpticalModel()
    monkeypatch.setattr(RGFlatField, "_external_flat_fit", external_flat_fit)
    monkeypatch.setattr(RGFlatField, "_external_flat_apply", external_flat_apply)
    monkeypatch.setattr(RGFlatField, "_external_flat_validate", external_flat_validate)
    from tests.unit_tests.tissue_field_process_stub import external_tissue_field

    monkeypatch.setattr(RGFocus, "_external_tissue_field", external_tissue_field)
    for cls in (MoonrakerStage, MoonrakerIllumination, SimulatedCamera):
        monkeypatch.setattr(cls, "__enter__", lambda self: self)
        monkeypatch.setattr(cls, "__exit__", OFMThing.__exit__)
    monkeypatch.setattr(MoonrakerStage, "_hardware_lock", RLock(), raising=False)
    monkeypatch.setattr(
        MoonrakerStage, "_fetch_state", lambda _self, **_kwargs: model.state()
    )
    monkeypatch.setattr(
        MoonrakerStage,
        "controller_state",
        property(
            lambda _self: {
                **model.state().model_dump(),
                "reference_id": "fixture" if model.reference_valid else None,
                "motion_revision": 0,
                "position": dict(
                    zip(
                        ("x", "y", "z"),
                        (round(value * 1000) for value in model.position),
                        strict=True,
                    )
                ),
            }
        ),
    )
    monkeypatch.setattr(
        MoonrakerIllumination, "state", property(lambda _self: model.light())
    )
    monkeypatch.setattr(
        MoonrakerIllumination, "_read_state", lambda _self: model.light()
    )
    monkeypatch.setattr(
        MoonrakerIllumination,
        "_read_controller_and_light",
        lambda _self: (model.state(), model.light()),
    )
    monkeypatch.setattr(
        MoonrakerIllumination, "set_mode", lambda _self, mode: model.select(mode)
    )
    monkeypatch.setattr(
        MoonrakerIllumination,
        "_extinguish_after_verified",
        lambda _self, _verified, _controller: model.select("off"),
    )
    monkeypatch.setattr(
        MoonrakerIllumination,
        "_transition_after_verified",
        lambda _self, _verified, _controller, mode: model.select(mode),
    )
    monkeypatch.setattr(
        MoonrakerIllumination,
        "_select_red_green_probe",
        lambda _self: model.select("mixed"),
    )

    def verified_mixed(_self, verified, _controller):
        """Preserve the real WHITE-only fast transition contract in the fixture."""
        if verified.mode != "white" or model.mode != "white":
            raise IlluminationError("A live WHITE readback is required")
        return model.select("mixed")

    monkeypatch.setattr(
        MoonrakerIllumination,
        "_select_red_green_probe_after_verified",
        verified_mixed,
    )
    monkeypatch.setattr(
        SimulatedCamera,
        "jpeg_measurement_configuration",
        property(lambda _self: copy.deepcopy(model.binding)),
        raising=False,
    )
    monkeypatch.setattr(
        SimulatedCamera,
        "raw_measurement_configuration",
        property(lambda _self: copy.deepcopy(model.raw_binding)),
        raising=False,
    )
    monkeypatch.setattr(
        SimulatedCamera,
        "capture_jpeg_frame",
        lambda _self, timeout: model.capture(timeout),
        raising=False,
    )
    monkeypatch.setattr(
        SimulatedCamera,
        "capture_linear_frame",
        lambda _self, timeout: model.capture_raw(timeout),
        raising=False,
    )
    monkeypatch.setattr(
        SimulatedCamera, "_measurement_preview", model.preview, raising=False
    )
    things = {
        "camera": "openflexure_microscope_server.things.camera.simulation:SimulatedCamera",
        "dummy": "openflexure_microscope_server.things.stage.dummy:DummyStage",
        "stage": "openflexure_microscope_server.things.stage.moonraker:MoonrakerStage",
        "illumination": "openflexure_microscope_server.things.illumination:MoonrakerIllumination",
        "rg_flat_field": "openflexure_microscope_server.things.focus.rg_flat_field:RGFlatField",
        "rg_simultaneous": {
            "class": "openflexure_microscope_server.things.focus.rg_simultaneous:RGSimultaneous",
            "kwargs": {
                "default_parameters": {"plane_roi": [0, 0, 32, 32]},
                "default_focus_parameters": {"focus_plane_roi": [0, 0, 32, 32]},
            },
        },
        "rg_focus": "openflexure_microscope_server.things.focus.rg_focus:RGFocus",
    }
    settings = tmp_path / "settings"
    settings.mkdir()

    def server():
        return LabThingsTestEnv(
            things=things,
            settings_folder=str(settings),
            application_config={"data_folder": str(tmp_path / "data")},
        )

    return server, model


def run(env, action, **kwargs):
    """Invoke through the real HTTP input validation and invocation lifecycle."""
    return env.poll_action(env.start_action("rg_flat_field", action, kwargs))


def run_simultaneous(env, action, **kwargs):
    """Invoke the independent simultaneous R/G mechanism through HTTP."""
    return env.poll_action(env.start_action("rg_simultaneous", action, kwargs))


def prepare_settings(env):
    """Use a small-image smoothing scale in the optical simulator only."""
    settings = FlatFieldSettings(
        measurement_domain=JPEG_DOMAIN,
        processing_roi=(0, 0, 64, 64),
        minimum_signal_dn=8,
        maximum_dark_signal_dn=32,
        smoothing_sigma_px=2.0,
        average_frames=4,
        validation_frames=3,
    ).model_dump(mode="json")
    response = env.client.post(
        "/rg_flat_field/set_parameters", json={"parameters": settings}
    )
    assert response.is_success, response.text
    assert env.poll_action(response)["status"] == "completed"


def test_calibrate_persist_preview_and_independent_validation(optical_server):
    """Full success remains valid after restart and never depends on motor zero."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        assert env.client.get("/rg_flat_field/readiness").json()["ready"]
        assert set(env.client.get("/rg_flat_field/manifests").json()) == {
            "red",
            "green",
        }
        action = run(env, "calibrate", prepared=True)
        assert action["status"] == "completed", action
        profiles = env.client.get("/rg_flat_field/profiles").json()
        assert set(profiles) == {"red", "green"}
        assert model.mode == "white"
        assert model.position == [0, 0, -0.001]
        assert model.frames == 26
        assert model.commands == ["off"] + ["white", "red", "green"] * 7 + ["white"]
        for mode in ("red", "green"):
            assert profiles[mode]["validated"]
            assert profiles[mode]["method"] == (
                "phase_matched_processed_jpeg_measured_dark"
            )
            assert profiles[mode]["acquisition_sequence"]["order"] == [
                "white",
                "red",
                "green",
            ]
            assert max(profiles[mode]["validation"]["after"]["cv"]) < 0.01
            assert (
                env.client.get(f"/rg_flat_field/preview/{mode}.png").status_code == 200
            )
        saved = copy.deepcopy(profiles)
        assert run(env, "validate", prepared=True)["status"] == "completed"
        assert env.client.get("/rg_flat_field/profiles").json() == saved
        thing = env.get_thing_by_name("rg_flat_field")
        model.select("red")
        _jpeg, rgb, info = model.capture(1)
        planes = np.moveaxis(rgb, 2, 0)
        info["binding"] = processing_binding(info["binding"], thing.parameters)
        info["geometry"] = info["binding"]["geometry"]
        with pytest.raises(ValueError, match="illumination"):
            thing.correct_frame("red", planes, info)
        info.update(light="red", illumination={"mode": "red"})
        plane, mask = thing.correct_frame("red", planes, info)
        assert plane.shape == (64, 64)
        assert mask.any()
        model.select("white")
    with server() as restarted:
        assert restarted.client.get("/rg_flat_field/profiles").json() == saved
        statuses = restarted.client.get("/rg_flat_field/calibration_status").json()
        assert all(value["status"] == "valid" for value in statuses.values())


def test_manual_colour_preview_uses_checked_map_and_keeps_normal_stream_white(
    optical_server,
):
    """Illumination diagnostics are corrected snapshots, never RED/GREEN MJPEG frames."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        assert run(env, "calibrate", prepared=True)["status"] == "completed"
        result = run(env, "select_diagnostic_mode", mode="red")
        assert result["status"] == "completed", result
        output = result["output"]
        assert output["mode"] == "red"
        assert output["flat_field_applied"]
        assert output["profile_id"]
        timing = output["correction_timing"]
        assert all(np.isfinite(value) and value >= 0 for value in timing.values())
        assert sum(
            value for key, value in timing.items() if key != "total_s"
        ) == pytest.approx(timing["total_s"])
        assert output["selected_mode_retained"]
        assert model.mode == "red"
        assert model.preview.status["holding"]
        image = env.client.get(output["image_href"])
        assert image.status_code == 200
        assert image.headers["cache-control"] == "no-store"
        assert image.content.startswith(b"\x89PNG")
        result = run(env, "select_diagnostic_mode", mode="white")
        assert result["status"] == "completed", result
        assert model.mode == "white"
        assert not model.preview.status["active"]


def test_manual_colour_preview_is_explicitly_uncorrected_without_a_map(optical_server):
    """The operator may inspect a colour before calibration without a false Applied label."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        result = run(env, "select_diagnostic_mode", mode="green")
        assert result["status"] == "completed", result
        output = result["output"]
        assert not output["flat_field_applied"]
        assert output["profile_id"] is None
        assert "No saved GREEN flat-field" in output["correction_reason"]
        assert env.client.get(output["image_href"]).status_code == 200
        assert model.mode == "green"
        assert run(env, "select_diagnostic_mode", mode="white")["status"] == "completed"


@pytest.mark.parametrize(
    "failure", ["frame", "bad_field", "cancel", "external_move", "exposure"]
)
def test_failed_repeat_keeps_previous_maps_and_restores_white(optical_server, failure):
    """No invalid profile replaces accepted maps; failures do not trigger stage recovery."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        assert run(env, "calibrate", prepared=True)["status"] == "completed"
        saved = env.client.get("/rg_flat_field/profiles").json()
        if failure == "frame":
            model.fail_at = model.frames + 6
        elif failure == "bad_field":
            model.bad = True
        elif failure == "cancel":

            def cancel():
                raise lt.exceptions.InvocationCancelledError("cancelled by test")

            model.on_capture = cancel
        elif failure == "external_move":
            model.on_capture = lambda: model.position.__setitem__(0, 1.0)
        else:
            model.on_capture = lambda: model.binding.__setitem__("analogue_gain", 2.0)
        result = run(env, "calibrate", prepared=True)
        assert result["status"] in ("error", "cancelled"), result
        assert env.client.get("/rg_flat_field/profiles").json() == saved
        assert model.mode == "white"
        assert all(
            command in ("white", "off", "red", "green") for command in model.commands
        )


def test_empty_confirmation_bad_input_and_compatibility(optical_server):
    """No implicit calibration, auto-arm/zero, or silent use of another camera mode."""
    server, model = optical_server
    with server() as env:
        assert run(env, "calibrate")["status"] == "error"
        assert not model.commands
        response = env.client.post(
            "/rg_flat_field/calibrate", json={"prepared": True, "modes": ["white"]}
        )
        assert response.status_code == 422
        assert not model.commands
        prepare_settings(env)
        assert (
            run(env, "calibrate", prepared=True, modes=["red"])["status"] == "completed"
        )
        profiles = env.client.get("/rg_flat_field/profiles").json()
        assert list(profiles) == ["red"]
        thing = env.get_thing_by_name("rg_flat_field")
        legacy = copy.deepcopy(profiles["red"])
        legacy["method"] = "native_raw_measured_dark"
        with pytest.raises(ValueError, match="Camera, exposure"):
            thing._compatible(legacy, "red")
        model.binding["exposure_time_us"] = 8000
        status = env.client.get("/rg_flat_field/calibration_status").json()
        assert status["red"]["status"] == "incompatible"
        assert status["green"]["status"] == "not_calibrated"
        assert run(env, "validate", prepared=True, modes=["red"])["status"] == "error"


def test_map_corruption_and_failed_atomic_save_preserve_pointer(
    optical_server, monkeypatch
):
    """Corrupt maps are refused at use; an unsuccessful settings save restores memory."""
    server, _model = optical_server
    with server() as env:
        prepare_settings(env)
        assert run(env, "calibrate", prepared=True)["status"] == "completed"
        thing = env.get_thing_by_name("rg_flat_field")
        saved = copy.deepcopy(thing.profiles)
        path = thing._directory(saved["red"]) / "red.npz"
        contents = bytearray(path.read_bytes())
        contents[-1] ^= 1
        path.write_bytes(contents)
        with pytest.raises(ValueError, match="checksum"):
            thing._load_maps("red")

        def failed_save():
            raise OSError("disk full")

        monkeypatch.setattr(thing, "save_settings", failed_save)
        with pytest.raises(OSError, match="disk full"):
            thing._commit("profiles", {})
        assert thing.profiles == saved


def test_diagnostic_pair_has_three_fresh_frames_without_claiming_correction(
    optical_server,
):
    """JPEG diagnostics use the same owner and restore WHITE."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        result = run(env, "capture_pair", prepared=True)
        assert result["status"] == "completed", result
        assert model.frames == 3
        assert model.commands == ["red", "green", "white"]
        assert env.client.get("/rg_flat_field/profiles").json() == {}


@pytest.mark.parametrize("referenced", [False, True])
def test_simultaneous_raw_probe_is_isolated_and_restores_white(
    optical_server, monkeypatch, referenced
):
    """The experimental path persists four RAW states and changes no calibration."""
    server, model = optical_server
    model.reference_valid = referenced
    model.motors_enabled = referenced
    fast_restores = []

    def transition(_self, _verified, controller, mode):
        # Match _light_operation: a supplied snapshot requires a real reference.
        if controller is not None and not model.reference_valid:
            raise IlluminationError(
                "Controller connection changed; set a fresh local zero"
            )
        fast_restores.append(mode)
        return model.select(mode)

    monkeypatch.setattr(MoonrakerIllumination, "_transition_after_verified", transition)
    monkeypatch.setattr(
        MoonrakerStage,
        "_expected_reference_state",
        lambda _self: model.state() if model.reference_valid else None,
    )
    with server() as env:
        prepare_settings(env)
        result = run_simultaneous(env, "capture_probe", prepared=True)
        assert result["status"] == "completed", result
        output = result["output"]
        assert output["experimental"]
        assert not output["profile_changed"]
        assert [frame["light"] for frame in output["frames"]] == [
            "off",
            "red",
            "green",
            "mixed",
        ]
        assert model.commands == ["off", "red", "green", "mixed", "white"]
        assert model.mode == "white"
        assert model.reference_valid is referenced
        assert model.motors_enabled is referenced
        assert fast_restores == (["white"] if referenced else [])
        assert env.client.get("/rg_flat_field/profiles").json() == {}
        assert env.client.get("/rg_simultaneous/profile").json() is None
        directory = Path(output["directory"])
        report = json.loads((directory / "acquisition.json").read_text())
        assert report["timing"]["phases"]["raw_save"]["count"] == 4
        # Four post-frame checks plus final WHITE confirmation. The already
        # atomic readiness snapshot is reused, not queried again in Acquisition.
        assert report["timing"]["phases"]["combined_readback"]["count"] == 5
        for frame in output["frames"]:
            path = directory / frame["raw_file"]
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == frame["raw_sha256"]
            with np.load(path, allow_pickle=False) as archive:
                assert archive["planes"].shape == (4, 32, 32)


def test_simultaneous_raw_calibration_uses_memory_and_keeps_jpeg_profiles(
    optical_server,
):
    """Accepted spectral maps persist separately; working JPEG profiles stay untouched."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        parameters = env.client.get("/rg_simultaneous/parameters").json()
        parameters.update(
            plane_roi=[0, 0, 32, 32],
            fit_cycles=2,
            validation_cycles=2,
            brightness_id="test-r150-g150",
        )
        configured = run_simultaneous(env, "set_parameters", parameters=parameters)
        assert configured["status"] == "completed", configured
        result = run_simultaneous(
            env,
            "calibrate",
            prepared=True,
        )
        assert result["status"] == "completed", result
        profile = result["output"]
        assert profile["validated"]
        assert profile["binding"]["brightness_id"] == "test-r150-g150"
        assert profile["working_separate_rg_changed"] is False
        assert profile["fit"]["signature_condition_number"] < 2
        assert profile["validation"]["mixed_reconstruction_residual_p95"] < 0.03
        assert env.client.get("/rg_flat_field/profiles").json() == {}
        assert env.client.get("/rg_simultaneous/profile").json() == profile
        assert env.client.get("/rg_simultaneous/status").json()["status"] == "valid"
        directory = (
            Path(env.get_thing_by_name("rg_simultaneous").data_dir) / profile["id"]
        )
        assert (directory / "spectral-flat-field.npz").is_file()
        assert (directory / "validation.png").is_file()
        assert not list(directory.glob("raw-*.npz"))
        assert len(list(directory.glob("raw-*.json"))) == 12
        assert model.mode == "white"
        assert model.commands == [
            "off",
            "red",
            "green",
            "red",
            "green",
            "red",
            "green",
            "red",
            "green",
            "mixed",
            "white",
        ]


def test_simultaneous_map_digest_is_reused_until_file_identity_changes(
    optical_server, monkeypatch
):
    """A stable 60 MB map is hashed once; any metadata change forces revalidation."""
    server, _model = optical_server
    with server() as env:
        prepare_settings(env)
        result = run_simultaneous(env, "calibrate", prepared=True)
        assert result["status"] == "completed", result
        profile = result["output"]
        thing = env.get_thing_by_name("rg_simultaneous")
        path = thing._map_path(profile)
        original_hash = simultaneous_module.file_hash
        hashed = []

        def recording_hash(candidate):
            hashed.append(candidate)
            return original_hash(candidate)

        monkeypatch.setattr(simultaneous_module, "file_hash", recording_hash)
        thing._verified_maps_cache_key = None
        assert thing._compatible(profile) == path
        assert thing._compatible(profile) == path
        assert hashed == [path]

        time.sleep(0.01)
        path.touch()
        assert thing._compatible(profile) == path
        assert hashed == [path, path]


def test_simultaneous_focus_calibration_owns_curve_and_returns_z(  # noqa: C901, PLR0915
    optical_server, monkeypatch
):
    """The isolated focus grid uses fixed windows and never changes separate R/G."""
    server, model = optical_server
    position_units = [0, 0, 0]
    moves = []

    monkeypatch.setattr(
        MoonrakerStage,
        "hardware_settings",
        property(
            lambda _self: {"axes": {"z": {"units_per_mm": 1000, "direction_sign": 1}}}
        ),
    )
    monkeypatch.setattr(
        MoonrakerStage,
        "get_xyz_position",
        lambda _self: tuple(position_units),
    )
    monkeypatch.setattr(MoonrakerStage, "validate_path", lambda _self, _path: None)

    def move_z(_self, target, preload, approach_sign):
        moves.append((target - approach_sign * preload, target))
        position_units[2] = target
        model.position[2] = target / 1000
        model.print_time += 2

    monkeypatch.setattr(MoonrakerStage, "move_z_with_preload", move_z)

    with server() as env:
        prepare_settings(env)
        flat_result = run_simultaneous(env, "calibrate", prepared=True)
        assert flat_result["status"] == "completed", flat_result
        flat_profile = env.client.get("/rg_simultaneous/profile").json()

        model.motors_enabled = True
        model.reference_valid = True
        original_capture_raw = model.capture_raw
        size = 32
        yy, xx = np.mgrid[:size, :size]
        noise = np.random.default_rng(29).normal(0, 1, (size, size)).astype(np.float32)
        tissue = noise - 0.35 * np.roll(noise, 1, axis=0)
        tissue = (tissue - tissue.min()) / (tissue.max() - tissue.min())
        red_component = 0.25 + 0.65 * tissue
        source_shading = 0.7 + 0.3 * ((xx + 2 * yy) % 11) / 10
        red_response = (
            np.array([20, 90, 92, 900], dtype=float)[:, None, None] * source_shading
        )
        green_response = (
            np.array([12, 720, 710, 80], dtype=float)[:, None, None] * source_shading
        )

        def capture_focus_raw(timeout):
            _planes, preview, info = original_capture_raw(timeout)
            if model.mode != "mixed":
                return _planes, preview, info
            green_component = np.roll(red_component, position_units[2], axis=1)
            dark = np.full((4, size, size), 128.0)
            planes = (
                dark + red_response * red_component + green_response * green_component
            )
            return np.rint(planes).astype(np.uint16), preview, info

        model.capture_raw = capture_focus_raw

        def measured_shift(
            _components,
            _valid,
            _settings,
            boxes,
            *,
            adaptive_window_selection=True,
        ):
            if boxes is None:
                assert adaptive_window_selection
                patch_count = _settings.core.maximum_patch_count
            else:
                assert not adaptive_window_selection
                patch_count = len(boxes)
            return SimultaneousShiftMeasurement(
                status="ready",
                reason="synthetic stage-linked displacement",
                dx=float(position_units[2]),
                dy=0,
                confidence=0.8,
                candidate_patch_count=patch_count,
                accepted_patch_count=patch_count,
                inlier_patch_count=patch_count,
                tissue_coverage=0.5,
                dx_mad=0,
                dy_mad=0,
                median_response=0.8,
            )

        thing = env.get_thing_by_name("rg_simultaneous")
        original_external_focus_sample = thing._external_focus_sample

        def external_with_measurement(measurement_function, peripheral=None):
            def external(  # noqa: PLR0913, PLR0917
                directory,
                planes,
                maps,
                settings,
                offsets,
                white_level,
                boxes,
                *,
                adaptive_window_selection=True,
                inspect_periphery=False,
            ):
                result = original_external_focus_sample(
                    directory,
                    planes,
                    maps,
                    settings,
                    offsets,
                    white_level,
                    boxes,
                    adaptive_window_selection=adaptive_window_selection,
                    inspect_periphery=inspect_periphery,
                )
                measurement_boxes = (
                    result["boxes"]
                    if boxes is None and not adaptive_window_selection
                    else boxes
                )
                result["measurement"] = measurement_function(
                    None,
                    None,
                    settings,
                    measurement_boxes,
                    adaptive_window_selection=adaptive_window_selection,
                )
                if inspect_periphery:
                    result["peripheral_search"] = (
                        peripheral()
                        if peripheral is not None
                        else {
                            "status": "not_needed",
                            "diagnostic_only": True,
                            "autofocus_authorized": False,
                            "windows": [],
                        }
                    )
                return result

            return external

        monkeypatch.setattr(
            thing,
            "_external_focus_sample",
            external_with_measurement(measured_shift),
        )
        parameters = env.client.get("/rg_simultaneous/focus_parameters").json()
        parameters.update(
            calibration_positions_um=[-4, -3, -2, -1, 0, 1, 2, 3, 4],
            holdout_positions_um=[-3, -1, 1, 3],
            preload_um=1,
            focus_tolerance_um=0.5,
            maximum_correction_um=4,
            focus_plane_roi=[4, 4, 24, 24],
            maximum_holdout_error_um=1,
            maximum_fit_rmse_um=1,
            processing_downsample=1,
            minimum_slope_norm_px_per_um=0.1,
            minimum_confidence=0,
            maximum_shift_mad_px=10,
        )
        parameters["core"].update(
            registration_highpass_sigma_px=2,
            texture_background_sigma_px=3,
            texture_score_sigma_px=1,
            texture_morphology_kernel_px=3,
            patch_size_px=16,
            patch_stride_px=8,
            minimum_tissue_coverage=0.15,
            minimum_patch_signal=1,
            minimum_patch_std=1,
            minimum_patch_response=0,
            minimum_patch_peak_margin=0,
            minimum_patch_spectral_correlation=-1,
            maximum_absolute_shift_px=6,
            maximum_absolute_orthogonal_shift_px=2,
            maximum_patch_count=9,
            minimum_patch_count=3,
        )
        parameters["minimum_outlier_threshold_px"] = 3
        configured = run_simultaneous(
            env, "set_focus_parameters", parameters=parameters
        )
        assert configured["status"] == "completed", configured
        result = run_simultaneous(env, "calibrate_focus", prepared=True)
        assert result["status"] == "completed", result
        profile = result["output"]
        assert profile["method"] == "simultaneous-raw12-spectral-subpixel-shift"
        assert profile["validated"]
        assert profile["working_separate_rg_changed"] is False
        assert profile["curve"]["holdout_max_absolute_error_um"] <= 1
        assert position_units == [0, 0, 0]
        assert moves[0] == (-1, 0)
        assert moves[-1] == (-1, 0)
        assert env.client.get("/rg_simultaneous/profile").json() == flat_profile
        assert (
            env.client.get("/rg_simultaneous/focus_status").json()["status"] == "valid"
        )
        assert run_simultaneous(env, "set_enabled", enabled=True)["output"] is True
        evidence = (
            Path(env.get_thing_by_name("rg_simultaneous").data_dir)
            / profile["id"]
            / "focus-evidence.json"
        )
        assert evidence.is_file()
        assert (
            hashlib.sha256(evidence.read_bytes()).hexdigest()
            == profile["evidence_sha256"]
        )
        focus_report = json.loads((evidence.parent / "focus-report.json").read_text())
        first_sample = focus_report["samples"][0]
        assert first_sample["flat_field_plane_roi"] == [0, 0, 32, 32]
        assert first_sample["focus_plane_roi"] == [4, 4, 24, 24]
        assert first_sample["capture_plane_roi"] == [4, 4, 24, 24]
        assert first_sample["frame"]["processing_plane_size"] == [24, 24]
        assert first_sample["focus_working_size"] == [24, 24]
        assert first_sample["processing_timing"]["focus_map_cache_hit"] is False
        if hasattr(simultaneous_module, "NMI_BACKEND"):
            assert first_sample["processing_timing"]["nmi_backend"] in {
                "native-exhaustive",
                "opencv-exhaustive",
            }
        assert first_sample["processing_timing"]["total_s"] >= 0
        assert all(
            sample["processing_timing"]["focus_map_cache_hit"] is True
            for sample in focus_report["samples"][1:]
        )

        # The cached full-frame maps follow archive identity, not the focus ROI.
        settings = thing.focus_parameters
        warm, _, hit, _ = thing._load_focus_maps(settings)
        assert hit
        shifted_settings = settings.model_copy(
            update={"focus_plane_roi": (6, 6, 24, 24)}
        )
        shifted, _, hit, _ = thing._load_focus_maps(shifted_settings)
        assert hit
        assert shifted is warm
        restored, _, hit, _ = thing._load_focus_maps(settings)
        assert hit
        assert restored is warm
        archive = thing._map_path(flat_profile)
        stat = archive.stat()
        os.utime(archive, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
        refreshed, _, hit, _ = thing._load_focus_maps(settings)
        assert not hit
        assert refreshed is not warm
        thing._commit("profile", flat_profile)
        assert thing._focus_maps_cache is None

        position_units[2] = 2
        model.position[2] = 0.002
        moves.clear()
        frames_before = model.frames
        focused = run_simultaneous(env, "autofocus", prepared=True)
        assert focused["status"] == "completed", focused
        assert focused["output"]["focus_method"] == "rg_simultaneous"
        assert focused["output"]["mixed_capture_count"] == 1
        assert focused["output"]["correction_count"] == 1
        assert focused["output"]["correction_um"] == -2
        assert position_units == [0, 0, 0]
        assert model.frames == frames_before + 1

        saved_focus_profile = env.client.get("/rg_simultaneous/focus_profile").json()

        # Peripheral inspection is opt-in, uses the same RAW request and cannot
        # feed an unvalidated off-axis shift into the central Z curve.
        settings_path = Path(thing._thing_server_interface.settings_file_path)
        saved_settings = settings_path.read_bytes()
        moves_before = list(moves)
        frames_before = model.frames
        diagnostic = run_simultaneous(
            env, "measure_focus", prepared=True, inspect_periphery=True
        )
        assert diagnostic["status"] == "completed", diagnostic
        sample = diagnostic["output"]["sample"]
        assert sample["peripheral_search"]["status"] == "not_needed"
        assert sample["capture_plane_roi"] == [0, 0, 32, 32]
        assert sample["focus_plane_roi"] == [4, 4, 24, 24]
        assert model.frames == frames_before + 1

        with monkeypatch.context() as patch:

            def central_refusal(*_args, **_kwargs):
                return SimultaneousShiftMeasurement(
                    status="refused",
                    reason="Only 2 tissue windows passed; 3 required",
                    confidence=0,
                    candidate_patch_count=9,
                    accepted_patch_count=2,
                    inlier_patch_count=0,
                    tissue_coverage=0.1,
                    dx_mad=0,
                    dy_mad=0,
                    median_response=0,
                )

            def peripheral_evidence():
                return {
                    "status": "ready",
                    "diagnostic_only": True,
                    "autofocus_authorized": False,
                    "windows": [{"native_plane_roi": [0, 0, 16, 16]}],
                }

            patch.setattr(
                thing,
                "_external_focus_sample",
                external_with_measurement(central_refusal, peripheral_evidence),
            )
            frames_before = model.frames
            diagnostic = run_simultaneous(
                env, "measure_focus", prepared=True, inspect_periphery=True
            )
            assert diagnostic["status"] == "completed", diagnostic
            output = diagnostic["output"]
            assert output["status"] == "refused"  # central result is NOT replaced
            reserve = output["sample"]["peripheral_search"]
            assert reserve["status"] == "ready"
            assert not reserve["autofocus_authorized"]
            assert reserve["windows"][0]["sensor_plane_roi"] == [0, 0, 16, 16]
            assert reserve["additional_capture_count"] == 0
            assert (
                reserve["retained_raw_bytes"] == 4 * 32 * 32 * 4
            )  # owned float32 crop
            assert model.frames == frames_before + 1

            frames_before = model.frames
            diagnostic = run_simultaneous(
                env, "measure_focus", prepared=True, inspect_periphery=True
            )
            assert diagnostic["status"] == "completed", diagnostic
            assert diagnostic["output"]["status"] == "refused"
            assert (
                diagnostic["output"]["sample"]["peripheral_search"]["status"] == "ready"
            )
            assert model.frames == frames_before + 1
        assert moves == moves_before
        assert model.mode == "white"
        assert settings_path.read_bytes() == saved_settings
        assert (
            env.client.get("/rg_simultaneous/focus_profile").json()
            == saved_focus_profile
        )

        def refused_shift(
            components,
            valid,
            settings,
            boxes,
            *,
            adaptive_window_selection=True,
        ):
            if position_units[2] != 4:
                return measured_shift(
                    components,
                    valid,
                    settings,
                    boxes,
                    adaptive_window_selection=adaptive_window_selection,
                )
            return SimultaneousShiftMeasurement(
                status="refused",
                reason="synthetic tissue refusal",
                confidence=0,
                candidate_patch_count=len(boxes),
                accepted_patch_count=0,
                inlier_patch_count=0,
                tissue_coverage=0,
                dx_mad=0,
                dy_mad=0,
                median_response=0,
            )

        monkeypatch.setattr(
            thing,
            "_external_focus_sample",
            external_with_measurement(refused_shift),
        )
        moves.clear()
        failed = run_simultaneous(env, "calibrate_focus", prepared=True)
        assert failed["status"] == "error"
        assert position_units == [0, 0, 0]
        assert moves[-1] == (-1, 0)
        assert (
            env.client.get("/rg_simultaneous/focus_profile").json()
            == saved_focus_profile
        )

        fallback_calls = []

        def always_refused(
            _components,
            _valid,
            _settings,
            boxes,
            *,
            adaptive_window_selection=True,
        ):
            assert boxes is None
            assert adaptive_window_selection
            return SimultaneousShiftMeasurement(
                status="refused",
                reason="synthetic runtime refusal",
                confidence=0,
                candidate_patch_count=16,
                accepted_patch_count=0,
                inlier_patch_count=0,
                tissue_coverage=0,
                dx_mad=0,
                dy_mad=0,
                median_response=0,
            )

        def separate_fallback(_self, **_kwargs):
            raise AssertionError("A simultaneous refusal must go directly to WHITE")

        def early_direct_white(_self, **kwargs):
            fallback_calls.append((True, kwargs["white_dz"]))
            return {
                "focus_method": "white_fallback",
                "fallback_count": 1,
                "status": "focused",
            }

        monkeypatch.setattr(
            thing,
            "_external_focus_sample",
            external_with_measurement(always_refused),
        )
        monkeypatch.setattr(RGFocus, "checked_profile", lambda _self: {})
        monkeypatch.setattr(RGFocus, "autofocus_with_white_fallback", separate_fallback)
        monkeypatch.setattr(
            RGFocus, "autofocus_white_after_simultaneous_refusal", early_direct_white
        )
        frames_before = model.frames
        fallback = run_simultaneous(env, "autofocus", prepared=True, white_dz=12)
        assert fallback["status"] == "completed", fallback
        assert fallback["output"]["focus_method"] == "white_fallback"
        assert fallback["output"]["fallback_count"] == 1
        assert fallback["output"]["simultaneous_attempted"] is True
        assert fallback["output"]["simultaneous_mixed_capture_count"] == 1
        assert fallback_calls == [(True, 12)]
        assert model.frames == frames_before + 1

        evidence_bytes = evidence.read_bytes()
        evidence.write_bytes(evidence_bytes + b"\n")
        fallback_calls.clear()
        frames_before = model.frames
        incompatible = run_simultaneous(env, "autofocus", prepared=True, white_dz=14)
        assert incompatible["status"] == "completed", incompatible
        assert incompatible["output"]["focus_method"] == "white_fallback"
        assert incompatible["output"]["simultaneous_mixed_capture_count"] == 0
        assert (
            "evidence is missing or changed"
            in incompatible["output"]["simultaneous_fallback_reason"]
        )
        assert fallback_calls == [(True, 14)]
        assert model.frames == frames_before
        evidence.write_bytes(evidence_bytes)

        direct_white_calls = []

        def unavailable_separate_profile(_self):
            raise ValueError("Current JPEG measurement policy is not commissioned")

        def direct_white_after_simultaneous(_self, **kwargs):
            direct_white_calls.append(kwargs)
            return {
                "focus_method": "white_fallback",
                "fallback_count": 1,
                "status": "focused",
            }

        monkeypatch.setattr(RGFocus, "checked_profile", unavailable_separate_profile)
        monkeypatch.setattr(
            RGFocus,
            "autofocus_white_after_simultaneous_refusal",
            direct_white_after_simultaneous,
        )
        manual_fallback = run_simultaneous(env, "autofocus", prepared=True, white_dz=16)
        assert manual_fallback["status"] == "completed", manual_fallback
        assert manual_fallback["output"]["focus_method"] == "white_fallback"
        assert manual_fallback["output"]["fallback_count"] == 1
        assert manual_fallback["output"]["simultaneous_attempted"] is True
        assert direct_white_calls[0]["white_dz"] == 16
        assert (
            "Direct WHITE fallback selected"
            in direct_white_calls[0]["separate_rg_unavailable_reason"]
        )

        direct_white_calls.clear()
        scan_fallback = env.get_thing_by_name("rg_simultaneous").autofocus_for_scan(
            white_dz=18
        )
        assert scan_fallback["focus_method"] == "white_fallback"
        assert scan_fallback["fallback_count"] == 1
        assert scan_fallback["simultaneous_attempted"] is True
        assert direct_white_calls[0]["white_dz"] == 18
        assert (
            "Direct WHITE fallback selected"
            in direct_white_calls[0]["separate_rg_unavailable_reason"]
        )

        # Live recovery opts in separately; the central Z profile is not rewritten.
        assert run_simultaneous(env, "set_peripheral_focus_enabled", enabled=True)[
            "output"
        ]
        with monkeypatch.context() as patch:
            central_available = True

            def adaptive_capture(_self, _phase, _settings, *, inspect_periphery=False):
                assert inspect_periphery
                central = [(4, 4), (12, 4)] if central_available else []
                outer = [(0, 0), (0, 8), (8, 0), (16, 16)]
                windows = [
                    {
                        "patch_id": f"{x}:{y}",
                        "x": x,
                        "y": y,
                        "w": 16,
                        "h": 16,
                        "status": "accepted",
                        "reason": "synthetic",
                        "coverage": 1,
                        "dx": 2.0,
                        "dy": 0.0,
                        "response": 0.8,
                    }
                    for x, y in central + outer
                ]
                refused = always_refused(None, None, _settings, None)
                return (
                    refused,
                    [],
                    {
                        "measurement": refused.model_dump(mode="json"),
                        "peripheral_search": {
                            "attempted": True,
                            "used": False,
                            "status": "ready",
                            "measurement": {"tissue_coverage": 1.0},
                            "windows": windows,
                            "search_plane_roi": [0, 0, 32, 32],
                            "peripheral_patch_count": len(outer),
                            "evaluated_patch_count": len(windows),
                            "elapsed_s": 0.1,
                        },
                    },
                )

            patch.setattr(type(thing), "_capture_focus_sample", adaptive_capture)
            position_units[2] = 2
            model.position[2] = 0.002
            recovered = run_simultaneous(env, "autofocus", prepared=True, white_dz=18)
            assert recovered["status"] == "completed", recovered
            assert recovered["output"]["focus_method"] == "rg_simultaneous"
            assert recovered["output"]["peripheral_search"]["used"]
            assert recovered["output"]["peripheral_search"]["central_anchor_count"] == 2
            assert recovered["output"]["correction_um"] == -2
            assert position_units == [0, 0, 0]

            central_available = False
            position_units[2] = 2
            model.position[2] = 0.002
            white = run_simultaneous(env, "autofocus", prepared=True, white_dz=18)
            assert white["status"] == "completed", white
            assert white["output"]["focus_method"] == "white_fallback"
            assert white["output"]["fallback_count"] == 1
            assert white["output"]["peripheral_search"]["attempted"]
            assert not white["output"]["peripheral_search"]["used"]
            assert white["output"]["peripheral_search"]["central_anchor_count"] == 0
        assert (
            run_simultaneous(env, "set_peripheral_focus_enabled", enabled=False)[
                "output"
            ]
            is False
        )
        assert (
            env.client.get("/rg_simultaneous/focus_profile").json()
            == saved_focus_profile
        )

        fallback_calls.clear()
        white_calls_before_fault = len(direct_white_calls)

        def unsafe_camera_failure(_timeout):
            raise OSError("synthetic RAW transport failure")

        model.capture_raw = unsafe_camera_failure
        unsafe = run_simultaneous(env, "autofocus", prepared=True, white_dz=16)
        assert unsafe["status"] == "error"
        assert "synthetic RAW transport failure" in unsafe["error"]["detail"]
        assert fallback_calls == []
        assert len(direct_white_calls) == white_calls_before_fault


def test_direct_colour_transition_holds_preview_before_switch(
    optical_server, monkeypatch
):
    """Close MJPEG before WHITE-to-RED, not only after light confirmation."""
    server, model = optical_server
    original_capture = model.capture

    def capture_with_live_white_preview(timeout):
        result = original_capture(timeout)
        if model.mode == "white":
            start = time.monotonic_ns() + 5_000_000
            for offset_ms in (0, 5, 10):
                stamp = start + offset_ms * 1_000_000
                model.preview.record({"SensorTimestamp": stamp, "ExposureTime": 4000})
                model.preview.allows(stamp // 1000)
            assert not model.preview.status["holding"]
        return result

    model.capture = capture_with_live_white_preview
    with server() as env:
        prepare_settings(env)
        illumination = env.get_thing_by_name("illumination")

        def transition(_verified, _controller, mode):
            assert model.preview.status["holding"]
            return model.select(mode)

        monkeypatch.setattr(illumination, "_transition_after_verified", transition)
        result = run(env, "capture_pair", prepared=True)
        assert result["status"] == "completed", result


def test_stability_records_fixed_settings_and_light_and_keeps_maps(
    optical_server, monkeypatch
):
    """A bounded diagnostic neither calibrates nor moves; preview waits for fresh WHITE."""
    server, model = optical_server
    monkeypatch.setattr(lt, "cancellable_sleep", lambda _seconds: None)
    with server() as env:
        prepare_settings(env)
        result = run(env, "measure_stability", prepared=True, samples=5)
        assert result["status"] == "completed", result
        assert model.frames == 6
        assert model.commands == ["red", "white"]
        assert model.preview.status["holding"]
        assert not model.preview.status["active"]
        assert env.client.get("/rg_flat_field/profiles").json() == {}
        assert (
            run(env, "measure_stability", prepared=True, samples=91)["status"]
            == "error"
        )
        assert model.frames == 6


def test_processing_drift_stops_and_white_restore_resumes_preview(optical_server):
    """Auto-processing cannot pass simply because shutter/analogue gain remain fixed."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        model.on_capture = lambda: setattr(
            model, "processing_gain", 1.0 + model.frames / 100
        )
        result = run(env, "capture_pair", prepared=True)
        assert result["status"] == "error", result
        assert model.mode == "white"
        assert model.preview.status["holding"]
        stamp = time.monotonic_ns() + 5_000_000
        for offset_ms in (0, 5):
            queued = stamp + offset_ms * 1_000_000
            model.preview.record({"SensorTimestamp": queued, "ExposureTime": 4000})
            assert not model.preview.allows(queued // 1000)
        released = stamp + 10_000_000
        model.preview.record({"SensorTimestamp": released, "ExposureTime": 4000})
        assert model.preview.allows(released // 1000)


def test_failed_recheck_persists_blocks_use_and_can_recover_without_refitting(
    optical_server,
):
    """Keep map bytes but never silently use a profile after failed validation."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        assert run(env, "calibrate", prepared=True)["status"] == "completed"
        saved = env.client.get("/rg_flat_field/profiles").json()
        model.bad = True
        assert run(env, "validate", prepared=True, modes=["red"])["status"] == "error"
        assert model.mode == "white"
        statuses = env.client.get("/rg_flat_field/calibration_status").json()
        assert statuses["red"]["status"] == "validation_failed"
        assert statuses["green"]["status"] == "valid"
        assert env.client.get("/rg_flat_field/profiles").json() == saved
    with server() as env:
        statuses = env.client.get("/rg_flat_field/calibration_status").json()
        assert statuses["red"]["status"] == "validation_failed"
        thing = env.get_thing_by_name("rg_flat_field")
        with pytest.raises(ValueError, match="latest check"):
            thing.correct_frame("red", np.zeros((4, 64, 64)), {})
        model.bad = False
        assert (
            run(env, "validate", prepared=True, modes=["red"])["status"] == "completed"
        )
        assert env.client.get("/rg_flat_field/profiles").json() == saved
        assert (
            env.client.get("/rg_flat_field/calibration_status").json()["red"]["status"]
            == "valid"
        )


def test_preset_gate_before_effects_and_exact_jpeg_evidence(optical_server):
    """Migration is explicit; every accepted request saves its exact full JPEG."""
    server, model = optical_server
    with server() as env:
        for action in ("capture_pair", "calibrate", "validate"):
            assert run(env, action, prepared=True)["status"] == "error"
        assert model.commands == []
        assert model.frames == 0
        result = run(env, "use_jpeg_preset", processing_roi=[8, 0, 48, 64])
        assert result["status"] == "completed", result
        parameters = env.client.get("/rg_flat_field/parameters").json()
        assert parameters["minimum_signal_dn"] == 8
        assert parameters["maximum_dark_signal_dn"] == 32
        thing = env.get_thing_by_name("rg_flat_field")
        assert list(Path(thing.data_dir).glob("parameters-before-jpeg-*.json"))
        output = run(env, "capture_pair", prepared=True)["output"]
        directory = Path(output["directory"])
        report = json.loads((directory / "acquisition.json").read_text())
        for info, payload in zip(output["frames"], model.jpeg_frames, strict=True):
            assert (directory / info["jpeg_file"]).read_bytes() == payload
            assert hashlib.sha256(payload).hexdigest() == info["jpeg_sha256"]
            assert json.loads((directory / info["metadata_file"]).read_text()) == info
            assert info["geometry"]["image_size"] == [48, 64]
            assert info["source_geometry"]["image_size"] == [64, 64]
        assert report["storage"]["jpeg_files_bytes"] == sum(map(len, model.jpeg_frames))
        assert not list(directory.glob("*.npz"))
        assert not list(directory.glob("*.png"))
        for phase in report["timing"]["phases"].values():
            assert np.isfinite(phase["seconds"])
            assert phase["seconds"] >= 0
            assert phase["count"] >= 0


def test_all_calibration_cycles_have_source_jpegs_and_disable_reset_keep_history(
    optical_server,
):
    """OFF, conditioning, fit and holdout are archived; only active pointers reset."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        profiles = run(env, "calibrate", prepared=True)["output"]
        thing = env.get_thing_by_name("rg_flat_field")
        directory = thing._directory(profiles["red"])
        report = json.loads((directory / "acquisition.json").read_text())
        assert len(list(directory.glob("frame-*.jpg"))) == model.frames == 26
        assert {frame["light"] for frame in report["frames"]} == {
            "off",
            "white",
            "red",
            "green",
        }
        for frame, payload in zip(report["frames"], model.jpeg_frames, strict=True):
            assert (directory / frame["jpeg_file"]).read_bytes() == payload
        assert (
            run(env, "set_enabled", mode="red", enabled=False)["status"] == "completed"
        )
        assert thing.calibration_status["red"]["status"] == "disabled"
        with pytest.raises(ValueError, match="disabled"):
            thing._load_maps("red")
        assert (
            run(env, "set_enabled", mode="red", enabled=True)["status"] == "completed"
        )
        assert thing.calibration_status["red"]["status"] == "valid"
        assert run(env, "reset_profile", mode="red")["status"] == "completed"
        assert thing.calibration_status["red"]["status"] == "not_calibrated"
        assert thing.profiles["green"] == profiles["green"]
        assert (directory / "red.npz").exists()


def test_first_processing_reference_and_cross_session_check(optical_server):
    """Small per-frame drift cannot walk away; saved actual processing binds rechecks."""
    server, model = optical_server
    with server() as env:
        prepare_settings(env)
        model.on_capture = lambda: setattr(
            model, "processing_gain", 1.0 + model.frames * 0.000006
        )
        assert run(env, "capture_pair", prepared=True)["status"] == "error"
        assert model.frames == 3
        assert model.mode == "white"
        model.on_capture = None
        model.processing_gain = 1.0000462532
        assert run(env, "calibrate", prepared=True)["status"] == "completed"
        model.processing_gain = 1.02
        result = run(env, "validate", prepared=True)
        assert result["status"] == "error", result
        assert model.mode == "white"


def test_processing_roi_excludes_dark_saturated_edges_from_fit_and_qc(optical_server):
    """Unusable full-sensor edges are archived, but never enter selected ROI statistics."""
    server, model = optical_server
    model.flat[:, :8] = 0
    model.flat[:, 56:] = 255
    with server() as env:
        assert (
            run(env, "use_jpeg_preset", processing_roi=[48, 0, 32, 64])["status"]
            == "error"
        )
        assert model.commands == []
        assert model.frames == 0
        prepare_settings(env)
        thing = env.get_thing_by_name("rg_flat_field")
        parameters = thing.parameters.model_dump(mode="json")
        parameters["processing_roi"] = [8, 0, 48, 64]
        assert (
            run(env, "set_parameters", parameters=parameters)["status"] == "completed"
        )
        result = run(env, "calibrate", prepared=True)
        assert result["status"] == "completed", result
        profile = result["output"]["red"]
        assert profile["geometry"]["plane_size"] == [48, 64]
        report = json.loads(
            (thing._directory(profile) / "acquisition.json").read_text()
        )
        red = next(frame for frame in report["frames"] if frame["light"] == "red")
        assert red["channel_saturation_fraction"][0] == 0
        with Image.open(thing._directory(profile) / red["jpeg_file"]) as image:
            rgb = np.asarray(image)
            assert np.max(rgb[:, 56:, 0]) >= 250
            assert image.size == (64, 64)


def test_legacy_settings_startup_is_byte_preserving_and_roi_inputs_are_strict(
    optical_server,
):
    """Reading an old settings file never migrates thresholds or writes the new domain."""
    server, model = optical_server
    with server() as env:
        thing = env.get_thing_by_name("rg_flat_field")
        thing.save_settings()
        path = Path(thing._thing_server_interface.settings_file_path)
        saved = json.loads(path.read_text())
        saved["parameters"].pop("measurement_domain", None)
        saved["parameters"].pop("processing_roi", None)
        path.write_text(json.dumps(saved, indent=2))
        historical = path.read_bytes()
    with server() as env:
        thing = env.get_thing_by_name("rg_flat_field")
        assert thing.parameters.minimum_signal_dn == 64
        assert thing.parameters.maximum_dark_signal_dn == 256
        assert path.read_bytes() == historical
        for roi in ([True, 0, 32, 32], [0.5, 0, 32, 32], ["8", 0, 32, 32]):
            response = env.client.post(
                "/rg_flat_field/use_jpeg_preset", json={"processing_roi": roi}
            )
            assert response.status_code == 422
            assert path.read_bytes() == historical
        assert model.commands == []
        assert model.frames == 0


@pytest.mark.parametrize("invalid_roi", [False, True])
def test_white_off_controls_work_without_jpeg_readiness(optical_server, invalid_roi):
    """Operator WHITE/OFF retain preview safety without preset, geometry or capture."""
    server, model = optical_server
    with server() as env:
        thing = env.get_thing_by_name("rg_flat_field")
        if invalid_roi:
            prepare_settings(env)
            model.binding["geometry"]["image_size"] = [16, 16]
        thing.save_settings()
        settings_path = Path(thing._thing_server_interface.settings_file_path)
        before = settings_path.read_bytes()
        assert not thing.readiness["ready"]
        for mode in ("off", "white"):
            result = run(env, "select_diagnostic_mode", mode=mode)
            assert result["status"] == "completed", result
            assert result["output"]["image_href"] is None
            assert result["output"]["selected_mode_retained"]
            assert model.mode == mode
            assert settings_path.read_bytes() == before
        assert not model.preview.status["active"]
        assert model.frames == 0
        assert model.commands == ["off", "white"]
        assert not list(Path(thing.data_dir).glob("*/acquisition.json"))
        assert run(env, "select_diagnostic_mode", mode="red")["status"] == "error"
        assert model.commands == ["off", "white"]
