"""Independent HTTP -> native snapshot -> native session acceptance boundary.

All calibration/optical values here are explicit fixtures, not commissioning
defaults. Configuration is effect-free. Session construction is allowed only
read-only HTTP preflight against the accepted fake controller, never real Pi.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflexure_microscope_server.focus.focus_scan import FocusScanSettings
from openflexure_microscope_server.scanning.scan_directories import ScanDirectory
from openflexure_microscope_server.things import RelativeDataPath
from tests.conftest import SIM_CONFIG
from tests.shared_utils.lt_test_utils import LabThingsTestEnv
from tests.unit_tests.test_focus_approach import focus_stage as _stage_fixture
from tests.unit_tests.test_focus_scan import enabled_settings
from tests.unit_tests.test_focus_scan_lifecycle import _workflow_owners


@pytest.fixture
def native_stage(mocker):
    """Keep actual stage logic, faking only controller HTTP as in accepted tests."""
    yield from _stage_fixture.__wrapped__(mocker)


@pytest.fixture
def configuration_server(tmp_path):
    """Use the real simulation topology with every write rooted in test temp."""
    config = json.loads(Path(SIM_CONFIG).read_text())
    with LabThingsTestEnv(
        things=config["things"],
        settings_folder=str(tmp_path / "settings"),
        application_config={
            **config["application_config"],
            "data_folder": str(tmp_path / "data"),
            "log_folder": str(tmp_path / "logs"),
        },
    ) as env:
        yield env


def _wire_optical_owners(workflow, stage, mocker, tmp_path):
    """Patch only existing Thing slots and fixed optical fixture endpoints."""
    owners, rg, _unused_settings = _workflow_owners(stage, mocker, tmp_path)
    csm = owners._csm
    cam = owners._cam
    csm.image_resolution = (32, 32)
    csm.image_to_stage_displacement_matrix = [[0, 1], [1, 0]]
    csm.convert_image_to_stage_coordinates.side_effect = lambda *, x, y: {
        "x": round(x),
        "y": round(y),
    }
    cam.capture_modes = {"standard": SimpleNamespace(save_resolution=(32, 32))}
    for slot, owner in (
        ("_stage", stage),
        ("_rg_focus", rg),
        ("_csm", csm),
        ("_cam", cam),
        ("_autofocus", owners._autofocus),
    ):
        mocker.patch.object(
            type(workflow), slot, property(lambda _self, value=owner: value)
        )
    # Native all_settings ordinarily captures one resolution image. Replace only
    # that unrelated optical read, not all_settings, validation or session logic.
    mocker.patch.object(workflow, "_get_save_resolution", return_value=(32, 32))
    for method in ("_capture_image", "capture_and_save_to_path", "grab_as_array"):
        getattr(cam, method).side_effect = AssertionError("Unexpected camera effect")
    for method in ("_capture_pair", "prepare_tissue_field_for_scan", "autofocus"):
        mocker.patch.object(
            rg, method, side_effect=AssertionError("Unexpected R/G acquisition")
        )
    return rg


@pytest.mark.parametrize(
    "workflow_name", ["snake_workflow", "raster_workflow", "fast_ofm_scan_workflow"]
)
def test_http_settings_reach_real_frozen_session(  # noqa: PLR0915
    configuration_server, native_stage, mocker, tmp_path, workflow_name
):
    """A complete HTTP setup creates a native frozen session immune to next edits."""
    env = configuration_server
    workflow = env.get_thing_by_name(workflow_name)
    stage, controller = native_stage
    rg = _wire_optical_owners(workflow, stage, mocker, tmp_path)
    sim_camera = env.get_thing_by_name("camera")
    sim_light = env.get_thing_by_name("illumination")
    camera_effect = mocker.spy(sim_camera, "_capture_image")
    light_effect = mocker.spy(sim_light, "set_led")
    bound = workflow._current_focus_binding()
    expected_profile = rg.checked_profile().model_dump(mode="json")
    expected_policy = rg.measurement_parameters.model_dump(mode="json")
    expected_control = rg.control_parameters.model_dump(mode="json")
    payload = enabled_settings(bound, white=True).model_dump(mode="json")
    payload["maximum_field_elapsed_s"] = 601.0
    payload["maximum_field_z_travel_um"] = 501.0
    payload["post_move_verification_reserve_s"] = 31.0
    base = workflow.path.rstrip("/")
    requests = []
    stage._client.event_hooks["request"].append(
        lambda request: requests.append((request.method, request.url.path))
    )
    scripts_before = list(controller.scripts)
    controller_before = copy.deepcopy(controller.status)

    for key, value in (
        ("capture_mode", "standard"),
        ("autofocus_method", "led"),
        ("focus_strategy", "single_autofocus"),
        ("autofocus_dz", 20),
        ("focus_scan", payload),
    ):
        response = env.client.put(f"{base}/{key}", json=value)
        assert response.status_code == 201, response.text
        assert env.client.get(f"{base}/{key}").json() == value
    if workflow_name == "fast_ofm_scan_workflow":
        response = env.client.put(f"{base}/skip_background", json=False)
        assert response.status_code == 201, response.text
    assert not requests  # Configuration does not even perform controller reads.
    assert camera_effect.call_count == light_effect.call_count == 0
    assert controller.scripts == scripts_before

    settings, _stitching, resolution = workflow.all_settings(
        RelativeDataPath("test-only/images")
    )
    assert settings.focus_scan.model_dump(mode="json") == payload
    assert settings.autofocus_method == "led"
    assert settings.focus_strategy == "single_autofocus"
    assert settings.autofocus_params.dz == 20
    assert settings.physical_geometry["available"]
    assert resolution == (32, 32)
    if workflow_name == "fast_ofm_scan_workflow":
        assert settings.skip_background is False

    directory = ScanDirectory.new_scan_dir(workflow_name, str(tmp_path / "scans"))
    session = workflow.new_focus_session(
        settings, scan_id=directory.name, evidence_writer=directory.save_focus_evidence
    )
    assert session is not None
    assert session.settings is not settings.focus_scan
    assert session.settings.model_dump(mode="json") == payload
    assert (
        session.frozen_rg.binding.model_dump(mode="json") == payload["run"]["binding"]
    )
    assert session.frozen_rg.profile.model_dump(mode="json") == expected_profile
    assert (
        session.frozen_rg.measurement_policy.model_dump(mode="json") == expected_policy
    )
    assert session.frozen_rg.control.model_dump(mode="json") == expected_control
    assert (
        session.policy.measurement_offset_um
        == payload["run"]["surface"]["measurement_offset_um"]
    )
    assert session.policy.expected_prediction_error_range_um == tuple(
        payload["expected_prediction_error_range_um"]
    )
    assert not session.observations
    assert requests  # Real current-binding checks perform native read-only preflight.
    assert all(method == "GET" for method, _path in requests)
    assert controller.scripts == scripts_before
    assert controller.status == controller_before
    manifest = json.loads(
        Path(directory.dir_path, "focus", "manifest.json").read_text()
    )
    assert manifest["settings"] == payload
    assert manifest["frozen_rg"] == session.frozen_rg.model_dump(mode="json")
    assert manifest["resume_allowed"] is False

    frozen_before = session.frozen_rg.model_dump(mode="json")
    summary_before = session.runtime_summary
    map_before = session.observations
    requests.clear()
    next_payload = copy.deepcopy(payload)
    next_payload["maximum_field_elapsed_s"] = 602.0
    next_payload["run"]["surface"]["neighbor_radius_um"] += 1.0
    response = env.client.put(f"{base}/focus_scan", json=next_payload)
    assert response.status_code == 201, response.text
    assert env.client.get(f"{base}/focus_scan").json() == next_payload
    next_settings, _stitching, _resolution = workflow.all_settings(
        RelativeDataPath("test-only/next-images")
    )
    assert next_settings.focus_scan.model_dump(mode="json") == next_payload
    response = env.client.put(f"{base}/focus_scan", json={})
    assert response.status_code == 201, response.text
    assert env.client.get(
        f"{base}/focus_scan"
    ).json() == FocusScanSettings().model_dump(mode="json")
    assert session.settings.model_dump(mode="json") == payload
    assert settings.focus_scan.model_dump(mode="json") == payload
    assert session.frozen_rg.model_dump(mode="json") == frozen_before
    assert session.runtime_summary == summary_before
    assert session.observations is map_before
    assert not session.observations  # No fields were run; not an AF/map-learning test.
    assert not requests
    assert controller.scripts == scripts_before
    assert controller.status == controller_before
    assert camera_effect.call_count == light_effect.call_count == 0
    assert not env.client.get("/action_invocations").json()
