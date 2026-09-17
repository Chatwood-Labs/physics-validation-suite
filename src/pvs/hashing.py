"""Streaming file hashing and safe metadata."""

from __future__ import annotations

import hashlib
import os
import stat as stat_module
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .paths import stat_is_link_or_reparse


def _regular_path_metadata(path: Path) -> os.stat_result:
    """Inspect a path itself and reject links, reparse points and non-files."""

    metadata = os.stat(path, follow_symlinks=False)
    if stat_is_link_or_reparse(metadata):
        raise OSError(f"symbolic link or reparse point is not allowed: {path}")
    if not stat_module.S_ISREG(metadata.st_mode):
        raise OSError(f"not a regular file: {path}")
    return metadata


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    return str(file_identity(path, chunk_size=chunk_size)["sha256"])


def file_identity(path: Path, chunk_size: int = 1024 * 1024) -> dict[str, Any]:
    """Hash one opened regular file and bind size to those same bytes."""

    return _read_regular_file(path, chunk_size)


def acquire_file_bytes(
    path: Path, chunk_size: int = 1024 * 1024
) -> tuple[bytes, dict[str, Any]]:
    """Acquire immutable bytes and their identity from one stable regular-file read."""

    blocks: list[bytes] = []
    identity = _read_regular_file(path, chunk_size, blocks.append)
    return b"".join(blocks), identity


def _read_regular_file(
    path: Path,
    chunk_size: int,
    consume: Callable[[bytes], None] | None = None,
) -> dict[str, Any]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    path_before = _regular_path_metadata(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat_module.S_ISREG(before.st_mode):
            raise OSError(f"not a regular file: {path}")
        if not _same_file(path_before, before):
            raise OSError(f"file path changed while it was being opened: {path}")
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(descriptor, chunk_size)
            if not block:
                break
            digest.update(block)
            size += len(block)
            if consume is not None:
                consume(block)
        after = os.fstat(descriptor)
        path_after = _regular_path_metadata(path)
        stable = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
            and size == before.st_size
            and _same_file(before, path_after)
        )
        if not stable:
            raise OSError(f"file changed while it was being identified: {path}")
        return {"sha256": digest.hexdigest(), "size_bytes": size}
    finally:
        os.close(descriptor)


def snapshot_file(
    source: Path,
    destination: Path,
    chunk_size: int = 1024 * 1024,
    *,
    executable: bool = False,
) -> dict[str, Any]:
    """Copy one opened regular file and identify exactly the copied bytes."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    path_before = _regular_path_metadata(source)
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    source_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    destination_descriptor: int | None = None
    destination_created = False
    source_descriptor = os.open(source, source_flags)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        before = os.fstat(source_descriptor)
        if not stat_module.S_ISREG(before.st_mode):
            raise OSError(f"not a regular file: {source}")
        if executable and not (
            before.st_mode
            & (stat_module.S_IXUSR | stat_module.S_IXGRP | stat_module.S_IXOTH)
        ):
            raise OSError(f"file is not executable: {source}")
        if not _same_file(path_before, before):
            raise OSError(f"file path changed while it was being opened: {source}")
        destination_mode = 0o500 if executable else 0o600
        destination_descriptor = os.open(destination, destination_flags, destination_mode)
        destination_created = True
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(source_descriptor, chunk_size)
            if not block:
                break
            digest.update(block)
            size += len(block)
            offset = 0
            while offset < len(block):
                written = os.write(destination_descriptor, block[offset:])
                if written <= 0:  # pragma: no cover - defensive OS contract guard
                    raise OSError(f"short write while snapshotting: {source}")
                offset += written
        after = os.fstat(source_descriptor)
        path_after = _regular_path_metadata(source)
        stable = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
            and size == before.st_size
            and _same_file(before, path_after)
        )
        if not stable:
            raise OSError(f"file changed while it was being snapshotted: {source}")
        if executable:
            os.chmod(destination_descriptor, destination_mode)
        return {"sha256": digest.hexdigest(), "size_bytes": size}
    except BaseException:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
            destination_descriptor = None
        if destination_created:
            with suppress(OSError):
                destination.unlink(missing_ok=True)
        raise
    finally:
        try:
            if destination_descriptor is not None:
                os.close(destination_descriptor)
        finally:
            os.close(source_descriptor)
