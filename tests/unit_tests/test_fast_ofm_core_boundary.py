"""Executable checks for the GPL adapter/noncommercial process boundary."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[2] / "src" / "openflexure_microscope_server"


def _absolute_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    return imported


def test_gpl_package_never_imports_the_noncommercial_python_namespace() -> None:
    """Integration is JSON/artifacts over a subprocess, never a Python import."""
    violations = [
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if any(
            name == "fast_ofm_core" or name.startswith("fast_ofm_core.")
            for name in _absolute_imports(path)
        )
    ]
    assert violations == []


def test_sparse_coordinator_contains_no_surface_fitting_implementation() -> None:
    """The GPL scheduler may coordinate hardware but must not regain core math."""
    path = PACKAGE / "focus" / "sparse_focus.py"
    source = path.read_text()
    imports = _absolute_imports(path)
    assert "numpy" not in imports
    assert "numpy.typing" not in imports
    for forbidden in (
        "linalg.lstsq",
        "linalg.svd",
        "estimate_focus_height",
        "_estimate_plane",
        "_estimate_row",
    ):
        assert forbidden not in source


def test_focus_surface_module_is_contract_only() -> None:
    """Exact selection and local-plane prediction remain outside the GPL tree."""
    path = PACKAGE / "focus" / "focus_surface.py"
    source = path.read_text()
    imports = _absolute_imports(path)
    assert "numpy" not in imports
    for forbidden in (
        "predict_focus_surface",
        "select_usable_observations",
        "assess_focus_observation",
        "linalg.lstsq",
        "_fit_bounded_plane",
    ):
        assert forbidden not in source


def test_rg_decision_module_is_contract_only() -> None:
    """The signed correction policy is evaluated only by Fast OFM Core."""
    path = PACKAGE / "focus" / "rg" / "rg_focus_control.py"
    source = path.read_text()
    imports = _absolute_imports(path)
    assert "math" not in imports
    for forbidden in (
        "def decide_correction",
        "estimate_z_um",
        "cross_track_residual_px(",
        "effective_focus_tolerance_um(",
    ):
        assert forbidden not in source


def test_rg_profile_fitting_is_not_packaged_in_the_gpl_tree() -> None:
    """Calibration fitting is available only through the core process."""
    path = PACKAGE / "focus" / "rg" / "rg_focus_model.py"
    source = path.read_text()
    for forbidden in (
        "def fit_rg_focus_model",
        "def _approach_check",
        "def estimate_z_um",
        "def _cross_track",
        "def _validate_empirical_evidence",
        "def passed_is_exact",
        "def passed_is_complete",
        "def cross_track_residual_px",
        "def effective_focus_tolerance_um",
        "_SUPPORTED_GRID_ROLE_SETS",
        "np.linalg",
        "np.linalg.lstsq",
    ):
        assert forbidden not in source


def test_flat_field_module_is_contract_only() -> None:
    """Flat-field fitting, application and holdout QC run only in the core."""
    path = PACKAGE / "focus" / "rg" / "rg_flat_field.py"
    source = path.read_text()
    imports = _absolute_imports(path)
    assert "cv2" not in imports
    for forbidden in (
        "def fit_flat_field",
        "def apply_native_flat_field",
        "def validate_flat_field",
        "def smooth",
        "GaussianBlur",
    ):
        assert forbidden not in source


def test_rg_measurement_module_is_contract_only() -> None:
    """Patch evaluation and robust R/G aggregation run only in the core."""
    path = PACKAGE / "focus" / "rg" / "rg_focus_estimator.py"
    source = path.read_text()
    for forbidden in (
        "def estimate_rg_shift",
        "def _summarise_inliers",
        "def _dominant_failure",
        "evaluate_patches(",
        "summarise_shift(",
        "np.percentile",
    ):
        assert forbidden not in source


def test_tissue_field_module_is_contract_only() -> None:
    """WHITE classification, mask construction and overlay rendering stay in core."""
    path = PACKAGE / "focus" / "rg" / "rg_focus_field.py"
    source = path.read_text()
    imports = _absolute_imports(path)
    assert "cv2" not in imports
    for forbidden in (
        "def prepare_tissue_field",
        "def require_tissue_field_for_pair",
        "def render_tissue_overlay",
        "def _coherent_texture",
        "connectedComponentsWithStats",
        "texture_candidate_mask(",
    ):
        assert forbidden not in source


def test_rg_numerical_core_module_is_contract_only() -> None:
    """NMI, masking and patch evaluation exist only in the core distribution."""
    path = PACKAGE / "focus" / "rg" / "rg_focus_core.py"
    source = path.read_text()
    imports = _absolute_imports(path)
    assert "cv2" not in imports
    assert "numpy" not in imports
    for forbidden in (
        "def mutual_information_shift_masked",
        "def texture_candidate_mask",
        "def candidate_boxes",
        "def evaluate_patches",
        "def measure_patches",
        "def select_reference_boxes",
        "def summarise_shift",
        "ThreadPoolExecutor",
    ):
        assert forbidden not in source
    assert not (path.parent / "_rg_nmi.cpp").exists()
    assert not (path.parent / "_rg_nmi.pyi").exists()


def test_simultaneous_rg_module_is_contract_only() -> None:
    """Spectral unmixing, registration and curve fitting remain in core."""
    path = PACKAGE / "focus" / "rg" / "rg_simultaneous.py"
    source = path.read_text()
    imports = _absolute_imports(path)
    assert "cv2" not in imports
    assert "numpy" not in imports
    for forbidden in (
        "def fit_spectral_flat_field",
        "def validate_spectral_flat_field",
        "def unmix_components",
        "def measure_simultaneous_shift",
        "def inspect_simultaneous_focus_field",
        "def fit_simultaneous_focus_curve",
        "def infer_simultaneous_defocus",
        "def validate_peripheral_focus",
        "class PreparedSpectralUnmix",
    ):
        assert forbidden not in source


def test_removed_stitching_implementations_are_not_packaged() -> None:
    """Only the GPL process adapter remains in the OpenFlexure distribution."""
    stitching = PACKAGE / "stitching"
    for name in ("pyramidal_stitch.py", "stage_stitch.py", "stitch_acceleration.py"):
        assert not (stitching / name).exists()
