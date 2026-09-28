"""Tests that check that a tuning file can be reloaded."""

import pytest
from picamera2 import Picamera2

from openflexure_microscope_server.things.camera import (
    picamera_recalibrate_utils as recalibrate_utils,
)
from openflexure_microscope_server.things.camera import (
    picamera_tuning_file_utils as tf_utils,
)

MODEL = Picamera2.global_camera_info()[0]["Model"]


def generate_bad_tuning():
    """Return a tuning file with an invalid version number to force an error when loaded."""
    default_tuning = tf_utils.load_default_tuning("imx219")
    bad_tuning = default_tuning.copy()
    bad_tuning["version"] = 999
    return bad_tuning


def _test_bad_tuning_after_good_tuning(configure: bool = False):
    """Test loading good, bad, then another good tuning files in sequence.

    Load the default tuning file into the camera, re-load with a broken tuning file,
    check it errors. Finally check the default tuning file will load again afterwards.

    :param configure: Boolean, set true to configure the camera on initial loading

    This test checks that:
    recalibrate_utils.recreate_camera_manager() is working as expected. As the default
    PiCamera2 behaviour does not expect the tuning file to be reloaded.
    """
    bad_tuning = generate_bad_tuning()
    default_tuning = tf_utils.load_default_tuning("imx219")
    print("opening camera with default tuning")
    with Picamera2(tuning=default_tuning) as cam:
        if configure:
            cam.configure(cam.create_preview_configuration())
    del cam
    recalibrate_utils.recreate_camera_manager()
    print(f"Opening camera with bad tuning - ['version'] = {bad_tuning['version']}")
    with pytest.raises(IndexError):
        # The bad version should cause a problem
        cam = Picamera2(tuning=bad_tuning)
    recalibrate_utils.recreate_camera_manager()
    with Picamera2(tuning=default_tuning) as cam:
        # Reload the camera with working tuning, or it will stop responding
        # and fail future tests
        pass
    del cam


# Note: The tests below are marked with
# @pytest.mark.filterwarnings("ignore: Exception ignored")
# as the picamera will throw an exception ignored warning when deleting
# the picamera object. This is expected

# The 2 pairs of repeated identical tests exist because pytest only
# starts once, and so by running the test more than one time checks
# that the camera can be initialised multiple times without restarting
# python.


@pytest.mark.filterwarnings("ignore: Exception ignored")
def test_bad_tuning_after_good_tuning_noconfigure():
    """First run setting good, then bad, then good tuning.

    create_preview_configuration() is NOT run on initial setup.
    """
    _test_bad_tuning_after_good_tuning(configure=False)


@pytest.mark.filterwarnings("ignore: Exception ignored")
def test_bad_tuning_after_good_tuning_configure():
    """Second run setting good, then bad, then good tuning.

    create_preview_configuration() is run on initial setup this time.
    """
    _test_bad_tuning_after_good_tuning(configure=True)


@pytest.mark.filterwarnings("ignore: Exception ignored")
def test_bad_tuning_after_good_tuning_noconfigure2():
    """3rd run setting good, then bad, then good tuning.

    create_preview_configuration() is NOT run on initial setup.
    """
    _test_bad_tuning_after_good_tuning(configure=False)


@pytest.mark.filterwarnings("ignore: Exception ignored")
def test_bad_tuning_after_good_tuning_configure2():
    """Final run setting good, then bad, then good tuning.

    create_preview_configuration() is run on initial setup again.
    """
    _test_bad_tuning_after_good_tuning(configure=True)
