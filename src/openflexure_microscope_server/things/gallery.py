"""Server-side gallery functionality.

The types of data that are captured and how they display in the gallery are defined by
the Things that capture the Data. This thing just provides a unified way for the gallery
front end to access the data.
"""

from typing import Mapping, Optional, Protocol, Self, cast, runtime_checkable

import labthings_fastapi as lt

from openflexure_microscope_server.gallery_db import (
    GalleryEntryModel,
    OFMGalleryDBEngine,
)
from openflexure_microscope_server.things import OFMThing
from openflexure_microscope_server.ui import ActionButton


@runtime_checkable
class GalleryCompatibleThing(Protocol):
    """Protocol for checking Things using the gallery are complete.

    Note that runtime checkable protocols only check that the methods exist, not the
    full signatures. This means that anything attempting to define the methods will
    be picked up, but may throw an error when used.
    """

    name: str

    # Ensure it is a Thing:
    _thing_server_interface: lt.ThingServerInterface

    # Ensure it is an OFMThing:
    show_data_in_gallery: bool

    # Ignore D102: No docstrings for the protocol.
    @property
    def gallery_db_engine(self) -> OFMGalleryDBEngine: ...  # noqa: D102

    def delete_all_gallery_items(self) -> None: ...  # noqa: D102

    def get_gallery_bulk_actions(self) -> list[ActionButton]: ...  # noqa: D102


class GalleryThing(lt.Thing):
    """A Thing for communicating with the front end gallery."""

    all_ofm_things: Mapping[str, OFMThing] = lt.thing_slot()

    _gallery_providing_things: Optional[dict[str, GalleryCompatibleThing]] = None
    _card_types: Optional[list[str]] = None
    _card_type_map: Optional[dict[str, str]] = None

    # Cannot initialise the gallery on enter, as all gallery providers must be entered.
    # So it is initialised on first call if entered.
    _entered = False

    @property
    def gallery_providing_things(self) -> dict[str, GalleryCompatibleThing]:
        """All Things that can provide data to the gallery."""
        if self._gallery_providing_things is None:
            if self._entered:
                self._set_gallery_providers()
            else:
                raise RuntimeError(
                    "Cannot access gallery_providing_things before server has started."
                )
        # Gallery providing things is now set but MyPy doesn't know so cast:
        return cast(dict[str, GalleryCompatibleThing], self._gallery_providing_things)

    def __enter__(self) -> Self:
        """Mark this Thing as entered.

        Sets ``self._entered`` to True. This is used to determine if
        ``self._set_gallery_providers`` can be called. As the gallery providers
        cannot be set until all OFMThings are entered.
        """
        self._entered = True

        return self

    def _set_gallery_providers(self) -> None:
        """Set the mapping of gallery providing things.

        This is called on ``__enter__`` when the server starts.
        """
        gallery_providers = {
            name: thing
            for name, thing in self.all_ofm_things.items()
            if thing.show_data_in_gallery
        }
        # cache initial list of keys as it may change in the loop
        keys = list(gallery_providers.keys())
        for key in keys:
            if not isinstance(gallery_providers[key], GalleryCompatibleThing):
                self.logger.error(
                    f"Data from {key} cannot be shown in gallery as it does not "
                    "provide all necessary properties/methods for the gallery."
                )
                gallery_providers.pop(key)

        # Cast the type of each thing to "GalleryCompatibleThing" as other Things have
        # been popped.
        self._gallery_providing_things = cast(
            dict[str, GalleryCompatibleThing], gallery_providers
        )
        self._set_card_types(self._gallery_providing_things)

    def _set_card_types(
        self, gallery_providers: dict[str, GalleryCompatibleThing]
    ) -> None:
        """Find the card types from each provider.

        This is not called on __enter__ as it can only be called once all OFMThings
        have also been entered. Instead, it is called on the first attempt to read
        a gallery provider.

        :param gallery_providers: The dictionary of gallery providing things. If card
            data cannot be extracted for a Thing it will be popped from the provider
            dictionary.
        """
        card_types: list[str] = []
        card_type_map: dict[str, str] = {}
        # cache initial list of keys as it may change in the loop
        keys = list(gallery_providers.keys())
        for key in keys:
            db_engine = gallery_providers[key].gallery_db_engine
            if not hasattr(db_engine, "card_type"):
                self.logger.error(
                    f"Data from {key} cannot be shown in gallery as database engine "
                    " doesn'thave a static card_type."
                )
                gallery_providers.pop(key)
                continue
            card_type = db_engine.card_type
            if card_type not in card_types:
                card_types.append(card_type)
            card_type_map[key] = card_type

        self._card_type_map = card_type_map
        self._card_types = card_types

    @property
    def card_type_map(self) -> dict[str, str]:
        """A mapping from thing name to card type."""
        # Card type map is not set on __enter__ as it requires the db_engine to be
        # initialised on each Thing. Instead it is cached on first call.
        if self._card_type_map is None:
            if not self._entered:
                raise RuntimeError(
                    "Cannot access card_type_map before server has started."
                )
            # _set_card_types sets both self._card_types and self._card_type_map
            self._set_card_types(self.gallery_providing_things)
        # self._card_type_map is now set so cast rather than re-check
        return cast(dict[str, str], self._card_type_map)

    @lt.property
    def card_types(self) -> list[str]:
        """Names for the card types in the gallery."""
        # Card types is not set on __enter__ as it requires the db_engine to be
        # initialised on each Thing. Instead it is cached on first call.
        if self._card_types is None:
            if not self._entered:
                raise RuntimeError(
                    "Cannot access card_types before server has started."
                )
            # _set_card_types sets both self._card_types and self._card_type_map
            self._set_card_types(self.gallery_providing_things)
        # self._card_types is now set so cast rather than re-check
        return cast(list[str], self._card_types)

    # Cache result after first call.
    _bulk_actions: Optional[list[ActionButton]] = None

    @lt.property
    def bulk_actions(self) -> list[ActionButton]:
        """All bulk actions."""
        if self._bulk_actions is None:
            actions: list[ActionButton] = []
            for thing in self.gallery_providing_things.values():
                actions += thing.get_gallery_bulk_actions()
            self._bulk_actions = actions
        return self._bulk_actions

    @lt.property
    def list_data(self) -> list[GalleryEntryModel]:
        """List the data from all registered things.

        Currently only works with `smart_scan` as the UI cards are not customisable.

        This will change to an action (or another type of endpoint) at a
        later date to enable filtering, and returning only a specific page.
        """
        data_list = []
        for thing in self.gallery_providing_things.values():
            db_engine = thing.gallery_db_engine
            try:
                data_list += db_engine.get_all_entries_as_base_model()
            except Exception as e:
                # If any of the providers errors producing a list, log the exception
                # and continue.
                self.logger.exception(e)
        return data_list

    @lt.action
    def delete_all_data(self, card_types: list[str]) -> None:
        """Delete all the gallery data on this microscope with the given card types."""
        for thing in self.gallery_providing_things.values():
            thing_card_type = self.card_type_map.get(thing.name)
            if thing_card_type in card_types:
                try:
                    thing.delete_all_gallery_items()
                except Exception as e:
                    self.logger.exception(e)
