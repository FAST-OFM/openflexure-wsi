"""A package for all of the core LabThings-FastAPI Things shipped with the microscope.

The microscope can be extended to be used with other hardware by creating a package
with other Things and including them in the LabThings-FastAPI config file.
"""

import os
from pathlib import PurePath
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Optional, Self

from pydantic import PrivateAttr, RootModel, model_validator

import labthings_fastapi as lt


class OFMThing(lt.Thing):
    """A custom LabThings Thing class for the OpenFlexure Microscope."""

    _data_dir: Optional[str] = None

    _show_data_in_gallery: bool = False

    @property
    def show_data_in_gallery(self) -> bool:
        """Whether to show in the Gallery."""
        return self._show_data_in_gallery

    def __enter__(self) -> Self:
        """Set the data directory when the Thing is entered."""
        # Note that the `application_config` was already validated when the
        # server initialised.
        application_config = self._thing_server_interface.application_config
        if application_config is None:
            raise ValueError("No application configuration was supplied.")
        app_data_dir = application_config["data_folder"]
        self._data_dir = os.path.join(
            os.path.normpath(str(app_data_dir)), os.path.normpath(self.name)
        )
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException],
        _exc_value: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> None:
        """Close the OFMThing.

        This is needed for the context manager protocol to work. Currently it doesn't
        do anything.
        """
        pass

    @property
    def data_dir(self) -> str:
        """The data directory for this thing."""
        if self._data_dir is None:
            raise RuntimeError(
                "No data directory set. Has the LabThings server been started?"
            )
        return self._data_dir

    def create_data_path(self, path: str, absolute: bool = False) -> "RelativeDataPath":
        """Create a ``RelativeDataPath`` object with this Thing set as the saving Thing.

        :param path: The relative path within the data directory of this Thing's data
            dir that the data should be saved to.
        :param absolute: Set to True if the current path is absolute. A relative path
            will be returned. A validation error will be raised if the absolute path
            is not within the data directory.

        :return: A ``RelativeDataPath`` object with the saving Thing already set.
        """
        if absolute:
            path = os.path.relpath(path, self.data_dir)
        rel_data_path = RelativeDataPath(path)
        rel_data_path.set_saving_thing(self)
        return rel_data_path


class RelativeDataPath(RootModel[str]):
    """A relative path that is validated, and can have a Thing assigned to it.

    Use ``set_saving_thing`` or ``set_saving_thing_if_unset`` to set the Thing whose
    data directory will be used for the final save.

    Use the ``abs_data_path`` property to get the final path for saving.
    """

    _saving_thing: Optional[OFMThing | TemporaryDirectory] = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        """Validate the relative path is relative and has no parent dir references."""
        p = PurePath(value)

        if p.is_absolute():
            raise ValueError("Absolute paths are not allowed")

        if ".." in p.parts:
            raise ValueError("Parent directory references are not allowed")

        return os.path.normpath(value)

    @property
    def save_location_set(self) -> bool:
        """Return True if the saving thing is set."""
        return self._saving_thing is not None

    def set_saving_thing(self, thing: OFMThing | TemporaryDirectory) -> None:
        """Set the Thing that is saving the data.

        The thing can also be a ``TemporaryDirectory`` object.

        This will set the data directory.
        """
        if self.save_location_set:
            raise RuntimeError("The saving Thing for the relative path is already set")
        self._saving_thing = thing

    def set_saving_thing_if_unset(self, thing: OFMThing) -> None:
        """Set the Thing that is saving the data if it is not already set.

        Use this in an action to set the Thing for paths set via the API.
        """
        if not self.save_location_set:
            self._saving_thing = thing

    def save_to_tempdir(self) -> TemporaryDirectory:
        """Use a temporary directory to save raher than an ``OFMThing``.

        :returns: the ``TemporaryDirectory`` object.
        """
        if self.save_location_set:
            raise RuntimeError("The saving Thing for the relative path is already set")
        self._saving_thing = TemporaryDirectory()
        return self._saving_thing

    def join(self, sub_path: str) -> "RelativeDataPath":
        """Join a path to the end of this path.

        :return: A new ``RelativeDataPath`` object with the path appended.
        """
        new_path = RelativeDataPath(os.path.join(self.root, sub_path))
        if self._saving_thing is not None:
            new_path.set_saving_thing(self._saving_thing)
        return new_path

    @property
    def abs_data_path(self) -> str:
        """The absolute data directory to save to."""
        if self._saving_thing is None:
            raise RuntimeError("The saving Thing for the relative path was never set")
        if isinstance(self._saving_thing, TemporaryDirectory):
            return os.path.join(self._saving_thing.name, self.root)
        return os.path.join(self._saving_thing.data_dir, self.root)

    @property
    def posixpath(self) -> str:
        """The relative posix path."""
        # Use PurePath's as_posix function as it is reliable across OSes
        return PurePath(self.root).as_posix()
