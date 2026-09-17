"""Necessary equality facts without identifying distinct selection expressions."""

from __future__ import annotations

import copy
import json
import math
import zipfile
from pathlib import Path

import pytest
from test_same_source_consistency import _produce

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.checks.engine import evaluate_checks
from pvs.checks.inspection import UnavailableOperand, inspect_operand
from pvs.constants import EVIDENCE_SCHEMA, LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA
from pvs.errors import ArtifactError
from pvs.jsonutil import load_strict, write_pretty
from pvs.verification.observations import require_observation_metadata
from pvs.verify import verify_target

GENERATIONS = [LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA]


def _record(value=2.0, coefficient=-1.0):
    literal = {"value": value, "unit": "1"}
    declaration = {
        "id": "balance", "type": "conservation", "unit": "1", "required": True,
        "terms": [
            {"label": "positive", "coefficient": 1.0, "value": literal},
            {"label": "negative", "coefficient": coefficient, "value": copy.deepcopy(literal)},
        ],
        "expected": {"value": value + coefficient * value, "unit": "1"},
        "absolute_tolerance": 0.0, "relative_tolerance": 0.0,
    }
    result = evaluate_checks([declaration], {})[0]
    assert result.status.value == "PASS"
    declaration = copy.deepcopy(declaration)
    source = {"artifact": "result", "pointer": "/a", "reduce": "sum", "unit": "1"}
    declaration["terms"][0]["value"] = source
    declaration["terms"][1]["value"] = dict(reversed(list(source.items())))
    return declaration, {"status": "PASS", "observed": result.observed}


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("status", ["PASS", "WARN", "FAIL"])
@pytest.mark.parametrize("coefficient", [-1.0, 0.0, 2.0])
def test_repeated_source_totals_must_agree_even_for_zero_coefficients(schema, status, coefficient):
    declared, result = _record(coefficient=coefficient)
    result["status"] = status
    result["observed"]["terms"][1]["raw_total"] = 1.0
    with pytest.raises(ArtifactError, match="identical raw totals"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("value", [0.0, -0.0, 2.0, -2.0, 0.5, 1e308, math.ulp(0.0)])
def test_reordered_source_keys_and_opposite_contributions_remain_valid(schema, value):
    declared, result = _record(value)
    if schema == LEGACY_EVIDENCE_SCHEMA:
        del declared["unit"], result["observed"]["unit"]
        for term in declared["terms"]:
            del term["value"]["unit"]
        for term in result["observed"]["terms"]:
            del term["unit"]
    require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
def test_even_one_binary64_step_in_raw_totals_is_rejected(schema):
    declared, result = _record(coefficient=0.0)
    result["observed"]["terms"][1]["raw_total"] = math.nextafter(2.0, math.inf)
    with pytest.raises(ArtifactError, match="identical raw totals"):
        require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("field,value", [
    ("artifact", "another"), ("pointer", "/b"), ("column", "other"),
    ("variable", "other"), ("component", 1), ("reduce", "size"), ("unit", "m"),
])
def test_source_identity_preserves_every_selection_dimension(field, value):
    source = {"artifact": "result", "pointer": "/a", "unit": "1"}
    first = inspect_operand(source)
    second = inspect_operand({**source, field: value})
    assert isinstance(first, UnavailableOperand) and isinstance(second, UnavailableOperand)
    assert first.source_identity != second.source_identity


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("field,value", [("artifact", "another"), ("pointer", "/b"),
                                         ("component", 1), ("reduce", "first")])
def test_different_source_selections_are_not_conflated(schema, field, value):
    declared, result = _record()
    declared["terms"][1]["value"][field] = value
    result["observed"]["terms"][1]["raw_total"] = 1.0
    require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
@pytest.mark.parametrize("status", ["ERROR", "SKIP"])
def test_unevaluated_checks_do_not_claim_repeated_totals(schema, status):
    declared, _ = _record()
    require_observation_metadata(declared, {"status": status, "observed": {}},
                                 evidence_schema=schema)


@pytest.mark.parametrize("schema", GENERATIONS)
def test_signed_zero_totals_are_numerically_identical(schema):
    declared, result = _record(0.0)
    result["observed"]["terms"][1]["raw_total"] = -0.0
    require_observation_metadata(declared, result, evidence_schema=schema)


@pytest.mark.parametrize("fixture", ["synthetic-json-evidence-v1.zip",
                                     "evidence-v2-0.2.8.zip", "synthetic-json-evidence-v3.zip"])
def test_frozen_generation_records_verify_with_their_declared_rules(tmp_path, fixture):
    with zipfile.ZipFile(Path(__file__).parent / "fixtures" / fixture) as archive:
        envelope = json.loads(archive.read(next(
            name for name in archive.namelist() if name.endswith("/evidence.json")
        )))
    path = tmp_path / "original.json"
    write_pretty(path, envelope)
    assert verify_target(path).valid


def test_rehashed_v2_repeated_sources_reach_the_observation_contract(tmp_path):
    fixture = Path(__file__).parent / "fixtures/evidence-v2-0.2.8.zip"
    with zipfile.ZipFile(fixture) as archive:
        envelope = json.loads(archive.read(next(
            name for name in archive.namelist() if name.endswith("/evidence.json")
        )))
    record = envelope["record"]
    check = next(item for item in record["checks"] if item["type"] == "conservation")
    declared = next(item for item in record["case"]["resolved_definition"]["checks"]
                    if item["id"] == check["id"])
    declared["terms"][1]["value"] = copy.deepcopy(declared["terms"][0]["value"])
    check["observed"]["terms"][1]["raw_total"] = (
        check["observed"]["terms"][0]["raw_total"] + 1.0
    )
    digest = canonical_sha256(record)
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    path = tmp_path / "inconsistent-v2.json"
    write_pretty(path, envelope)
    result = verify_target(path)
    assert not result.valid
    assert any(item["name"] == "check observation contract"
               and item["status"] == "FAIL" and "identical raw totals" in item["detail"]
               for item in result.checks)


def test_producer_self_comparisons_and_repeated_checks_remain_coherent(tmp_path):
    _produce(tmp_path, reduction="size", expected=0.0)
    path = tmp_path / "case/pvs.yaml"
    case = load_strict(path)
    original = case["checks"][0]
    repeated = copy.deepcopy(original)
    repeated["id"] = "balance-again"
    case["checks"].append(repeated)
    source = original["terms"][0]["value"]
    for metric in ("absolute", "relative", "l1", "l2", "linf", "close"):
        check = {"id": f"self-{metric}", "type": "compare", "metric": metric, "unit": "1",
                 "actual": copy.deepcopy(source), "expected": copy.deepcopy(source)}
        if metric == "close":
            check.update(absolute_tolerance=0.0, relative_tolerance=0.0)
        else:
            check["tolerance"] = 0.0
        if metric == "relative":
            check["scale_floor"] = 1.0
        case["checks"].append(check)
    write_pretty(path, case)
    outcome = validate_case(tmp_path / "case", output_dir=tmp_path / "relationships")
    assert outcome.status.value == "PASS"
    assert verify_target(outcome.output_directory).valid
    checks = load_strict(outcome.evidence_path)["record"]["checks"]
    assert checks[0]["observed"] == checks[1]["observed"]
    assert all(check["observed"]["gating_error"] == 0.0 for check in checks[2:])
