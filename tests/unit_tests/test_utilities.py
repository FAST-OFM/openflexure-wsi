"""Unit tests for utility functions."""

import logging
import sys
from collections import namedtuple

import pytest

from openflexure_microscope_server.utilities import (
    _WINDOWS_RESERVED_NAMES,
    NotEnoughFreeSpaceError,
    check_free_disk_space,
    is_path_safe,
    make_name_safe,
)

BAD_PATHS_WITH_DOTS = {"a./b/c.", "path./to/file.", "./path./to/file."}

GOOD_PATHS_WITH_DOTS = {"a.a/b/.c", "path.to/file.file.file", "./path.to/file.files"}

PATHS_WITH_SPACES = {"path /to /file ", "path/to /file", "path/a long path name /file"}

RESERVED_NAME_PATHS = {"path/CON/file", "path/NUL.txt/file", "C:/AUX/test"}

if sys.platform.startswith("win"):
    UNSAFE_CHARACTER_PATHS = {
        "C:/path/asterisk/data*.csv",
        "data/files/read_me_first!.txt",
        "logs/system<error>.log",
        "projects/alpha#1/notes.txt",
    }
else:
    UNSAFE_CHARACTER_PATHS = {
        "/var/log/app:backup.log",
        "/etc/configs/network-settings\v1",
        "/tmp/file_with_@_symbol.txt",
        "/home/user/script(1).py",
    }


def test_make_name_safe_basic():
    """Test basic functionality of make_name_safe."""
    assert make_name_safe("normal_name") == "normal_name"
    assert make_name_safe("name with spaces") == "name_with_spaces"
    assert make_name_safe("name/with/slashes") == "name_with_slashes"
    assert make_name_safe("name\\with\\backslashes") == "name_with_backslashes"
    assert make_name_safe("sPoNgEmoCk") == "spongemock"


def test_make_name_safe_trailing_chars():
    """Test that trailing dots and spaces are removed."""
    assert make_name_safe("name.") == "name"
    assert make_name_safe("name ") == "name"
    assert make_name_safe("name. ") == "name"
    assert make_name_safe("name .") == "name"
    assert make_name_safe(".") == "_"
    assert make_name_safe(" ") == "_"
    assert make_name_safe(". ") == "_"


@pytest.mark.parametrize("name", _WINDOWS_RESERVED_NAMES)
def test_make_name_safe_reserved_names(name):
    """Test Windows reserved names."""
    # Base name should be sanitized
    assert make_name_safe(name) == f"{name.lower()}_"
    # Case-insensitive
    assert make_name_safe(name.lower()) == f"{name.lower()}_"
    # With extension
    assert make_name_safe(f"{name}.txt") == f"{name.lower()}.txt_"
    # Multiple extensions
    assert make_name_safe(f"{name}.tar.gz") == f"{name.lower()}.tar.gz_"


def test_make_name_safe_reserved_names_false_positives():
    """Test that names containing but not equal to reserved names are safe."""
    assert make_name_safe("CONSTANT") == "constant"
    assert make_name_safe("CON2") == "con2"
    assert make_name_safe("ICON") == "icon"
    assert make_name_safe("icon") == "icon"
    assert make_name_safe("iCon") == "icon"
    assert make_name_safe("iCOn") == "icon"
    assert make_name_safe("icOn") == "icon"
    assert make_name_safe("CHILLI CON CARNE") == "chilli_con_carne"


def test_is_path_safe_basic(caplog):
    """Test basic functionality of is_path_safe."""
    # Note: behavior depends on platform for separators
    with caplog.at_level(logging.WARNING):
        if sys.platform.startswith("win"):
            assert is_path_safe("C:\\path\\to/file")
            assert is_path_safe("C:\\a very long path\\to/file")
        else:
            assert is_path_safe("path/to/file")
            assert is_path_safe("a very long path/to/file")

        # No errors should be raised. These paths are safe.
        assert len(caplog.records) == 0


@pytest.mark.parametrize("path", UNSAFE_CHARACTER_PATHS)
def test_is_path_safe_unsafe_characters_in_components(caplog, path):
    """Test unsafe characters within path components."""
    with caplog.at_level(logging.WARNING):
        assert not is_path_safe(path)
        assert (
            f"{path} contains characters that may be unsafe on this platform."
            in caplog.messages
        )


@pytest.mark.parametrize("path", RESERVED_NAME_PATHS)
def test_is_path_safe_reserved_in_components(caplog, path):
    """Test reserved names within path components."""
    with caplog.at_level(logging.WARNING):
        assert not is_path_safe(path)
        assert (
            f"{path} contains a reserved system name and may cause issues."
            in caplog.messages
        )


def test_is_path_safe_reserved_name_components(caplog):
    """Test reserved names are okay as part of a larger dir name."""
    with caplog.at_level(logging.WARNING):
        assert is_path_safe("chilli con carne/recipe")
        assert len(caplog.records) == 0


@pytest.mark.parametrize("path", PATHS_WITH_SPACES)
def test_is_path_safe_trailing_space_in_components(caplog, path):
    """Test trailing spaces in path components."""
    with caplog.at_level(logging.WARNING):
        # Test Windows - cannot have trailing spaces
        if sys.platform.startswith("win"):
            assert not is_path_safe(path)
            assert len(caplog.records) == 1
            assert (
                f"File path {path} may be unsafe due to trailing dots or whitespace."
                in caplog.messages
            )
        else:
            # Trailing spaces allows in POSIX systems
            assert is_path_safe(path)
            # No warnings should be raised
            assert len(caplog.records) == 0


def test_is_path_safe_relative(caplog):
    """Test that relative path components are preserved."""
    # Relative imports ./foo, ../foo or ../../foo are allowed.
    with caplog.at_level(logging.WARNING):
        assert is_path_safe("./openflexure/data/")
        assert is_path_safe("../openflexure/data/")
        assert is_path_safe("../../openflexure/data/")
        assert is_path_safe(".")
        assert is_path_safe("..")

        assert len(caplog.records) == 0

        assert not is_path_safe("a_bad/../relative_path")
        assert len(caplog.records) == 1
        assert (
            "File path a_bad/../relative_path may be unsafe due to unexpected relative navigation."
            in caplog.messages
        )


@pytest.mark.parametrize("filepath", BAD_PATHS_WITH_DOTS)
def test_is_path_safe_with_trailing_dots_warning(caplog, filepath):
    """Test that a user is warned about trailing dots in their file paths. The paths should remain unchanged."""
    with caplog.at_level(logging.WARNING):
        assert not is_path_safe(filepath)
        assert (
            f"File path {filepath} may be unsafe due to trailing dots or whitespace."
            in caplog.messages
        )


@pytest.mark.parametrize("filepath", GOOD_PATHS_WITH_DOTS)
def test_is_path_safe_allowed_dots_warning(caplog, filepath):
    """Test that a user is warned about trailing dots in their file paths. The paths should remain unchanged."""
    with caplog.at_level(logging.WARNING):
        assert is_path_safe(filepath)
        assert len(caplog.records) == 0


DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


@pytest.mark.parametrize("free_space", [500_000_001, 700_000_000, 5_000_000_000])
def test_disk_not_full(mocker, free_space):
    """Check no error thrown if disk has over 500MB of space."""
    total_space = 16_000_000_000
    # Mock the disk_usage
    mocker.patch(
        "shutil.disk_usage",
        return_value=DiskUsage(
            total=total_space, used=total_space - free_space, free=free_space
        ),
    )

    check_free_disk_space("fake_file")


@pytest.mark.parametrize("free_space", [100_000_000, 499_999_999])
def test_disk_full(mocker, free_space):
    """Check error thrown if disk has under 500MB of space."""
    total_space = 16_000_000_000
    # Mock the disk_usage
    mocker.patch(
        "shutil.disk_usage",
        return_value=DiskUsage(
            total=total_space, used=total_space - free_space, free=free_space
        ),
    )

    with pytest.raises(NotEnoughFreeSpaceError):
        check_free_disk_space("fake_file")
