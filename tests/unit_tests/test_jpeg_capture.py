"""One same-request processed JPEG measurement, without hardware or RAW decoding."""

import copy
import hashlib
import io
import json
import math
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from labthings_fastapi.exceptions import InvocationCancelledError
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.acquisition.jpeg_capture import (
    JPEGEncoding,
    jpeg_geometry,
)


@pytest.fixture
def jpeg_request(mock_picam_thing, mocker):
    """Keep the actual owner/lock/binding path and fake only Picamera2 IO."""
    from openflexure_microscope_server.things.camera import picamera

    assert mock_picam_thing is not None
    owner = create_thing_without_server(picamera.PiCameraHQ)
    owner.stream_active = True
    owner._exposure_time = 100
    owner._analogue_gain = 1.0
    owner._colour_gains = (1.875, 1.482)
    config = {
        "main": {"size": (1014, 760), "format": "XBGR8888"},
        "lores": {"size": (1014, 760), "format": "YUV420"},
        "sensor": {"output_size": (4056, 3040), "bit_depth": 12},
        "transform": "<libcamera.Transform 'identity'>",
        "colour_space": "<libcamera.ColorSpace 'sYCC'>",
        "controls": {
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": 101,
            "AnalogueGain": 1.0,
            "ColourGains": (1.875, 1.482),
            "Brightness": 0,
            "Contrast": 1,
            "Saturation": 1,
            "Sharpness": 1,
            "ScalerCrop": (0, 0, 4056, 3040),
        },
    }
    metadata = {
        "SensorTimestamp": 1_200_000,
        "ExposureTime": 100,
        "AnalogueGain": 1.0,
        "DigitalGain": 1.0000462532,
        "ColourGains": [1.875, 1.482],
        "ColourCorrectionMatrix": [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0],
        "ColourTemperature": 4500,
        "FrameDuration": 85335,
        "ScalerCrop": [0, 0, 4056, 3040],
    }
    cam = mocker.Mock(started=True, camera_properties={"Model": "imx477"})
    cam.camera_configuration.side_effect = lambda: copy.deepcopy(config)
    owner._picamera = cam
    request = mocker.Mock(config=copy.deepcopy(config))
    request.get_metadata.side_effect = lambda: copy.deepcopy(metadata)
    source = np.random.default_rng(78).integers(0, 256, (760, 1014, 4), dtype=np.uint8)
    source[:, :20, :3] = (220, 30, 70)
    source[:, :20, 3] = 0  # RGBX/RGBA alpha is discarded, never composited.
    request.make_image.return_value = Image.fromarray(source, "RGBA")

    def dispatch(**kwargs):
        kwargs["signal_function"](SimpleNamespace(get_result=lambda: request))

    cam.capture_request.side_effect = dispatch
    cam.capture_metadata.side_effect = AssertionError(
        "No capture through a setting getter"
    )
    save_settings = mocker.patch.object(owner, "save_settings")
    raw_decode = mocker.patch.object(
        picamera, "decode_hq_raw", side_effect=AssertionError("No RAW decode")
    )
    mocker.patch.object(picamera.time, "monotonic_ns", return_value=1_000_000)
    return SimpleNamespace(
        owner=owner,
        cam=cam,
        request=request,
        metadata=metadata,
        config=config,
        source=source,
        save_settings=save_settings,
        raw_decode=raw_decode,
        module=picamera,
    )


def assert_no_hardware_mutations(context):
    """Acquisition borrows one current mode without reconfiguration or extra captures."""
    context.cam.configure.assert_not_called()
    context.cam.set_controls.assert_not_called()
    context.cam.stop.assert_not_called()
    context.cam.start.assert_not_called()
    context.cam.capture_metadata.assert_not_called()
    context.request.make_array.assert_not_called()
    context.raw_decode.assert_not_called()
    context.save_settings.assert_not_called()


def test_jpeg_bytes_and_owned_rgb_are_the_same_measurement(jpeg_request):
    """Saved bytes reproduce the RGB exactly; the pre-JPEG image is not returned."""
    context = jpeg_request
    payload, rgb, info = context.owner.capture_jpeg_frame(1.0)
    assert_no_hardware_mutations(context)
    context.request.release.assert_called_once()
    context.request.make_image.assert_called_once_with("main")
    context.request.get_metadata.assert_called_once()
    context.cam.capture_request.assert_called_once()
    assert context.cam.capture_request.call_args.kwargs["wait"] is False
    assert context.cam.capture_request.call_args.kwargs["flush"] == 1_000_000
    assert rgb.dtype == np.uint8
    assert rgb.shape == (760, 1014, 3)
    assert rgb.flags.owndata
    with Image.open(io.BytesIO(payload)) as decoded:
        np.testing.assert_array_equal(rgb, np.asarray(decoded))
        assert decoded.layer == [(1, 1, 1, 0), (2, 1, 1, 1), (3, 1, 1, 1)]
    assert not np.array_equal(rgb, context.source[:, :, :3])
    np.testing.assert_allclose(rgb[100, 10], [220, 30, 70], atol=2)
    assert info["request_metadata"] == context.metadata
    assert info["processing"]["digital_gain"] == 1.0000462532
    assert info["jpeg_sha256"] == hashlib.sha256(payload).hexdigest()
    assert info["jpeg_size_bytes"] == len(payload)
    assert info["geometry"] == info["binding"]["geometry"]
    assert info["geometry"]["channel_order"] == ["R", "G", "B"]
    assert info["binding"]["encoding"]["quality"] == 95
    assert info["binding"]["encoding"]["subsampling"] == 0
    assert all(
        math.isfinite(value) and value >= 0 for value in info["timings"].values()
    )
    assert set(info["timings"]) == {"encode_s", "decode_s"}
    assert json.loads(json.dumps(info)) == info
    assert "bayer_order" not in info["geometry"]
    assert "channel_offsets_xy" not in info["geometry"]
    context.source[:] = 0
    assert rgb.any()


def test_jpeg_geometry_has_pixel_centres_not_bayer_offsets(jpeg_request):
    """Rectangular [width,height] dimensions and pixel-centre affine are explicit."""
    geometry = jpeg_geometry(jpeg_request.config, [0, 0, 4056, 3040])
    for name in ("image_size", "plane_size", "white_size"):
        assert geometry[name] == [1014, 760]
    assert geometry["bit_depth"] == 8
    assert geometry["white_level"] == 255
    assert geometry["pixel_to_sensor"] == [[4, 0, 1.5], [0, 4, 1.5]]
    assert (
        geometry["white_to_sensor"]
        == geometry["common_plane_to_sensor"]
        == geometry["pixel_to_sensor"]
    )
    config = copy.deepcopy(jpeg_request.config)
    config["main"]["size"] = [1000, 750]
    geometry = jpeg_geometry(config, [28, 20, 4000, 3000])
    assert geometry["pixel_to_sensor"] == [[4, 0, 29.5], [0, 4, 21.5]]
    np.testing.assert_allclose(
        np.asarray(geometry["pixel_to_sensor"]) @ [999, 749, 1], [4025.5, 3017.5]
    )


@pytest.mark.parametrize(
    "encoding",
    [
        {"quality": 0},
        {"quality": 96},
        {"quality": True},
        {"subsampling": 3},
        {"subsampling": False},
    ],
)
def test_invalid_jpeg_settings_are_rejected(encoding):
    """Codec changes must be explicit valid integer settings, not coercions."""
    with pytest.raises(ValidationError):
        JPEGEncoding.model_validate(encoding)


def test_changed_jpeg_parameters_are_bound(jpeg_request):
    """A constructor-selected codec changes the binding and the actual JPEG encoding."""
    context = jpeg_request
    context.owner._jpeg_encoding = JPEGEncoding(quality=80, subsampling=2)
    payload, _rgb, info = context.owner.capture_jpeg_frame(1)
    assert info["binding"]["encoding"]["quality"] == 80
    assert info["binding"]["encoding"]["subsampling"] == 2
    with Image.open(io.BytesIO(payload)) as decoded:
        assert decoded.layer[0][1:3] == (2, 2)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SensorTimestamp", 800_000),
        ("ExposureTime", 101),
        ("AnalogueGain", 1.2),
        ("ColourGains", [1.0, 1.0]),
        ("DigitalGain", float("nan")),
        ("ColourCorrectionMatrix", [0.0] * 8),
        ("AeEnable", True),
        ("AwbEnable", True),
        ("ScalerCrop", [2, 0, 4054, 3040]),
    ],
)
def test_bad_same_request_metadata_releases(jpeg_request, key, value):
    """Invalid exposures, processing, or crop never become an accepted measurement."""
    context = jpeg_request
    context.metadata[key] = value
    with pytest.raises(ValueError, match="JPEG|exposure"):
        context.owner.capture_jpeg_frame(1)
    context.request.release.assert_called_once()
    assert_no_hardware_mutations(context)


@pytest.mark.parametrize(
    "defect",
    ["format", "transform", "size", "sensor", "isp", "image", "mode", "encode"],
)
def test_bad_request_or_encoding_releases_without_reconfiguration(
    jpeg_request, mocker, defect
):
    """The same-request config and final owner identity are checked around encoding."""
    context = jpeg_request
    if defect == "format":
        context.request.config["main"]["format"] = "XRGB8888"
    elif defect == "transform":
        context.request.config["transform"] = "hflip"
    elif defect == "size":
        context.request.config["main"]["size"] = [2028, 1520]
    elif defect == "sensor":
        context.request.config["sensor"]["output_size"] = [2028, 1520]
    elif defect == "isp":
        context.request.config["controls"]["Contrast"] = 2
    elif defect == "image":
        context.request.make_image.return_value = Image.new("RGB", (760, 1014))
    elif defect == "mode":
        context.request.make_image.return_value = Image.new("L", (1014, 760))
    else:
        mocker.patch.object(
            Image.Image, "save", side_effect=OSError("JPEG encoder failed")
        )
    with pytest.raises((ValueError, OSError)):
        context.owner.capture_jpeg_frame(1)
    context.request.release.assert_called_once()
    assert_no_hardware_mutations(context)


@pytest.mark.parametrize("defect", ["settings", "tuning", "codec"])
def test_changed_owner_identity_cannot_accept_a_frame(jpeg_request, mocker, defect):
    """A concurrent manual setting/tuning/codec change cannot escape final binding checks."""
    context = jpeg_request
    original = context.module.encode_decode_jpeg

    def change_during_encoding(*args):
        if defect == "settings":
            context.owner._exposure_time = 101
        elif defect == "codec":
            context.owner._jpeg_encoding = JPEGEncoding(quality=80)
        else:
            context.owner.tuning["changed_test_identity"] = True
        return original(*args)

    mocker.patch.object(
        context.module, "encode_decode_jpeg", side_effect=change_during_encoding
    )
    with pytest.raises(ValueError, match="configuration changed"):
        context.owner.capture_jpeg_frame(1)
    context.request.release.assert_called_once()
    assert_no_hardware_mutations(context)


@pytest.mark.parametrize("defect", ["stopped", "auto", "model", "dimensions"])
def test_invalid_current_configuration_does_not_dispatch(jpeg_request, defect):
    """Preflight uses config/private controls, not captures or settings getters."""
    context = jpeg_request
    if defect == "stopped":
        context.cam.started = False
    elif defect == "auto":
        context.config["controls"]["AeEnable"] = True
    elif defect == "model":
        context.cam.camera_properties["Model"] = "imx219"
    else:
        context.config["main"]["size"] = [2028, 1520]
    with pytest.raises(ValueError, match="JPEG"):
        context.owner.capture_jpeg_frame(1)
    context.cam.capture_request.assert_not_called()
    assert_no_hardware_mutations(context)


@pytest.mark.parametrize(
    "defect", ["timeout", "cancel_wait", "cancel_complete", "cancel_encoded"]
)
def test_timeout_and_cancel_keep_exactly_once_ownership(jpeg_request, mocker, defect):
    """Outstanding jobs block both paths; cancellation/late callbacks release once."""
    context = jpeg_request
    if defect in ("timeout", "cancel_wait"):
        context.cam.capture_request.side_effect = None
    if defect in ("cancel_wait", "cancel_complete"):
        mocker.patch.object(
            context.module.lt,
            "raise_if_cancelled",
            side_effect=[None, InvocationCancelledError()],
        )
    elif defect == "cancel_encoded":
        mocker.patch.object(
            context.module.lt,
            "raise_if_cancelled",
            side_effect=[None, None, InvocationCancelledError()],
        )
    with pytest.raises((TimeoutError, InvocationCancelledError)):
        context.owner.capture_jpeg_frame(0.001)
    if defect in ("timeout", "cancel_wait"):
        with pytest.raises(RuntimeError, match="still pending"):
            context.owner.capture_jpeg_frame(0.001)
        mocker.patch.object(
            type(context.owner),
            "raw_measurement_configuration",
            new=property(lambda _self: {}),
        )
        with pytest.raises(RuntimeError, match="still pending"):
            context.owner.capture_linear_frame(0.001)
        assert context.cam.capture_request.call_count == 1
        context.request.release.assert_not_called()
        callback = context.cam.capture_request.call_args.kwargs["signal_function"]
        callback(SimpleNamespace(get_result=lambda: context.request))
    context.request.release.assert_called_once()
    assert_no_hardware_mutations(context)
