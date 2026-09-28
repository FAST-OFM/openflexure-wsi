"""Tests for GPL scan-state coordination around an injected predictor."""

import pytest

from openflexure_microscope_server.focus.sparse_focus import (
    FocusAnchor,
    FocusEstimate,
    SparseFocusScheduler,
    SparseFocusSettings,
)


def _refused(*_args, **_kwargs) -> FocusEstimate:
    return FocusEstimate(False, None, "none", "external prediction refused")


def _flat_prediction(anchors, **_kwargs) -> FocusEstimate:
    if len(anchors) < 4:
        return _refused()
    return FocusEstimate(True, 7.0, "plane", "validated external surface", 4)


def _scheduler(predictor=_refused, **settings) -> SparseFocusScheduler:
    return SparseFocusScheduler(
        SparseFocusSettings(enabled=True, **settings),
        x_um_per_unit=1.0,
        y_um_per_unit=1.0,
        z_um_per_unit=1.0,
        predictor=predictor,
    )


def test_scheduler_passes_only_measured_anchors_to_external_predictor() -> None:
    """Predicted heights never feed themselves back into measured support."""
    calls = []

    def predictor(anchors, **kwargs):
        calls.append((anchors, kwargs))
        return _flat_prediction(anchors, **kwargs)

    scheduler = _scheduler(predictor)
    for x, y in ((0, 0), (1000, 0), (0, 1000), (1000, 1000)):
        assert scheduler.prepare(x, y, 7, 7).requires_anchor
        scheduler.complete_anchor(7)
    measured = tuple(scheduler.anchors)
    decision = scheduler.prepare(500, 500, 7, 7)
    assert not decision.requires_anchor
    assert decision.target_z_units == 7
    scheduler.complete_prediction()
    assert tuple(scheduler.anchors) == measured
    assert calls[-1][0] == measured
    assert calls[-1][1]["target_x_um"] == 500


def test_background_does_not_advance_probe_cadence() -> None:
    """A skipped field cannot consume an R/G cadence position."""
    scheduler = _scheduler(_flat_prediction, anchor_interval=2)
    scheduler.anchors.extend(
        FocusAnchor(x, y, 7) for x, y in ((0, 0), (1000, 0), (0, 1000), (1000, 1000))
    )
    assert not scheduler.prepare(500, 500, 7, 7).requires_anchor
    scheduler.skip_background()
    assert scheduler.fields_since_rg_probe == 0
    assert not scheduler.prepare(500, 500, 7, 7).requires_anchor
    scheduler.complete_prediction()
    assert scheduler.prepare(500, 500, 7, 7).requires_anchor


def test_refused_surface_enters_bounded_white_mode() -> None:
    """Three unsuccessful row probes degrade to WHITE, then retry by cadence."""
    scheduler = _scheduler(maximum_recovery_anchors=3)
    for x in range(4):
        scheduler.prepare(x, 0, 0, 0)
        scheduler.complete_anchor(0)
    for x in range(3):
        decision = scheduler.prepare(x, 1, 0, 0)
        assert decision.requires_anchor
        assert not decision.white_only
        scheduler.complete_anchor(0)
    decision = scheduler.prepare(3, 1, 0, 23)
    assert decision.requires_anchor
    assert decision.white_only
    assert decision.target_z_units == 0
    scheduler.complete_white_fallback(rg_attempted=False)
    assert scheduler.white_fallback_fields == 1


def test_failed_rg_warmup_can_degrade_without_any_false_anchor() -> None:
    """Failed probes can degrade to WHITE without inventing support."""
    scheduler = _scheduler(maximum_recovery_anchors=3)
    for x in range(4):
        scheduler.prepare(x, 0, 0, 0)
        scheduler.complete_white_fallback(rg_attempted=True)
    for x in range(3):
        scheduler.prepare(x, 1, 0, 0)
        scheduler.complete_white_fallback(rg_attempted=True)
    assert scheduler.prepare(3, 1, 0, 0).white_only
    assert not scheduler.anchors


def test_valid_surface_resumes_immediately_after_white_degradation() -> None:
    """A later valid core estimate clears the degraded execution path."""
    estimates = iter(
        [
            FocusEstimate(False, None, "plane", "invalid"),
            FocusEstimate(True, 9.0, "plane", "valid", 4),
        ]
    )
    scheduler = _scheduler(
        lambda *_args, **_kwargs: next(estimates), maximum_recovery_anchors=3
    )
    scheduler.anchors.extend(
        FocusAnchor(x, y, 7) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))
    )
    scheduler._initial_row_y_units = 0
    scheduler._recovery_anchors = 3
    assert scheduler.prepare(0, 2, 7, 7).white_only
    scheduler.complete_white_fallback(rg_attempted=False)
    decision = scheduler.prepare(1, 2, 7, 7)
    assert not decision.requires_anchor
    assert decision.target_z_units == 9


@pytest.mark.parametrize("source", ["white_fallback", "prediction", "unknown", "rg"])
def test_non_rg_source_cannot_be_inserted_as_anchor(source: str) -> None:
    """Only a measured simultaneous R/G result may become support."""
    scheduler = _scheduler()
    scheduler.prepare(0, 0, 0, 0)
    with pytest.raises(ValueError, match="Only measured simultaneous RG"):
        scheduler.complete_anchor(-100, focus_method=source)
    assert not scheduler.anchors


def test_pending_decision_must_be_completed_before_next_request() -> None:
    """Per-field decisions remain immutable until a terminal outcome."""
    scheduler = _scheduler()
    scheduler.prepare(0, 0, 0, 0)
    with pytest.raises(RuntimeError, match="still pending"):
        scheduler.prepare(1, 0, 0, 0)


def test_invalid_recovery_limit_is_rejected() -> None:
    """Zero cannot ambiguously mean either disabled or immediately degraded."""
    with pytest.raises(ValueError, match="Recovery anchor limit"):
        SparseFocusSettings(maximum_recovery_anchors=0)
