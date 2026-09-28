"""Test data collection from the Raspberry Picamera."""

import tempfile
from copy import deepcopy

from openflexure_microscope_server.things.camera.picamera import StreamingPiCamera2

from .cam_test_utils import camera_test_client


def test_calibration(picamera_test_env):
    """Check that full auto calibrate completes and set the expected values."""
    picamera_client = picamera_test_env.get_thing_client("camera")
    picamera_thing = picamera_test_env.get_thing_by_type(StreamingPiCamera2)
    # Check the calibration_required property used by the calibration wizard
    assert picamera_thing.calibration_required
    # Save copy of default tuning file for end of test
    original_default = deepcopy(picamera_thing.default_tuning)
    # Tuning should start the same as the server is loading with no settings.
    assert picamera_thing.default_tuning == picamera_thing.tuning
    # The background detector isn't ready as there is no background image.
    assert not picamera_thing.background_detector.ready

    # Run full auto calibrate
    picamera_client.full_auto_calibrate()

    # After calibration it should report that calibration is no longer  required
    assert not picamera_thing.calibration_required
    # The tuning files should be different
    assert picamera_thing.default_tuning != picamera_thing.tuning
    # The default should be unchanged
    assert picamera_thing.default_tuning == original_default

    assert picamera_thing.background_detector.ready


def test_tuning_is_persistent():
    """Check that tuning file adjustments are persistent."""
    # First check full auto-calibrate is persistent if same save dir is used.
    with tempfile.TemporaryDirectory() as tmpdir:
        with camera_test_client(settings_folder=tmpdir) as client:
            initial_tuning = deepcopy(client.tuning)
            client.full_auto_calibrate()
            calibrated_tuning = deepcopy(client.tuning)
            # Full auto calibrate should have saved the tuning.
            assert initial_tuning != calibrated_tuning

        # Reload the camera and server from scratch
        with camera_test_client(settings_folder=tmpdir) as client:
            initial_tuning = deepcopy(client.tuning)
            # On reload the initial tuning is as it was when calibrated last run
            assert initial_tuning == calibrated_tuning
            # Set the lens shading to be flat
            client.flat_lens_shading()
            flat_tuning = deepcopy(client.tuning)
            assert flat_tuning != calibrated_tuning

        # Reload to check the flat lst persists
        with camera_test_client(settings_folder=tmpdir) as client:
            initial_tuning = deepcopy(client.tuning)
            # On reload the initial tuning the flat tuning
            assert initial_tuning == flat_tuning
            # Reset the lens shading to what is in the default tuning file
            client.reset_lens_shading()
            flat_tuning = deepcopy(client.tuning)
            assert flat_tuning != calibrated_tuning

        # Reload the one more time to check the reset lst persists
        with camera_test_client(settings_folder=tmpdir) as client:
            initial_tuning = deepcopy(client.tuning)
            # On reload the initial tuning the flat tuning
            assert initial_tuning == flat_tuning
