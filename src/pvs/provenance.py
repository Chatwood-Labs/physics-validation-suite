"""Non-secret runtime, dependency and implementation provenance."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import stat
import sys
from pathlib import Path
from typing import Any

from . import _build_info
from .canonical import canonical_sha256
from .hashing import file_identity
from .paths import path_is_link_or_reparse

CONTENT_IDENTITY_PROFILE = "pvs-installed-package-tree-sha256-v1"
_PACKAGE_ROOT = Path(__file__).parent
_RUNTIME_DISTRIBUTIONS = (
    "jsonschema",
    "netCDF4",
    "numpy",
    "pypdf",
    "PyYAML",
    "reportlab",
    "rfc8785",
)


def installed_content_manifest(package_root: Path | None = None) -> dict[str, Any]:
    """Describe installed PVS package bytes without distribution self-reference.

    The profile intentionally covers the importable ``pvs/`` tree rather than a
    wheel archive or ``*.dist-info/RECORD``. Bytecode caches are derived runtime
    state and are excluded. Every other regular file is bound by normalized
    POSIX path, byte count and SHA-256.
    """

    root = package_root if package_root is not None else _PACKAGE_ROOT
    if path_is_link_or_reparse(root) or not root.is_dir():
        raise OSError(f"installed PVS package root is not a regular directory: {root}")
    entries: list[dict[str, Any]] = []
    for current_text, directories, filenames in os.walk(root, followlinks=False):
        current = Path(current_text)
        kept_directories: list[str] = []
        for name in sorted(directories):
            child = current / name
            if name == "__pycache__":
                continue
            if path_is_link_or_reparse(child) or not child.is_dir():
                raise OSError(f"installed PVS package contains a linked directory: {child}")
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = current / name
            if path_is_link_or_reparse(path):
                raise OSError(f"installed PVS package contains a linked file: {path}")
            if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                raise OSError(f"installed PVS package contains a non-regular file: {path}")
            identity = file_identity(path)
            entries.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": identity["sha256"],
                    "size_bytes": identity["size_bytes"],
                }
            )
    entries.sort(key=lambda item: str(item["path"]))
    if not entries:
        raise OSError(f"installed PVS package contains no identifiable files: {root}")
    return {"profile": CONTENT_IDENTITY_PROFILE, "files": entries}


def installed_content_identity(package_root: Path | None = None) -> dict[str, Any]:
    """Return the compact identity recorded in evidence provenance."""

    manifest = installed_content_manifest(package_root)
    return {
        "profile": CONTENT_IDENTITY_PROFILE,
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "digest": canonical_sha256(manifest),
        "files_count": len(manifest["files"]),
    }


def build_provenance() -> dict[str, Any]:
    """Return source facts embedded into this installed build."""

    return {
        "source_identity": _build_info.SOURCE_IDENTITY,
        "source_repository": _build_info.SOURCE_REPOSITORY,
        "source_revision": _build_info.SOURCE_REVISION,
        "source_dirty": _build_info.SOURCE_DIRTY,
        "source_date_epoch": _build_info.SOURCE_DATE_EPOCH,
    }


def runtime_environment() -> dict[str, Any]:
    dependencies: list[dict[str, str]] = []
    for name in _RUNTIME_DISTRIBUTIONS:
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "not-installed"
        dependencies.append({"name": name, "version": version})
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": dependencies,
        "byteorder": sys.byteorder,
    }
