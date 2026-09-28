"""Utilities to help with testing the camera."""

import tempfile
from contextlib import contextmanager
from typing import Optional

from openflexure_microscope_server.things.camera.picamera import PiCameraV2
from openflexure_microscope_server.things.scanning.background_detect import (
    ChannelDeviationLUV,
)

from ...shared_utils.lt_test_utils import LabThingsTestEnv


@contextmanager
def camera_test_env(settings_folder: Optional[str] = None):
    """Yield a test environment with a server that contains just the camera.

    This is a context manager, not a pytest fixture, as it needs to be created
    multiple times in some tests.

    :param settings_folder: The settings folder for the camera, if none is supplied, new
        temporary directory will be used as the settings folder.
    """
    thing_conf = {
        "camera": PiCameraV2,
        "bg_channel_deviations_luv": ChannelDeviationLUV,
    }
    app_config = {
        "data_folder": tempfile.mkdtemp(),
    }
    with LabThingsTestEnv(
        things=thing_conf,
        settings_folder=settings_folder,
        application_config=app_config,
    ) as env:
        yield env


@contextmanager
def camera_test_client(settings_folder: Optional[str] = None):
    """Yield a camera ThingClient on a camera server.

    This is a context manager, not a pytest fixture, as it needs to be created
    multiple times in some tests.

    :param settings_folder: The settings folder for the camera, if none is supplied, new
        temporary directory will be used as the settings folder.
    """
    with camera_test_env(settings_folder=settings_folder) as env:
        yield env.get_thing_client("camera")
