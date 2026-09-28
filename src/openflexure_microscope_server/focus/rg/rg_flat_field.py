"""Empirical processed-JPEG R/G flat-field; no camera, motion or AI dependencies."""

from __future__ import annotations

import copy
from typing import Literal, Self

import numpy as np
from pydantic import Field, StrictInt, field_validator, model_validator

from ...fast_ofm_contracts import StrictModel

Mode = Literal["red", "green"]
JPEG_DOMAIN = "processed-jpeg-rgb8"


class FlatFieldSettings(StrictModel):
    """One saved parameter set for acquisition and independent holdout checks."""

    measurement_domain: Literal["legacy-raw", "processed-jpeg-rgb8"] = "legacy-raw"
    processing_roi: tuple[StrictInt, StrictInt, StrictInt, StrictInt] | None = None

    dark_frames: int = Field(default=4, ge=2, le=16)
    average_frames: int = Field(
        default=4, ge=2, le=64, description="Phase-matched fit cycles"
    )
    validation_frames: int = Field(
        default=3, ge=2, le=16, description="Independent phase-matched cycles"
    )
    frame_timeout_s: float = Field(default=5, gt=0, le=10)
    timeout_s: float = Field(default=300, gt=0, le=600)
    smoothing_sigma_px: float = Field(default=1, ge=1, le=64)
    minimum_signal_dn: float = Field(default=64, ge=8, le=1024)
    maximum_gain: float = Field(default=4, ge=1, le=10)
    maximum_mask_fraction: float = Field(default=0.02, ge=0, le=0.1)
    maximum_saturation_fraction: float = Field(default=0.001, ge=0, le=0.05)
    saturation_level_fraction: float = Field(default=0.98, ge=0.9, le=1)
    maximum_dark_signal_dn: float = Field(default=256, ge=1, le=1024)
    maximum_field_texture: float = Field(default=0.04, gt=0, le=0.2)
    maximum_residual_cv: float = Field(default=0.05, gt=0, le=0.2)
    maximum_drift_fraction: float = Field(default=0.05, gt=0, le=0.2)
    maximum_spatial_residual_fraction: float = Field(default=0.05, gt=0, le=0.2)
    optics_id: str = Field(default="current", min_length=1, max_length=100)
    illumination_id: str = Field(default="current", min_length=1, max_length=100)

    @field_validator("processing_roi", mode="before")
    @classmethod
    def json_roi(cls, value: object) -> object:
        """Accept the JSON array container while retaining strict integer coordinates."""
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_domain(self) -> Self:
        """Load historical DN values safely but reject invalid explicit JPEG settings."""
        if self.measurement_domain == JPEG_DOMAIN and (
            self.minimum_signal_dn > 255 or self.maximum_dark_signal_dn > 255
        ):
            raise ValueError("JPEG thresholds are absolute 8-bit DN, at most 255")
        if self.processing_roi is not None:
            x, y, width, height = self.processing_roi
            if min(x, y) < 0 or min(width, height) < 16:
                raise ValueError(
                    "Processing ROI needs nonnegative origin and at least 16x16 pixels"
                )
        return self

    def require_jpeg(self) -> None:
        """Require a deliberate JPEG preset/settings save before acquisition or fitting."""
        if self.measurement_domain != JPEG_DOMAIN:
            raise ValueError("Save the explicit JPEG8 preset before R/G acquisition")
        if self.processing_roi is None:
            raise ValueError("Select and save an explicit common R/G processing ROI")


def processing_binding(configuration: dict, settings: FlatFieldSettings) -> dict:
    """Bind one common JPEG ROI and translate pixel centres without changing sensor FOV."""
    settings.require_jpeg()
    if configuration.get("measurement_space") != JPEG_DOMAIN:
        raise ValueError(
            "Camera does not provide the processed JPEG measurement domain"
        )
    result = copy.deepcopy(configuration)
    geometry = result["geometry"]
    width, height = geometry["image_size"]
    roi = settings.processing_roi
    if roi is None:
        raise ValueError("An explicit processing ROI is required")
    x, y, w, h = roi
    if x + w > width or y + h > height:
        raise ValueError("Processing ROI exceeds the decoded JPEG dimensions")
    result["source_geometry"] = copy.deepcopy(geometry)
    result["processing_roi"] = list(roi)
    geometry.update(
        source_image_size=[width, height],
        processing_roi=list(roi),
        image_size=[w, h],
        plane_size=[w, h],
        white_size=[w, h],
    )
    for key in ("pixel_to_sensor", "white_to_sensor", "common_plane_to_sensor"):
        affine = np.asarray(geometry[key], dtype=float)
        if affine.shape != (2, 3) or not np.isfinite(affine).all():
            raise ValueError("Invalid JPEG pixel-centre geometry")
        affine[:, 2] += affine[:, :2] @ np.array([x, y])
        geometry[key] = affine.tolist()
    return result


def check_processing(processing: dict, reference: dict | None = None) -> None:
    """Validate actual processing against one fixed reference (relative tolerance 1e-5)."""
    for key, shape in (
        ("digital_gain", ()),
        ("colour_gains", (2,)),
        ("colour_correction_matrix", (9,)),
    ):
        values = np.asarray(processing.get(key), dtype=float)
        if (
            values.shape != shape
            or not np.isfinite(values).all()
            or (key != "colour_correction_matrix" and (values <= 0).any())
        ):
            raise ValueError("Invalid actual JPEG processing metadata")
        if reference is not None:
            expected = np.asarray(reference.get(key), dtype=float)
            if (
                expected.shape != shape
                or not np.isfinite(expected).all()
                or not np.allclose(values, expected, rtol=1e-5, atol=0)
            ):
                raise ValueError("Digital gain/white balance/colour processing changed")


def channel_indices(mode: Mode) -> list[int]:
    """Select decoded RGB red or green, on the same pixel grid."""
    if mode not in ("red", "green"):
        raise ValueError("Flat-field mode must be red or green")
    return [0] if mode == "red" else [1]


def common_measurement_plane(
    corrected: np.ndarray, valid: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return the selected same-grid JPEG plane without Bayer shifts or resampling."""
    if corrected.shape != valid.shape or corrected.ndim != 3 or len(corrected) != 1:
        raise ValueError("Expected one common JPEG plane and its mask")
    return corrected[0].copy(), valid[0].copy()
