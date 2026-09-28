"""Exercise the actual Z calibration HTTP actions and native persisted settings."""

import json

import pytest

from openflexure_microscope_server.focus.z_backlash_calibration import (
    ZCalibrationSettings,
)
from openflexure_microscope_server.things import OFMThing
from openflexure_microscope_server.things.camera.simulation import SimulatedCamera
from openflexure_microscope_server.things.illumination import (
    LightState,
    MoonrakerIllumination,
)
from openflexure_microscope_server.things.stage.moonraker import MoonrakerStage

from ..shared_utils.lt_test_utils import LabThingsTestEnv
from ..unit_tests.test_z_backlash_calibration import PlayModel


@pytest.fixture
def http_calibration(tmp_path, monkeypatch):
    """Keep real LabThings routing/slots/settings, replacing only physical devices."""
    model = PlayModel()
    for cls in (MoonrakerStage, MoonrakerIllumination, SimulatedCamera):
        monkeypatch.setattr(cls, "__enter__", lambda self: self)
        monkeypatch.setattr(cls, "__exit__", OFMThing.__exit__)
    monkeypatch.setattr(
        MoonrakerStage, "hardware_settings", property(lambda _self: model.hardware)
    )
    monkeypatch.setattr(
        MoonrakerStage,
        "validate_path",
        lambda _self, *a, **kw: model.validate(*a, **kw),
    )
    monkeypatch.setattr(
        MoonrakerStage, "get_xyz_position", lambda _self: (model.x, model.y, model.z)
    )
    monkeypatch.setattr(
        MoonrakerStage, "move_absolute_in_segments", lambda _self, xyz: model.move(xyz)
    )
    monkeypatch.setattr(
        MoonrakerStage,
        "move_z_with_preload",
        lambda _self, z, preload, approach: model.preloaded(z, preload, approach),
    )
    monkeypatch.setattr(
        MoonrakerIllumination,
        "state",
        property(
            lambda _self: LightState(
                available=True,
                mode="white",
                channels={"white": True, "red": False, "green": False},
            )
        ),
    )
    monkeypatch.setattr(SimulatedCamera, "stream_active", property(lambda _self: True))
    monkeypatch.setattr(
        SimulatedCamera,
        "focus_configuration",
        property(lambda _self: {"sensor": "synthetic", "roi": [0, 0, 64, 64]}),
        raising=False,
    )
    monkeypatch.setattr(
        SimulatedCamera,
        "capture_settled_frame",
        lambda _self, timeout: model.capture(timeout),
        raising=False,
    )
    things = {
        "camera": "openflexure_microscope_server.things.camera.simulation:SimulatedCamera",
        "dummy": "openflexure_microscope_server.things.stage.dummy:DummyStage",
        "stage": "openflexure_microscope_server.things.stage.moonraker:MoonrakerStage",
        "illumination": "openflexure_microscope_server.things.illumination:MoonrakerIllumination",
        "z_backlash": "openflexure_microscope_server.things.focus.z_backlash:ZBacklashCalibration",
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


def test_parameters_and_manifest_through_http(http_calibration):
    """Settings and manifest use the real typed action boundary and survive restart."""
    server, model = http_calibration
    parameters = ZCalibrationSettings(span_um=24.0).model_dump(mode="json")
    with server() as env:
        assert (
            env.client.get("/z_backlash/calibration_status").json()["status"]
            == "not_calibrated"
        )
        manifest = env.client.get("/z_backlash/manifest").json()
        assert manifest["id"] == "z_backlash"
        assert len(manifest["stages"]) == 6
        invocation = env.poll_action(
            env.start_action("z_backlash", "set_parameters", {"parameters": parameters})
        )
        assert invocation["status"] == "completed", invocation
        assert env.client.get("/z_backlash/parameters").json() == parameters
        bad = dict(parameters, step_um=0)
        response = env.client.post(
            "/z_backlash/set_parameters", json={"parameters": bad}
        )
        assert response.status_code == 422, response.text
        assert env.client.get("/z_backlash/parameters").json() == parameters
        assert (
            env.client.put("/z_backlash/last_calibration", json={}).status_code == 405
        )
        unprepared = env.poll_action(env.start_action("z_backlash", "calibrate"))
        assert unprepared["status"] == "error", unprepared
        assert not model.moves
        assert not model.captures
    with server() as restarted:
        assert restarted.client.get("/z_backlash/parameters").json() == parameters
        assert not model.moves


def test_success_and_failed_repeat_through_http(http_calibration):
    """A real action persists the checked profile, retained after a failed repeat."""
    server, model = http_calibration
    with server() as env:
        assert env.client.get("/z_backlash/readiness").json()["ready"]
        invocation = env.poll_action(
            env.start_action("z_backlash", "calibrate", {"prepared": True})
        )
        assert invocation["status"] == "completed", invocation
        result = env.client.get("/z_backlash/last_calibration").json()
        assert result["estimate"]["backlash_um"] == pytest.approx(4, abs=0.05)
        assert result["frame_count"] == 99
        assert model.z == 0
        model.flat = True
        failed = env.poll_action(
            env.start_action("z_backlash", "calibrate", {"prepared": True})
        )
        assert failed["status"] == "error", failed
        assert env.client.get("/z_backlash/last_calibration").json() == result
        assert env.client.get("/z_backlash/progress").json()["phase"] == "stopped"
    model.ready = False
    with server() as restarted:
        assert restarted.client.get("/z_backlash/last_calibration").json() == result
        assert (
            restarted.client.get("/z_backlash/calibration_status").json()["status"]
            == "valid"
        )
        assert not restarted.client.get("/z_backlash/readiness").json()["ready"]
        assert json.loads(json.dumps(result)) == result
