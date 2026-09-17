"""Resolve, snapshot and optionally embed declared artefacts."""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .case import CaseDefinition
from .errors import ArtifactError
from .hashing import file_identity, snapshot_file
from .paths import confined_path


@dataclass(frozen=True)
class ResolvedArtifact:
    id: str
    root: Path
    declaration: dict[str, Any]
    resolution_error: str | None = None

    @property
    def path(self) -> Path:
        # Re-resolve on every access. A subject process may have created or
        # replaced an output path after the pre-execution snapshot.
        return confined_path(
            self.root,
            str(self.declaration["path"]),
            reject_symlinks=True,
        )

    @property
    def role(self) -> str:
        return str(self.declaration["role"])

    @property
    def format(self) -> str:
        return str(self.declaration["format"])

    @property
    def required(self) -> bool:
        return bool(self.declaration.get("required", True))


def resolve_artifacts(case: CaseDefinition) -> dict[str, ResolvedArtifact]:
    return {
        artifact_id: ResolvedArtifact(
            id=artifact_id,
            root=case.root,
            declaration=declaration,
        )
        for artifact_id, declaration in case.data["artifacts"].items()
    }


def snapshot(artifact: ResolvedArtifact) -> dict[str, Any]:
    try:
        path = artifact.path
    except ArtifactError as exc:
        return {"exists": False, "resolution_error": str(exc)}
    base: dict[str, Any] = {
        "exists": path.is_file(),
    }
    if base["exists"]:
        base.update(file_identity(path))
    return base


def acquire_validation_snapshot(
    destination: Path,
    artifacts: dict[str, ResolvedArtifact],
) -> tuple[dict[str, ResolvedArtifact], dict[str, dict[str, Any]]]:
    """Copy each validation input once and bind checks to those immutable bytes."""

    snapshot_artifacts: dict[str, ResolvedArtifact] = {}
    identities: dict[str, dict[str, Any]] = {}
    for index, artifact_id in enumerate(sorted(artifacts)):
        artifact = artifacts[artifact_id]
        source_name = Path(str(artifact.declaration["path"])).name
        relative = Path(f"{index:04d}") / source_name
        declaration = dict(artifact.declaration)
        declaration["path"] = relative.as_posix()
        snapshot_artifact = ResolvedArtifact(
            id=artifact.id,
            root=destination,
            declaration=declaration,
        )
        snapshot_artifacts[artifact_id] = snapshot_artifact
        try:
            source = artifact.path
            try:
                metadata = source.stat()
            except FileNotFoundError:
                identities[artifact_id] = {"exists": False}
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactError(f"artifact is not a regular file: {artifact_id} ({source})")
            identities[artifact_id] = {
                "exists": True,
                **snapshot_file(source, destination / relative),
            }
        except (ArtifactError, OSError) as exc:
            identities[artifact_id] = {"exists": False, "resolution_error": str(exc)}
            snapshot_artifacts[artifact_id] = replace(snapshot_artifact, resolution_error=str(exc))
    return snapshot_artifacts, identities


def evidence_artifacts(
    case: CaseDefinition,
    artifacts: dict[str, ResolvedArtifact],
    pre_execution: dict[str, dict[str, Any]],
    validation_input: dict[str, dict[str, Any]],
    post_validation: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for artifact_id in sorted(artifacts):
        artifact = artifacts[artifact_id]
        declaration = artifact.declaration
        records.append(
            {
                "id": artifact.id,
                "role": artifact.role,
                "format": artifact.format,
                "required": artifact.required,
                "path": Path(str(declaration["path"])).as_posix(),
                "media_type": declaration.get("media_type"),
                "description": declaration.get("description"),
                "expected_sha256": declaration.get("expected_sha256"),
                "expected_sha256_matches": (
                    validation_input.get(artifact_id, {}).get("sha256")
                    == declaration.get("expected_sha256")
                    if declaration.get("expected_sha256")
                    and validation_input.get(artifact_id, {}).get("exists")
                    else None
                ),
                "pre_execution": pre_execution.get(artifact_id),
                "validation_input": validation_input.get(artifact_id),
                "post_validation": post_validation.get(artifact_id),
            }
        )
    return records


def embed_artifacts(
    destination: Path,
    artifacts: dict[str, ResolvedArtifact],
) -> dict[str, str]:
    """Copy present artefacts into a role/id hierarchy and return package paths."""

    package_paths: dict[str, str] = {}
    for artifact_id in sorted(artifacts):
        artifact = artifacts[artifact_id]
        try:
            path = artifact.path
        except ArtifactError:
            continue
        if not path.is_file():
            continue
        relative = Path("artifacts") / artifact.role / artifact_id / path.name
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        package_paths[artifact_id] = relative.as_posix()
    return package_paths
