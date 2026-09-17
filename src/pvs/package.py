"""Evidence package manifest creation."""

from __future__ import annotations

import stat as stat_module
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .canonical import canonical_sha256
from .constants import (
    CANONICALIZATION,
    HASH_ALGORITHM,
    HTML_REPORT_PROFILE,
    MANIFEST_SCHEMA,
    PDF_REPORT_PROFILE,
    V2_MANIFEST_SCHEMA,
)
from .errors import IntegrityError
from .hashing import file_identity
from .jsonutil import write_pretty
from .paths import portable_relative_path, stat_is_link_or_reparse, walk_directory_entries

PolicySource = Literal["schema_default", "case", "runtime_override"]

PACKAGE_POLICY_DEFAULTS: dict[str, bool] = {
    "embed_artifacts": False,
    "html": True,
    "pdf": True,
}


@dataclass(frozen=True)
class ResolvedPolicyValue:
    """One effective package switch and the declaration layer that selected it."""

    effective: bool
    source: PolicySource

    def as_dict(self) -> dict[str, bool | str]:
        return {"effective": self.effective, "source": self.source}


@dataclass(frozen=True)
class ResolvedPackagePolicy:
    """Closed, typed package policy resolved before any package bytes are emitted."""

    embed_artifacts: ResolvedPolicyValue
    html: ResolvedPolicyValue
    pdf: ResolvedPolicyValue

    def as_dict(self) -> dict[str, dict[str, bool | str]]:
        return {
            "embed_artifacts": self.embed_artifacts.as_dict(),
            "html": self.html.as_dict(),
            "pdf": self.pdf.as_dict(),
        }


def _require_bool_or_none(name: str, value: object) -> None:
    if value is not None and type(value) is not bool:
        raise TypeError(f"{name} must be bool or None, not {type(value).__name__}")


def _resolve_policy_value(
    package_config: Mapping[str, Any],
    name: str,
    override: bool | None,
) -> ResolvedPolicyValue:
    if override is not None:
        return ResolvedPolicyValue(override, "runtime_override")
    if name in package_config:
        case_value = package_config[name]
        if type(case_value) is not bool:
            # Normally rejected by the case schema. Keep this resolver safe when
            # called directly, too; in particular, never coerce 0/1 or strings.
            raise TypeError(f"package.{name} must be bool, not {type(case_value).__name__}")
        return ResolvedPolicyValue(case_value, "case")
    return ResolvedPolicyValue(PACKAGE_POLICY_DEFAULTS[name], "schema_default")


def resolve_package_policy(
    package_config: Mapping[str, Any] | None,
    *,
    embed_artifacts: bool | None = None,
    render_html_report: bool | None = None,
    render_pdf_report: bool | None = None,
) -> ResolvedPackagePolicy:
    """Resolve defaults, explicit case keys and API overrides without coercion."""

    _require_bool_or_none("embed_artifacts", embed_artifacts)
    _require_bool_or_none("render_html_report", render_html_report)
    _require_bool_or_none("render_pdf_report", render_pdf_report)
    declaration: Mapping[str, Any] = package_config or {}
    return ResolvedPackagePolicy(
        embed_artifacts=_resolve_policy_value(
            declaration, "embed_artifacts", embed_artifacts
        ),
        html=_resolve_policy_value(declaration, "html", render_html_report),
        pdf=_resolve_policy_value(declaration, "pdf", render_pdf_report),
    )


def report_profiles(manifest_schema: str = MANIFEST_SCHEMA) -> dict[str, str]:
    if manifest_schema == V2_MANIFEST_SCHEMA:
        return {"html": "pvs-html/2", "pdf": "pvs-pdf/2"}
    return {"html": HTML_REPORT_PROFILE, "pdf": PDF_REPORT_PROFILE}


def package_contract(policy: ResolvedPackagePolicy) -> dict[str, Any]:
    """Return the exact package contract bound into evidence and its manifest."""

    return {
        "manifest_schema": MANIFEST_SCHEMA,
        "policy": policy.as_dict(),
        "report_profiles": report_profiles(),
    }


def package_file_role(path: str) -> str:
    if path == "evidence.json":
        return "evidence"
    if path == "case/pvs.yaml":
        return "case"
    if path.startswith("logs/"):
        return "log"
    if path in {"report.html", "report.pdf"}:
        return "report"
    return "artifact"


def normalized_package_path(path: str) -> str:
    return portable_relative_path(path, integrity=True)


def create_manifest(
    directory: Path,
    evidence_id: str,
    created_at: str,
    *,
    policy: ResolvedPackagePolicy | None = None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    collision_keys: set[str] = {"manifest.json"}
    try:
        entries = sorted(
            walk_directory_entries(directory),
            key=lambda item: item[0].relative_to(directory).as_posix(),
        )
    except OSError as exc:
        raise IntegrityError(f"could not safely enumerate package: {exc}") from exc
    for path, metadata in entries:
        relative = path.relative_to(directory).as_posix()
        if relative == "manifest.json":
            continue
        if stat_is_link_or_reparse(metadata):
            raise IntegrityError(
                f"package files must not be symlinks or reparse points: {relative}"
            )
        if stat_module.S_ISDIR(metadata.st_mode):
            continue
        if not stat_module.S_ISREG(metadata.st_mode):
            raise IntegrityError(f"package entries must be regular files: {relative}")
        normalized = normalized_package_path(relative)
        collision_key = normalized.casefold()
        if collision_key in collision_keys:
            raise IntegrityError(f"duplicate package path after normalization: {relative}")
        collision_keys.add(collision_key)
        files.append(
            {
                "path": normalized,
                "role": package_file_role(normalized),
                **file_identity(path),
            }
        )
    if not files:
        raise IntegrityError("cannot create a package manifest for an empty directory")
    resolved_policy = policy or resolve_package_policy(None)
    contract = package_contract(resolved_policy)
    package = {
        "evidence_id": evidence_id,
        "created_at": created_at,
        "policy": contract["policy"],
        "report_profiles": contract["report_profiles"],
        "files": files,
    }
    digest = canonical_sha256(package)
    return {
        "schema": MANIFEST_SCHEMA,
        "package": package,
        "integrity": {
            "canonicalization": CANONICALIZATION,
            "algorithm": HASH_ALGORITHM,
            "digest": digest,
            "package_id": f"pvs-package:sha256:{digest}",
        },
    }


def write_manifest(
    directory: Path,
    evidence_id: str,
    created_at: str,
    *,
    policy: ResolvedPackagePolicy | None = None,
) -> dict[str, Any]:
    manifest = create_manifest(directory, evidence_id, created_at, policy=policy)
    write_pretty(directory / "manifest.json", manifest)
    return manifest
