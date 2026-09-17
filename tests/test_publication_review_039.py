"""Regression tests for PVS 0.3.9 publication review.

Run against an installed PVS development environment:
    python -m pytest -q test_publication_review_039.py

On the reviewed 0.3.9 wheel, the three rejection tests fail and the controls
pass. These tests exercise internal semantic consistency of unsigned packages.
They do not bypass an externally trusted Package ID or demonstrate that the
honest producer computes an incorrect scientific result.

No network, subject execution, or modification of installed PVS code is used.
All evidence packages are created in pytest's temporary directory.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from pvs.api import RunOutcome, validate_case
from pvs.canonical import canonical_sha256
from pvs.finding import create_finding_metadata
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict, write_pretty
from pvs.verify import verify_target

KINDS = ("conservation-expected", "conservation-normalization", "self-compare")


def _produce(root: Path, kind: str) -> tuple[RunOutcome, dict[str, Any]]:
    source = {"artifact": "result", "pointer": "/a", "reduce": "size", "unit": "1"}
    zero = {"coefficient": 1.0, "value": {"value": 0.0, "unit": "1"}}
    if kind == "conservation-expected":
        # x = size([10, 20]) = 2; 2*x + 0 == x is false at zero tolerance.
        check = {
            "id": "relationship",
            "type": "conservation",
            "unit": "1",
            "terms": [{"coefficient": 2.0, "value": source}, zero],
            "expected": copy.deepcopy(source),
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
        }
    elif kind == "conservation-normalization":
        # Residual x = 2 exceeds the permitted 0.5*abs(x) = 1.
        check = {
            "id": "relationship",
            "type": "conservation",
            "unit": "1",
            "terms": [{"coefficient": 1.0, "value": source}, zero],
            "expected": {"value": 0.0, "unit": "1"},
            "normalization": copy.deepcopy(source),
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.5,
        }
    elif kind == "self-compare":
        # Comparing exactly the same finite scalar selection must have zero error.
        check = {
            "id": "relationship",
            "type": "compare",
            "unit": "1",
            "actual": source,
            "expected": copy.deepcopy(source),
            "metric": "absolute",
            "tolerance": 0.0,
        }
    else:
        raise ValueError(f"Unknown regression kind: {kind}")
    case_root = root / "case"
    case_root.mkdir(parents=True)
    write_pretty(case_root / "result.json", {"a": [10, 20]})
    case = {
        "schema": "pvs-case/2",
        "id": kind,
        "version": "1.0.0",
        "title": "Publication review: relationships within a single check",
        "classifications": ["verification"],
        "subject": {"name": "synthetic", "version": "1.0.0"},
        "artifacts": {
            "result": {"path": "result.json", "role": "output", "format": "json"},
        },
        "checks": [check],
        "package": {"embed_artifacts": True, "html": False, "pdf": False},
    }
    write_pretty(case_root / "pvs.yaml", case)
    outcome = validate_case(case_root, output_dir=root / "package")
    return outcome, load_strict(outcome.evidence_path)


def _mutate(envelope: dict[str, Any], kind: str) -> None:
    check = envelope["record"]["checks"][0]
    observed = check["observed"]
    if kind == "conservation-expected":
        check["status"] = "PASS"
        check["summary"] = "conservation residual is within tolerance"
        observed.update(expected=4.0, normalization=4.0, absolute_error=0.0)
    elif kind == "conservation-normalization":
        check["status"] = "PASS"
        check["summary"] = "conservation residual is within tolerance"
        observed.update(normalization=4.0, permitted_error=2.0)
    elif kind == "self-compare":
        check["status"] = "FAIL"
        check["summary"] = "comparison exceeds tolerance"
        observed.update(actual=3.0, expected=2.0, absolute_error=1.0, gating_error=1.0)
        for key in ("l1_error", "l2_error", "linf_error"):
            observed[key] = {"status": "AVAILABLE", "value": 1.0}
        observed["relative_error"] = {"status": "AVAILABLE", "value": 0.5}
    else:
        raise ValueError(f"Unknown regression kind: {kind}")


def _rehash(outcome: RunOutcome, envelope: dict[str, Any]) -> None:
    """Recompute all unsigned IDs, rather than merely damaging a checksum."""
    record = envelope["record"]
    counts = {key: 0 for key in record["summary"]["counts"]}
    for check in record["checks"]:
        counts[check["status"]] += 1
    record["summary"]["counts"] = counts
    record["summary"]["status"] = record["checks"][0]["status"]
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


def _preserved_inputs(outcome: RunOutcome) -> dict[Path, bytes]:
    return {
        path.relative_to(outcome.output_directory): path.read_bytes()
        for directory in ("case", "artifacts")
        for path in (outcome.output_directory / directory).rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("kind", KINDS)
def test_unmodified_producer_packages_remain_valid(tmp_path: Path, kind: str) -> None:
    outcome, _ = _produce(tmp_path, kind)
    expected = "PASS" if kind == "self-compare" else "FAIL"
    assert outcome.status.value == expected
    assert verify_target(outcome.output_directory).valid
    assert verify_target(outcome.output_directory, expect_package_id=outcome.package_id).valid


@pytest.mark.parametrize("kind", KINDS)
def test_original_external_package_pin_rejects_mutation(tmp_path: Path, kind: str) -> None:
    outcome, envelope = _produce(tmp_path, kind)
    _mutate(envelope, kind)
    _rehash(outcome, envelope)
    assert not verify_target(outcome.output_directory, expect_package_id=outcome.package_id).valid


@pytest.mark.parametrize("kind", KINDS)
def test_reject_contradictory_same_source_observations(tmp_path: Path, kind: str) -> None:
    outcome, envelope = _produce(tmp_path, kind)
    assert verify_target(outcome.output_directory).valid
    original_inputs = _preserved_inputs(outcome)
    _mutate(envelope, kind)
    _rehash(outcome, envelope)
    assert all(
        (outcome.output_directory / relative).read_bytes() == content
        for relative, content in original_inputs.items()
    ), "The case and retained scientific artifacts must not change in this regression"
    verification = verify_target(outcome.output_directory)
    assert not verification.valid, (
        f"Accepted contradictory {kind} evidence: "
        f"status={verification.evidence_status}, trust={verification.trust}"
    )
