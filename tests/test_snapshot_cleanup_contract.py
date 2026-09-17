"""Failed snapshot acquisitions release every owned descriptor and preserve inputs."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case, dump_case

import pvs.artifacts as artifacts_module
import pvs.hashing as hashing
from pvs.api import validate_case
from pvs.jsonutil import load_strict
from pvs.models import Status
from pvs.verify import verify_target


@pytest.mark.parametrize(
    "failure", ["mkdir-file", "mkdir-permission", "destination-exists", "write"]
)
def test_repeated_snapshot_failures_close_all_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = tmp_path / "source.bin"
    payload = b"exact\r\nsource\x1a\x00bytes"
    source.write_bytes(payload)
    parent = tmp_path / "destination"
    destination = parent / "snapshot.bin"
    if failure == "mkdir-file":
        parent.write_bytes(b"blocker")
    elif failure == "destination-exists":
        parent.mkdir()
        destination.write_bytes(b"existing owner")
    real_open, real_close, real_mkdir = os.open, os.close, Path.mkdir
    opened: list[int] = []
    closed: list[int] = []

    def tracked_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        if Path(path) in {source, destination}:
            opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor: int) -> None:
        real_close(descriptor)
        closed.append(descriptor)

    def fail_mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == parent:
            raise PermissionError("injected directory denial")
        real_mkdir(path, *args, **kwargs)

    def fail_write(*args: Any, **kwargs: Any) -> int:
        raise OSError("injected snapshot write failure")

    monkeypatch.setattr(hashing.os, "open", tracked_open)
    monkeypatch.setattr(hashing.os, "close", tracked_close)
    if failure == "mkdir-permission":
        monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    elif failure == "write":
        monkeypatch.setattr(hashing.os, "write", fail_write)

    for _ in range(5):
        opened.clear()
        closed.clear()
        try:
            with pytest.raises(OSError):
                hashing.snapshot_file(source, destination, chunk_size=3)
            assert opened
            assert Counter(opened) == Counter(closed)
            for descriptor in opened:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
        finally:
            for descriptor in set(opened) - set(closed):
                real_close(descriptor)
        assert source.read_bytes() == payload
        if failure == "mkdir-file":
            assert parent.read_bytes() == b"blocker"
        elif failure == "destination-exists":
            assert destination.read_bytes() == b"existing owner"
        else:
            assert not destination.exists()


@pytest.mark.parametrize("required", [True, False], ids=["required", "optional"])
def test_snapshot_mkdir_failure_publishes_verified_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required: bool
) -> None:
    case = base_case()
    case["checks"] = [{
        "id": "finite", "type": "finite", "required": required, "unit": "1",
        "source": {"artifact": "result", "unit": "1"},
    }]
    path = dump_case(tmp_path / "case/pvs.yaml", case)
    source = path.parent / "result.json"
    source.write_text("[1.0]\n", encoding="utf-8")
    real_mkdir, real_open, real_close = Path.mkdir, os.open, os.close
    snapshot_active = False
    opened: list[int] = []
    closed: list[int] = []

    def snapshot_with_failure(*args: Any, **kwargs: Any) -> Any:
        nonlocal snapshot_active
        snapshot_active = True
        try:
            return hashing.snapshot_file(*args, **kwargs)
        finally:
            snapshot_active = False

    def fail_snapshot_mkdir(directory: Path, *args: Any, **kwargs: Any) -> None:
        if snapshot_active:
            raise PermissionError("injected snapshot directory denial")
        real_mkdir(directory, *args, **kwargs)

    def track_open(target: Any, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(target, *args, **kwargs)
        if snapshot_active:
            opened.append(descriptor)
        return descriptor

    def track_close(descriptor: int) -> None:
        real_close(descriptor)
        if snapshot_active:
            closed.append(descriptor)

    monkeypatch.setattr(artifacts_module, "snapshot_file", snapshot_with_failure)
    monkeypatch.setattr(Path, "mkdir", fail_snapshot_mkdir)
    monkeypatch.setattr(hashing.os, "open", track_open)
    monkeypatch.setattr(hashing.os, "close", track_close)
    try:
        outcome = validate_case(path, output_dir=tmp_path / "evidence")
        assert opened and Counter(opened) == Counter(closed)
    finally:
        for descriptor in set(opened) - set(closed):
            real_close(descriptor)
    assert outcome.status is Status.ERROR
    assert outcome.finding_id is None
    record = load_strict(outcome.evidence_path)["record"]
    assert record["summary"]["provenance_status"] == "INCOMPLETE"
    assert record["checks"][0]["status"] == "ERROR"
    identity = record["artifacts"][0]["validation_input"]
    assert "snapshot directory denial" in identity["resolution_error"]
    assert verify_target(outcome.output_directory).valid
