"""Cross-field producer invariants and aggregate status contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ..canonical import canonical_sha256
from ..case import _source_specs, case_schema_kind, validate_case_semantics
from ..checks.contracts import expected_criterion
from ..constants import (
    CASE_SCHEMA,
    EVIDENCE_SCHEMA,
    LEGACY_CASE_SCHEMA,
    LEGACY_EVIDENCE_SCHEMA,
    V2_EVIDENCE_SCHEMA,
)
from ..errors import ArtifactError
from ..models import STATUS_PRIORITY, Status
from ..package import (
    normalized_package_path,
)
from .observations import (
    CompletedObservation,
    InvalidObservation,
    require_observation_metadata,
    validate_observation,
)
from .policies import _record_embedding_policy, _verify_policy_sources
from .results import VerificationCheck, _append
from .schemas import _schema_errors


def _snapshot_well_formed(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("exists"), bool):
        return False
    if value["exists"]:
        return (
            isinstance(value.get("sha256"), str)
            and len(value["sha256"]) == 64
            and isinstance(value.get("size_bytes"), int)
            and value["size_bytes"] >= 0
            and "resolution_error" not in value
        )
    return "sha256" not in value and "size_bytes" not in value


def _snapshot_identity_changed(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Mirror the producer's material identity comparison exactly."""

    return (
        left.get("exists") != right.get("exists")
        or left.get("sha256") != right.get("sha256")
        or left.get("size_bytes") != right.get("size_bytes")
    )


def _parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def _has_missing_check_input(
    declared: dict[str, Any], validation_inputs: dict[str, dict[str, Any]]
) -> bool:
    # An exists check produces PASS/FAIL/WARN, never a missing-input SKIP.
    if declared["type"] == "exists":
        return False
    inputs = [source["artifact"] for source in _source_specs(declared)]
    if declared["type"] == "schema":
        inputs.extend([declared["artifact"], declared["schema_artifact"]])
    return any(
        validation_inputs.get(artifact_id, {}).get("exists") is False
        and not validation_inputs[artifact_id].get("resolution_error")
        for artifact_id in inputs
    )


def _completed_inputs_available(
    declared: dict[str, Any], validation_inputs: dict[str, dict[str, Any]]
) -> bool:
    """A completed check cannot have consumed a missing or unresolved snapshot."""

    if declared["type"] == "exists":
        # This check observes the snapshot path's existence, without reading it.
        # The lifecycle invariant separately requires ERROR provenance for a
        # failed acquisition, including historical v2 evidence.
        return True
    inputs = [source["artifact"] for source in _source_specs(declared)]
    if declared["type"] == "schema":
        inputs.extend([declared["artifact"], declared["schema_artifact"]])
    return all(
        validation_inputs.get(artifact_id, {}).get("exists") is True
        and not validation_inputs[artifact_id].get("resolution_error")
        for artifact_id in inputs
    )


def _producer_invariants(
    record: dict[str, Any],
    checks: list[VerificationCheck],
    *,
    evidence_schema: str,
) -> None:
    """Check cross-field rules that JSON Schema cannot express cleanly."""

    case_record = record["case"]
    definition = case_record["resolved_definition"]
    expected_case_schema = (
        LEGACY_CASE_SCHEMA if evidence_schema == LEGACY_EVIDENCE_SCHEMA else CASE_SCHEMA
    )
    pvs_record = record["pvs"]
    generation_contract_ok = (
        pvs_record["evidence_schema"] == evidence_schema
        and pvs_record["case_schema"] == expected_case_schema
        and definition.get("schema") == expected_case_schema
    )
    _append(
        checks,
        "evidence generation contract",
        generation_contract_ok,
        f"envelope={evidence_schema} case={expected_case_schema}",
    )
    try:
        schema_kind = case_schema_kind(definition, allow_legacy=True)
        case_errors = _schema_errors(definition, schema_kind)
    except Exception as exc:
        case_errors = [str(exc)]
    declared_case_schema = definition.get("schema")
    _append(
        checks,
        "resolved case schema",
        not case_errors,
        "; ".join(case_errors[:5]) or f"valid {declared_case_schema}",
    )
    if case_errors:
        return
    try:
        validate_case_semantics(definition, None)
    except Exception as exc:
        _append(checks, "resolved case semantics", False, str(exc))
        return
    _append(checks, "resolved case semantics", True, "cross-references and paths are valid")

    semantic_digest = canonical_sha256(definition)
    case_identity_ok = (
        case_record["id"] == definition["id"]
        and case_record["version"] == definition["version"]
        and case_record["title"] == definition["title"]
        and case_record.get("description") == definition.get("description")
        and case_record["classifications"] == definition["classifications"]
        and case_record["semantic_sha256"] == semantic_digest
    )
    _append(
        checks, "case semantic identity", case_identity_ok, f"computed sha256:{semantic_digest}"
    )
    _append(
        checks,
        "subject declaration",
        record["subject"] == definition["subject"],
        "evidence subject equals resolved case subject",
    )
    _append(
        checks,
        "reference declarations",
        record["references"] == definition.get("references", []),
        "evidence references equal resolved case references",
    )

    execution = record["execution"]
    run = record["run"]
    mode = run["mode"]
    timing_ok = (
        execution["mode"] == mode
        and execution["started_at"] == run["started_at"]
        and execution["finished_at"] == run["finished_at"]
        and execution["duration_seconds"] == run["duration_seconds"]
    )
    _append(checks, "run/execution identity", timing_ok, f"mode={mode}")
    try:
        chronology_ok = _parse_rfc3339(run["finished_at"]) >= _parse_rfc3339(
            run["started_at"]
        )
    except (TypeError, ValueError):
        chronology_ok = False
    _append(
        checks,
        "execution chronology",
        chronology_ok,
        "finished_at must be greater than or equal to started_at",
    )
    declaration = definition.get("execution")
    ran_subject = False
    if mode == "run":
        execution_declaration_ok = declaration is not None and (
            execution["command"] == declaration["command"]
            and execution["working_directory"] == declaration.get("working_directory", ".")
            and execution["expected_exit_codes"] == declaration.get("expected_exit_codes", [0])
            and execution["declared_environment"] == declaration.get("environment", {})
        )
        expected_success = (
            execution["error"] is None
            and execution["return_code"] in execution["expected_exit_codes"]
        )
        resolved_executable = execution["resolved_executable"]
        preflight_abort = (
            execution["return_code"] is None
            and resolved_executable is None
            and execution["error"] == "pre-execution provenance failed; subject was not executed"
        )
        ran_subject = not preflight_abort
        if preflight_abort:
            executable_identity_ok = True
        elif isinstance(resolved_executable, dict):
            requested_matches = (
                isinstance(declaration, dict)
                and isinstance(declaration.get("command"), list)
                and bool(declaration["command"])
                and resolved_executable.get("requested") == declaration["command"][0]
            )
            executable_identity_ok = requested_matches and isinstance(
                resolved_executable.get("resolved"), bool
            )
            if resolved_executable.get("resolved") is True:
                identity_names = {
                    "sha256",
                    "size_bytes",
                    "post_execution_sha256",
                    "post_execution_size_bytes",
                }
                identity_fields_present = {
                    name for name in identity_names if name in resolved_executable
                }
                identity_shape_ok = (
                    not identity_fields_present or identity_fields_present == identity_names
                )
                identity_unchanged = (
                    isinstance(resolved_executable.get("sha256"), str)
                    and isinstance(resolved_executable.get("size_bytes"), int)
                    and isinstance(resolved_executable.get("post_execution_sha256"), str)
                    and isinstance(resolved_executable.get("post_execution_size_bytes"), int)
                    and resolved_executable.get("sha256")
                    == resolved_executable.get("post_execution_sha256")
                    and resolved_executable.get("size_bytes")
                    == resolved_executable.get("post_execution_size_bytes")
                )
                executable_identity_ok = executable_identity_ok and (
                    (
                        evidence_schema == LEGACY_EVIDENCE_SCHEMA
                        or (
                            isinstance(resolved_executable.get("filename"), str)
                            and bool(resolved_executable["filename"])
                            and resolved_executable.get("launch_profile")
                            == "pvs-private-executable-snapshot/1"
                        )
                    )
                    and resolved_executable.get("unchanged_during_execution")
                    is identity_unchanged
                    and identity_shape_ok
                )
                if execution["succeeded"]:
                    executable_identity_ok = executable_identity_ok and (
                        identity_fields_present == identity_names and identity_unchanged
                    )
            else:
                forbidden_unresolved_fields = {
                    "launch_profile",
                    "sha256",
                    "size_bytes",
                    "post_execution_sha256",
                    "post_execution_size_bytes",
                    "unchanged_during_execution",
                }
                executable_identity_ok = executable_identity_ok and (
                    execution["return_code"] is None
                    and execution["error"] is not None
                    and not forbidden_unresolved_fields.intersection(resolved_executable)
                )
        else:
            executable_identity_ok = False
    else:
        execution_declaration_ok = (
            execution["command"] is None
            and execution["working_directory"] == "."
            and execution["return_code"] is None
            and execution["expected_exit_codes"] == []
            and execution["declared_environment"] == (declaration or {}).get("environment", {})
        )
        expected_success = execution["error"] is None
        executable_identity_ok = execution["resolved_executable"] is None

    if evidence_schema in {V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA}:
        if mode == "run":
            stdout = execution.get("stdout")
            stderr = execution.get("stderr")
            log_contract_ok = (
                isinstance(stdout, dict)
                and stdout.get("package_path") == "logs/stdout.txt"
                and isinstance(stderr, dict)
                and stderr.get("package_path") == "logs/stderr.txt"
            )
            log_detail = "run mode binds stdout and stderr to their exact package paths"
        else:
            log_contract_ok = "stdout" not in execution and "stderr" not in execution
            log_detail = "validate mode records no execution logs"
        _append(checks, "execution log contract", log_contract_ok, log_detail)
    _append(
        checks,
        "execution declaration",
        execution_declaration_ok,
        "execution fields agree with resolved case and mode",
    )
    _append(
        checks,
        "execution success",
        execution["succeeded"] == expected_success,
        f"recomputed succeeded={expected_success}",
    )
    _append(
        checks,
        "executable identity",
        executable_identity_ok,
        "execution provenance is coherent and launched executables are bound"
        if mode == "run"
        else "validate mode has no executed subject",
    )

    declared_artifacts = definition["artifacts"]
    artifact_records = record["artifacts"]
    artifact_ids = [item["id"] for item in artifact_records]
    artifacts_cover = set(artifact_ids) == set(declared_artifacts) and len(artifact_ids) == len(
        declared_artifacts
    )
    _append(
        checks,
        "artifact declaration coverage",
        artifacts_cover,
        f"declared={sorted(declared_artifacts)} recorded={sorted(artifact_ids)}",
    )
    artifact_fields_ok = artifacts_cover
    lifecycle_ok = artifacts_cover
    lifecycle_violation = False
    package_paths: list[str] = []
    if artifacts_cover:
        for item in artifact_records:
            declared = declared_artifacts[item["id"]]
            expected_fields = (
                item["role"] == declared["role"]
                and item["format"] == declared["format"]
                and item["required"] == declared.get("required", True)
                and item["path"] == Path(declared["path"]).as_posix()
                and item.get("media_type") == declared.get("media_type")
                and item.get("description") == declared.get("description")
                and item.get("expected_sha256") == declared.get("expected_sha256")
            )
            artifact_fields_ok = artifact_fields_ok and expected_fields
            snapshots = [
                item["pre_execution"],
                item["validation_input"],
                item["post_validation"],
            ]
            lifecycle_ok = lifecycle_ok and all(_snapshot_well_formed(value) for value in snapshots)
            validation = item["validation_input"]
            expected_hash = declared.get("expected_sha256")
            expected_match = (
                validation.get("sha256") == expected_hash
                if expected_hash and validation.get("exists")
                else None
            )
            lifecycle_ok = lifecycle_ok and item.get("expected_sha256_matches") == expected_match
            if _snapshot_identity_changed(validation, item["post_validation"]):
                lifecycle_violation = True
            if mode == "run" and declared["role"] == "output":
                lifecycle_violation = lifecycle_violation or item["pre_execution"].get(
                    "exists", False
                )
            if ran_subject and declared["role"] != "output":
                lifecycle_violation = lifecycle_violation or _snapshot_identity_changed(
                    item["pre_execution"], validation
                )
            if not ran_subject:
                lifecycle_violation = lifecycle_violation or _snapshot_identity_changed(
                    item["pre_execution"], validation
                )
            if item["required"] and not validation.get("exists", False):
                lifecycle_violation = True
            if expected_hash and validation.get("exists") and not expected_match:
                lifecycle_violation = True
            if any(value.get("resolution_error") for value in snapshots):
                lifecycle_violation = True
            package_path = item.get("package_path")
            if package_path is not None:
                package_paths.append(package_path)
                try:
                    lifecycle_ok = (
                        lifecycle_ok and normalized_package_path(package_path) == package_path
                    )
                except Exception:
                    lifecycle_ok = False
    if lifecycle_violation:
        lifecycle_ok = lifecycle_ok and (
            record["summary"]["provenance_status"] == "INCOMPLETE"
            and record["summary"]["status"] == "ERROR"
        )
    _append(checks, "artifact declaration fields", artifact_fields_ok, "records match declarations")
    _append(checks, "artifact lifecycle", lifecycle_ok, "snapshots and hash claims are coherent")
    _append(
        checks,
        "artifact package paths",
        len(package_paths) == len(set(path.casefold() for path in package_paths)),
        f"{len(package_paths)} retained artifact paths",
    )
    if evidence_schema in {V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA}:
        _verify_policy_sources(record["package"]["policy"], record, checks)
        embedding_ok, _expected_paths, _record_paths, embedding_details = (
            _record_embedding_policy(record)
        )
        _append(
            checks,
            "artifact embedding record policy",
            embedding_ok,
            "; ".join(embedding_details[:5]),
        )
        dependencies = record["environment"]["dependencies"]
        dependency_names = [item["name"] for item in dependencies]
        _append(
            checks,
            "dependency name uniqueness",
            len(dependency_names)
            == len({name.casefold() for name in dependency_names}),
            f"{len(dependency_names)} dependency records",
        )

    declared_checks = definition["checks"]
    check_records = record["checks"]
    artifact_validation_exists = {
        item["id"]: cast(bool, item["validation_input"]["exists"])
        for item in artifact_records
    }
    checks_cover = len(check_records) == len(declared_checks)
    status_contract_ok = checks_cover
    criterion_contract_ok = checks_cover
    observation_contract_ok = checks_cover
    observed_status_ok = checks_cover
    skip_contract_ok = checks_cover
    completed_inputs_ok = checks_cover
    validation_inputs = {item["id"]: item["validation_input"] for item in artifact_records}
    observation_details: list[str] = []
    if checks_cover:
        for result, declared in zip(check_records, declared_checks, strict=True):
            checks_cover = checks_cover and (
                result["id"] == declared["id"]
                and result["type"] == declared["type"]
                and result["required"] == declared.get("required", True)
                and result.get("reference_id") == declared.get("reference_id")
            )
            if evidence_schema == LEGACY_EVIDENCE_SCHEMA:
                try:
                    require_observation_metadata(declared, result, evidence_schema=evidence_schema)
                except (ArtifactError, ArithmeticError, KeyError, TypeError, ValueError) as exc:
                    observation_contract_ok = False
                    observation_details.append(f"{result['id']}: {exc}")
            if evidence_schema in {V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA}:
                status = result["status"]
                required = result["required"]
                if status == "SKIP":
                    skip_contract_ok = skip_contract_ok and _has_missing_check_input(
                        declared, validation_inputs
                    )
                status_contract_ok = status_contract_ok and (
                    (status not in {"WARN", "SKIP"} or required is False)
                    and (status != "FAIL" or required is True)
                )
                if status in {"PASS", "WARN", "FAIL"}:
                    completed_inputs_ok = completed_inputs_ok and _completed_inputs_available(
                        declared, validation_inputs
                    )
                    try:
                        expected: Mapping[str, object] = expected_criterion(declared)
                    except (KeyError, TypeError, ValueError) as exc:
                        criterion_contract_ok = False
                        expected = {"criterion_error": str(exc)}
                    criterion_contract_ok = (
                        criterion_contract_ok and result["criterion"] == expected
                    )
                else:
                    criterion_contract_ok = criterion_contract_ok and result["criterion"] == {}
                observation = validate_observation(
                    declared,
                    result,
                    evidence_schema=evidence_schema,
                    artifact_exists=artifact_validation_exists.get(
                        str(declared.get("artifact"))
                    ),
                )
                observed_ok = not isinstance(observation, InvalidObservation)
                observation_contract_ok = observation_contract_ok and observed_ok
                if isinstance(observation, InvalidObservation):
                    observation_details.append(f"{result['id']}: {observation.reason}")
                if result["status"] in {"PASS", "FAIL", "WARN"}:
                    expected_status = (
                        "PASS"
                        if isinstance(observation, CompletedObservation) and observation.passed
                        else "FAIL"
                        if result["required"]
                        else "WARN"
                    )
                    status_matches = observed_ok and result["status"] == expected_status
                    observed_status_ok = observed_status_ok and status_matches
                    if not status_matches:
                        observation_details.append(
                            f"{result['id']}: observed outcome requires {expected_status}"
                        )
    _append(
        checks,
        "check declaration coverage",
        checks_cover,
        f"{len(check_records)} results for {len(declared_checks)} declarations",
    )
    if evidence_schema == LEGACY_EVIDENCE_SCHEMA:
        _append(
            checks, "check legacy observation metadata", observation_contract_ok,
            "; ".join(observation_details[:5])
            or "source relations, size domains and evaluation dtype are coherent",
        )
    if evidence_schema in {V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA}:
        _append(
            checks,
            "check completed-input contract",
            completed_inputs_ok,
            "completed checks require available inputs; exists may observe legitimate absence",
        )
        _append(
            checks,
            "check missing-input SKIP contract",
            skip_contract_ok,
            "SKIP requires an absent declared check input without a resolution error",
        )
        _append(
            checks,
            "check required/status contract",
            status_contract_ok,
            "WARN and SKIP are optional; FAIL is required",
        )
        _append(
            checks,
            "check criterion contract",
            criterion_contract_ok,
            "successful outcomes use the exact effective criterion; ERROR/SKIP use {}",
        )
        _append(
            checks,
            "check observation contract",
            observation_contract_ok,
            "; ".join(observation_details[:5])
            or "observations have the exact evaluator shape",
        )
        _append(
            checks,
            "check observed/status contract",
            observed_status_ok,
            "; ".join(observation_details[:5])
            or "statuses agree with the recorded gating observations",
        )
    summary = record["summary"]
    provenance_coherent = (summary["provenance_status"] == "COMPLETE") == (
        summary["provenance_issues"] == []
    )
    _append(checks, "provenance summary", provenance_coherent, summary["provenance_status"])
    _append(
        checks,
        "check total",
        summary["checks_total"] == len(check_records),
        f"recomputed {len(check_records)}",
    )


def _recomputed_status(record: dict[str, Any]) -> str:
    statuses: list[Status] = []
    for check in record.get("checks", []):
        try:
            statuses.append(Status(check["status"]))
        except (KeyError, ValueError):
            return "ERROR"
    if record.get("summary", {}).get("provenance_status") != "COMPLETE":
        statuses.append(Status.ERROR)
    if not record.get("execution", {}).get("succeeded", False):
        statuses.append(Status.ERROR)
    if not statuses:
        return "ERROR"
    return max(statuses, key=STATUS_PRIORITY.__getitem__).value
