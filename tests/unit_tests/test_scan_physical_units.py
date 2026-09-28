"""Physical scan settings are views of native values, never hardware actions."""

import json
from pathlib import Path

import pytest
from PIL import Image

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things.camera import CaptureMode
from openflexure_microscope_server.things.scanning.scan_workflows import (
    FastOFMWorkflow,
    RasterWorkflow,
    SnakeWorkflow,
)
from openflexure_microscope_server.things.stage.camera_stage_mapping import (
    csm_img_to_stage,
)
from openflexure_microscope_server.things.stage.moonraker import MoonrakerStage
from openflexure_microscope_server.ui import (
    Accordion,
    HelpBlock,
    PropertyControl,
    UIElementList,
)


def make_workflow(cls=SnakeWorkflow, units=(1000, 1000, 1000)):
    """Use an unstarted real stage; an accidental hardware call must fail."""
    stage = create_thing_without_server(
        MoonrakerStage,
        hardware={
            "axes": {
                axis: {"units_per_mm": scale}
                for axis, scale in zip("xyz", units, strict=True)
            }
        },
    )
    defaults = {"autofocus_dz": 50, "stack_dz": 5}
    defaults.update({"max_range": 1000} if cls is FastOFMWorkflow else {"y_count": 3})
    w = create_thing_without_server(cls, mock_all_slots=True, default_settings=defaults)
    cls._stage.connect(w, {"stage": stage})
    w._thing_server_interface._mocks.append(stage)  # Slots keep only weak references.
    w._csm.calibration_required = False
    w._csm.image_resolution = (700, 700)
    w._csm.image_to_stage_displacement_matrix = [
        [0.006414316017812542, -1.2711073953005023],
        [1.2743437592238684, 0.012566666616971983],
    ]
    w._csm.convert_image_to_stage_coordinates.side_effect = lambda **xy: (
        csm_img_to_stage(w._csm.image_to_stage_displacement_matrix, **xy)
    )
    w._cam.capture_modes = {
        "standard": CaptureMode(description="HQ", save_resolution=(1400, 1400))
    }
    w._cam._capture_image.return_value = Image.new("RGB", (1400, 1400))
    if cls is FastOFMWorkflow:
        w._background_detector.settings_ui.return_value = UIElementList([])
    return w


def test_pi4_geometry_is_read_only_and_resolution_specific():
    """Check the calibrated Pi4 field without opening a camera or controller."""
    w = make_workflow()
    g = w.scan_geometry
    assert g["available"]
    assert g["field_width_um"] == pytest.approx(892.083, abs=0.01)
    assert g["field_height_um"] == pytest.approx(889.787, abs=0.01)
    assert g["saved_pixel_width_um"] == pytest.approx(g["field_width_um"] / 1400)
    assert g["coverage_x_um"] == pytest.approx(
        g["field_x_span_um"] + 2 * g["tile_pitch_x_um"]
    )
    assert g["columns"] == g["rows"] == 3
    w._cam._capture_image.assert_not_called()
    w._autofocus.fast_autofocus.assert_not_called()
    assert w._stage._client is None


@pytest.mark.parametrize("units", [(1000, 1000, 1000), (2000, 4000, 10000)])
def test_distance_conversion_uses_per_axis_scale(units):
    """Check distance conversion uses per axis scale."""
    w = make_workflow(units=units)
    w.autofocus_dz_um = 45
    w.stack_dz_um = 3
    assert w.autofocus_dz == round(45 * units[2] / 1000)
    assert w.stack_dz == round(3 * units[2] / 1000)
    assert w.autofocus_dz_um == 45
    assert w.stack_dz_um == 3
    w.overlap_percent = 40
    assert w.overlap == 0.4
    assert w.overlap_percent == 40


def test_matrix_row_convention_and_anisotropic_stage():
    """Check matrix row convention and anisotropic stage."""
    w = make_workflow(units=(2000, 4000, 1000))
    w._csm.image_resolution = (100, 200)
    w._csm.image_to_stage_displacement_matrix = [[0, -8], [4, 0]]
    g = w.scan_geometry
    assert (g["field_width_um"], g["field_height_um"]) == (400, 200)
    assert (g["field_x_span_um"], g["field_y_span_um"]) == (400, 200)
    assert (g["tile_pitch_x_um"], g["tile_pitch_y_um"]) == (260, 130)


def test_width_height_round_up_to_whole_fields_and_read_back():
    """Check width height round up to whole fields and read back."""
    w = make_workflow()
    g = w.scan_geometry
    w.scan_width_um = 2000
    w.scan_height_um = 2000
    assert w.x_count == w.y_count == 3
    assert w.scan_width_um >= 2000
    assert w.scan_height_um >= 2000
    w.scan_width_um = w.scan_width_um
    assert w.x_count == 3
    w.scan_width_um = g["field_x_span_um"] + g["tile_pitch_x_um"]
    assert w.x_count == 2
    w.scan_height_um = 1
    assert w.y_count == 1
    assert w.scan_height_um == round(g["field_y_span_um"], 1)
    w.scan_height_um = w.scan_height_um
    assert w.y_count == 1


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_bad_distance_leaves_settings_unchanged(bad):
    """Check bad distance leaves settings unchanged."""
    w = make_workflow()
    before = (w.autofocus_dz, w.stack_dz, w.x_count)
    for name in ("autofocus_dz_um", "stack_dz_um", "scan_width_um"):
        with pytest.raises(ValueError, match="finite and positive"):
            setattr(w, name, bad)
    assert (w.autofocus_dz, w.stack_dz, w.x_count) == before


@pytest.mark.parametrize(
    "matrix", [None, [[0, 0], [0, 0]], [[float("nan"), 0], [0, 1]]]
)
def test_invalid_mapping_does_not_invent_coverage(matrix):
    """Check invalid mapping does not invent coverage."""
    w = make_workflow()
    w._csm.image_to_stage_displacement_matrix = matrix
    assert not w.scan_geometry["available"]
    with pytest.raises(ValueError, match="Camera Stage Mapping"):
        w.scan_width_um = 2000
    assert [c.property_name for c in w._grid_controls()] == ["x_count", "y_count"]
    w._cam._capture_image.assert_not_called()


def test_fast_ofm_limit_is_conservative_on_unequal_axes_and_equal_pitch():
    """Check the Fast OFM limit is conservative on unequal axes and equal pitch."""
    w = make_workflow(FastOFMWorkflow, (2000, 4000, 1000))
    w.max_range_um = 1000
    assert w.max_range == 2000
    g = w.scan_geometry
    assert g["centre_limit_x_um"] == 1000
    assert g["centre_limit_y_um"] == 500
    assert w.max_range_um == 1000
    w.equal_distances = True
    g = w.scan_geometry
    dx, dy = w._calc_displacement_from_overlap(0.35)
    assert g["tile_pitch_x_um"] == min(abs(dx), abs(dy)) * 0.5
    assert g["tile_pitch_y_um"] == min(abs(dx), abs(dy)) * 0.25


def test_fast_ofm_rectangle_geometry_uses_explicit_shape():
    """Known-area snake exposes the explicit frozen grid, including even shapes."""
    w = make_workflow(FastOFMWorkflow)
    w.scan_pattern = "rectangle_snake"
    w.rectangle_columns = 4
    w.rectangle_rows = 2
    g = w.scan_geometry
    assert g["columns"] == w.rectangle_columns
    assert g["rows"] == w.rectangle_rows
    assert g["coverage_x_um"] == pytest.approx(
        g["field_x_span_um"] + (g["columns"] - 1) * g["tile_pitch_x_um"]
    )
    assert g["coverage_y_um"] == pytest.approx(
        g["field_y_span_um"] + (g["rows"] - 1) * g["tile_pitch_y_um"]
    )
    assert "centre_limit_x_um" not in g


@pytest.mark.parametrize("cls", [SnakeWorkflow, RasterWorkflow, FastOFMWorkflow])
def test_physical_ui_fields_are_real_properties(cls):
    """Check physical ui fields are real properties."""
    w = make_workflow(cls)
    root = w.settings_ui().root
    if cls is FastOFMWorkflow:
        advanced = root[1].children.root
        groups = {
            element.title: element.children.root
            for element in advanced
            if isinstance(element, Accordion)
        }
        ui = [
            *root[0].children.root,
            *groups["Area and path"],
            *groups["Focus details"],
        ]
        assert isinstance(groups["Area and path"][0], HelpBlock)
    else:
        groups = {
            element.title: element.children.root
            for element in root
            if isinstance(element, Accordion)
        }
        ui = [*groups["Area"], *groups["Focus"]]
        assert isinstance(groups["Area"][0], HelpBlock)
    controls = {e.property_name: e for e in ui if isinstance(e, PropertyControl)}
    assert {"stack_dz_um", "autofocus_dz_um", "overlap_percent"} <= controls.keys()
    assert all("steps" not in c.label and not c.broken for c in controls.values())
    if cls is FastOFMWorkflow:
        assert "max_range_um" in controls
    else:
        assert {"scan_width_um", "scan_height_um"} <= controls.keys()


def test_loading_defaults_preserves_saved_operator_settings(tmp_path):
    """Check loading defaults preserves saved operator settings."""
    kwargs = {
        "settings_folder": str(tmp_path),
        "default_settings": {"stack_dz": 5, "autofocus_dz": 50, "y_count": 3},
    }
    first = create_thing_without_server(SnakeWorkflow, **kwargs)
    first.load_settings()
    assert first.stack_dz == 5
    first.stack_dz = 7
    first.y_count = 4
    second = create_thing_without_server(SnakeWorkflow, **kwargs)
    assert second.stack_dz == 5
    second.load_settings()
    assert second.stack_dz == 7
    assert second.y_count == 4
    assert second.autofocus_dz == 50


def test_snapshot_has_scale_and_native_execution_values():
    """Check snapshot has scale and native execution values."""
    w = make_workflow()
    w.scan_width_um = 2000
    settings, _, _ = w.all_settings(RelativeDataPath("test/images"))
    assert settings.autofocus_params.dz == 50
    assert settings.smart_stack_params.stack_dz == 5
    assert settings.x_count == 3
    assert settings.physical_geometry["save_resolution_px"] == [1400, 1400]
    w.overlap_percent = 50
    assert settings.physical_geometry["overlap_percent"] == 35


def test_deployment_defaults_match_configured_logical_stage_units():
    """Check deployment defaults match configured logical stage units."""
    c = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/prototype_motion_profile.json"
        ).read_text()
    )
    assert all(
        a["units_per_mm"] == 1000
        for a in c["things"]["stage"]["kwargs"]["hardware"]["axes"].values()
    )
    axes = c["things"]["stage"]["kwargs"]["hardware"]["axes"]
    assert all(
        axes[axis]["speed_mm_s"] == 4 and axes[axis]["accel_mm_s2"] == 40
        for axis in ("x", "y")
    )
    assert (axes["z"]["speed_mm_s"], axes["z"]["accel_mm_s2"]) == (0.1, 1)
    for name in ("snake_workflow", "raster_workflow", "fast_ofm_scan_workflow"):
        d = c["things"][name]["kwargs"]["default_settings"]
        assert (d["stack_dz"], d["autofocus_dz"], d["overlap"]) == (5, 50, 0.35)


def test_physical_scan_rejects_oversize_sweeps_before_start():
    """Validate persisted values too, not only the physical input widget."""
    w = make_workflow()
    # The fixture's unopened Z is deliberately disabled; enable only the model.
    from openflexure_microscope_server.fast_ofm_contracts import MotionSettings

    profile = w._stage.hardware_settings
    profile["axes"]["z"].update(
        enabled=True, min_mm=-0.5, max_mm=0.5, max_move_mm=0.05, ui_step_mm=0.005
    )
    w._stage._hardware = MotionSettings.model_validate(profile)
    with pytest.raises(ValueError, match="single-move"):
        w.autofocus_dz_um = 1000
    with pytest.raises(ValueError, match="single-move"):
        w.stack_dz_um = 51
    w.autofocus_dz = 1000
    with pytest.raises(ValueError, match="single-move"):
        w.check_before_start("no-hardware")
    assert w._stage._client is None
