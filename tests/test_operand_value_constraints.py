"""Declaration-derived domains across check types and evidence generations."""

from __future__ import annotations

import copy
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_review_findings_037 import _declaration

from pvs.canonical import canonical_sha256
from pvs.checks.engine import evaluate_checks
from pvs.constants import EVIDENCE_SCHEMA, LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA
from pvs.errors import ArtifactError
from pvs.jsonutil import MAX_SAFE_INTEGER, write_pretty
from pvs.verification.comparisons import validate_comparison
from pvs.verification.observations import (
    CompletedObservation,
    InvalidObservation,
    UnevaluatedObservation,
    require_observation_metadata,
    validate_observation,
)
from pvs.verify import verify_target

GENERATIONS = [LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA]
ROLES = ["actual", "expected", "finite", "range", "term", "conservation_expected",
         "normalization"]


def _record(role: str, value: float, schema: str):
    declared = _declaration(role, value)
    if role in {"finite", "range"}:
        observed: dict[str, Any] = {
            "dtype": "float64", "shape": [], "size": 1, "finite_count": 1,
            "nonfinite_count": 0, "minimum": value, "maximum": value, "unit": "1",
        }
        if role == "range":
            declared.update(minimum=value, maximum=value)
            observed.update(failing_count=0, failing_indices=[])
    else:
        def literalize(item):
            if isinstance(item, dict):
                if "artifact" in item:
                    return {"value": value, "unit": "1"}
                return {key: literalize(child) for key, child in item.items()}
            if isinstance(item, list):
                return [literalize(child) for child in item]
            return item
        result = evaluate_checks([literalize(declared)], {})[0]
        assert result.status.value == "PASS"
        observed = result.observed
        if role in {"actual", "expected"} and schema != EVIDENCE_SCHEMA:
            observed = {key: (item.get("value") if isinstance(item, dict) else item)
                        for key, item in observed.items() if key != "profile"}
    return declared, {"status": "PASS", "observed": observed}


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("value", [0.5, float(2**53)])
def test_size_domain_rejects_fraction_and_unsafe_integer_in_every_generation(schema, role, value):
    declared, result = _record(role, value, schema)
    assert isinstance(
        validate_observation(declared, result, evidence_schema=schema), InvalidObservation
    )


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("role", ROLES[:-1])
def test_size_domain_rejects_negative_values_in_every_generation(schema, role):
    declared, result = _record(role, -1.0, schema)
    assert isinstance(
        validate_observation(declared, result, evidence_schema=schema), InvalidObservation
    )


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("value", [0.0, 2.0, float(MAX_SAFE_INTEGER)])
def test_size_observation_domain_accepts_zero_integral_float_and_safe_boundary(schema, role, value):
    # No array of MAX_SAFE_INTEGER elements is allocated or claimed to be replayed.
    # The declared artifact is unknown; this checks only the possible value domain.
    declared, result = _record(role, value, schema)
    actual = validate_observation(declared, result, evidence_schema=schema)
    assert isinstance(actual, CompletedObservation) and actual.passed


@pytest.mark.parametrize("reduction", ["sum", "mean", "min", "max", "first", "last"])
@pytest.mark.parametrize("value", [-1.0, 0.5])
def test_numeric_reductions_do_not_inherit_the_element_count_domain(reduction, value):
    declared, result = _record("actual", value, EVIDENCE_SCHEMA)
    declared["actual"]["reduce"] = reduction
    assert isinstance(validate_observation(declared, result, evidence_schema=EVIDENCE_SCHEMA),
                      CompletedObservation)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("kind", ["finite", "range"])
def test_size_summary_cannot_claim_nonfinite_count(schema, kind):
    declared, result = _record(kind, 2.0, schema)
    result["status"] = "FAIL"
    observed = result["observed"]
    observed.update(finite_count=0, nonfinite_count=1)
    del observed["minimum"], observed["maximum"]
    if kind == "range":
        observed.update(failing_count=1, failing_indices=[[]])
    assert isinstance(
        validate_observation(declared, result, evidence_schema=schema), InvalidObservation
    )


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("kind", ["finite", "range"])
def test_scalar_size_extrema_agree_including_the_legacy_metadata_path(schema, kind):
    declared, result = _record(kind, 2.0, schema)
    result["observed"]["minimum"] = 1.0
    with pytest.raises(ArtifactError, match="identical extrema"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("dtype", ["banana", "float32", "complex128", "int64", ">f8", ""])
@pytest.mark.parametrize("kind", ["finite", "range"])
def test_finite_summary_dtype_is_evaluation_binary64_in_each_generation(schema, dtype, kind):
    declared, result = _record(kind, 2.0, schema)
    del declared["source"]["reduce"]
    result["observed"]["dtype"] = dtype
    assert isinstance(
        validate_observation(declared, result, evidence_schema=schema), InvalidObservation
    )


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("status", ["ERROR", "SKIP"])
def test_unevaluated_size_checks_make_no_value_or_dtype_claim(schema, status):
    declared, _ = _record("finite", 2.0, schema)
    actual = validate_observation(
        declared, {"status": status, "observed": {}}, evidence_schema=schema
    )
    assert isinstance(actual, UnevaluatedObservation)


@pytest.mark.parametrize("metric", ["absolute", "l1", "l2", "linf", "relative", "close"])
@pytest.mark.parametrize("role", ["actual", "expected"])
@pytest.mark.parametrize("kind", ["compare", "reference"])
def test_direct_comparison_validator_enforces_size_for_each_metric(metric, role, kind):
    declared = _declaration(role, 0.5)
    declared["type"] = kind
    declared["metric"] = metric
    if metric == "relative":
        declared["scale_floor"] = 1.0
    elif metric == "close":
        del declared["tolerance"]
        declared.update(absolute_tolerance=0.0, relative_tolerance=0.0)
    literals = copy.deepcopy(declared)
    literals[role] = {"value": 0.5, "unit": "1"}
    observed = evaluate_checks([literals], {})[0].observed
    with pytest.raises(ArtifactError, match="size reduction"):
        validate_comparison(declared, observed)


@pytest.mark.parametrize("value", [True, False, math.nan, math.inf, -math.inf])
def test_size_summary_requires_a_real_finite_count(value):
    declared, result = _record("finite", 2.0, EVIDENCE_SCHEMA)
    result["observed"].update(minimum=value, maximum=value)
    assert isinstance(validate_observation(declared, result, evidence_schema=EVIDENCE_SCHEMA),
                      InvalidObservation)


@pytest.mark.parametrize("terms", [None, [], [{}], [None, None]])
def test_malformed_conservation_terms_return_invalid_observation(terms):
    declared, result = _record("term", 2.0, EVIDENCE_SCHEMA)
    result["observed"]["terms"] = terms
    assert isinstance(validate_observation(declared, result, evidence_schema=EVIDENCE_SCHEMA),
                      InvalidObservation)


@pytest.mark.parametrize("fixture", ["synthetic-json-evidence-v1.zip",
                                     "evidence-v2-0.2.8.zip", "synthetic-json-evidence-v3.zip"])
def test_frozen_generation_dtype_observations_remain_valid(fixture, tmp_path):
    with zipfile.ZipFile(Path(__file__).parent / "fixtures" / fixture) as archive:
        envelope = json.loads(archive.read(next(
            name for name in archive.namelist() if name.endswith("/evidence.json")
        )))
    record = envelope["record"]
    declarations = {check["id"]: check for check in record["case"]["resolved_definition"]["checks"]}
    checked = 0
    for result in record["checks"]:
        if result["type"] not in {"finite", "range"} or result["status"] in {"ERROR", "SKIP"}:
            continue
        assert result["observed"]["dtype"] == "float64"
        require_observation_metadata(
            declarations[result["id"]], result, evidence_schema=envelope["schema"]
        )
        checked += 1
    assert checked > 0
    path = tmp_path / "original.json"
    write_pretty(path, envelope)
    assert verify_target(path).valid


@pytest.mark.parametrize("dtype", ["banana", "float32", "complex128"])
def test_legacy_verification_rejects_rehashed_wrong_dtype(tmp_path, dtype):
    fixture = Path(__file__).parent / "fixtures/synthetic-json-evidence-v1.zip"
    with zipfile.ZipFile(fixture) as archive:
        envelope = json.loads(archive.read(next(
            name for name in archive.namelist() if name.endswith("/evidence.json")
        )))
    check = next(check for check in envelope["record"]["checks"] if check["type"] == "finite")
    check["observed"]["dtype"] = dtype
    digest = canonical_sha256(envelope["record"])
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    path = tmp_path / "forged-legacy.json"
    write_pretty(path, envelope)
    result = verify_target(path)
    assert not result.valid
    assert any(check["name"] == "check legacy observation metadata" and check["status"] == "FAIL"
               for check in result.checks)


@pytest.mark.parametrize("value", [-1.0, 0.5, float(2**53)])
def test_legacy_verification_enforces_size_without_modern_unit_requirements(tmp_path, value):
    fixture = Path(__file__).parent / "fixtures/synthetic-json-evidence-v1.zip"
    with zipfile.ZipFile(fixture) as archive:
        envelope = json.loads(archive.read(next(
            name for name in archive.namelist() if name.endswith("/evidence.json")
        )))
    record = envelope["record"]
    check = next(check for check in record["checks"] if check["type"] == "finite")
    declared = next(check_decl for check_decl in record["case"]["resolved_definition"]["checks"]
                    if check_decl["id"] == check["id"])
    assert "unit" not in declared
    declared["source"]["reduce"] = "size"
    check["observed"].update(shape=[], size=1, finite_count=1, nonfinite_count=0,
                             minimum=value, maximum=value)
    digest = canonical_sha256(record)
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    path = tmp_path / "forged-legacy-size.json"
    write_pretty(path, envelope)
    result = verify_target(path)
    assert not result.valid
    assert any(check["name"] == "check legacy observation metadata" and check["status"] == "FAIL"
               for check in result.checks)
