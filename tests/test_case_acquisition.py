"""Case parsing, identity and retention share one acquired byte sequence."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest
from conftest import base_case

import pvs.api as api
import pvs.case as case_module
import pvs.hashing as hashing
from pvs.case import load_case
from pvs.cli import main
from pvs.errors import CaseError, IntegrityError
from pvs.jsonutil import load_strict
from pvs.verify import verify_target


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_case_preserves_exact_utf8_bytes(case_factory, newline, bom):
    data = base_case()
    data["subject"]["name"] = "temperature-\u03b8"
    path, _ = case_factory(data)
    raw = bom + path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
    path.write_bytes(raw)
    loaded = load_case(path)
    assert loaded.raw_bytes == raw
    assert loaded.raw_identity == {
        "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)
    }
    assert loaded.data["subject"] == data["subject"]


@pytest.mark.parametrize("operation", ["load", "inspect"])
def test_edit_after_acquisition_never_changes_the_parsed_identity(
    case_factory, monkeypatch, capsys, operation
):
    path, _ = case_factory()
    original = path.read_bytes()
    acquire = case_module.acquire_file_bytes
    calls = []

    def acquire_then_edit(source):
        result = acquire(source)
        calls.append(source)
        source.write_bytes(original.replace(b"synthetic-subject", b"different-subject"))
        return result

    monkeypatch.setattr(case_module, "acquire_file_bytes", acquire_then_edit)
    if operation == "load":
        loaded = load_case(path)
        subject, digest = loaded.data["subject"], loaded.raw_identity["sha256"]
        assert loaded.raw_bytes == original
    else:
        assert main(["inspect", str(path), "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        subject, digest = result["subject"], result["raw_sha256"]
    assert calls == [path.resolve()]
    assert path.read_bytes() != original  # The race really ran on the new boundary.
    assert subject["name"] == "synthetic-subject"
    assert digest == hashlib.sha256(original).hexdigest()


def test_edit_during_acquisition_is_a_case_error(case_factory, monkeypatch):
    path, _ = case_factory()
    read = hashing.os.read
    mutated = False

    def read_then_edit(descriptor, size):
        nonlocal mutated
        block = read(descriptor, size)
        if block and not mutated:
            mutated = True
            with path.open("ab") as stream:
                stream.write(b"\n# concurrent edit\n")
        return block

    monkeypatch.setattr(hashing.os, "read", read_then_edit)
    with pytest.raises(CaseError, match="file changed"):
        load_case(path)
    assert mutated


@pytest.mark.parametrize("restore_live_case", [False, True])
def test_transaction_retains_the_acquired_case_after_a_live_edit(
    case_factory, tmp_path, monkeypatch, restore_live_case
):
    path, _ = case_factory()
    original = path.read_bytes()
    acquire = api.load_case
    evaluate = api.evaluate_checks
    retained = []

    def load_then_edit(source):
        case = acquire(source)
        path.write_bytes(original.replace(b"synthetic-subject", b"different-subject"))
        return case

    def evaluate_after_retention(*args, **kwargs):
        copies = list(tmp_path.glob(".retained.pvs-tmp-*/case/pvs.yaml"))
        assert len(copies) == 1
        retained.append(copies[0].read_bytes())
        if restore_live_case:
            path.write_bytes(original)
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(api, "load_case", load_then_edit)
    monkeypatch.setattr(api, "evaluate_checks", evaluate_after_retention)
    outcome = api.validate_case(path, output_dir=tmp_path / "retained")
    assert retained == [original]
    assert (outcome.output_directory / "case/pvs.yaml").read_bytes() == original
    evidence = load_strict(outcome.evidence_path)
    assert evidence["record"]["subject"]["name"] == "synthetic-subject"
    assert evidence["record"]["summary"]["status"] == (
        "PASS" if restore_live_case else "ERROR"
    )
    assert verify_target(outcome.output_directory).valid
    if not restore_live_case:
        assert outcome.finding_id is None


@pytest.mark.skipif(os.name != "posix", reason="PVS subject execution is POSIX-only")
def test_retained_case_is_checked_before_subject_execution(case_factory, tmp_path, monkeypatch):
    data = base_case()
    data["artifacts"]["result"]["role"] = "input"
    data["execution"] = {"command": [sys.executable, "-c", "pass"]}
    path, _ = case_factory(data)
    real_identity = api.file_identity
    ran = False

    def altered_copy_identity(candidate, *args, **kwargs):
        if candidate.name == "pvs.yaml" and ".blocked.pvs-tmp-" in str(candidate):
            candidate.write_bytes(candidate.read_bytes() + b"\n# corrupted retention\n")
        return real_identity(candidate, *args, **kwargs)

    def must_not_run(*args, **kwargs):
        nonlocal ran
        ran = True
        pytest.fail("subject executed before retained case was checked")

    monkeypatch.setattr(api, "file_identity", altered_copy_identity)
    monkeypatch.setattr(api, "run_command", must_not_run)
    with pytest.raises(IntegrityError, match="retained case bytes differ"):
        api.run_case(path, output_dir=tmp_path / "blocked")
    assert not ran
    assert not list(tmp_path.glob(".blocked.pvs-*"))
    assert not (tmp_path / "blocked").exists()


@pytest.mark.parametrize("operation", [hashing.acquire_file_bytes, hashing.file_identity,
                                      hashing.snapshot_file])
@pytest.mark.parametrize("chunk_size", [0, -1])
def test_acquisition_rejects_nonpositive_chunks_before_opening(
    tmp_path, operation, chunk_size
):
    source, target = tmp_path / "not-opened", tmp_path / "not-created"
    args = (source, target) if operation is hashing.snapshot_file else (source,)
    with pytest.raises(ValueError, match="chunk_size must be positive"):
        operation(*args, chunk_size=chunk_size)
    assert not target.exists()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("operation", [hashing.acquire_file_bytes, hashing.snapshot_file])
def test_interrupted_acquisition_closes_descriptors_and_removes_its_partial_copy(
    tmp_path, monkeypatch, failure, operation
):
    source, target = tmp_path / "source.bin", tmp_path / "copy.bin"
    source.write_bytes(b"bytes")
    opened, closed = [], []
    original_open, original_close = os.open, os.close
    error = failure("acquisition interrupted")

    def track_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def track_close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)

    def interrupt(*args):
        raise error

    monkeypatch.setattr(hashing.os, "open", track_open)
    monkeypatch.setattr(hashing.os, "close", track_close)
    monkeypatch.setattr(hashing.os, "read", interrupt)
    args = (source, target) if operation is hashing.snapshot_file else (source,)
    with pytest.raises(failure) as caught:
        operation(*args)
    assert caught.value is error
    assert len(opened) == (2 if operation is hashing.snapshot_file else 1)
    assert sorted(closed) == sorted(opened)
    assert not target.exists()
    assert source.read_bytes() == b"bytes"


def test_acquired_bytes_use_binary_descriptors_and_streaming_chunks(tmp_path, monkeypatch):
    source = tmp_path / "binary.bin"
    payload = bytes(range(256)) * 3 + b"\r\n\x1a\x00"
    source.write_bytes(payload)
    original_open = os.open
    synthetic_flag, native_flag = 1 << 29, getattr(os, "O_BINARY", 0)

    def open_binary(path, flags, *args):
        assert flags & synthetic_flag
        return original_open(path, (flags & ~synthetic_flag) | native_flag, *args)

    monkeypatch.setattr(hashing.os, "O_BINARY", synthetic_flag, raising=False)
    monkeypatch.setattr(hashing.os, "open", open_binary)
    raw, identity = hashing.acquire_file_bytes(source, chunk_size=7)
    assert raw == payload
    assert identity == {"sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload)}


def test_acquisition_rejects_links(tmp_path, symlink_factory):
    source, link = tmp_path / "source", tmp_path / "link"
    source.write_bytes(b"data")
    symlink_factory(link, source)
    with pytest.raises(OSError, match=r"link|reparse"):
        hashing.acquire_file_bytes(link)
