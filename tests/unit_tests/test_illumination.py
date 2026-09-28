"""Tests for the Illumination thing."""

from unittest.mock import call, patch

import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.illumination import (
    Illumination,
    SangaIllumination,
    SimulatorIllumination,
)

# Test the BaseClass before testing specific implementation


def test_base_illumination_set_led_not_implemented():
    """Check that calling set_led on the base Illumination raises NotImplementedError."""
    ill = create_thing_without_server(Illumination)
    with pytest.raises(
        NotImplementedError,
        match="Turning the LED on and off should be implemented by child classes.",
    ):
        ill.set_led(led_on=True)


def test_flash_calls_set_led_and_raises():
    """Check that calling flash from Baseclass calls flash with right arguments."""
    ill = create_thing_without_server(Illumination)

    number_of_flashes = 3  # smaller number for test speed
    dt = 0  # avoid sleeping

    with pytest.raises(NotImplementedError):
        ill.flash(number_of_flashes=number_of_flashes, dt=dt)

    # Patch set_led to just record calls without raising
    with patch.object(ill, "set_led") as mock_set_led:
        # Call flash normally
        ill.flash(number_of_flashes=number_of_flashes, dt=dt)

        # Each flash calls set_led twice: False, True
        expected_sequence = [call(False), call(True)] * number_of_flashes
        assert mock_set_led.call_args_list == expected_sequence

        # Also check total number of calls
        assert mock_set_led.call_count == number_of_flashes * 2


def test_negative_flash_calls():
    """Check that calling flash with negative number doesn't call set_led."""
    ill = create_thing_without_server(Illumination)

    number_of_flashes = -33  # smaller number for test speed
    dt = 0  # avoid sleeping

    # Patch set_led to just record calls without raising
    with patch.object(ill, "set_led") as mock_set_led:
        # Call flash normally
        ill.flash(number_of_flashes=number_of_flashes, dt=dt)

        # Also check total number of calls
        assert mock_set_led.call_count == 0


def test_negative_flash_dt():
    """Check that calling flash with negative dt raises ValueError."""
    ill = create_thing_without_server(Illumination)

    number_of_flashes = 3  # smaller number for test speed
    dt = -5  # set a negative delay

    # Patch set_led to just record calls without raising
    with (
        patch.object(ill, "set_led"),
        pytest.raises(ValueError, match="sleep length must be non-negative"),
    ):
        ill.flash(number_of_flashes=number_of_flashes, dt=dt)


@pytest.fixture
def sanga_mock_stage():
    """Return a SangaIllumination with a mocked stage."""
    return create_thing_without_server(SangaIllumination, mock_all_slots=True)


@pytest.fixture
def sim_mock_cam():
    """Return a SimulatorIllumination with a mocked camera."""
    return create_thing_without_server(SimulatorIllumination, mock_all_slots=True)


def test_sanga_flash_calls_led(sanga_mock_stage):
    """Test that flashing calls set_led with correct parameters."""
    number_of_flashes = 3
    dt = 0.01  # tiny sleep for test speed

    sanga_mock_stage.flash(number_of_flashes=number_of_flashes, dt=dt)

    # Each flash calls set_led False then True
    assert sanga_mock_stage._stage.set_led.call_count == 2 * number_of_flashes

    # Check that False and True are alternating when sent to _stage.set_led
    calls = sanga_mock_stage._stage.set_led.call_args_list
    expected_sequence = [False, True] * number_of_flashes
    for led_call, expected in zip(calls, expected_sequence, strict=True):
        args, kwargs = led_call
        assert args[0] == expected


def test_sim_flash_calls_led(sim_mock_cam):
    """Test that simulator flash calls set_led correctly."""
    number_of_flashes = 2
    dt = 0.01

    sim_mock_cam.flash(number_of_flashes=number_of_flashes, dt=dt)

    # Each flash calls set_led False then True
    assert sim_mock_cam._cam.set_led.call_count == 2 * number_of_flashes

    # Check that False and True are alternating when sent to _cam.set_led
    calls = sim_mock_cam._cam.set_led.call_args_list
    expected_sequence = [False, True] * number_of_flashes
    for led_call, expected in zip(calls, expected_sequence, strict=True):
        args, kwargs = led_call
        assert args[0] == expected
