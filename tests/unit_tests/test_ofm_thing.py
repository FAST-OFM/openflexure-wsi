"""Tests for the for thing defined in the base __init__ of the things dir."""

import os
from tempfile import TemporaryDirectory

import pytest
from pydantic import ValidationError

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things import OFMThing, RelativeDataPath


@pytest.mark.parametrize(
    ("path", "valid", "os_skip"),
    [
        ("foo.png", True, []),
        ("foo/bar/", True, []),
        ("./foo/bar", True, []),
        ("/foo/bar", False, ["nt"]),
        ("C:\\foo/bar", False, ["posix"]),
        ("../foo/bar", False, []),
        ("foo/../bar", False, []),
    ],
)
def test_rel_data_path_validation(path, valid, os_skip):
    """Check validation for a number of cases."""
    if os.name in os_skip:
        # Skip this case for this OS
        return
    if valid:
        path_obj = RelativeDataPath(path)
        assert path_obj.root == os.path.normpath(path)
    else:
        with pytest.raises(ValidationError):
            RelativeDataPath(path)


def test_setting_saving_things():
    """Check that the saving thing can be set."""
    thing1 = create_thing_without_server(OFMThing)
    thing1._data_dir = os.path.join("data", "thing1")
    thing2 = create_thing_without_server(OFMThing)
    thing2._data_dir = os.path.join("data", "thing2")
    path = RelativeDataPath("foo.png")

    assert not path.save_location_set

    with pytest.raises(
        RuntimeError, match="The saving Thing for the relative path was never set"
    ):
        path.abs_data_path

    # Set the saving thing and check the response is as expected
    path.set_saving_thing(thing1)
    assert path.save_location_set
    assert path.abs_data_path == os.path.join("data", "thing1", "foo.png")
    assert path._saving_thing is thing1

    # Check there is an error is trying to overwrite it with a new thing...
    with pytest.raises(
        RuntimeError, match="The saving Thing for the relative path is already set"
    ):
        path.set_saving_thing(thing2)
    # or a temporary dir
    with pytest.raises(
        RuntimeError, match="The saving Thing for the relative path is already set"
    ):
        path.save_to_tempdir()

    # Check no error when using set_saving_thing_if_unset
    path.set_saving_thing_if_unset(thing2)
    assert path.save_location_set
    assert path.abs_data_path == os.path.join("data", "thing1", "foo.png")
    assert path._saving_thing is thing1

    # Check set_saving_thing_if_unset works on a new path.
    path2 = RelativeDataPath("foo2.png")
    assert not path2.save_location_set
    path2.set_saving_thing_if_unset(thing2)
    assert path2.save_location_set
    assert path2.abs_data_path == os.path.join("data", "thing2", "foo2.png")
    assert path2._saving_thing is thing2


def test_saving_thing_propagates_on_join():
    """Check when joining to a path the saving thing propagates to the new path."""
    thing = create_thing_without_server(OFMThing)
    thing._data_dir = os.path.join("data", "thing")
    path = RelativeDataPath("foo")

    # No thing set
    assert not path.save_location_set

    # After joining no thing set
    path2 = path.join("bar")
    assert not path2.save_location_set

    # Set thing and check joined path is as expected
    path2.set_saving_thing(thing)
    assert path2.save_location_set
    assert path2.abs_data_path == os.path.join("data", "thing", "foo", "bar")

    # Do another join. Set thing remains
    path3 = path2.join("file.png")
    assert path3.save_location_set
    assert path3.abs_data_path == os.path.join(
        "data", "thing", "foo", "bar", "file.png"
    )


def test_temp_dir_for_saving_thing():
    """Check that the saving thing can actually be a temporary directory."""
    path = RelativeDataPath("foo.png")
    tmpdir = path.save_to_tempdir()
    assert isinstance(tmpdir, TemporaryDirectory)
    assert path.save_location_set
    assert path.abs_data_path == os.path.join(tmpdir.name, "foo.png")


def test_create_data_path_from_thing():
    """Check the create_data_path method of ``OFMThing``.

    It should create a RelativeDataPath with itself set as the saving thing.
    """
    thing = create_thing_without_server(OFMThing)
    thing._data_dir = os.path.join("data", "thing")
    path = thing.create_data_path("file.png")

    assert path.save_location_set
    assert path.abs_data_path == os.path.join("data", "thing", "file.png")
    assert path._saving_thing is thing
