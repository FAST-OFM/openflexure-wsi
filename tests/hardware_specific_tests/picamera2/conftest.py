"""Shared fixtures for picamera tests."""

import pytest

import labthings_fastapi as lt

from .cam_test_utils import camera_test_env


@pytest.fixture
def picamera_test_env() -> lt.ThingClient:
    """Initialise a test environment with only a PiCameraV2 Thing."""
    with camera_test_env() as env:
        yield env


@pytest.fixture
def picamera_client() -> lt.ThingClient:
    """Initialise a test picamera_client (in a LabThings test env)."""
    with camera_test_env() as env:
        yield env.get_thing_client("camera")
