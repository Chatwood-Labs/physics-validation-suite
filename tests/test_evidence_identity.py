from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from rfc8785 import FloatDomainError

from pvs.canonical import canonical_bytes, canonical_sha256, evidence_id
from pvs.jsonutil import load_strict, loads_strict
from pvs.verify import verify_target


def _rehash(envelope: dict) -> None:
    digest = canonical_sha256(envelope["record"])
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = f"pvs:sha256:{digest}"


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def test_rfc8785_identity_is_independent_of_mapping_insertion_order() -> None:
    left = {"z": [3, 2, 1], "a": {"beta": True, "alpha": "value"}}
    right = {"a": {"alpha": "value", "beta": True}, "z": [3, 2, 1]}

    assert canonical_bytes(left) == canonical_bytes(right)
    assert canonical_sha256(left) == canonical_sha256(right)
    assert evidence_id(left) == f"pvs:sha256:{canonical_sha256(left)}"


def test_rfc8785_bytes_are_hashed_directly() -> None:
    value = {"string": '€$\x0f\nA\'B"\\"/', "numbers": [1, 0.0, -0.0, 1e-7]}

    encoded = canonical_bytes(value)

    assert canonical_sha256(value) == hashlib.sha256(encoded).hexdigest()
    assert loads_strict(encoded.decode("utf-8")) == value
    assert b"-0" not in encoded


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonicalization_rejects_nonfinite_numbers(value: float) -> None:
    with pytest.raises(FloatDomainError):
        canonical_bytes({"bad": value})


def test_generated_evidence_digest_and_id_bind_only_the_record(package_factory) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    digest = canonical_sha256(envelope["record"])
    assert outcome.finding_id is not None

    assert envelope["integrity"] == {
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "digest": digest,
        "evidence_id": f"pvs:sha256:{digest}",
        "finding": {
            "projection": "pvs-finding-projection/1",
            "canonicalization": "RFC8785",
            "algorithm": "sha256",
            "status": "ISSUED",
            "digest": outcome.finding_id.rsplit(":", 1)[-1],
            "finding_id": outcome.finding_id,
        },
    }
    assert outcome.evidence_id == f"pvs:sha256:{digest}"
    assert verify_target(package / "evidence.json").valid is True


def test_integrity_envelope_changes_do_not_change_record_digest(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    original = canonical_sha256(envelope["record"])
    envelope["integrity"]["digest"] = "0" * 64

    assert canonical_sha256(envelope["record"]) == original


def test_one_byte_semantic_evidence_mutation_fails_verification(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["record"]["case"]["title"] = "Tampered title"
    _write(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert (
        next(item for item in result.checks if item["name"] == "canonical record digest")["status"]
        == "FAIL"
    )


def test_rehashed_summary_count_forgery_is_detected(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["record"]["summary"]["counts"]["PASS"] += 1
    _rehash(envelope)
    _write(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert (
        next(item for item in result.checks if item["name"] == "canonical record digest")["status"]
        == "PASS"
    )
    assert (
        next(item for item in result.checks if item["name"] == "summary counts")["status"] == "FAIL"
    )


def test_rehashed_overall_status_forgery_is_detected(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["record"]["summary"]["status"] = "ERROR"
    _rehash(envelope)
    _write(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert (
        next(item for item in result.checks if item["name"] == "summary status")["status"] == "FAIL"
    )


@pytest.mark.parametrize("collection", ["checks", "artifacts", "references"])
def test_rehashed_duplicate_record_ids_are_detected(package_factory, collection: str) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    values = envelope["record"][collection]
    if collection == "references" and not values:
        values.append(
            {
                "id": "synthetic-reference",
                "type": "analytic",
                "citation": "Synthetic reference for verifier testing.",
                "locator": "tests/test_evidence_identity.py",
            }
        )
    values.append(copy.deepcopy(values[0]))
    if collection == "checks":
        status = values[0]["status"]
        envelope["record"]["summary"]["checks_total"] += 1
        envelope["record"]["summary"]["counts"][status] += 1
    _rehash(envelope)
    _write(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    check_name = {
        "checks": "check ID uniqueness",
        "artifacts": "artifact ID uniqueness",
        "references": "reference ID uniqueness",
    }[collection]
    assert next(item for item in result.checks if item["name"] == check_name)["status"] == "FAIL"


def test_expected_evidence_id_anchors_rehashed_alternative_record(package_factory) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    changed_title = "Different, internally consistent evidence"
    envelope["record"]["case"]["title"] = changed_title
    envelope["record"]["case"]["resolved_definition"]["title"] = changed_title
    envelope["record"]["case"]["semantic_sha256"] = canonical_sha256(
        envelope["record"]["case"]["resolved_definition"]
    )
    _rehash(envelope)
    _write(evidence_path, envelope)

    unanchored = verify_target(evidence_path)
    anchored = verify_target(evidence_path, expect_evidence_id=outcome.evidence_id)

    assert unanchored.valid is True
    assert unanchored.evidence_id != outcome.evidence_id
    assert anchored.valid is False
    assert (
        next(item for item in anchored.checks if item["name"] == "expected Evidence ID")["status"]
        == "FAIL"
    )


def test_duplicate_keys_and_nonfinite_values_in_evidence_fail_strict_parse(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"pvs-evidence/1","record":{},"record":{}}', encoding="utf-8")
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"schema":"pvs-evidence/1","record":{"x":NaN}}', encoding="utf-8")

    for path in (duplicate, nonfinite):
        result = verify_target(path)
        assert result.valid is False
        assert result.checks == [
            {
                "name": "strict evidence JSON",
                "status": "FAIL",
                "detail": result.checks[0]["detail"],
            }
        ]


def test_standalone_evidence_has_no_package_identity(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)

    result = verify_target(
        package / "evidence.json",
        expect_package_id="pvs-package:sha256:" + "0" * 64,
    )

    assert result.valid is False
    assert result.level == "record"
    assert (
        next(item for item in result.checks if item["name"] == "expected Package ID")["detail"]
        == "standalone evidence has no Package ID"
    )
