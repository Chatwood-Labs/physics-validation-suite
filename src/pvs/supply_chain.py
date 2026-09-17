"""Deterministic release SBOM and unsigned build-provenance tooling."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from packaging.markers import (
    InvalidMarker,
    Marker,
    UndefinedComparison,
    UndefinedEnvironmentName,
)
from packaging.requirements import InvalidRequirement, Requirement

from .canonical import canonical_sha256
from .hashing import file_identity
from .paths import path_is_link_or_reparse
from .provenance import build_provenance, installed_content_identity
from .version import __version__

_DISTRIBUTION_NAME = "physics-validation-suite"
_BOOTSTRAP_DISTRIBUTIONS = {
    "build",
    "pip",
    "pyproject-hooks",
    "setuptools",
    "wheel",
}
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SOURCE_ARCHIVE_PROFILE = "pvs-source-archive-sha256-v1"
_SOURCE_TREE_PROFILE = "pvs-source-tree-sha256-v1"
_CANONICAL_MARKER_ENVIRONMENT = {
    "implementation_name": "cpython",
    "implementation_version": "3.12.0",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "platform_release": "",
    "platform_system": "Linux",
    "platform_version": "",
    "python_full_version": "3.12.0",
    "python_version": "3.12",
    "sys_platform": "linux",
    "extra": "",
}


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{quote(_normalized_name(name), safe='')}@{quote(version, safe='.+-')}"


def _timestamp(epoch: int) -> str:
    if epoch < 0:
        raise ValueError("SOURCE_DATE_EPOCH must be a non-negative integer")
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def source_material_identity(source: Path) -> dict[str, Any]:
    """Identify the exact source archive or pristine extracted source tree."""

    if path_is_link_or_reparse(source):
        raise OSError(f"release source material must not be a link or reparse point: {source}")
    if source.is_file():
        identity = file_identity(source)
        return {
            "profile": _SOURCE_ARCHIVE_PROFILE,
            "algorithm": "sha256",
            "digest": identity["sha256"],
            "size_bytes": identity["size_bytes"],
        }
    if not source.is_dir():
        raise OSError(f"release source material does not exist: {source}")
    for metadata_name in (".git", ".hg", ".svn"):
        if (source / metadata_name).exists():
            raise OSError(
                "source-tree identity requires a pristine extracted release tree without "
                f"VCS metadata: {source / metadata_name}"
            )
    entries: list[dict[str, Any]] = []
    for current_text, directories, filenames in os.walk(source, followlinks=False):
        current = Path(current_text)
        for name in sorted(directories):
            child = current / name
            if path_is_link_or_reparse(child):
                raise OSError(f"release source contains a linked directory: {child}")
        directories[:] = sorted(directories)
        for name in sorted(filenames):
            path = current / name
            if path_is_link_or_reparse(path):
                raise OSError(f"release source contains a linked file: {path}")
            if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                raise OSError(f"release source contains a non-regular file: {path}")
            identity = file_identity(path)
            entries.append(
                {
                    "path": path.relative_to(source).as_posix(),
                    "sha256": identity["sha256"],
                    "size_bytes": identity["size_bytes"],
                }
            )
    entries.sort(key=lambda item: str(item["path"]))
    if not entries:
        raise OSError(f"release source tree contains no files: {source}")
    manifest = {"profile": _SOURCE_TREE_PROFILE, "files": entries}
    return {
        "profile": _SOURCE_TREE_PROFILE,
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "digest": canonical_sha256(manifest),
        "files_count": len(entries),
    }


def _embedded_source_identity(value: dict[str, Any]) -> dict[str, str]:
    return {
        "profile": str(value["profile"]),
        "algorithm": str(value["algorithm"]),
        "digest": str(value["digest"]),
    }


def _installed_distributions() -> dict[str, importlib.metadata.Distribution]:
    result: dict[str, importlib.metadata.Distribution] = {}
    for distribution in importlib.metadata.distributions():
        metadata = cast(Any, distribution.metadata)
        name = metadata.get("Name")
        if not name:
            continue
        normalized = _normalized_name(name)
        if normalized in _BOOTSTRAP_DISTRIBUTIONS or normalized == _DISTRIBUTION_NAME:
            continue
        existing = result.get(normalized)
        if existing is not None and existing.version != distribution.version:
            raise RuntimeError(
                f"multiple installed versions found for {normalized}: "
                f"{existing.version} and {distribution.version}"
            )
        result[normalized] = distribution
    return result


def _component(distribution: importlib.metadata.Distribution) -> dict[str, Any]:
    name = str(distribution.metadata["Name"])
    version = distribution.version
    component: dict[str, Any] = {
        "type": "library",
        "bom-ref": _purl(name, version),
        "name": name,
        "version": version,
        "purl": _purl(name, version),
    }
    metadata = cast(Any, distribution.metadata)
    license_expression = metadata.get("License-Expression")
    if license_expression:
        component["licenses"] = [{"expression": license_expression}]
    return component


def _dependency_names(distribution: importlib.metadata.Distribution) -> set[str]:
    result: set[str] = set()
    distribution_name = _normalized_name(str(distribution.metadata["Name"]))
    for requirement_text in distribution.requires or []:
        try:
            requirement = Requirement(requirement_text)
        except InvalidRequirement as exc:
            raise RuntimeError(
                f"installed distribution {distribution_name!r} has an invalid "
                f"Requires-Dist entry: {requirement_text!r}"
            ) from exc
        # The release SBOM describes the active runtime environment, not every
        # optional development edge advertised by installed package metadata.
        # Supplying an empty ``extra`` evaluates unconditional/platform markers
        # while excluding dormant ``extra == ...`` requirements.  The root
        # component still binds the complete installed runtime closure.
        if requirement.marker is not None and not requirement.marker.evaluate(
            _CANONICAL_MARKER_ENVIRONMENT
        ):
            continue
        dependency_name = _normalized_name(requirement.name)
        if dependency_name != distribution_name:
            result.add(dependency_name)
    return result


def _load_lock(lock_data: bytes) -> dict[str, Any]:
    try:
        text = lock_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"dependency lock is not UTF-8: {exc}") from exc
    try:
        if sys.version_info >= (3, 11):
            import tomllib

            value = tomllib.loads(text)
        else:  # pragma: no cover - the release profile is CPython 3.12
            tomli = importlib.import_module("tomli")
            value = tomli.loads(text)
    except Exception as exc:
        raise RuntimeError(f"dependency lock is not valid TOML: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("dependency lock must contain a TOML document")
    return value


def _lock_marker_applies(marker: Any) -> bool:
    if marker is None:
        return True
    if not isinstance(marker, str):
        raise RuntimeError("dependency lock contains a non-string environment marker")
    try:
        return Marker(marker).evaluate(_CANONICAL_MARKER_ENVIRONMENT)
    except (InvalidMarker, KeyError, UndefinedComparison, UndefinedEnvironmentName) as exc:
        raise RuntimeError(f"dependency lock contains an invalid marker {marker!r}: {exc}") from exc


def canonical_runtime_lock_graph(
    lock_data: bytes,
    *,
    project_version: str = __version__,
) -> dict[str, dict[str, Any]]:
    """Resolve the exact CPython 3.12/Linux x86_64 ``[all]`` lock graph.

    The returned mapping excludes the PVS root and release bootstrap tools.  It
    binds each normalized runtime distribution name to its exact version and
    direct active runtime dependencies.
    """

    value = _load_lock(lock_data)
    package_values = value.get("package")
    if not isinstance(package_values, list):
        raise RuntimeError("dependency lock contains no package array")
    packages_by_name: dict[str, list[dict[str, Any]]] = {}
    for index, package in enumerate(package_values):
        if not isinstance(package, dict):
            raise RuntimeError(f"dependency lock package {index} is malformed")
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise RuntimeError(f"dependency lock package {index} has no name/version identity")
        packages_by_name.setdefault(_normalized_name(name), []).append(package)

    def select_package(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        raw_name = entry.get("name")
        if not isinstance(raw_name, str):
            raise RuntimeError("dependency lock contains a dependency without a name")
        name = _normalized_name(raw_name)
        candidates = packages_by_name.get(name, [])
        requested_version = entry.get("version")
        if requested_version is not None:
            if not isinstance(requested_version, str):
                raise RuntimeError(f"dependency {name!r} has a non-string version")
            candidates = [item for item in candidates if item.get("version") == requested_version]
        active: list[dict[str, Any]] = []
        for candidate in candidates:
            resolution_markers = candidate.get("resolution-markers")
            if resolution_markers is None or (
                isinstance(resolution_markers, list)
                and any(_lock_marker_applies(marker) for marker in resolution_markers)
            ):
                active.append(candidate)
            elif not isinstance(resolution_markers, list):
                raise RuntimeError(
                    f"dependency lock package {name!r} has malformed resolution markers"
                )
        if len(active) != 1:
            versions = [str(item.get("version")) for item in active or candidates]
            raise RuntimeError(
                f"dependency lock does not resolve {name!r} uniquely for the canonical "
                f"runtime environment: {versions!r}"
            )
        return name, active[0]

    root_name, root = select_package(
        {"name": _DISTRIBUTION_NAME, "version": project_version}
    )
    selected: dict[str, dict[str, Any]] = {root_name: root}
    active_extras: dict[str, set[str]] = {root_name: {"all"}}
    dependencies: dict[str, set[str]] = {}
    pending = [root_name]
    while pending:
        name = pending.pop()
        package = selected[name]
        entries: list[Any] = []
        base_dependencies = package.get("dependencies", [])
        if not isinstance(base_dependencies, list):
            raise RuntimeError(f"dependency lock package {name!r} has malformed dependencies")
        entries.extend(base_dependencies)
        optional = package.get("optional-dependencies", {})
        if not isinstance(optional, dict):
            raise RuntimeError(
                f"dependency lock package {name!r} has malformed optional dependencies"
            )
        for extra in sorted(active_extras.get(name, set())):
            extra_entries = optional.get(extra, [])
            if not isinstance(extra_entries, list):
                raise RuntimeError(
                    f"dependency lock package {name!r} extra {extra!r} is malformed"
                )
            entries.extend(extra_entries)

        resolved: set[str] = set()
        for raw_entry in entries:
            if not isinstance(raw_entry, dict):
                raise RuntimeError(f"dependency lock package {name!r} has a malformed edge")
            if not _lock_marker_applies(raw_entry.get("marker")):
                continue
            dependency_name, dependency_package = select_package(raw_entry)
            if dependency_name == name:
                raise RuntimeError(f"dependency lock contains a runtime self edge for {name!r}")
            existing = selected.get(dependency_name)
            if (
                existing is not None
                and existing.get("version") != dependency_package.get("version")
            ):
                raise RuntimeError(
                    f"dependency lock resolves multiple runtime versions of {dependency_name!r}"
                )
            resolved.add(dependency_name)
            requested_extras = raw_entry.get("extra", [])
            if not isinstance(requested_extras, list) or any(
                not isinstance(extra, str) for extra in requested_extras
            ):
                raise RuntimeError(
                    f"dependency lock edge to {dependency_name!r} has malformed extras"
                )
            before = set(active_extras.get(dependency_name, set()))
            active_extras.setdefault(dependency_name, set()).update(requested_extras)
            if existing is None:
                selected[dependency_name] = dependency_package
                pending.append(dependency_name)
            elif before != active_extras[dependency_name]:
                pending.append(dependency_name)
        dependencies[name] = resolved

    runtime_names = set(selected) - {root_name}
    bootstrap_runtime = runtime_names & _BOOTSTRAP_DISTRIBUTIONS
    if bootstrap_runtime:
        raise RuntimeError(
            "release bootstrap distributions entered the runtime lock closure: "
            f"{sorted(bootstrap_runtime)!r}"
        )
    return {
        name: {
            "version": str(selected[name]["version"]),
            "dependencies": sorted(dependencies.get(name, set()) & runtime_names),
        }
        for name in sorted(runtime_names)
    }


def cyclonedx_sbom(
    subject: Path,
    lock_path: Path,
    source: Path,
    *,
    source_date_epoch: int,
) -> dict[str, Any]:
    """Create a CycloneDX 1.6 SBOM for one external release artefact.

    Run this in a clean environment containing the released wheel and the exact
    extras being qualified. The installed distribution set is the tested
    dependency closure; pip/setuptools/wheel are treated as environment
    bootstrap tools rather than runtime components.
    """

    subject_identity = file_identity(subject)
    lock_identity = file_identity(lock_path)
    source_identity = source_material_identity(source)
    implementation = installed_content_identity()
    root_ref = _purl(_DISTRIBUTION_NAME, __version__)
    root_component: dict[str, Any] = {
        "type": "application",
        "bom-ref": root_ref,
        "group": "Chatwood Labs Ltd",
        "name": _DISTRIBUTION_NAME,
        "version": __version__,
        "purl": root_ref,
        "hashes": [{"alg": "SHA-256", "content": subject_identity["sha256"]}],
        "properties": [
            {"name": "pvs:release-subject", "value": subject.name},
            {
                "name": "pvs:release-subject-size-bytes",
                "value": str(subject_identity["size_bytes"]),
            },
            {"name": "pvs:installed-content-profile", "value": implementation["profile"]},
            {"name": "pvs:installed-content-sha256", "value": implementation["digest"]},
            {"name": "pvs:dependency-lock", "value": lock_path.name},
            {"name": "pvs:dependency-lock-sha256", "value": lock_identity["sha256"]},
            {"name": "pvs:source-material", "value": source.name},
            {"name": "pvs:source-material-profile", "value": source_identity["profile"]},
            {"name": "pvs:source-material-sha256", "value": source_identity["digest"]},
        ],
    }
    installed = _installed_distributions()
    locked_runtime = canonical_runtime_lock_graph(lock_path.read_bytes())
    expected_versions = {
        name: str(package["version"]) for name, package in locked_runtime.items()
    }
    installed_versions = {
        name: distribution.version for name, distribution in installed.items()
    }
    if installed_versions != expected_versions:
        missing = sorted(set(expected_versions) - set(installed_versions))
        unexpected = sorted(set(installed_versions) - set(expected_versions))
        mismatched = sorted(
            name
            for name in set(expected_versions) & set(installed_versions)
            if expected_versions[name] != installed_versions[name]
        )
        raise RuntimeError(
            "installed runtime closure does not exactly match the canonical CPython "
            "3.12/Linux x86_64 [all] closure from uv.lock: "
            f"missing={missing!r}, unexpected={unexpected!r}, "
            f"version_mismatches={mismatched!r}"
        )
    components = sorted(
        (_component(distribution) for distribution in installed.values()),
        key=lambda item: str(item["bom-ref"]),
    )
    references = {
        name: _purl(str(distribution.metadata["Name"]), distribution.version)
        for name, distribution in installed.items()
    }
    dependencies: list[dict[str, Any]] = [
        {"ref": root_ref, "dependsOn": sorted(references.values())}
    ]
    for name in sorted(installed, key=references.__getitem__):
        depends_on = sorted(
            references[dependency]
            for dependency in locked_runtime[name]["dependencies"]
        )
        dependencies.append({"ref": references[name], "dependsOn": depends_on})

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": _timestamp(source_date_epoch),
            "component": root_component,
        },
        "components": components,
        "dependencies": dependencies,
    }


def slsa_provenance(
    subjects: Sequence[Path],
    lock_path: Path,
    source: Path,
    *,
    source_date_epoch: int,
    builder_id: str,
    build_command: str,
    source_repository: str | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Create an unsigned in-toto statement using the SLSA Provenance v1 predicate."""

    if (source_repository is None) != (source_revision is None):
        raise ValueError("source repository and revision must be supplied together")
    if source_revision is not None and _REVISION_PATTERN.fullmatch(source_revision) is None:
        raise ValueError("source revision must be a full 40- or 64-character hexadecimal value")
    source_identity = source_material_identity(source)
    embedded_identity = build_provenance()["source_identity"]
    expected_identity = _embedded_source_identity(source_identity)
    if embedded_identity is None:
        raise RuntimeError(
            "release provenance requires an embedded source identity; build with "
            "PVS_BUILD_SOURCE_ARCHIVE or PVS_BUILD_SOURCE_DIGEST"
        )
    if embedded_identity != expected_identity:
        raise RuntimeError(
            "embedded build source identity does not match the supplied release source"
        )
    subject_values = []
    for subject in sorted(subjects, key=lambda item: item.name):
        subject_values.append(
            {"name": subject.name, "digest": {"sha256": file_identity(subject)["sha256"]}}
        )
    lock_identity = file_identity(lock_path)
    resolved_dependencies: list[dict[str, Any]] = [
        {
            "uri": f"file:{quote(source.name)}",
            "digest": {"sha256": source_identity["digest"]},
            "annotations": {"pvs:identityProfile": source_identity["profile"]},
        },
        {
            "uri": f"file:{quote(lock_path.name)}",
            "digest": {"sha256": lock_identity["sha256"]},
        },
    ]
    if source_repository is not None and source_revision is not None:
        resolved_dependencies.append(
            {
                "uri": f"git+{source_repository}@{source_revision}",
                "digest": {"gitCommit": source_revision},
            }
        )
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": subject_values,
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://chatwoodlabs.com/buildtypes/pvs-python-distribution/v1",
                "externalParameters": {
                    "sourceDateEpoch": source_date_epoch,
                    "buildCommand": build_command,
                },
                "internalParameters": {},
                "resolvedDependencies": resolved_dependencies,
            },
            "runDetails": {
                "builder": {"id": builder_id},
            },
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_release_metadata(
    subjects: Sequence[Path],
    lock_path: Path,
    source: Path,
    output_directory: Path,
    *,
    source_date_epoch: int,
    builder_id: str,
    build_command: str,
    source_repository: str | None = None,
    source_revision: str | None = None,
) -> list[Path]:
    sboms = [
        (
            subject,
            cyclonedx_sbom(
                subject,
                lock_path,
                source,
                source_date_epoch=source_date_epoch,
            ),
        )
        for subject in sorted(subjects, key=lambda item: item.name)
    ]
    provenance = slsa_provenance(
        subjects,
        lock_path,
        source,
        source_date_epoch=source_date_epoch,
        builder_id=builder_id,
        build_command=build_command,
        source_repository=source_repository,
        source_revision=source_revision,
    )

    output_directory.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for subject, sbom in sboms:
        output = output_directory / f"{subject.name}.cdx.json"
        _write_json(output, sbom)
        outputs.append(output)
    provenance_path = output_directory / f"pvs-{__version__}.provenance.intoto.json"
    _write_json(
        provenance_path,
        provenance,
    )
    outputs.append(provenance_path)
    return outputs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subjects", nargs="+", type=Path)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="released source archive or pristine extracted source tree",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        default=int(os.environ["SOURCE_DATE_EPOCH"])
        if "SOURCE_DATE_EPOCH" in os.environ
        else None,
    )
    parser.add_argument(
        "--builder-id",
        default=os.environ.get("PVS_BUILDER_ID"),
    )
    parser.add_argument(
        "--build-command",
        default=os.environ.get("PVS_BUILD_COMMAND"),
        help="exact command used to build the release subjects",
    )
    parser.add_argument("--source-repository")
    parser.add_argument("--source-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.source_date_epoch is None:
        raise SystemExit("SOURCE_DATE_EPOCH or --source-date-epoch is required")
    if args.builder_id is None:
        raise SystemExit("PVS_BUILDER_ID or --builder-id is required")
    if args.build_command is None:
        raise SystemExit("PVS_BUILD_COMMAND or --build-command is required")
    outputs = write_release_metadata(
        args.subjects,
        args.lock,
        args.source,
        args.output,
        source_date_epoch=args.source_date_epoch,
        builder_id=args.builder_id,
        build_command=args.build_command,
        source_repository=args.source_repository,
        source_revision=args.source_revision,
    )
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
