"""Published-table adapter checks, including physical normalisation failures."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from pvs import validate_case, verify_target

PACK = Path(__file__).parents[1] / "benchmark-packs" / "fusion-reactivity-v1"


def prepare_case(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "case"
    shutil.copytree(PACK, root)
    case_path = root / "pvs.yaml"
    case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
    case["package"]["html"] = False
    case["package"]["pdf"] = False
    case_path.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(root / "convert_reference_table.py"),
            "--output",
            str(root / "derived_values.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return case_path, case


def test_fusion_table_adapter_passes_and_verifies(tmp_path: Path) -> None:
    case_path, _ = prepare_case(tmp_path)
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "PASS"
    result = verify_target(outcome.output_directory)
    assert result.valid, result.as_dict()


@pytest.mark.parametrize(
    ("field", "channel", "factor", "check_id"),
    [
        ("event_rate_per_m3_s", "dd_total", 2.0, "dd-total-event-reference"),
        ("reactivity_m3_s", "dt", 1.0e6, "dt-reactivity-reference"),
        ("reactivity_m3_s", "d_he3", 0.5, "d-he3-reactivity-reference"),
    ],
)
def test_physical_output_errors_fail(
    tmp_path: Path,
    field: str,
    channel: str,
    factor: float,
    check_id: str,
) -> None:
    """Reject missing DD pair factor, cm/SI error, and an altered channel."""
    case_path, _ = prepare_case(tmp_path)
    actual_path = case_path.parent / "derived_values.json"
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    actual[field][channel][3] *= factor
    actual_path.write_text(json.dumps(actual), encoding="utf-8")
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status.value == "FAIL"
    evidence = json.loads((outcome.output_directory / "evidence.json").read_text())
    checks = {check["id"]: check for check in evidence["record"]["checks"]}
    assert checks[check_id]["status"] == "FAIL"


def test_reactivity_table_corruption_cannot_rewrite_frozen_targets(tmp_path: Path) -> None:
    """Even deliberately repinning an incorrect input cannot move the oracle."""
    case_path, case = prepare_case(tmp_path)
    table_path = case_path.parent / "published_table.json"
    table = json.loads(table_path.read_text(encoding="utf-8"))
    table["reactivity_cm3_s"]["dt"][3] = "1.1e-15"
    table_path.write_text(json.dumps(table), encoding="utf-8")
    case["artifacts"]["published-table"]["expected_sha256"] = hashlib.sha256(
        table_path.read_bytes()
    ).hexdigest()
    case_path.write_text(yaml.safe_dump(case, sort_keys=False), encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            str(case_path.parent / "convert_reference_table.py"),
            "--output",
            str(case_path.parent / "derived_values.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert validate_case(case_path, output_dir=tmp_path / "evidence").status.value == "FAIL"


def test_published_precision_and_independent_density_arithmetic() -> None:
    """SI and simplified density factors from separately read source rows."""
    frozen = json.loads((PACK / "reference_values.json").read_text(), parse_float=Decimal)
    # NRL p.45,10keV row, separately transcribed; no adapter execution/import.
    printed_cm3_s = {"dt": "1.1e-16", "dd_total": "1.2e-18", "d_he3": "2.3e-19"}
    pair_factors = {"dt": "2.5e39", "dd_total": "5e39", "d_he3": "2.5e39"}
    for channel, value in printed_cm3_s.items():
        expected_si = Decimal(value) / Decimal(10) ** 6
        assert frozen["reactivity_m3_s"][channel][3] == expected_si
        assert frozen["event_rate_per_m3_s"][channel][3] == (
            expected_si * Decimal(pair_factors[channel])
        )
    table = json.loads((PACK / "published_table.json").read_text())
    assert table["reactivity_cm3_s"]["d_he3"][0] == "1e-26"
    assert table["last_displayed_decimal_unit_cm3_s"]["d_he3"][0] == "1E-26"


def test_adapter_rejects_overwriting_reference(tmp_path: Path) -> None:
    case_path, _ = prepare_case(tmp_path)
    frozen_path = case_path.parent / "reference_values.json"
    before = frozen_path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(case_path.parent / "convert_reference_table.py"),
            "--output",
            str(frozen_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert frozen_path.read_bytes() == before


def test_adapter_rejects_hardlink_to_reference(tmp_path: Path) -> None:
    case_path, _ = prepare_case(tmp_path)
    frozen_path = case_path.parent / "reference_values.json"
    alias = tmp_path / "output-alias.json"
    try:
        os.link(frozen_path, alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    before = frozen_path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            str(case_path.parent / "convert_reference_table.py"),
            "--output",
            str(alias),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert frozen_path.read_bytes() == before
