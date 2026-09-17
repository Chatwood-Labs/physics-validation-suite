"""Conservation relations close over terms, expected and normalization sources."""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import pytest

from pvs.api import RunOutcome, validate_case
from pvs.canonical import canonical_sha256
from pvs.checks.engine import evaluate_checks
from pvs.constants import EVIDENCE_SCHEMA, LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA
from pvs.errors import ArtifactError
from pvs.finding import create_finding_metadata
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict, write_pretty
from pvs.verification.observations import require_observation_metadata
from pvs.verify import verify_target

GENERATIONS = [LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA]
RELATIONS = ["term-expected", "term-normalization", "expected-normalization", "all"]


def _declaration(value: Any = -2.0) -> dict[str, Any]:
    return {
        "id": "relation", "type": "conservation", "unit": "1", "required": True,
        "terms": [
            {"label": "source", "coefficient": 1.0, "value": {"value": value, "unit": "1"}},
            {"label": "zero", "coefficient": 1.0, "value": {"value": 0.0, "unit": "1"}},
        ],
        "expected": {"value": value, "unit": "1"},
        "normalization": {"value": value, "unit": "1"},
        "absolute_tolerance": 0.0, "relative_tolerance": 0.0,
    }


def _use_source(declared: dict[str, Any], relation: str, source: dict[str, Any]) -> None:
    if relation in {"term-expected", "term-normalization", "all"}:
        declared["terms"][0]["value"] = copy.deepcopy(source)
    if relation in {"term-expected", "expected-normalization", "all"}:
        declared["expected"] = dict(reversed(list(source.items())))
    if relation in {"term-normalization", "expected-normalization", "all"}:
        declared["normalization"] = copy.deepcopy(source)


def _record(relation: str, value: float = -2.0) -> tuple[dict[str, Any], dict[str, Any]]:
    declared = _declaration(value)
    check = evaluate_checks([declared], {})[0]
    assert check.status.value == "PASS"
    source = {"artifact": "result", "pointer": "/a", "unit": "1"}
    _use_source(declared, relation, source)
    return declared, {"status": "PASS", "observed": check.observed}


def _historical_units(declared: dict[str, Any], result: dict[str, Any], schema: str) -> None:
    if schema != LEGACY_EVIDENCE_SCHEMA:
        return
    del declared["unit"], result["observed"]["unit"]
    for term in declared["terms"]:
        del term["value"]["unit"]
    for term in result["observed"]["terms"]:
        del term["unit"]
    del declared["expected"]["unit"], declared["normalization"]["unit"]


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("relation", RELATIONS)
@pytest.mark.parametrize("status", ["PASS", "WARN", "FAIL"])
def test_completed_source_contradictions_are_rejected_for_each_generation(schema, relation, status):
    declared, result = _record(relation)
    _historical_units(declared, result, schema)
    result["status"] = status
    name = "expected" if relation == "term-expected" else "normalization"
    result["observed"][name] = math.nextafter(result["observed"][name], math.inf)
    with pytest.raises(ArtifactError, match="identical conservation sources"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("relation", RELATIONS)
@pytest.mark.parametrize("value", [-2.0, 0.0, -0.0, 0.5, math.ulp(0.0), 1e308])
def test_scalar_source_sign_and_magnitude_relations_preserve_valid_records(schema, relation, value):
    declared, result = _record(relation, value)
    _historical_units(declared, result, schema)
    if value == 0:
        result["observed"]["expected"] = -0.0
        result["observed"]["normalization"] = 0.0
        result["observed"]["terms"][0]["raw_total"] = 0.0
    require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("coefficient", [-2.0, 0.0, 2.0])
def test_term_expected_relation_uses_unweighted_total_even_with_zero_coefficient(
    schema, coefficient,
):
    declared, result = _record("term-expected")
    declared["terms"][0]["coefficient"] = coefficient
    result["observed"]["terms"][0]["coefficient"] = coefficient
    result["observed"]["terms"][0]["contribution"] = coefficient * -2.0
    require_observation_metadata(declared, result, evidence_schema=schema)
    result["observed"]["expected"] = -1.0
    with pytest.raises(ArtifactError, match="expected to equal the raw total"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("status", ["ERROR", "SKIP"])
def test_unevaluated_conservation_sources_require_no_completed_values(schema, status):
    declared, _ = _record("all")
    require_observation_metadata(declared, {"status": status, "observed": {}},
                                 evidence_schema=schema)


@pytest.mark.parametrize("relation", RELATIONS[:-1])
@pytest.mark.parametrize("field,value", [
    ("artifact", "other"), ("pointer", "/b"), ("column", "b"), ("variable", "b"),
    ("component", 0), ("reduce", "first"), ("unit", "other"),
])
def test_source_relations_preserve_each_distinct_selection_dimension(relation, field, value):
    declared, result = _record(relation)
    name = "expected" if relation == "term-expected" else "normalization"
    declared[name][field] = value
    result["observed"][name] = 123.0
    # This tests the equality premise, not unit compatibility or all arithmetic.
    require_observation_metadata(declared, result, evidence_schema=EVIDENCE_SCHEMA)


def _package(root: Path, declared: dict[str, Any], data: Any) -> RunOutcome:
    case_root = root / "case"
    case_root.mkdir(parents=True)
    write_pretty(case_root / "result.json", {"a": data})
    write_pretty(case_root / "pvs.yaml", {
        "schema": "pvs-case/2", "id": "conservation-relations", "version": "1.0.0",
        "title": "Conservation scalar uses bind identical selections",
        "classifications": ["verification"],
        "subject": {"name": "synthetic", "version": "1.0.0"},
        "artifacts": {"result": {"path": "result.json", "role": "output", "format": "json"}},
        "checks": [declared],
        "package": {"embed_artifacts": True, "html": False, "pdf": False},
    })
    return validate_case(case_root, output_dir=root / "package")


def _rehash(outcome: RunOutcome, envelope: dict[str, Any]) -> None:
    record = envelope["record"]
    result = record["checks"][0]
    record["summary"]["status"] = result["status"]
    record["summary"]["counts"] = {
        name: int(name == result["status"]) for name in record["summary"]["counts"]
    }
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


@pytest.mark.parametrize("relation", RELATIONS[:-1])
def test_full_package_rejects_rehashed_negative_scalar_relations(tmp_path, relation):
    declared = _declaration(-2.0)
    source = {"artifact": "result", "pointer": "/a", "unit": "1"}
    _use_source(declared, relation, source)
    if relation == "term-expected":
        declared["terms"][0]["coefficient"] = 2.0
    elif relation == "term-normalization":
        declared["expected"] = {"value": 0.0, "unit": "1"}
        declared["relative_tolerance"] = 0.5
    else:
        declared["terms"][0]["value"]["value"] = -4.0
        declared["relative_tolerance"] = 0.5
    # Size-one arrays need not have a scalar shape to establish this relation.
    outcome = _package(tmp_path, declared, [[-2.0]])
    assert outcome.status.value == "FAIL"
    assert verify_target(outcome.output_directory).valid
    preserved = {
        path.relative_to(outcome.output_directory): path.read_bytes()
        for directory in ("case", "artifacts")
        for path in (outcome.output_directory / directory).rglob("*") if path.is_file()
    }
    envelope = load_strict(outcome.evidence_path)
    result = envelope["record"]["checks"][0]
    result.update(status="PASS", summary="conservation residual is within tolerance")
    if relation == "term-expected":
        result["observed"].update(expected=-4.0, absolute_error=0.0)
    else:
        result["observed"].update(normalization=4.0, permitted_error=2.0)
    _rehash(outcome, envelope)
    assert all((outcome.output_directory / path).read_bytes() == data
               for path, data in preserved.items())
    assert not verify_target(outcome.output_directory, expect_package_id=outcome.package_id).valid
    verification = verify_target(outcome.output_directory)
    assert not verification.valid
    assert any(check["status"] == "FAIL" and "identical conservation sources" in check["detail"]
               for check in verification.checks)


@pytest.mark.parametrize("data,selection", [
    (-2.0, {}), ([-2.0], {}), ([[-2.0]], {}), ([-2.0, 3.0], {"component": 0}),
    ([-2.0, 3.0], {"reduce": "first"}), ([], {"reduce": "size"}),
    ([2.0, 3.0], {"reduce": "sum"}), ([2.0, 3.0], {"reduce": "size"}),
])
def test_honest_scalar_and_size_one_array_source_uses_verify(tmp_path, data, selection):
    declared = _declaration()
    _use_source(declared, "all", {"artifact": "result", "pointer": "/a", "unit": "1",
                                  **selection})
    outcome = _package(tmp_path, declared, data)
    assert outcome.status.value == "PASS"
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("data", [[], [-2.0, 3.0]])
def test_non_scalar_expected_source_remains_a_verified_error(tmp_path, data):
    declared = _declaration()
    _use_source(declared, "all", {"artifact": "result", "pointer": "/a", "unit": "1"})
    outcome = _package(tmp_path, declared, data)
    assert outcome.status.value == "ERROR"
    assert verify_target(outcome.output_directory).valid


def test_general_array_total_is_not_compared_to_a_distinct_scalar_selection(tmp_path):
    declared = _declaration()
    source = {"artifact": "result", "pointer": "/a", "unit": "1"}
    declared["terms"][0]["value"] = source
    declared["expected"] = {**source, "component": 0}
    declared["normalization"] = {**source, "component": 1}
    declared["relative_tolerance"] = 1.0
    outcome = _package(tmp_path, declared, [2.0, 3.0])
    assert outcome.status.value == "PASS"
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("artifact_expected", [False, True])
@pytest.mark.parametrize("value", [-2.0, 0.0, -0.0, 0.5, math.ulp(0.0), 1e308])
def test_implicit_normalization_preserves_expected_magnitude_in_every_generation(
    schema, artifact_expected, value,
):
    relation = "term-expected" if artifact_expected else "term-normalization"
    declared, result = _record(relation, value)
    _historical_units(declared, result, schema)
    del declared["normalization"]
    require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("artifact_expected", [False, True])
def test_implicit_normalization_contradiction_is_rejected_even_without_artifact_expected(
    schema, artifact_expected,
):
    declared, result = _record("term-expected" if artifact_expected else "term-normalization")
    _historical_units(declared, result, schema)
    del declared["normalization"]
    result["observed"]["normalization"] = math.nextafter(2.0, math.inf)
    with pytest.raises(ArtifactError, match="implicit conservation normalization"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("status", ["ERROR", "SKIP"])
def test_implicit_normalization_does_not_require_unevaluated_values(schema, status):
    declared, _ = _record("term-expected")
    del declared["normalization"]
    require_observation_metadata(declared, {"status": status, "observed": {}},
                                 evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("artifact_normalization", [False, True])
@pytest.mark.parametrize("invalid", [-1.0, True, math.inf])
def test_unrelated_normalization_still_requires_an_abs_output_domain(
    schema, artifact_normalization, invalid,
):
    declared, result = _record("term-expected")
    if artifact_normalization:
        declared["normalization"] = {"artifact": "other", "pointer": "/b", "unit": "1"}
    _historical_units(declared, result, schema)
    require_observation_metadata(declared, result, evidence_schema=schema)
    result["observed"]["normalization"] = invalid
    message = "normalization must record a finite nonnegative scalar"
    with pytest.raises(ArtifactError, match=message):
        require_observation_metadata(declared, result, evidence_schema=schema)
