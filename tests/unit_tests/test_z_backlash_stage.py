"""Candidate Z preload paths exercise the actual Moonraker adapter."""

import httpx
import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.stage.moonraker import (
    MoonrakerStage,
    StageSafetyError,
)

from .test_moonraker_stage import Controller, reference


@pytest.fixture
def preload_stage(mocker):
    """Use the existing controller fake with an explicitly bounded Z profile."""
    mocker.patch(
        "openflexure_microscope_server.things.stage.moonraker.lt.cancellable_sleep"
    )
    controller = Controller()
    hardware = {
        "allow_motion": True,
        "axes": {
            "x": {},
            "y": {},
            "z": {
                "enabled": True,
                "min_mm": -0.005,
                "max_mm": 0.005,
                "max_move_mm": 0.003,
                "ui_step_mm": 0.001,
            },
        },
    }
    stage = create_thing_without_server(MoonrakerStage, hardware=hardware)
    stage._client = httpx.Client(
        base_url="http://controller",
        transport=httpx.MockTransport(controller.handle),
    )
    reference(stage)
    yield stage, controller
    stage._client.close()


@pytest.mark.parametrize("sign", [-1, 1])
def test_preload_segments_with_no_global_compensation(preload_stage, sign):
    """Each leg observes single-move limits and uses the chosen final approach."""
    stage, controller = preload_stage
    stage.move_z_with_preload(z=sign * 4, preload=6, approach_sign=sign)
    assert stage.position["z"] == sign * 4
    assert len(controller.scripts) == 3
    assert all("M400" in script for script in controller.scripts)
    assert stage.backlash_steps == {"x": 0, "y": 0, "z": 0}


def test_bad_preload_path_refuses_before_first_command(preload_stage):
    """A valid target does not excuse an out-of-bounds preload endpoint."""
    stage, controller = preload_stage
    with pytest.raises(StageSafetyError):
        stage.move_z_with_preload(z=0, preload=10, approach_sign=1)
    assert not controller.scripts


def test_preload_timeout_never_sends_second_leg(preload_stage):
    """An ambiguous first command stops the candidate path."""
    stage, controller = preload_stage
    controller.timeout = True
    with pytest.raises(StageSafetyError, match="uncertain"):
        stage.move_z_with_preload(z=4, preload=6, approach_sign=1)
    assert len(controller.scripts) == 1
    assert stage._reference is None


@pytest.mark.parametrize("bad", [True, 0, -1, 1.5])
def test_invalid_preload_has_no_commands(preload_stage, bad):
    """Candidate preload requires positive integer controller units."""
    stage, controller = preload_stage
    with pytest.raises(StageSafetyError):
        stage.move_z_with_preload(z=0, preload=bad, approach_sign=1)
    assert not controller.scripts
