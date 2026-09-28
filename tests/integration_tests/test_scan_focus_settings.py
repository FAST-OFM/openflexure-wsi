"""Exercise scan focus properties through the real HTTP validation boundary."""

import copy

import pytest

from openflexure_microscope_server.things import RelativeDataPath
from tests.unit_tests.test_focus_approach import binding
from tests.unit_tests.test_focus_scan import enabled_settings


def _enabled_payload(*, white=True):
    """Return explicit test-only values, never deployment or commissioning defaults."""
    return enabled_settings(binding(), white=white).model_dump(mode="json")


def test_focus_settings_http_roundtrip(simulation_test_env):
    """JSON enum choices and numeric budgets save without any scan action."""
    env = simulation_test_env
    for name in ("fast_ofm_scan_workflow", "snake_workflow", "raster_workflow"):
        workflow = env.get_thing_by_name(name)
        base = workflow.path.rstrip("/")
        for strategy in ("single_autofocus", "smart_stack", "single_autofocus"):
            response = env.client.put(f"{base}/focus_strategy", json=strategy)
            assert response.status_code == 201, response.text
            assert env.client.get(f"{base}/focus_strategy").json() == strategy

        response = env.client.put(f"{base}/focus_strategy", json="unknown")
        assert response.status_code == 422
        assert env.client.get(f"{base}/focus_strategy").json() == "single_autofocus"

        for method in (
            "none",
            "openflexure",
            "led",
            "simultaneous_rg",
            "openflexure",
        ):
            response = env.client.put(f"{base}/autofocus_method", json=method)
            assert response.status_code == 201, response.text
            assert env.client.get(f"{base}/autofocus_method").json() == method
        response = env.client.put(f"{base}/autofocus_method", json="unknown")
        assert response.status_code == 422
        assert env.client.get(f"{base}/autofocus_method").json() == "openflexure"

        response = env.client.put(f"{base}/stack_attempts", json=1)
        assert response.status_code == 201, response.text
        assert env.client.get(f"{base}/stack_attempts").json() == 1
        assert env.client.put(f"{base}/stack_attempts", json=0).status_code == 422
        assert env.client.get(f"{base}/stack_attempts").json() == 1
    assert env.get_thing_client("stage").position == {"x": 0, "y": 0, "z": 0}


def test_fast_ofm_scan_pattern_http_roundtrip(simulation_test_env):
    """The operator can choose a frozen deterministic snake without any motion."""
    workflow = simulation_test_env.get_thing_by_name("fast_ofm_scan_workflow")
    base = workflow.path.rstrip("/")
    assert (
        simulation_test_env.client.put(
            f"{base}/scan_pattern", json="rectangle_snake"
        ).status_code
        == 201
    )
    assert (
        simulation_test_env.client.get(f"{base}/scan_pattern").json()
        == "rectangle_snake"
    )
    assert (
        simulation_test_env.client.put(
            f"{base}/scan_pattern", json="spiral"
        ).status_code
        == 422
    )
    assert (
        simulation_test_env.client.get(f"{base}/scan_pattern").json()
        == "rectangle_snake"
    )


def test_focus_scan_http_to_frozen_workflow_roundtrip_has_no_config_effects(
    simulation_test_env, mocker
):
    """One complete PUT/GET reaches each native workflow snapshot without effects."""
    env = simulation_test_env
    camera = env.get_thing_by_name("camera")
    stage = env.get_thing_by_name("stage")
    illumination = env.get_thing_by_name("illumination")
    capture = mocker.spy(camera, "_capture_image")
    move = mocker.spy(stage, "_hardware_move_relative")
    start_move = mocker.spy(stage, "_hardware_start_move_relative")
    light = mocker.spy(illumination, "set_led")
    start_position = dict(stage.position)
    payload = _enabled_payload()

    for name in ("snake_workflow", "raster_workflow", "fast_ofm_scan_workflow"):
        workflow = env.get_thing_by_name(name)
        base = workflow.path.rstrip("/")
        assert env.client.put(f"{base}/autofocus_method", json="led").status_code == 201
        assert (
            env.client.put(
                f"{base}/focus_strategy", json="single_autofocus"
            ).status_code
            == 201
        )
        if name == "fast_ofm_scan_workflow":
            assert (
                env.client.put(f"{base}/skip_background", json=False).status_code == 201
            )
        assert env.client.put(f"{base}/focus_scan", json=payload).status_code == 201
        assert env.client.get(f"{base}/focus_scan").json() == payload

        # Explicitly test only the native snapshot builder. Synthetic optics avoid
        # its unrelated resolution capture; configuration itself stays effect-free.
        mocker.patch.object(workflow, "_get_save_resolution", return_value=(640, 480))
        mocker.patch.object(
            workflow, "_calc_displacement_from_overlap", return_value=(10, 20)
        )
        settings, _stitching, resolution = workflow.all_settings(
            RelativeDataPath("test-only/images")
        )
        frozen = settings.focus_scan.model_copy(deep=True)
        assert frozen.model_dump(mode="json") == payload
        assert resolution == (640, 480)

        reset = frozen.model_copy(
            update={
                "run": frozen.run.model_copy(
                    update={
                        "surface": frozen.run.surface.model_copy(
                            update={"enabled": False}
                        ),
                        "unpredicted_focus_mode": "selected_method",
                        "white_search": None,
                        "binding": None,
                    }
                )
            }
        ).model_dump(mode="json")
        assert env.client.put(f"{base}/focus_scan", json=reset).status_code == 201
        assert frozen.run.surface.enabled
        assert frozen.run.binding == enabled_settings(binding()).run.binding

    assert dict(stage.position) == start_position
    assert capture.call_count == 0
    assert move.call_count == 0
    assert start_move.call_count == 0
    assert light.call_count == 0


def _delete_neighbor(payload):
    del payload["run"]["surface"]["neighbor_radius_um"]


def _invalid_neighbor(payload):
    payload["run"]["surface"]["neighbor_radius_um"] = "invalid"


def _nan_neighbor(payload):
    # JSON has no numeric NaN token. Exercise a string spelling over valid JSON;
    # native float NaN/Infinity are covered by test_surface_limits_are_finite.
    payload["run"]["surface"]["neighbor_radius_um"] = "NaN"


def _boolean_neighbor(payload):
    payload["run"]["surface"]["neighbor_radius_um"] = True


def _reversed_envelope(payload):
    payload["experiment_envelope"]["x_um"] = [5, -5]


def _coupled_neighbors(payload):
    payload["run"]["surface"]["minimum_plane_points"] = 8
    payload["run"]["surface"]["maximum_neighbors"] = 4


def _coupled_white_budget(payload):
    payload["run"]["white_search"]["white_search_timeout_s"] = 450
    payload["run"]["white_search"]["approach_and_rg_reserve_s"] = 100
    payload["run"]["white_search"]["total_focus_budget_s"] = 500


@pytest.mark.parametrize(
    "mutate",
    [
        _delete_neighbor,
        _invalid_neighbor,
        _nan_neighbor,
        _boolean_neighbor,
        _reversed_envelope,
        _coupled_neighbors,
        _coupled_white_budget,
    ],
)
def test_invalid_complete_focus_put_is_atomic(simulation_test_env, mutate):
    """Missing/type/nonfinite/order/coupled failures preserve the old typed value."""
    env = simulation_test_env
    workflow = env.get_thing_by_name("snake_workflow")
    base = workflow.path.rstrip("/")
    original = _enabled_payload()
    assert env.client.put(f"{base}/focus_scan", json=original).status_code == 201
    invalid = copy.deepcopy(original)
    mutate(invalid)
    response = env.client.put(f"{base}/focus_scan", json=invalid)
    assert response.status_code == 422, response.text
    assert env.client.get(f"{base}/focus_scan").json() == original
