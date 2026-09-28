"""Tests for interactions with tuning files and dictionaries."""

import json
from copy import deepcopy

import numpy as np
import pytest

from openflexure_microscope_server.things.camera import (
    picamera_tuning_file_utils as tf_utils,
)

RNG = np.random.default_rng()


def test_explicit_pisp_profile_and_complete_tables(tmp_path, imx477_tuning):
    """Keep the Pi 5 target and all 1024 entries; never reuse VC4-sized tables."""
    tuning = deepcopy(imx477_tuning)
    tuning["target"] = "pisp"
    tuning = tf_utils.flatten_lst(tuning)
    path = tmp_path / "imx477.json"
    path.write_text(json.dumps(tuning))
    loaded = tf_utils.load_default_tuning("imx477", tuning_path=str(path))
    assert loaded["target"] == "pisp"
    tables = tf_utils.get_lst(loaded)
    assert np.shape(tables.luminance) == (32, 32)
    assert np.shape(tables.Cr) == (32, 32)
    with pytest.raises(ValueError, match="32x32"):
        tf_utils.set_lst(
            loaded, luminance=np.ones((12, 16)), cr=None, cb=None, colour_temp=5000
        )


def test_explicit_missing_tuning_is_not_silently_replaced(tmp_path):
    """A missing selected profile must fail instead of falling back to the wrong ISP."""
    with pytest.raises(tf_utils.TuningFileError):
        tf_utils.load_default_tuning(
            "imx477", tuning_path=str(tmp_path / "missing.json")
        )


@pytest.fixture
def imx219_tuning():
    """Load the default tuning file for the PiCamera v2."""
    return tf_utils.load_default_tuning("imx219")


@pytest.fixture
def imx477_tuning():
    """Load the default tuning file for the PiCamera HQ."""
    return tf_utils.load_default_tuning("imx477")


def test_load_tuning(imx219_tuning, imx477_tuning):
    """Check the sctandard tuning files load and contain the correct information."""
    assert imx219_tuning != imx477_tuning

    for tuning in [imx219_tuning, imx477_tuning]:
        assert tuning["version"] == 2.0
        assert tuning["target"] == "bcm2835"
        assert isinstance(tuning["algorithms"], list)

    # The algorithms in order, and whether we expect them to be the same for the cameras.
    algos = [
        ("rpi.black_level", True),
        ("rpi.dpc", True),
        ("rpi.lux", True),
        ("rpi.noise", True),
        ("rpi.geq", True),
        ("rpi.sdn", True),
        ("rpi.awb", True),
        ("rpi.agc", True),
        ("rpi.alsc", False),
        ("rpi.contrast", True),
        ("rpi.ccm", False),
        ("rpi.sharpen", True),
        ("rpi.hdr", True),
        ("rpi.sync", True),
    ]

    # Strict= true so this will error if the tuning files are not the same length as
    # the algorithms
    zipped_algos = zip(
        algos, imx219_tuning["algorithms"], imx477_tuning["algorithms"], strict=True
    )

    # Split up the algorithm name, either we expect them to be the same in both files
    # and the algorithm for the two tuning files.
    for (algo_name, algo_same), algo219, algo477 in zipped_algos:
        # Check this is the correct algorithm
        assert algo_name in algo219
        assert algo_name in algo477
        # Check they are they are the same if expected to be.
        if algo_same:
            assert algo219 == algo477
        else:
            assert algo219 != algo477


def test_find_tuning_algo_v1():
    """Check tuning algorithms are read directly from dict if no version is set."""
    # Version 1 onf the tuning file was just a dictionary of algorithms
    mock_tuning = {"mock": {"param": 1}, "foo": {"param": "bar"}}
    assert tf_utils.find_tuning_algo(mock_tuning, "mock") == {"param": 1}
    assert tf_utils.find_tuning_algo(mock_tuning, "foo") == {"param": "bar"}
    with pytest.raises(KeyError, match="No algorithm who in tuning."):
        assert tf_utils.find_tuning_algo(mock_tuning, "who")

    # Also check that if version is not 1 it is read differently.
    mock_tuning["version"] = 2
    with pytest.raises(KeyError, match="A v2 tuning file must specify"):
        assert tf_utils.find_tuning_algo(mock_tuning, "mock")


def test_find_tuning_algo_v2():
    """Check reading version 2 dictionary structure."""
    mock_tuning = {
        "version": 2.0,
        "algorithms": [{"mock": {"param": 1}}, {"foo": {"param": "bar"}}],
    }
    assert tf_utils.find_tuning_algo(mock_tuning, "mock") == {"param": 1}
    assert tf_utils.find_tuning_algo(mock_tuning, "foo") == {"param": "bar"}
    with pytest.raises(KeyError, match="No algorithm who in tuning."):
        assert tf_utils.find_tuning_algo(mock_tuning, "who")


def _assert_lst_table_equivalence(array, table):
    """Check equivalence between input numpy array and output table."""
    for i in range(12):
        for j in range(16):
            assert table[i][j] == round(float(array[i, j]), 3)
            assert table[i][j] == round(float(array[i, j]), 3)


def test_tuning_lst_tools(imx219_tuning, imx477_tuning):
    """Check reading the default lens shading tables."""
    for tuning in (imx219_tuning, imx477_tuning):
        assert not tf_utils.lst_calibrated(tuning)

        lst_model = tf_utils.get_lst(tuning)
        assert isinstance(lst_model, tf_utils.LensShadingModel)
        # Check the tables are lists of lists
        assert isinstance(lst_model.luminance, list)
        assert isinstance(lst_model.luminance[0], list)
        assert isinstance(lst_model.Cr, list)
        assert isinstance(lst_model.Cr[0], list)
        assert isinstance(lst_model.Cb, list)
        assert isinstance(lst_model.Cb[0], list)
        # Loaded table has default lens tuning,
        assert lst_model.colour_temp == tf_utils.DEFAULT_COLOUR_TEMP


def test_tuning_initial_colour_gains(imx219_tuning, imx477_tuning):
    """The initial colour gains are as expected."""
    assert tf_utils.get_colour_gains_from_lst(imx219_tuning) == (1.068, 1.259)
    assert tf_utils.get_colour_gains_from_lst(imx477_tuning) == (1.0, 2.0)


def test_setting_lst(imx219_tuning):
    """Check that the LST can be set."""
    lum = RNG.normal(size=(12, 16))
    cr = RNG.normal(size=(12, 16))
    cb = RNG.normal(size=(12, 16))

    updated_tuning = tf_utils.set_lst(
        imx219_tuning,
        luminance=lum,
        cr=cr,
        cb=cb,
        colour_temp=tf_utils.CALIBRATED_COLOUR_TEMP,
    )

    # Check it wasn't modified in place.
    assert updated_tuning != imx219_tuning
    # Check it now reports as calibrated
    assert tf_utils.lst_calibrated(updated_tuning)

    lst_model = tf_utils.get_lst(updated_tuning)
    assert lst_model.colour_temp == tf_utils.CALIBRATED_COLOUR_TEMP
    # Check the numbers are equivalent from the input array and the output table
    # except for formatting and rounding.
    _assert_lst_table_equivalence(lum, lst_model.luminance)
    _assert_lst_table_equivalence(cr, lst_model.Cr)
    _assert_lst_table_equivalence(cb, lst_model.Cb)

    # Flatten the lens shading tables
    flat_tuning = tf_utils.flatten_lst(updated_tuning)
    # In this case only the colour channels
    flat_chroma_tuning = tf_utils.flatten_lst(updated_tuning, keep_luminance=True)

    # Check each was changed
    assert updated_tuning != flat_tuning
    assert updated_tuning != flat_chroma_tuning
    assert flat_tuning != flat_chroma_tuning

    # Check the flattened tunings report as not calibrated
    assert not tf_utils.lst_calibrated(flat_tuning)
    assert not tf_utils.lst_calibrated(flat_chroma_tuning)

    flat_lst_model = tf_utils.get_lst(flat_tuning)
    flat_chroma_lst_model = tf_utils.get_lst(flat_chroma_tuning)

    flat_array = np.ones((12, 16))

    # Check the that it really was flattened for the flat tuning
    _assert_lst_table_equivalence(flat_array, flat_lst_model.luminance)
    _assert_lst_table_equivalence(flat_array, flat_lst_model.Cr)
    _assert_lst_table_equivalence(flat_array, flat_lst_model.Cb)

    # Check only colour channels flattened when keep_luminance=True
    _assert_lst_table_equivalence(lum, flat_chroma_lst_model.luminance)
    _assert_lst_table_equivalence(flat_array, flat_chroma_lst_model.Cr)
    _assert_lst_table_equivalence(flat_array, flat_chroma_lst_model.Cb)


def test_setting_ccm(imx219_tuning):
    """Check the colour correction matrix can be set."""
    # CCM is set as a flat list. Create a mock ccm
    new_ccm = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]

    updated_tuning = tf_utils.set_ccm(imx219_tuning, new_ccm)

    # Check it wasn't modified in place.
    assert updated_tuning != imx219_tuning

    # Check the value was set
    assert tf_utils.get_ccm(updated_tuning) == new_ccm

    # Check that our dictionary doesn't update if the input list is updated.
    new_ccm[0] = 0.0
    assert tf_utils.get_ccm(updated_tuning) != new_ccm

    new_ccm.append(1.0)
    with pytest.raises(
        ValueError, match="col_corr_matrix should be a list of 9 floats"
    ):
        tf_utils.set_ccm(imx219_tuning, new_ccm)


def test_green_equalisation(imx219_tuning):
    """Test setting the offset parameter of green equalisation."""
    assert tf_utils.geq_is_static(imx219_tuning)
    bad_geq_tuning = tf_utils.set_static_geq(imx219_tuning, offset=1234)
    # Check it wasn't modified in place.
    assert bad_geq_tuning != imx219_tuning

    # No longer static
    assert not tf_utils.geq_is_static(bad_geq_tuning)

    fixed_geq_tuning = tf_utils.set_static_geq(bad_geq_tuning)

    assert tf_utils.geq_is_static(fixed_geq_tuning)


def test_ce_enable(imx219_tuning):
    """Check the setting of ce enable in the contrast algorithm."""
    assert tf_utils.ce_enable_is_static(imx219_tuning)

    # No way to enable it so do it manually
    bad_tuning = deepcopy(imx219_tuning)
    contrast = tf_utils.find_tuning_algo(bad_tuning, "rpi.contrast")
    contrast["ce_enable"] = 1

    assert not tf_utils.ce_enable_is_static(bad_tuning)

    fixed_tuning = tf_utils.set_ce_to_disabled(bad_tuning)
    assert tf_utils.ce_enable_is_static(fixed_tuning)


def test_copying_algorithms(imx219_tuning):
    """Check an algorithm can be copied from one file to another."""
    # Update the CCM
    new_ccm = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    updated_ccm_tuning = tf_utils.set_ccm(imx219_tuning, new_ccm)
    # It has changed
    assert updated_ccm_tuning != imx219_tuning

    # Create another tuning file with the GEQ updated.
    updated_geq_tuning = tf_utils.set_static_geq(imx219_tuning, offset=1234)
    # It has changed
    assert updated_geq_tuning != imx219_tuning

    # Copy the CCM from the dict with the bad GEQ value into the one with the updated
    # CCM
    original_tuning = tf_utils.copy_algo_from_other_tuning(
        "rpi.ccm", base_tuning_file=updated_ccm_tuning, copy_from=updated_geq_tuning
    )

    # This should now be equivalent to the original.
    assert original_tuning == imx219_tuning
