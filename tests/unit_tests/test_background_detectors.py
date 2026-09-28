"""Test the background detection algorithms.

This tests both the base class an individual algorithms.
"""

import re

import numpy as np
import pytest

import labthings_fastapi as lt
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.scanning.background_detect import (
    BackgroundDetectAlgorithm,
    ChannelBlankError,
    ChannelDeviationLUV,
    ColourChannelDetectLUV,
    MissingBackgroundDataError,
    _chunked_stds,
)
from openflexure_microscope_server.ui import PropertyControl

RNG = np.random.default_rng()
IMG_SHAPE = (820, 616, 3)
BG_COLOR = [220, 215, 217]

PERC_REGEX = re.compile(r"(\d+\.+\d)+%")


@pytest.fixture
def background_image():
    """Generate a numpy array that simulates a background image."""
    image = np.ones(IMG_SHAPE, dtype=np.int16)
    image[:, :, 0] *= BG_COLOR[0]
    image[:, :, 1] *= BG_COLOR[1]
    image[:, :, 2] *= BG_COLOR[2]
    image += RNG.normal(scale=3, size=IMG_SHAPE).astype("int16")
    image[image < 0] = 0
    image[image > 255] = 255
    return image.astype("uint8")


@pytest.fixture
def sample_image(background_image):
    """Generate a numpy array of a simulated image where 50% is a block of red."""
    image = background_image.copy()
    x_cent = IMG_SHAPE[0] // 2
    image[:x_cent, :, 0] -= 180
    return image


def test_bg_detect_base_class():
    """Test the base class for background detect.

    If initialised as is it should raise not implemented error.
    """
    with pytest.raises(NotImplementedError):
        create_thing_without_server(BackgroundDetectAlgorithm)


def test_partial_base_classes():
    """Create a partial class and check it raises the correct errors."""

    class BadAlgo(BackgroundDetectAlgorithm):
        """Can initialise. Other properties and methods error."""

        display_name: str = lt.property(default="Bad Algorithm", readonly=True)

    bad_algo = create_thing_without_server(BadAlgo)

    with pytest.raises(NotImplementedError):
        bad_algo.ready

    with pytest.raises(NotImplementedError):
        bad_algo.settings_ui()

    with pytest.raises(NotImplementedError):
        bad_algo.set_background(background_image)

    with pytest.raises(NotImplementedError):
        bad_algo.image_is_sample(background_image)


def test_colour_channel_luv(background_image, sample_image):
    """Test measuring if a sample is background."""
    cc_luv = create_thing_without_server(ColourChannelDetectLUV)

    # No background data so it is not ready and will error if image_is_sample is called.
    assert not cc_luv.ready
    with pytest.raises(MissingBackgroundDataError):
        cc_luv.image_is_sample(background_image)

    # Set the background
    cc_luv.set_background(background_image)
    # Now it is ready
    assert cc_luv.ready
    sample, message = cc_luv.image_is_sample(background_image)
    assert not sample
    assert "0.0%" in message
    sample, message = cc_luv.image_is_sample(sample_image)
    assert sample
    match = PERC_REGEX.search(message)
    assert match is not None
    # Should be 50% background. Allowing 49.8-50.2% due to noise.
    assert 49.8 < float(match.group(1)) < 50.2

    # Require 75% coverage
    cc_luv.min_sample_coverage = 75.0

    sample, message = cc_luv.image_is_sample(sample_image)
    # No longer detected as a sample.
    assert not sample
    match = PERC_REGEX.search(message)
    assert match is not None
    # Still 50% background.
    assert 49.8 < float(match.group(1)) < 50.2


def create_patchwork_image(magnitude=3, blank_channels=None):
    """Create a patchwork image, with known stds per chunk.

    :return: The image, and the (8,8,3) array of stds
    """
    if blank_channels is None:
        blank_channels = []
    n_rows = 8
    n_cols = 8
    chunk_h = 10
    chunk_w = 10

    # Precompute chunk stds and construct the final image
    expected_stds = np.zeros((n_rows, n_cols, 3), dtype=float)
    img = np.zeros((n_rows * chunk_h, n_cols * chunk_w, 3), dtype=np.uint8)

    for i in range(n_rows):
        for j in range(n_cols):
            for channel in range(3):
                if channel in blank_channels:
                    chunk = np.zeros((chunk_h, chunk_w), dtype=np.uint8)
                else:
                    chunk = 9.8 * np.ones((chunk_h, chunk_w))
                    chunk += RNG.normal(scale=magnitude, size=(chunk_h, chunk_w))
                    chunk = chunk.astype(np.uint8)

                # Store its std
                expected_stds[i, j, channel] = np.std(chunk)

                # Insert chunk into the final large image
                y0, y1 = i * chunk_h, (i + 1) * chunk_h
                x0, x1 = j * chunk_w, (j + 1) * chunk_w
                img[y0:y1, x0:x1, channel] = chunk
    return img, expected_stds


def test_chunked_stds_with_precomputed_chunk_stds():
    """Test _chunked_stds returns the answer calculated when making a patchwork image."""
    img, expected_stds = create_patchwork_image()

    # Run the function under test
    result = _chunked_stds(img, n_rows=8, n_cols=8)

    # Compare
    np.testing.assert_allclose(result, expected_stds, rtol=1e-6, atol=1e-12)


def test_channel_deviation_luv_set_background(mocker):
    """Test set_background takes the median of each channel, and errors for blank channels."""
    cd_luv = create_thing_without_server(ChannelDeviationLUV)

    # Patch RGB to LUV so or we don't know what the STDs should be
    mocker.patch("cv2.cvtColor", side_effect=lambda img, _method: img)

    for magnitude in [0.2, 1, 10]:
        # Create an image and set it as background
        img, expected_stds = create_patchwork_image(magnitude=magnitude)
        cd_luv.set_background(img)

        # Do a somewhat verbose checking for clarity
        for channel in range(3):
            # Saved std
            channel_std = cd_luv.background_stds[channel]
            # Expected median
            channel_median = np.median(expected_stds[:, :, channel])
            # If the median is above the minimum allowed then it should be returned
            if channel_median > cd_luv.min_stds[channel]:
                assert np.isclose(channel_std, channel_median, rtol=1e-6, atol=1e-12)
            else:
                # If not the minimum is returned.
                assert channel_std == cd_luv.min_stds[channel]

    # Also check that if channels are blank then an error is thrown
    img, _expected_stds = create_patchwork_image(magnitude=1, blank_channels=[1, 2])
    with pytest.raises(ChannelBlankError):
        cd_luv.set_background(img)


def test_channel_deviation_luv_image_is_sample(background_image, mocker):
    """Check image_is_sample reports the result from get_sample_coverage."""
    cd_luv = create_thing_without_server(ChannelDeviationLUV)

    # No background data so it is not ready and will error if image_is_sample is called.
    assert not cd_luv.ready
    with pytest.raises(MissingBackgroundDataError):
        cd_luv.image_is_sample(background_image)

    cd_luv.min_sample_coverage = 20
    cd_luv.get_sample_coverage = mocker.Mock(return_value=10)
    is_sample, message = cd_luv.image_is_sample(background_image)
    assert not is_sample
    assert message == r"only 10.0% sample"

    # Reduce the min coverage
    cd_luv.min_sample_coverage = 9

    is_sample, message = cd_luv.image_is_sample(background_image)
    assert is_sample
    assert message == r"10.0% sample"


def test_channel_deviation_luv_get_sample_coverage(background_image, mocker):
    """Check _get_sample_coverage returns the values expected."""
    cd_luv = create_thing_without_server(ChannelDeviationLUV)

    # Create fake chunked STD data where each channel is the numbers 0 -> 31.5 in 0.5
    # steps
    grid = np.arange(0, 32, 0.5).reshape(8, 8)
    fake_stds = np.stack([grid, grid, grid], axis=-1)
    mocker.patch(
        "openflexure_microscope_server.things.scanning.background_detect._chunked_stds",
        return_value=fake_stds,
    )

    # Create fake background
    cd_luv.background_stds = [1.1, 1.1, 1.1]

    # Get sample coverage with channel tolerance of 7. Checking each channel for the
    # numbers below 7.7. There are 16 out of 64. So 75% should be sample
    cd_luv.channel_tolerance = 7
    assert cd_luv.get_sample_coverage(background_image) == 75
    # This is unchanged if two channels have larger background values.
    cd_luv.background_stds = [1.6, 1.6, 1.1]
    assert cd_luv.get_sample_coverage(background_image) == 75
    # But coverage increases if any channels has a lower background value.
    cd_luv.background_stds = [1.6, 0.6, 1.1]
    assert cd_luv.get_sample_coverage(background_image) == 85.9375
    # Returns to 75% if that channel is empty
    fake_stds[:, :, 1] = 0
    assert cd_luv.get_sample_coverage(background_image) == 75


@pytest.mark.parametrize("detector_cls", [ColourChannelDetectLUV, ChannelDeviationLUV])
def test_background_detect_settings_ui(detector_cls):
    """Check that both background detectors provide the expected UI to the webapp.

    As both have identical settings they can be tested together
    """
    detector = create_thing_without_server(detector_cls)
    ui = detector.settings_ui().root

    assert len(ui) == 2
    assert isinstance(ui[0], PropertyControl)
    assert ui[0].property_name == "channel_tolerance"
    assert ui[0].label == "Channel Tolerance"
    assert isinstance(ui[1], PropertyControl)
    assert ui[1].property_name == "min_sample_coverage"
    assert ui[1].label == "Sample Coverage Required (%)"
