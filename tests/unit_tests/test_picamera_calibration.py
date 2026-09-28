"""Server-side regression tests for VC4/PiSP RAW and the native calibration path."""

from contextlib import contextmanager
from copy import deepcopy

import numpy as np
import pytest


@pytest.fixture
def recalibrate(mock_picam_thing):
    """Use the existing Picamera2 import fixture; no camera hardware is opened."""
    from openflexure_microscope_server.things.camera import picamera_recalibrate_utils

    assert mock_picam_thing is not None
    return picamera_recalibrate_utils


def raw_frame(sensor_bits=12, storage_bits=12, order="BGGR", level=None):
    """Build an unpacked frame with deliberately poisonous stride padding."""
    values = {"B": 100, "G": 200, "R": 300}
    pixels = np.empty((8, 12), dtype=np.uint16)
    for i, colour in enumerate(order):
        pixels[i // 2 :: 2, i % 2 :: 2] = values[colour] if level is None else level
    if storage_bits == 16:
        pixels <<= 16 - sensor_bits
    padded = np.full((8, 16), 65535, dtype=np.uint16)
    padded[:, :12] = pixels
    config = {
        "raw": {"format": f"S{order}{storage_bits}", "size": (12, 8), "stride": 32},
        "sensor": {"bit_depth": sensor_bits},
    }
    return padded, config


@pytest.mark.parametrize("sensor_bits", [10, 12])
@pytest.mark.parametrize("pisp", [False, True])
@pytest.mark.parametrize("order", ["BGGR", "GBRG", "GRBG", "RGGB"])
def test_raw_sensor_levels_bayer_order_and_padding(
    recalibrate, sensor_bits, pisp, order
):
    """Both Pis produce identical sensor-level channels, excluding all padding."""
    sensor = (
        recalibrate.IMX477_SENSOR_INFO
        if sensor_bits == 12
        else recalibrate.IMX219_SENSOR_INFO
    )
    words, config = raw_frame(sensor_bits, 16 if pisp else sensor_bits, order)
    for image in (words, words.view(np.uint8)):
        channels = recalibrate._channels_from_bayer_array(image, config, sensor)
        assert channels.shape == (4, 4, 6)
        np.testing.assert_array_equal(
            channels,
            np.broadcast_to(
                np.array([100, 200, 200, 300])[:, None, None], channels.shape
            ),
        )


@pytest.mark.parametrize("bad_format", ["BGGR16_PISP_COMP1", "SBGGR12_CSI2P", "RGB888"])
def test_reject_non_unpacked_raw(recalibrate, bad_format):
    """Reject compressed/processed formats instead of treating bytes as sensor levels."""
    image, config = raw_frame()
    config["raw"]["format"] = bad_format
    with pytest.raises(ValueError, match="unpacked Bayer"):
        recalibrate._channels_from_bayer_array(
            image, config, recalibrate.IMX477_SENSOR_INFO
        )


def test_reject_mismatched_raw_geometry_and_depth(recalibrate):
    """Reject settings that cannot match the calibration's Bayer layout or scale."""
    image, config = raw_frame()
    sensor = recalibrate.IMX477_SENSOR_INFO
    config["sensor"]["bit_depth"] = 10
    with pytest.raises(ValueError, match="bit depth"):
        recalibrate._channels_from_bayer_array(image, config, sensor)
    config["sensor"]["bit_depth"] = 12
    config["raw"]["size"] = (11, 8)
    with pytest.raises(ValueError, match="even"):
        recalibrate._channels_from_bayer_array(image, config, sensor)
    config["raw"]["size"] = (18, 8)
    with pytest.raises(ValueError, match="dimensions"):
        recalibrate._channels_from_bayer_array(image, config, sensor)


@pytest.mark.parametrize("grid_shape", [(12, 16), (32, 32)])
def test_full_grid_and_flat_field_correction(recalibrate, grid_shape):
    """Correct nonuniform illumination and channel gains with the full ISP grid."""
    y, x = np.mgrid[:96, :128]
    light = 600 + 3 * y + x
    channels = np.stack([light * 0.5, light, light, light * 0.8]) + 256
    grids = recalibrate._downsampled_channels(channels, 256, grid_shape)
    lum, cr, cb = recalibrate._lst_from_channels(channels, 256, grid_shape)
    assert lum.shape == cr.shape == cb.shape == grid_shape
    np.testing.assert_allclose(lum * grids[1], grids[1].max())
    np.testing.assert_allclose(cr, 1.25)
    np.testing.assert_allclose(cb, 2)
    # The VC4 partition matches the old CTT ceil-sized blocks including edges.
    rows, cols = grid_shape
    dy, dx = (96 + rows - 1) // rows, (128 + cols - 1) // cols
    assert grids[1, -1, -1] == np.mean(light[(rows - 1) * dy :, (cols - 1) * dx :])


@pytest.mark.parametrize("bad_level", [0, -1, np.nan, np.inf])
def test_invalid_flat_field_is_not_accepted(recalibrate, bad_level):
    """Reject bad cells before division, rather than storing infinities in tuning."""
    grids = np.ones((4, 32, 32))
    grids[0, 0, 0] = bad_level
    with pytest.raises(ValueError, match="above black level"):
        recalibrate._lst_from_grids(grids)


def test_grid_requires_nonempty_cells(recalibrate):
    """Reject undersized input instead of calculating empty-cell means."""
    with pytest.raises(ValueError, match="too small"):
        recalibrate._downsampled_channels(np.ones((4, 16, 16)), 0, (32, 32))


def mock_raw_camera(mocker, *, pisp=True, level=1856):
    """Create one request with matching data, metadata and stream configuration."""
    image, config = raw_frame(storage_bits=16 if pisp else 12, level=level)
    camera = mocker.Mock(started=False, sensor_resolution=(12, 8))
    request = camera.capture_request.return_value
    request.config = config
    request.make_array.return_value = image.view(np.uint8)
    request.get_metadata.return_value = {"ExposureTime": 1000, "AnalogueGain": 1.0}
    return camera, request


@pytest.mark.parametrize("pisp", [False, True])
def test_exposure_uses_sensor_units_and_subtracts_black_once(recalibrate, mocker, pisp):
    """Measure the same brightness on Pi4 and Pi5 including correct black subtraction."""
    camera, request = mock_raw_camera(mocker, pisp=pisp)
    result = recalibrate._test_exposure_settings(
        camera, recalibrate.IMX477_SENSOR_INFO, 99.9
    )
    assert result.level == 1600
    assert result.exposure_time == 1000
    request.release.assert_called_once()


def test_exposure_failure_is_reported_not_silently_accepted(recalibrate, mocker):
    """Do not continue a full wizard when the exposure never reaches its target."""
    camera, _ = mock_raw_camera(mocker, level=256)
    camera.capture_metadata.return_value = {"ExposureTime": 1000, "AnalogueGain": 1.0}
    mocker.patch.object(recalibrate.time, "sleep")
    with pytest.raises(RuntimeError, match="Failed to reach target brightness"):
        recalibrate.adjust_shutter_and_gain_from_raw(
            camera,
            recalibrate.IMX477_SENSOR_INFO,
            target_white_level=1600,
            max_iterations=1,
        )


@pytest.mark.parametrize("failure", [None, "array", "format"])
def test_raw_capture_cleanup_and_manual_controls(recalibrate, mocker, failure):
    """Preserve manual controls and release/stop even when acquisition or decode fails."""
    camera, request = mock_raw_camera(mocker)
    controls = {"ExposureTime": 1001, "AnalogueGain": 1, "AeEnable": False}
    if failure == "array":
        request.make_array.side_effect = RuntimeError("capture failed")
    elif failure == "format":
        request.config["raw"]["format"] = "BGGR16_PISP_COMP1"
    if failure:
        with pytest.raises((ValueError, RuntimeError)):
            recalibrate.capture_calibration_frame(
                camera, recalibrate.IMX477_SENSOR_INFO, controls=controls
            )
    else:
        channels, config, metadata = recalibrate.capture_calibration_frame(
            camera, recalibrate.IMX477_SENSOR_INFO, controls=controls
        )
        assert channels.max() == 1856
        assert config["raw"]["format"] == "SBGGR16"
        assert metadata["ExposureTime"] == 1000
    assert camera.create_still_configuration.call_args.kwargs["controls"] == controls
    assert "sensor" in camera.create_still_configuration.call_args.kwargs
    request.release.assert_called_once()
    camera.stop.assert_called_once()


@pytest.mark.parametrize("plural", [False, True])
def test_crop_applied_after_configuration_without_mutation(
    mock_picam_thing, mocker, plural
):
    """Override library defaults after configure on old and new control APIs."""
    from labthings_fastapi.testing import create_thing_without_server

    from openflexure_microscope_server.things.camera.picamera import PiCameraHQ

    camera = mocker.Mock(camera_controls={"ScalerCrops": None} if plural else {})
    camera.create_video_configuration.return_value = {}
    controls = {"ExposureTime": 1001}
    mode = create_thing_without_server(PiCameraHQ).streaming_modes["default"]
    mock_picam_thing._configure_picamera(camera, controls, mode)
    assert controls == {"ExposureTime": 1001}
    assert camera.method_calls[-2] == mocker.call.configure({"buffer_count": 4})
    expected = (
        {"ScalerCrops": [mode.scaler_crop, mode.scaler_crop]}
        if plural
        else {"ScalerCrop": mode.scaler_crop}
    )
    assert camera.method_calls[-1] == mocker.call.set_controls(expected)


def test_preview_is_restored_after_failed_raw_action(mock_picam_thing, mocker):
    """Resume the original stream on exceptional exits as well as successful ones."""
    mock_picam_thing.stream_active = True
    stop = mocker.patch.object(mock_picam_thing, "_stop_streaming")
    start = mocker.patch.object(mock_picam_thing, "_start_streaming")
    with (
        pytest.raises(RuntimeError),
        mock_picam_thing._streaming_picamera(pause_stream=True),
    ):
        raise RuntimeError("bad RAW")
    stop.assert_called_once_with(stop_web_stream=False)
    start.assert_called_once_with("default")


def test_failed_wizard_restores_previous_profile(mock_picam_thing, mocker):
    """Keep the last profile if a subsequent full calibration fails part-way through."""
    camera = mock_picam_thing
    original = deepcopy(camera.tuning)
    controls = {"ExposureTime": 1001, "AnalogueGain": 2, "ColourGains": (1.2, 1.5)}
    mocker.patch.object(camera, "_get_persistent_controls", return_value=controls)

    def flatten():
        camera.tuning = {"changed": True}
        camera._exposure_time = 25

    @contextmanager
    def paused(**_kwargs):
        yield None

    mocker.patch.object(camera, "_streaming_picamera", side_effect=paused)
    mocker.patch.object(camera, "flat_lens_shading", side_effect=flatten)
    mocker.patch.object(
        camera, "auto_expose_from_minimum", side_effect=RuntimeError("dark field")
    )
    reinit = mocker.patch.object(camera, "_initialise_picamera")
    with pytest.raises(RuntimeError, match="dark field"):
        camera.full_auto_calibrate()
    assert camera.tuning == original
    assert camera._exposure_time == 1000
    assert camera._analogue_gain == 2
    assert camera._colour_gains == (1.2, 1.5)
    reinit.assert_called_once()
