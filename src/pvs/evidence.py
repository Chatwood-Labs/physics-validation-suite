"""Build the authoritative evidence record and its content identity."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .canonical import canonical_sha256
from .case import CaseDefinition
from .constants import (
    CANONICALIZATION,
    CASE_SCHEMA,
    COMPARISON_PROFILE,
    EVIDENCE_PREFIX,
    EVIDENCE_SCHEMA,
    HASH_ALGORITHM,
)
from .finding import create_finding_metadata
from .hashing import file_identity
from .models import CheckResult, ExecutionResult, Status, aggregate_status
from .package import ResolvedPackagePolicy, package_contract
from .provenance import build_provenance, installed_content_identity, runtime_environment
from .version import __version__


def _execution_record(
    execution: ExecutionResult,
    executable: dict[str, Any] | None,
    declared_environment: dict[str, str],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "mode": execution.mode,
        "command": execution.command,
        "working_directory": execution.working_directory,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "duration_seconds": execution.duration_seconds,
        "return_code": execution.return_code,
        "expected_exit_codes": execution.expected_exit_codes,
        "succeeded": execution.succeeded,
        "error": execution.error,
        "declared_environment": declared_environment,
        "resolved_executable": executable,
    }
    if execution.stdout_path is not None and execution.stdout_path.is_file():
        value["stdout"] = {
            "package_path": "logs/stdout.txt",
            **file_identity(execution.stdout_path),
        }
    if execution.stderr_path is not None and execution.stderr_path.is_file():
        value["stderr"] = {
            "package_path": "logs/stderr.txt",
            **file_identity(execution.stderr_path),
        }
    return value


def create_evidence(
    *,
    run_id: str,
    case: CaseDefinition,
    execution: ExecutionResult,
    executable: dict[str, Any] | None,
    artifact_records: list[dict[str, Any]],
    check_results: list[CheckResult],
    provenance_issues: list[str],
    package_paths: dict[str, str],
    package_policy: ResolvedPackagePolicy,
) -> dict[str, Any]:
    checks = [result.as_dict() for result in check_results]
    statuses = [result.status for result in check_results]
    if provenance_issues or not execution.succeeded:
        statuses.append(Status.ERROR)
    overall = aggregate_status(statuses)
    counts = Counter(result.status.value for result in check_results)
    for status in Status:
        counts.setdefault(status.value, 0)

    for artifact in artifact_records:
        artifact["package_path"] = package_paths.get(str(artifact["id"]))

    execution_declaration = case.data.get("execution", {})
    record: dict[str, Any] = {
        "run": {
            "run_id": run_id,
            "mode": execution.mode,
            "started_at": execution.started_at,
            "finished_at": execution.finished_at,
            "duration_seconds": execution.duration_seconds,
        },
        "pvs": {
            "name": "Physics Validation Suite",
            "version": __version__,
            "implementation": "chatwood-labs/physics-validation-suite",
            "case_schema": CASE_SCHEMA,
            "evidence_schema": EVIDENCE_SCHEMA,
            "comparison_profile": COMPARISON_PROFILE,
            "integrity_profile": "pvs-rfc8785-sha256-v1",
            "content_identity": installed_content_identity(),
            "build": build_provenance(),
        },
        "case": {
            "id": case.id,
            "version": case.version,
            "title": case.data["title"],
            "description": case.data.get("description"),
            "classifications": case.data["classifications"],
            "filename": case.path.name,
            "raw_sha256": case.raw_identity["sha256"],
            "raw_size_bytes": case.raw_identity["size_bytes"],
            "semantic_sha256": case.semantic_sha256,
            "resolved_definition": case.data,
        },
        "subject": case.data["subject"],
        "execution": _execution_record(
            execution,
            executable,
            {str(k): str(v) for k, v in execution_declaration.get("environment", {}).items()},
        ),
        "artifacts": artifact_records,
        "references": case.data.get("references", []),
        "checks": checks,
        "summary": {
            "status": overall.value,
            "checks_total": len(check_results),
            "counts": dict(sorted(counts.items())),
            "provenance_status": "COMPLETE" if not provenance_issues else "INCOMPLETE",
            "provenance_issues": provenance_issues,
        },
        "package": package_contract(package_policy),
        "environment": runtime_environment(),
    }
    digest = canonical_sha256(record)
    return {
        "schema": EVIDENCE_SCHEMA,
        "record": record,
        "integrity": {
            "canonicalization": CANONICALIZATION,
            "algorithm": HASH_ALGORITHM,
            "digest": digest,
            "evidence_id": f"{EVIDENCE_PREFIX}{digest}",
            "finding": create_finding_metadata(record),
        },
    }
