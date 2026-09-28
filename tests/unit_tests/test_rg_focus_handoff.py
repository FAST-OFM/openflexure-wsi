"""Native scan/standalone acquisition handoff; no physical camera or controller."""

import json
import math
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.rg.rg_flat_field import (
    JPEG_DOMAIN,
    FlatFieldSettings,
)
from openflexure_microscope_server.scanning.scan_directories import ScanDirectory
from openflexure_microscope_server.things.focus.rg_flat_field import RGFlatField
from openflexure_microscope_server.things.focus.rg_focus import RGFocus
from openflexure_microscope_server.things.illumination import LightState
from tests.integration_tests.test_rg_flat_field_api import OpticalModel
from tests.unit_tests.flat_field_process_stub import (
    apply_native_flat_field,
    fit_flat_field,
    validate_flat_field,
)
from tests.unit_tests.test_focus_scan import enabled_settings
from tests.unit_tests.test_focus_scan_lifecycle import _workflow_owners
from tests.unit_tests.test_focus_scan_lifecycle import (
    lifecycle_stage as _lifecycle_stage_fixture,
)
from tests.unit_tests.test_rg_focus_control import measurement
from tests.unit_tests.tissue_field_process_stub import external_tissue_field

HANDOFF_IMAGE_SIZE = 512


@pytest.fixture
def handoff_stage(mocker):
    """Reuse the accepted native stage and HTTP fake without any second writer."""
    yield from _lifecycle_stage_fixture.__wrapped__(mocker)


def _native_flat(stage, mocker, tmp_path):
    """Use real Acquisition/cleanup/maps with synthetic lowest-level camera/light."""
    model = OpticalModel()
    # Keep the commissioned 128-pixel patch policy; enlarge only synthetic optics.
    size = HANDOFF_IMAGE_SIZE
    geometry = model.binding["geometry"]
    for key in ("plane_size", "image_size", "white_size"):
        geometry[key] = [size, size]
    scale = geometry["sensor_crop"][2] / size
    for key in ("pixel_to_sensor", "white_to_sensor", "common_plane_to_sensor"):
        geometry[key] = [[scale, 0, (scale - 1) / 2], [0, scale, (scale - 1) / 2]]
    model.rgb_override = lambda _mode, original: cv2.resize(original, (size, size))
    flat = create_thing_without_server(RGFlatField, mock_all_slots=True)
    mocker.patch.object(
        flat,
        "_external_flat_fit",
        side_effect=lambda _directory, _label, source, dark, settings: fit_flat_field(
            source, dark, settings
        ),
    )
    mocker.patch.object(
        flat,
        "_external_flat_apply",
        side_effect=apply_native_flat_field,
    )
    mocker.patch.object(
        flat,
        "_external_flat_validate",
        side_effect=lambda _directory, _label, heldout, maps, report, settings: (
            validate_flat_field(heldout, maps, report, settings)
        ),
    )
    flat._data_dir = str(tmp_path / "flat")
    Path(flat.data_dir).mkdir()
    mocker.patch.object(RGFlatField, "_stage", property(lambda _self: stage))
    flat._cam.jpeg_measurement_configuration = model.binding
    flat._cam._measurement_preview = model.preview
    flat._cam.capture_jpeg_frame = model.capture
    flat._illumination.state = model.light()
    flat._illumination._read_state.side_effect = model.light
    flat._illumination._read_controller_and_light.side_effect = lambda: (
        stage._fetch_state(refresh=True),
        model.light(),
    )
    flat._illumination.set_mode.side_effect = model.select
    flat._illumination._extinguish_after_verified.side_effect = (
        lambda _verified, _controller: model.select("off")
    )
    flat._illumination._transition_after_verified.side_effect = (
        lambda _verified, _controller, mode: model.select(mode)
    )
    flat.parameters = FlatFieldSettings(
        measurement_domain=JPEG_DOMAIN,
        processing_roi=(0, 0, size, size),
        minimum_signal_dn=8,
        maximum_dark_signal_dn=32,
        smoothing_sigma_px=2.0,
        average_frames=4,
        validation_frames=3,
    )
    flat.calibrate(prepared=True)
    assert model.mode == "white"
    assert all(value["status"] == "valid" for value in flat.calibration_status.values())
    model.frames = 0
    model.commands.clear()
    # Connected synthetic fibres satisfy the commissioned 512-pixel component gate.
    # This is a handoff fixture, not evidence for tissue/estimator accuracy.
    white = np.full((size, size), 80.0)
    yy, xx = np.mgrid[24 : size - 24, 24 : size - 24]
    white[24:-24, 24:-24] += 24 * np.sin((xx + 4 * np.sin(yy / 24)) / 3.5)
    rgb = np.repeat(np.clip(white, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)
    rgb[0, 0] = [10, 20, 30]
    model.rgb_override = lambda mode, original: (
        rgb if mode == "white" else cv2.resize(original, (size, size))
    )
    return flat, model


@pytest.mark.parametrize("path", ["standalone", "scan"])
@pytest.mark.parametrize("restore_fails", [False, True])
def test_native_af_handoff_keeps_scan_contract_and_owned_frames(  # noqa: C901, PLR0915
    handoff_stage, mocker, tmp_path, path, restore_fails
):
    """Only camera/light pixels and final optical estimate are fixtures, not handoff."""
    stage, controller = handoff_stage
    flat, model = _native_flat(stage, mocker, tmp_path)
    workflow, rg, run_settings = _workflow_owners(stage, mocker, tmp_path)
    mocker.patch.object(RGFocus, "_rg_flat_field", property(lambda _self: flat))
    policy = rg.checked_profile().measurement_policy
    bound = workflow._current_focus_binding()
    run_settings = run_settings.model_copy(
        update={"focus_scan": enabled_settings(bound)}
    )
    scan = ScanDirectory.new_scan_dir("handoff", str(tmp_path / "scans"))
    session = workflow.new_focus_session(
        run_settings, scan_id=scan.name, evidence_writer=scan.save_focus_evidence
    )
    assert session is not None
    field = session.prepare_field((0, 0)) if path == "scan" else None
    if field is not None:
        rg.measurement_parameters = policy.model_copy(
            update={
                "estimator": policy.estimator.model_copy(
                    update={"minimum_confidence": 0.2}
                )
            }
        )

    buffers_seen = []
    original_handoff = flat._capture_focus_pair

    def handoff(*args, **kwargs):
        assert kwargs["persist_colour_frames"] is False
        result = original_handoff(*args, **kwargs)
        buffers_seen.append(
            {
                "capture": dict(result.capture),
                "metadata": json.loads(json.dumps(result.metadata)),
                "owned": [
                    array.flags.owndata and not array.flags.writeable
                    for array in (
                        result.white_rgb,
                        result.red_source_jpeg8,
                        result.green_source_jpeg8,
                    )
                ],
                "white": result.white_rgb,
                "red": result.red_source_jpeg8,
                "green": result.green_source_jpeg8,
            }
        )
        return result

    mocker.patch.object(flat, "_capture_focus_pair", side_effect=handoff)
    public_capture = mocker.patch.object(
        flat, "capture_pair", side_effect=AssertionError("AF used public disk handoff")
    )
    original_load = np.load

    def no_frame_load(filename, *args, **kwargs):
        if str(filename).endswith(("white-raw.npz", "red-raw.npz", "green-raw.npz")):
            raise AssertionError("AF reread its own RAW archive")
        return original_load(filename, *args, **kwargs)

    mocker.patch.object(np, "load", side_effect=no_frame_load)
    mocker.patch.object(
        cv2, "imread", side_effect=AssertionError("AF reread WHITE PNG")
    )
    supplied_white = []

    def prepare(white, reference, geometry_value, supplied_policy, exchange):
        assert white.dtype == np.uint8
        assert white.shape == (HANDOFF_IMAGE_SIZE, HANDOFF_IMAGE_SIZE, 3)
        assert supplied_policy == policy
        supplied_white.append(reference)
        return external_tissue_field(
            rg, white, reference, geometry_value, supplied_policy, exchange
        )

    mocker.patch.object(rg, "_external_tissue_field", side_effect=prepare)
    inputs_seen = []

    def optical_estimate(inputs, supplied_policy, _directory, _iteration):
        assert supplied_policy == policy
        assert inputs.field.metrics.status == "ready"
        assert all(buffers_seen[-1]["owned"])
        assert inputs.red_source_jpeg8 is buffers_seen[-1]["red"]
        assert inputs.green_source_jpeg8 is buffers_seen[-1]["green"]
        assert not inputs.red_source_jpeg8.flags.writeable
        assert not inputs.green_source_jpeg8.flags.writeable
        inputs_seen.append(inputs)
        return measurement(dx=6, dy=-0.8) if len(inputs_seen) == 1 else measurement()

    mocker.patch.object(rg, "_external_measure", side_effect=optical_estimate)
    if restore_fails:
        transition = flat._illumination._transition_after_verified.side_effect

        def failed_white_restore(verified, controller, mode):
            if mode == "white" and model.frames == 3:
                return LightState(available=False, mode="unknown", channels={})
            return transition(verified, controller, mode)

        flat._illumination._transition_after_verified.side_effect = failed_white_restore
    before = len(controller.scripts)
    if restore_fails:
        with pytest.raises(ValueError, match="focus light"):
            rg.autofocus(prepared=True) if field is None else rg.autofocus_for_scan(
                field
            )
        assert not buffers_seen
        assert not inputs_seen
        assert not supplied_white
        assert not session.observations
        expected_preload_scripts = 2 if path == "standalone" else 0
        assert len(controller.scripts) == before + expected_preload_scripts
        public_capture.assert_not_called()
        paths = list(Path(rg.data_dir).glob("*/iteration-*-timing.json"))
        assert len(paths) == 1
        timing = json.loads(paths[0].read_text())
        assert timing["status"] == "failed"
        assert timing["failed_phase"] == "acquisition"
        return

    result = (
        rg.autofocus(prepared=True) if field is None else rg.autofocus_for_scan(field)
    )
    if field is not None:
        field.validate_rg_result(result)
        field.accept_rg_result()
        # A corrected demo one-pair result is candidate evidence. It deliberately
        # does not train the surface without an independent final-Z measurement.
        assert len(session.observations) == 0
        assert result["field_id"] == field.field_id
        assert result["measurement_parameters"] == policy.model_dump(mode="json")
        assert rg.measurement_parameters.estimator.minimum_confidence == 0.2
    assert result["status"] == "focused"
    expected_pairs = 1 if field is not None else 2
    assert len(buffers_seen) == len(inputs_seen) == expected_pairs
    assert model.frames == 3 * expected_pairs
    assert model.commands == ["red", "green", "white"] * expected_pairs
    assert model.mode == "white"
    public_capture.assert_not_called()
    timings = [
        json.loads(path.read_text())
        for path in Path(rg.data_dir).glob("*/iteration-*-timing.json")
    ]
    assert len(timings) == expected_pairs
    for timing in timings:
        assert timing["status"] == "completed"
        assert set(timing["phases"]) == {
            "stage_validation",
            "acquisition",
            "input_load",
            "tissue",
            "source_planes",
            "red_correction",
            "green_correction",
            "estimate",
            "overlay",
            "frame_references",
        }
        assert all(
            math.isfinite(value["seconds"])
            and value["seconds"] >= 0
            and value["count"] == 1
            for value in timing["phases"].values()
        )
        assert sum(
            value["seconds"] for value in timing["phases"].values()
        ) == pytest.approx(timing["elapsed_s"])
    for index, evidence in enumerate(buffers_seen):
        directory = Path(evidence["capture"]["directory"])
        metadata = evidence["metadata"]
        for mode in ("white", "red", "green"):
            assert metadata[mode] == json.loads(
                (directory / metadata[mode]["metadata_file"]).read_text()
            )
        assert len(list(directory.glob("*.jpg"))) == 1
        assert metadata["white"]["jpeg_persisted"] is True
        white_payload = (directory / metadata["white"]["jpeg_file"]).read_bytes()
        with Image.open(BytesIO(white_payload)) as image:
            decoded_white = np.array(image.convert("RGB"), copy=True)
        np.testing.assert_array_equal(evidence["white"], decoded_white)
        for mode in ("red", "green"):
            assert metadata[mode]["jpeg_persisted"] is False
            assert metadata[mode]["jpeg_file"] is None
        if field is not None:
            expected_ids = [
                f"{field.field_id}:{mode}:{metadata[mode]['sensor_timestamp_ns']}"
                for mode in ("white", "red", "green")
            ]
            assert result["iterations"][index]["frame_ids"] == expected_ids
            assert inputs_seen[index].red_reference.frame_id == expected_ids[1]
            assert inputs_seen[index].green_reference.frame_id == expected_ids[2]
