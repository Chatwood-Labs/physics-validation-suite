"""Frozen analytical targets, complete comparisons and negative controls."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from pvs import validate_case, verify_target

PACK = Path(__file__).parents[1] / "benchmark-packs" / "classical-physics-v1"


def prepared_case(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "case"
    shutil.copytree(PACK, root, ignore=shutil.ignore_patterns("evidence", "__pycache__"))
    subprocess.run(
        [sys.executable, "derive_reference_values.py", "--output", "derived_values.json"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    case_path = root / "pvs.yaml"
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    case["package"]["html"] = False
    case["package"]["pdf"] = False
    case_path.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
    return case_path, root / "derived_values.json"


def test_classical_independent_frozen_calculation() -> None:
    completed = subprocess.run(
        [sys.executable, str(PACK / "verify_frozen_values.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {
        "status": "PASS", "scalar_targets": 36, "decimal_precision": 80
    }


def test_classical_all_checks_pass_and_evidence_verifies(tmp_path: Path) -> None:
    case, _ = prepared_case(tmp_path)
    outcome = validate_case(case, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "PASS"
    assert verify_target(outcome.output_directory).valid
    evidence = json.loads((outcome.output_directory / "evidence.json").read_text())
    assert len(evidence["record"]["checks"]) == 18
    assert all(check["status"] == "PASS" for check in evidence["record"]["checks"])


@pytest.mark.parametrize(
    ("family", "field"),
    [
        ("oscillator", "position_m"),
        ("oscillator", "velocity_m_s"),
        ("oscillator", "total_energy_j"),
        ("heat", "temperature_excess_k"),
        ("kepler", "circular_speed_m_s"),
        ("kepler", "orbital_period_s"),
    ],
)
def test_classical_corrupted_solver_output_fails(
    tmp_path: Path, family: str, field: str
) -> None:
    case, output = prepared_case(tmp_path)
    values: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    if family == "heat":
        values[family][field][1][2] *= 1.01
    else:
        values[family][field][1] *= 1.01
    output.write_text(json.dumps(values), encoding="utf-8")
    outcome = validate_case(case, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "FAIL"
    evidence = json.loads((outcome.output_directory / "evidence.json").read_text())
    checks = {check["id"]: check for check in evidence["record"]["checks"]}
    target_id = family + "-" + field.replace("_", "-") + "-reference"
    assert checks[target_id]["status"] == "FAIL"


def test_classical_wrong_sampling_grid_fails_schema(tmp_path: Path) -> None:
    case, output = prepared_case(tmp_path)
    values = json.loads(output.read_text(encoding="utf-8"))
    values["heat"]["time_s"][1] = 0.6
    output.write_text(json.dumps(values), encoding="utf-8")
    outcome = validate_case(case, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "FAIL"
    evidence = json.loads((outcome.output_directory / "evidence.json").read_text())
    checks = {check["id"]: check for check in evidence["record"]["checks"]}
    assert checks["output-schema"]["status"] == "FAIL"


def test_classical_producer_cannot_overwrite_frozen_reference(tmp_path: Path) -> None:
    root = tmp_path / "case"
    shutil.copytree(PACK, root)
    frozen = root / "reference_values.json"
    before = hashlib.sha256(frozen.read_bytes()).hexdigest()
    completed = subprocess.run(
        [sys.executable, "derive_reference_values.py", "--output", "reference_values.json"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == before
