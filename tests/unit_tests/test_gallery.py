"""Tests that captures have the expected metadata."""

import logging
from dataclasses import dataclass
from typing import Callable, Optional
from unittest import mock

import pytest

from labthings_fastapi.exceptions import InvocationCancelledError
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.gallery_db import OFMGalleryDBEngine
from openflexure_microscope_server.things import OFMThing
from openflexure_microscope_server.things.gallery import (
    GalleryCompatibleThing,
    GalleryThing,
)
from openflexure_microscope_server.ui import ActionButton

from ..shared_utils.lt_test_utils import LabThingsTestEnv


def test_error_if_accessing_properties_before_started():
    """Check that unfefined properties errors before enter."""
    thing = create_thing_without_server(GalleryThing)
    with pytest.raises(
        RuntimeError,
        match="Cannot access gallery_providing_things before server has started.",
    ):
        thing.gallery_providing_things
    with pytest.raises(
        RuntimeError,
        match="Cannot access card_types before server has started.",
    ):
        thing.card_types


class MinimalGalleryClass:
    """A class that provides data to the gallery but is NOT a thing.

    This should fail the protocol check as it is not an OFMThing
    """

    _mock_engine = None

    @property
    def gallery_db_engine(self) -> OFMGalleryDBEngine:
        """A mock database engine."""
        if self._mock_engine is None:
            self._mock_engine = mock.Mock(spec=OFMGalleryDBEngine)
            self._mock_engine.card_type = "Mock"
        return self._mock_engine

    def delete_all_gallery_items(self) -> None:
        """Mock deleting all the data."""

    def get_gallery_bulk_actions(self) -> list[ActionButton]:
        """No bulk actions."""
        return []


class MinimalGallerySource(OFMThing, MinimalGalleryClass):
    """Minimal example of a Thing that can show data in the gallery.

    Mixes MinimalGalleryClass into OFMThing to be recognised by the protocol.
    """

    show_data_in_gallery = True


class MinimalGallerySource2(MinimalGallerySource):
    """Another Thing that can show data in the gallery with a different card type."""

    @property
    def gallery_db_engine(self) -> OFMGalleryDBEngine:
        """A mock database engine."""
        if self._mock_engine is None:
            self._mock_engine = mock.Mock(spec=OFMGalleryDBEngine)
            self._mock_engine.card_type = "Mock2"
        return self._mock_engine


class BadGallerySource(OFMThing):
    """An OFMThing that almost defines enough to show in the gallery.

    Shouldn't match the protocol as ``gallery_db_engine`` is missing.
    """

    show_data_in_gallery = True

    def delete_all_gallery_items(self) -> None:
        """Mock deleting all the data."""

    def get_gallery_bulk_actions(self) -> list[ActionButton]:
        """No bulk actions."""
        return []


class BadGallerySource2(OFMThing):
    """An OFMThing that defines enough to show in the gallery with bad card data.

    Matches the protocol but will be rejected when setting card types.
    """

    show_data_in_gallery = True

    @property
    def gallery_db_engine(self) -> OFMGalleryDBEngine:
        """A mock database engine."""
        return mock.Mock(spec=OFMGalleryDBEngine)

    def delete_all_gallery_items(self) -> None:
        """Mock deleting all the data."""

    def get_gallery_bulk_actions(self) -> list[ActionButton]:
        """No bulk actions."""
        return []


def test_gallery_compatible_protocol():
    """Check the gallery compatible protocol detects compatible classes only."""
    # Minimal class has the gallery specific methods but is not an OFMThing.
    assert not isinstance(MinimalGalleryClass(), GalleryCompatibleThing)

    # BadGallerySource is an OFM Thing but is missing one of the required properties.
    assert not isinstance(
        create_thing_without_server(BadGallerySource), GalleryCompatibleThing
    )

    # MinimalGallerySource should match!
    assert isinstance(
        create_thing_without_server(MinimalGallerySource), GalleryCompatibleThing
    )


def test_gallery_thing_finds_all_providers(simulation_test_env):
    """Check the gallery identifies the correct Things that will provide data."""
    # Started the full simulation in a test environment.
    # Get the gallery and 3 other Things from the environment.
    gallery = simulation_test_env.get_thing_by_name("gallery")
    snake_workflow = simulation_test_env.get_thing_by_name("snake_workflow")
    smart_scan = simulation_test_env.get_thing_by_name("smart_scan")
    camera = simulation_test_env.get_thing_by_name("camera")

    # Check that all_ofm_things are correct. Camera and smart scan are ofm things
    # snake workflow is not.
    assert snake_workflow not in gallery.all_ofm_things.values()
    assert camera in gallery.all_ofm_things.values()
    assert smart_scan in gallery.all_ofm_things.values()

    # Also check that both the camera and smart scan are recognised as gallery
    # providers based on the protocol.
    assert snake_workflow not in gallery.gallery_providing_things.values()
    assert camera in gallery.gallery_providing_things.values()
    assert smart_scan in gallery.gallery_providing_things.values()

    # Also check the card types:
    assert gallery.card_types == ["Capture", "Scan"]
    assert gallery._card_type_map == {"camera": "Capture", "smart_scan": "Scan"}

    # Also check all bulk actions
    bulk_actions = gallery.bulk_actions
    # Only actions should be stitch all scans
    assert len(bulk_actions) == 1
    assert bulk_actions[0].thing == "smart_scan"
    assert bulk_actions[0].action == "stitch_all_scans"
    assert bulk_actions[0].can_terminate
    assert bulk_actions[0].requires_confirmation
    assert "Stitch all unstitched scans?" in bulk_actions[0].confirmation_message

    # Also check the runtime checkable protocol GalleryCompatibleThing works directly
    # when called with isinstance.
    assert not isinstance(snake_workflow, GalleryCompatibleThing)
    assert isinstance(smart_scan, GalleryCompatibleThing)


@pytest.mark.parametrize("bad_source_class", [BadGallerySource, BadGallerySource2])
def test_gallery_logs_for_bad_providers(bad_source_class, caplog):
    """Test that an error is logged if a gallery source doesn't match the protocol."""
    # Create a config with the minimal Thing and the bad Thing
    things = {
        "minimal_thing": MinimalGallerySource,
        "bad_thing": bad_source_class,
        "gallery": GalleryThing,
    }
    with caplog.at_level(logging.INFO):
        # Start the server
        with LabThingsTestEnv(
            things=things, application_config={"data_folder": "./openflexure/data/"}
        ) as env:
            gallery = env.get_thing_by_name("gallery")
            # Minimal thing is listed as a gallery provider
            assert "minimal_thing" in gallery.gallery_providing_things
            # Bad thing isn't as the protocol doesn't match
            assert "bad_thing" not in gallery.gallery_providing_things

        # There is an error logged about bad_thing
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "Data from bad_thing cannot be shown in gallery" in record.message
        assert record.levelname == "ERROR"


@pytest.fixture
def minimal_gallery_env():
    """Return a minimal working environment for testing the gallery."""
    things = {
        "minimal_thing1": MinimalGallerySource,
        "minimal_thing2": MinimalGallerySource2,
        "gallery": GalleryThing,
    }
    with LabThingsTestEnv(
        things=things, application_config={"data_folder": "./openflexure/data/"}
    ) as env:
        return env


def test_gallery_lists_data(minimal_gallery_env, caplog):
    """Test that the gallery calls all gallery things to list data."""
    # Create a config with the two minimal things and get the things by name.
    gallery = minimal_gallery_env.get_thing_by_name("gallery")
    thing_1 = minimal_gallery_env.get_thing_by_name("minimal_thing1")
    thing_2 = minimal_gallery_env.get_thing_by_name("minimal_thing2")

    # Also mock the get_data_for_gallery methods.
    thing_1.gallery_db_engine.get_all_entries_as_base_model.return_value = ["Mock"]
    thing_2.gallery_db_engine.get_all_entries_as_base_model.return_value = ["Sock"]

    # Check that the gallery returns the expected concatenated data
    assert gallery.list_data == ["Mock", "Sock"]

    # Check that if the first thing called throws an error the data from the second
    # thing is still returned.
    thing_1.gallery_db_engine.get_all_entries_as_base_model.side_effect = RuntimeError
    with caplog.at_level(logging.INFO):
        assert gallery.list_data == ["Sock"]
    # Check an error was logged about the first thing failing to collect data.
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "ERROR"


@dataclass
class GalleryDeleteTestCase:
    """Inputs and expected outputs for testing ``save_from_memory``.

    The default save kwargs assume a jpeg.
    """

    cards_to_delete: list[str]
    thing_1_side_effect: Optional[Callable]
    thing_1_call_count: int
    thing_2_call_count: int
    # Expect directly calling the action will raise the thing 1 side_effect error
    action_errors: bool


GALLERY_DELETE_TEST_CASES = [
    # Delete both types of card.
    GalleryDeleteTestCase(
        cards_to_delete=["Mock", "Mock2"],
        thing_1_side_effect=None,
        thing_1_call_count=1,
        thing_2_call_count=1,
        action_errors=False,
    ),
    # Delete first type of card.
    GalleryDeleteTestCase(
        cards_to_delete=["Mock"],
        thing_1_side_effect=None,
        thing_1_call_count=1,
        thing_2_call_count=0,
        action_errors=False,
    ),
    # Delete second type of card.
    GalleryDeleteTestCase(
        cards_to_delete=["Mock2"],
        thing_1_side_effect=None,
        thing_1_call_count=0,
        thing_2_call_count=1,
        action_errors=False,
    ),
    # Thing 1 errors, thing 2 is still called
    GalleryDeleteTestCase(
        cards_to_delete=["Mock", "Mock2"],
        thing_1_side_effect=RuntimeError,
        thing_1_call_count=1,
        thing_2_call_count=1,
        action_errors=False,
    ),
    # Thing 1 raises invocation cancelled error. Thing 2 is not called.
    # The InvocationError is raised
    GalleryDeleteTestCase(
        cards_to_delete=["Mock", "Mock2"],
        thing_1_side_effect=InvocationCancelledError,
        thing_1_call_count=1,
        thing_2_call_count=0,
        action_errors=True,
    ),
]


@pytest.mark.parametrize("test_case", GALLERY_DELETE_TEST_CASES)
def test_gallery_calls_delete(test_case, minimal_gallery_env, mocker):
    """Test that the gallery deletes on all gallery providing things."""
    # Create a config with the minimal Thing and the bad Thing
    gallery = minimal_gallery_env.get_thing_by_name("gallery")
    thing_1 = minimal_gallery_env.get_thing_by_name("minimal_thing1")
    thing_2 = minimal_gallery_env.get_thing_by_name("minimal_thing2")

    # Set up the desired side effect for the test case.
    mocker.patch.object(
        thing_1, "delete_all_gallery_items", side_effect=test_case.thing_1_side_effect
    )
    mocker.patch.object(thing_2, "delete_all_gallery_items")

    # Call delete_all_data, checking for errors if the test case expects an error.
    if test_case.action_errors:
        with pytest.raises(test_case.thing_1_side_effect):
            gallery.delete_all_data(test_case.cards_to_delete)
    else:
        gallery.delete_all_data(test_case.cards_to_delete)

    # Check the call counts are as expected from the test case.
    assert thing_1.delete_all_gallery_items.call_count == test_case.thing_1_call_count
    assert thing_2.delete_all_gallery_items.call_count == test_case.thing_2_call_count
