from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.cli import main
from pvs.finding import create_finding_metadata, finding_projection
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.verify import verify_target


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _rehash(envelope: dict[str, Any]) -> None:
    digest = canonical_sha256(envelope["record"])
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = f"pvs:sha256:{digest}"


def _downgrade_envelope_to_v1(envelope: dict[str, Any]) -> None:
    """Convert the simple synthetic current fixture into a genuine v1 record."""

    envelope["record"]["pvs"].pop("comparison_profile", None)
    envelope["schema"] = "pvs-evidence/1"
    record = envelope["record"]
    record["pvs"]["case_schema"] = "pvs-case/1"
    record["pvs"]["evidence_schema"] = "pvs-evidence/1"
    record["pvs"].pop("content_identity", None)
    record["pvs"].pop("build", None)
    definition = record["case"]["resolved_definition"]
    definition["schema"] = "pvs-case/1"
    record["case"]["semantic_sha256"] = canonical_sha256(definition)
    record.pop("package")
    envelope["integrity"].pop("finding")
    _rehash(envelope)


def _projection_record() -> dict[str, Any]:
    check = {
        "id": "bounded",
        "type": "range",
        "source": {"artifact": "result", "pointer": "/value"},
        "minimum": 0.0,
        "maximum": 2.0,
    }
    return {
        "run": {
            "run_id": "urn:uuid:12345678-1234-4234-8234-123456789abc",
            "mode": "run",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "duration_seconds": 1.0,
        },
        "pvs": {"version": "0.2.3", "implementation": "implementation-a"},
        "case": {
            "id": "finding-golden",
            "version": "1.0",
            "title": "Golden projection prose",
            "description": "Not finding identity",
            "filename": "pvs.yaml",
            "raw_sha256": "1" * 64,
            "raw_size_bytes": 1,
            "semantic_sha256": "2" * 64,
            "classifications": ["validation", "verification"],
            "resolved_definition": {
                "schema": "pvs-case/1",
                "id": "finding-golden",
                "version": "1.0",
                "title": "Golden projection prose",
                "description": "Not finding identity",
                "classifications": ["validation", "verification"],
                "subject": {
                    "name": "solver",
                    "version": "4.0",
                    "revision": "abc123",
                    "description": "Prose",
                },
                "artifacts": {},
                "references": [
                    {
                        "id": "published-value",
                        "type": "published",
                        "citation": "A. Author, Exact source",
                        "locator": "Table 2, row 1",
                        "artifact": "input",
                        "accessed_utc": "2026-01-01",
                        "derivation": "Free-form derivation prose",
                        "notes": "Free-form notes",
                    }
                ],
                "checks": [check],
            },
        },
        "subject": {
            "name": "solver",
            "version": "4.0",
            "revision": "abc123",
            "description": "Prose",
        },
        "execution": {
            "mode": "run",
            "command": ["solver", "--config", "input.json"],
            "working_directory": ".",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "duration_seconds": 1.0,
            "return_code": 0,
            "expected_exit_codes": [0],
            "succeeded": True,
            "error": None,
            "declared_environment": {},
            "resolved_executable": {"sha256": "3" * 64},
            "stdout": {"sha256": "4" * 64},
        },
        "artifacts": [
            {
                "id": "result",
                "role": "output",
                "format": "json",
                "required": True,
                "validation_input": {
                    "exists": True,
                    "sha256": "5" * 64,
                    "size_bytes": 100,
                },
                "package_path": "artifacts/output/result/result.json",
            },
            {
                "id": "input",
                "role": "reference",
                "format": "json",
                "required": True,
                "validation_input": {
                    "exists": True,
                    "sha256": "6" * 64,
                    "size_bytes": 20,
                },
                "package_path": "artifacts/reference/input/input.json",
            },
        ],
        "references": [
            {
                "id": "published-value",
                "type": "published",
                "citation": "A. Author, Exact source",
                "locator": "Table 2, row 1",
                "artifact": "input",
                "accessed_utc": "2026-01-01",
                "derivation": "Free-form derivation prose",
                "notes": "Free-form notes",
            }
        ],
        "checks": [
            {
                "id": "bounded",
                "type": "range",
                "required": True,
                "status": "PASS",
                "summary": "all values are within range",
                "criterion": {
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "inclusive_minimum": True,
                    "inclusive_maximum": True,
                },
                "observed": {
                    "shape": [1],
                    "minimum": 1.25,
                    "maximum": 1.25,
                    "nonfinite_count": 0,
                    "failing_count": 0,
                    "failing_indices": [],
                },
            }
        ],
        "summary": {
            "status": "PASS",
            "provenance_status": "COMPLETE",
            "provenance_issues": [],
        },
        "environment": {"platform": {"system": "irrelevant"}},
    }


def test_finding_projection_v1_golden_vector() -> None:
    record = _projection_record()

    projection = finding_projection(record)

    assert projection == {
        "projection": "pvs-finding-projection/1",
        "subject": {
            "name": "solver",
            "version": "4.0",
            "revision": "abc123",
            "source_repository": None,
        },
        "case": {
            "id": "finding-golden",
            "version": "1.0",
            "classifications": ["validation", "verification"],
        },
        "artifacts": [
            {
                "id": "input",
                "role": "reference",
                "format": "json",
                "required": True,
                "unit": None,
                "validation_identity": {
                    "exists": True,
                    "sha256": "6" * 64,
                    "size_bytes": 20,
                },
            },
            {
                "id": "result",
                "role": "output",
                "format": "json",
                "required": True,
                "unit": None,
                "validation_identity": None,
            },
        ],
        "references": [
            {
                "id": "published-value",
                "type": "published",
                "citation": "A. Author, Exact source",
                "locator": "Table 2, row 1",
                "artifact": "input",
                "source_location": None,
                "accessed_utc": "2026-01-01",
                "derivation": "Free-form derivation prose",
                "notes": "Free-form notes",
            }
        ],
        "checks": [
            {
                "declaration": {
                    "id": "bounded",
                    "type": "range",
                    "required": True,
                    "unit": None,
                        "source": {
                            "artifact": "result",
                            "pointer": "/value",
                            "column": None,
                            "variable": None,
                            "component": None,
                            "reduce": None,
                            "netcdf_decoding": None,
                            "unit": None,
                        },
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "inclusive_minimum": True,
                    "inclusive_maximum": True,
                },
                "status": "PASS",
                "criterion": {
                    "minimum": 0.0,
                    "maximum": 2.0,
                    "inclusive_minimum": True,
                    "inclusive_maximum": True,
                },
                "observed": {
                    "shape": [1],
                    "minimum": 1.25,
                    "maximum": 1.25,
                    "nonfinite_count": 0,
                    "failing_count": 0,
                    "failing_indices": [],
                },
                "reference_id": None,
            }
        ],
    }
    golden_digest = "0d29eb9e8aefacc08bed9cbd455a3a611440245b1a8d9ca4fa228d36e33d9597"
    assert canonical_sha256(projection) == golden_digest
    metadata = create_finding_metadata(record)
    assert metadata["finding_id"] == "pvs-finding:v1:sha256:" + golden_digest


def test_repeated_transactions_share_finding_but_not_evidence(package_factory) -> None:
    first, _ = package_factory(html=False, pdf=False, embed=False)
    second, _ = package_factory(html=False, pdf=False, embed=False)

    assert first.finding_id is not None
    assert first.finding_id == second.finding_id
    assert first.evidence_id != second.evidence_id
    assert first.run_id != second.run_id


def test_default_equivalent_case_declarations_share_finding(case_factory, tmp_path: Path) -> None:
    from conftest import base_case

    implicit = base_case()
    implicit["checks"] = [
        {
            "id": "bounded",
            "type": "range",
            "source": {"artifact": "result", "pointer": "/value", "unit": "1"},
            "unit": "1",
            "minimum": 0.0,
            "maximum": 2.0,
        }
    ]
    explicit = copy.deepcopy(implicit)
    explicit["checks"][0].update(
        {"required": True, "inclusive_minimum": True, "inclusive_maximum": True}
    )
    first_case, _ = case_factory(implicit, name="implicit")
    second_case, _ = case_factory(explicit, name="explicit")

    first = validate_case(first_case, output_dir=tmp_path / "implicit-evidence")
    second = validate_case(second_case, output_dir=tmp_path / "explicit-evidence")

    assert first.finding_id == second.finding_id
    assert first.evidence_id != second.evidence_id


def test_unchecked_output_bytes_do_not_change_finding(case_factory, tmp_path: Path) -> None:
    first_case, _ = case_factory(result_text='{"value":1,"unchecked":"left"}\n', name="left")
    second_case, _ = case_factory(
        result_text='{"value":999,"unchecked":"entirely different"}\n', name="right"
    )

    first = validate_case(first_case, output_dir=tmp_path / "left-evidence")
    second = validate_case(second_case, output_dir=tmp_path / "right-evidence")

    # The declared check is existence only. PVS makes no scientific claim
    # about either value; whole-output byte identities remain Evidence-ID bound.
    assert first.finding_id == second.finding_id
    assert first.evidence_id != second.evidence_id


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["run"].update(
            {
                "run_id": "urn:uuid:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "started_at": "2030-01-01T00:00:00Z",
                "duration_seconds": 99.0,
            }
        ),
        lambda value: value["execution"].update(
            {
                "mode": "validate",
                "command": None,
                "resolved_executable": None,
                "duration_seconds": 0.0,
            }
        ),
        lambda value: value["environment"].update({"platform": {"system": "elsewhere"}}),
        lambda value: value["pvs"].update({"version": "99.0", "implementation": "other"}),
        lambda value: value["case"].update(
            {"title": "Changed prose", "filename": "renamed.yaml", "raw_sha256": "9" * 64}
        ),
        lambda value: value["case"]["resolved_definition"].update(
            {"title": "Changed prose", "description": "Changed prose"}
        ),
        lambda value: value["subject"].update({"description": "Changed prose"}),
        lambda value: value["checks"][0].update({"summary": "Changed prose"}),
        lambda value: value["artifacts"][0].update(
            {"package_path": "elsewhere/result.json"}
        ),
    ],
)
def test_transaction_and_prose_fields_are_excluded(mutation) -> None:
    original = _projection_record()
    changed = copy.deepcopy(original)
    mutation(changed)

    assert create_finding_metadata(changed) == create_finding_metadata(original)


def test_non_output_input_identity_is_finding_semantics() -> None:
    original = _projection_record()
    changed = copy.deepcopy(original)
    changed["artifacts"][1]["validation_input"]["sha256"] = "7" * 64

    assert create_finding_metadata(changed) != create_finding_metadata(original)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unit", "m s^-1"),
        ("component", 0),
        ("netcdf_decoding", "pvs-netcdf/1"),
    ],
)
def test_selector_unit_component_and_decoding_are_finding_semantics(
    field: str, value: Any
) -> None:
    original = _projection_record()
    changed = copy.deepcopy(original)
    changed["case"]["resolved_definition"]["checks"][0]["source"][field] = value

    assert create_finding_metadata(changed) != create_finding_metadata(original)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("citation", "Different source"),
        ("locator", "Table 9"),
        ("accessed_utc", "2030-01-01"),
        ("derivation", "Different derivation"),
        ("notes", "Different source anomaly"),
    ],
)
def test_complete_reference_declaration_is_finding_semantics(field: str, value: str) -> None:
    original = _projection_record()
    changed = copy.deepcopy(original)
    changed["references"][0][field] = value

    assert create_finding_metadata(changed) != create_finding_metadata(original)


@pytest.mark.parametrize(
    "field,value",
    [("criterion", {"minimum": -1.0}), ("observed", {"minimum": 1.5}), ("status", "FAIL")],
)
def test_exact_criterion_observation_and_status_are_finding_semantics(field, value) -> None:
    original = _projection_record()
    changed = copy.deepcopy(original)
    changed["checks"][0][field] = value
    if field == "status":
        changed["summary"]["status"] = value

    assert create_finding_metadata(changed) != create_finding_metadata(original)


@pytest.mark.parametrize("status", ["FAIL", "WARN"])
def test_fail_and_warn_results_are_eligible(status: str) -> None:
    record = _projection_record()
    record["checks"][0]["status"] = status
    record["summary"]["status"] = status

    metadata = create_finding_metadata(record)

    assert metadata["status"] == "ISSUED"
    assert metadata["finding_id"].startswith("pvs-finding:v1:sha256:")


def test_error_and_incomplete_provenance_are_not_eligible() -> None:
    error = _projection_record()
    error["checks"][0]["status"] = "ERROR"
    error["summary"]["status"] = "ERROR"
    incomplete = _projection_record()
    incomplete["summary"]["provenance_status"] = "INCOMPLETE"
    incomplete["summary"]["provenance_issues"] = ["identity changed"]

    assert create_finding_metadata(error) == {
        "projection": "pvs-finding-projection/1",
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "status": "NOT_ISSUED",
        "reason": "ERROR_PRESENT",
    }
    assert create_finding_metadata(incomplete)["reason"] == "INCOMPLETE_PROVENANCE"


def test_error_outcome_exposes_no_finding_id(case_factory, tmp_path: Path) -> None:
    from conftest import base_case

    data = base_case()
    data["checks"] = [
        {
            "id": "bad-selector",
            "type": "finite",
            "source": {"artifact": "result", "pointer": "/missing", "unit": "1"},
            "unit": "1",
        }
    ]
    case_path, _ = case_factory(data)

    outcome = validate_case(case_path, output_dir=tmp_path / "error-evidence")
    envelope = load_strict(outcome.evidence_path)

    assert outcome.finding_id is None
    assert envelope["integrity"]["finding"]["status"] == "NOT_ISSUED"
    assert envelope["integrity"]["finding"]["reason"] == "ERROR_PRESENT"
    assert verify_target(outcome.output_directory).valid is True


def test_verifier_recomputes_finding_after_rehashed_observation_tamper(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["record"]["checks"][0]["observed"]["exists"] = False
    _rehash(envelope)
    _write(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert next(
        item for item in result.checks if item["name"] == "Scientific Finding digest"
    )["status"] == "FAIL"


def test_finding_metadata_is_closed_and_recomputed(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["integrity"]["finding"]["undeclared"] = True
    _write(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert next(item for item in result.checks if item["name"] == "evidence schema")[
        "status"
    ] == "FAIL"


def test_expected_finding_id_has_distinct_pinned_trust(package_factory) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    assert outcome.finding_id is not None

    accepted = verify_target(package, expect_finding_id=outcome.finding_id)
    rejected = verify_target(
        package,
        expect_finding_id="pvs-finding:v1:sha256:" + "0" * 64,
    )

    assert accepted.valid is True
    assert accepted.finding_id == outcome.finding_id
    assert accepted.finding_identity_pinned is True
    assert accepted.evidence_identity_pinned is False
    assert accepted.trust == "finding-pinned"
    assert rejected.valid is False
    assert next(
        item for item in rejected.checks if item["name"] == "expected Scientific Finding ID"
    )["status"] == "FAIL"


def test_cli_accepts_and_reports_finding_pin(package_factory, capsys) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    assert outcome.finding_id is not None

    code = main(
        [
            "verify",
            str(package),
            "--expect-finding-id",
            outcome.finding_id,
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out.startswith("PVS SCIENTIFIC FINDING PINNED\n")
    assert f"Finding ID:  {outcome.finding_id}\n" in captured.out
    assert "Trust:       finding-pinned\n" in captured.out


def test_pvs_evidence_v1_standalone_record_remains_verifiable(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    _downgrade_envelope_to_v1(envelope)
    legacy = tmp_path / "pvs-evidence-v1.json"
    _write(legacy, envelope)

    result = verify_target(legacy)

    assert result.valid is True
    assert result.finding_id is None
    assert result.trust == "unpinned"


def test_pvs_evidence_v1_package_remains_verifiable(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    _downgrade_envelope_to_v1(envelope)

    retained_case_path = package / "case" / "pvs.yaml"
    retained_case = envelope["record"]["case"]["resolved_definition"]
    retained_case_path.write_text(
        yaml.safe_dump(retained_case, sort_keys=False), encoding="utf-8", newline="\n"
    )
    retained_case_identity = file_identity(retained_case_path)
    envelope["record"]["case"]["raw_sha256"] = retained_case_identity["sha256"]
    envelope["record"]["case"]["raw_size_bytes"] = retained_case_identity["size_bytes"]
    _rehash(envelope)
    _write(evidence_path, envelope)

    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["schema"] = "pvs-manifest/1"
    manifest["package"].pop("policy")
    manifest["package"].pop("report_profiles")
    manifest["package"]["evidence_id"] = envelope["integrity"]["evidence_id"]
    evidence_entry = next(
        item for item in manifest["package"]["files"] if item["path"] == "evidence.json"
    )
    evidence_entry.update(file_identity(evidence_path))
    case_entry = next(
        item for item in manifest["package"]["files"] if item["path"] == "case/pvs.yaml"
    )
    case_entry.update(retained_case_identity)
    manifest_digest = canonical_sha256(manifest["package"])
    manifest["integrity"]["digest"] = manifest_digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{manifest_digest}"
    _write(manifest_path, manifest)

    result = verify_target(package)

    assert result.valid is True
    assert result.level == "package"
    assert result.finding_id is None
