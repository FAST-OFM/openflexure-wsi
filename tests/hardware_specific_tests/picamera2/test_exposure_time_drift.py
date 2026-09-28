"""Check exposure times do not drift.

This can get very tedious. Recommend running pytest with -s option
to monitor progress.
"""

import json
import logging
import os
import tempfile
import time
from typing import Any

from openflexure_microscope_server.things.camera.picamera import StreamingPiCamera2

from .cam_test_utils import camera_test_client, camera_test_env

logging.basicConfig(level=logging.DEBUG)

# The tolerance used to be a setting of the camera, and any changes within tolerance
# were ignored. Now this isn't needed we always set a value 1 larger than the last
# accepted value as the PiCamera always rounds down.
# The tolerance is needed in this test as when setting an arbitrary value it is rounded
# down to a hardware compatible one.
EXPOSURE_TOL = 30


def _test_exposure_time_drift(desired_time: int) -> None:
    """Capture 10 full res images and check that the exposure time remains constant.

    This confirms that automatic exposure time adjustment is fully turned off during
    capture.
    """
    with camera_test_client() as client:
        client.exposure_time = desired_time
        print(f"Setting desired time of {desired_time}")
        time.sleep(0.5)
        pre_capture_et = client.exposure_time
        print(f"Pre-capture the time is set to {pre_capture_et}")
        # Check exp is set correctly within known tolerance
        assert abs(pre_capture_et - desired_time) < EXPOSURE_TOL

        for i in range(10):
            client.capture(capture_mode="full")
            if i == 0:
                # Exposure can update on first capture, due to frame rate restrictions
                first_et = client.exposure_time
                assert abs(first_et - desired_time) < EXPOSURE_TOL
            else:
                frame_et = client.exposure_time
                print(f"Frame {i} captured with exposure time {frame_et}")
                # Check no further drift in value
                assert first_et == frame_et

        # Set the exposure time to the value it already is. To check it doesn't shift
        print(f"Setting exposure time to {frame_et} to check it doesn't change")
        client.exposure_time = frame_et
        time.sleep(0.5)
        # Check before and after capture
        assert client.exposure_time == frame_et
        client.capture(capture_mode="full")
        assert client.exposure_time == frame_et
        print("Exposure time didn't change!!")
    print(f"End of test for exposure target {desired_time}")


def test_exposure_time_drift():
    """Performs the exposure time test for a range of exposure time values."""
    for desired_time in [100, 1000, 10000]:
        _test_exposure_time_drift(desired_time)


def test_exposure_time_on_start_and_stop_stream():
    """Start and stop stream in the same way a scan does and check exposure doesn't drift.

    Take images using capture_to_memory() just as a scan would.
    """
    desired_time = 1000
    with camera_test_client() as client:
        # Set a desired exposure time
        client.exposure_time = desired_time
        print(f"Setting desired time of {desired_time}")
        time.sleep(0.5)
        # Take a couple of images to make sure that the exposure is adjusted to
        # a hardware compatible value.
        for _i in range(2):
            client.capture(capture_mode="full")
        # Save this time.
        set_time = client.exposure_time
        assert abs(set_time - desired_time) < EXPOSURE_TOL

        # Mimic doing 10 smart scans. Change to full res, take some images. Return
        # to standard preview resolution.
        for i in range(10):
            print(f"Starting simulation scan {i}")
            # This will need updating if we start supporting other Picamera models
            # It is currently used here to mimic the behaviour in in scanning.
            client.change_streaming_mode(mode="full_resolution")
            time.sleep(0.5)
            for _j in range(5):
                client.capture_to_memory(buffer_max=1)
            # Reset to main resolution
            time.sleep(0.5)
            client.change_streaming_mode(mode="default")
        # Check after all of this the exposure time is the same.
        assert client.exposure_time == set_time


def _load_camera_and_return_exposure(tmpdir: str) -> int:
    """Load a camera (using any settings in tempdir) take images and check exposure."""
    with camera_test_client(settings_folder=tmpdir) as client:
        # Set a desired exposure time
        time.sleep(0.5)
        # Take a couple of images to make sure that the exposure is adjusted to
        # a hardware compatible value.
        for _i in range(2):
            client.capture(capture_mode="full")
        # Save this time.
        return client.exposure_time


def _load_setting(setting_file: str) -> dict[str, Any]:
    """Load settings json from disk to dictionary."""
    with open(setting_file, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _save_setting(settings: dict[str, Any], setting_file: str):
    """Save settings dictionary to disk."""
    with open(setting_file, "w", encoding="utf-8") as file_obj:
        json.dump(settings, file_obj)


def _camera_setting_file_basename():
    """Start a test env to get the setting file name of the camera."""
    with camera_test_env() as env:
        camera = env.get_thing_by_type(StreamingPiCamera2)
        return os.path.basename(camera._thing_server_interface.settings_file_path)


def test_exposure_time_saves_and_loads():
    """Check that exposure time saves to disk and loads correctly."""
    setting_file_basename = _camera_setting_file_basename()
    with tempfile.TemporaryDirectory() as tmpdir:
        setting_file = os.path.join(tmpdir, "camera", setting_file_basename)

        # Create a server, take some images, and get the exposure time
        initial_exposure = _load_camera_and_return_exposure(tmpdir)
        settings = _load_setting(setting_file)
        assert settings["exposure_time"] == initial_exposure

        # Adjust exposure to 1000 and save
        settings["exposure_time"] = 1000
        _save_setting(settings, setting_file)

        # Create a server, take some images, and get the exposure time
        recorded_exposure = _load_camera_and_return_exposure(tmpdir)
        settings = _load_setting(setting_file)
        assert settings["exposure_time"] == recorded_exposure
        # Check it was set correctly within tolerance
        assert abs(recorded_exposure - 1000) < EXPOSURE_TOL

        # Load a second time without changing the file. Exposure should not change
        recorded_exposure_second_load = _load_camera_and_return_exposure(tmpdir)
        settings = _load_setting(setting_file)
        assert settings["exposure_time"] == recorded_exposure_second_load
        # Check it was set to exactly the value previously saved to disk
        assert recorded_exposure == recorded_exposure_second_load

        # Repeat with 2000
        settings["exposure_time"] = 2000
        _save_setting(settings, setting_file)

        # Create a server, take some images, and get the exposure time
        recorded_exposure = _load_camera_and_return_exposure(tmpdir)
        settings = _load_setting(setting_file)
        assert settings["exposure_time"] == recorded_exposure
        # Check it was set correctly within tolerance
        assert abs(recorded_exposure - 2000) < EXPOSURE_TOL

        # Load a second time without changing the file. Exposure should not change
        recorded_exposure_second_load = _load_camera_and_return_exposure(tmpdir)
        settings = _load_setting(setting_file)
        assert settings["exposure_time"] == recorded_exposure_second_load
        # Check it was set to exactly the value previously saved to disk
        assert recorded_exposure == recorded_exposure_second_load
