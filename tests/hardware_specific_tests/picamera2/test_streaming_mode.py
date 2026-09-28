"""Test changing and setting camerat modes on the Raspberry Picamera."""

import logging

import numpy as np

from .cam_test_utils import camera_test_client

logging.basicConfig(level=logging.DEBUG)


def test_streaming_mode():
    """Test capturing raw arrays in two different sensor modes."""
    with camera_test_client() as client:
        for mode, res in ["default", (820, 616)], ["full_resolution", (3280, 2464)]:
            client.change_streaming_mode(mode=mode)
            arr = np.array(client.capture_as_array(capture_mode="quick"))
            # Check that the array dimensions match the requested image size.
            # Note: Numpy array shape is (y,x), but the sensor is set with (x,y)
            # hence the need to compare index 0 with index 1.
            assert arr.shape[0] == res[1]
