"""Processed JPEG geometry and encoding, independent of any camera owner.

Image/plane coordinates name RGB8 pixel centres after ISP and JPEG decoding.
These are not linear sensor samples and have no Bayer offsets or RAW fields.
"""

from __future__ import annotations

import io
import json
import math
import time
from typing import Any

import numpy as np
from PIL import Image, features
from PIL import __version__ as pillow_version
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class JPEGEncoding(BaseModel):
    """Explicit immutable JPEG parameters for acquisition and profile binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    quality: StrictInt = Field(default=95, ge=1, le=95)
    subsampling: StrictInt = Field(default=0, ge=0, le=2)

    def binding(self) -> dict:
        """Return encoder identity as well as the selected numeric parameters."""
        return {
            "format": "jpeg",
            **self.model_dump(),
            "encoder": "Pillow",
            "pillow_version": pillow_version,
            "libjpeg_version": features.version_codec("jpg"),
        }


def jpeg_geometry(configuration: dict, crop: list | tuple) -> dict:
    """Describe the verified unbinned HQ RGB field, without inventing Bayer data."""
    main = configuration["main"]
    sensor = configuration["sensor"]
    size = main["size"]
    if (
        list(sensor["output_size"]) != [4056, 3040]
        or sensor["bit_depth"] != 12
        or str(configuration.get("transform", "")) != "<libcamera.Transform 'identity'>"
        or main.get("format") != "XBGR8888"
    ):
        raise ValueError("JPEG measurement requires identity full-sensor HQ XBGR8888")
    if (
        len(crop) != 4
        or any(type(value) is not int or value < 0 or value % 2 for value in crop)
        or min(crop[2:]) <= 0
        or crop[0] + crop[2] > 4056
        or crop[1] + crop[3] > 3040
        or len(size) != 2
        or any(type(value) is not int or value <= 0 or value % 2 for value in size)
        or size[0] > crop[2]
        or size[1] > crop[3]
        or size[0] * crop[3] != size[1] * crop[2]
    ):
        raise ValueError(
            "JPEG geometry must be even, in bounds, and preserve ROI aspect"
        )
    width, height = size
    affine = [
        [crop[2] / width, 0, crop[0] + crop[2] / width / 2 - 0.5],
        [0, crop[3] / height, crop[1] + crop[3] / height / 2 - 0.5],
    ]
    return {
        "measurement_space": "processed-jpeg-rgb8",
        "sensor": "imx477",
        "sensor_resolution": [4056, 3040],
        "sensor_crop": list(crop),
        "image_size": list(size),
        "plane_size": list(size),
        "white_size": list(size),
        "bit_depth": 8,
        "white_level": 255,
        "channel_order": ["R", "G", "B"],
        "array_axes": "height,width,channel",
        "pixel_to_sensor": affine,
        "white_to_sensor": [row[:] for row in affine],
        "common_plane_to_sensor": [row[:] for row in affine],
    }


def jpeg_isp_configuration(configuration: dict) -> dict:
    """Freeze ISP controls/colour space, excluding irrelevant RAW buffer storage."""
    controls = dict(configuration.get("controls", {}))
    if controls.get("AeEnable") is not False or controls.get("AwbEnable") is not False:
        raise ValueError("JPEG measurement requires fixed manual AE/AWB controls")
    if "NoiseReductionMode" in controls:
        controls["NoiseReductionMode"] = int(controls["NoiseReductionMode"])
    return json.loads(
        json.dumps(
            {
                "controls": controls,
                "colour_space": str(configuration.get("colour_space", "")),
                "transform": str(configuration.get("transform", "")),
                "main_format": configuration["main"]["format"],
            },
            allow_nan=False,
        )
    )


def jpeg_processing(metadata: dict, binding: dict) -> dict:
    """Validate fixed controls and retain actual per-request ISP processing values."""
    digital_gain: Any = metadata.get("DigitalGain")
    gains = np.asarray(metadata.get("ColourGains"), dtype=float)
    matrix = np.asarray(metadata.get("ColourCorrectionMatrix"), dtype=float)
    if (
        type(digital_gain) not in (int, float)
        or not math.isfinite(digital_gain)
        or digital_gain <= 0
        or gains.shape != (2,)
        or not np.isfinite(gains).all()
        or not np.allclose(gains, binding["colour_gains"], rtol=1e-5, atol=0)
        or matrix.shape != (9,)
        or not np.isfinite(matrix).all()
        or metadata.get("AeEnable", False) is not False
        or metadata.get("AwbEnable", False) is not False
    ):
        raise ValueError(
            "JPEG processing metadata does not match fixed manual controls"
        )
    configured_matrix = binding["isp"]["controls"].get("ColourCorrectionMatrix")
    if configured_matrix is not None and not np.allclose(
        matrix, configured_matrix, rtol=1e-5, atol=0
    ):
        raise ValueError("JPEG colour matrix does not match fixed ISP controls")
    return {
        "digital_gain": float(digital_gain),
        "colour_gains": gains.tolist(),
        "colour_correction_matrix": matrix.tolist(),
        "colour_temperature": metadata.get("ColourTemperature"),
        "ae_enabled": False,
        "awb_enabled": False,
    }


def encode_decode_jpeg(
    source: Image.Image, encoding: JPEGEncoding
) -> tuple[bytes, np.ndarray, dict]:
    """Encode once and return an owned RGB8 array decoded from those exact bytes."""
    if source.mode not in ("RGB", "RGBX", "RGBA"):
        raise ValueError(
            "JPEG source requires an RGB8 main image, without channel guessing"
        )
    started = time.monotonic()
    with io.BytesIO() as output:
        source.convert("RGB").save(output, format="JPEG", **encoding.model_dump())
        payload = output.getvalue()
    encoded = time.monotonic()
    with Image.open(io.BytesIO(payload)) as image:
        if image.format != "JPEG" or image.mode != "RGB" or image.size != source.size:
            raise ValueError("Encoded JPEG changed RGB mode or image dimensions")
        rgb = np.array(image, dtype=np.uint8, copy=True)
    return (
        payload,
        rgb,
        {"encode_s": encoded - started, "decode_s": time.monotonic() - encoded},
    )
