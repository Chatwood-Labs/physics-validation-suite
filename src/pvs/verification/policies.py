"""Record and package policy, embedding and manifest contracts."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from ..constants import (
    EVIDENCE_SCHEMA,
    LEGACY_EVIDENCE_SCHEMA,
    LEGACY_MANIFEST_SCHEMA,
    MANIFEST_SCHEMA,
    V2_EVIDENCE_SCHEMA,
    V2_MANIFEST_SCHEMA,
)
from ..package import (
    PACKAGE_POLICY_DEFAULTS,
    package_file_role,
)
from .results import VerificationCheck, _append


def _record_embedding_policy(
    record: dict[str, Any],
) -> tuple[bool, set[str], set[str], list[str]]:
    """Validate record-side embedding paths without requiring package bytes."""

    enabled = record["package"]["policy"]["embed_artifacts"]["effective"]
    expected_paths: set[str] = set()
    record_paths: set[str] = set()
    rules_ok = True
    details: list[str] = []
    for artifact in record["artifacts"]:
        present = artifact["validation_input"].get("exists") is True
        package_path = artifact.get("package_path")
        expected_path = (
            PurePosixPath("artifacts")
            / str(artifact["role"])
            / str(artifact["id"])
            / PurePosixPath(str(artifact["path"])).name
        ).as_posix()
        if enabled and present:
            expected_paths.add(expected_path)
            if package_path != expected_path:
                rules_ok = False
                details.append(
                    f"{artifact['id']}: expected {expected_path!r}, got {package_path!r}"
                )
        elif package_path is not None:
            rules_ok = False
            details.append(f"{artifact['id']}: package_path must be null, got {package_path!r}")
        if isinstance(package_path, str):
            record_paths.add(package_path)
    rules_ok = rules_ok and record_paths == expected_paths
    if not details:
        details.append(
            f"effective={enabled}; expected={sorted(expected_paths)}; "
            f"recorded={sorted(record_paths)}"
        )
    return rules_ok, expected_paths, record_paths, details


def _verify_report_policy(
    policy: dict[str, Any],
    actual_files: dict[str, Path],
    checks: list[VerificationCheck],
) -> None:
    for name, filename, label in (
        ("html", "report.html", "HTML"),
        ("pdf", "report.pdf", "PDF"),
    ):
        effective = policy[name]["effective"]
        present = filename in actual_files
        _append(
            checks,
            f"package {label} report policy",
            present is effective,
            f"effective={effective} present={present}",
        )


def _verify_policy_sources(
    policy: dict[str, Any],
    record: dict[str, Any],
    checks: list[VerificationCheck],
) -> None:
    definition = record.get("case", {}).get("resolved_definition", {})
    declaration_value = definition.get("package", {}) if isinstance(definition, dict) else {}
    declaration = declaration_value if isinstance(declaration_value, dict) else {}
    for name in ("embed_artifacts", "html", "pdf"):
        setting = policy[name]
        effective = setting["effective"]
        source = setting["source"]
        if source == "schema_default":
            passed = name not in declaration and effective is PACKAGE_POLICY_DEFAULTS[name]
            detail = (
                f"case key absent; effective={effective}; "
                f"schema default={PACKAGE_POLICY_DEFAULTS[name]}"
            )
        elif source == "case":
            passed = name in declaration and declaration.get(name) is effective
            detail = f"case value={declaration.get(name)!r}; effective={effective}"
        else:
            # Runtime arguments are transaction inputs and cannot be reconstructed
            # from the retained case. Their declared effective value is nevertheless
            # bound by the Package ID and checked against the produced file set.
            passed = source == "runtime_override"
            detail = f"runtime override recorded; effective={effective}"
        _append(checks, f"package policy source {name}", passed, detail)


def _verify_embedding_policy(
    policy: dict[str, Any],
    record: dict[str, Any],
    actual_files: dict[str, Path],
    checks: list[VerificationCheck],
) -> None:
    enabled = policy["embed_artifacts"]["effective"]
    expected_paths: set[str] = set()
    record_paths: set[str] = set()
    record_rules_ok = True
    details: list[str] = []
    for artifact in record.get("artifacts", []):
        present = artifact.get("validation_input", {}).get("exists") is True
        package_path = artifact.get("package_path")
        should_embed = enabled and present
        expected_path = (
            PurePosixPath("artifacts")
            / str(artifact.get("role"))
            / str(artifact.get("id"))
            / PurePosixPath(str(artifact.get("path"))).name
        ).as_posix()
        if should_embed:
            expected_paths.add(expected_path)
            if package_path != expected_path:
                record_rules_ok = False
                details.append(
                    f"{artifact.get('id')}: expected {expected_path!r}, got {package_path!r}"
                )
        elif package_path is not None:
            record_rules_ok = False
            details.append(f"{artifact.get('id')}: package_path must be null, got {package_path!r}")
        if isinstance(package_path, str):
            record_paths.add(package_path)

    actual_paths = {path for path in actual_files if package_file_role(path) == "artifact"}
    passed = record_rules_ok and record_paths == expected_paths and actual_paths == expected_paths
    if not details:
        details.append(
            f"effective={enabled}; expected={sorted(expected_paths)}; actual={sorted(actual_paths)}"
        )
    _append(checks, "package artifact embedding policy", passed, "; ".join(details[:5]))


def _verify_evidence_manifest_contract(
    manifest_schema: str,
    package: dict[str, Any],
    record: dict[str, Any],
    checks: list[VerificationCheck],
) -> bool:
    """Bind each evidence generation to its exact package contract generation."""

    evidence_schema = record.get("pvs", {}).get("evidence_schema")
    if evidence_schema == LEGACY_EVIDENCE_SCHEMA:
        passed = manifest_schema == LEGACY_MANIFEST_SCHEMA
        detail = (
            f"evidence={LEGACY_EVIDENCE_SCHEMA} manifest={manifest_schema}; "
            f"required={LEGACY_MANIFEST_SCHEMA}"
        )
    elif evidence_schema in {V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA}:
        expected_manifest = (
            V2_MANIFEST_SCHEMA if evidence_schema == V2_EVIDENCE_SCHEMA else MANIFEST_SCHEMA
        )
        contract = record.get("package")
        passed = (
            isinstance(contract, dict)
            and manifest_schema == expected_manifest
            and contract.get("manifest_schema") == manifest_schema
            and contract.get("policy") == package.get("policy")
            and contract.get("report_profiles") == package.get("report_profiles")
            and package.get("created_at") == record.get("run", {}).get("finished_at")
        )
        detail = (
            f"evidence={evidence_schema} manifest={manifest_schema}; "
            "policy, renderer profiles and completion timestamp must match evidence exactly"
        )
    else:
        passed = False
        detail = f"unsupported evidence package contract: {evidence_schema!r}"
    _append(checks, "evidence/manifest contract", passed, detail)
    return passed
