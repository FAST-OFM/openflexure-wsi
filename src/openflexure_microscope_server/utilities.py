"""Utility functions and classes."""

import json
import logging
import os
import re
import shutil
import sys
import tomllib
from datetime import datetime
from functools import wraps
from importlib.metadata import version
from pathlib import PurePath
from typing import (
    Any,
    Callable,
    Concatenate,
    Literal,
    Mapping,
    Optional,
    ParamSpec,
    TypeAlias,
    TypeVar,
    overload,
)

from pydantic import BaseModel

import labthings_fastapi as lt
from labthings_fastapi.exceptions import InvocationError
from labthings_fastapi.invocation_contexts import get_invocation_id

T = TypeVar("T")
P = ParamSpec("P")
LockableClass = TypeVar("LockableClass")

JSONScalar: TypeAlias = str | int | float | bool | None
JSONType: TypeAlias = dict[str, "JSONType"] | list["JSONType"] | JSONScalar

LOGGER = logging.getLogger(__name__)

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
# Regex for a full commit hash
COMMIT_REGEX = re.compile(r"[0-9a-f]{40}")
# Regex for a reference in the git HEAD file. Group 1 is the path.
REF_REGEX = re.compile(r"^ref:\s(.*)$")


def _is_lock_like(obj: Any) -> bool:
    """Check if an object is a lock.

    Cannot use ``isinstance(obj, threading.RLock)``, may be possible to use
    ``isinstance(obj, threading._RLock)``. But this uses a private method and may
    break. Instead making the check that both "acquire" and "release" exist.
    """
    return hasattr(obj, "acquire") and hasattr(obj, "release")


def requires_lock(
    method: Callable[Concatenate[LockableClass, P], T],
) -> Callable[Concatenate[LockableClass, P], T]:
    """Decorate a class method so that it requires the class lock to run.

    The class should have a reentrant lock with the name ``self._lock``.
    """

    @wraps(method)
    def wrapper(self: LockableClass, *args: P.args, **kwargs: P.kwargs) -> T:
        # Confirm the object the method is attached to has a self._lock property
        if not hasattr(self, "_lock"):
            raise AttributeError(f"{self.__class__.__name__} has no '_lock' attribute")
        # And that it is a reentrant lock.
        if not _is_lock_like(self._lock):
            raise TypeError("requires_lock requires self._lock to be a lock")
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


# Compiled regular expressions for unsafe characters
# Matches anything that isn't a-z, A-Z, 0-9, _, ., -, :, /, \, space
_WINDOWS_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_.\-:/\ \\]")
# Matches anything that isn't a-z, A-Z, 0-9, _, ., -, \, space
_POSIX_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_.\-/ ]")

UNSAFE_PATTERN = (
    _WINDOWS_UNSAFE_PATTERN if sys.platform.startswith("win") else _POSIX_UNSAFE_PATTERN
)
# Matches anything that isn't a-z, A-Z, 0-9, _, ., -
_NAME_UNSAFE_PATTERN = re.compile(r"[^a-zA-Z0-9_.\-]")

# Windows reserved names (case-insensitive)
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def is_path_safe(unvalidated_path: str) -> bool:
    """Check if a file path has any unsafe elements in it.

    The path is not coerced into a safe form because if we ask
    for a file to be written to a location we shouldn't be changing
    that location as we have no "consent" to write to this other location.

    :param unvalidated_path: The original path string to sanitise.

    :returns: A boolean indicating if any unsafe features were found.
    """
    return all(
        [
            _path_normalised(unvalidated_path),
            _no_trailing_dots_or_whitespaces(unvalidated_path),
            _no_unsafe_characters(unvalidated_path),
            _no_reserved_name(unvalidated_path),
        ]
    )


def _path_normalised(unvalidated_path: str) -> bool:
    """Check that the path starts normalised.

    Warn and return false if . or .. is not at the start of the path.
    """
    original_path = unvalidated_path
    # Ensure correct path separators before normalisation:
    if sys.platform.startswith("win"):
        unvalidated_path = unvalidated_path.replace("/", "\\")
    normalised = os.path.normpath(unvalidated_path)

    # Allow starting with ./ (or .\ on Windows)
    if unvalidated_path.startswith("." + os.path.sep):
        normalised = "." + os.path.sep + normalised

    # Allow trailing path separator
    if unvalidated_path.endswith(os.path.sep):
        normalised += os.path.sep

    if normalised != unvalidated_path:
        LOGGER.warning(
            f"File path {original_path} may be unsafe due to unexpected relative "
            "navigation."
        )
        return False
    return True


def _no_trailing_dots_or_whitespaces(unvalidated_path: str) -> bool:
    """Disallow trailing dots or whitespace."""
    # Use PurePath from Python's standard library to iterate over each element
    # in the path as separated by path separators. For example:
    # "foo/bar/tada.py" -> ("foo", "bar", "tada.py")
    for component in PurePath(unvalidated_path).parts:
        if component == "..":
            continue
        bad_trailing_chars = ". " if sys.platform.startswith("win") else "."
        if component.rstrip(bad_trailing_chars) != component:
            LOGGER.warning(
                f"File path {unvalidated_path} may be unsafe due to trailing dots "
                "or whitespace."
            )
            return False
    # All passed
    return True


def _no_unsafe_characters(unvalidated_path: str) -> bool:
    """Disallow unsafe characters."""
    for component in PurePath(unvalidated_path).parts:
        if UNSAFE_PATTERN.search(component):
            LOGGER.warning(
                f"{unvalidated_path} contains characters that may be unsafe on this "
                "platform."
            )
            return False
    # All passed
    return True


def _no_reserved_name(unvalidated_path: str) -> bool:
    """Disallow use of Windows reserved names (e.g., CON, PRN, LPT1)."""
    for component in PurePath(unvalidated_path).parts:
        if _sanitise_reserved(component, is_filename=False) != component:
            LOGGER.warning(
                f"{unvalidated_path} contains a reserved system name and may cause "
                "issues."
            )
            return False
    # All passed
    return True


def make_name_safe(unsafe_name_string: str) -> str:
    """Replace unsafe characters in a filename or identifier with underscores.

    This excludes all path separators, ensuring the result is safe to use as a
    standalone filename or identifier component.

    This also handles Windows reserved names and trailing dots/spaces.

    :param unsafe_name_string: The original name string to sanitise.

    :returns: A version of the input string safe to use as a file name or identifier.
    """
    # 1. Strip trailing dots and spaces
    # 2. Apply unsafe character regex
    # 3. Check for reserved names
    name = unsafe_name_string.rstrip(". ")
    if not name:
        return "_"

    name = _NAME_UNSAFE_PATTERN.sub("_", name)
    return _sanitise_reserved(name)


def _sanitise_reserved(name: str, is_filename: bool = True) -> str:
    """Check a name against Windows reserved names.

    :param name: The component to check.
    :param is_filename: Whether the component is a filename.
        If True, names will be forced into lowercase.

    :returns: The sanitised component, in lower case.
    """
    # Check for Windows reserved names.
    # These are reserved even with an extension (e.g. NUL.txt).
    # We check the part before the first dot.
    base_name = name.split(".", maxsplit=1)[0].upper()
    if base_name in _WINDOWS_RESERVED_NAMES:
        return f"{name.lower()}_" if is_filename else f"{name}_"

    # We force names to be lowercase to avoid issues on
    # Windows where files with the same name
    # but different case can cause problems when checking if
    # a file already exists.
    return name.lower() if is_filename else name


class VersionData(BaseModel):
    """A BaseModel containing the information about the server version.

    :param version: The version string for the server. Or "Undefined" if there is an
        error.
    :param version_source: Either a Git commit hash, "TOML", "Dist" or in the case of
        an error - "Undefined".
    """

    version: str
    version_source: str


def robust_version_strings() -> VersionData:
    """Return a version string and information on its source.

    Returning a python version string is not without problems. If the package is
    installed in an editable way, often METADATA is never updated. This provides 4
    ways to provide a version string:

    1. If both a Git directory and a pyproject.toml are available. Return the version
        string from the toml file and the git hash from the ``.git`` directory as its
        source.
    2. If a pyproject.toml is available but no Git directory then this is likely an
        installation from source. Return the version string from the toml file and
        return the literal string "TOML" as its source.
    3. If a Git directory is available but no pyproject.toml then something is very
        weird - log an error. Then return "Undefined" as the version string, but still
        return the Git hash as the source.
    4. Finally if neither a Git directory nor a pyproject.toml are available then the
       server has been installed from a distribution package (i.e. a wheel). In this
       case use the standard library function ``importlib.metadata.version`` for the
       version string (prepending a "v") and return "Dist" as its source.

    :returns: a VersionData instance with the version string and source.

    """
    project_toml_path = os.path.join(REPO_DIR, "pyproject.toml")
    git_dir_path = os.path.join(REPO_DIR, ".git")
    has_project_toml = os.path.isfile(project_toml_path)
    has_git_dir = os.path.isdir(git_dir_path)

    if has_project_toml and has_git_dir:
        # .git dir and pyproject.toml available. This is a development
        # installation.
        ver = "v" + _get_version_from_toml(project_toml_path)
        source = _get_hash_from_git_dir(git_dir_path)
    elif has_project_toml:
        # Only pyproject.toml available. Likely installed from source. Check
        # file directly as `importlib.metadata.version` plays badly with editable
        # installs.
        ver = "v" + _get_version_from_toml(project_toml_path)
        source = "TOML"
    elif has_git_dir:
        # Only .git dir, that is weird.
        LOGGER.error(
            "Unexpected installation configuration. Version number cannot be verified."
        )
        ver = "Undefined"
        source = _get_hash_from_git_dir(git_dir_path)
    else:
        # Has neither .git nor pyproject.toml. It is probably installed from wheel.
        # This will be the expected method of packaging in the future. This option
        # is handled last as ``importlib.metadata.version`` can be unreliable if
        # the package is no installed as a distribution package.
        ver = "v" + version("openflexure_microscope_server")
        source = "Dist"
    return VersionData(version=ver, version_source=source)


def _get_hash_from_git_dir(git_dir_path: str) -> str:
    """Get the commit hash from a .git directory directly.

    This function doesn't rely on GIT being on the PATH or even installed. It doesn't
    require any packages outside the Python standard library. It directly inspects the
    .git directory to get the commit hash:

    * If the repository is on a detached HEAD then the hash will be in the file
        .git/HEAD. A detached HEAD is could be from checking out a tag or checking out
        a commit directly.
    * In normal operation .git/HEAD points to a reference or "ref". This ref file
        contains the hash.

    :param git_dir_path: The path to the .git directory.

    :returns: The hash, or "Undefined" if there is any error, errors are logged not
        raised.
    """
    head_path = os.path.join(git_dir_path, "HEAD")
    try:
        with open(head_path, "r", encoding="utf-8") as head_file:
            head = head_file.read()
    except IOError:
        LOGGER.error("Problem opening .git/HEAD")
        return "Undefined"
    # The file HEAD should either be a reference or commit ID.
    if match := COMMIT_REGEX.match(head):
        return match.group(0)
    if match := REF_REGEX.match(head):
        ref_path = os.path.join(git_dir_path, match.group(1))
        return _get_hash_from_git_ref(ref_path)
    LOGGER.error("Unexpected format for .git/HEAD")
    return "Undefined"


def _get_hash_from_git_ref(git_ref_path: str) -> str:
    """Get the commit hash from a ref in the .git directory.

    For more detail see `_get_hash_from_git_dir`

    :param git_ref_path: The full path to the ref file in the .git directory.

    :returns: The hash, or "Undefined" if there is any error, errors are logged not
        raised.
    """
    try:
        with open(git_ref_path, "r", encoding="utf-8") as ref_file:
            ref = ref_file.read()
    except IOError:
        LOGGER.error("Problem opening Git branch reference.")
        return "Undefined"
    if ref_match := COMMIT_REGEX.match(ref):
        return ref_match.group(0)
    LOGGER.error("No hash found in Git ref")
    return "Undefined"


def _get_version_from_toml(toml_path: str) -> str:
    """Get the version string from a pyproject.toml file.

    :param toml_path: The path to the toml file.

    :returns: The version string directly as reported in the toml file, or "Undefined"
        if there is any error, errors are logged not raised.
    """
    try:
        with open(toml_path, "rb") as toml_file:
            toml_dict = tomllib.load(toml_file)
        return toml_dict["project"]["version"]
    except (IOError, ValueError, KeyError):
        LOGGER.error("Problem opening pyproject.toml")
        return "Undefined"


# Use overload to clarify to MyPy that if enforce_dict is true, inputs and outputs are
# dictionaries
@overload
def merge_patch(
    target: dict[str, JSONType], patch: dict[str, JSONType], enforce_dict: Literal[True]
) -> dict[str, JSONType]: ...


# If not they are any JSONType
@overload
def merge_patch(
    target: JSONType, patch: JSONType, enforce_dict: Literal[False]
) -> JSONType: ...
@overload
def merge_patch(target: JSONType, patch: JSONType) -> JSONType: ...


def merge_patch(
    target: JSONType, patch: JSONType, enforce_dict: bool = False
) -> JSONType:
    """Merge json data using the methods from IETF RFC 7396.

    Primarily this is designed to merge dictionaries, but IETF RFC 7396 provides
    defined methods for handling non-dictionary data loaded from JSON, so this
    has been provided in full.

    :param target: The target object
    :param patch: The patch to be applied
    :param enforce_dict: Boolean, set True enforces that the target and patch are both
        dictionaries.
    """
    if enforce_dict and not (isinstance(target, dict) and isinstance(patch, dict)):
        raise ValueError("Both target and patch should be dictionaries.")

    # If patch is not a dict (object), it replaces target entirely:
    if not isinstance(patch, dict):
        return patch

    # If target is not an object, replace it with an empty object
    if not isinstance(target, dict):
        target = {}

    result = {}
    # First, keep all keys from target that are not in patch
    for key, target_val in target.items():
        if key not in patch:
            result[key] = target_val

    # Then apply patch keys
    for key, patch_val in patch.items():
        # If patch_val is None, do not add the key (deleting from target).
        if patch_val is None:
            continue

        # Check if values are dictionaries.
        if isinstance(patch_val, dict):
            # If patch value is a dictionary then recurse.
            result[key] = merge_patch(target.get(key), patch_val)
        else:
            # Else, use the patch value
            result[key] = patch_val
    return result


def load_patched_config(config_path: str) -> dict:
    """Load a json configuration file, if it a patch, return the full config.

    Load a json file. If it does not contains the "base_config_file" key then return
    the data as read.

    If the configuration specifies a "base_config_file" but no "patch" then return the
    data from reading base_config_file as json.

    If the configuration specifies a "base_config_file" and "patch" then apply the
    patch to the data in base_config_file and return the result. The patching method
    is merge_patch()

    :param config_path: The path of the configuration file.
    :return: The contents of the configuration file after loading and patching the
        specified base configuration file if applicable.

    """
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except IOError as e:
        raise type(e)(f"Couldn't load configuration file {config_path}") from e
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"Invalid JSON in configuration file {config_path}: {e}", e.doc, e.pos
        ) from e

    # If base_config_file not specified then this is a normal LabThings config file
    # Just return it.
    if "base_config_file" not in config:
        return config

    config_dir = os.path.dirname(os.path.abspath(config_path))
    base_conf_path = config["base_config_file"]

    base_conf_path = resolve_path_from_dir(base_conf_path, config_dir)

    try:
        with open(base_conf_path, "r", encoding="utf-8") as f:
            base_config = json.load(f)
    except IOError as e:
        raise type(e)(f"Couldn't load base configuration file {base_conf_path}") from e
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"Invalid JSON in base configuration file {config_path}: {e}", e.doc, e.pos
        ) from e

    if "patch" in config:
        return merge_patch(base_config, config["patch"], enforce_dict=True)

    return base_config


def resolve_path_from_dir(path: str, directory: str) -> str:
    """Convert a relative or abs path specified in one dir to the working dir.

    This also expands any environment variables.

    :param path: The specified path (could be absolute or relative)
    :param directory: The directory from which the path was specified
    :return: The normalised path.
    """
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = os.path.join(directory, path)
    return os.path.normpath(path)


def coerce_thing_selector(
    thing_mapping: Mapping[str, lt.Thing],
    selected: Optional[str],
    default: Optional[str] = None,
) -> Optional[str]:
    """Return a valid selector for a mapping of things or None if empty.

    :param thing_mapping: A mapping of str -> ``Thing``
    :param selected: The key of the selected thing (or ``None``)
    :param default: The default key to fallback to if selected is ``None`` or the key is
        missing
    :return: ``None`` if the mapping is empty, else a key that is in the mapping. Order
        of preference is: selected, default, the first key.
    """
    if not thing_mapping:
        # Empty mapping
        if selected is not None:
            LOGGER.warning(
                f"Could not select {selected} from Thing mapping as the mapping is empty."
            )
        # The return is always None if the mapping is empty
        return None

    if selected in thing_mapping:
        # Selected exists, return it.
        return selected

    if selected is not None:
        LOGGER.warning(f"Could not select '{selected}' from Thing mapping")

    if default in thing_mapping:
        return default

    if default is not None:
        LOGGER.warning(f"Could not select default key '{default}' from Thing mapping")

    # Final option is to return the first key
    return list(thing_mapping)[0]


def get_invocation_logs(
    thing: lt.Thing, logged_since: float = 0.0
) -> list[logging.LogRecord]:
    """Get logs from an ongoing action invocation.

    This must be called from the action's context (thread).

    Note that only 1000 logs are stored in LabThings. For very long tasks that
    log regularly it may be good to periodically poll this and request only
    the logs since the last log was created.

    :param thing: The thing that the action is called from.
    :param logged_since: The timestamp that records must come after. This could be the
        ``record.created`` time of the last log.
    :return: A list of ``LogRecord`` objects.
    """
    server = thing._thing_server_interface._server()
    if server is None:  # pragma: no cover
        # This should be impossible to reach, but we require this line because the
        # server is a weak_ref which MyPy says could return None.
        raise RuntimeError("Could not retrieve server from thing_server_interface.")
    manager = server.action_manager
    inv_id = get_invocation_id()
    invocation = manager.get_invocation(inv_id)

    # Type ignore needed as LabThings incorrectly reports the type from invocation.log
    # If pull request 260 on LabThings is merged we can remove this function.
    return [record for record in invocation.log if record.created > logged_since]  # type: ignore


def save_invocation_logs(
    filename: str, thing: lt.Thing, last_saved: float = 0.0, append: bool = True
) -> float:
    """Save the invocation logs from and ongoing action.

    This must be called from the action's context (thread).

    :param filename: The filename to write the logs to.
    :param thing: The thing that the action is called from.
    :param last_saved: The timestamp that records must come after.
    :param append: If True (default) append to the file if it exists.

    :return: The timestamp of the most recent log.
    """
    logs = get_invocation_logs(thing, last_saved)
    mode = "a" if append else "w"
    with open(filename, mode, encoding="utf-8") as log_file:
        for record in logs:
            level = record.levelname
            dt = datetime.fromtimestamp(record.created)
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            msg = record.getMessage()
            log_file.write(f"[{date_str}] [{level}] {msg}\n")
    return 0.0 if not logs else logs[-1].created


class NotEnoughFreeSpaceError(InvocationError):
    """An exception raised if there is not enough free space on disk to scan."""


def check_free_disk_space(directory: str, min_space: int = 500000000) -> None:
    """Raise an exception if there is not enough free disk space to continue.

    :param min_space: the minimum space required in bytes.
        Default = 500,000,000 (500MB)

    :raises NotEnoughFreeSpaceError: if the remaining storage is below min_space
    """
    disk_usage = shutil.disk_usage(directory)
    if disk_usage.free < min_space:
        raise NotEnoughFreeSpaceError(
            "There is not enough free disk space to continue. "
            f"(Required: {min_space >> 20}MB. Free: {disk_usage.free >> 20}MB)."
        )
