"""Regression tests from a review of Physics Validation Suite 0.2.5.

All four fail against 0.2.5 and pass with the 0.2.6 correctness repairs.
Run in the project's supported development environment:
    python -m pytest -q tests/test_review_regressions.py

The tests use the public API and do not modify the PVS source tree.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pvs.api import RunOutcome, validate_case
from pvs.models import Status
from pvs.verify import verify_target


def _validate(
    tmp_path: Path,
    *,
    contents: str,
    check: dict[str, Any],
    artifact_format: str = "json",
) -> RunOutcome:
    case_root = tmp_path / "case"
    case_root.mkdir()
    artifact_name = f"result.{artifact_format}"
    (case_root / artifact_name).write_text(contents, encoding="utf-8")
    definition = {
        "schema": "pvs-case/2",
        "id": "review-regression",
        "version": "1.0.0",
        "title": "PVS code-review regression",
        "classifications": ["verification"],
        "subject": {"name": "synthetic", "version": "1.0.0"},
        "artifacts": {
            "result": {
                "path": artifact_name,
                "role": "output",
                "format": artifact_format,
            }
        },
        "checks": [check],
        "package": {"embed_artifacts": True, "html": False, "pdf": False},
    }
    (case_root / "pvs.yaml").write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )
    return validate_case(case_root, output_dir=tmp_path / "evidence")


def test_mixed_boolean_numeric_artifact_is_not_silently_coerced(tmp_path: Path) -> None:
    outcome = _validate(
        tmp_path,
        contents="[true, 1.0]\n",
        check={
            "id": "compare-values",
            "type": "compare",
            "unit": "1",
            "metric": "linf",
            "tolerance": 0.0,
            "actual": {"artifact": "result", "unit": "1"},
            "expected": {"value": [1.0, 1.0], "unit": "1"},
        },
    )
    assert outcome.status is Status.ERROR, "Boolean data must not silently become numeric 1.0"


def test_mixed_csv_integer_float_column_rejects_unsafe_integer(tmp_path: Path) -> None:
    outcome = _validate(
        tmp_path,
        contents="value\n9007199254740993\n1.0\n",
        artifact_format="csv",
        check={
            "id": "compare-first",
            "type": "compare",
            "unit": "1",
            "metric": "absolute",
            "tolerance": 0.0,
            "actual": {
                "artifact": "result",
                "column": "value",
                "reduce": "first",
                "unit": "1",
            },
            "expected": {"value": 9007199254740992.0, "unit": "1"},
        },
    )
    assert outcome.status is Status.ERROR, "Reject unsafe integers before NumPy dtype promotion"


def test_literal_l2_comparison_survives_its_own_verifier(tmp_path: Path) -> None:
    outcome = _validate(
        tmp_path,
        contents="{}\n",
        check={
            "id": "literal-l2",
            "type": "compare",
            "unit": "1",
            "metric": "l2",
            "tolerance": 500.0,
            "actual": {"value": [286.0, 337.0], "unit": "1"},
            "expected": {"value": [0.0, 0.0], "unit": "1"},
        },
    )
    assert outcome.status is Status.PASS
    assert verify_target(outcome.output_directory).valid


def test_optional_malformed_json_is_error_regardless_of_error_message(tmp_path: Path) -> None:
    outcome = _validate(
        tmp_path,
        contents='{"does not exist": 1, "does not exist": 2}\n',
        check={
            "id": "optional-finite",
            "type": "finite",
            "required": False,
            "unit": "1",
            "source": {"artifact": "result", "unit": "1"},
        },
    )
    assert outcome.status is Status.ERROR, "A parse error is not an absent optional artifact"
