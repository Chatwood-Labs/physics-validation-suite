from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case, dump_case
from jsonschema import Draft202012Validator

import pvs.formats as formats_module
from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.case import _validate_case_filename, load_case, validate_case_semantics
from pvs.errors import CaseError, IntegrityError
from pvs.finding import create_finding_metadata
from pvs.formats import FormatSupportError, required_format_checker
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.package import create_manifest
from pvs.schemas import load_schema
from pvs.verify import VerificationResult, verify_target


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _check(result: VerificationResult, name: str) -> dict[str, Any]:
    return next(item for item in result.checks if item["name"] == name)


def _rehash(envelope: dict[str, Any], *, case_changed: bool = False) -> None:
    record = envelope["record"]
    if case_changed:
        record["case"]["semantic_sha256"] = canonical_sha256(
            record["case"]["resolved_definition"]
        )
    envelope["integrity"]["finding"] = create_finding_metadata(record)
    digest = canonical_sha256(record)
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = f"pvs:sha256:{digest}"


def _rehash_without_finding(envelope: dict[str, Any]) -> None:
    digest = canonical_sha256(envelope["record"])
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = f"pvs:sha256:{digest}"


def _write_standalone(package: Path, tmp_path: Path, envelope: dict[str, Any], name: str) -> Path:
    target = tmp_path / name
    _write_json(target, envelope)
    return target


def _successful_run_envelope(package: Path) -> dict[str, Any]:
    envelope = load_strict(package / "evidence.json")
    record = envelope["record"]
    definition = record["case"]["resolved_definition"]
    definition["execution"] = {"command": ["solver"]}
    record["run"]["mode"] = "run"
    execution = record["execution"]
    execution.update(
        {
            "mode": "run",
            "command": ["solver"],
            "working_directory": ".",
            "return_code": 0,
            "expected_exit_codes": [0],
            "succeeded": True,
            "error": None,
            "declared_environment": {},
            "resolved_executable": {
                "requested": "solver",
                "resolved": True,
                "filename": "solver",
                "launch_profile": "pvs-private-executable-snapshot/1",
                "sha256": "1" * 64,
                "size_bytes": 7,
                "post_execution_sha256": "1" * 64,
                "post_execution_size_bytes": 7,
                "unchanged_during_execution": True,
            },
            "stdout": {
                "package_path": "logs/stdout.txt",
                "sha256": "2" * 64,
                "size_bytes": 0,
            },
            "stderr": {
                "package_path": "logs/stderr.txt",
                "sha256": "3" * 64,
                "size_bytes": 0,
            },
        }
    )
    for artifact in record["artifacts"]:
        if artifact["role"] == "output":
            artifact["pre_execution"] = {"exists": False}
    _rehash(envelope, case_changed=True)
    return envelope


def _set_summary_status(
    record: dict[str, Any], status: str, *, check_status_changed: bool = True
) -> None:
    if check_status_changed:
        for name in ("PASS", "WARN", "FAIL", "ERROR", "SKIP"):
            record["summary"]["counts"][name] = int(name == status)
    record["summary"]["status"] = status


def test_schema_valid_malformed_resolved_case_fails_without_crashing(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    envelope["record"]["case"]["resolved_definition"] = {
        "schema": "pvs-case/2",
        "checks": [1],
    }
    _rehash_without_finding(envelope)
    path = _write_standalone(package, tmp_path, envelope, "malformed-definition.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "PASS"
    assert _check(result, "resolved case schema")["status"] == "FAIL"
    assert _check(result, "Scientific Finding recomputation")["status"] == "FAIL"


def test_run_mode_without_execution_declaration_fails_without_none_dereference(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    record = envelope["record"]
    record["run"]["mode"] = "run"
    record["execution"].update(
        {
            "mode": "run",
            "command": ["missing"],
            "return_code": None,
            "expected_exit_codes": [0],
            "succeeded": False,
            "error": "executable not found: missing",
            "resolved_executable": {"requested": "missing", "resolved": False},
            "stdout": {
                "package_path": "logs/stdout.txt",
                "sha256": "1" * 64,
                "size_bytes": 0,
            },
            "stderr": {
                "package_path": "logs/stderr.txt",
                "sha256": "2" * 64,
                "size_bytes": 1,
            },
        }
    )
    _set_summary_status(record, "ERROR", check_status_changed=False)
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, "run-without-declaration.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "PASS"
    assert _check(result, "execution declaration")["status"] == "FAIL"


def test_validate_lifecycle_detects_pre_to_validation_identity_change(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    artifact = envelope["record"]["artifacts"][0]
    artifact["pre_execution"]["sha256"] = "f" * 64
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, "validate-lifecycle.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "artifact lifecycle")["status"] == "FAIL"


def test_run_lifecycle_detects_auxiliary_identity_change(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = _successful_run_envelope(package)
    record = envelope["record"]
    definition = record["case"]["resolved_definition"]
    definition["artifacts"]["auxiliary"] = {
        "path": "auxiliary.txt",
        "role": "auxiliary",
        "format": "text",
    }
    record["artifacts"].append(
        {
            "id": "auxiliary",
            "role": "auxiliary",
            "format": "text",
            "required": True,
            "path": "auxiliary.txt",
            "media_type": None,
            "description": None,
            "expected_sha256": None,
            "expected_sha256_matches": None,
            "pre_execution": {"exists": True, "sha256": "4" * 64, "size_bytes": 1},
            "validation_input": {
                "exists": True,
                "sha256": "5" * 64,
                "size_bytes": 1,
            },
            "post_validation": {"exists": True, "sha256": "5" * 64, "size_bytes": 1},
            "package_path": None,
        }
    )
    _rehash(envelope, case_changed=True)
    path = _write_standalone(package, tmp_path, envelope, "run-auxiliary-lifecycle.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "artifact lifecycle")["status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["missing_stdout", "wrong_stdout", "validate_log"])
def test_v2_execution_log_contract_is_closed(
    package_factory, tmp_path: Path, mutation: str
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    if mutation == "validate_log":
        envelope = load_strict(package / "evidence.json")
        envelope["record"]["execution"]["stdout"] = {
            "package_path": "logs/stdout.txt",
            "sha256": "1" * 64,
            "size_bytes": 0,
        }
    else:
        envelope = _successful_run_envelope(package)
        if mutation == "missing_stdout":
            del envelope["record"]["execution"]["stdout"]
        else:
            envelope["record"]["execution"]["stdout"]["package_path"] = "logs/other.txt"
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, f"{mutation}.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "FAIL"


def test_v2_envelope_requires_v2_resolved_case(package_factory, tmp_path: Path) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    envelope["record"]["case"]["resolved_definition"]["schema"] = "pvs-case/1"
    _rehash_without_finding(envelope)
    path = _write_standalone(package, tmp_path, envelope, "cross-generation.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "FAIL"


@pytest.mark.parametrize("embed", [False, True])
def test_standalone_record_enforces_embedding_path_derivation(
    package_factory, tmp_path: Path, embed: bool
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=embed)
    envelope = load_strict(package / "evidence.json")
    artifact = envelope["record"]["artifacts"][0]
    artifact["package_path"] = (
        "artifacts/output/result/wrong.json" if embed else "artifacts/output/result/result.json"
    )
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, f"embedding-{embed}.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "artifact embedding record policy")["status"] == "FAIL"


def test_standalone_record_enforces_package_policy_source_attribution(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    envelope["record"]["package"]["policy"]["pdf"]["source"] = "schema_default"
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, "policy-source.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "package policy source pdf")["status"] == "FAIL"


@pytest.mark.parametrize(
    "missing",
    [
        "filename",
        "launch_profile",
        "sha256",
        "size_bytes",
        "post_execution_sha256",
        "post_execution_size_bytes",
        "unchanged_during_execution",
    ],
)
def test_successful_resolved_executable_requires_complete_identity(
    package_factory, tmp_path: Path, missing: str
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = _successful_run_envelope(package)
    del envelope["record"]["execution"]["resolved_executable"][missing]
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, f"missing-{missing}.json")

    result = verify_target(path)

    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "FAIL"


def test_failed_executable_acquisition_shape_is_valid_but_orphan_identity_is_not(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = _successful_run_envelope(package)
    record = envelope["record"]
    execution = record["execution"]
    execution.update(
        {
            "return_code": None,
            "succeeded": False,
            "error": "could not acquire or execute command: denied",
        }
    )
    resolved = execution["resolved_executable"]
    for name in (
        "sha256",
        "size_bytes",
        "post_execution_sha256",
        "post_execution_size_bytes",
    ):
        resolved.pop(name)
    resolved["unchanged_during_execution"] = False
    _set_summary_status(record, "ERROR", check_status_changed=False)
    _rehash(envelope)
    valid_path = _write_standalone(package, tmp_path, envelope, "failed-acquisition.json")

    assert verify_target(valid_path).valid is True

    hostile = copy.deepcopy(envelope)
    hostile["record"]["execution"]["resolved_executable"]["sha256"] = "1" * 64
    _rehash(hostile)
    hostile_path = _write_standalone(package, tmp_path, hostile, "orphan-identity.json")
    result = verify_target(hostile_path)
    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "FAIL"


def test_unresolved_executable_forbids_identity_extras_but_allows_location_hints(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = _successful_run_envelope(package)
    record = envelope["record"]
    record["execution"].update(
        {
            "return_code": None,
            "succeeded": False,
            "error": "executable not found: solver",
            "resolved_executable": {
                "requested": "solver",
                "resolved": False,
                "filename": "solver",
                "case_relative_path": "solver",
            },
        }
    )
    _set_summary_status(record, "ERROR", check_status_changed=False)
    _rehash(envelope)
    valid_path = _write_standalone(package, tmp_path, envelope, "unresolved.json")
    assert verify_target(valid_path).valid is True

    hostile = copy.deepcopy(envelope)
    hostile["record"]["execution"]["resolved_executable"]["size_bytes"] = 1
    _rehash(hostile)
    hostile_path = _write_standalone(package, tmp_path, hostile, "unresolved-extra.json")
    result = verify_target(hostile_path)
    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "FAIL"


@pytest.mark.parametrize(
    ("status", "required"),
    [("WARN", True), ("SKIP", True), ("FAIL", False)],
)
def test_v2_check_status_required_semantics_are_enforced(
    package_factory, tmp_path: Path, status: str, required: bool
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    record = envelope["record"]
    declared = record["case"]["resolved_definition"]["checks"][0]
    result_record = record["checks"][0]
    declared["required"] = required
    result_record["required"] = required
    result_record["status"] = status
    if status == "SKIP":
        result_record["criterion"] = {}
        result_record["observed"] = {}
    elif status != "PASS":
        result_record["observed"] = {"exists": False}
    _set_summary_status(record, status)
    _rehash(envelope, case_changed=True)
    path = _write_standalone(package, tmp_path, envelope, f"status-{status}.json")

    verification = verify_target(path)

    assert verification.valid is False
    assert _check(verification, "evidence schema")["status"] == "FAIL"


@pytest.mark.parametrize("status", ["PASS", "ERROR", "SKIP"])
def test_v2_criterion_is_exact_and_empty_for_error_or_skip(
    package_factory, tmp_path: Path, status: str
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    record = envelope["record"]
    result_record = record["checks"][0]
    if status == "PASS":
        result_record["criterion"]["invented"] = True
    else:
        result_record["status"] = status
        result_record["criterion"] = {"invented": True}
        result_record["observed"] = {}
        if status == "SKIP":
            result_record["required"] = False
            record["case"]["resolved_definition"]["checks"][0]["required"] = False
        _set_summary_status(record, status)
    _rehash(envelope, case_changed=status == "SKIP")
    path = _write_standalone(package, tmp_path, envelope, f"criterion-{status}.json")

    verification = verify_target(path)

    assert verification.valid is False
    name = "check criterion contract" if status == "PASS" else "evidence schema"
    assert _check(verification, name)["status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["contradiction", "extra_field"])
def test_v2_observation_cannot_contradict_status_or_evaluator_shape(
    package_factory, tmp_path: Path, mutation: str
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    observed = envelope["record"]["checks"][0]["observed"]
    if mutation == "contradiction":
        observed["exists"] = False
    else:
        observed["invented"] = True
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, f"observed-{mutation}.json")

    verification = verify_target(path)

    assert verification.valid is False
    failed = (
        "check observed/status contract"
        if mutation == "contradiction"
        else "check observation contract"
    )
    assert _check(verification, failed)["status"] == "FAIL"


def _assert_rehashed_scientific_forgery_is_rejected(
    envelope: dict[str, Any], tmp_path: Path, name: str
) -> VerificationResult:
    _rehash(envelope)
    path = _write_standalone(tmp_path, tmp_path, envelope, name)
    verification = verify_target(path)

    assert verification.valid is False
    assert _check(verification, "evidence schema")["status"] == "PASS"
    assert _check(verification, "canonical record digest")["status"] == "PASS"
    assert _check(verification, "evidence ID")["status"] == "PASS"
    assert _check(verification, "Scientific Finding digest")["status"] == "PASS"
    assert _check(verification, "Scientific Finding ID")["status"] == "PASS"
    assert _check(verification, "check observation contract")["status"] == "FAIL"
    return verification


def test_v2_exists_observation_must_match_artifact_validation_snapshot(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    record = envelope["record"]
    assert record["artifacts"][0]["validation_input"]["exists"] is True
    record["checks"][0]["observed"]["exists"] = False
    record["checks"][0]["status"] = "FAIL"
    _set_summary_status(record, "FAIL")

    _assert_rehashed_scientific_forgery_is_rejected(
        envelope, tmp_path, "forged-exists.json"
    )


def test_v2_range_pass_cannot_claim_out_of_bound_minimum(
    case_factory, tmp_path: Path
) -> None:
    case = base_case()
    case["checks"] = [
        {
            "id": "bounded",
            "type": "range",
            "source": {"artifact": "result", "pointer": "/values", "unit": "1"},
            "unit": "1",
            "minimum": 0.0,
            "maximum": 2.0,
        }
    ]
    case_path, _ = case_factory(case, result_text='{"values":[0.0,1.0,2.0]}\n')
    outcome = validate_case(case_path, output_dir=tmp_path / "range-evidence")
    envelope = load_strict(outcome.evidence_path)
    result = envelope["record"]["checks"][0]
    assert result["status"] == "PASS"
    assert result["observed"]["failing_count"] == 0
    result["observed"]["minimum"] = -100.0

    _assert_rehashed_scientific_forgery_is_rejected(
        envelope, tmp_path, "forged-range.json"
    )


def test_v2_decreasing_monotonic_pass_cannot_claim_positive_steps(
    case_factory, tmp_path: Path
) -> None:
    case = base_case()
    case["checks"] = [
        {
            "id": "decreasing",
            "type": "monotonic",
            "source": {"artifact": "result", "pointer": "/values", "unit": "1"},
            "unit": "1",
            "direction": "decreasing",
            "absolute_tolerance": 0.0,
        }
    ]
    case_path, _ = case_factory(case, result_text='{"values":[3.0,2.0,1.0]}\n')
    outcome = validate_case(case_path, output_dir=tmp_path / "monotonic-evidence")
    envelope = load_strict(outcome.evidence_path)
    result = envelope["record"]["checks"][0]
    assert result["status"] == "PASS"
    result["observed"]["minimum_step"] = 1.0
    result["observed"]["maximum_step"] = 1.0

    _assert_rehashed_scientific_forgery_is_rejected(
        envelope, tmp_path, "forged-monotonic.json"
    )


def test_v2_monotonic_failing_index_must_identify_a_real_step(
    case_factory, tmp_path: Path
) -> None:
    case = base_case()
    case["checks"] = [
        {
            "id": "decreasing",
            "type": "monotonic",
            "source": {"artifact": "result", "pointer": "/values", "unit": "1"},
            "unit": "1",
            "direction": "decreasing",
            "absolute_tolerance": 0.0,
        }
    ]
    case_path, _ = case_factory(case, result_text='{"values":[3.0,2.0,3.0]}\n')
    outcome = validate_case(case_path, output_dir=tmp_path / "failed-monotonic-evidence")
    envelope = load_strict(outcome.evidence_path)
    result = envelope["record"]["checks"][0]
    assert result["status"] == "FAIL"
    assert result["observed"]["failing_indices"] == [1]

    # A length-three series has only step indexes 0 and 1. Index 2 refers to
    # an element, not an adjacent-difference observation.
    result["observed"]["failing_indices"] = [2]

    _assert_rehashed_scientific_forgery_is_rejected(
        envelope, tmp_path, "forged-monotonic-index.json"
    )


def test_v2_comparison_observation_is_bound_to_literal_operands_and_tolerance(
    case_factory, tmp_path: Path
) -> None:
    case = base_case()
    case["checks"] = [
        {
            "id": "literal-comparison",
            "type": "compare",
            "actual": {"value": 1.0, "unit": "1"},
            "expected": {"value": 1.0, "unit": "1"},
            "unit": "1",
            "metric": "absolute",
            "tolerance": 0.0,
        }
    ]
    case_path, _ = case_factory(case)
    outcome = validate_case(case_path, output_dir=tmp_path / "comparison-evidence")
    envelope = load_strict(outcome.evidence_path)
    result = envelope["record"]["checks"][0]
    assert result["status"] == "PASS"
    result["observed"]["actual"] = 2.0
    result["observed"]["expected"] = 2.0
    result["observed"]["permitted_error"] = 1e99

    _assert_rehashed_scientific_forgery_is_rejected(
        envelope, tmp_path, "forged-comparison.json"
    )


def test_v2_conservation_pass_cannot_forge_terms_or_permitted_error(
    case_factory, tmp_path: Path
) -> None:
    case = base_case()
    case["checks"] = [
        {
            "id": "balance",
            "type": "conservation",
            "terms": [
                {
                    "label": "source",
                    "value": {"value": 2.0, "unit": "1"},
                    "coefficient": 3.0,
                },
                {
                    "label": "sink",
                    "value": {"value": 0.0, "unit": "1"},
                    "coefficient": -1.0,
                },
            ],
            "expected": {"value": 6.0, "unit": "1"},
            "unit": "1",
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
        }
    ]
    case_path, _ = case_factory(case)
    outcome = validate_case(case_path, output_dir=tmp_path / "conservation-evidence")
    envelope = load_strict(outcome.evidence_path)
    result = envelope["record"]["checks"][0]
    assert result["status"] == "PASS"
    observed = result["observed"]
    observed["terms"][0]["coefficient"] = 4.0
    observed["terms"][0]["contribution"] = 8.0
    observed["permitted_error"] = 1e99

    _assert_rehashed_scientific_forgery_is_rejected(
        envelope, tmp_path, "forged-conservation.json"
    )


def test_v2_chronology_rejects_finished_before_started(package_factory, tmp_path: Path) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    for owner in (envelope["record"]["run"], envelope["record"]["execution"]):
        owner["started_at"] = "2026-01-02T00:00:00Z"
        owner["finished_at"] = "2026-01-01T00:00:00Z"
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, "reversed-time.json")

    verification = verify_target(path)

    assert verification.valid is False
    assert _check(verification, "execution chronology")["status"] == "FAIL"


def test_validate_package_forbids_any_manifested_log(
    package_factory,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    extra = package / "logs" / "extra.txt"
    extra.parent.mkdir()
    extra.write_text("extra\n", encoding="utf-8")
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["package"]["files"].append(
        {"path": "logs/extra.txt", "role": "log", **file_identity(extra)}
    )
    manifest["package"]["files"].sort(key=lambda item: item["path"])
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{digest}"
    _write_json(manifest_path, manifest)

    verification = verify_target(package)

    assert verification.valid is False
    assert _check(verification, "exact package coverage")["status"] == "PASS"
    assert _check(verification, "package execution log coverage")["status"] == "FAIL"


@pytest.mark.parametrize("terminator", ["\r", "\n", "\u2028", "\u2029"])
@pytest.mark.parametrize("kind", ["case-v2", "evidence-v2", "manifest-v2"])
def test_v2_pattern_contracts_reject_terminal_line_separators(
    package_factory, terminator: str, kind: str
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    if kind == "case-v2":
        value = base_case()
        value["id"] += terminator
    elif kind == "evidence-v2":
        value = load_strict(package / "evidence.json")
        value["integrity"]["evidence_id"] += terminator
    else:
        value = load_strict(package / "manifest.json")
        value["integrity"]["package_id"] += terminator
    validator = Draft202012Validator(
        load_schema(kind), format_checker=required_format_checker()
    )

    assert list(validator.iter_errors(value))


@pytest.mark.parametrize("kind", ["case-v2", "evidence-v2", "manifest-v2"])
def test_every_v2_pattern_explicitly_rejects_all_json_line_separators(kind: str) -> None:
    def patterns(value: Any) -> list[str]:
        if isinstance(value, dict):
            current = [value["pattern"]] if "pattern" in value else []
            return current + [item for child in value.values() for item in patterns(child)]
        if isinstance(value, list):
            return [item for child in value for item in patterns(child)]
        return []

    for pattern in patterns(load_schema(kind)):
        assert all(token in pattern for token in (r"\r", r"\n", r"\u2028", r"\u2029"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_repository", "not a uri"),
        ("source_repository", "relative/path"),
    ],
)
def test_case_format_validation_rejects_malformed_uri(
    tmp_path: Path, field: str, value: str
) -> None:
    case = base_case()
    case["subject"][field] = value
    path = dump_case(tmp_path / "pvs.yaml", case)

    with pytest.raises(CaseError, match="uri"):
        load_case(path)


@pytest.mark.parametrize(
    ("location", "value"),
    [("date-time", "2026-02-31T00:00:00Z"), ("uri", "not a uri")],
)
def test_evidence_format_validation_rejects_malformed_values(
    package_factory, tmp_path: Path, location: str, value: str
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    if location == "date-time":
        envelope["record"]["run"]["started_at"] = value
        envelope["record"]["execution"]["started_at"] = value
    else:
        envelope["record"]["subject"]["source_repository"] = value
    _rehash_without_finding(envelope)
    path = _write_standalone(package, tmp_path, envelope, f"bad-{location}.json")

    verification = verify_target(path)

    assert verification.valid is False
    assert _check(verification, "evidence schema")["status"] == "FAIL"


def test_manifest_format_validation_rejects_impossible_completion_date(
    package_factory,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["package"]["created_at"] = "2026-02-31T00:00:00Z"
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{digest}"
    _write_json(manifest_path, manifest)

    verification = verify_target(package)

    assert verification.valid is False
    assert _check(verification, "manifest schema")["status"] == "FAIL"


def test_scientific_json_schema_check_enforces_declared_formats(
    case_factory, tmp_path: Path
) -> None:
    case = base_case()
    case["artifacts"]["result-schema"] = {
        "path": "result.schema.json",
        "role": "input",
        "format": "json",
    }
    case["checks"] = [
        {
            "id": "result-schema",
            "type": "schema",
            "artifact": "result",
            "schema_artifact": "result-schema",
        }
    ]
    case_path, _ = case_factory(
        case,
        result_text='{"observed_at":"2026-02-31T00:00:00Z"}\n',
    )
    (case_path.parent / "result.schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["observed_at"],
                "properties": {"observed_at": {"type": "string", "format": "date-time"}},
            }
        ),
        encoding="utf-8",
    )

    outcome = validate_case(case_path, output_dir=tmp_path / "schema-format-evidence")
    envelope = load_strict(outcome.evidence_path)

    assert envelope["record"]["checks"][0]["status"] == "FAIL"
    assert envelope["record"]["checks"][0]["observed"]["error_count"] == 1
    assert verify_target(outcome.output_directory).valid is True


def test_missing_required_format_plugin_fails_closed_with_actionable_error(
    package_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    monkeypatch.delitem(formats_module.FormatChecker.checkers, "uri")

    with pytest.raises(FormatSupportError, match=r"jsonschema\[format-nongpl\]"):
        required_format_checker()
    verification = verify_target(package / "evidence.json")
    assert verification.valid is False
    assert "jsonschema[format-nongpl]" in _check(verification, "evidence schema")["detail"]


def test_v2_execution_command_requires_nonempty_program_but_allows_empty_argument(
    tmp_path: Path,
) -> None:
    invalid = base_case()
    invalid["execution"] = {"command": [""]}
    invalid_path = dump_case(tmp_path / "invalid" / "pvs.yaml", invalid)
    with pytest.raises(CaseError, match="non-empty"):
        load_case(invalid_path)

    valid = base_case()
    valid["execution"] = {"command": ["true", ""]}
    valid_path = dump_case(tmp_path / "valid" / "pvs.yaml", valid)
    assert load_case(valid_path).data["execution"]["command"] == ["true", ""]


@pytest.mark.parametrize("filename", ["bad\\name.yaml", "CON.yaml", "trailing."])
def test_case_filename_must_be_one_portable_component(filename: str) -> None:
    with pytest.raises(CaseError, match="case filename is not portable"):
        _validate_case_filename(filename)


@pytest.mark.parametrize("artifact_id", ["CON", "a."])
def test_artifact_id_must_be_one_portable_component(
    tmp_path: Path, artifact_id: str
) -> None:
    case = base_case()
    case["artifacts"][artifact_id] = case["artifacts"].pop("result")
    case["checks"][0]["artifact"] = artifact_id
    path = dump_case(tmp_path / artifact_id / "pvs.yaml", case)

    with pytest.raises(CaseError, match="artifact ID"):
        load_case(path)


def test_artifact_ids_must_be_casefold_unique(tmp_path: Path) -> None:
    case = base_case()
    case["artifacts"]["foo"] = {
        "path": "foo.json",
        "role": "input",
        "format": "json",
    }
    case["artifacts"]["FOO"] = {
        "path": "FOO.json",
        "role": "input",
        "format": "json",
    }
    path = dump_case(tmp_path / "pvs.yaml", case)

    with pytest.raises(CaseError, match="case-insensitive"):
        load_case(path)


def test_legacy_case_semantics_preserve_nonportable_artifact_ids() -> None:
    legacy = base_case()
    legacy["schema"] = "pvs-case/1"
    declaration = legacy["artifacts"].pop("result")
    legacy["artifacts"]["CON"] = declaration
    legacy["artifacts"]["con"] = {
        "path": "second.json",
        "role": "input",
        "format": "json",
    }
    legacy["checks"][0]["artifact"] = "CON"

    validate_case_semantics(legacy, None)


def test_duplicate_dependency_names_are_rejected(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    dependency = copy.deepcopy(envelope["record"]["environment"]["dependencies"][0])
    dependency["name"] = dependency["name"].swapcase()
    envelope["record"]["environment"]["dependencies"].append(dependency)
    _rehash(envelope)
    path = _write_standalone(package, tmp_path, envelope, "duplicate-dependency.json")

    verification = verify_target(path)

    assert verification.valid is False
    assert _check(verification, "dependency name uniqueness")["status"] == "FAIL"


def test_empty_directory_cannot_become_a_manifest(tmp_path: Path) -> None:
    with pytest.raises(IntegrityError, match="empty directory"):
        create_manifest(
            tmp_path,
            "pvs:sha256:" + "0" * 64,
            "2026-01-01T00:00:00Z",
        )
