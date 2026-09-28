"""Run-scoped acquisition strategy and bounded smart-stack budgets."""

from unittest.mock import MagicMock

import numpy as np
import pytest
from pydantic import ValidationError

from labthings_fastapi.testing import create_thing_without_server

from openflexure_microscope_server.focus.sparse_focus import (
    FocusAnchor,
    FocusEstimate,
    SparseFocusScheduler,
)
from openflexure_microscope_server.things import RelativeDataPath
from openflexure_microscope_server.things.camera import CaptureParams
from openflexure_microscope_server.things.focus.autofocus import (
    AutofocusParams,
    AutofocusThing,
    NoFocusFoundError,
    SmartStackParams,
)
from openflexure_microscope_server.things.scanning.scan_workflows import (
    FastOFMWorkflow,
    RasterWorkflow,
    SnakeWorkflow,
)
from openflexure_microscope_server.things.stage.moonraker import (
    CompletedScanPreload,
    PreparedScanPreload,
)
from openflexure_microscope_server.ui import PropertyControl


def make_workflow(cls=FastOFMWorkflow):
    """Use mock hardware; no microscope requests are possible."""
    w = create_thing_without_server(cls, mock_all_slots=True)
    w._stage.get_xyz_position.return_value = (10, 20, 7)
    w._cam.stream_active = True
    w._cam.streaming_mode = "default"
    w._rg_focus.calibration_status = {
        "status": "candidate",
        "reason": "two-sided approach failed",
    }
    if cls is FastOFMWorkflow:
        w._background_detector.image_is_sample.return_value = (True, "sample")
        w._rg_focus.precheck_scan_white.return_value = {
            "status": "ready",
            "reason": "Fresh WHITE tissue field is ready",
        }
    return w


def snapshot(w):
    """Use the same run-settings builder used by all_settings."""
    return w._build_scan_settings(
        {
            "overlap": 0.35,
            "dx": 580,
            "dy": -578,
            "capture_params": CaptureParams(
                images_dir=RelativeDataPath("scan/images"),
                capture_mode="standard",
            ),
            "autofocus_params": AutofocusParams(dz=50),
        }
    )


def _test_focus_predictor(anchors, **_kwargs):
    """Stand in for the process boundary; core mathematics has its own suite."""
    if len(anchors) < 4:
        return FocusEstimate(False, None, "none", "insufficient measured support")
    return FocusEstimate(True, anchors[-1].z_um, "plane", "validated test surface", 4)


def test_rectangle_freezes_prediction_recovery_budget():
    """The rectangle stops all-AF degradation without changing spiral policy."""
    w = make_workflow()
    w.scan_pattern = "rectangle_snake"
    settings = snapshot(w)
    assert settings.sparse_focus.maximum_recovery_anchors == 3
    w.scan_pattern = "tissue_spiral"
    assert snapshot(w).sparse_focus.maximum_recovery_anchors is None
    assert settings.sparse_focus.maximum_recovery_anchors == 3


@pytest.mark.parametrize("cls", [FastOFMWorkflow, SnakeWorkflow, RasterWorkflow])
def test_single_strategy_one_focus_one_image_no_startup_duplicate(cls):
    """The first field and subsequent fields each receive exactly one AF."""
    w = make_workflow(cls)
    w.focus_strategy = "single_autofocus"
    settings = snapshot(w)
    w.pre_scan_routine(settings)
    w._autofocus.looping_autofocus.assert_not_called()
    w._autofocus.fast_autofocus.assert_not_called()
    assert w.acquisition_routine(settings, (10, 20, 0)) == (True, 7, 1)
    w._autofocus.fast_autofocus.assert_called_once_with(dz=50)
    w._autofocus.run_smart_stack.assert_not_called()
    capture = w._cam.capture_and_save_to_path.call_args.kwargs
    assert capture["path"].root == "scan/images/img_10_20_7.jpeg"
    assert capture["capture_mode"] == "standard"
    w.acquisition_routine(settings, (10, 598, 7))
    assert w._autofocus.fast_autofocus.call_count == 2
    assert w._cam.capture_and_save_to_path.call_count == 2


@pytest.mark.parametrize("cls", [FastOFMWorkflow, SnakeWorkflow, RasterWorkflow])
def test_none_method_never_calls_either_autofocus_handler(cls):
    """The frozen none selection captures at current Z without any AF call."""
    w = make_workflow(cls)
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "none"
    settings = snapshot(w)
    assert settings.autofocus_method == "none"
    assert w.acquisition_routine(settings, (10, 20, 0)) == (True, 7, 1)
    w._autofocus.fast_autofocus.assert_not_called()
    w._autofocus.looping_autofocus.assert_not_called()
    w._autofocus.run_smart_stack.assert_not_called()
    w._rg_focus.autofocus.assert_not_called()


def test_led_method_dispatches_explicit_hybrid_action_and_uses_its_failure():
    """The no-surface scan delegates one recorded WHITE fallback to the R/G owner."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "led"
    w._rg_focus.autofocus_with_white_fallback_for_scan.return_value = {
        "status": "focused"
    }
    settings = snapshot(w)
    assert w.acquisition_routine(settings, (10, 20, 0)) == (True, 7, 1)
    w._rg_focus.autofocus_with_white_fallback_for_scan.assert_called_once_with(
        white_dz=50
    )
    w._autofocus.fast_autofocus.assert_not_called()
    w._rg_focus.autofocus_with_white_fallback_for_scan.side_effect = ValueError(
        "WHITE fallback failed"
    )
    with pytest.raises(ValueError, match="WHITE fallback failed"):
        w.acquisition_routine(settings, (10, 20, 0))
    w._autofocus.fast_autofocus.assert_not_called()


def test_simultaneous_method_is_frozen_and_uses_one_explicit_fallback_chain():
    """The saved one-frame choice cannot drift back to editable scan settings."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "simultaneous_rg"
    w._rg_simultaneous.autofocus_for_scan.return_value = {
        "status": "focused",
        "focus_method": "rg_simultaneous",
        "fallback_count": 0,
    }
    settings = snapshot(w)
    w.autofocus_method = "openflexure"

    assert settings.autofocus_method == "simultaneous_rg"
    assert w.acquisition_routine(settings, (10, 20, 0)) == (True, 7, 1)
    w._rg_simultaneous.autofocus_for_scan.assert_called_once_with(
        white_dz=50,
        scan_preload=None,
    )
    w._rg_focus.autofocus_with_white_fallback_for_scan.assert_not_called()
    w._autofocus.fast_autofocus.assert_not_called()


def test_simultaneous_scan_completes_preload_before_background_and_reuses_result():
    """The field performs only the final Z approach before tissue check and R/G."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "simultaneous_rg"
    prepared = MagicMock(spec=PreparedScanPreload)
    completed = MagicMock(spec=CompletedScanPreload)
    events: list[str] = []
    w.complete_scan_preload = MagicMock(
        side_effect=lambda _value: events.append("approach") or completed
    )
    w._cam.grab_as_array.side_effect = lambda **_kwargs: (
        events.append("background") or np.zeros((760, 1014, 3), dtype=np.uint8)
    )
    w._rg_simultaneous.autofocus_for_scan.side_effect = lambda **_kwargs: (
        events.append("focus")
        or {
            "status": "focused",
            "focus_method": "rg_simultaneous",
            "fallback_count": 0,
            "final_position_units": [10, 20, 7],
        }
    )
    settings = snapshot(w)

    assert w.acquisition_routine(settings, (10, 20, 7), scan_preload=prepared) == (
        True,
        7,
        1,
    )

    assert events[:3] == ["approach", "background", "focus"]
    w.complete_scan_preload.assert_called_once_with(prepared)
    w._rg_simultaneous.autofocus_for_scan.assert_called_once_with(
        white_dz=50,
        scan_preload=completed,
    )
    w._stage.get_xyz_position.assert_not_called()


def test_sparse_simultaneous_anchor_is_measured_and_saved_as_support():
    """A scheduled anchor runs real R/G focus and records its final measured Z."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "simultaneous_rg"
    w.sparse_focus_enabled = True
    settings = snapshot(w)
    scheduler = SparseFocusScheduler(
        settings.sparse_focus,
        x_um_per_unit=1.0,
        y_um_per_unit=1.0,
        z_um_per_unit=1.0,
        predictor=_test_focus_predictor,
    )
    w._sparse_focus_scheduler = scheduler
    decision = scheduler.prepare(10, 20, 5, 5)
    assert decision.requires_anchor
    w._rg_simultaneous.autofocus_for_scan.return_value = {
        "status": "focused",
        "focus_method": "rg_simultaneous",
        "fallback_count": 0,
        "final_position_units": [10, 20, 7],
    }

    assert w.acquisition_routine(settings, (10, 20, 5)) == (True, 7, 1)
    assert scheduler.anchor_fields == 1
    assert scheduler.anchors == [FocusAnchor(10.0, 20.0, 7.0)]
    w._rg_focus.precheck_scan_white.assert_not_called()


def test_sparse_simultaneous_prediction_skips_blink_precheck_and_autofocus():
    """A bounded prediction saves one WHITE tile without any R/G hardware call."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "simultaneous_rg"
    w.sparse_focus_enabled = True
    settings = snapshot(w)
    scheduler = SparseFocusScheduler(
        settings.sparse_focus,
        x_um_per_unit=1.0,
        y_um_per_unit=1.0,
        z_um_per_unit=1.0,
        predictor=_test_focus_predictor,
    )
    scheduler.anchors.extend(
        [
            FocusAnchor(0.0, 0.0, 7.0),
            FocusAnchor(1000.0, 0.0, 7.0),
            FocusAnchor(0.0, 1000.0, 7.0),
            FocusAnchor(1000.0, 1000.0, 7.0),
        ]
    )
    w._sparse_focus_scheduler = scheduler
    decision = scheduler.prepare(500, 500, 7, 7)
    assert not decision.requires_anchor
    completed = MagicMock(spec=CompletedScanPreload)
    w.complete_scan_preload = MagicMock(return_value=completed)

    prepared = MagicMock(spec=PreparedScanPreload)
    assert w.acquisition_routine(settings, (500, 500, 7), scan_preload=prepared) == (
        True,
        None,
        1,
    )
    assert scheduler.predicted_fields == 1
    assert len(scheduler.anchors) == 4
    w.complete_scan_preload.assert_called_once_with(prepared)
    w._rg_focus.precheck_scan_white.assert_not_called()
    w._rg_simultaneous.autofocus_for_scan.assert_not_called()
    w._autofocus.fast_autofocus.assert_not_called()
    w._cam.capture_and_save_to_path.assert_called_once()


def test_sparse_camera_mode_holds_full_stream_between_rg_anchors():
    """Only hardware anchors switch back to the calibrated default RAW mode."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "simultaneous_rg"
    w.sparse_focus_enabled = True
    settings = snapshot(w)
    scheduler = SparseFocusScheduler(
        settings.sparse_focus,
        x_um_per_unit=1.0,
        y_um_per_unit=1.0,
        z_um_per_unit=1.0,
        predictor=_test_focus_predictor,
    )
    w._sparse_focus_scheduler = scheduler

    scheduler.prepare(0, 0, 7, 7)
    assert w.preferred_camera_mode(settings) == "default"
    scheduler.complete_anchor(7)
    scheduler.anchors.extend(
        [
            FocusAnchor(1000.0, 0.0, 7.0),
            FocusAnchor(0.0, 1000.0, 7.0),
            FocusAnchor(1000.0, 1000.0, 7.0),
        ]
    )
    scheduler.prepare(500, 500, 7, 7)
    assert w.preferred_camera_mode(settings) == "full_resolution"


def test_sparse_preload_parameters_are_frozen_before_camera_mode_changes():
    """The calibrated approach is read once in default mode, before field movement."""
    w = make_workflow()
    w.sparse_focus_enabled = True
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "simultaneous_rg"
    settings = snapshot(w)
    scheduler = SparseFocusScheduler(
        settings.sparse_focus,
        x_um_per_unit=1.0,
        y_um_per_unit=1.0,
        z_um_per_unit=1.0,
        predictor=_test_focus_predictor,
    )
    w._sparse_focus_scheduler = scheduler
    w._sparse_scan_preload_parameters = None
    w._rg_simultaneous.scan_preload_parameters.return_value = (24, 1)

    assert w.prepare_scan_target_z(settings, (0, 0), 7, 7) == 7
    assert w._sparse_scan_preload_parameters == (24, 1)
    w._rg_simultaneous.scan_preload_parameters.assert_called_once_with()


def test_fast_ofm_led_precheck_routes_rejected_white_directly_to_one_white_focus():
    """A weak fresh Fast OFM frame avoids R/G capture without skipping the tile."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "led"
    frame = np.zeros((760, 1014, 3), dtype=np.uint8)
    w._cam.grab_as_array.return_value = frame
    precheck = {
        "status": "insufficient_tissue",
        "reason": "Coherent tissue support is too small for fixed common patches",
        "metrics": {"box_count": 0},
        "source": "fresh_fast_ofm_lores_white",
    }
    w._rg_focus.precheck_scan_white.return_value = precheck
    w._rg_focus.autofocus_white_precheck_for_scan.return_value = {
        "status": "focused",
        "focus_method": "white_precheck",
        "rg_attempted": False,
    }

    settings = snapshot(w)
    assert settings.precheck_rg_tissue is True
    assert w.acquisition_routine(settings, (10, 20, 0)) == (True, 7, 1)

    w._rg_focus.precheck_scan_white.assert_called_once_with(frame)
    w._rg_focus.autofocus_white_precheck_for_scan.assert_called_once_with(
        white_dz=50,
        precheck=precheck,
    )
    w._rg_focus.autofocus_with_white_fallback_for_scan.assert_not_called()
    w._autofocus.fast_autofocus.assert_not_called()
    w._cam.capture_and_save_to_path.assert_called_once()


def test_fast_ofm_led_precheck_selection_is_frozen_with_scan_settings():
    """Changing the next-run checkbox cannot alter an active scan's route."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "led"
    w.precheck_rg_tissue = False
    settings = snapshot(w)
    w.precheck_rg_tissue = True
    w._rg_focus.autofocus_with_white_fallback_for_scan.return_value = {
        "status": "focused"
    }

    assert w.acquisition_routine(settings, (10, 20, 0)) == (True, 7, 1)

    assert settings.precheck_rg_tissue is False
    w._rg_focus.precheck_scan_white.assert_not_called()
    w._rg_focus.autofocus_with_white_fallback_for_scan.assert_called_once_with(
        white_dz=50
    )


def test_led_standard_tile_restores_default_before_binding_and_observation():
    """The native standard capture transition is closed before accepting R/G."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "led"
    result = {"status": "focused", "id": "rg-result"}
    w._rg_focus.autofocus_for_scan.return_value = result
    field = MagicMock()
    field.no_tissue = False
    events = []

    field.before_main_capture.side_effect = lambda: events.append("before-capture")

    def validate(actual):
        assert actual == result
        events.append("rg-validated")

    def capture(**_kwargs):
        assert w._cam.streaming_mode == "default"
        events.append("capture-default")
        w._cam.streaming_mode = "full_resolution"
        events.append("capture-full-resolution")
        w._cam.streaming_mode = "default"
        events.append("capture-restored")

    def sync(name):
        assert name == "white_tile_capture"
        assert w._cam.streaming_mode == "default"
        events.append("binding-rechecked")

    def accept():
        assert events[-1] == "binding-rechecked"
        events.append("observation-accepted")

    w._cam.capture_and_save_to_path.side_effect = capture
    field.validate_rg_result.side_effect = validate
    field.sync_after_effect.side_effect = sync
    field.accept_rg_result.side_effect = accept
    assert w.acquisition_routine(snapshot(w), (10, 20, 0), field) == (True, 7, 1)
    assert events == [
        "rg-validated",
        "before-capture",
        "capture-default",
        "capture-full-resolution",
        "capture-restored",
        "binding-rechecked",
        "observation-accepted",
    ]
    w._autofocus.fast_autofocus.assert_not_called()
    field.session.stop.assert_not_called()


@pytest.mark.parametrize("failure", ["restore", "binding"])
def test_led_tile_restore_or_binding_failure_stops_before_observation(failure):
    """An unknown post-capture state cannot insert a map point or use fallback."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "led"
    w._rg_focus.autofocus_for_scan.return_value = {
        "status": "focused",
        "id": "pending-rg-result",
    }
    field = MagicMock()
    field.no_tissue = False

    def capture(**_kwargs):
        w._cam.streaming_mode = "full_resolution"
        if failure == "binding":
            w._cam.streaming_mode = "default"

    w._cam.capture_and_save_to_path.side_effect = capture
    if failure == "binding":
        field.sync_after_effect.side_effect = ValueError("fixture binding changed")
        match = "fixture binding changed"
    else:
        match = "was not restored"

    with pytest.raises((ValueError, RuntimeError), match=match):
        w.acquisition_routine(snapshot(w), (10, 20, 0), field)
    field.validate_rg_result.assert_called_once()
    field.accept_rg_result.assert_not_called()
    field.session.stop.assert_called_once()
    w._autofocus.fast_autofocus.assert_not_called()


@pytest.mark.parametrize("method", ["none", "led", "simultaneous_rg"])
def test_unsupported_smart_stack_method_refuses_before_handlers(method):
    """none/LED cannot enter stock smart-stack calls through an unsupported pairing."""
    w = make_workflow()
    with pytest.raises(RuntimeError, match="only single-image"):
        w._check_autofocus_method(method, "smart_stack")
    w._autofocus.looping_autofocus.assert_not_called()
    w._autofocus.run_smart_stack.assert_not_called()
    w._rg_focus.autofocus.assert_not_called()


def test_candidate_led_profile_refuses_before_any_focus_handler():
    """The actual saved candidate reason is surfaced before scan motion or capture."""
    w = make_workflow()
    w._rg_focus.checked_profile.side_effect = ValueError("candidate-only")
    with pytest.raises(RuntimeError, match="LED autofocus unavailable: candidate-only"):
        w._check_autofocus_method("led", "single_autofocus")
    w._autofocus.fast_autofocus.assert_not_called()
    w._rg_focus.autofocus.assert_not_called()


def test_unavailable_simultaneous_profile_refuses_before_focus_handler():
    """A scan cannot move while its selected one-frame profile is disabled or stale."""
    w = make_workflow()
    w._rg_simultaneous.checked_focus_profile.side_effect = ValueError("disabled")
    with pytest.raises(
        RuntimeError, match="Simultaneous R/G autofocus unavailable: disabled"
    ):
        w._check_autofocus_method("simultaneous_rg", "single_autofocus")
    w._rg_simultaneous.autofocus.assert_not_called()
    w._rg_focus.autofocus.assert_not_called()
    w._autofocus.fast_autofocus.assert_not_called()


def test_autofocus_method_is_frozen_even_if_form_changes_mid_scan():
    """Dispatch uses saved run settings, never the later editable Thing property."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w.autofocus_method = "openflexure"
    settings = snapshot(w)
    w.autofocus_method = "none"
    w.acquisition_routine(settings, (10, 20, 0))
    w._autofocus.fast_autofocus.assert_called_once_with(dz=50)
    w._rg_focus.autofocus.assert_not_called()


def test_single_focus_error_never_saves_or_falls_back():
    """A failed one-shot autofocus is not silently retried as a stack."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w._autofocus.fast_autofocus.side_effect = NoFocusFoundError("no focus")
    with pytest.raises(NoFocusFoundError):
        w.acquisition_routine(snapshot(w), (10, 20, 0))
    w._cam.capture_and_save_to_path.assert_not_called()
    w._autofocus.run_smart_stack.assert_not_called()
    w._autofocus.looping_autofocus.assert_not_called()


def test_real_background_skips_without_autofocus():
    """Only the background detector may classify a field as empty."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    w._background_detector.image_is_sample.return_value = (False, "background")
    assert w.acquisition_routine(snapshot(w), (0, 0, 0)) == (False, None, 0)
    w._autofocus.fast_autofocus.assert_not_called()
    w._cam.capture_and_save_to_path.assert_not_called()


@pytest.mark.parametrize("cls", [FastOFMWorkflow, SnakeWorkflow, RasterWorkflow])
def test_run_strategy_and_budgets_do_not_follow_later_form_changes(cls):
    """The scan stores its selected strategy and every retry budget."""
    w = make_workflow(cls)
    w.stack_attempts = 1
    w.stack_extra_images = 2
    w.stack_refocus_attempts = 2
    w.stack_undershoot_images = 0
    settings = snapshot(w)
    saved = settings.model_dump()
    assert saved["focus_strategy"] == "smart_stack"
    assert saved["smart_stack_params"]["max_images_to_test"] == 11
    w.focus_strategy = "single_autofocus"
    w.stack_attempts = 3
    w.stack_extra_images = 15
    w.stack_refocus_attempts = 10
    w.pre_scan_routine(settings)
    w._autofocus.looping_autofocus.assert_called_once_with(
        dz=50,
        start="centre",
        max_attempts=2,
    )
    w._autofocus.run_smart_stack.return_value = (True, 7, 1)
    assert w.acquisition_routine(settings, (10, 20, 0)) == (True, 7, 1)
    params = w._autofocus.run_smart_stack.call_args.kwargs["stack_parameters"]
    assert (
        params.max_attempts,
        params.max_images_to_test,
        params.refocus_max_attempts,
    ) == (1, 11, 2)
    assert params.img_undershoot == 0
    assert settings.model_dump() == saved
    w._autofocus.fast_autofocus.assert_not_called()


def test_conditional_controls_and_human_readable_choices():
    """Single mode hides irrelevant stack knobs; the API retains their values."""
    w = make_workflow()
    w.focus_strategy = "single_autofocus"
    controls = [
        c for c in w.focus_property_controls() if isinstance(c, PropertyControl)
    ]
    assert [c.property_name for c in controls] == [
        "autofocus_method",
        "focus_strategy",
    ]
    assert controls[0].options == {
        "Off": "none",
        "Standard (WHITE)": "openflexure",
        "R/G + fallback": "led",
        "Fast R/G + fallback": "simultaneous_rg",
    }
    assert controls[1].options == {
        "One focused image": "single_autofocus",
        "Z stack": "smart_stack",
    }
    w.focus_strategy = "smart_stack"
    names = {
        c.property_name
        for c in w.focus_property_controls()
        if isinstance(c, PropertyControl)
    }
    assert {
        "stack_extra_images",
        "stack_attempts",
        "stack_refocus_attempts",
        "stack_undershoot_images",
    } <= names


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stack_attempts", 0),
        ("stack_attempts", 4),
        ("stack_extra_images", -1),
        ("stack_extra_images", 16),
        ("stack_refocus_attempts", 0),
        ("stack_refocus_attempts", 11),
        ("stack_undershoot_images", -1),
        ("stack_undershoot_images", 6),
        ("focus_strategy", "unknown"),
    ],
)
def test_invalid_setting_rejected_without_hardware(field, value):
    """Configurable budgets remain within existing maximum bounds."""
    w = make_workflow()
    before = getattr(w, field)
    with pytest.raises(ValidationError):
        setattr(w, field, value)
    assert getattr(w, field) == before
    w._autofocus.fast_autofocus.assert_not_called()


def test_stack_parameters_validate_api_budget():
    """Direct smart-stack callers cannot bypass the declared retry range."""
    with pytest.raises(ValidationError):
        SmartStackParams(
            stack_dz=5, images_to_save=1, min_images_to_test=9, max_attempts=4
        )
    p = SmartStackParams(
        stack_dz=5, images_to_save=1, min_images_to_test=9, extra_images=0
    )
    assert p.max_images_to_test == 9


def test_explicit_loop_budget_does_not_mutate_global_default(mocker):
    """A scan-scoped one-attempt loop terminates, without changing manual AF settings."""
    af = create_thing_without_server(AutofocusThing, mock_all_slots=True)
    af._stage.position = {"x": 0, "y": 0, "z": 0}
    monitor = MagicMock()
    monitor.focus_rel.return_value = (0, 50)
    monitor.move_data.return_value = (
        [0, 1, 2],
        np.array([0, 25, 50]),
        np.array([3, 2, 1]),
    )
    context = mocker.patch(
        "openflexure_microscope_server.things.focus.autofocus.JPEGSharpnessMonitor"
    )
    context.return_value.__enter__.return_value = monitor
    with pytest.raises(NoFocusFoundError):
        af.looping_autofocus(dz=50, max_attempts=1)
    assert monitor.focus_rel.call_count == 1
    assert af.max_attempts == 10


def test_setting_persistence_and_deployment_default(tmp_path):
    """Saved operator strategy wins over deployment defaults on reload."""
    kwargs = {
        "mock_all_slots": True,
        "settings_folder": str(tmp_path),
        "default_settings": {"focus_strategy": "single_autofocus", "stack_attempts": 1},
    }
    w = create_thing_without_server(FastOFMWorkflow, **kwargs)
    w.load_settings()
    assert w.focus_strategy == "single_autofocus"
    w.focus_strategy = "smart_stack"
    w.stack_attempts = 2
    reloaded = create_thing_without_server(FastOFMWorkflow, **kwargs)
    reloaded.load_settings()
    assert reloaded.focus_strategy == "smart_stack"
    assert reloaded.stack_attempts == 2
