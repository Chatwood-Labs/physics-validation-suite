"""Cross-layer, rehashed contradictions for every supported check type."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case, dump_case
from contract_helpers import rehash_record

import pvs.api as api_module
from pvs.api import validate_case
from pvs.checks.contracts import expected_criterion
from pvs.jsonutil import load_strict
from pvs.models import Status
from pvs.verify import verify_target

KINDS = ["exists", "schema", "finite", "range", "monotonic", "compare", "reference", "conservation"]
NUMERIC_KINDS = ["finite", "range", "monotonic", "compare", "reference", "conservation"]


def test_exists_io_failure_remains_publishable_error_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = make_case(tmp_path / "case", "exists")
    evaluate = api_module.evaluate_checks
    is_file = Path.is_file

    def evaluate_with_failed_stat(declarations, artifacts):
        def fail(path):
            if path == artifacts["result"].path:
                raise PermissionError("injected validation-time stat failure")
            return is_file(path)

        with monkeypatch.context() as scope:
            scope.setattr(Path, "is_file", fail)
            return evaluate(declarations, artifacts)

    monkeypatch.setattr(api_module, "evaluate_checks", evaluate_with_failed_stat)
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert outcome.status is Status.ERROR
    assert outcome.finding_id is None
    result = load_strict(outcome.evidence_path)["record"]["checks"][0]
    assert result["status"] == "ERROR"
    assert result["observed"] == result["criterion"] == {}
    assert verify_target(outcome.output_directory).valid


def make_case(root: Path, kind: str, *, passing: bool = True, required: bool = True) -> Path:
    root.mkdir(parents=True)
    case = base_case()
    case["artifacts"]["result"].update(required=False)
    source = {"artifact": "result", "column": "value", "unit": "1"}
    check: dict[str, Any] = {"id": "boundary", "type": kind, "required": required}
    if kind in NUMERIC_KINDS:
        case["artifacts"]["result"].update(path="result.csv", format="csv")
        values = "1\n2\n" if passing else "3\n1\n"
        if kind == "finite" and not passing:
            values = "inf\n1\n"
        (root / "result.csv").write_text("value\n" + values, encoding="utf-8")
        check["unit"] = "1"
    else:
        (root / "result.json").write_text('{"value": 1}\n', encoding="utf-8")
    if kind == "exists":
        check["artifact"] = "result"
        if not passing:
            (root / "result.json").unlink()
    elif kind == "schema":
        case["artifacts"]["schema"] = {
            "path": "schema.json",
            "role": "auxiliary",
            "format": "json",
            "required": False,
        }
        (root / "schema.json").write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {"value": {"const": 1 if passing else 2}},
                }
            ),
            encoding="utf-8",
        )
        check.update(artifact="result", schema_artifact="schema")
    elif kind in {"finite", "range", "monotonic"}:
        check["source"] = source
        if kind == "range":
            check.update(minimum=0, maximum=2)
        elif kind == "monotonic":
            check.update(direction="increasing", absolute_tolerance=0)
    elif kind in {"compare", "reference"}:
        check.update(
            actual=source, expected={"value": [1, 2], "unit": "1"}, metric="linf", tolerance=0
        )
        if kind == "reference":
            check["reference_id"] = "anchor"
            case["references"] = [
                {
                    "id": "anchor",
                    "type": "analytic",
                    "citation": "Synthetic reference",
                    "locator": "Declared [1, 2] test vector",
                }
            ]
    else:
        check.update(
            terms=[
                {"value": source, "coefficient": 1},
                {"value": {"value": 0, "unit": "1"}, "coefficient": -1},
            ],
            expected={"value": 3, "unit": "1"},
            absolute_tolerance=0,
            relative_tolerance=0,
        )
    case["checks"] = [check]
    return dump_case(root / "pvs.yaml", case)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(("passing", "required"), [(True, True), (False, True), (False, False)])
def test_producer_outcomes_verify_and_opposite_status_is_rejected(
    tmp_path: Path,
    kind: str,
    passing: bool,
    required: bool,
) -> None:
    path = make_case(tmp_path / "case", kind, passing=passing, required=required)
    outcome = validate_case(path, output_dir=tmp_path / "package")
    expected = Status.PASS if passing else Status.FAIL if required else Status.WARN
    assert outcome.status is expected
    assert verify_target(outcome.output_directory).valid
    envelope = load_strict(outcome.evidence_path)
    assert envelope["integrity"]["finding"]["status"] == "ISSUED"
    envelope["record"]["checks"][0]["status"] = (
        "PASS" if not passing else "FAIL" if required else "WARN"
    )
    forged = rehash_record(envelope, tmp_path / "contradiction.json")
    result = verify_target(forged)
    assert not result.valid
    assert any(
        c["name"] == "canonical record digest" and c["status"] == "PASS" for c in result.checks
    )


@pytest.mark.parametrize("kind", [k for k in KINDS if k != "exists"])
@pytest.mark.parametrize("required", [True, False])
def test_missing_input_errors_and_skips_verify_but_cannot_be_rehashed_into_completion(
    tmp_path: Path,
    kind: str,
    required: bool,
) -> None:
    path = make_case(tmp_path / "case", kind, required=required)
    valid = validate_case(path, output_dir=tmp_path / "valid")
    good_result = load_strict(valid.evidence_path)["record"]["checks"][0]
    artifact = path.parent / ("result.json" if kind == "schema" else "result.csv")
    artifact.unlink()
    outcome = validate_case(path, output_dir=tmp_path / "missing")
    assert outcome.status is (Status.ERROR if required else Status.SKIP)
    assert (outcome.finding_id is None) is required
    assert verify_target(outcome.output_directory).valid
    envelope = load_strict(outcome.evidence_path)
    envelope["record"]["checks"][0] = good_result
    result = verify_target(rehash_record(envelope, tmp_path / "missing-pass.json"))
    assert not result.valid
    assert (
        next(c for c in result.checks if c["name"] == "check completed-input contract")["status"]
        == "FAIL"
    )


@pytest.mark.parametrize("kind", NUMERIC_KINDS)
@pytest.mark.parametrize("status", ["PASS", "FAIL", "WARN"])
def test_invalid_declared_units_cannot_support_any_completed_status(
    tmp_path: Path,
    kind: str,
    status: str,
) -> None:
    path = make_case(tmp_path / "case", kind, passing=status == "PASS", required=status != "WARN")
    outcome = validate_case(path, output_dir=tmp_path / "valid")
    envelope = load_strict(outcome.evidence_path)
    definition = envelope["record"]["case"]["resolved_definition"]
    check = definition["checks"][0]
    operand = (
        check["source"]
        if "source" in check
        else check["actual"]
        if "actual" in check
        else check["terms"][0]["value"]
    )
    operand["unit"] = "s"
    forged = rehash_record(envelope, tmp_path / "unit-contradiction.json")
    assert not verify_target(forged).valid
    # The identical declaration still produces publishable ERROR evidence.
    dump_case(path, definition)
    error = validate_case(path, output_dir=tmp_path / "error")
    assert error.status is Status.ERROR
    assert error.finding_id is None
    assert verify_target(error.output_directory).valid


@pytest.mark.parametrize("kind", ["finite", "range", "monotonic", "compare", "reference"])
def test_known_reduction_shape_is_bound_even_when_values_are_unavailable(
    tmp_path: Path,
    kind: str,
) -> None:
    path = make_case(tmp_path / "case", kind)
    outcome = validate_case(path, output_dir=tmp_path / "valid")
    envelope = load_strict(outcome.evidence_path)
    check = envelope["record"]["case"]["resolved_definition"]["checks"][0]
    (check["source"] if "source" in check else check["actual"])["reduce"] = "first"
    assert not verify_target(rehash_record(envelope, tmp_path / "shape-contradiction.json")).valid


@pytest.mark.parametrize("kind", [k for k in KINDS if k != "exists"])
def test_malformed_existing_input_is_verified_error_even_if_optional(
    tmp_path: Path, kind: str
) -> None:
    path = make_case(tmp_path / "case", kind, required=False)
    target = path.parent / ("result.json" if kind == "schema" else "result.csv")
    target.write_text('{"does not exist":' if kind == "schema" else 'value\n"1', encoding="utf-8")
    outcome = validate_case(path, output_dir=tmp_path / "error")
    assert outcome.status is Status.ERROR
    assert outcome.finding_id is None
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("operand", ["term", "expected", "normalization"])
def test_conservation_rejects_known_invalid_literal_operands_after_rehash(
    tmp_path: Path,
    operand: str,
) -> None:
    path = make_case(tmp_path / "case", "conservation")
    outcome = validate_case(path, output_dir=tmp_path / "valid")
    envelope = load_strict(outcome.evidence_path)
    check = envelope["record"]["case"]["resolved_definition"]["checks"][0]
    if operand == "term":
        check["terms"][0]["value"] = {"value": [1e308, 1e308], "unit": "1"}
    else:
        check[operand] = {"value": [0, 0], "unit": "1"}
    envelope["record"]["checks"][0]["criterion"] = dict(expected_criterion(check))
    assert not verify_target(rehash_record(envelope, tmp_path / "invalid-literal.json")).valid
    dump_case(path, copy.deepcopy(envelope["record"]["case"]["resolved_definition"]))
    error = validate_case(path, output_dir=tmp_path / "error")
    assert error.status is Status.ERROR
    assert verify_target(error.output_directory).valid
