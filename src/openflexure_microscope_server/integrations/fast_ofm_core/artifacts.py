"""GPL-side immutable artifact construction for the separate core process."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import numpy as np

FRAME_MEDIA_TYPE = "application/vnd.fast-ofm.rg-frame+npz"
CALIBRATION_MEDIA_TYPE = "application/vnd.fast-ofm.rg-calibration+json"
SERIES_MEDIA_TYPE = "application/vnd.fast-ofm.rg-calibration-series+json"
STITCH_MANIFEST_MEDIA_TYPE = "application/vnd.fast-ofm.stitch-manifest+json"
FLAT_FIELD_INPUT_MEDIA_TYPE = "application/vnd.fast-ofm.rg-flat-field-input+npz"
FLAT_FIELD_RESULT_MEDIA_TYPE = "application/vnd.fast-ofm.rg-flat-field-result+npz"
TISSUE_FIELD_INPUT_MEDIA_TYPE = "application/vnd.fast-ofm.rg-tissue-field-input+npz"
TISSUE_FIELD_RESULT_MEDIA_TYPE = "application/vnd.fast-ofm.rg-tissue-field-result+npz"
SIMULTANEOUS_INPUT_MEDIA_TYPE = "application/vnd.fast-ofm.rg-simultaneous-input+npz"
SIMULTANEOUS_RESULT_MEDIA_TYPE = "application/vnd.fast-ofm.rg-simultaneous-result+npz"
MAXIMUM_FLAT_FIELD_RESULT_BYTES = 1024 * 1024 * 1024
MAXIMUM_FLAT_FIELD_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_TISSUE_FIELD_RESULT_BYTES = 512 * 1024 * 1024
MAXIMUM_TISSUE_FIELD_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAXIMUM_SIMULTANEOUS_RESULT_BYTES = 2 * 1024 * 1024 * 1024
MAXIMUM_SIMULTANEOUS_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_descriptor(path: Path, media_type: str, *, role: str) -> dict[str, object]:
    """Describe exact immutable bytes for the process protocol."""
    resolved = path.resolve(strict=True)
    return {
        "uri": resolved.as_uri(),
        "sha256": _sha256(resolved),
        "media_type": media_type,
        "size_bytes": resolved.stat().st_size,
        "role": role,
    }


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary_name).replace(path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_rg_measurement_artifacts(  # noqa: PLR0913
    directory: Path,
    *,
    white_frame: np.ndarray,
    red_plane: np.ndarray,
    green_plane: np.ndarray,
    red_source_jpeg8: np.ndarray,
    green_source_jpeg8: np.ndarray,
    red_valid: np.ndarray,
    green_valid: np.ndarray,
    white_reference: Mapping[str, object],
    red_reference: Mapping[str, object],
    green_reference: Mapping[str, object],
    geometry: Mapping[str, object],
    calibration_id: str,
    measurement_policy: Mapping[str, object],
    profile: Mapping[str, object] | None = None,
    control_settings: Mapping[str, object] | None = None,
    iteration: int = 0,
    total_correction_um: float = 0.0,
) -> dict[str, dict[str, object]]:
    """Atomically write the two versioned artifacts consumed by ``rg.measure``."""
    directory.mkdir(parents=True, exist_ok=True)
    metadata = json.dumps(
        {
            "white_reference": dict(white_reference),
            "red_reference": dict(red_reference),
            "green_reference": dict(green_reference),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    frame_path = directory / "rg-frame.npz"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{frame_path.name}.", suffix=".npz", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(
            temporary,
            white_frame=np.asarray(white_frame),
            red_plane=np.asarray(red_plane),
            green_plane=np.asarray(green_plane),
            red_source_jpeg8=np.asarray(red_source_jpeg8),
            green_source_jpeg8=np.asarray(green_source_jpeg8),
            red_valid=np.asarray(red_valid),
            green_valid=np.asarray(green_valid),
            metadata_json=np.asarray(metadata),
        )
        temporary.replace(frame_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    calibration: dict[str, Any] = {
        "schema_version": "1.0",
        "calibration_id": calibration_id,
        "geometry": dict(geometry),
        "measurement_policy": dict(measurement_policy),
        "iteration": iteration,
        "total_correction_um": total_correction_um,
    }
    if profile is not None:
        calibration["profile"] = dict(profile)
    if control_settings is not None:
        calibration["control_settings"] = dict(control_settings)
    calibration_path = directory / "rg-calibration.json"
    _atomic_json(calibration_path, calibration)
    return {
        "frame": artifact_descriptor(frame_path, FRAME_MEDIA_TYPE, role="rg-frame"),
        "calibration": artifact_descriptor(
            calibration_path, CALIBRATION_MEDIA_TYPE, role="rg-calibration"
        ),
    }


def write_calibration_series(
    directory: Path, value: Mapping[str, object]
) -> dict[str, object]:
    """Write one immutable calibration-series artifact and return its descriptor."""
    path = directory / "rg-calibration-series.json"
    _atomic_json(path, dict(value))
    return artifact_descriptor(path, SERIES_MEDIA_TYPE, role="rg-calibration-series")


def write_flat_field_input(
    directory: Path, name: str, **arrays: np.ndarray
) -> dict[str, object]:
    """Atomically write one exact flat-field array bundle for the core process."""
    if not name or not arrays or any(not key for key in arrays):
        raise ValueError("Flat-field input name and arrays are required")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.npz"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".npz", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(
            temporary,
            **{key: np.asarray(value) for key, value in arrays.items()},
        )
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return artifact_descriptor(path, FLAT_FIELD_INPUT_MEDIA_TYPE, role=name)


def read_flat_field_result(
    descriptor: object,
    *,
    directory: Path,
    expected_arrays: set[str],
) -> dict[str, np.ndarray]:
    """Verify and load one bounded core-produced flat-field NPZ."""
    if not isinstance(descriptor, Mapping) or set(descriptor) - {
        "uri",
        "sha256",
        "media_type",
        "size_bytes",
        "role",
    }:
        raise ValueError("Core flat-field artifact descriptor is invalid")
    uri = descriptor.get("uri")
    if (
        not isinstance(uri, str)
        or descriptor.get("media_type") != FLAT_FIELD_RESULT_MEDIA_TYPE
        or descriptor.get("role") != "rg-flat-field-result"
    ):
        raise ValueError("Core flat-field artifact media type is invalid")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("Core flat-field artifact must use a local file URI")
    path = Path(unquote(parsed.path)).resolve(strict=True)
    root = directory.resolve(strict=True)
    if root not in path.parents or not path.is_file() or path.is_symlink():
        raise ValueError("Core flat-field artifact is outside its exchange directory")
    if path.stat().st_size > MAXIMUM_FLAT_FIELD_RESULT_BYTES:
        raise ValueError("Core flat-field artifact exceeds its size bound")
    expected_descriptor = artifact_descriptor(
        path,
        FLAT_FIELD_RESULT_MEDIA_TYPE,
        role="rg-flat-field-result",
    )
    if dict(descriptor) != expected_descriptor:
        raise ValueError("Core flat-field artifact identity changed")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if (
                any(member.flag_bits & 0x1 for member in members)
                or sum(member.file_size for member in members)
                > MAXIMUM_FLAT_FIELD_UNCOMPRESSED_BYTES
            ):
                raise ValueError("Core flat-field result archive is unsafe")
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != expected_arrays:
                raise ValueError("Core flat-field result arrays changed")
            return {
                name: np.array(archive[name], copy=True) for name in expected_arrays
            }
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Core flat-field result is not a valid NPZ") from error


def write_tissue_field_input(
    directory: Path, white_frame: np.ndarray
) -> dict[str, object]:
    """Write one exact WHITE frame for external tissue-field preparation."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tissue-field-input.npz"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".npz", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(temporary, white_frame=np.asarray(white_frame))
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return artifact_descriptor(
        path, TISSUE_FIELD_INPUT_MEDIA_TYPE, role="rg-tissue-field-input"
    )


def read_tissue_field_result(  # noqa: C901 - explicit trust-boundary checks
    descriptor: object, *, directory: Path
) -> dict[str, np.ndarray]:
    """Verify and load one core-produced tissue-field array bundle."""
    if not isinstance(descriptor, Mapping) or set(descriptor) - {
        "uri",
        "sha256",
        "media_type",
        "size_bytes",
        "role",
    }:
        raise ValueError("Core tissue-field artifact descriptor is invalid")
    uri = descriptor.get("uri")
    if (
        not isinstance(uri, str)
        or descriptor.get("media_type") != TISSUE_FIELD_RESULT_MEDIA_TYPE
        or descriptor.get("role") != "rg-tissue-field-result"
    ):
        raise ValueError("Core tissue-field artifact media type is invalid")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("Core tissue-field artifact must use a local file URI")
    path = Path(unquote(parsed.path)).resolve(strict=True)
    root = directory.resolve(strict=True)
    if root not in path.parents or not path.is_file() or path.is_symlink():
        raise ValueError("Core tissue-field artifact is outside its exchange directory")
    if path.stat().st_size > MAXIMUM_TISSUE_FIELD_RESULT_BYTES:
        raise ValueError("Core tissue-field artifact exceeds its size bound")
    expected_descriptor = artifact_descriptor(
        path,
        TISSUE_FIELD_RESULT_MEDIA_TYPE,
        role="rg-tissue-field-result",
    )
    if dict(descriptor) != expected_descriptor:
        raise ValueError("Core tissue-field artifact identity changed")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if (
                any(member.flag_bits & 0x1 for member in members)
                or sum(member.file_size for member in members)
                > MAXIMUM_TISSUE_FIELD_UNCOMPRESSED_BYTES
            ):
                raise ValueError("Core tissue-field result archive is unsafe")
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != {"mask", "white_common", "overlay"}:
                raise ValueError("Core tissue-field result arrays changed")
            arrays = {
                name: np.array(archive[name], copy=True)
                for name in ("mask", "white_common", "overlay")
            }
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Core tissue-field result is not a valid NPZ") from error
    mask = arrays["mask"]
    white = arrays["white_common"]
    overlay = arrays["overlay"]
    if (
        mask.dtype != np.bool_
        or white.ndim != 2
        or mask.shape != white.shape
        or overlay.dtype != np.uint8
        or overlay.shape != (*white.shape, 3)
        or not np.isfinite(white).all()
    ):
        raise ValueError("Core tissue-field result geometry or dtype changed")
    for value in arrays.values():
        value.setflags(write=False)
    return arrays


def write_simultaneous_input(
    directory: Path, name: str, **arrays: np.ndarray
) -> dict[str, object]:
    """Write one exact simultaneous R/G array bundle for the core process."""
    if not name or not arrays or any(not key for key in arrays):
        raise ValueError("Simultaneous input name and arrays are required")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.npz"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".npz", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(
            temporary,
            **{key: np.asarray(value) for key, value in arrays.items()},
        )
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return artifact_descriptor(path, SIMULTANEOUS_INPUT_MEDIA_TYPE, role=name)


def read_simultaneous_result(  # noqa: C901 - explicit trust-boundary checks
    descriptor: object,
    *,
    directory: Path,
    expected_arrays: set[str],
) -> dict[str, np.ndarray]:
    """Verify and load one bounded core-produced simultaneous R/G NPZ."""
    if not isinstance(descriptor, Mapping) or set(descriptor) - {
        "uri",
        "sha256",
        "media_type",
        "size_bytes",
        "role",
    }:
        raise ValueError("Core simultaneous artifact descriptor is invalid")
    uri = descriptor.get("uri")
    if (
        not isinstance(uri, str)
        or descriptor.get("media_type") != SIMULTANEOUS_RESULT_MEDIA_TYPE
        or descriptor.get("role") != "rg-simultaneous-result"
    ):
        raise ValueError("Core simultaneous artifact media type is invalid")
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError("Core simultaneous artifact must use a local file URI")
    path = Path(unquote(parsed.path)).resolve(strict=True)
    root = directory.resolve(strict=True)
    if root not in path.parents or not path.is_file() or path.is_symlink():
        raise ValueError("Core simultaneous artifact is outside its exchange directory")
    if path.stat().st_size > MAXIMUM_SIMULTANEOUS_RESULT_BYTES:
        raise ValueError("Core simultaneous artifact exceeds its size bound")
    expected_descriptor = artifact_descriptor(
        path,
        SIMULTANEOUS_RESULT_MEDIA_TYPE,
        role="rg-simultaneous-result",
    )
    if dict(descriptor) != expected_descriptor:
        raise ValueError("Core simultaneous artifact identity changed")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if (
                any(member.flag_bits & 0x1 for member in members)
                or sum(member.file_size for member in members)
                > MAXIMUM_SIMULTANEOUS_UNCOMPRESSED_BYTES
            ):
                raise ValueError("Core simultaneous result archive is unsafe")
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != expected_arrays:
                raise ValueError("Core simultaneous result arrays changed")
            return {
                name: np.array(archive[name], copy=True) for name in expected_arrays
            }
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Core simultaneous result is not a valid NPZ") from error


def write_stitching_request(  # noqa: PLR0913
    directory: Path,
    *,
    output_name: str,
    workers: int,
    cache_bytes: int,
    registration_mode: str,
    minimum_overlap: float,
    correlation_resize: float,
    work_tile_size: int,
    viewer_dzi: bool,
) -> Path:
    """Freeze source tiles and one protocol envelope for cancellable final stitching."""
    media_types = {
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    tiles = sorted(
        path
        for path in directory.glob("img_*.*")
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.lower() in media_types
    )
    if not tiles:
        raise ValueError("No source tiles were found for final stitching")
    manifest_path = directory / "fast-ofm-stitch-manifest.json"
    _atomic_json(
        manifest_path,
        {
            "schema_version": "1.0",
            "tiles": [
                artifact_descriptor(
                    path, media_types[path.suffix.lower()], role="source-tile"
                )
                for path in tiles
            ],
        },
    )
    request_path = directory / "fast-ofm-stitch-request.json"
    _atomic_json(
        request_path,
        {
            "protocol_version": "1.0",
            "request_id": str(uuid.uuid4()),
            "operation": "stitching.run",
            "timeout_ms": 3_600_000,
            "payload": {
                "tile_manifest": artifact_descriptor(
                    manifest_path,
                    STITCH_MANIFEST_MEDIA_TYPE,
                    role="tile-manifest",
                ),
                "output_name": output_name,
                "workers": workers,
                "cache_bytes": cache_bytes,
                "registration_mode": registration_mode,
                "pyramid": True,
                "viewer_dzi": viewer_dzi,
                "minimum_overlap": minimum_overlap,
                "correlation_resize": correlation_resize,
                "work_tile_size": work_tile_size,
                "maximum_runtime_s": 21_600,
            },
        },
    )
    return request_path
