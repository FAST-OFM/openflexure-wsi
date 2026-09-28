"""Defines the database used by Things providing data to the gallery."""

import logging
import os
import posixpath
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import PurePath
from typing import Any, Iterator, Optional

import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from sqlalchemy import orm

import labthings_fastapi as lt

from openflexure_microscope_server.things import RelativeDataPath

LOGGER = logging.getLogger(__name__)

GALLERY_DB_SCHEMA_VERSION = 1
GALLERY_DB_NAME = "gallery.db"


class OFMGalleryBase(orm.MappedAsDataclass, orm.DeclarativeBase):
    """Create a base class for all ofm gallery databases.

    This is required by sqlalchemy, which will not allow tables to directly subclass
    DeclarativeBase. Each database subclassed from this will be created
    for each `OFMGalleryDBEngine`.
    """


class GalleryEntry(OFMGalleryBase):
    """A class to define gallery entries and their table.

    Do not update this without updating the schema version and creating way to migrate.
    """

    __tablename__ = "gallery_entries"

    path: orm.Mapped[str] = orm.mapped_column(primary_key=True)
    """The path on disk relative to the base data directory.

    This must always be the normalised posix path relative to the data directory.
    """

    hash: orm.Mapped[str] = orm.mapped_column()
    """A hash for checking if the file has updated.

    Each gallery providing Thing can choose its own hashing method.
    """

    created: orm.Mapped[float] = orm.mapped_column()
    """The created time.

    This is separated from gallery_info so it can be used for sorting and filtering."""

    modified: orm.Mapped[float] = orm.mapped_column()
    """The modifiedtime.

    This is separated from gallery_info so it can be used for sorting and filtering."""

    gallery_info: orm.Mapped[dict] = orm.mapped_column(sa.JSON)
    """Card type specific information that is shared with the gallery."""

    thumbnail_source: orm.Mapped[str | None] = orm.mapped_column(default=None)
    """The path on disk (relative to the base data dir) or None for no thumbnail."""

    gallery_added_data: orm.Mapped[dict] = orm.mapped_column(
        sa.JSON, default_factory=dict
    )
    """Extra data added by the gallery UI.

    This should not be changed server side. Its value should never affect the hash,
    as the hash is used to determine if the data on disk has changed since caching
    to the database. This data is not stored on disk except in the database.
    """


# Developer note: This model largely duplicates the structure of the GalleryEntry.
# It is used to provide a document API. It also explicitly includes the name of the
# Thing which is needed by the UI but should not be stored in the database as it would
# cause issues if the thing name is updated and the data is migrated.
# It contains extra information such as the url for deleting the file.
class GalleryEntryModel(BaseModel):
    """The data required for each entry in the gallery."""

    name: str
    """The display name for the item in the gallery."""

    path: str
    """The mounted path for this entry with the data directory."""

    thing: str
    """The name of the thing that created this object."""

    delete_endpoint: str
    """The endpoint for deleting this item with a DELETE request.

    This is relative to the base URI
    """

    created: float
    """The created time."""

    modified: float
    """The modified time."""

    card_type: str
    """Name of the card type for the UI so it can interpret the metadata."""

    gallery_info: dict[str, Any]
    """Card type specific information that is shared with the gallery."""

    thumbnail_source: Optional[str]
    """The mounted path for the thumbnail."""

    gallery_added_data: dict[str, Any]
    """Extra data added by the gallery UI."""


def _new_gallery_db_engine(db_file: str) -> sa.Engine:
    """Create a new database engine with the correct schema for the Gallery.

    Note that no migration yet exists as this is on the first schema.
    """
    engine = sa.create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        # Read SQLite "user_version" as this is our schema versioning.
        version = conn.exec_driver_sql("PRAGMA user_version").scalar_one()

        # Version is zero if never used. Create the database tables
        if version == 0:
            OFMGalleryBase.metadata.create_all(conn)
            conn.exec_driver_sql(f"PRAGMA user_version = {GALLERY_DB_SCHEMA_VERSION}")
            return engine

        if version == GALLERY_DB_SCHEMA_VERSION:
            return engine

        # Unknown gallery schema version.
        raise RuntimeError(
            f"Gallery schema {version} for database {engine.url} is invalid."
        )


class OFMGalleryDBEngine(ABC):
    """A database engine that can be subclassed for different Gallery providers."""

    # When subclassing these should be defined
    card_type: str
    gallery_info_model: type[BaseModel]

    def __init__(self, data_dir: str, thing: lt.Thing) -> None:
        """Initialise the database engine.

        :param data_dir: The directory of the data.
        :param thing: The thing that is managing this data source.
        """
        self._data_dir = data_dir
        self._thing = thing
        db_file = os.path.join(data_dir, GALLERY_DB_NAME)
        self._engine = _new_gallery_db_engine(db_file)
        self.sync_db_with_file_system()

    def dispose(self) -> None:
        """Dispose of the active connections for this database engine."""
        self._engine.dispose()

    @contextmanager
    def session(self, write: bool) -> Iterator[orm.Session]:
        """Yield a database session. Commit on completion, rollback on error."""
        # Create a database session to be yielded to caller.
        with orm.Session(self._engine) as session:
            try:
                yield session
                if write:
                    # If no errors are raised and the session is in write mode, then
                    # commit any changes made to the database.
                    session.commit()
            except:
                if write:
                    # In the case of an error, rollback the database to its state
                    # at the start of the session.
                    session.rollback()
                raise

    def fullpath_to_normpath(self, fullpath: str) -> str:
        """Convert the abs native path to the relative posix path for the database.

        The database requires normalised POSIX paths.
        """
        rel = os.path.relpath(fullpath, self._data_dir)
        # Use PurePath's as_posix function as it is reliable across OSes at turning
        # paths into posix.
        return posixpath.normpath(PurePath(rel).as_posix())

    def normpath_to_fullpath(self, normpath: str) -> str:
        """Convert the relative posix path from the database to the abs native path."""
        native_path = os.path.normpath(normpath)
        return os.path.join(self._data_dir, native_path)

    def sync_db_with_file_system(self) -> None:
        """Sync the database with the file system.

        This is run when the microscope first loads. It can also be manually called if
        the data is changed on disk. It shouldn't be needed in other situations.
        """
        fs_items = self._list_all_items_on_file_system()
        unchecked_db_items = {entry.path: entry for entry in self.get_all_entries()}

        with self.session(write=True) as session:
            for fs_item in fs_items:
                if fs_item not in unchecked_db_items:
                    session.add(self._generate_entry(fs_item))
                    continue

                # Pop from unchecked as we are now checking the item
                db_item = unchecked_db_items.pop(fs_item)
                expected_hash = self._hash(fs_item)

                # Rebuild if the hashes don't match
                rebuild = db_item.hash != expected_hash

                # If the hash matches, also check that the info model validates.
                if not rebuild:
                    try:
                        self.gallery_info_model.model_validate(db_item.gallery_info)
                    except ValidationError:
                        rebuild = True

                if rebuild:
                    session.merge(
                        self._generate_entry(
                            fs_item, gallery_added_data=db_item.gallery_added_data
                        )
                    )

            # Any items remaining at this point exist in the database but not on disk.
            # Remove them from database
            for item in unchecked_db_items.values():
                session.delete(item)

    def get(self, path: str) -> Optional[GalleryEntry]:
        """Get a single item from the gallery database.

        :param path: The normalised relative posixpath for the requested entry.

        :return: The GalleryEntry that matches the path, or None if nothing matches.
        """
        with self.session(write=False) as session:
            return session.get(GalleryEntry, path)

    def get_all_entries(self) -> list[GalleryEntry]:
        """Get a list of all Gallery Entries."""
        with self.session(write=False) as session:
            return list(session.scalars(sa.select(GalleryEntry)))

    def get_all_paths(self) -> list[str]:
        """Get a list of the paths for all gallery items."""
        return [entry.path for entry in self.get_all_entries()]

    def get_all_entries_as_base_model(self) -> list[GalleryEntryModel]:
        """Get a list of entries as a base model for the API."""
        return [self.entry_to_base_model(entry) for entry in self.get_all_entries()]

    def add(self, path: str | RelativeDataPath) -> None:
        """Add an item to the gallery database.

        This should be called when an item is added to disk.
        """
        if isinstance(path, RelativeDataPath) and self._thing != path._saving_thing:
            # This item was saved in the directory of a different Thing.
            raise RuntimeError(
                f"Cannot add {path.root} to database as it is saved in the wrong "
                "directory"
            )
        normpath = path.posixpath if isinstance(path, RelativeDataPath) else path
        with self.session(write=True) as session:
            session.add(self._generate_entry(normpath))

    def rebuild(self, path: str, ignore_missing: bool = False) -> None:
        """Rebuild a single gallery entry as it has changed on disk."""
        with self.session(write=True) as session:
            entry = session.get(GalleryEntry, path)
            if entry is None:
                if ignore_missing:
                    return
                raise ValueError("Can't rebuild {path}. It is not in the database.")
            session.merge(
                self._generate_entry(path, gallery_added_data=entry.gallery_added_data)
            )

    def delete(self, path: str) -> bool:
        """Delete an item from the gallery database."""
        entry = self.get(path)
        if entry is None:
            LOGGER.warning(
                f"Attempted to delete {path} from gallery database, but it does not "
                "exist."
            )
            return False
        with self.session(write=True) as session:
            session.delete(entry)
        return True

    def entry_to_base_model(self, entry: GalleryEntry) -> GalleryEntryModel:
        """Convert GalleryEntry to GalleryEntryModel."""
        thumbnail = (
            None
            if entry.thumbnail_source is None
            else self._mounted_path(entry.thumbnail_source)
        )
        return GalleryEntryModel(
            name=entry.path,
            path=self._mounted_path(entry.path),
            thing=self._thing.name,
            delete_endpoint=self._delete_endpoint(entry.path),
            created=entry.created,
            modified=entry.modified,
            card_type=self.card_type,
            gallery_info=entry.gallery_info,
            thumbnail_source=thumbnail,
            gallery_added_data=entry.gallery_added_data,
        )

    @abstractmethod
    def _list_all_items_on_file_system(self) -> list[str]:
        """List all the items on this file system.

        :return: A list of paths.
        """

    @abstractmethod
    def _hash(self, path: str) -> str:
        """Create a unique hash for an item.

        For images in the gallery this is just checking the image has not
        been updated or replaced. For speed we use a blake2b 8-byte hash of the file
        size and the modified time.

        :param path: The normalised relative posixpath for the item to hash. This is
            the primary key of the GalleryEntry table.

        :return: A unique identifier to check if the item has changed. For large items
            that are a folder of data it is not recommended to hash the entire file as
            this is slow. Instead, hash just enough information about the item to
            confirm that it's unchanged.
        """

    @abstractmethod
    def _generate_entry(
        self, path: str, gallery_added_data: Optional[dict] = None
    ) -> GalleryEntry:
        """Create a GalleryEntry for an item based on the data on disk.

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        :param gallery_added_data: Any data added by the gallery. This can be used to
            persist gallery added data even if item is modified on disk.

        :return: A GalleryEntry for the item.
        """

    @abstractmethod
    def _mounted_path(self, path: str) -> str:
        """Return the mount point for the normalised path.

        This may be the mount point for an entry or another file in the data directory.

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        """

    @abstractmethod
    def _delete_endpoint(self, path: str) -> str:
        """Return the endpoint for deleting an entry (relative to the base URL).

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        """
