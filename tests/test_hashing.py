from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import pvs.hashing
from pvs.hashing import file_identity, sha256_file, snapshot_file


def test_file_identity_hashes_exact_bytes_and_size_with_streaming_chunks(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 17 + b"tail"
    path = tmp_path / "artifact.bin"
    path.write_bytes(payload)

    identity = file_identity(path, chunk_size=31)

    assert identity == {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    assert sha256_file(path, chunk_size=29) == identity["sha256"]


def test_file_identity_supports_empty_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.touch()

    assert file_identity(path) == {
        "sha256": hashlib.sha256(b"").hexdigest(),
        "size_bytes": 0,
    }


def test_raw_descriptors_request_binary_mode_for_exact_windows_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every raw descriptor must opt out of Windows CRT text translation."""

    payload = b"line-one\r\nline-two\nembedded-sub:\x1a\x00tail"
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    destination = tmp_path / "snapshot.bin"
    original_open = os.open
    native_binary_flag = getattr(os, "O_BINARY", 0)
    synthetic_binary_flag = 1 << 29
    observed_flags: list[int] = []

    def recording_open(path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        observed_flags.append(flags)
        assert flags & synthetic_binary_flag
        native_flags = (flags & ~synthetic_binary_flag) | native_binary_flag
        return original_open(path, native_flags, mode)

    monkeypatch.setattr(pvs.hashing.os, "O_BINARY", synthetic_binary_flag, raising=False)
    monkeypatch.setattr(pvs.hashing.os, "open", recording_open)

    expected = {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}
    assert file_identity(source, chunk_size=7) == expected
    assert snapshot_file(source, destination, chunk_size=5) == expected
    assert destination.read_bytes() == payload
    assert len(observed_flags) == 3


def test_file_identity_rejects_symlink_without_following_it(
    tmp_path: Path, symlink_factory
) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"secret target bytes")
    link = tmp_path / "link.bin"
    symlink_factory(link, target)

    with pytest.raises(OSError):
        file_identity(link)


def test_file_identity_rejects_directory_as_non_regular(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="not a regular file"):
        file_identity(tmp_path)


def test_file_identity_rejects_windows_reparse_metadata_without_link_privilege(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ordinary-file.bin"
    path.write_bytes(b"ordinary bytes")
    real_stat = os.stat
    metadata = real_stat(path, follow_symlinks=False)
    reparse_flag = getattr(pvs.hashing.stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    class ReparseMetadata:
        st_mode = metadata.st_mode
        st_file_attributes = reparse_flag

    monkeypatch.setattr(pvs.hashing.os, "stat", lambda *_args, **_kwargs: ReparseMetadata())

    with pytest.raises(OSError, match="reparse point"):
        file_identity(path)


def test_file_identity_detects_mutation_during_streaming_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "changing.bin"
    path.write_bytes(b"a" * 4096)
    original_read = pvs.hashing.os.read
    mutated = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        block = original_read(descriptor, size)
        if block and not mutated:
            mutated = True
            with path.open("ab") as stream:
                stream.write(b"changed")
        return block

    monkeypatch.setattr(pvs.hashing.os, "read", mutating_read)

    with pytest.raises(OSError, match="file changed while it was being identified"):
        file_identity(path, chunk_size=64)

    assert mutated is True


def test_snapshot_file_never_removes_or_overwrites_a_preexisting_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"new snapshot bytes")
    destination = tmp_path / "destination.bin"
    original_destination = b"pre-existing owner bytes"
    destination.write_bytes(original_destination)

    with pytest.raises(FileExistsError):
        snapshot_file(source, destination)

    assert destination.read_bytes() == original_destination


def test_snapshot_file_detects_source_mutation_and_removes_only_its_partial_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "changing-source.bin"
    source.write_bytes(b"a" * 4096)
    destination = tmp_path / "snapshot.bin"
    original_read = pvs.hashing.os.read
    mutated = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        block = original_read(descriptor, size)
        if block and not mutated:
            mutated = True
            with source.open("ab") as stream:
                stream.write(b"changed")
        return block

    monkeypatch.setattr(pvs.hashing.os, "read", mutating_read)

    with pytest.raises(OSError, match="file changed while it was being snapshotted"):
        snapshot_file(source, destination, chunk_size=64)

    assert mutated is True
    assert not destination.exists()


@pytest.mark.skipif(os.name != "posix", reason="executable modes are a POSIX contract")
def test_executable_snapshot_is_private_and_refuses_non_executable_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "subject"
    source.write_bytes(b"#!/bin/sh\nexit 0\n")
    source.chmod(0o755)
    snapshot = tmp_path / "snapshot"

    identity = snapshot_file(source, snapshot, executable=True)

    assert identity == file_identity(source)
    assert snapshot.read_bytes() == source.read_bytes()
    assert snapshot.stat().st_mode & 0o777 == 0o500

    non_executable = tmp_path / "not-executable"
    non_executable.write_bytes(b"#!/bin/sh\nexit 1\n")
    non_executable.chmod(0o600)
    rejected_snapshot = tmp_path / "rejected-snapshot"

    with pytest.raises(OSError, match="file is not executable"):
        snapshot_file(non_executable, rejected_snapshot, executable=True)

    assert not rejected_snapshot.exists()
