"""Process-boundary tests for the optional Fast OFM Core adapter."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from openflexure_microscope_server.focus.rg.rg_flat_field import FlatFieldSettings
from openflexure_microscope_server.focus.rg.rg_focus_estimator import (
    RGFocusEstimatorSettings,
)
from openflexure_microscope_server.focus.rg.rg_focus_model import (
    RGFocusMeasurementPolicy,
)
from openflexure_microscope_server.integrations.fast_ofm_core import (
    FastOFMCoreBlockingProcess,
    FastOFMCoreError,
    FastOFMCoreProcess,
)
from openflexure_microscope_server.integrations.fast_ofm_core.artifacts import (
    read_flat_field_result,
    write_flat_field_input,
    write_rg_measurement_artifacts,
    write_stitching_request,
)

from .test_rg_focus_estimator import (
    core_settings,
    field_settings,
    geometry,
    ready_field,
    references,
    shifted_pair,
    white_image,
)
from .test_rg_focus_model import empirical_profile

CORE_ROOT = Path(__file__).parents[3] / "fast-ofm-core"


def core_environment() -> dict[str, str]:
    """Expose only the standalone package to the child interpreter."""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(CORE_ROOT / "src")
    return environment


def test_capability_discovery_uses_a_separate_process() -> None:
    """Discover a live child without importing core implementation modules."""
    asyncio.run(_capability_discovery())


async def _capability_discovery() -> None:
    client = FastOFMCoreProcess(
        (sys.executable, "-m", "fast_ofm_core", "serve-jsonl"),
        environment=core_environment(),
    )
    try:
        result = await client.capabilities()
        assert result["protocol_versions"] == ["1.0"]
        assert "planning.route" in result["operations"]
        assert "rg.measure" in result["operations"]
        assert "calibration.fit" in result["operations"]
        assert "calibration.validate" in result["operations"]
    finally:
        await client.close()


def test_core_refusal_is_a_typed_adapter_error() -> None:
    """Preserve fail-closed refusal codes at the GPL adapter boundary."""
    asyncio.run(_core_refusal())


async def _core_refusal() -> None:
    client = FastOFMCoreProcess(
        (sys.executable, "-m", "fast_ofm_core", "serve-jsonl"),
        environment=core_environment(),
    )
    try:
        with pytest.raises(FastOFMCoreError) as captured:
            await client.request("rg.measure", {})
        assert captured.value.code == "INVALID_PAYLOAD"
        assert captured.value.retryable is False
    finally:
        await client.close()


def test_blocking_client_keeps_a_warm_separate_process(tmp_path: Path) -> None:
    """LabThings-style synchronous actions can share the same safe process."""
    client = FastOFMCoreBlockingProcess(
        (sys.executable, "-m", "fast_ofm_core", "serve-jsonl"),
        environment=core_environment(),
        artifact_roots=(tmp_path,),
    )
    try:
        first = client.capabilities()
        process = client._process
        second = client.capabilities()
        assert "rg.measure" in first["operations"]
        assert first == second
        assert client._process is process
    finally:
        client.close()


def test_missing_core_is_a_retryable_typed_error() -> None:
    """An optional-core deployment remains bootable and fails explicitly."""
    client = FastOFMCoreBlockingProcess(("/definitely/missing/fast-ofm-core",))

    with pytest.raises(FastOFMCoreError) as captured:
        client.capabilities()

    assert captured.value.code == "CORE_UNAVAILABLE"
    assert captured.value.retryable is True


def test_incompatible_protocol_fails_closed(tmp_path: Path) -> None:
    """A separately installed but incompatible core cannot be used silently."""
    service = tmp_path / "incompatible_core.py"
    service.write_text(
        """import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({
        \"protocol_version\": \"2.0\",
        \"request_id\": request[\"request_id\"],
        \"status\": \"completed\",
        \"result\": {},
    }), flush=True)
""",
        encoding="utf-8",
    )
    client = FastOFMCoreBlockingProcess((sys.executable, str(service)))
    try:
        with pytest.raises(FastOFMCoreError) as captured:
            client.capabilities()
        assert captured.value.code == "CORE_PROTOCOL_ERROR"
        assert "identity" in str(captured.value)
    finally:
        client.close()


def test_external_core_plans_the_exact_signed_grid() -> None:
    """The process boundary preserves the deployed snake's signed coordinates."""
    client = FastOFMCoreBlockingProcess(
        (sys.executable, "-m", "fast_ofm_core", "serve-jsonl"),
        environment=core_environment(),
    )
    try:
        response = client.request(
            "planning.route",
            {
                "traversal": "exact_grid_serpentine",
                "grid": {
                    "origin_um": {"x_um": 100.0, "y_um": 200.0},
                    "step_um": {"x_um": 10.0, "y_um": -20.0},
                    "columns": 3,
                    "rows": 2,
                },
            },
        )
        visits = [
            action["position_um"]
            for action in response["result"]["actions"]
            if action["kind"] != "component_start"
        ]
        assert visits == [
            {"x_um": 100.0, "y_um": 200.0},
            {"x_um": 110.0, "y_um": 200.0},
            {"x_um": 120.0, "y_um": 200.0},
            {"x_um": 120.0, "y_um": 180.0},
            {"x_um": 110.0, "y_um": 180.0},
            {"x_um": 100.0, "y_um": 180.0},
        ]
    finally:
        client.close()


def test_external_core_revalidates_profile_and_returns_residual_budget() -> None:
    """Persisted model evidence is rechecked outside the GPL package before use."""
    profile = empirical_profile()
    client = FastOFMCoreBlockingProcess(
        (sys.executable, "-m", "fast_ofm_core", "serve-jsonl"),
        environment=core_environment(),
    )
    try:
        response = client.request(
            "calibration.validate",
            {
                "profile": profile.model_dump(mode="json"),
                "focus_tolerance_um": 2.0,
            },
        )
        assert response["result"]["profile"] == profile.model_dump(mode="json")
        assert response["result"]["effective_residual_tolerance_um"] == pytest.approx(
            0.8
        )

        tampered = profile.model_dump(mode="json")
        tampered["empirical_observations"][0]["signed_cross_track_residual_px"] += 0.1
        with pytest.raises(FastOFMCoreError) as captured:
            client.request("calibration.validate", {"profile": tampered})
        assert captured.value.code == "INVALID_PROFILE"
        assert captured.value.retryable is False
    finally:
        client.close()


def test_gpl_adapter_freezes_a_verified_stitch_request(tmp_path: Path) -> None:
    """Final stitching sends hashes and coarse settings instead of Python objects."""
    for index in range(2):
        path = tmp_path / f"img_{index}_0_0.jpeg"
        Image.new("RGB", (8, 8), (index * 20, 0, 0)).save(path)

    request_path = write_stitching_request(
        tmp_path,
        output_name="scan_stitched",
        workers=3,
        cache_bytes=4 * 1024 * 1024 * 1024,
        registration_mode="full_correlation",
        minimum_overlap=0.14,
        correlation_resize=0.2,
        work_tile_size=8192,
        viewer_dzi=True,
    )

    request = json.loads(request_path.read_text())
    assert request["operation"] == "stitching.run"
    assert request["payload"]["viewer_dzi"] is True
    assert request["payload"]["workers"] == 3
    manifest = json.loads((tmp_path / "fast-ofm-stitch-manifest.json").read_text())
    assert len(manifest["tiles"]) == 2
    assert all(len(item["sha256"]) == 64 for item in manifest["tiles"])


def test_gpl_artifact_bridge_replays_rg_in_the_external_core(tmp_path: Path) -> None:
    """Ordinary files cross the boundary and reproduce the accepted R/G shift."""
    field = ready_field()
    red, green = shifted_pair(field)
    white_reference, red_reference, green_reference = references()
    policy = RGFocusMeasurementPolicy(
        measurement_domain="processed-jpeg-rgb8",
        core=core_settings().model_dump(mode="json"),
        estimator=RGFocusEstimatorSettings(),
        tissue_field=field_settings().model_dump(mode="json"),
    )
    artifacts = write_rg_measurement_artifacts(
        tmp_path,
        white_frame=white_image(),
        red_plane=red,
        green_plane=green,
        red_source_jpeg8=np.full(red.shape, 100, dtype=np.uint8),
        green_source_jpeg8=np.full(green.shape, 100, dtype=np.uint8),
        red_valid=np.ones(red.shape, dtype=bool),
        green_valid=np.ones(green.shape, dtype=bool),
        white_reference=white_reference.model_dump(mode="json"),
        red_reference=red_reference.model_dump(mode="json"),
        green_reference=green_reference.model_dump(mode="json"),
        geometry=geometry(),
        calibration_id="synthetic",
        measurement_policy=policy.model_dump(mode="json"),
    )
    client = FastOFMCoreBlockingProcess(
        (sys.executable, "-m", "fast_ofm_core", "serve-jsonl"),
        environment=core_environment(),
        artifact_roots=(tmp_path,),
    )
    try:
        response = client.request(
            "rg.measure",
            {
                **artifacts,
                "position_um": {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0},
            },
        )
        result = response["result"]
        assert result["measurement_status"] == "accepted"
        assert result["shift_px"] == {"dx_px": 3.0, "dy_px": -2.0}
    finally:
        client.close()


def test_gpl_flat_field_bridge_fits_maps_in_the_external_core(tmp_path: Path) -> None:
    """The GPL owner sends arrays and receives verified maps without local fitting."""
    yy, xx = np.mgrid[:96, :96]
    shading = 90 + 70 * np.exp(-((xx - 48) ** 2 + (yy - 48) ** 2) / 4000)
    dark = np.full((1, 96, 96), 5, dtype=np.float32)
    flat = dark + shading[None].astype(np.float32)
    settings = FlatFieldSettings(
        measurement_domain="processed-jpeg-rgb8",
        processing_roi=(0, 0, 96, 96),
        minimum_signal_dn=8,
        maximum_dark_signal_dn=32,
        smoothing_sigma_px=2.0,
    )
    exchange = tmp_path / "flat-field"
    artifact = write_flat_field_input(
        exchange, "flat-field-fit-input", flat=flat, dark=dark
    )
    client = FastOFMCoreBlockingProcess(
        (sys.executable, "-m", "fast_ofm_core", "serve-jsonl"),
        environment=core_environment(),
        artifact_roots=(tmp_path,),
    )
    try:
        response = client.request(
            "rg.flat_field.fit",
            {"input": artifact, "settings": settings.model_dump(mode="json")},
        )
        result = response["result"]
        maps = read_flat_field_result(
            result["maps"],
            directory=exchange,
            expected_arrays={"dark", "gain", "valid"},
        )
        assert maps["valid"].all()
        assert result["report"]["black_source"] == "measured_all_off_same_exposure"
    finally:
        client.close()
