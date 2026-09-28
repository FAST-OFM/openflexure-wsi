"""Tests for ScanWorkflow things."""

import itertools
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image
from pydantic import BaseModel

import labthings_fastapi as lt
from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.sparse_focus import (
    FocusAnchor,
    FocusEstimate,
    SparseFocusScheduler,
)
from openflexure_microscope_server.scanning.scan_planners import (
    FrozenGridPlanner,
    SmartSpiral,
)
from openflexure_microscope_server.stitching.stitching import StitchingSettings
from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things.camera import CaptureMode
from openflexure_microscope_server.things.focus.autofocus import (
    NoFocusFoundError,
    SmartStackParams,
    StackParams,
)
from openflexure_microscope_server.things.scanning.scan_workflows import (
    CChipWorkflow,
    FastOFMSettingsModel,
    FastOFMWorkflow,
    ScanWorkflow,
    SnakeWorkflow,
    WorkflowStartError,
)
from openflexure_microscope_server.things.stage.camera_stage_mapping import (
    csm_img_to_stage,
)
from openflexure_microscope_server.ui import (
    Accordion,
    ActionButton,
    Container,
    HelpBlock,
    PropertyControl,
    UIElementList,
)


def test_partial_base_classes():
    """Create a partial class and check it raises the correct errors."""

    class MinimalSettings(BaseModel):
        """Some minimal settings for a workflow that doesn't work."""

        foo: str = "bar"

    class BadWorkflow(ScanWorkflow[MinimalSettings]):
        """Can initialise. Other properties and methods error."""

        display_name: str = lt.property(default="Bad Workflow", readonly=True)

    bad_workflow = create_thing_without_server(BadWorkflow)

    settings = MinimalSettings()

    with pytest.raises(NotImplementedError):
        bad_workflow.ready

    with pytest.raises(NotImplementedError):
        bad_workflow.all_settings("Fake Dir")

    with pytest.raises(NotImplementedError):
        bad_workflow.pre_scan_routine(settings)

    with pytest.raises(NotImplementedError):
        bad_workflow.new_scan_planner(settings, position={"x": 0, "y": 0, "z": 0})

    with pytest.raises(NotImplementedError):
        bad_workflow.acquisition_routine(settings, xyz_pos=[0, 0, 0])

    with pytest.raises(NotImplementedError):
        bad_workflow.settings_ui()


@pytest.fixture
def fast_ofm_workflow():
    """Return a FastOFMWorkflow thing with slots mocked."""
    workflow = create_thing_without_server(FastOFMWorkflow, mock_all_slots=True)
    workflow._cam.capture_modes = {"standard": "Mock"}
    workflow._cam._capture_image.return_value = Image.new("RGB", (1111, 1222))
    return workflow


@pytest.fixture
def snake_workflow():
    """Return a SnakeScanWorkflow thing with slots mocked."""
    workflow = create_thing_without_server(SnakeWorkflow, mock_all_slots=True)
    workflow._cam.capture_modes = {"standard": "Mock"}
    workflow._cam.stream_active = True
    workflow._cam.streaming_mode = "default"
    workflow._cam._capture_image.return_value = Image.new("RGB", (1111, 1222))
    workflow._stage.get_xyz_position.return_value = (1, 2, 3)
    return workflow


@pytest.fixture
def cchip_workflow():
    """Return a CChipWorkflow thing with slots mocked."""
    workflow = create_thing_without_server(CChipWorkflow, mock_all_slots=True)
    workflow._cam.capture_modes = {"standard": "Mock"}
    workflow._cam._capture_image.return_value = Image.new("RGB", (1111, 1222))
    workflow._stage.get_xyz_position.return_value = (1, 2, 3)
    return workflow


def test_fast_ofm_workflow_save_resolution(fast_ofm_workflow):
    """Check that modes without a configured resize use the captured size."""
    width, height = fast_ofm_workflow._get_save_resolution()
    assert width == 1111
    assert height == 1222


def test_fast_ofm_workflow_uses_configured_final_save_resolution(fast_ofm_workflow):
    """Record the resized on-disk shape rather than the larger capture buffer."""
    fast_ofm_workflow._cam.capture_modes = {
        "standard": CaptureMode(description="HQ", save_resolution=(2028, 1520))
    }

    assert fast_ofm_workflow._get_save_resolution() == (2028, 1520)
    fast_ofm_workflow._cam._capture_image.assert_not_called()


# Use itertools to iterate over every true/false permutation
@pytest.mark.parametrize(
    ("csm_calibrated", "skip_background", "bg_ready"),
    itertools.product([True, False], repeat=3),
)
def test_fast_ofm_workflow_ready(
    fast_ofm_workflow, csm_calibrated, skip_background, bg_ready, caplog
):
    """Check ready property and the pre-run check work for each permutation."""
    fast_ofm_workflow._csm.calibration_required = not csm_calibrated
    fast_ofm_workflow.skip_background = skip_background
    fast_ofm_workflow._background_detector.ready = bg_ready

    if not csm_calibrated:
        # For all permutations not ready if CSM isn't calibrated.
        assert not fast_ofm_workflow.ready
        with pytest.raises(
            RuntimeError, match="Camera Stage Mapping is not calibrated."
        ):
            fast_ofm_workflow.check_before_start("scan_name")
    elif not skip_background:
        # CSM is ready and background is not skipped: Always ready, but warns on check
        assert fast_ofm_workflow.ready
        with caplog.at_level(logging.WARNING):
            fast_ofm_workflow.check_before_start("scan_name")
        assert len(caplog.records) == 1
    elif not bg_ready:
        # Skipping background, but detector not ready. Raises error
        assert not fast_ofm_workflow.ready
        with pytest.raises(RuntimeError, match="Background is not set"):
            fast_ofm_workflow.check_before_start("scan_name")
    else:
        # Finally CSM calibrated, skipping background, and detector ready: This is
        # ready and should not warn.
        assert fast_ofm_workflow.ready
        with caplog.at_level(logging.WARNING):
            fast_ofm_workflow.check_before_start("scan_name")
        assert len(caplog.records) == 0


def test_fast_ofm_workflow_settings_generation(fast_ofm_workflow, mocker):
    """Check the settings models generate as expected."""
    mocker.patch.object(
        fast_ofm_workflow, "_calc_displacement_from_overlap", return_value=(123, 456)
    )

    img_dir = RelativeDataPath("this/img_dir")
    workflow_settings, stitching_settings, save_res = fast_ofm_workflow.all_settings(
        img_dir
    )
    assert save_res == (1111, 1222)
    ## Check type
    assert isinstance(workflow_settings, FastOFMSettingsModel)
    assert isinstance(stitching_settings, StitchingSettings)
    assert isinstance(workflow_settings.smart_stack_params, SmartStackParams)

    # Check stitching defaults
    assert stitching_settings.correlation_resize == 0.5
    assert stitching_settings.overlap == 0.35
    # Check some workflow defaults
    assert workflow_settings.overlap == 0.35
    assert workflow_settings.max_dist == 45000
    assert workflow_settings.scan_pattern == "tissue_spiral"
    assert workflow_settings.rectangle_columns == 731
    assert workflow_settings.rectangle_rows == 197
    assert workflow_settings.skip_background
    assert workflow_settings.refine_background_boundaries is False
    assert workflow_settings.smart_stack_params.stack_dz == 50
    assert workflow_settings.smart_stack_params.images_to_save == 1
    assert workflow_settings.smart_stack_params.min_images_to_test == 9
    # Check values from calculating overlap are as expected (from above mock)
    assert workflow_settings.dx == 123
    assert workflow_settings.dy == 456
    # And that the input image dir is passed to stack the stack parameter for saving
    assert workflow_settings.capture_params.images_dir.posixpath == "this/img_dir"


def test_fast_ofm_workflow_settings_generation_equal_overlap(fast_ofm_workflow, mocker):
    """Check that setting x and y equal behaves as expected."""
    mocker.patch.object(
        fast_ofm_workflow, "_calc_displacement_from_overlap", return_value=(123, 456)
    )

    # Different when False
    fast_ofm_workflow.equal_distances = False
    img_dir = RelativeDataPath("this/img_dir")
    workflow_settings, _stitching_settings, _save_res = fast_ofm_workflow.all_settings(
        img_dir
    )

    assert workflow_settings.dx == 123
    assert workflow_settings.dy == 456

    # Same when set True
    fast_ofm_workflow.equal_distances = True
    workflow_settings, _stitching_settings, _save_res = fast_ofm_workflow.all_settings(
        img_dir
    )

    assert workflow_settings.dx == 123
    assert workflow_settings.dy == 123


# A CSM that is "normal" changing from camera maxtrix coordinates (y,x) to normal
# (x, y) coordinates
CSM_NORMAL = [
    [0.02, 0.25],
    [0.25, 0.01],
]
# A CSM that is "normal" changing from camera maxtrix coordinates (y,x) to normal
# (x, y) coordinates, but the output y sign is flipped.
CSM_FLIP_Y = [
    [0.02, -0.25],
    [0.25, 0.01],
]
# A CSM that is for a rotated compared to normal camera changing from camera maxtrix
#  thus dx (the x motor) controls y and the y motor controls x
CSM_ROTATED = [
    [0.25, 0.02],
    [0.01, 0.25],
]


@pytest.mark.parametrize(
    ("csm_matrix", "overlap", "expected_steps"),
    [
        (CSM_NORMAL, 0.5, (100, 75)),
        (CSM_NORMAL, 0.25, (150, 112)),
        (CSM_FLIP_Y, 0.5, (100, -75)),
        (CSM_FLIP_Y, 0.25, (150, -112)),
        (CSM_ROTATED, 0.5, (75, 100)),
        (CSM_ROTATED, 0.25, (112, 150)),
    ],
)
def test_fast_ofm_calculate_overlap(
    csm_matrix, overlap, expected_steps, fast_ofm_workflow
):
    """Check the dx and dy values are as expected for given overlap and matrix.

    Note that the matrices on the dominant axes all have 0.25 magnitude. And the
    mock camera is set to be 800px in x and 600px in y. For 0 overlap the movement
    should be dx of 200 (due to the 0.25 factor).
    """

    def apply_csm(x: float, y: float, **_kwargs: float) -> dict[str, int]:
        """Convert image coordinates to stage coordinates."""
        return csm_img_to_stage(csm_matrix, x=x, y=y)

    fast_ofm_workflow._csm.convert_image_to_stage_coordinates.side_effect = apply_csm

    # First check this error if CSM isn't calibrated:
    fast_ofm_workflow._csm.calibration_required = True
    fast_ofm_workflow._csm.image_resolution = None
    with pytest.raises(RuntimeError, match="CSM not set"):
        fast_ofm_workflow._calc_displacement_from_overlap(0.25)

    fast_ofm_workflow._csm.calibration_required = False
    # the img resolution is in matrix coords, so (y, x) not (x, y)
    fast_ofm_workflow._csm.image_resolution = (600, 800)
    dx, dy = fast_ofm_workflow._calc_displacement_from_overlap(overlap)
    assert (dx, dy) == expected_steps


def test_fast_ofm_pre_scan_routine(fast_ofm_workflow, mocker):
    """Check the pre-scan routine does autofocusses."""
    # Rather than create a whole Setting class, just create a mock with the value
    # we need set
    mock_settings = mocker.Mock()
    mock_settings.focus_strategy = "smart_stack"
    mock_settings.autofocus_method = "openflexure"
    mock_settings.autofocus_params.dz = 1234
    # Run the function
    fast_ofm_workflow.pre_scan_routine(mock_settings)
    # Check the autofocus was run using the mocked slot.
    assert fast_ofm_workflow._autofocus.looping_autofocus.call_count == 1
    call_kwargs = fast_ofm_workflow._autofocus.looping_autofocus.call_args.kwargs
    assert call_kwargs["dz"] == 1234
    assert call_kwargs["start"] == "centre"


def test_fast_ofm_new_scan_planner(fast_ofm_workflow, mocker):
    """Check the pre-scan routine does autofocusses."""
    # Rather than create a whole Setting class, just create a mock with the 3 values
    # we need set
    mock_settings = mocker.Mock()
    mock_settings.focus_strategy = "smart_stack"
    mock_settings.dx = 666
    mock_settings.dy = 999
    mock_settings.max_dist = 54321
    mock_settings.scan_pattern = "tissue_spiral"
    mock_settings.refine_background_boundaries = False

    planner = fast_ofm_workflow.new_scan_planner(
        mock_settings, position={"x": 1, "y": 2, "z": 3}
    )
    assert isinstance(planner, SmartSpiral)
    # Check the settings were read
    assert planner._dx == 666
    assert planner._dy == 999
    assert planner._max_dist == 54321


def test_fast_ofm_rectangle_snake_starts_at_corner_and_is_frozen(
    fast_ofm_workflow, mocker
):
    """Known-area mode visits the exact frozen grid from the operator's corner."""
    settings = FastOFMSettingsModel.model_construct(
        dx=10,
        dy=20,
        max_dist=25,
        scan_pattern="rectangle_snake",
        rectangle_columns=5,
        rectangle_rows=3,
    )

    expected = (
        (100, 200),
        (110, 200),
        (120, 200),
        (130, 200),
        (140, 200),
        (140, 220),
        (130, 220),
        (120, 220),
        (110, 220),
        (100, 220),
        (100, 240),
        (110, 240),
        (120, 240),
        (130, 240),
        (140, 240),
    )
    external = mocker.patch.object(
        fast_ofm_workflow, "_external_rectangle_route", return_value=expected
    )
    position = {"x": 100, "y": 200, "z": 3}
    planner = fast_ofm_workflow.new_scan_planner(settings, position=position)

    external.assert_called_once_with(settings, position)
    assert isinstance(planner, FrozenGridPlanner)
    assert planner.remaining_locations == list(expected)


def test_fast_ofm_rectangle_route_converts_only_at_the_process_boundary(
    fast_ofm_workflow, mocker
):
    """Physical core coordinates round-trip to the exact frozen stage grid."""
    settings = FastOFMSettingsModel.model_construct(
        dx=10,
        dy=-20,
        max_dist=25,
        scan_pattern="rectangle_snake",
        rectangle_columns=3,
        rectangle_rows=2,
    )
    mocker.patch.object(
        fast_ofm_workflow,
        "_um_per_unit",
        side_effect=lambda axis: {"x": 0.5, "y": 2.0}[axis],
    )
    process = mocker.Mock()
    process.request.return_value = {
        "result": {
            "actions": [
                {
                    "kind": "component_start",
                    "position_um": {"x_um": 50.0, "y_um": 400.0},
                },
                *[
                    {"kind": "visit", "position_um": {"x_um": x, "y_um": y}}
                    for x, y in (
                        (50.0, 400.0),
                        (55.0, 400.0),
                        (60.0, 400.0),
                        (60.0, 360.0),
                        (55.0, 360.0),
                        (50.0, 360.0),
                    )
                ],
            ]
        }
    }
    mocker.patch.object(fast_ofm_workflow, "_route_core_process", return_value=process)

    route = fast_ofm_workflow._external_rectangle_route(
        settings, {"x": 100, "y": 200, "z": 3}
    )

    assert route == (
        (100, 200),
        (110, 200),
        (120, 200),
        (120, 180),
        (110, 180),
        (100, 180),
    )
    process.request.assert_called_once_with(
        "planning.route",
        {
            "traversal": "exact_grid_serpentine",
            "grid": {
                "origin_um": {"x_um": 50.0, "y_um": 400.0},
                "step_um": {"x_um": 5.0, "y_um": -40.0},
                "columns": 3,
                "rows": 2,
            },
        },
        timeout_s=5.0,
    )


def test_fast_ofm_rectangle_dimensions_are_explicit_and_frozen(
    fast_ofm_workflow, mocker
):
    """Known-area Fast OFM uses requested rows/columns instead of a square radius."""
    mocker.patch.object(
        fast_ofm_workflow, "_calc_displacement_from_overlap", return_value=(1101, -825)
    )
    fast_ofm_workflow.scan_pattern = "rectangle_snake"
    fast_ofm_workflow.rectangle_columns = 6
    fast_ofm_workflow.rectangle_rows = 14

    settings, _stitching, _resolution = fast_ofm_workflow.all_settings(
        RelativeDataPath("scan/images")
    )

    assert settings.rectangle_columns == 6
    assert settings.rectangle_rows == 14
    assert settings.dx == 1101
    assert settings.dy == -825


def test_fast_ofm_acquisition_with_background_detected(
    fast_ofm_workflow, mocker, caplog
):
    """If background is detected it should return without a stack."""
    # Mocking for settings as above
    mock_settings = mocker.Mock()
    mock_settings.focus_strategy = "smart_stack"
    mock_settings.skip_background = True

    fast_ofm_workflow._background_detector.image_is_sample.return_value = (
        False,
        "totally empty",
    )

    with caplog.at_level(logging.INFO):
        imaged, focus_height, image_count = fast_ofm_workflow.acquisition_routine(
            mock_settings, xyz_pos=(1, 2, 3)
        )

    # Check returns
    assert imaged is False
    assert focus_height is None  # None meaning not focussed
    assert image_count == 0
    # Check there is a log explaining what happened
    assert len(caplog.records) == 1
    assert caplog.records[0].message == "Skipping (1, 2, 3) as it is totally empty."
    assert caplog.records[0].levelname == "INFO"

    # And that smart stack never ran
    assert fast_ofm_workflow._autofocus.run_smart_stack.call_count == 0


def test_fast_ofm_acquisition_not_skipping_background(
    fast_ofm_workflow, mocker, caplog
):
    """Check when not skipping background an image is always saved."""
    # Mocking for settings as above
    mock_settings = mocker.Mock()
    mock_settings.focus_strategy = "smart_stack"
    mock_settings.autofocus_method = "openflexure"
    mock_settings.skip_background = False
    mock_settings.smart_stack_params = SmartStackParams(
        stack_dz=5,
        images_to_save=3,
        min_images_to_test=3,
        save_on_failure=True,
        check_turning_points=True,
    )

    # Set up background detector to say it is empty, this shouldn't stop stacking.
    fast_ofm_workflow._background_detector.image_is_sample.return_value = (
        False,
        "totally empty",
    )

    with caplog.at_level(logging.INFO):
        # First set smart stack to report failure
        fast_ofm_workflow._autofocus.run_smart_stack.return_value = (False, 123, 3)
        imaged, focus_height, image_count = fast_ofm_workflow.acquisition_routine(
            mock_settings, xyz_pos=(1, 2, 3)
        )
        # Check return
        assert imaged is True  # Always images if not skipping background
        assert focus_height is None
        assert image_count == 3

        # Successful smart stack
        fast_ofm_workflow._autofocus.run_smart_stack.return_value = (True, 123, 3)

        imaged, focus_height, image_count = fast_ofm_workflow.acquisition_routine(
            mock_settings, xyz_pos=(1, 2, 3)
        )
        # Check return
        assert imaged is True  # Always images if not skipping background
        assert focus_height == 123
        assert image_count == mock_settings.smart_stack_params.images_to_save

    # Should never warn
    assert len(caplog.records) == 0

    # And that smart stack never ran
    assert fast_ofm_workflow._autofocus.run_smart_stack.call_count == 2


def test_fast_ofm_acquisition_on_sample(fast_ofm_workflow, mocker, caplog):
    """Check when skipping background, but background not detected."""
    # Mocking for settings as above
    mock_settings = mocker.Mock()
    mock_settings.focus_strategy = "smart_stack"
    mock_settings.autofocus_method = "openflexure"
    mock_settings.skip_background = True
    mock_settings.smart_stack_params = SmartStackParams(
        stack_dz=5,
        images_to_save=3,
        min_images_to_test=3,
        save_on_failure=False,
        check_turning_points=True,
    )

    # Set up background detector to say there is sample.
    fast_ofm_workflow._background_detector.image_is_sample.return_value = (True, None)

    with caplog.at_level(logging.INFO):
        # First set smart stack to report failure
        fast_ofm_workflow._autofocus.run_smart_stack.return_value = (False, 123, 0)
        with pytest.raises(NoFocusFoundError, match="not a background classification"):
            fast_ofm_workflow.acquisition_routine(mock_settings, xyz_pos=(1, 2, 3))

        # Successful smart stack
        fast_ofm_workflow._autofocus.run_smart_stack.return_value = (True, 123, 3)
        imaged, focus_height, image_count = fast_ofm_workflow.acquisition_routine(
            mock_settings, xyz_pos=(1, 2, 3)
        )
        # Check return
        assert imaged is True  # Always images if not skipping background
        assert focus_height == 123
        assert image_count == mock_settings.smart_stack_params.images_to_save

    assert len(caplog.records) == 0

    # And that smart stack never ran
    assert fast_ofm_workflow._autofocus.run_smart_stack.call_count == 2


def test_autofocus_and_capture_returns_one_image(snake_workflow, mocker):
    """Test snake scan acquisition returns one image."""
    mock_settings = mocker.Mock()
    mock_settings.focus_strategy = "smart_stack"
    mock_settings.autofocus_method = "openflexure"
    mock_settings.smart_stack_params = StackParams(
        stack_dz=5,
        images_to_save=7,
        min_images_to_test=7,
        save_on_failure=False,
        check_turning_points=True,
    )
    snake_workflow._autofocus.run_smart_stack.return_value = (
        True,
        123,
        mock_settings.smart_stack_params.images_to_save,
    )

    imaged, focus_height, image_count = snake_workflow.acquisition_routine(
        mock_settings,
        xyz_pos=(1, 2, 3),
    )

    assert imaged
    assert image_count == 7


@pytest.mark.parametrize(
    ("policy", "expected", "raises"),
    [
        ("keep_z", (True, None, 1), False),
        ("skip_tile", (False, None, 0), False),
        ("pause", None, True),
    ],
)
def test_no_tissue_policy_never_starts_autofocus(
    snake_workflow, policy, expected, raises
):
    """Fresh no-tissue evidence follows the frozen policy without any AF call."""
    settings = MagicMock()
    settings.focus_scan.run.no_tissue_mode = policy
    settings.capture_params.images_dir.join.return_value = "tile.jpeg"
    settings.capture_params.capture_mode = "standard"
    field = MagicMock()
    field.no_tissue = True
    if raises:
        with pytest.raises(NoFocusFoundError, match="Fresh WHITE"):
            snake_workflow._acquire_with_focus(settings, (1, 2, 3), field)
    else:
        assert (
            snake_workflow._acquire_with_focus(settings, (1, 2, 3), field) == expected
        )
    snake_workflow._autofocus.fast_autofocus.assert_not_called()
    snake_workflow._rg_focus.autofocus_for_scan.assert_not_called()
    if policy == "keep_z":
        field.before_main_capture.assert_called_once_with()
        field.sync_after_effect.assert_called_once_with("white_tile_capture")
    else:
        snake_workflow._cam.capture_and_save_to_path.assert_not_called()


def test_fast_ofm_enabled_background_skip_is_rejected_before_motion(
    fast_ofm_workflow, mocker
):
    """The unproven detector-at-offset combination is refused, not silently disabled."""
    from .test_focus_approach import binding, snapshot
    from .test_focus_scan import enabled_settings

    stage_binding = binding(snapshot())
    settings = FastOFMSettingsModel.model_construct(
        focus_scan=enabled_settings(stage_binding), skip_background=True
    )
    mocker.patch(
        "openflexure_microscope_server.things.scanning.scan_workflows.MoonrakerStage",
        type(fast_ofm_workflow._stage),
    )
    mocker.patch.object(
        fast_ofm_workflow, "_current_focus_binding", return_value=stage_binding
    )
    with pytest.raises(WorkflowStartError, match="background skipping"):
        fast_ofm_workflow.validate_focus_scan(settings)


def test_focus_scan_capability_is_read_only_and_enumerates_selection_gates(
    snake_workflow, mocker
):
    """Capability reports every static gate without camera, autofocus or movement."""
    from .test_focus_approach import binding, snapshot

    stage_binding = binding(snapshot())
    mocker.patch(
        "openflexure_microscope_server.things.scanning.scan_workflows.MoonrakerStage",
        type(snake_workflow._stage),
    )
    mocker.patch.object(
        snake_workflow, "_current_focus_binding", return_value=stage_binding
    )
    snake_workflow._cam.reset_mock()
    snake_workflow._stage.reset_mock()
    snake_workflow._autofocus.reset_mock()

    capability = snake_workflow.focus_scan_capability()

    assert capability.supported
    assert not capability.available
    assert capability.reasons == (
        "Autofocus method must be Tissue R/G LED",
        "Focus strategy must be single autofocus",
    )
    assert capability.current_binding == stage_binding
    assert capability.maximum_white_searches_per_field == 1
    assert capability.on_focus_failure == "pause"
    snake_workflow._cam.assert_not_called()
    snake_workflow._stage.assert_not_called()
    snake_workflow._autofocus.assert_not_called()


def test_focus_scan_capability_reports_current_and_stale_saved_binding(
    snake_workflow, mocker
):
    """Reading a new valid binding does not silently rewrite a stale saved setup."""
    from .test_focus_approach import binding, snapshot
    from .test_focus_scan import enabled_settings

    current = binding(snapshot())
    stale = current.model_copy(update={"reference_id": "previous-reference"})
    mocker.patch(
        "openflexure_microscope_server.things.scanning.scan_workflows.MoonrakerStage",
        type(snake_workflow._stage),
    )
    mocker.patch.object(snake_workflow, "_current_focus_binding", return_value=current)
    snake_workflow.autofocus_method = "led"
    snake_workflow.focus_strategy = "single_autofocus"
    snake_workflow.focus_scan = enabled_settings(stale)

    capability = snake_workflow.focus_scan_capability()

    assert capability.available
    assert capability.reasons == ()
    assert capability.current_binding == current
    assert capability.saved_binding_current is False
    assert capability.saved_binding_reason == (
        "Focus binding identities or frozen parameters changed"
    )
    assert capability.saved_binding_mismatched_fields == ("reference_id",)
    assert snake_workflow.focus_scan.run.binding == stale


@pytest.mark.parametrize(
    "reason",
    [
        "R/G focus profile is candidate-only: test-only candidate",
        "A valid live stage reference is required",
    ],
)
def test_focus_scan_capability_preserves_precise_binding_failure(
    snake_workflow, mocker, reason
):
    """Candidate profiles and a missing zero remain distinct unavailable reasons."""
    mocker.patch(
        "openflexure_microscope_server.things.scanning.scan_workflows.MoonrakerStage",
        type(snake_workflow._stage),
    )
    mocker.patch.object(
        snake_workflow, "_current_focus_binding", side_effect=ValueError(reason)
    )
    snake_workflow.autofocus_method = "led"
    snake_workflow.focus_strategy = "single_autofocus"

    capability = snake_workflow.focus_scan_capability()

    assert not capability.available
    assert capability.reasons == (f"Current focus binding is unavailable: {reason}",)


def test_fast_ofm_focus_capability_keeps_background_combination_unavailable(
    fast_ofm_workflow, mocker
):
    """The UI bridge cannot weaken the accepted Fast OFM near-focus guard."""
    from .test_focus_approach import binding, snapshot

    stage_binding = binding(snapshot())
    mocker.patch(
        "openflexure_microscope_server.things.scanning.scan_workflows.MoonrakerStage",
        type(fast_ofm_workflow._stage),
    )
    mocker.patch.object(
        fast_ofm_workflow, "_current_focus_binding", return_value=stage_binding
    )
    fast_ofm_workflow.autofocus_method = "led"
    fast_ofm_workflow.focus_strategy = "single_autofocus"
    fast_ofm_workflow.skip_background = True

    capability = fast_ofm_workflow.focus_scan_capability()

    assert not capability.available
    assert capability.fast_ofm_background_skipping
    assert capability.reasons == (
        "Fast OFM background skipping is not proven near predicted focus",
    )


def test_enabled_capture_failure_keeps_focus_separate_from_captured(snake_workflow):
    """A failed WHITE tile cannot prematurely accept the pending LED result."""
    field = MagicMock()
    result = {"status": "focused", "id": "rg-1"}
    snake_workflow._rg_focus.autofocus_for_scan.return_value = result
    failure = OSError("tile write failed")
    snake_workflow._cam.capture_and_save_to_path.side_effect = failure
    with pytest.raises(OSError, match="tile write failed"):
        snake_workflow._autofocus_and_capture(
            (1, 2, 3),
            50,
            MagicMock(),
            "standard",
            "led",
            focus_field=field,
        )
    snake_workflow._rg_focus.autofocus_for_scan.assert_called_once_with(field)
    field.validate_rg_result.assert_called_once_with(result)
    field.accept_rg_result.assert_not_called()
    field.before_main_capture.assert_called_once_with()
    field.sync_after_effect.assert_not_called()
    field.session.stop.assert_called_once_with(field, failure)


@pytest.fixture
def sparse_journal_workflow():
    """Create a real bounded journal around mocked camera/stage owners."""
    from .test_scan_focus_strategy import make_workflow, snapshot

    workflow = make_workflow()
    workflow.focus_strategy = "single_autofocus"
    workflow.autofocus_method = "simultaneous_rg"
    workflow.sparse_focus_enabled = True
    workflow.scan_pattern = "rectangle_snake"
    settings = snapshot(workflow)
    images = RelativeDataPath(".")
    with images.save_to_tempdir():
        settings.capture_params.images_dir = images
        scheduler = SparseFocusScheduler(
            settings.sparse_focus,
            x_um_per_unit=1,
            y_um_per_unit=1,
            z_um_per_unit=1,
            predictor=lambda anchors, **_kwargs: FocusEstimate(
                len(anchors) >= 4,
                7.0 if len(anchors) >= 4 else None,
                "plane" if len(anchors) >= 4 else "none",
                "validated test surface" if len(anchors) >= 4 else "test warm-up",
                min(4, len(anchors)),
            ),
        )
        workflow._sparse_focus_scheduler = scheduler
        workflow._start_focus_telemetry(images)
        yield (
            workflow,
            settings,
            scheduler,
            Path(images.abs_data_path) / "focus-telemetry.jsonl",
        )


@pytest.mark.parametrize("white", [False, True])
def test_rg_peripheral_outcome_survives_capture_and_trains_only_rg(
    sparse_journal_workflow, white
):
    """One search is attempted; only an actual RG rescue is used or becomes support."""
    workflow, settings, scheduler, journal = sparse_journal_workflow
    scheduler.prepare(10, 20, 7, 7)
    focus_z = -6 if white else 9
    workflow._rg_simultaneous.autofocus_for_scan.return_value = {
        "status": "focused",
        "focus_method": "white_fallback" if white else "rg_simultaneous",
        "fallback_count": 1 if white else 0,
        "mixed_capture_count": 1,
        "simultaneous_attempted": True,
        "final_position_units": [10, 20, focus_z],
        "report_ref": "rg_focus/white/report.json"
        if white
        else "rg_simultaneous/ok/focus-report.json",
        "simultaneous_report_ref": "rg_simultaneous/probe/focus-report.json",
        "fallback_reason": "too few central supports" if white else None,
        "peripheral_search": {
            "attempted": True,
            "used": not white,
            "status": "refused" if white else "ready",
            "evaluated_patch_count": 12,
            "peripheral_patch_count": 6,
            "central_anchor_count": 1 if white else 2,
            "elapsed_s": 0.4,
            "stop_reason": "no central support" if white else "accepted",
            "large_patch_diagnostics": ["must not copy"],
        },
    }
    assert workflow.acquisition_routine(settings, (10, 20, 7)) == (True, focus_z, 1)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["focus_height_units"] == focus_z
    assert row["surface_anchor_added"] is not white
    assert row["image_file"] == f"img_10_20_{focus_z}.jpeg"
    assert "large_patch_diagnostics" not in row["peripheral_search"]
    assert row["report_ref"].endswith("report.json")
    assert row["cumulative"]["rg_attempt_fields"] == 1
    assert row["cumulative"]["rg_mixed_captures"] == 1
    assert row["cumulative"]["peripheral_attempt_fields"] == 1
    assert row["cumulative"]["peripheral_used_fields"] == int(not white)
    assert row["cumulative"]["white_fallback_fields"] == int(white)
    assert scheduler.anchors == ([] if white else [FocusAnchor(10, 20, focus_z)])
    workflow._rg_focus.autofocus_with_white_fallback_for_scan.assert_not_called()


def test_white_degraded_capture_has_no_rg_or_peripheral_attempt(
    sparse_journal_workflow,
):
    """The degraded branch actually invokes WHITE, not another simultaneous probe."""
    workflow, settings, scheduler, journal = sparse_journal_workflow
    scheduler._initial_row_y_units = 0
    scheduler._recovery_anchors = 3
    decision = scheduler.prepare(10, 20, 7, 7)
    assert decision.white_only
    assert workflow.preferred_camera_mode(settings) == "default"
    workflow._rg_focus.autofocus_white_sparse_fallback_for_scan.return_value = {
        "status": "focused",
        "focus_method": "white_fallback",
        "sparse_degraded": True,
        "fallback_count": 1,
        "mixed_capture_count": 0,
        "simultaneous_attempted": False,
        "rg_attempted": False,
        "final_position_units": [10, 20, 12],
        "report_ref": "rg_focus/degraded/report.json",
        "peripheral_search": None,
    }
    assert workflow.acquisition_routine(settings, (10, 20, 7)) == (True, 12, 1)
    workflow._rg_simultaneous.autofocus_for_scan.assert_not_called()
    workflow._rg_focus.autofocus_white_sparse_fallback_for_scan.assert_called_once()
    reason = (
        workflow._rg_focus.autofocus_white_sparse_fallback_for_scan.call_args.kwargs[
            "reason"
        ]
    )
    assert decision.estimate.reason in reason
    assert not scheduler.anchors
    row = json.loads(journal.read_text())
    assert row["cumulative"]["white_degraded_fields"] == 1
    assert row["cumulative"]["white_fallback_fields"] == 1
    assert row["cumulative"]["rg_probe_fields"] == 0
    assert row["cumulative"]["rg_attempt_fields"] == 0
    assert row["cumulative"]["rg_mixed_captures"] == 0
    assert row["cumulative"]["peripheral_attempt_fields"] == 0


def test_prediction_and_background_journal_do_not_invent_focus(sparse_journal_workflow):
    """Predictions and skipped fields neither train nor duplicate earlier RG telemetry."""
    workflow, settings, scheduler, journal = sparse_journal_workflow
    scheduler.anchors.extend(
        FocusAnchor(x, y, 7) for x, y in ((0, 0), (1000, 0), (0, 1000), (1000, 1000))
    )
    assert not scheduler.prepare(500, 500, 7, 7).requires_anchor
    assert workflow.acquisition_routine(settings, (500, 500, 7)) == (True, None, 1)
    scheduler.prepare(600, 500, 7, 7)
    workflow._background_detector.image_is_sample.return_value = (False, "background")
    assert workflow.acquisition_routine(settings, (600, 500, 7)) == (False, None, 0)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert [row["field_index"] for row in rows] == [0, 1]
    assert [row["focus_method"] for row in rows] == ["prediction", "background"]
    assert rows[1]["cumulative"]["prediction_fields"] == 1
    assert rows[1]["cumulative"]["background_fields"] == 1
    assert rows[1]["cumulative"]["rg_attempt_fields"] == 0
    assert len(scheduler.anchors) == 4


@pytest.mark.parametrize(
    "error", [ValueError("reference lost"), OSError("camera"), KeyboardInterrupt()]
)
def test_focus_hardware_failure_is_journalled_but_never_retried_or_white_fallback(
    sparse_journal_workflow, error
):
    """Only owner-classified measurement refusals permit WHITE, never broad exceptions."""
    workflow, settings, scheduler, journal = sparse_journal_workflow
    scheduler.prepare(10, 20, 7, 7)
    workflow._rg_simultaneous.autofocus_for_scan.side_effect = error
    with pytest.raises(type(error)) as caught:
        workflow.acquisition_routine(settings, (10, 20, 7))
    assert caught.value is error
    workflow._rg_focus.autofocus_white_sparse_fallback_for_scan.assert_not_called()
    workflow._cam.capture_and_save_to_path.assert_not_called()
    assert not scheduler.anchors
    row = json.loads(journal.read_text())
    assert row["outcome"] == "failed"
    assert row["cumulative"]["failed_fields"] == 1
    assert row["cumulative"]["rg_attempt_unknown_fields"] == 1
    assert row["cumulative"]["white_fallback_fields"] == 0


def test_capture_failure_retains_rg_result_but_does_not_complete_anchor(
    sparse_journal_workflow,
):
    """Recording the measured source precedes tile capture; adding support follows it."""
    workflow, settings, scheduler, journal = sparse_journal_workflow
    scheduler.prepare(10, 20, 7, 7)
    workflow._rg_simultaneous.autofocus_for_scan.return_value = {
        "status": "focused",
        "focus_method": "rg_simultaneous",
        "fallback_count": 0,
        "mixed_capture_count": 1,
        "report_ref": "rg_simultaneous/measured/focus-report.json",
        "final_position_units": [10, 20, 9],
        "peripheral_search": {"attempted": True, "used": True},
    }
    workflow._cam.capture_and_save_to_path.side_effect = OSError("disk write")
    with pytest.raises(OSError, match="disk write"):
        workflow.acquisition_routine(settings, (10, 20, 7))
    row = json.loads(journal.read_text())
    assert row["report_ref"] == "rg_simultaneous/measured/focus-report.json"
    assert row["cumulative"]["rg_attempt_fields"] == 1
    assert row["cumulative"]["peripheral_used_fields"] == 1
    assert row["cumulative"]["captured_fields"] == 0
    assert not scheduler.anchors


def test_journal_cannot_overwrite_existing_run(sparse_journal_workflow):
    """Reinitialising an existing scan is an explicit error, not truncated telemetry."""
    workflow, settings, _scheduler, _journal = sparse_journal_workflow
    with pytest.raises(FileExistsError):
        workflow._start_focus_telemetry(settings.capture_params.images_dir)


def test_cchip_acquisition_returns_stack_image_count(cchip_workflow, mocker):
    """Check cchip acquisition returns the number of images in the basic stack param."""
    # Mocking for settings as above
    mock_settings = mocker.Mock()
    mock_settings.focus_strategy = "smart_stack"
    mock_settings.skip_background = True
    mock_settings.stack_params = StackParams(
        stack_dz=5,
        images_to_save=7,
        min_images_to_test=7,
        save_on_failure=False,
        check_turning_points=True,
    )

    imaged, focus_height, image_count = cchip_workflow.acquisition_routine(
        mock_settings, xyz_pos=(1, 2, 3)
    )

    assert imaged
    assert focus_height == 3
    assert image_count == 7


def test_fast_ofm_workflow_settings_ui(fast_ofm_workflow, mocker):
    """Check that the workflow specifies the expected controls."""
    mock_prop_control = mocker.Mock(spec=PropertyControl)
    fast_ofm_workflow._background_detector.settings_ui.return_value = UIElementList(
        [mock_prop_control]
    )

    ui = fast_ofm_workflow.settings_ui().root

    assert len(ui) == 2
    assert all(isinstance(element, Container) for element in ui)
    assert ui[0].css_class == "fast-ofm-wizard-essential"
    essential_ui = ui[0].children.root
    assert [control.property_name for control in essential_ui] == [
        "max_range",
        "autofocus_method",
        "skip_background",
    ]

    assert ui[1].css_class == "fast-ofm-wizard-advanced"
    advanced_ui = ui[1].children.root
    assert isinstance(advanced_ui[0], HelpBlock)
    fragment = "This scan workflow is optimised for scanning H&E stained biopsies."
    assert fragment in advanced_ui[0].text
    assert advanced_ui[0].label == "About this workflow"
    assert isinstance(advanced_ui[1], Accordion)
    assert advanced_ui[1].title == "Area and path"
    assert isinstance(advanced_ui[2], Accordion)
    assert advanced_ui[2].title == "Focus details"
    assert isinstance(advanced_ui[3], Accordion)
    assert advanced_ui[3].title == "Background calibration"

    # Background UI is ...
    background_ui = advanced_ui[3].children.root
    assert background_ui[1] is mock_prop_control
    # plus boundary refinement and 2 action buttons
    assert len(background_ui) == 4
    assert isinstance(background_ui[2], ActionButton)
    assert background_ui[2].action == "set_background"
    assert isinstance(background_ui[3], ActionButton)
    assert background_ui[3].action == "check_background"

    # Finally check the contents of the scan settings accordion
    scan_settings = [
        *essential_ui,
        *advanced_ui[1].children.root,
        *advanced_ui[2].children.root,
        *advanced_ui[3].children.root,
    ]
    for element in scan_settings:
        assert (
            isinstance(element, (PropertyControl, HelpBlock, ActionButton))
            or element is mock_prop_control
        )

    names = [
        el.property_name
        for el in scan_settings
        if isinstance(el, PropertyControl) and el is not mock_prop_control
    ]
    expected_names = [
        "max_range",
        "autofocus_method",
        "skip_background",
        "overlap",
        "scan_pattern",
        "equal_distances",
        "rectangle_columns",
        "rectangle_rows",
        "focus_strategy",
        "stack_images_to_save",
        "stack_min_images_to_test",
        "stack_dz",
        "stack_extra_images",
        "stack_attempts",
        "stack_refocus_attempts",
        "stack_undershoot_images",
        "sparse_focus_enabled",
        "sparse_focus_anchor_interval",
        "autofocus_dz",
        "precheck_rg_tissue",
        "refine_background_boundaries",
    ]
    assert names == expected_names


@pytest.mark.parametrize(
    ("save_res", "expected_resize"),
    [
        ((1400, 1400), 1 / 2),
        ((1640, 1232), 1 / 2),
        ((760, 750), 1 / 1),
        ((1499, 1000), 1 / 2),
        ((2250, 1800), 1 / 3),
        ((700, 700), 1 / 1),
    ],
)
def test_correlation_resize(fast_ofm_workflow, save_res, expected_resize):
    """Test that scan_workflows chooses a suitable correlation_resize factor.

    correlation_resize is always a unit fraction (1/N, N integer) so that the
    downsampled image has an area roughly equal to TARGET_STITCHING_DIMENSION**2.
    This ensures stitching correlations are fast, robust, and avoid artefacts.

    The value is found by taking the image area (width * height), dividing by the
    target area, taking the square root, and rounding to the nearest integer N. Then
    correlation_resize = 1 / N.
    """
    fast_ofm_workflow.overlap = 0.1
    settings = fast_ofm_workflow._get_stitching_settings_model(save_res)

    assert isinstance(settings, StitchingSettings)
    assert settings.correlation_resize == expected_resize
    assert 0 < settings.correlation_resize <= 1
