"""PEP 517 backend wrapper with deterministic source-distribution output."""

from __future__ import annotations

import gzip
import io
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from setuptools import build_meta as _setuptools


def _source_date_epoch() -> int:
    value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is None:
        return 0
    try:
        epoch = int(value)
    except ValueError as exc:
        raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer") from exc
    if epoch < 0:
        raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer")
    return epoch


def _normalized_mode(member: tarfile.TarInfo) -> int:
    if member.isdir():
        return 0o755
    parts = member.name.split("/")
    return 0o755 if len(parts) == 2 and parts[-1] == "test-pvs.sh" else 0o644


def _normalize_sdist(path: Path) -> None:
    """Rewrite an sdist with stable ordering, metadata and gzip headers."""

    entries: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(path, "r:gz") as source:
        for original in source.getmembers():
            if original.issym() or original.islnk():
                raise RuntimeError(f"source distribution contains a link: {original.name}")
            if not (original.isdir() or original.isfile()):
                raise RuntimeError(
                    f"source distribution contains an unsupported entry: {original.name}"
                )
            stream = source.extractfile(original) if original.isfile() else None
            content = stream.read() if stream is not None else None
            member = tarfile.TarInfo(original.name)
            member.type = tarfile.DIRTYPE if original.isdir() else tarfile.REGTYPE
            member.size = len(content) if content is not None else 0
            member.mode = _normalized_mode(original)
            member.mtime = _source_date_epoch()
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            entries.append((member, content))

    epoch = _source_date_epoch()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with (
            temporary.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed,
            tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as target,
        ):
            for member, content in sorted(entries, key=lambda item: item[0].name):
                target.addfile(member, io.BytesIO(content) if content is not None else None)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    filename = _setuptools.build_sdist(sdist_directory, config_settings)
    _normalize_sdist(Path(sdist_directory) / filename)
    return filename


build_wheel = _setuptools.build_wheel
build_editable = _setuptools.build_editable
get_requires_for_build_sdist = _setuptools.get_requires_for_build_sdist
get_requires_for_build_wheel = _setuptools.get_requires_for_build_wheel
get_requires_for_build_editable = _setuptools.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _setuptools.prepare_metadata_for_build_editable
