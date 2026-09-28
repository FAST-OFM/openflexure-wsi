"""Test the functionality in the scan_directories module."""

import json
import logging
import math
import os
import random
import shutil
import tempfile
import time

import pytest

from openflexure_microscope_server.gallery_db import GalleryEntry
from openflexure_microscope_server.scanning.scan_directories import (
    SCAN_DATA_FILENAME,
    ScanDirectory,
    ScanGalleryDBEngine,
    ScanGalleryInfo,
    get_files_in_zip,
)

from .test_scan_data import (
    assert_active_and_historic_data_equivalent,
    fake_active_scan_data,
)
from .utilities import assert_unique_of_length

# Use our own dir in the root temp dir not a dynamically generated one so we
# have some control of when it is deleted
BASE_SCAN_DIR = os.path.join(tempfile.gettempdir(), "scans")


def _clear_scan_dir() -> None:
    """Delete the scan dir."""
    if os.path.exists(BASE_SCAN_DIR):
        shutil.rmtree(BASE_SCAN_DIR)
    os.makedirs(BASE_SCAN_DIR)


def _add_fake_image(scan_dir: ScanDirectory) -> None:
    """Make a fake image on disk in the scan directory."""
    unique = False
    while not unique:
        x_pos = random.randint(-10000, 10000)
        y_pos = random.randint(-10000, 10000)
        filename = f"image_{x_pos}_{y_pos}.jpg"
        filepath = os.path.join(scan_dir.images_dir, filename)
        unique = not os.path.exists(filepath)
    with open(filepath, "w") as f_obj:
        f_obj.write("fake")


def _add_fake_file(
    scan_dir: ScanDirectory, filename: str, in_im_dir: bool = False
) -> None:
    """Make a fake file on disk in the scan directory.

    :param in_im_dir: Boolean, if set True the fake file is created in the images
    directory of the scan not the root directory.
    """
    if in_im_dir:
        filepath = os.path.join(scan_dir.images_dir, filename)
    else:
        filepath = os.path.join(scan_dir.dir_path, filename)
    with open(filepath, "w") as f_obj:
        f_obj.write("fake")


def _make_fake_dzi(scan_dir: ScanDirectory, n_layers: int = 8) -> None:
    """Create a fake DZI in a scan.

    :param n_layers: The number of layers of tiles. I.e. tile directories numbered
        0...(n_layers-1) will be created. Default 8
    """
    # Add an a dzi image
    dzi_fname = scan_dir.name + ".dzi"
    dzi_path = os.path.join(scan_dir.images_dir, dzi_fname)
    with open(dzi_path, "w") as f_obj:
        f_obj.write("This should be xml")

    # and the directory for the dzi tiles
    dzi_tile_dir = scan_dir.name + "_files"
    dzi_tile_dir_path = os.path.join(scan_dir.images_dir, dzi_tile_dir)
    os.makedirs(dzi_tile_dir_path)

    # this directory then contains a directories numbered from 0-n
    for i in range(n_layers):
        layer_dir_path = os.path.join(dzi_tile_dir_path, str(i))
        os.makedirs(layer_dir_path)

        # Number of images (in each axis) in this layer. Number of tiles doubles each
        # layer, but the first few layers always have 1 image.
        n_ims = math.ceil(2 ** (i - 4))
        for x_index in range(n_ims):
            for y_index in range(n_ims):
                tile_name = f"{x_index}_{y_index}.jpg"
                tile_path = os.path.join(layer_dir_path, tile_name)
                with open(tile_path, "w") as f_obj:
                    f_obj.write("This would normally be jpeg data")


def test_bad_scan_names():
    """Check scan names with spaces, or worse BASH commands are not allowed."""
    _clear_scan_dir()
    scan_dir = ScanDirectory.new_scan_dir("fake_scan_0001", BASE_SCAN_DIR)
    assert scan_dir.name == "fake_scan_0001"
    with pytest.raises(ValueError, match=r".*contains unsafe characters."):
        scan_dir = ScanDirectory.new_scan_dir("fake scan;rm -rf /;", BASE_SCAN_DIR)


@pytest.fixture
def db_engine(mocker):
    """Clear scan directory and yield a new gallery database engine."""
    _clear_scan_dir()
    mock_thing = mocker.Mock()
    mock_thing.name = "mock"
    engine = ScanGalleryDBEngine(BASE_SCAN_DIR, mock_thing)
    yield engine
    engine.dispose()


def _make_dir_and_add_to_db(scan_name, db_engine, scan_data=None, force_unique=True):
    """Create a scan dir and add it to the database.

    A helper function, can aslso provide scan data.
    """
    name = db_engine.unique_scan_name(scan_name) if force_unique else scan_name
    scan_dir = ScanDirectory.new_scan_dir(name, BASE_SCAN_DIR)
    if scan_data is not None:
        scan_dir.save_scan_data(scan_data)
    db_engine.add(name)
    return scan_dir


def test_scan_sequence_and_listing(db_engine, caplog):
    """Check created scans are added in order and listed correctly."""
    # Create some scan data and mark it as successful to get an end date.
    scan_data = fake_active_scan_data()
    scan_data.set_final_data(result="Success")
    # Make 4 scans
    for _i in range(4):
        _make_dir_and_add_to_db("fake_scan", db_engine, scan_data=scan_data)

    # Check they exist and are numbered sequentially
    all_scans = db_engine.get_all_paths()
    assert_unique_of_length(all_scans, 4)
    assert "fake_scan_0001" in all_scans
    assert "fake_scan_0002" in all_scans
    assert "fake_scan_0003" in all_scans
    assert "fake_scan_0004" in all_scans

    with caplog.at_level(logging.WARNING):
        # Check entries can be read for all of them
        # (more detailed scan_info tests below)

        for entry in db_engine.get_all_entries():
            assert isinstance(entry, GalleryEntry)
            assert entry.path.startswith("fake_scan_000")
            scan_info = ScanGalleryInfo.model_validate(entry.gallery_info)
            assert scan_info.number_of_images == 0
            assert scan_info.duration is not None
        # There are no warnings or errors
        assert len(caplog.records) == 0


def test_scan_sequence_case_sensitive(db_engine):
    """Check created scans are added in order and listed correctly, even when cases are different."""
    # Create some scan data and mark it as successful to get an end date.
    scan_data = fake_active_scan_data()
    scan_data.set_final_data(result="Success")

    _make_dir_and_add_to_db("fake_scan", db_engine, scan_data=scan_data)
    _make_dir_and_add_to_db("FAKE_SCAN", db_engine, scan_data=scan_data)
    _make_dir_and_add_to_db("Fake_Scan", db_engine, scan_data=scan_data)
    _make_dir_and_add_to_db("FAKE_scan", db_engine, scan_data=scan_data)
    _make_dir_and_add_to_db("fAkE_sCaN", db_engine, scan_data=scan_data)

    # Check they exist and are numbered sequentially
    all_scans = db_engine.get_all_paths()
    assert_unique_of_length(all_scans, 5)
    assert "fake_scan_0001" in all_scans
    assert "fake_scan_0002" in all_scans
    assert "fake_scan_0003" in all_scans
    assert "fake_scan_0004" in all_scans
    assert "fake_scan_0005" in all_scans


def test_scan_names_have_single_underscores(db_engine):
    """Check created scans have only one underscore in them, not two."""
    # Create some scan data and mark it as successful to get an end date.
    scan_data = fake_active_scan_data()
    scan_data.set_final_data(result="Success")

    _make_dir_and_add_to_db("fake_scan_", db_engine, scan_data=scan_data)
    _make_dir_and_add_to_db("fake_scan__", db_engine, scan_data=scan_data)
    _make_dir_and_add_to_db("fake_scan_______", db_engine, scan_data=scan_data)
    _make_dir_and_add_to_db("fake_scan", db_engine, scan_data=scan_data)

    # Check they exist and are numbered sequentially
    all_scans = db_engine.get_all_paths()
    assert_unique_of_length(all_scans, 4)
    assert "fake_scan_0001" in all_scans
    assert "fake_scan_0002" in all_scans
    assert "fake_scan_0003" in all_scans
    assert "fake_scan_0004" in all_scans


def test_scan_name_non_sequential(db_engine):
    """Check new scan has the correct name if the directories are not sequential."""
    # Create a number of non-sequential scans
    _make_dir_and_add_to_db("fake_scan_0001", db_engine, force_unique=False)
    _make_dir_and_add_to_db("fake_scan_0002", db_engine, force_unique=False)
    _make_dir_and_add_to_db("fake_scan_0003", db_engine, force_unique=False)
    _make_dir_and_add_to_db("fake_scan_0005", db_engine, force_unique=False)
    _make_dir_and_add_to_db("fake_scan_0007", db_engine, force_unique=False)
    _make_dir_and_add_to_db("fake_scan_0011", db_engine, force_unique=False)

    # Now make one forcing a inique name
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine, force_unique=True)
    assert scan_dir.name == "fake_scan_0012"

    all_scans = db_engine.get_all_paths()
    assert_unique_of_length(all_scans, 7)
    assert "fake_scan_0001" in all_scans
    assert "fake_scan_0002" in all_scans
    assert "fake_scan_0003" in all_scans
    assert "fake_scan_0005" in all_scans
    assert "fake_scan_0007" in all_scans
    assert "fake_scan_0011" in all_scans
    assert "fake_scan_0012" in all_scans


def test_all_scan_names_taken(db_engine):
    """If the next sequential scan name needs more than 4 digits check error is thrown."""
    _make_dir_and_add_to_db("fake_scan_9999", db_engine, force_unique=False)
    with pytest.raises(FileExistsError):
        _make_dir_and_add_to_db("fake_scan", db_engine, force_unique=True)


def test_no_scan_names_given(db_engine):
    """Check correct default scan name is used if empty string is given."""
    scan_dir = _make_dir_and_add_to_db("", db_engine, force_unique=True)
    assert scan_dir.name == "scan_0001"


def test_scan_info(db_engine):
    """Test the scan info is correct even using fake scan data."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine, force_unique=True)

    for _i in range(17):
        _add_fake_image(scan_dir)
    info = scan_dir.scan_info()

    assert scan_dir.name == "fake_scan_0001"

    assert info.number_of_images == 17
    assert not info.stitch_available
    assert info.stitched_tiff is None
    assert info.dzi is None

    # Add a fake stitched images and check this is recognised as a stitch not
    # a scan image
    _add_fake_file(scan_dir, "fake_scan_0001_stitched.jpg", in_im_dir=True)

    # Make the database rebuild the record now it changed
    db_engine.rebuild("fake_scan_0001")
    # Then fetch
    entry = db_engine.get("fake_scan_0001")
    info = ScanGalleryInfo.model_validate(entry.gallery_info)
    assert info.number_of_images == 17
    assert info.stitch_available
    assert info.stitched_jpeg == "images/fake_scan_0001_stitched.jpg"
    assert info.stitched_tiff is None
    # Created and modified in the last 5 seconds
    now = time.time()
    assert now - 5 < entry.created < now
    assert now - 5 < entry.modified < now


def test_get_final_stitch(db_engine):
    """Check that the final stitch can be retrieved."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine)

    # Create some scan images files
    for _i in range(17):
        _add_fake_image(scan_dir)

    # No scans, so None should be returned, from both database and scan dir
    assert scan_dir.get_final_stitch_name() is None
    assert db_engine.get("fake_scan_0001").gallery_info["stitched_jpeg"] is None
    assert db_engine.get("fake_scan_0001").gallery_info["stitched_tiff"] is None

    fake_scan_name = "fake_scan_0001_stitched.jpg"

    # Add a fake scan
    _add_fake_file(scan_dir, fake_scan_name, in_im_dir=True)
    # And rebuild the database entry
    db_engine.rebuild("fake_scan_0001")

    # ScanDirectory object returns just the filename
    assert scan_dir.get_final_stitch_name() == fake_scan_name
    # database the includes the directory
    db_final_stitch = db_engine.get("fake_scan_0001").gallery_info["stitched_jpeg"]
    assert db_final_stitch == "images/" + fake_scan_name

    fake_tiff_name = "fake_scan_0001_stitched.ome.tiff"
    _add_fake_file(scan_dir, fake_tiff_name, in_im_dir=True)
    db_engine.rebuild("fake_scan_0001")

    # A pyramidal TIFF is canonical when both new and legacy outputs exist.
    assert scan_dir.get_final_stitch_name() == fake_tiff_name
    gallery_info = db_engine.get("fake_scan_0001").gallery_info
    assert gallery_info["stitched_tiff"] == "images/" + fake_tiff_name
    assert gallery_info["stitched_jpeg"] == "images/" + fake_scan_name


def test_get_scan_data_path(db_engine):
    """Check that a scan data path behaves as expected."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine)
    expected_path = os.path.join(scan_dir.images_dir, SCAN_DATA_FILENAME)

    # Asking the scan directly will return the location the file should be located
    # this is so it can be created.
    assert scan_dir.scan_data_path == expected_path


def test_get_scan_data(db_engine):
    """Check that the scan data is returned, or None if doesn't exist."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine)

    fake_active_data = fake_active_scan_data()
    with open(scan_dir.scan_data_path, "w", encoding="utf-8") as json_file:
        json.dump(fake_active_data.model_dump(), json_file)

    # Should now be able to load this fake data from disk
    fake_historic_data = scan_dir.get_scan_data()
    assert_active_and_historic_data_equivalent(fake_active_data, fake_historic_data)

    # Check None is returned if the data cannot be read.
    with open(scan_dir.scan_data_path, "w", encoding="utf-8") as json_file:
        json_file.write("this is not json")
    assert scan_dir.get_scan_data() is None

    # Check None is returned if the data cannot or is json but cannot be serialised to
    # the data model
    with open(scan_dir.scan_data_path, "w", encoding="utf-8") as json_file:
        json_file.write(json.dumps({"foo": "bar"}))
    assert scan_dir.get_scan_data() is None


def test_empty_scan_info(db_engine):
    """Test the scan info is correct even if the scan is empty."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine)

    info = scan_dir.scan_info()
    now = time.time()

    assert scan_dir.name == "fake_scan_0001"
    # Created and modified in the last 5 seconds
    assert now - 5 < scan_dir.created_time < now
    assert now - 5 < scan_dir.get_modified_time() < now
    assert info.number_of_images == 0
    assert not info.stitch_available
    assert info.dzi is None


def test_zipping_scan_data(db_engine):
    """Test zipping the scan images with fake image data."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine)

    # Create 21 fake scan images, a fake stitch, and a fake zip
    for _i in range(21):
        _add_fake_image(scan_dir)
    _add_fake_file(scan_dir, "fake_scan_0001_stitched.jpg", in_im_dir=True)
    _add_fake_file(scan_dir, "fake_scan_0001_stitched.ome.tiff", in_im_dir=True)
    _add_fake_file(scan_dir, "zipfile.zip")

    # This fake dzi should have loads of images. The DZI should not be zipped!
    _make_fake_dzi(scan_dir)

    # zip the directory without setting as the final version. It should only
    # zip the 21 scan images
    zip_fname = scan_dir.zip_files()

    zip_files = get_files_in_zip(zip_fname)
    assert_unique_of_length(zip_files, 21)

    # Zip again with final version on and both legacy/new final formats are included.
    scan_dir.zip_files(final_version=True)

    zip_files = get_files_in_zip(zip_fname)
    assert_unique_of_length(get_files_in_zip(zip_fname), 23)
    # Check the zips are not in the zip
    for file in zip_files:
        assert not file.endswith(".zip")
        assert not file.endswith(".dzi")


def test_saving_scan_data_error(db_engine):
    """Test that saving scan data if there is no images directory raises FileNotFoundError."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine)

    # Remove the images directory.
    shutil.rmtree(scan_dir.images_dir)
    # Should raise FileNotFoundError.
    with pytest.raises(FileNotFoundError):
        scan_dir.save_scan_data(fake_active_scan_data())


def test_all_files(db_engine):
    """Test all_files returns the path, and respects skipped directories."""
    scan_dir = _make_dir_and_add_to_db("fake_scan", db_engine)
    # This fake dzi should have loads of images. The DZI should not be zipped!
    _make_fake_dzi(scan_dir)
    all_files = scan_dir.all_files()

    # As standard for 8 layers there are 89 jpegs and 1 dzi file.
    assert_unique_of_length(all_files, 90)
    dzi_file = None
    # Check all files exist
    for file in all_files:
        assert os.path.exists(os.path.join(scan_dir.dir_path, file))
        if file.endswith(".dzi"):
            # There is only 1 dzi file, so this should be None
            assert dzi_file is None
            dzi_file = file
    # Once loop is complete there should be a dzi file
    assert dzi_file is not None
    # Get the dzi tile directory name
    dzi_dir = os.path.basename(dzi_file[:-4] + "_files")

    # Get all files skipping the dzi tile directory
    all_files = scan_dir.all_files(skip_dirs=dzi_dir)
    # There is now only one file
    assert len(all_files) == 1
    # It is the DZI file
    assert all_files[0] == dzi_file


def test_creating_scan_dir_for_missing_scan():
    """Check creating ScanDirectory object for a dir that doesn't exist fails."""
    _clear_scan_dir()
    with pytest.raises(FileNotFoundError):
        ScanDirectory("not_real_0001", BASE_SCAN_DIR)


def test_none_returned_for_missing_images_dir():
    """None should be returned for image dir path if images dir does not exist.

    By default images directories are created at the same time the scan directory
    is created. However, edge cases such as problems in deletions, microscopes
    with older scans on, etc can cause and empty scan directory, so it is handled
    explicitly.
    """
    _clear_scan_dir()
    os.makedirs(os.path.join(BASE_SCAN_DIR, "fake_scan_0001"))
    scan_dir = ScanDirectory("fake_scan_0001", BASE_SCAN_DIR)
    assert scan_dir.images_dir is None
    # Also check that get scan files returns and empty list
    assert scan_dir.get_scan_files() == []


def test_extracting_files():
    """Test the private _find_files method of ScanDirectories.

    Add files to directory and check expected returns.
    """
    _clear_scan_dir()
    os.makedirs(os.path.join(BASE_SCAN_DIR, "fake_scan_0001", "images"))
    scan_dir = ScanDirectory("fake_scan_0001", BASE_SCAN_DIR)

    # Starting all lists should be empty
    scan_files = scan_dir.get_scan_files()
    assert scan_dir._extract_scan_images(scan_files) == []
    assert scan_dir._extract_final_stitches(scan_files) == []
    assert scan_dir._extract_dzi_files(scan_files) == []

    # Add a number of images
    for _i in range(2321):
        _add_fake_image(scan_dir)

    scan_files = scan_dir.get_scan_files()
    assert_unique_of_length(scan_dir._extract_scan_images(scan_files), 2321)
    assert_unique_of_length(scan_dir._extract_final_stitches(scan_files), 0)
    assert_unique_of_length(scan_dir._extract_dzi_files(scan_files), 0)

    # Add and a stitched image
    _add_fake_file(scan_dir, "fake_scan_0001_stitched.jpg", in_im_dir=True)
    _add_fake_file(scan_dir, "fake_scan_0001_stitched.ome.tiff", in_im_dir=True)

    scan_files = scan_dir.get_scan_files()
    assert_unique_of_length(scan_dir._extract_scan_images(scan_files), 2321)
    assert_unique_of_length(scan_dir._extract_final_stitches(scan_files), 2)
    assert_unique_of_length(scan_dir._extract_dzi_files(scan_files), 0)

    _make_fake_dzi(scan_dir)

    # check totals are still correct after adding a dzi with lots of tiles.
    scan_files = scan_dir.get_scan_files()
    scan_images = scan_dir._extract_scan_images(scan_files)
    stitches = scan_dir._extract_final_stitches(scan_files)
    dzi_files = scan_dir._extract_dzi_files(scan_files)
    assert_unique_of_length(scan_images, 2321)
    assert_unique_of_length(stitches, 2)
    assert_unique_of_length(dzi_files, 1)

    # And check the names are as expected
    assert stitches == [
        "fake_scan_0001_stitched.ome.tiff",
        "fake_scan_0001_stitched.jpg",
    ]
    assert dzi_files[0] == "fake_scan_0001.dzi"
