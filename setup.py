"""Setuptools commands that embed non-recursive source/build provenance."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from runpy import run_path
from typing import Any
from urllib.parse import urlsplit

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

_ROOT = Path(__file__).resolve().parent
_SOURCE_INFO = _ROOT / "src" / "pvs" / "_build_info.py"
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def _boolean_environment(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _existing_info() -> dict[str, Any]:
    if not _SOURCE_INFO.is_file():
        return {}
    return run_path(str(_SOURCE_INFO))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_identity(existing: dict[str, Any]) -> dict[str, str] | None:
    archive_text = os.environ.get("PVS_BUILD_SOURCE_ARCHIVE")
    digest_text = os.environ.get("PVS_BUILD_SOURCE_DIGEST")
    profile_text = os.environ.get("PVS_BUILD_SOURCE_IDENTITY_PROFILE")
    if archive_text is not None:
        if profile_text not in {None, "pvs-source-archive-sha256-v1"}:
            raise RuntimeError(
                "PVS_BUILD_SOURCE_ARCHIVE requires the pvs-source-archive-sha256-v1 "
                "identity profile"
            )
        archive = Path(archive_text)
        if not archive.is_absolute():
            archive = _ROOT / archive
        if not archive.is_file():
            raise RuntimeError(f"PVS_BUILD_SOURCE_ARCHIVE is not a regular file: {archive}")
        archive_digest = _sha256(archive)
        if digest_text is not None and digest_text.strip().lower() != archive_digest:
            raise RuntimeError(
                "PVS_BUILD_SOURCE_DIGEST does not match PVS_BUILD_SOURCE_ARCHIVE"
            )
        digest_text = archive_digest
    if digest_text is None:
        value = existing.get("SOURCE_IDENTITY")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise RuntimeError("embedded SOURCE_IDENTITY must be a dictionary or None")
        result = {str(key): str(item) for key, item in value.items()}
    else:
        result = {
            "profile": profile_text or "pvs-source-archive-sha256-v1",
            "algorithm": "sha256",
            "digest": digest_text.strip().lower(),
        }
    if set(result) != {"profile", "algorithm", "digest"}:
        raise RuntimeError(
            "embedded source identity must contain only profile, algorithm and digest"
        )
    if result.get("profile") not in {
        "pvs-source-archive-sha256-v1",
        "pvs-source-tree-sha256-v1",
    }:
        raise RuntimeError("PVS_BUILD_SOURCE_IDENTITY_PROFILE is not a supported profile")
    if result.get("algorithm") != "sha256":
        raise RuntimeError("embedded source identity algorithm must be sha256")
    if re.fullmatch(r"[0-9a-f]{64}", result.get("digest", "")) is None:
        raise RuntimeError("PVS_BUILD_SOURCE_DIGEST must be 64 lowercase hexadecimal characters")
    return result


def _build_info() -> dict[str, Any]:
    existing = _existing_info()
    revision = os.environ.get("PVS_BUILD_SOURCE_REVISION")
    if revision is None:
        revision = existing.get("SOURCE_REVISION")
    if revision is not None:
        revision = str(revision).strip().lower()
        if _REVISION_PATTERN.fullmatch(revision) is None:
            raise RuntimeError(
                "PVS_BUILD_SOURCE_REVISION must be a full 40- or 64-character hexadecimal "
                "VCS revision"
            )

    dirty = _boolean_environment("PVS_BUILD_SOURCE_DIRTY")
    if dirty is None:
        existing_dirty = existing.get("SOURCE_DIRTY")
        dirty = existing_dirty if isinstance(existing_dirty, bool) else None

    repository = os.environ.get("PVS_BUILD_SOURCE_REPOSITORY")
    if repository is None:
        existing_repository = existing.get("SOURCE_REPOSITORY")
        repository = existing_repository if isinstance(existing_repository, str) else None
    elif not repository.strip():
        raise RuntimeError("PVS_BUILD_SOURCE_REPOSITORY must not be empty")
    else:
        repository = repository.strip()
        parsed_repository = urlsplit(repository)
        if not parsed_repository.scheme:
            raise RuntimeError("PVS_BUILD_SOURCE_REPOSITORY must be an absolute URI")

    raw_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    epoch: int | None
    if raw_epoch is None:
        existing_epoch = existing.get("SOURCE_DATE_EPOCH")
        epoch = existing_epoch if isinstance(existing_epoch, int) else None
    else:
        try:
            epoch = int(raw_epoch)
        except ValueError as exc:
            raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer") from exc
        if epoch < 0:
            raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer")

    return {
        "source_identity": _source_identity(existing),
        "repository": repository,
        "revision": revision,
        "dirty": dirty,
        "epoch": epoch,
    }


def _render_build_info() -> str:
    info = _build_info()
    return (
        '"""Build provenance generated by the PVS build backend."""\n\n'
        f"SOURCE_IDENTITY: dict[str, str] | None = {info['source_identity']!r}\n"
        f"SOURCE_REPOSITORY: str | None = {info['repository']!r}\n"
        f"SOURCE_REVISION: str | None = {info['revision']!r}\n"
        f"SOURCE_DIRTY: bool | None = {info['dirty']!r}\n"
        f"SOURCE_DATE_EPOCH: int | None = {info['epoch']!r}\n"
    )


class _BuildPy(build_py):
    def run(self) -> None:
        super().run()
        destination = Path(self.build_lib) / "pvs" / "_build_info.py"
        destination.write_text(_render_build_info(), encoding="utf-8", newline="\n")


class _Sdist(sdist):
    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        super().make_release_tree(base_dir, files)
        destination = Path(base_dir) / "src" / "pvs" / "_build_info.py"
        destination.write_text(_render_build_info(), encoding="utf-8", newline="\n")


setup(cmdclass={"build_py": _BuildPy, "sdist": _Sdist})
