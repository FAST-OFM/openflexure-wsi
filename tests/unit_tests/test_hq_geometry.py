"""HQ full-field profiles and CSM binding, without hardware or physical calibration."""

import copy
import json

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from labthings_fastapi.testing import (
    create_thing_without_server,
    manually_connect_thing_slot,
)

from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things.camera.simulation import SimulatedCamera
from openflexure_microscope_server.things.scanning.scan_workflows import (
    FastOFMWorkflow,
    WorkflowStartError,
)
from openflexure_microscope_server.things.stage.camera_stage_mapping import (
    CameraStageMapper,
    CSMUncalibratedError,
)


@pytest.fixture
def hq(mock_picam_thing):
    """Load the camera module with mocked platform dependencies, then use real HQ."""
    from openflexure_microscope_server.things.camera.picamera import PiCameraHQ

    assert mock_picam_thing.mapping_geometry is None
    return create_thing_without_server(PiCameraHQ)


def test_hq_default_full_field_modes(hq, mocker):
    """Both modes keep full unbinned sensor/ROI; sizes and buffer counts are explicit."""
    expected = {"default": ((1014, 760), 4), "full_resolution": ((4056, 3040), 2)}
    controls = {"AeEnable": False, "AwbEnable": False, "ExposureTime": 4477}
    cam = mocker.Mock()
    cam.create_video_configuration.side_effect = lambda **kwargs: kwargs
    for name, (main, buffers) in expected.items():
        mode = hq.streaming_modes[name]
        assert mode.main_resolution == main
        assert mode.lores_resolution == (1014, 760)
        assert mode.scaler_crop == (0, 0, 4056, 3040)
        assert mode.sensor_mode_dict == {"output_size": (4056, 3040), "bit_depth": 12}
        config = hq._create_picam_config_from_mode_info(cam, controls, mode)
        assert config["buffer_count"] == buffers
        assert config["main"]["size"] == main
        assert config["lores"]["size"] == (1014, 760)
        assert config["controls"] == {**controls, "ScalerCrop": (0, 0, 4056, 3040)}
    assert "ScalerCrop" not in controls
    assert hq.capture_modes["standard"].save_resolution == (2028, 1520)
    assert hq.capture_modes["full"].save_resolution is None
    assert hq.capture_modes["quick"].streaming_mode is None
    assert hq.mapping_geometry["image_resolution"] == [760, 1014]
    assert hq.mapping_geometry["preview_resolution"] == [1014, 760]
    assert "mapping_geometry" not in hq.thing_description().properties


@pytest.mark.parametrize(
    ("mode", "expected"), [("standard", (2028, 1520)), ("full", (4056, 3040))]
)
def test_hq_saved_resolution_preserves_field(hq, tmp_path, mocker, mode, expected):
    """The existing save path applies the configured standard/full dimensions."""
    hq._data_dir = str(tmp_path)
    mocker.patch.object(hq, "_add_metadata_to_capture")
    image = Image.new("RGB", (4056, 3040), (30, 80, 120))
    buffer_id = hq._memory_buffer.add_image(image, {}, mode)
    hq.save_from_memory(RelativeDataPath("field.jpeg"), buffer_id)
    with Image.open(tmp_path / "field.jpeg") as saved:
        assert saved.size == expected
        assert saved.width * 3040 == saved.height * 4056


@pytest.mark.parametrize(
    "patch",
    [
        {"roi": (-2, 0, 4056, 3040)},
        {"roi": (2, 0, 4056, 3040)},
        {"roi": (0, 0, 0, 3040)},
        {"roi": (1, 0, 4054, 3040)},
        {"default_resolution": (700, 700)},
        {"lores_resolution": (507, 380)},
        {"full_resolution": (8112, 6080)},
        {"standard_resolution": (0, 0)},
        {"default_resolution": (1014.0, 760)},
        {"default_buffer_count": True},
        {"full_buffer_count": 6},
        {"lores_resolution": (2028, 1520)},
    ],
)
def test_invalid_hq_profile_refused_before_initialisation(hq, mocker, patch):
    """Reject stretching, clipping, invalid sizes/types and extra CMA allocation."""
    from openflexure_microscope_server.things.camera import picamera

    initialise = mocker.patch.object(picamera.StreamingPiCamera2, "__init__")
    with pytest.raises(ValidationError):
        create_thing_without_server(type(hq), hq_profile=patch)
    initialise.assert_not_called()


def test_custom_hq_roi_and_sizes_are_validated_together(hq):
    """An explicit rectangle is supported without silently stretching other modes."""
    profile = {
        "roi": [28, 20, 4000, 3000],
        "default_resolution": [1000, 750],
        "lores_resolution": [1000, 750],
        "standard_resolution": [2000, 1500],
        "full_resolution": [4000, 3000],
    }
    camera = create_thing_without_server(type(hq), hq_profile=profile)
    assert camera.mapping_geometry["roi"] == [28, 20, 4000, 3000]
    assert camera.mapping_geometry["image_resolution"] == [750, 1000]
    assert camera.streaming_modes["full_resolution"].main_resolution == (4000, 3000)
    assert camera.streaming_modes["default"].sensor_mode_resolution == (4056, 3040)
    profile["roi"][0] = 0
    assert camera.mapping_geometry["roi"][0] == 28
    with pytest.raises(ValidationError):
        camera._hq_profile.roi = (0, 0, 4000, 3000)


@pytest.fixture
def mapper(hq):
    """Use the real mapper and HQ geometry with only the stage mocked."""
    value = create_thing_without_server(CameraStageMapper, mock_all_slots=True)
    manually_connect_thing_slot(value, "_cam", hq)
    return value


def calibration_for(camera):
    """Synthetic calibration with asymmetric signed scales in image [y, x] order."""
    return {
        "camera_stage_mapping_calibration": {
            "image_to_stage_displacement": [[0, -3], [2, 0]]
        },
        "image_resolution": camera.mapping_geometry["image_resolution"],
        "camera_geometry": camera.mapping_geometry,
    }


def test_hq_matching_csm_preserves_rectangular_axis_math(mapper, hq):
    """Current binding allows existing sign/axis math and independently sized edges."""
    mapper.last_calibration = json.loads(json.dumps(calibration_for(hq)))
    assert not mapper.calibration_required
    assert mapper.image_resolution == [760, 1014]
    assert mapper.convert_image_to_stage_coordinates(x=10, y=5) == {"x": 20, "y": -15}
    assert mapper.convert_stage_to_image_coordinates(x=20, y=-15) == {"x": 10, "y": 5}
    workflow = create_thing_without_server(FastOFMWorkflow, mock_all_slots=True)
    manually_connect_thing_slot(workflow, "_csm", mapper)
    assert workflow._calc_displacement_from_overlap(0.5) == (1014, -1140)
    mapper._stage.move_relative.assert_not_called()


@pytest.mark.parametrize("defect", ["missing", "roi", "size", "saved_size"])
def test_hq_stale_csm_is_unavailable_before_conversion_jog_or_scan(mapper, hq, defect):
    """Old data stays stored, but incompatible HQ geometry cannot command movement."""
    calibration = calibration_for(hq)
    if defect == "missing":
        del calibration["camera_geometry"]
        calibration["image_resolution"] = [700, 700]
    elif defect == "roi":
        calibration["camera_geometry"]["roi"] = [628, 120, 2800, 2800]
    elif defect == "size":
        calibration["camera_geometry"]["image_resolution"] = [700, 700]
    elif defect == "saved_size":
        calibration["image_resolution"] = [700, 700]
    mapper.last_calibration = calibration
    historical = copy.deepcopy(mapper.last_calibration)
    assert mapper.calibration_required
    assert mapper.image_to_stage_displacement_matrix is None
    assert mapper.image_resolution is None
    assert mapper.thing_state == {
        "image_to_stage_displacement_matrix": None,
        "image_resolution": None,
    }
    for operation in (
        mapper.convert_image_to_stage_coordinates,
        mapper.convert_stage_to_image_coordinates,
        mapper.move_in_image_coordinates,
    ):
        with pytest.raises(CSMUncalibratedError, match="incompatible"):
            operation(x=10, y=5)
    workflow = create_thing_without_server(FastOFMWorkflow, mock_all_slots=True)
    manually_connect_thing_slot(workflow, "_csm", mapper)
    workflow.autofocus_method = "none"
    workflow.focus_strategy = "single_autofocus"
    manually_connect_thing_slot(workflow, "_cam", hq)
    with pytest.raises(WorkflowStartError, match="not calibrated"):
        workflow.check_before_start("test-rectangular-field")
    mapper._stage.move_relative.assert_not_called()
    workflow._stage.move_absolute.assert_not_called()
    assert mapper.last_calibration == historical


def test_hq_csm_scales_across_same_field_streaming_modes(mapper, hq):
    """Default and full-resolution metadata describe one physical CSM."""
    mapper.last_calibration = json.loads(json.dumps(calibration_for(hq)))
    default_matrix = np.asarray(mapper.image_to_stage_displacement_matrix)
    default_resolution = mapper.image_resolution
    assert default_resolution == [760, 1014]

    hq.streaming_mode = "full_resolution"
    full_matrix = np.asarray(mapper.image_to_stage_displacement_matrix)
    full_resolution = mapper.image_resolution
    assert full_resolution == [3040, 4056]
    assert np.allclose(full_matrix, default_matrix / 4)
    assert mapper.convert_image_to_stage_coordinates(x=40, y=20) == {
        "x": 20,
        "y": -15,
    }

    # The standard scan tile is 2028 pixels wide.  Stitching scales each recorded
    # CSM from its recorded width to that saved width, so anchor metadata collected
    # in default mode and prediction metadata collected in full-resolution mode must
    # become identical.
    saved_width = hq.capture_modes["standard"].save_resolution[0]
    anchor_csm = default_matrix / (saved_width / default_resolution[1])
    prediction_csm = full_matrix / (saved_width / full_resolution[1])
    assert np.allclose(anchor_csm, prediction_csm)


def test_non_hq_unbound_calibration_unchanged(mapper):
    """Cameras not opting into geometry binding keep existing calibration behavior."""
    camera = create_thing_without_server(SimulatedCamera)
    manually_connect_thing_slot(mapper, "_cam", camera)
    mapper.last_calibration = {
        "camera_stage_mapping_calibration": {
            "image_to_stage_displacement": [[0, 3], [2, 0]]
        },
        "image_resolution": [600, 800],
    }
    assert camera.mapping_geometry is None
    assert not mapper.calibration_required
    assert mapper.image_resolution == [600, 800]
    assert mapper.convert_image_to_stage_coordinates(x=10, y=5) == {"x": 20, "y": 15}


def test_uniform_output_resolution_change_keeps_physical_tile_pitch(mapper, hq):
    """A uniformly denser output for the same field reuses and scales the fit."""
    workflow = create_thing_without_server(FastOFMWorkflow, mock_all_slots=True)
    manually_connect_thing_slot(workflow, "_csm", mapper)
    mapper.last_calibration = calibration_for(hq)
    pitch = workflow._calc_displacement_from_overlap(0.5)
    other = create_thing_without_server(
        type(hq), hq_profile={"default_resolution": [2028, 1520]}
    )
    manually_connect_thing_slot(mapper, "_cam", other)
    assert not mapper.calibration_required
    assert mapper.image_to_stage_displacement_matrix == [[0, -1.5], [1, 0]]
    assert workflow._calc_displacement_from_overlap(0.5) == pitch
    assert (
        other.capture_modes["standard"].save_resolution
        == hq.capture_modes["standard"].save_resolution
    )
    mapper._stage.move_relative.assert_not_called()


@pytest.mark.parametrize(
    "defect", [None, "changed_mode", "wrong_frame", "downsample", "changed_downsample"]
)
def test_calibration_records_geometry_or_preserves_previous_on_change(
    mapper, hq, mocker, defect
):
    """Bind a new successful fit; refuse changed dimensions before the second axis."""
    from camera_stage_mapping.exceptions import MappingError

    mapper.last_calibration = {"historical": True}
    old = copy.deepcopy(mapper.last_calibration)

    def axis_result(_direction):
        if defect == "changed_mode":
            hq.streaming_mode = "full_resolution"
        if defect == "changed_downsample":
            hq.downsampled_array_factor = 1
        resolution = [350, 350] if defect == "wrong_frame" else [380, 507]
        return {"image_resolution": resolution}

    calibrate = mocker.patch.object(mapper, "calibrate_1d", side_effect=axis_result)
    mocker.patch(
        "openflexure_microscope_server.things.stage.camera_stage_mapping.image_to_stage_displacement_from_1d",
        return_value={
            "image_to_stage_displacement": np.array([[0.0, -6.0], [4.0, 0.0]])
        },
    )
    if defect == "downsample":
        hq.downsampled_array_factor = 4
    if defect is not None:
        with pytest.raises(MappingError, match="geometry|downsampling"):
            mapper.calibrate_xy()
        assert mapper.last_calibration == old
        assert calibrate.call_count == (0 if defect == "downsample" else 1)
    else:
        result = mapper.calibrate_xy()
        assert result["camera_geometry"] == hq.mapping_geometry
        assert result["image_resolution"] == [760, 1014]
        assert not mapper.calibration_required
        assert mapper.image_to_stage_displacement_matrix == [[0, -3], [2, 0]]
        assert calibrate.call_count == 2
    mapper._stage.move_relative.assert_not_called()
