"""Package-level reproductions reconstructed from the supplied 0.3.7 review.

Rehashing preserves internal identities, not the original external Package ID.
These tests leave the retained case and input bytes unchanged.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case, dump_case
from contract_helpers import rehash_record

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.checks.engine import evaluate_checks
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict, write_pretty
from pvs.verify import verify_target


def _declaration(role: str, anchor: float) -> dict[str, Any]:
    source = {"artifact": "result", "reduce": "size", "unit": "1"}
    literal = {"value": anchor, "unit": "1"}
    check: dict[str, Any] = {"id": "count", "required": True, "unit": "1"}
    if role in {"actual", "expected"}:
        check.update(type="compare", metric="absolute", tolerance=0.0,
                     actual=source if role == "actual" else literal,
                     expected=source if role == "expected" else literal)
    elif role in {"finite", "range"}:
        check.update(type=role, source=source)
        if role == "range":
            check.update(minimum=-100.0, maximum=100.0)
    else:
        check.update(type="conservation", absolute_tolerance=0.0, relative_tolerance=0.0,
                     terms=[{"label": "count", "coefficient": 1.0,
                             "value": source if role == "term" else literal},
                            {"label": "zero", "coefficient": -1.0,
                             "value": {"value": 0.0, "unit": "1"}}],
                     expected=copy.deepcopy(source if role == "conservation_expected" else literal))
        if role == "normalization":
            check.update(normalization=source, relative_tolerance=1.0)
    return check


def _produce(root: Path, check: dict[str, Any], values: Any = None):
    root.mkdir()
    (root / "result.json").write_text(
        json.dumps([1.0, 2.0] if values is None else values) + "\n", encoding="utf-8"
    )
    case = base_case()
    case["checks"] = [check]
    case["package"]["embed_artifacts"] = True
    outcome = validate_case(dump_case(root / "pvs.yaml", case), output_dir=root / "package")
    assert verify_target(outcome.output_directory).valid
    return outcome, load_strict(outcome.evidence_path)


def _replace_observation(outcome, envelope: dict[str, Any], observed: dict[str, Any]):
    package = outcome.output_directory
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    original_pin = manifest["integrity"]["package_id"]
    preserved = {entry["path"]: (package / entry["path"]).read_bytes()
                 for entry in manifest["package"]["files"] if entry["path"] != "evidence.json"}
    envelope["record"]["checks"][0].update(observed=observed, status="PASS")
    rehash_record(envelope, outcome.evidence_path)
    manifest["package"]["evidence_id"] = envelope["integrity"]["evidence_id"]
    for entry in manifest["package"]["files"]:
        entry.update(file_identity(package / entry["path"]))
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"].update(digest=digest, package_id=f"pvs-package:sha256:{digest}")
    write_pretty(manifest_path, manifest)
    assert all((package / name).read_bytes() == data for name, data in preserved.items())
    assert not verify_target(package, expect_package_id=original_pin).valid
    return verify_target(package)


def _impossible_observation(envelope: dict[str, Any], role: str, value: float):
    observed = copy.deepcopy(envelope["record"]["checks"][0]["observed"])
    if role in {"finite", "range"}:
        observed.update(minimum=value, maximum=value)
        return observed
    declared = copy.deepcopy(envelope["record"]["case"]["resolved_definition"]["checks"][0])
    literal = {"value": value, "unit": "1"}
    if role in {"actual", "expected"}:
        declared[role] = literal
    elif role == "term":
        declared["terms"][0]["value"] = literal
    elif role == "conservation_expected":
        declared["expected"] = literal
    else:
        declared["normalization"] = literal
    template = evaluate_checks([declared], {})[0]
    assert template.status.value == "PASS"
    return template.observed


INVALID_SIZES = [
    (role, value)
    for role in ("actual", "expected", "finite", "range", "term", "conservation_expected")
    for value in (-1.0, 0.5)
] + [("normalization", 0.5), ("normalization", float(2**53))]


@pytest.mark.parametrize("role,value", INVALID_SIZES)
def test_rehashed_package_rejects_impossible_size(tmp_path: Path, role: str, value: float):
    outcome, envelope = _produce(tmp_path / "case", _declaration(role, value))
    if role in {"actual", "expected", "term", "conservation_expected"}:
        assert envelope["record"]["checks"][0]["status"] == "FAIL"
    verification = _replace_observation(
        outcome, envelope, _impossible_observation(envelope, role, value)
    )
    assert not verification.valid, f"impossible {role} size {value} accepted: {verification}"
    assert any(check["status"] == "FAIL" and "observation" in check["name"]
               for check in verification.checks)


@pytest.mark.parametrize("kind", ["finite", "range"])
@pytest.mark.parametrize("dtype", ["banana", "float32", "complex128"])
def test_rehashed_package_rejects_wrong_evaluation_dtype(
    tmp_path: Path, kind: str, dtype: str,
):
    check = _declaration(kind, 2.0)
    del check["source"]["reduce"]
    outcome, envelope = _produce(tmp_path / "case", check)
    observed = copy.deepcopy(envelope["record"]["checks"][0]["observed"])
    assert observed["dtype"] == "float64"
    observed["dtype"] = dtype
    assert not _replace_observation(outcome, envelope, observed).valid


@pytest.mark.parametrize("values", [[], [1], ["a", "b"]])
@pytest.mark.parametrize("role", ["actual", "expected", "finite", "range", "term",
                                  "conservation_expected", "normalization"])
def test_honest_size_packages_preserve_zero_and_integer_counts(
    tmp_path: Path, values: list[Any], role: str,
):
    outcome, envelope = _produce(tmp_path / "case", _declaration(role, float(len(values))), values)
    assert envelope["record"]["checks"][0]["status"] == "PASS"
    assert verify_target(outcome.output_directory, expect_package_id=outcome.package_id).valid


def test_size_conservation_allows_negative_weighted_contributions(tmp_path: Path):
    check = _declaration("term", -4.0)
    check["terms"][0]["coefficient"] = -2.0
    outcome, envelope = _produce(tmp_path / "case", check)
    term = envelope["record"]["checks"][0]["observed"]["terms"][0]
    assert term["raw_total"] == 2.0 and term["contribution"] == -4.0
    assert verify_target(outcome.output_directory).valid
