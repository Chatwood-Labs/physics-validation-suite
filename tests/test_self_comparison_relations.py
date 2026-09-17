"""Complete same-source comparisons must record exact zero differences."""

from __future__ import annotations

import copy
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case, dump_case

from pvs.api import RunOutcome, validate_case
from pvs.canonical import canonical_sha256
from pvs.checks.engine import evaluate_checks
from pvs.constants import EVIDENCE_SCHEMA, LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA
from pvs.errors import ArtifactError
from pvs.finding import create_finding_metadata
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict, write_pretty
from pvs.verification.comparisons import require_self_comparison, validate_comparison
from pvs.verification.observations import (
    CompletedObservation,
    require_observation_metadata,
    validate_observation,
)
from pvs.verify import verify_target

GENERATIONS = (LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA)
METRICS = ("absolute", "l1", "l2", "linf", "relative", "close")
SHAPE_METRICS = [(metric, scalar) for metric in METRICS for scalar in (True, False)
                 if scalar or metric != "absolute"]
OVERFLOW = {"status": "UNAVAILABLE", "reason": "OVERFLOW"}


def _check(actual: Any, expected: Any, metric: str, kind: str = "compare") -> dict[str, Any]:
    check: dict[str, Any] = {
        "id": "self", "type": kind, "unit": "1", "metric": metric,
        "actual": {"value": actual, "unit": "1"},
        "expected": {"value": expected, "unit": "1"},
    }
    if kind == "reference":
        check["reference_id"] = "synthetic"
    if metric == "close":
        check.update(absolute_tolerance=0.0, relative_tolerance=0.0)
    else:
        check["tolerance"] = 0.0
    if metric == "relative":
        check["scale_floor"] = 1.0
    return check


def _source(**selectors: Any) -> dict[str, Any]:
    return {"artifact": "result", "pointer": "/a", "unit": "1", **selectors}


def _produce(tmp_path: Path, check: dict[str, Any], data: Any):
    case = base_case()
    case["checks"] = [check]
    case["references"] = [{"id": "synthetic", "type": "canonical-fixture",
                           "citation": "Synthetic self-comparison control",
                           "locator": "result.json"}]
    case["package"]["embed_artifacts"] = True
    path = dump_case(tmp_path / "case/pvs.yaml", case)
    write_pretty(path.parent / "result.json", data)
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert verify_target(outcome.output_directory).valid
    return outcome, load_strict(outcome.evidence_path)


def _rehash(outcome: RunOutcome, envelope: dict[str, Any]) -> None:
    record = envelope["record"]
    status = record["checks"][0]["status"]
    record["summary"]["status"] = status
    record["summary"]["counts"] = {key: int(key == status)
                                   for key in record["summary"]["counts"]}
    envelope["integrity"]["finding"] = create_finding_metadata(record)
    digest = canonical_sha256(record)
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    write_pretty(outcome.evidence_path, envelope)
    manifest = load_strict(outcome.manifest_path)
    manifest["package"]["evidence_id"] = envelope["integrity"]["evidence_id"]
    for entry in manifest["package"]["files"]:
        entry.update(file_identity(outcome.output_directory / entry["path"]))
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"].update(digest=digest, package_id=f"pvs-package:sha256:{digest}")
    write_pretty(outcome.manifest_path, manifest)


@pytest.mark.parametrize("kind", ["compare", "reference"])
@pytest.mark.parametrize("metric,scalar", SHAPE_METRICS)
def test_rehashed_impossible_self_comparison_rejected_by_full_package_verifier(
    tmp_path, kind, metric, scalar,
):
    expected = 2.0 if scalar else [2.0, 3.0, 4.0]
    actual = 3.0 if scalar else [3.0, 5.0, 7.0]
    check = _check(expected, expected, metric, kind)
    source = _source(reduce="first") if scalar else _source()
    check.update(actual=source, expected=dict(reversed(list(source.items()))))
    outcome, envelope = _produce(tmp_path, check, {"a": [2.0, 3.0, 4.0]})
    assert outcome.status.value == "PASS"
    original_inputs = {
        path: path.read_bytes() for directory in ("case", "artifacts")
        for path in (outcome.output_directory / directory).rglob("*") if path.is_file()
    }
    # Use observations that are valid for different values, so rejection cannot
    # be attributed to broken arithmetic or malformed diagnostics.
    different = _check(actual, expected, metric, kind)
    produced = evaluate_checks([different], {})[0].as_dict()
    assert produced["status"] == "FAIL"
    distinct = copy.deepcopy(check)
    distinct["expected"]["pointer"] = "/different"
    assert isinstance(validate_observation(distinct, produced, evidence_schema=EVIDENCE_SCHEMA),
                      CompletedObservation)
    result = envelope["record"]["checks"][0]
    result.update(observed=produced["observed"], status="FAIL", summary=produced["summary"])
    _rehash(outcome, envelope)
    assert all(path.read_bytes() == data for path, data in original_inputs.items())
    assert not verify_target(outcome.output_directory, expect_package_id=outcome.package_id).valid
    verification = verify_target(outcome.output_directory)
    assert not verification.valid
    assert any(item["status"] == "FAIL" and "identical comparison sources" in item["detail"]
               for item in verification.checks)


def _historical_result(metric, actual, expected, schema):
    declared = _check(actual, expected, metric)
    result = evaluate_checks([declared], {})[0].as_dict()
    assert result["status"] in {"PASS", "FAIL"}
    observed = result["observed"]
    declared.update(actual=_source(), expected=dict(reversed(list(_source().items()))))
    if schema != EVIDENCE_SCHEMA:
        del observed["profile"]
        for name in ("l1_error", "l2_error", "linf_error", "reference_norm", "relative_error"):
            if name in observed:
                diagnostic = observed[name]
                observed[name] = diagnostic.get("value")
        if metric == "relative":
            del observed["reference_norm"]
    if schema == LEGACY_EVIDENCE_SCHEMA:
        del declared["unit"], declared["actual"]["unit"], declared["expected"]["unit"]
        del observed["unit"]
    return declared, result


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("metric,scalar", SHAPE_METRICS)
def test_same_source_relation_covers_historical_metadata_paths(schema, metric, scalar):
    same = 2.0 if scalar else [2.0, 4.0]
    different = 3.0 if scalar else [3.0, 4.0]
    declared, result = _historical_result(metric, same, same, schema)
    require_observation_metadata(declared, result, evidence_schema=schema)
    declared, result = _historical_result(metric, different, same, schema)
    with pytest.raises(ArtifactError, match="identical comparison sources"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("status", ["ERROR", "SKIP"])
def test_unevaluated_self_comparisons_have_no_finite_equality_claim(schema, status):
    declaration = _check(0.0, 0.0, "relative")
    declaration.update(actual=_source(), expected=_source())
    require_observation_metadata(declaration, {"status": status, "observed": {}},
                                 evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("value", [0.0, -0.0, 2.0, -2.0, math.ulp(0.0)])
def test_scalar_relative_diagnostic_and_signed_zero_preserve_generation_policy(schema, value):
    declared, result = _historical_result("absolute", value, value, schema)
    if value == 0.0:
        result["observed"]["actual"] = -value
    require_observation_metadata(declared, result, evidence_schema=schema)
    result["observed"]["relative_error"] = (
        {"status": "AVAILABLE", "value": 1.0} if schema == EVIDENCE_SCHEMA else 1.0
    )
    with pytest.raises(ArtifactError, match="identical comparison sources"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS[:2])
def test_zero_numerator_never_has_historical_relative_overflow(schema):
    declared, result = _historical_result("absolute", 2.0, 2.0, schema)
    result["observed"].update(relative_error=None, relative_error_overflow=True)
    with pytest.raises(ArtifactError):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS[:2])
def test_zero_reference_requires_recorded_historical_null_diagnostic(schema):
    declared, result = _historical_result("absolute", 0.0, 0.0, schema)
    del result["observed"]["relative_error"]
    with pytest.raises(ArtifactError, match="identical comparison sources"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("status", ["FAIL", "WARN"])
def test_completed_self_comparisons_cannot_claim_failure_with_zero_errors(schema, status):
    declared, result = _historical_result("absolute", 2.0, 2.0, schema)
    result["status"] = status
    with pytest.raises(ArtifactError, match="passing completed result"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("field,value", [
    ("artifact", "another"), ("pointer", "/b"), ("column", "other"),
    ("variable", "other"), ("component", 1), ("reduce", "first"), ("unit", "m"),
])
def test_different_complete_expressions_are_not_inferred_equal(field, value):
    declared, result = _historical_result("absolute", 3.0, 2.0, EVIDENCE_SCHEMA)
    declared["expected"][field] = value
    require_self_comparison(declared, result["observed"], evidence_schema=EVIDENCE_SCHEMA)


@pytest.mark.parametrize("reduction", ["size", "first", "last", "sum", "mean", "min", "max"])
def test_selected_reductions_compare_to_themselves_without_changing_values(tmp_path, reduction):
    check = _check(0, 0, "absolute")
    source = _source(component=1, reduce=reduction)
    check.update(actual=source, expected=dict(reversed(list(source.items()))))
    outcome, _ = _produce(tmp_path, check, {"a": [[-2.0, -3.0], [0.0, 4.0]]})
    assert outcome.status.value == "PASS"


@pytest.mark.parametrize("value", [[2.0], [[2.0]], -0.0])
def test_single_element_shapes_and_signed_zero_remain_valid(tmp_path, value):
    check = _check(0, 0, "absolute")
    check.update(actual=_source(), expected=_source())
    outcome, _ = _produce(tmp_path, check, {"a": value})
    assert outcome.status.value == "PASS"


@pytest.mark.parametrize("metric", ["l1", "l2", "relative"])
def test_self_comparison_preserves_reference_overflow_policy(tmp_path, metric):
    check = _check(0, 0, metric)
    check.update(actual=_source(), expected=_source())
    outcome, envelope = _produce(tmp_path, check, {"a": [1.7e308, 1.7e308]})
    result = envelope["record"]["checks"][0]
    if metric == "relative":
        assert outcome.status.value == "ERROR"
        assert result["observed"] == {}
    else:
        assert outcome.status.value == "PASS"
        assert result["observed"]["reference_norm"] == OVERFLOW
        assert validate_comparison(check, result["observed"])


@pytest.mark.parametrize("field", ["l1_error", "l2_error", "linf_error"])
def test_same_source_error_norms_cannot_claim_unavailability(field):
    declared, result = _historical_result("l2", [2.0, 3.0], [2.0, 3.0], EVIDENCE_SCHEMA)
    result["observed"][field] = OVERFLOW
    with pytest.raises(ArtifactError, match="identical comparison sources"):
        validate_comparison(declared, result["observed"])


def test_self_closeness_cannot_claim_failing_positions_with_zero_errors():
    declared, result = _historical_result("close", [2.0, 3.0], [2.0, 3.0], EVIDENCE_SCHEMA)
    result["observed"].update(failing_count=1, failing_indices=[[1]])
    with pytest.raises(ArtifactError, match="cannot have closeness failures"):
        validate_comparison(declared, result["observed"])


@pytest.mark.parametrize("fixture", ["synthetic-json-evidence-v1.zip",
                                     "evidence-v2-0.2.8.zip", "synthetic-json-evidence-v3.zip"])
def test_frozen_generation_envelopes_enforce_source_identity(tmp_path, fixture):
    with zipfile.ZipFile(Path(__file__).parent / "fixtures" / fixture) as archive:
        envelope = json.loads(archive.read(next(
            name for name in archive.namelist() if name.endswith("/evidence.json")
        )))
    path = tmp_path / "historical.json"
    write_pretty(path, envelope)
    assert verify_target(path).valid
    record = envelope["record"]
    result = next(check for check in record["checks"] if check["type"] in {"compare", "reference"})
    definition = record["case"]["resolved_definition"]
    declared = next(check for check in definition["checks"] if check["id"] == result["id"])
    if "artifact" not in declared["actual"]:
        declared["actual"] = {"artifact": next(iter(definition["artifacts"])),
                              "reduce": "first", "unit": "1"}
    declared["expected"] = copy.deepcopy(declared["actual"])
    observed = result["observed"]
    for name in ("l1_error", "l2_error", "linf_error"):
        observed[name] = {"status": "AVAILABLE", "value": 1e-20} if (
            envelope["schema"] == EVIDENCE_SCHEMA) else 1e-20
    observed["gating_error"] = 1e-20
    record["case"]["semantic_sha256"] = canonical_sha256(definition)
    if envelope["schema"] != LEGACY_EVIDENCE_SCHEMA:
        envelope["integrity"]["finding"] = create_finding_metadata(record)
    digest = canonical_sha256(record)
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    write_pretty(path, envelope)
    verification = verify_target(path)
    assert not verification.valid
    assert any(item["status"] == "FAIL" and "identical comparison sources" in item["detail"]
               for item in verification.checks)


@pytest.mark.parametrize("status", ["FAIL", "WARN"])
def test_rehashed_v1_self_comparison_cannot_report_failure_for_zero_error(tmp_path, status):
    fixture = Path(__file__).parent / "fixtures/synthetic-json-evidence-v1.zip"
    with zipfile.ZipFile(fixture) as archive:
        envelope = json.loads(archive.read(next(
            name for name in archive.namelist() if name.endswith("/evidence.json")
        )))
    record = envelope["record"]
    result = next(check for check in record["checks"] if check["type"] == "reference")
    definition = record["case"]["resolved_definition"]
    declared = next(check for check in definition["checks"] if check["id"] == result["id"])
    declared["expected"] = copy.deepcopy(declared["actual"])
    assert result["observed"]["gating_error"] == 0.0
    result["status"] = status
    result["required"] = declared["required"] = status != "WARN"
    record["summary"]["status"] = status
    record["summary"]["counts"] = {
        key: sum(check["status"] == key for check in record["checks"])
        for key in record["summary"]["counts"]
    }
    record["case"]["semantic_sha256"] = canonical_sha256(definition)
    digest = canonical_sha256(record)
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    path = tmp_path / "false-failure-v1.json"
    write_pretty(path, envelope)
    verification = verify_target(path)
    assert not verification.valid
    assert any(item["status"] == "FAIL" and "passing completed result" in item["detail"]
               for item in verification.checks)


@pytest.mark.parametrize("schema", GENERATIONS)
def test_known_single_value_shape_cannot_omit_scalar_equality_evidence(schema):
    declared, result = _historical_result("absolute", 2.0, 2.0, schema)
    for field in ("actual", "expected", "absolute_error", "relative_error"):
        del result["observed"][field]
    with pytest.raises(ArtifactError):
        require_observation_metadata(declared, result, evidence_schema=schema)


def test_legacy_schema_requires_nonnegative_comparison_tolerances():
    from jsonschema import Draft202012Validator

    from pvs.schemas import load_schema

    case = base_case()
    case["schema"] = "pvs-case/1"
    declared, _ = _historical_result("close", 2.0, 2.0, LEGACY_EVIDENCE_SCHEMA)
    case["checks"] = [declared]
    validator = Draft202012Validator(load_schema("case-v1"))
    assert validator.is_valid(case)
    for field in ("absolute_tolerance", "relative_tolerance"):
        changed = copy.deepcopy(case)
        changed["checks"][0][field] = -math.ulp(0.0)
        errors = list(validator.iter_errors(changed))
        assert any(list(error.absolute_path) == ["checks", 0, field] for error in errors)
    declared, _ = _historical_result("absolute", 2.0, 2.0, LEGACY_EVIDENCE_SCHEMA)
    case["checks"] = [declared]
    assert validator.is_valid(case)
    declared["tolerance"] = -math.ulp(0.0)
    assert not validator.is_valid(case)


@pytest.mark.parametrize("dimension", ["pointer", "component", "reduce"])
def test_honest_distinct_selections_can_produce_verified_failure(tmp_path, dimension):
    check = _check(0, 0, "absolute")
    source = _source(component=0, reduce="first")
    expected = {**source, dimension: {"pointer": "/b", "component": 1, "reduce": "last"}[
        dimension]}
    check.update(actual=source, expected=expected)
    outcome, _ = _produce(tmp_path, check, {"a": [[2.0, 3.0], [4.0, 5.0]], "b": [[8.0, 9.0]]})
    assert outcome.status.value == "FAIL"


def test_inadmissible_self_selection_remains_verified_error(tmp_path):
    check = _check(0, 0, "absolute")
    check.update(actual=_source(pointer="/missing"), expected=_source(pointer="/missing"))
    outcome, envelope = _produce(tmp_path, check, {"a": [1.0, 2.0]})
    assert outcome.status.value == "ERROR"
    assert envelope["record"]["checks"][0]["observed"] == {}


@pytest.mark.parametrize("schema", GENERATIONS)
def test_one_binary64_step_is_not_an_equal_self_comparison(schema):
    declared, result = _historical_result("absolute", math.nextafter(2.0, math.inf), 2.0, schema)
    # Even a permissive scientific tolerance cannot change source identity.
    declared["tolerance"] = result["observed"]["permitted_error"] = 1.0
    result["status"] = "PASS"
    with pytest.raises(ArtifactError, match="identical comparison sources"):
        require_observation_metadata(declared, result, evidence_schema=schema)
