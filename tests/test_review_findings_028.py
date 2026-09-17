"""Regression tests from review of PVS 0.2.8.

Run with PVS and pytest installed in the project's supported development environment:
    python -m pytest -q test_review_findings_028.py

The assertions describe desired behavior. Against the reviewed 0.2.8 source,
our review environment reports 5 failures and 2 passes. The four verifier tests
recompute identifiers deliberately: they test internal consistency, NOT an
ability to modify evidence without changing a trusted identity.

Review runtime: CPython 3.13.5, NumPy 2.3.5, pytest 9.0.2; rfc8785 0.1.4 source
restored from its upstream tag. This is not the frozen/supported release
qualification environment. No PVS implementation files were changed.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.checks.contracts import expected_criterion
from pvs.checks.engine import evaluate_checks
from pvs.finding import create_finding_metadata
from pvs.jsonutil import load_strict, write_pretty
from pvs.models import Status
from pvs.verify import verify_target


def _comparison(
    actual: Any = None, expected: Any = None, *, metric: str = "linf"
) -> dict[str, Any]:
    return {
        "id": "comparison", "type": "compare", "unit": "m", "metric": metric,
        "tolerance": 0.0,
        "actual": {"value": [0.0, 0.0] if actual is None else actual, "unit": "m"},
        "expected": {"value": [0.0, 0.0] if expected is None else expected, "unit": "m"},
    }


def _write_case(
    root: Path, check: dict[str, Any], *, csv_text: str | None = None
) -> Path:
    root.mkdir(parents=True)
    filename, artifact_format = ("result.json", "json") if csv_text is None else (
        "result.csv", "csv"
    )
    data = {
        "schema": "pvs-case/2", "id": "review-case", "version": "1.0.0",
        "title": "Review regression", "classifications": ["verification"],
        "subject": {"name": "synthetic-subject", "version": "1.0.0"},
        "artifacts": {"result": {
            "path": filename, "role": "output", "format": artifact_format,
        }},
        "checks": [check],
        "package": {"embed_artifacts": False, "html": False, "pdf": False},
    }
    (root / filename).write_text("{}\n" if csv_text is None else csv_text, encoding="utf-8")
    path = root / "pvs.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _invalid_comparison(scenario: str) -> dict[str, Any]:
    if scenario == "absolute-vector":
        return _comparison(metric="absolute")
    if scenario == "mismatched-literal-shapes":
        return _comparison([0.0, 0.0], [0.0])
    if scenario == "overflowing-literal-subtraction":
        return _comparison([1e308, 1e308], [-1e308, -1e308])
    if scenario == "mismatched-operand-units":
        check = _comparison()
        check["actual"]["unit"] = "s"
        return check
    raise AssertionError(f"Unknown scenario: {scenario}")


@pytest.mark.parametrize("scenario", [
    "absolute-vector", "mismatched-literal-shapes",
    "overflowing-literal-subtraction", "mismatched-operand-units",
])
def test_verifier_rejects_pass_for_invalid_comparison(
    tmp_path: Path, scenario: str,
) -> None:
    declaration = _invalid_comparison(scenario)
    path = _write_case(tmp_path / "case", declaration)
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert outcome.status is Status.ERROR
    assert outcome.finding_id is None
    assert verify_target(outcome.evidence_path).valid

    # A valid zero-error vector observation is structurally plausible, but it
    # must not validate against an impossible comparison declaration.
    claimed_result = copy.deepcopy(evaluate_checks([_comparison()], {})[0].as_dict())
    claimed_result["criterion"] = dict(expected_criterion(declaration))
    if declaration["metric"] == "absolute":
        claimed_result["observed"].pop("reference_norm")

    envelope = load_strict(outcome.evidence_path)
    record = envelope["record"]
    record["checks"] = [claimed_result]
    record["summary"]["status"] = "PASS"
    record["summary"]["counts"] = {
        status.value: int(status is Status.PASS) for status in Status
    }
    digest = canonical_sha256(record)
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = f"pvs:sha256:{digest}"
    envelope["integrity"]["finding"] = create_finding_metadata(record)
    modified = tmp_path / "modified-record.json"
    write_pretty(modified, envelope)

    verification = verify_target(modified)
    assert not verification.valid, (
        f"Verifier accepted impossible PASS for {scenario}; "
        f"trust={verification.trust}, finding={verification.finding_id}"
    )


def _csv_check() -> dict[str, Any]:
    check = _comparison(1.0, 1.0, metric="absolute")
    check["actual"] = {
        "artifact": "result", "column": "temperature", "reduce": "first", "unit": "m",
    }
    return check


def test_unterminated_csv_quote_is_error(tmp_path: Path) -> None:
    path = _write_case(tmp_path / "case", _csv_check(), csv_text='temperature\n"1')
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert outcome.status is Status.ERROR, "Truncated quoted CSV field was accepted as PASS"
    assert outcome.finding_id is None
    assert verify_target(outcome.output_directory).valid


def test_well_formed_quoted_csv_still_passes(tmp_path: Path) -> None:
    path = _write_case(tmp_path / "case", _csv_check(), csv_text='temperature\n"1"\n')
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert outcome.status is Status.PASS
    assert verify_target(outcome.output_directory).valid


def test_valid_literal_comparison_still_passes(tmp_path: Path) -> None:
    path = _write_case(tmp_path / "case", _comparison())
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert outcome.status is Status.PASS
    assert verify_target(outcome.evidence_path).valid
