"""Source-informed units/conventions and end-to-end qualification of plasma scales."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from pvs import run_case, validate_case, verify_target

PACK = Path(__file__).parents[1] / "benchmark-packs" / "plasma-scales-v1"


def _copy_case(tmp_path: Path, *, produce: bool = True) -> Path:
    root = tmp_path / "case"
    shutil.copytree(PACK, root)
    case_path = root / "pvs.yaml"
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    case["execution"]["command"][0] = sys.executable
    case["package"]["html"] = False
    case["package"]["pdf"] = False
    case_path.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
    if produce:
        subprocess.run(
            [sys.executable, "derive_values.py", "--output", "derived_values.json"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    return case_path


def test_plasma_scales_frozen_values_have_independent_decimal_check() -> None:
    result = subprocess.run(
        [sys.executable, str(PACK / "independent_reference.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "PASS: 30 frozen scalar values" in result.stdout


def test_plasma_scales_match_nrl_rounded_gaussian_coefficients() -> None:
    """Separate practical-unit anchors catch SI factors and thermal conventions.

    NRL 2023 pp.28-29: these are deliberately approximate published coefficients,
    not the higher-precision SI equations used by either implementation. The
    0.4% allowance covers their three-significant-digit rounding; it is not a
    claimed experimental or model uncertainty.
    """
    frozen = json.loads((PACK / "reference_values.json").read_text(encoding="utf-8"))
    ion_proton_ratio = 1.9990075012699  # CODATA 2022, also in the original pack.
    cases = {
        "low_density": (1e12, 10.0, 5.0, 1e3),
        "intermediate": (1e13, 100.0, 100.0, 1e4),
        "high_density": (1e14, 1000.0, 500.0, 5e4),
    }
    for name, (n_cm3, te_ev, ti_ev, b_gauss) in cases.items():
        expected = {
            "electron_debye_length_m": 7.43e2 * math.sqrt(te_ev / n_cm3) / 100,
            "electron_plasma_angular_frequency_rad_s": 5.64e4 * math.sqrt(n_cm3),
            "deuteron_plasma_angular_frequency_rad_s": 1.32e3
            * math.sqrt(n_cm3 / ion_proton_ratio),
            "deuteron_cyclotron_angular_frequency_rad_s": 9.58e3 * b_gauss / ion_proton_ratio,
            "deuteron_thermal_speed_m_s": 9.79e5 * math.sqrt(ti_ev / ion_proton_ratio) / 100,
            "deuteron_thermal_gyroradius_m": 1.02e2
            * math.sqrt(ion_proton_ratio * ti_ev)
            / b_gauss
            / 100,
            "alfven_speed_m_s": 2.18e11
            * b_gauss
            / math.sqrt(n_cm3 * ion_proton_ratio)
            / 100,
            "total_plasma_beta": 4.03e-11 * n_cm3 * (te_ev + ti_ev) / b_gauss**2,
            "electron_inertial_length_m": 5.31e5 / math.sqrt(n_cm3) / 100,
        }
        for quantity, anchor in expected.items():
            assert frozen["points"][name][quantity] == pytest.approx(anchor, rel=0.004)


def test_plasma_scales_validate_and_offline_verify(tmp_path: Path) -> None:
    case_path = _copy_case(tmp_path)
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "PASS"
    evidence = json.loads(outcome.evidence_path.read_text(encoding="utf-8"))
    checks = evidence["record"]["checks"]
    assert len(checks) == 33
    assert all(check["status"] == "PASS" for check in checks)
    assert verify_target(outcome.output_directory).valid


@pytest.mark.skipif(os.name == "nt", reason="PVS supervised command execution requires POSIX")
def test_plasma_scales_supervised_run(tmp_path: Path) -> None:
    case_path = _copy_case(tmp_path, produce=False)
    outcome = run_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "PASS"
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize(
    ("quantity", "factor"),
    [
        ("electron_plasma_angular_frequency_rad_s", 1 / (2 * math.pi)),
        ("electron_debye_length_m", 1000),
        ("deuteron_thermal_gyroradius_m", math.sqrt(2)),
        ("total_plasma_beta", 0.5),
        ("exb_drift_y_m_s", -1),
    ],
)
def test_plasma_scales_reject_convention_and_unit_errors(
    tmp_path: Path, quantity: str, factor: float
) -> None:
    case_path = _copy_case(tmp_path)
    values_path = case_path.parent / "derived_values.json"
    values = json.loads(values_path.read_text(encoding="utf-8"))
    values["points"]["intermediate"][quantity] *= factor
    values_path.write_text(json.dumps(values), encoding="utf-8")
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "FAIL"
    evidence = json.loads(outcome.evidence_path.read_text(encoding="utf-8"))
    checks = {item["id"]: item for item in evidence["record"]["checks"]}
    check_id = f"intermediate-{quantity}".replace("_", "-")
    assert checks[check_id]["status"] == "FAIL"


@pytest.mark.parametrize(
    "protected_name",
    [
        "derive_values.py",
        "independent_reference.py",
        "reference_values.json",
        "reference_values.schema.json",
        "source_manifest.json",
        "pvs.yaml",
        "README.md",
    ],
)
def test_plasma_scales_producer_preserves_pack_inputs(
    tmp_path: Path, protected_name: str
) -> None:
    root = tmp_path / "case"
    shutil.copytree(PACK, root)
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    result = subprocess.run(
        [sys.executable, "derive_values.py", "--output", protected_name],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "pack source and reference files cannot be used as outputs" in result.stderr
    after = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert after == before


def test_plasma_scales_producer_rejects_hardlink_to_reference(tmp_path: Path) -> None:
    root = tmp_path / "case"
    shutil.copytree(PACK, root)
    reference = root / "reference_values.json"
    before = reference.read_bytes()
    alias = tmp_path / "apparently-separate-output.json"
    try:
        os.link(reference, alias)
    except OSError as exc:
        pytest.skip(f"test filesystem cannot create hardlinks: {exc}")
    result = subprocess.run(
        [sys.executable, str(root / "derive_values.py"), "--output", str(alias)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "pack source and reference files cannot be used as outputs" in result.stderr
    assert reference.read_bytes() == before
