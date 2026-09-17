from __future__ import annotations

import errno
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from conftest import base_case

import pvs.api as api_module
from pvs.api import run_case, validate_case
from pvs.artifacts import ResolvedArtifact
from pvs.canonical import canonical_sha256
from pvs.errors import CaseError, IntegrityError
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.models import Status
from pvs.verify import verify_target


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _refresh_manifest(package: Path) -> None:
    path = package / "manifest.json"
    manifest = load_strict(path)
    for entry in manifest["package"]["files"]:
        target = package / entry["path"]
        if target.is_file():
            entry.update(file_identity(target))
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{digest}"
    _write_json(path, manifest)


def _run_case_data(command: list[str]) -> dict:
    data = base_case()
    data["execution"] = {
        "command": command,
        "timeout_seconds": 5,
        "expected_exit_codes": [0],
    }
    data["package"] = {"embed_artifacts": True, "html": False, "pdf": False}
    return data


@pytest.mark.skipif(
    os.name != "posix", reason="PVS run mode requires POSIX process containment"
)
def test_run_transaction_creates_output_then_emits_self_verifying_package(
    case_factory, tmp_path: Path
) -> None:
    data = _run_case_data([sys.executable, "producer.py"])
    case_path, _ = case_factory(data)
    (case_path.parent / "result.json").unlink()
    (case_path.parent / "producer.py").write_text(
        "from pathlib import Path\n"
        "Path('result.json').write_text('{\"value\": 1.0}\\n', encoding='utf-8')\n"
        "print('subject completed')\n",
        encoding="utf-8",
    )

    outcome = run_case(case_path, output_dir=tmp_path / "run-package")
    envelope = load_strict(outcome.evidence_path)
    result_artifact = next(
        item for item in envelope["record"]["artifacts"] if item["id"] == "result"
    )

    assert outcome.status is Status.PASS
    assert result_artifact["pre_execution"] == {"exists": False}
    assert result_artifact["validation_input"]["exists"] is True
    assert envelope["record"]["execution"]["succeeded"] is True
    assert (outcome.output_directory / "logs/stdout.txt").read_text(encoding="utf-8") == (
        "subject completed\n"
    )
    assert verify_target(outcome.output_directory).valid is True


def test_preexisting_run_output_produces_error_evidence_that_still_verifies(
    case_factory, tmp_path: Path
) -> None:
    marker = tmp_path / "subject-must-not-run"
    data = _run_case_data(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    )
    case_path, _ = case_factory(data)

    outcome = run_case(case_path, output_dir=tmp_path / "preexisting-package")
    envelope = load_strict(outcome.evidence_path)

    assert outcome.status is Status.ERROR
    assert not marker.exists()
    assert envelope["record"]["execution"]["error"] == (
        "pre-execution provenance failed; subject was not executed"
    )
    assert (
        "output artifact already exists before execution: result"
        in envelope["record"]["summary"]["provenance_issues"]
    )
    assert verify_target(outcome.output_directory).valid is True


@pytest.mark.skipif(
    os.name != "posix", reason="PVS run mode requires POSIX process containment"
)
def test_nonzero_run_produces_error_evidence_that_still_verifies(
    case_factory, tmp_path: Path
) -> None:
    data = _run_case_data([sys.executable, "-c", "import sys; sys.exit(12)"])
    case_path, _ = case_factory(data)
    (case_path.parent / "result.json").unlink()

    outcome = run_case(case_path, output_dir=tmp_path / "nonzero-package")
    envelope = load_strict(outcome.evidence_path)

    assert outcome.status is Status.ERROR
    assert envelope["record"]["execution"]["return_code"] == 12
    assert envelope["record"]["execution"]["succeeded"] is False
    assert verify_target(outcome.output_directory).valid is True


def test_validate_mode_never_executes_declared_subject_command(
    case_factory, tmp_path: Path
) -> None:
    marker = tmp_path / "validate-must-not-execute"
    data = _run_case_data(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    )
    case_path, _ = case_factory(data)

    outcome = validate_case(case_path, output_dir=tmp_path / "validate-package")

    assert outcome.status is Status.PASS
    assert not marker.exists()
    assert verify_target(outcome.output_directory).valid is True


def test_checks_and_retained_artifacts_use_immutable_acquired_bytes(
    case_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = base_case()
    data["checks"] = [
        {
            "id": "acquired-value",
            "type": "compare",
            "actual": {"artifact": "result", "pointer": "/value", "unit": "1"},
            "expected": {"value": 1.0, "unit": "1"},
            "unit": "1",
            "metric": "absolute",
            "tolerance": 0.0,
        }
    ]
    data["package"]["embed_artifacts"] = True
    case_path, _ = case_factory(data, result_text='{"value": 1.0}\n')
    live_artifact = case_path.parent / "result.json"
    replacement = case_path.parent / "replacement.json"
    replacement.write_text('{"value": 999.0}\n', encoding="utf-8")
    original_acquire = api_module.acquire_validation_snapshot

    def acquire_then_swap_live_path(
        destination: Path,
        artifacts: dict[str, ResolvedArtifact],
    ) -> tuple[dict[str, ResolvedArtifact], dict[str, dict[str, Any]]]:
        acquired = original_acquire(destination, artifacts)
        replacement.replace(live_artifact)
        return acquired

    monkeypatch.setattr(api_module, "acquire_validation_snapshot", acquire_then_swap_live_path)

    outcome = validate_case(case_path, output_dir=tmp_path / "immutable-input-package")
    envelope = load_strict(outcome.evidence_path)
    check = envelope["record"]["checks"][0]
    artifact = envelope["record"]["artifacts"][0]
    retained = outcome.output_directory / artifact["package_path"]

    assert check["status"] == "PASS"
    assert check["observed"]["actual"] == 1.0
    assert load_strict(retained)["value"] == 1.0
    assert load_strict(live_artifact)["value"] == 999.0
    assert artifact["validation_input"]["sha256"] != artifact["post_validation"]["sha256"]
    assert (
        "artifact changed while checks were evaluated: result"
        in envelope["record"]["summary"]["provenance_issues"]
    )
    assert outcome.status is Status.ERROR
    assert verify_target(outcome.output_directory).valid is True


def test_relative_ratio_overflow_emits_verifiable_error_evidence(
    case_factory,
    tmp_path: Path,
) -> None:
    data = base_case()
    data["checks"] = [
        {
            "id": "ratio-overflow",
            "type": "compare",
            "actual": {"artifact": "result", "pointer": "/actual", "unit": "1"},
            "expected": {"artifact": "result", "pointer": "/expected", "unit": "1"},
            "unit": "1",
            "metric": "relative",
            "tolerance": 1.0,
            "scale_floor": 1e-308,
        }
    ]
    case_path, _ = case_factory(
        data,
        result_text='{"actual": 10.0, "expected": 1e-308}\n',
    )

    outcome = validate_case(case_path, output_dir=tmp_path / "ratio-overflow-package")
    envelope = load_strict(outcome.evidence_path)
    check = envelope["record"]["checks"][0]

    assert outcome.status is Status.ERROR
    assert check["status"] == "ERROR"
    assert check["summary"] == "comparison ratio overflowed the finite numeric range"
    assert check["observed"] == {}
    assert check["criterion"] == {}
    assert verify_target(outcome.output_directory).valid is True


def test_atomic_publish_does_not_replace_a_concurrently_created_target(
    case_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_path, _ = case_factory()
    target = tmp_path / "publication-race-package"
    original_publish = api_module._publish_no_replace

    def create_collision_then_publish(source: Path, destination: Path) -> None:
        if destination == target:
            destination.mkdir()
            (destination / "concurrent-owner.txt").write_text("owned\n", encoding="utf-8")
        original_publish(source, destination)

    monkeypatch.setattr(api_module, "_publish_no_replace", create_collision_then_publish)

    with pytest.raises(CaseError, match="output directory already exists"):
        validate_case(case_path, output_dir=target)

    assert (target / "concurrent-owner.txt").read_text(encoding="utf-8") == "owned\n"
    assert not (target / "evidence.json").exists()
    assert not list(tmp_path.glob(f".{target.name}.pvs-tmp-*"))
    assert not list(tmp_path.glob(f".{target.name}.pvs-validation-*"))


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") or sys.platform == "darwin" or os.name == "nt"),
    reason="native no-replace publication is unsupported on this platform",
)
def test_native_atomic_publish_has_exactly_one_winner_under_a_real_race(
    tmp_path: Path,
) -> None:
    sources = [tmp_path / "contender-a", tmp_path / "contender-b"]
    target = tmp_path / "race-winner"
    for index, source in enumerate(sources):
        source.mkdir()
        (source / "owner.txt").write_text(f"contender-{index}\n", encoding="utf-8")
    ready = Barrier(len(sources))

    def contend(source: Path) -> tuple[Path, str]:
        ready.wait(timeout=5)
        try:
            api_module._publish_no_replace(source, target)
        except CaseError:
            return source, "collision"
        return source, "published"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contend, sources))

    winners = [source for source, status in results if status == "published"]
    losers = [source for source, status in results if status == "collision"]
    assert len(winners) == 1
    assert len(losers) == 1
    assert not winners[0].exists()
    assert losers[0].is_dir()
    winner_identity = (target / "owner.txt").read_text(encoding="utf-8")
    loser_identity = (losers[0] / "owner.txt").read_text(encoding="utf-8")
    assert {winner_identity, loser_identity} == {"contender-0\n", "contender-1\n"}


def test_linux_missing_renameat2_symbol_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LibcWithoutRenameat2:
        pass

    monkeypatch.setattr(api_module.sys, "platform", "linux")
    monkeypatch.setattr(
        api_module.ctypes,
        "CDLL",
        lambda *args, **kwargs: LibcWithoutRenameat2(),
    )

    with pytest.raises(IntegrityError, match="does not expose renameat2"):
        api_module._publish_no_replace(tmp_path / "source", tmp_path / "target")


@pytest.mark.parametrize(
    ("error_number", "expected", "error_label"),
    [
        (errno.ENOSYS, "not implemented by the running kernel", "ENOSYS"),
        (errno.EINVAL, "kernel or target filesystem", "EINVAL"),
        (errno.EOPNOTSUPP, "target filesystem does not support", "EOPNOTSUPP"),
    ],
)
def test_atomic_publication_unsupported_errors_are_classified(
    tmp_path: Path,
    error_number: int,
    expected: str,
    error_label: str,
) -> None:
    with pytest.raises(IntegrityError, match=expected) as failure:
        api_module._publication_os_error(
            "renameat2(RENAME_NOREPLACE)",
            tmp_path / "target",
            error_number,
        )

    assert error_label in str(failure.value)


def test_macos_renamex_np_uses_rename_excl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    class FakeRename:
        argtypes: list[Any] | None = None
        restype: Any = None

        def __call__(self, *args: Any) -> int:
            calls.append(args)
            return 0

    class FakeLibc:
        renamex_np = FakeRename()

    monkeypatch.setattr(api_module.sys, "platform", "darwin")
    monkeypatch.setattr(api_module.ctypes, "CDLL", lambda *args, **kwargs: FakeLibc())

    source = tmp_path / "source"
    target = tmp_path / "target"
    api_module._publish_no_replace(source, target)

    assert calls == [(os.fsencode(source), os.fsencode(target), 0x00000004)]


def test_atomic_publication_preflight_cleans_probes_after_capability_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported(source: Path, target: Path) -> None:
        raise IntegrityError("simulated unsupported filesystem")

    monkeypatch.setattr(api_module, "_publish_no_replace", unsupported)

    with pytest.raises(IntegrityError, match="simulated unsupported filesystem"):
        api_module._preflight_atomic_publication(tmp_path)

    assert not list(tmp_path.glob(".pvs-publish-probe-*"))


def test_atomic_publication_preflight_cleans_partial_probe_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_mkdtemp = api_module.tempfile.mkdtemp
    calls = 0

    def fail_second_probe(*args: Any, **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.ENOSPC, "simulated full filesystem")
        return original_mkdtemp(*args, **kwargs)

    monkeypatch.setattr(api_module.tempfile, "mkdtemp", fail_second_probe)

    with pytest.raises(IntegrityError, match="could not create or inspect"):
        api_module._preflight_atomic_publication(tmp_path)

    assert not list(tmp_path.glob(".pvs-publish-probe-*"))


@pytest.mark.skipif(
    os.name != "posix", reason="PVS run mode requires POSIX process containment"
)
def test_publication_capability_failure_occurs_before_subject_execution(
    case_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "subject-must-not-run"
    data = _run_case_data(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    )
    case_path, _ = case_factory(data)
    (case_path.parent / "result.json").unlink()
    target = tmp_path / "unsupported-publication-package"

    def fail_preflight(parent: Path) -> None:
        assert parent == target.parent
        assert not marker.exists()
        raise IntegrityError("publication preflight rejected target filesystem")

    monkeypatch.setattr(api_module, "_preflight_atomic_publication", fail_preflight)

    with pytest.raises(IntegrityError, match="publication preflight rejected"):
        run_case(case_path, output_dir=target)

    assert not marker.exists()
    assert not target.exists()
    assert not list(tmp_path.glob(f".{target.name}.pvs-tmp-*"))
    assert not list(tmp_path.glob(f".{target.name}.pvs-validation-*"))


@pytest.mark.skipif(
    os.name != "posix", reason="PVS run mode requires POSIX process containment"
)
def test_rehashed_manifest_cannot_hide_execution_log_tampering(
    case_factory, tmp_path: Path
) -> None:
    data = _run_case_data([sys.executable, "-c", "print('original log')"])
    case_path, _ = case_factory(data)
    (case_path.parent / "result.json").unlink()
    # The command deliberately does not create the required output, producing valid ERROR evidence.
    outcome = run_case(case_path, output_dir=tmp_path / "log-package")
    stdout = outcome.output_directory / "logs/stdout.txt"
    stdout.write_text("forged log\n", encoding="utf-8")
    _refresh_manifest(outcome.output_directory)

    result = verify_target(outcome.output_directory)

    assert result.valid is False
    assert any(
        check["status"] == "FAIL" and "stdout" in check["name"].lower() for check in result.checks
    )
