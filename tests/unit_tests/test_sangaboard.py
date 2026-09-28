"""Tests for the Sangaboard thing."""

import logging

import pytest

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things.stage.sangaboard import (
    RECOMMENDED_VERSION,
    REQUIRED_VERSION,
    WIKI_URL,
    SangaboardThing,
)


@pytest.fixture
def mock_sanga_thing(mocker):
    """Return a Sangaboard thing with a MagicMock for self._sangaboard."""
    sanga_thing = create_thing_without_server(SangaboardThing)
    sanga_thing._sangaboard = mocker.MagicMock()
    return sanga_thing


def test_check_old_firmware(mock_sanga_thing):
    """Check firmware prior to version 1 throws a Runtime Error."""
    mock_sanga_thing._sangaboard.firmware_version = "0.1.1"
    with pytest.raises(RuntimeError):
        mock_sanga_thing.check_firmware()


def test_firmware_before_recommended(mock_sanga_thing, caplog):
    """Check required version warns if it is lower than the recommended version."""
    if REQUIRED_VERSION == RECOMMENDED_VERSION:
        # If required is currently the same as recommended then this test isn't valid
        return
    mock_sanga_thing._sangaboard.firmware_version = str(REQUIRED_VERSION)
    with caplog.at_level(logging.WARNING):
        mock_sanga_thing.check_firmware()
    assert len(caplog.records) == 1
    msg = (
        f"Sangaboard firmware version {REQUIRED_VERSION} is below the recommended "
        f"{RECOMMENDED_VERSION}. Visit {WIKI_URL} for instructions on updating."
    )
    assert caplog.records[0].message == msg


def test_dev_is_before_recommended(mock_sanga_thing, caplog):
    """Check that a dev version of the recommended version warns."""
    if REQUIRED_VERSION == RECOMMENDED_VERSION:
        # If required is currently the same as recommended then this test isn't valid
        return
    dev_version = RECOMMENDED_VERSION.replace(prerelease="dev1")
    mock_sanga_thing._sangaboard.firmware_version = str(dev_version)
    with caplog.at_level(logging.WARNING):
        mock_sanga_thing.check_firmware()
    assert len(caplog.records) == 1
    msg = (
        f"Sangaboard firmware version {dev_version} is below the recommended "
        f"{RECOMMENDED_VERSION}. Visit {WIKI_URL} for instructions on updating."
    )
    assert caplog.records[0].message == msg


def test_valid_firmware(mock_sanga_thing, caplog):
    """Check valid firmware doesn't error."""
    versions = [
        RECOMMENDED_VERSION,  # Recommended
        RECOMMENDED_VERSION.bump_major(),  # Higher versions
        RECOMMENDED_VERSION.bump_minor(),
        RECOMMENDED_VERSION.bump_patch(),
        RECOMMENDED_VERSION.bump_patch().replace(prerelease="dev1"),
    ]
    # Note that dev1 will not warn in check version, but will warn in the underlying
    # Sangaboard library as it is a pre-release.

    for version in versions:
        mock_sanga_thing._sangaboard.firmware_version = str(version)
        with caplog.at_level(logging.WARNING):
            mock_sanga_thing.check_firmware()
        assert len(caplog.records) == 0
