"""Numerical RAW/flat-field contracts, including known geometric displacements."""

from concurrent.futures import Future
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from openflexure_microscope_server.acquisition.raw_capture import (
    PendingRequest,
    decode_hq_raw,
    exposure_metadata,
    hq_geometry,
)
from openflexure_microscope_server.focus.rg.rg_flat_field import (
    JPEG_DOMAIN,
    FlatFieldSettings,
    channel_indices,
    check_processing,
    common_measurement_plane,
    processing_binding,
)
from tests.unit_tests.flat_field_process_stub import (
    apply_native_flat_field,
    fit_flat_field,
    validate_flat_field,
)


def test_packed_raw12_low_nibbles_stride_crop_and_bayer_order():
    """Packing, row padding and Bayer offsets cannot silently change sensor levels."""
    values = np.array(
        [
            [0x123, 0xABC, 0xFFF, 0],
            [100, 200, 300, 400],
            [500, 600, 700, 800],
            [900, 1000, 1100, 1200],
        ],
        dtype=np.uint16,
    )
    data = np.full((4, 8), 255, dtype=np.uint8)
    data[:, :6:3] = values[:, ::2] >> 4
    data[:, 1:6:3] = values[:, 1::2] >> 4
    data[:, 2:6:3] = (values[:, ::2] & 15) | ((values[:, 1::2] & 15) << 4)
    geometry = {
        "raw_size": [4, 4],
        "raw_stride": 8,
        "sensor_crop": [0, 0, 4, 4],
        "channel_offsets_xy": [[0, 0], [1, 0], [0, 1], [1, 1]],
    }
    planes = decode_hq_raw(data, geometry)
    for plane, (x, y) in zip(planes, geometry["channel_offsets_xy"], strict=True):
        np.testing.assert_array_equal(plane, values[y::2, x::2])
    assert planes.flags.owndata
    with pytest.raises(ValueError, match="stride"):
        decode_hq_raw(data[:, :6], geometry)


def test_fixed_hq_geometry_is_explicit_and_rejects_guessing():
    """WHITE-to-sensor geometry and Bayer lattice offsets are recorded."""
    config = {
        "raw": {"format": "SBGGR12_CSI2P", "size": [4056, 3040], "stride": 6112},
        "sensor": {"output_size": [4056, 3040], "bit_depth": 12},
        "main": {"size": [700, 700]},
        "transform": "<libcamera.Transform 'identity'>",
    }
    geometry = hq_geometry(config, [628, 120, 2800, 2800])
    assert geometry["plane_size"] == [1400, 1400]
    assert geometry["white_to_sensor"][0] == [4, 0, 629.5]
    assert geometry["common_plane_to_sensor"][0] == [2, 0, 628.5]
    with pytest.raises(ValueError, match="even"):
        hq_geometry(config, [629, 120, 2800, 2800])
    config["raw"]["format"] = "SBGGR16"
    with pytest.raises(ValueError, match="RAW12"):
        hq_geometry(config, [0, 0, 64, 64])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("SensorTimestamp", 90_000),
        ("ExposureTime", 0),
        ("ExposureTime", 1000),
        ("AnalogueGain", float("nan")),
        ("AnalogueGain", True),
        ("FrameDuration", 0),
    ],
)
def test_invalid_or_transitional_exposure_rejected(field, value):
    """A success response or a new readout timestamp alone does not imply a fresh exposure."""
    metadata = {
        "SensorTimestamp": 200_000,
        "ExposureTime": 50,
        "AnalogueGain": 1.0,
        "FrameDuration": 100,
    }
    metadata[field] = value
    with pytest.raises(
        ValueError,
        match="RAW|exposure|Flat-field|flat-field|ALL_OFF|Field|Too much|Illumination|Independent|Incompatible|geometry",
    ):
        exposure_metadata(metadata, 100_000)


def test_successful_request_handoff_and_late_timeout_release(mocker):
    """Never leak the request that arrives after Picamera2's wait timeout."""
    request = mocker.Mock()
    pending = PendingRequest()
    with pytest.raises(TimeoutError):
        pending.take(0.001, lambda: None)
    pending.complete(SimpleNamespace(get_result=lambda: request))
    request.release.assert_called_once()
    assert pending.finished
    pending = PendingRequest()
    pending.complete(SimpleNamespace(get_result=lambda: request))
    assert pending.take(1, lambda: None) is request


def test_cancellation_after_callback_releases_request(mocker):
    """Cancellation racing with completion cannot strand the newly received buffer."""
    request = mocker.Mock()
    pending = PendingRequest()
    pending.complete(SimpleNamespace(get_result=lambda: request))

    def cancelled():
        raise RuntimeError("cancel")

    with pytest.raises(RuntimeError, match="cancel"):
        pending.take(1, cancelled)
    request.release.assert_called_once()


def test_callback_error_is_propagated_without_throwing_in_camera_thread():
    """The event-loop callback remains safe even when the capture job failed."""
    future = Future()
    future.set_exception(ValueError("camera"))
    pending = PendingRequest()
    pending.complete(SimpleNamespace(get_result=future.result))
    with pytest.raises(ValueError, match="camera"):
        pending.take(1, lambda: None)


@pytest.fixture
def field():
    """Smooth JPEG illumination and a measured dark on the same decoded grid."""
    yy, xx = np.mgrid[:96, :96]
    shading = 90 + 70 * np.exp(-((xx - 48) ** 2 + (yy - 48) ** 2) / 4000)
    dark = np.full((1, 96, 96), 5, dtype=np.float32)
    flat = dark + shading[None].astype(np.float32)
    settings = FlatFieldSettings(
        measurement_domain=JPEG_DOMAIN,
        processing_roi=(0, 0, 96, 96),
        minimum_signal_dn=8,
        maximum_dark_signal_dn=32,
        smoothing_sigma_px=2.0,
    )
    return flat, dark, settings


def test_flat_field_improves_unused_data_and_does_not_subtract_black_twice(field):
    """Known multiplicative shading is corrected; an unchanged scene remains aligned."""
    flat, dark, settings = field
    maps, fit = fit_flat_field(flat, dark, settings)
    heldout = flat + np.random.default_rng(4).normal(0, 0.1, flat.shape)
    report = validate_flat_field(heldout, maps, fit, settings)
    assert max(report["after"]["cv"]) < 0.005
    assert min(report["before"]["cv"]) > 0.05
    corrected, mask = apply_native_flat_field(flat, maps)
    assert mask.all()
    assert np.median(corrected) == pytest.approx(np.median(flat - dark), rel=0.01)
    assert fit["black_source"] == "measured_all_off_same_exposure"


@pytest.mark.parametrize("defect", ["weak", "saturated", "dark", "texture", "mask"])
def test_bad_flat_field_is_not_promoted(field, defect):
    """QC does not turn a poor field into a valid map merely by clipping its gain."""
    flat, dark, settings = field
    if defect == "weak":
        flat = dark + 1
    elif defect == "saturated":
        flat[:, :8] = 255
    elif defect == "dark":
        dark[:] = 90
    elif defect == "texture":
        flat[:, ::2] -= 50
    else:
        flat[:, :20] = dark[:, :20]
    with pytest.raises(
        ValueError,
        match="RAW|exposure|Flat-field|flat-field|ALL_OFF|Field|Too much|Illumination|Independent|Incompatible|geometry",
    ):
        fit_flat_field(flat, dark, settings)


@pytest.mark.parametrize("defect", ["weak", "shape", "nan", "saturated", "residual"])
def test_independent_validation_rejects_changed_or_invalid_frames(field, defect):
    """The heldout check is real: unseen bad data cannot pass using the fit report."""
    flat, dark, settings = field
    maps, fit = fit_flat_field(flat, dark, settings)
    heldout = flat.copy()
    if defect == "weak":
        heldout = dark + (flat - dark) * 0.01
    elif defect == "shape":
        heldout = heldout[:, :-1]
    elif defect == "nan":
        heldout[0, 0, 0] = np.nan
    elif defect == "saturated":
        heldout[:, :8] = 255
    else:
        heldout[:, :, :48] -= 25
    with pytest.raises(
        ValueError,
        match="RAW|exposure|Flat-field|flat-field|ALL_OFF|Field|Too much|Illumination|Independent|Incompatible|geometry",
    ):
        validate_flat_field(heldout, maps, fit, settings)


def test_jpeg_channels_share_exact_grid_without_half_pixel_remapping():
    """Decoded channels already share one pixel grid; preserve values and mask."""
    yy, xx = np.mgrid[:32, :32]

    def scene(x, y):
        return 100 + 2 * x + 3 * y

    planes = scene(xx, yy)[None].astype(np.float32)
    mask = np.ones_like(planes, bool)
    mask[0, 4, 6] = False
    output, valid = common_measurement_plane(planes, mask)
    np.testing.assert_array_equal(output, planes[0])
    np.testing.assert_array_equal(valid, mask[0])
    assert output.flags.owndata
    assert valid.flags.owndata


def test_flat_field_preserves_known_translated_scene(field):
    """Correction removes shading, not the spatial shift needed by R/G focus."""
    flat, dark, settings = field
    maps, _fit = fit_flat_field(flat, dark, settings)
    yy, xx = np.mgrid[:96, :96]
    scene = 0.4 + 0.3 * np.exp(-((xx - 40) ** 2 + (yy - 50) ** 2) / 100)
    first, _ = apply_native_flat_field(dark + (flat - dark) * scene, maps)
    second, _ = apply_native_flat_field(
        dark + (flat - dark) * np.roll(scene, 7, axis=1), maps
    )
    assert (
        np.unravel_index(np.argmax(second[0]), (96, 96))[1]
        - np.unravel_index(np.argmax(first[0]), (96, 96))[1]
        == 7
    )


def test_parameters_and_mode_are_validated():
    """No nonfinite settings or wrong channel fallbacks."""
    for kwargs in (
        {"average_frames": 1},
        {"minimum_signal_dn": float("nan")},
        {"unknown": 1},
    ):
        with pytest.raises(ValidationError):
            FlatFieldSettings(**kwargs)
    assert channel_indices("red") == [0]
    assert channel_indices("green") == [1]
    with pytest.raises(
        ValueError,
        match="RAW|exposure|Flat-field|flat-field|ALL_OFF|Field|Too much|Illumination|Independent|Incompatible|geometry",
    ):
        channel_indices("white")


def test_small_dust_patch_is_masked_instead_of_becoming_a_gain_feature(field):
    """Sparse dark dust is invalid support; its fixed edge must not drive R/G correlation."""
    flat, dark, settings = field
    flat[0, 43:46, 43:46] -= 50
    maps, report = fit_flat_field(flat, dark, settings)
    assert not maps["valid"][0, 43:46, 43:46].any()
    assert 0 < report["mask_fraction"][0] < settings.maximum_mask_fraction


def test_default_map_preserves_fine_stable_illumination_without_loosening_qc():
    """Native flat-field must retain stable illumination finer than a 16 px blur."""
    yy, xx = np.mgrid[:192, :192]
    signal = 48 * (1 + 0.08 * np.sin(xx / 3) + 0.08 * np.cos(yy / 3))
    dark = np.full((1, 192, 192), 5, dtype=np.float32)
    rng = np.random.default_rng(24)
    flat = dark + signal[None] + rng.normal(0, 0.2, dark.shape)
    settings = FlatFieldSettings(
        measurement_domain=JPEG_DOMAIN,
        processing_roi=(0, 0, 192, 192),
        minimum_signal_dn=8,
        maximum_dark_signal_dn=32,
    )
    with pytest.raises(ValueError, match="Too much weak/invalid"):
        fit_flat_field(
            flat, dark, settings.model_copy(update={"smoothing_sigma_px": 16})
        )
    maps, fit = fit_flat_field(flat, dark, settings)
    heldout = dark + signal[None] + rng.normal(0, 0.4, dark.shape)
    quality = validate_flat_field(heldout, maps, fit, settings)
    assert max(quality["after"]["cv"]) < 0.02
    assert max(fit["mask_fraction"]) < 0.02
    assert settings.maximum_residual_cv == 0.05


def test_scalar_brightness_change_is_reported_but_does_not_change_a_flat_field(field):
    """R/G shape correction is scale-invariant; it does not claim absolute photometry."""
    flat, dark, settings = field
    maps, fit = fit_flat_field(flat, dark, settings)
    brighter = dark + (flat - dark) * 1.2
    result = validate_flat_field(brighter, maps, fit, settings)
    assert result["brightness_drift_fraction"] == pytest.approx(0.2, abs=0.002)
    assert result["brightness_change_notice"] is True
    assert max(result["spatial_residual_p95_fraction"]) < 0.01
    # No photometric normalisation is secretly applied to the corrected image.
    corrected, mask = apply_native_flat_field(brighter, maps)
    assert np.median(corrected[mask]) == pytest.approx(
        np.median(flat - dark) * 1.2, rel=0.01
    )


def test_legacy_parameters_load_but_require_explicit_jpeg_preset(field):
    """Old RAW DN numbers are retained, never automatically reinterpreted as JPEG."""
    legacy = FlatFieldSettings(minimum_signal_dn=64, maximum_dark_signal_dn=256)
    assert legacy.measurement_domain == "legacy-raw"
    with pytest.raises(ValueError, match="explicit JPEG8"):
        fit_flat_field(field[0], field[1], legacy)
    with pytest.raises(ValueError, match="8-bit"):
        FlatFieldSettings(measurement_domain=JPEG_DOMAIN)
    with pytest.raises(ValueError, match="explicit common"):
        FlatFieldSettings(
            measurement_domain=JPEG_DOMAIN,
            minimum_signal_dn=8,
            maximum_dark_signal_dn=32,
        ).require_jpeg()


def test_processing_roi_translates_centres_without_changing_full_sensor(field):
    """Rectangular selected pixels remain tied to their original sensor centres."""
    geometry = {
        "image_size": [1014, 760],
        "sensor_crop": [0, 0, 4056, 3040],
        **{
            key: [[4, 0, 1.5], [0, 4, 1.5]]
            for key in ("pixel_to_sensor", "white_to_sensor", "common_plane_to_sensor")
        },
    }
    source = {"measurement_space": JPEG_DOMAIN, "geometry": geometry}
    settings = field[2].model_copy(update={"processing_roi": (102, 0, 810, 760)})
    binding = processing_binding(source, settings)
    assert binding["geometry"]["image_size"] == [810, 760]
    assert binding["geometry"]["pixel_to_sensor"] == [[4, 0, 409.5], [0, 4, 1.5]]
    assert binding["geometry"]["sensor_crop"] == [0, 0, 4056, 3040]
    assert source["geometry"]["image_size"] == [1014, 760]
    for roi in ((1000, 0, 20, 760), (0, 750, 1014, 20)):
        with pytest.raises(ValueError, match="exceeds"):
            processing_binding(
                source, settings.model_copy(update={"processing_roi": roi})
            )


def test_actual_processing_tolerance_is_against_a_fixed_reference():
    """Real non-unit digital gain is allowed, cumulative drift is not."""
    reference = {
        "digital_gain": 1.0000462532,
        "colour_gains": [1.1, 1.2],
        "colour_correction_matrix": np.eye(3).ravel().tolist(),
    }
    check_processing(reference)
    check_processing(
        {**reference, "digital_gain": reference["digital_gain"] * 1.000009}, reference
    )
    with pytest.raises(ValueError, match="processing changed"):
        check_processing(
            {**reference, "digital_gain": reference["digital_gain"] * 1.000018},
            reference,
        )
