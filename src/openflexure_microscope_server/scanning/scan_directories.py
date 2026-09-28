"""Functionality to manage file system operations for scan directories."""

import hashlib
import json
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

import labthings_fastapi as lt

from openflexure_microscope_server.gallery_db import GalleryEntry, OFMGalleryDBEngine
from openflexure_microscope_server.stitching.stitching import StitchingSettings
from openflexure_microscope_server.utilities import (
    make_name_safe,
    save_invocation_logs,
)

LOGGER = logging.getLogger(__name__)

IMG_DIR_NAME = "images"
SCAN_ZERO_PAD_DIGITS = 4

STITCH_JPEG_REGEX = re.compile(r"stitched\.jpe?g$")
STITCH_TIFF_REGEX = re.compile(r"stitched\.ome\.tiff?$")
IMAGE_REGEX = re.compile(r"-?[0-9]+_-?[0-9]+\.jpe?g$")

SCAN_DATA_FILENAME = "scan_data.json"
SCAN_DATA_SCHEMA_VERSION = 2


class ScanGalleryInfo(BaseModel):
    """Summary information for the UI about a scan folder."""

    duration: Optional[float]
    number_of_images: int
    # Stitch available could also be determined from if the jpeg is None, but it is
    # is useful to have a boolean. And may be more use if we start choosing TIFF vs
    # JPEG
    stitch_available: bool
    stitched_jpeg: Optional[str]
    dzi: Optional[str]
    stitched_tiff: Optional[str] = None
    thing: str = "smart_scan"


class BaseScanData(BaseModel):
    """Data about a scan not including workflow specific data.

    For including workflow specific data see also:

    * ActiveScanData which subclasses this including the BaseModel used by the
        ScanWorkflow
    * HistoricScanData which has the workflow specific data loaded as a dictionary.

    Separating historic and active data allows workflows to use any BaseModel for its
    settings, but for the data to be reloaded even if that model has updated or is not
    available. Historic scan data loaded from disk is used for stitching and for
    creating a ScanGalleryInfo object for communicating with the UI. These uses are
    clearly typed by this model.

    This serialises into a human readable format where possible with

    timestamps in %Y-%m-%d_%H:%M:%S format
    timedeltas in %H:%M:%S format

    Properties that are not known until the end have ``None`` serialised as "Unknown"
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCAN_DATA_SCHEMA_VERSION

    scan_name: str
    """The name of the scan i.e. scan_0001"""

    starting_position: Mapping[str, int]
    """The starting position in dictionary format."""

    start_time: datetime
    """The time the scan started."""

    stitch_automatically: bool
    """Whether the scan is set to automatically stitch when complete."""

    save_resolution: tuple[int, int]
    """The resolution that scan images are saved at."""

    image_count: int = 0
    """The number of images taken."""

    duration: Optional[timedelta] = None
    """The duration of the scan.

    This is automatically set when ``set_final_data()`` is run.
    """

    scan_result: Optional[str] = None
    """The result of the scan.

    This should be set with ``set_final_data()`` to ensure duration is set.
    """

    completion_reason: Optional[str] = None
    """A human readable explanation of why the scan ended.

    For a successful scan this explains why the scan planner stopped (e.g. it
    reached the maximum range of motion, or ran out of sample to follow). For a
    cancelled or failed scan, the detail is already captured in ``scan_result``
    and this is left as None. Set with ``set_final_data()``.
    """

    stitching_settings: Optional[StitchingSettings]
    """The data needed to stitch a scan.

    Set to None for types of scan that cannot be stitched.
    """

    workflow: str
    """The class name of the workflow Thing."""

    @model_validator(mode="after")
    def validate_schema_version(self) -> Self:
        """Validate the schema version is as the current one."""
        if self.schema_version != SCAN_DATA_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema version {self.schema_version}, "
                f"expected {SCAN_DATA_SCHEMA_VERSION}"
            )
        return self

    @field_validator("start_time", mode="before")
    @classmethod
    def parse_timestamp(cls, value: str | datetime) -> datetime:
        """Validate a timestamp that may be a string in Year-Month-Day_Hrs:Min:Sec format."""
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%d_%H:%M:%S")
        return value

    @field_serializer("start_time")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialise timestamp to Year-Month-Day_Hrs:Min:Sec format."""
        return value.strftime("%Y-%m-%d_%H:%M:%S")

    @field_validator("duration", mode="before")
    @classmethod
    def parse_timedelta(cls, value: Optional[str | timedelta]) -> Optional[timedelta]:
        """Validate a timedelta that may be a string in Hrs:Min:Sec format or "Unknown"."""
        if isinstance(value, str):
            if value == "Unknown":
                return None
            hrs, mins, secs = map(int, value.split(":"))
            return timedelta(hours=hrs, minutes=mins, seconds=secs)
        return value

    @field_serializer("duration")
    def serialize_timedelta(self, value: Optional[timedelta]) -> str:
        """Serialise timedelta to Hrs:Min:Sec (or "Unknown" if None)."""
        if value is None:
            return "Unknown"
        # Calculate the string manually rather than use str(), as this may convert to
        # days for a very very long scan, and then it is much harder to parse.
        total_secs = value.total_seconds()
        hrs = int(total_secs // 3600)
        mins = int((total_secs % 3600) // 60)
        secs = int((total_secs % 60))
        return f"{hrs}:{mins:02}:{secs:02}"

    @field_validator("scan_result", "completion_reason", mode="before")
    @classmethod
    def parse_unknown_as_none(cls, value: Optional[str | int]) -> Optional[str | int]:
        """Validate the string "Unknown" as None."""
        return None if value == "Unknown" else value

    @field_serializer("scan_result", "completion_reason")
    def serialize_none_as_unknown(self, value: Optional[str | int]) -> str | int:
        """Serialise None as "Unknown" for a more human readable result."""
        return "Unknown" if value is None else value


class HistoricScanData(BaseScanData):
    """A Model for the scan data that has been loaded from disk.

    Any workflow specific settings are loaded as an arbitrary dictionary. Other
    settings such as those which are needed for the UI or stitching are loaded and
    validated by the parent class ``BaseScanData``.
    """

    workflow_settings: dict
    """A dictionary of the settings for the workflow that was used workflow."""

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy(cls, data: dict) -> dict:
        """Coerce any scan data from before version 2 into the version 2 format."""
        # Before the current version no schema_version was set
        if "schema_version" in data:
            return data

        if "correlation_resize" and "overlap" in data:
            correlation_resize = data.pop("correlation_resize")
            # Note we don't pop overlap, as it is a setting for the legacy workflow as well
            # as a stitching setting.
            # This is done because in future workflows the stitching overlap may be a
            # directly set setting or something that is calculated from other settings.
            overlap = data["overlap"]
            data["stitching_settings"] = StitchingSettings(
                correlation_resize=correlation_resize,
                overlap=overlap,
            )
        else:
            data["stitching_settings"] = None

        # Add any legacy workflow settings that are found
        legacy_keys = [
            "overlap",
            "max_dist",
            "dx",
            "dy",
            "autofocus_dz",
            "autofocus_on",
            "skip_background",
        ]
        workflow_settings = {}
        for key in legacy_keys:
            if key in data:
                workflow_settings[key] = data.pop(key)

        data["workflow"] = "Legacy"
        data["workflow_settings"] = workflow_settings
        return data


class ScanGalleryDBEngine(OFMGalleryDBEngine):
    """The database engine to back gallery interactions for scans."""

    card_type = "Scan"
    gallery_info_model = ScanGalleryInfo

    def _list_all_items_on_file_system(self) -> list[str]:
        """List all the items on this file system.

        :return: A list of paths.
        """
        return [f.name for f in os.scandir(self._data_dir) if f.is_dir()]

    def _hash(self, path: str) -> str:
        """Create a unique hash for an item.

        For the scan this is a hash of the json file (if it exists) plus the scan
        modified timestamp.

        :param path: The normalised relative posixpath for the item to hash, in this
            case, the scan directory. This is the primary key of the GalleryEntry table.

        :return: A unique identifier to check if the item has changed. For large items
            that are a folder of data it is not recommended to hash the entire file as
            this is slow. Instead, hash just enough information about the item to
            confirm that it's unchanged.
        """
        scan_dir_obj = ScanDirectory(path, self._data_dir)
        data_file = scan_dir_obj.scan_data_path

        h = hashlib.blake2b(digest_size=8)
        if data_file is not None and os.path.exists(data_file):
            with open(data_file, "rb") as f_obj:
                # Update hash object based on the scan info file and the folder
                # modified time
                h.update(f_obj.read())
        h.update(int(scan_dir_obj.get_modified_time()).to_bytes(8, "little"))

        return h.hexdigest()

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
        scan_dir_obj = ScanDirectory(path, self._data_dir)

        return GalleryEntry(
            path=path,
            hash=self._hash(path),
            created=scan_dir_obj.created_time,
            modified=scan_dir_obj.get_modified_time(),
            gallery_info=scan_dir_obj.scan_info().model_dump(),
            thumbnail_source=f"{path}/{IMG_DIR_NAME}/stitched_thumbnail.jpg",
            gallery_added_data={} if gallery_added_data is None else gallery_added_data,
        )

    def _mounted_path(self, path: str) -> str:
        """Return the mount point for the normalised path.

        This may be the mount point for an entry or another file in the data directory.

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        """
        return self._thing.name + "/" + path

    def _delete_endpoint(self, path: str) -> str:
        """Return the endpoint for deleting an entry (relative to the base URL).

        :param path: The normalised relative posixpath for the item. This is the
            primary key of the GalleryEntry table.
        """
        return self._thing.name + "/scans/" + path

    def unique_scan_name(self, scan_name: str) -> str:
        """Get the next unique scan name starting with the given name.

        For more explanation on the scan naming see `new_scan_dir`
        """
        # if no scan name is set, set it to "scan". This done here as empty strings
        # get passed in otherwise.
        if not scan_name:
            scan_name = "scan"
        scan_name = make_name_safe(scan_name)

        # Strip all trailing underscores from the base name
        scan_name = scan_name.strip("_")
        # A regex with the scan name and a group for the numbers
        scan_regex = re.compile(
            "^" + scan_name + "_([0-9]{" + str(SCAN_ZERO_PAD_DIGITS) + "})$"
        )

        matching_scans = [
            scan for scan in self.get_all_paths() if scan_regex.match(scan)
        ]
        if not matching_scans:
            scan_num = 1
        else:
            last_matching_scan = sorted(matching_scans)[-1]
            # Get the first group from the regex, turn to int, and add 1
            scan_match = scan_regex.match(last_matching_scan)

            if scan_match is None:  # pragma: no cover
                # Type narrow, we know it is a match but mypy doesn't. This code is
                # Not reachable hence the no cover.
                raise RuntimeError("Internal error: regex No longer matches")

            scan_num = int(scan_match[1]) + 1

        # Set a sensible limit for the number of scans of one name
        # based on our zero padding.
        max_scan_no = 10**SCAN_ZERO_PAD_DIGITS - 1

        if scan_num > max_scan_no:
            raise FileExistsError(
                "Could not create a new scan folder: all names in use!"
            )

        return f"{scan_name}_{scan_num:0{SCAN_ZERO_PAD_DIGITS}d}"


def latest_scan_preview(scan_name: str, base_dir: str) -> Optional[str]:
    """Return the path of the latest preview from a scan or None if not available."""
    path = os.path.join(base_dir, scan_name, IMG_DIR_NAME, "preview.jpg")
    if os.path.exists(path):
        return path
    return None


class ScanDirectory:
    """A class for handling interactions with scan directories."""

    _name: str
    _base_scan_dir: str

    def __init__(self, name: str, base_scan_dir: str) -> None:
        """Initialise the scan directory.

        :param name: the name of the scan (the scan directory basename).
        :param base_scan_dir: Path of the directory that holds all scans.
        """
        self._log_saved_at = 0.0
        self._name = name
        self._base_scan_dir = base_scan_dir
        if not os.path.isdir(self.dir_path):
            raise FileNotFoundError(
                f"The scan directory {self.dir_path} cannot be found"
            )

    @classmethod
    def new_scan_dir(cls, scan_name: str, base_scan_dir: str) -> "ScanDirectory":
        """Create a new directory for the scan.

        This does not coerce the scan name, this should be done before running this
        function.

        Creates a new empty folder, into which scans are saved

        Returns the a ScanDirectory object
        """
        if scan_name != make_name_safe(scan_name):
            raise ValueError(f"{scan_name} contains unsafe characters.")
        fullpath = os.path.join(base_scan_dir, scan_name)

        if os.path.isdir(fullpath):
            raise ValueError(f"A scan with the name {scan_name} already exists.")

        os.makedirs(fullpath)
        os.makedirs(os.path.join(fullpath, IMG_DIR_NAME))
        return cls(scan_name, base_scan_dir)

    @property
    def name(self) -> str:
        """The name of the scan."""
        return self._name

    @property
    def dir_path(self) -> str:
        """The full path to the scan directory."""
        return os.path.join(self._base_scan_dir, self._name)

    @property
    def images_dir(self) -> Optional[str]:
        """The path to the images directory.

        None is returned if no images directory was created.
        """
        im_path = os.path.join(self.dir_path, IMG_DIR_NAME)
        if os.path.isdir(im_path):
            return im_path
        return None

    @property
    def scan_data_path(self) -> Optional[str]:
        """The path to the scan data json file for this directory.

        Returns None if there is no images dir to write to.
        """
        if self.images_dir is not None:
            return os.path.join(self.images_dir, SCAN_DATA_FILENAME)
        return None

    @property
    def created_time(self) -> float:
        """The time the directory was created on disk."""
        return os.path.getctime(self.dir_path)

    def get_scan_files(self) -> list[str]:
        """Return a list of the files in the images dir."""
        if self.images_dir is None:
            return []
        return os.listdir(self.images_dir)

    def _extract_scan_images(self, file_list: list[str]) -> list[str]:
        """Extract files which match the naming convention for scan images.

        :param file_list: The list of files to search. Normally this would be
            ``self.get_scan_files()``

        :returns: The list of files that match the naming convention for scan images
        """
        return [i for i in file_list if IMAGE_REGEX.search(i)]

    def _extract_final_stitches(self, file_list: list[str]) -> list[str]:
        """Extract files which match the naming convention for final stitches.

        :param file_list: The list of files to search.

        :returns: The list of files that match the naming convention for final stitches
        """
        tiffs = [i for i in file_list if STITCH_TIFF_REGEX.search(i)]
        jpegs = [i for i in file_list if STITCH_JPEG_REGEX.search(i)]
        return tiffs + jpegs

    def _extract_final_tiffs(self, file_list: list[str]) -> list[str]:
        """Return final pyramidal OME-TIFF files from a scan directory listing."""
        return [i for i in file_list if STITCH_TIFF_REGEX.search(i)]

    def _extract_final_jpegs(self, file_list: list[str]) -> list[str]:
        """Return legacy flat stitched JPEG files from a scan directory listing."""
        return [i for i in file_list if STITCH_JPEG_REGEX.search(i)]

    def _extract_dzi_files(self, file_list: list[str]) -> list[str]:
        """Extract files which match the naming convention for dzi_files.

        :param file_list: The list of files to search.

        :returns: The list of files that match the naming convention for dzi_files
        """
        return [i for i in file_list if i.endswith("dzi")]

    def get_final_stitch_name(self) -> Optional[str]:
        """Return the filename for the final stitch (in the images dir).

        If no final stitch is found, return None
        """
        stitches = self._extract_final_stitches(self.get_scan_files())
        if not stitches:
            return None
        return stitches[0]

    def get_modified_time(self) -> float:
        """Return the modified time of the directory."""
        return max(os.stat(root).st_mtime for root, _, _ in os.walk(self.dir_path))

    def _get_scan_data_dict(self) -> Optional[dict[str, Any]]:
        """Return the scan data from the json file as a dictionary.

        This is safer than get_scan_data for older scans before a defined model was
        used.

        :return: The data as a dictionary or None if it couldn't be loaded.
        """
        if self.scan_data_path is None or not os.path.exists(self.scan_data_path):
            return None
        try:
            with open(self.scan_data_path, "r", encoding="utf-8") as data_file:
                return json.load(data_file)
        except (json.decoder.JSONDecodeError, IOError):
            LOGGER.warning(f"Could not load scan data for {self.name}.")
            return None

    def get_scan_data(self) -> Optional[HistoricScanData]:
        """Return the scan data from the json file as a HistoricScanData model.

        :return: The data as a HistoricScanData model or None if it couldn't be loaded or
            valdiated.
        """
        data_dict = self._get_scan_data_dict()
        if data_dict is None:
            return None
        try:
            return HistoricScanData(**data_dict)
        except ValidationError:
            LOGGER.warning(f"Could not validate scan data for {self.name}.")
            return None

    def scan_info(self, skip_json: bool = False) -> ScanGalleryInfo:
        """Return the information to be used in the UI for the scan."""
        scan_files = self.get_scan_files()
        scan_images = self._extract_scan_images(scan_files)
        tiffs = self._extract_final_tiffs(scan_files)
        jpegs = self._extract_final_jpegs(scan_files)
        dzi_files = self._extract_dzi_files(scan_files)
        number_of_images = len(scan_images)
        stitch_available = bool(tiffs or jpegs)
        stitched_tiff = None if not tiffs else f"{IMG_DIR_NAME}/{tiffs[0]}"
        stitched_jpeg = None if not jpegs else f"{IMG_DIR_NAME}/{jpegs[0]}"
        dzi = None if not dzi_files else f"{IMG_DIR_NAME}/{dzi_files[0]}"

        scan_data = None if skip_json else self.get_scan_data()
        duration = (
            None
            if scan_data is None or scan_data.duration is None
            else scan_data.duration.total_seconds()
        )

        return ScanGalleryInfo(
            duration=duration,
            number_of_images=number_of_images,
            stitch_available=stitch_available,
            dzi=dzi,
            stitched_jpeg=stitched_jpeg,
            stitched_tiff=stitched_tiff,
        )

    def all_files(self, skip_dirs: Optional[list[str]] = None) -> list[str]:
        """Return a list of all files in the scan dir relative to the dir.

        :param skip_dirs: Skip any file in a directory that is on this list. The list
            should be the basename of the directory. e.g. "scan_0001_files" not
            "images/scan_0001_files"
        """
        if skip_dirs is None:
            skip_dirs = []
        files = []
        for file_root, dirs, filenames in os.walk(self.dir_path, topdown=True):
            # Skip any skipped directories.
            # Note: we must use slice assignment to edit in place.
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for filename in filenames:
                full_path = os.path.join(file_root, filename)
                files.append(os.path.relpath(full_path, self.dir_path))
        return files

    def save_scan_data(self, scan_data: BaseScanData) -> None:
        """Save the scan data for this scan to disk."""
        if self.scan_data_path is None:
            raise FileNotFoundError(
                "There is no images directory to save scan data into."
            )

        with open(self.scan_data_path, "w", encoding="utf-8") as f:
            f.write(scan_data.model_dump_json(indent=4))

    def save_focus_evidence(self, relative_path: str, value: Mapping[str, Any]) -> None:
        """Atomically publish one scan-scoped focus evidence object.

        Focus checkpoints are intentionally separate from the historic gallery
        schema.  Each event is independently replace-safe and therefore remains
        useful for analysis after a crash, but never authorizes automatic resume.
        """
        parts = Path(relative_path).parts
        if (
            not parts
            or Path(relative_path).is_absolute()
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise ValueError("Focus evidence path must stay inside its scan archive")
        directory = Path(self.dir_path) / "focus"
        path = directory.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                json.dump(dict(value), handle, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save_scan_log(self, thing: lt.Thing) -> None:
        """Save the scan log to file.

        :param thing: The SmartScanThing. This is needed to exxtract the logs from the
            LabThings server.
        """
        self._log_saved_at = save_invocation_logs(
            filename=os.path.join(self.dir_path, "log.txt"),
            thing=thing,
            last_saved=self._log_saved_at,
            append=True,
        )

    def zip_files(self, final_version: bool = False) -> str:
        """Zips any images from the scan not yet zipped, return full path to zip.

        ``final_version`` Set true to stitch all files not just the scan images
        this should only be done at the end as it is not possible to update a file
        in a zip.
        """
        zip_fname = os.path.join(self.dir_path, "images.zip")

        # Use noqa as converting this into a 1 liner is not more readable.
        if os.path.isfile(zip_fname):  # noqa: SIM108
            # get a list of files in the existing zip
            zip_files = get_files_in_zip(zip_fname)
        else:
            zip_files = []

        # For each `filename.dzi` we need to skip the `filename_files` directory
        dzi_files = self._extract_dzi_files(self.get_scan_files())
        dzi_dirs = [dzi[:-4] + "_files" for dzi in dzi_files]

        with zipfile.ZipFile(zip_fname, mode="a") as scan_zip:
            for file in self.all_files(skip_dirs=dzi_dirs):
                # Don't zip zipfiles, dzi files, or files already in the zip
                if file.endswith((".zip", ".dzi")) or file in zip_files:
                    continue
                # If this is not the final version, then only zip image files.
                if not final_version and not IMAGE_REGEX.search(file):
                    continue

                scan_zip.write(os.path.join(self.dir_path, file), arcname=file)
        return zip_fname


def get_files_in_zip(zip_path: str) -> list[str]:
    """List the relative paths of all files and folders in the zip folder specified."""
    scan_zip = zipfile.ZipFile(zip_path)
    return [os.path.normpath(i) for i in scan_zip.namelist()]
