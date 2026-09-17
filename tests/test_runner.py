from __future__ import annotations

import json
import os
import select
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

import pvs.runner as runner_module
from pvs.api import run_case
from pvs.canonical import canonical_sha256
from pvs.case import load_case
from pvs.errors import ExecutionError
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict, write_pretty
from pvs.models import Status
from pvs.runner import run_command
from pvs.verify import verify_target

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="PVS run mode requires POSIX process containment"
)


def _runner_case(case_factory, *, command: list[str], **execution_options):
    from conftest import base_case

    data = base_case()
    data["execution"] = {"command": command, **execution_options}
    return case_factory(data)


def test_runner_success_captures_logs_environment_and_executable_identity(
    case_factory, tmp_path: Path
) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=[sys.executable, "subject.py"],
        environment={"PVS_TEST_VALUE": "declared-value"},
        timeout_seconds=5,
        expected_exit_codes=[0],
    )
    (case_path.parent / "subject.py").write_text(
        "import os, sys\n"
        "print(os.environ['PVS_TEST_VALUE'])\n"
        "print('diagnostic', file=sys.stderr)\n",
        encoding="utf-8",
    )

    result, executable = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is True
    assert result.error is None
    assert result.return_code == 0
    assert result.command == [sys.executable, "subject.py"]
    assert result.working_directory == "."
    assert result.started_at.endswith("Z")
    assert result.finished_at.endswith("Z")
    assert result.duration_seconds >= 0
    assert result.stdout_path.read_text(encoding="utf-8") == "declared-value\n"
    assert result.stderr_path.read_text(encoding="utf-8") == "diagnostic\n"
    assert executable is not None
    assert executable["resolved"] is True
    assert len(executable["sha256"]) == 64
    assert executable["size_bytes"] > 0
    assert executable["post_execution_sha256"] == executable["sha256"]
    assert executable["post_execution_size_bytes"] == executable["size_bytes"]
    assert executable["unchanged_during_execution"] is True
    assert executable["launch_profile"] == "pvs-private-executable-snapshot/1"
    assert not list((tmp_path / "logs").glob(".pvs-executable-*"))


@pytest.mark.skipif(os.name != "posix", reason="executable fixture uses a POSIX shebang")
@pytest.mark.parametrize(
    ("declared_path", "executable_relative_path"),
    [("../bin", "bin/pvs-path-probe"), ("", "work/pvs-path-probe")],
    ids=["relative-path-entry", "empty-path-entry-means-cwd"],
)
def test_declared_path_resolves_and_evidence_binds_the_exact_executable(
    case_factory,
    tmp_path: Path,
    declared_path: str,
    executable_relative_path: str,
) -> None:
    from conftest import base_case

    data = base_case()
    data["artifacts"]["result"]["path"] = "work/result.json"
    data["execution"] = {
        "command": ["pvs-path-probe"],
        "working_directory": "work",
        "environment": {"PATH": declared_path},
    }
    data["package"]["embed_artifacts"] = True
    case_path, _ = case_factory(data)
    case_root = case_path.parent
    (case_root / "work").mkdir()
    executable_path = case_root / executable_relative_path
    executable_path.parent.mkdir(parents=True, exist_ok=True)
    executable_path.write_text(
        "#!/bin/sh\nprintf '%s\\n' '{\"value\": 1.0}' > result.json\n",
        encoding="utf-8",
    )
    executable_path.chmod(0o755)
    expected_identity = file_identity(executable_path)

    outcome = run_case(case_path, output_dir=tmp_path / f"package-{declared_path or 'empty'}")
    envelope = load_strict(outcome.evidence_path)
    execution = envelope["record"]["execution"]
    executable = execution["resolved_executable"]

    assert outcome.status is Status.PASS
    assert execution["command"] == ["pvs-path-probe"]
    assert execution["declared_environment"] == {"PATH": declared_path}
    assert executable["requested"] == "pvs-path-probe"
    assert executable["resolved"] is True
    assert executable["case_relative_path"] == executable_relative_path
    assert executable["sha256"] == expected_identity["sha256"]
    assert executable["size_bytes"] == expected_identity["size_bytes"]
    assert executable["post_execution_sha256"] == expected_identity["sha256"]
    assert executable["post_execution_size_bytes"] == expected_identity["size_bytes"]
    assert executable["unchanged_during_execution"] is True
    assert verify_target(outcome.output_directory).valid is True


def test_runner_launches_acquired_bytes_when_resolved_path_is_replaced_before_exec(
    case_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=["./subject"],
        timeout_seconds=5,
        expected_exit_codes=[0],
    )
    executable_path = case_path.parent / "subject"
    replacement = case_path.parent / "replacement"
    (case_path.parent / "result.json").unlink()
    executable_path.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"value\": 1.0}' > result.json\n"
        "printf 'acquired-original\\n'\n",
        encoding="utf-8",
    )
    replacement.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"value\": 999.0}' > result.json\n"
        "printf 'replacement\\n'\n",
        encoding="utf-8",
    )
    executable_path.chmod(0o755)
    replacement.chmod(0o755)
    acquired_identity = file_identity(executable_path)
    replacement_identity = file_identity(replacement)
    original_popen = runner_module.subprocess.Popen
    launched_path: Path | None = None

    def replace_resolved_path_then_launch(*args, **kwargs):
        nonlocal launched_path
        launched_path = Path(str(kwargs["executable"]))
        assert launched_path != executable_path
        assert launched_path.is_file()
        replacement.replace(executable_path)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(runner_module.subprocess, "Popen", replace_resolved_path_then_launch)

    outcome = run_case(case_path, output_dir=tmp_path / "acquired-executable-package")
    envelope = load_strict(outcome.evidence_path)
    execution = envelope["record"]["execution"]
    executable = execution["resolved_executable"]

    assert outcome.status is Status.PASS
    assert execution["succeeded"] is True
    assert execution["command"] == ["./subject"]
    assert (outcome.output_directory / "logs/stdout.txt").read_text(encoding="utf-8") == (
        "acquired-original\n"
    )
    assert executable is not None
    assert executable["requested"] == "./subject"
    assert executable["case_relative_path"] == "subject"
    assert executable["sha256"] == acquired_identity["sha256"]
    assert executable["size_bytes"] == acquired_identity["size_bytes"]
    assert executable["post_execution_sha256"] == acquired_identity["sha256"]
    assert executable["post_execution_size_bytes"] == acquired_identity["size_bytes"]
    assert executable["unchanged_during_execution"] is True
    assert file_identity(executable_path) == replacement_identity
    assert launched_path is not None and not launched_path.exists()
    assert all(".pvs-executable-" not in str(value) for value in executable.values())
    assert ".pvs-executable-" not in outcome.evidence_path.read_text(encoding="utf-8")
    assert not list((outcome.output_directory / "logs").glob(".pvs-executable-*"))
    assert verify_target(outcome.output_directory).valid is True


def test_verifier_preserves_valid_v1_run_package_compatibility(
    case_factory,
    tmp_path: Path,
) -> None:
    """The v0.2 launch profile must not be imposed on frozen v0.1 evidence."""

    from conftest import base_case, dump_case

    case = base_case()
    case["execution"] = {
        "command": [
            str(Path(sys.executable).resolve()),
            "-c",
            "from pathlib import Path; "
            "Path('result.json').write_text('{\"value\": 1.0}\\n')",
        ]
    }
    case_path, _ = case_factory(case)
    (case_path.parent / "result.json").unlink()
    package = tmp_path / "legacy-run-package"
    run_case(case_path, output_dir=package)

    legacy_case = base_case()
    legacy_case["schema"] = "pvs-case/1"
    legacy_case["execution"] = case["execution"]
    retained_case = dump_case(package / "case" / "pvs.yaml", legacy_case)
    retained_identity = file_identity(retained_case)

    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["schema"] = "pvs-evidence/1"
    record = envelope["record"]
    record.pop("package")
    record["pvs"] = {
        "name": record["pvs"]["name"],
        "version": record["pvs"]["version"],
        "implementation": record["pvs"]["implementation"],
        "case_schema": "pvs-case/1",
        "evidence_schema": "pvs-evidence/1",
        "integrity_profile": record["pvs"]["integrity_profile"],
    }
    record["case"]["raw_sha256"] = retained_identity["sha256"]
    record["case"]["raw_size_bytes"] = retained_identity["size_bytes"]
    record["case"]["resolved_definition"] = legacy_case
    record["case"]["semantic_sha256"] = canonical_sha256(legacy_case)
    resolved = record["execution"]["resolved_executable"]
    assert isinstance(resolved, dict)
    resolved.pop("launch_profile")
    digest = canonical_sha256(record)
    envelope["integrity"] = {
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "digest": digest,
        "evidence_id": f"pvs:sha256:{digest}",
    }
    write_pretty(evidence_path, envelope)

    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["schema"] = "pvs-manifest/1"
    manifest_package = manifest["package"]
    manifest_package["evidence_id"] = envelope["integrity"]["evidence_id"]
    manifest_package.pop("policy")
    manifest_package.pop("report_profiles")
    for entry in manifest_package["files"]:
        entry.update(file_identity(package / entry["path"]))
    manifest_digest = canonical_sha256(manifest_package)
    manifest["integrity"]["digest"] = manifest_digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{manifest_digest}"
    write_pretty(manifest_path, manifest)

    verification = verify_target(package)

    assert verification.valid is True
    assert verification.evidence_id == envelope["integrity"]["evidence_id"]
    assert all(check["status"] == "PASS" for check in verification.checks)


@pytest.mark.skipif(os.name != "posix", reason="PATH lookup semantics differ on Windows")
def test_empty_declared_path_does_not_fall_back_to_ambient_path(
    case_factory,
    tmp_path: Path,
) -> None:
    executable_name = Path(sys.executable).name
    case_path, _ = _runner_case(
        case_factory,
        command=[executable_name, "-c", "raise SystemExit('must not run')"],
        environment={"PATH": ""},
    )

    result, executable = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is False
    assert result.error == f"executable not found: {executable_name}"
    assert executable == {"requested": executable_name, "resolved": False}


def test_runner_honours_declared_working_directory(case_factory, tmp_path: Path) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=[sys.executable, "subject.py"],
        working_directory="work",
    )
    work = case_path.parent / "work"
    work.mkdir()
    (work / "subject.py").write_text("from pathlib import Path\nprint(Path.cwd().name)\n")

    result, _ = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is True
    assert result.working_directory == "work"
    assert result.stdout_path.read_text(encoding="utf-8") == "work\n"


def test_runner_unexpected_nonzero_exit_is_structured_error(case_factory, tmp_path: Path) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=[sys.executable, "-c", "import sys; sys.exit(7)"],
        expected_exit_codes=[0],
    )

    result, _ = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is False
    assert result.return_code == 7
    assert result.error == "command exited with 7; expected one of [0]"
    assert result.stdout_path.is_file()
    assert result.stderr_path.is_file()


def test_runner_accepts_declared_nonzero_exit(case_factory, tmp_path: Path) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=[sys.executable, "-c", "import sys; sys.exit(7)"],
        expected_exit_codes=[0, 7],
    )

    result, _ = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is True
    assert result.return_code == 7
    assert result.error is None


def test_runner_timeout_is_structured_and_process_is_terminated(
    case_factory, tmp_path: Path
) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=[sys.executable, "-c", "import time; time.sleep(60)"],
        timeout_seconds=0.05,
    )

    result, _ = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is False
    assert result.error == "command timed out after 0.05 seconds"
    assert result.return_code is not None
    assert result.duration_seconds < 5


@pytest.fixture
def descendant_lifetime_pipe():
    read_descriptor, write_descriptor = os.pipe()
    with os.fdopen(read_descriptor, "rb", buffering=0) as reader, os.fdopen(
        write_descriptor, "wb", buffering=0
    ) as writer:
        yield reader, writer


@pytest.mark.parametrize(
    ("startup_delay", "ignore_term"), [(0.0, False), (1.5, False), (0.0, True)],
    ids=["ready", "slow-startup", "forced-kill"],
)
def test_runner_timeout_terminates_the_subject_process_group(
    case_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    startup_delay: float,
    ignore_term: bool,
    descendant_lifetime_pipe,
) -> None:
    ready = tmp_path / "descendant-ready"
    lifetime_reader, lifetime_writer = descendant_lifetime_pipe
    descriptor = lifetime_writer.fileno()
    child_code = (
        "import os, signal, sys, time\n"
        "from pathlib import Path\n"
        "os.fstat(int(sys.argv[3]))\n"
        "time.sleep(float(sys.argv[2]))\n"
        "def stop(signum, frame):\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN if sys.argv[4] == 'ignore' else stop)\n"
        "Path(sys.argv[1]).write_text('ready\\n', encoding='utf-8')\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    parent_code = (
        "import os, subprocess, sys, time\n"
        "descriptor = int(sys.argv[4])\n"
        "subprocess.Popen([sys.executable, '-c', *sys.argv[1:]], pass_fds=(descriptor,))\n"
        "os.close(descriptor)\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    case_path, _ = _runner_case(
        case_factory,
        command=[
            sys.executable,
            "-c",
            parent_code,
            child_code,
            str(ready),
            str(startup_delay),
            str(descriptor),
            "ignore" if ignore_term else "handle",
        ],
        timeout_seconds=1,
    )

    # This test exercises timeout termination of an already-ready process
    # group. Separate interpreter/child startup from that one-second wait.
    # The short-timeout test above still exercises startup-inclusive timeout.
    real_popen = runner_module.subprocess.Popen
    real_killpg = os.killpg
    group_signals = []
    startup_duration = 0.0

    def signal_group(process_group, signum):
        if signum:
            group_signals.append((process_group, signum))
        return real_killpg(process_group, signum)

    def launch_ready(*args, **kwargs):
        nonlocal startup_duration
        assert kwargs.get("start_new_session") is True
        kwargs["pass_fds"] = (descriptor,)
        started = time.monotonic()
        process = real_popen(*args, **kwargs)
        lifetime_writer.close()
        try:
            deadline = started + 10.0
            while not ready.is_file():
                assert process.poll() is None, "fixture parent exited before child readiness"
                if time.monotonic() >= deadline:
                    pytest.fail(
                        "fixture child did not become ready within the 10-second setup budget"
                    )
                time.sleep(0.01)
            assert not select.select([lifetime_reader], [], [], 0)[0], (
                "fixture child must retain its lifetime descriptor before timeout"
            )
            startup_duration = time.monotonic() - started
            return process
        except BaseException:
            # Setup failure must not leak this test's newly created process group.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            raise

    monkeypatch.setattr(runner_module.subprocess, "Popen", launch_ready)
    monkeypatch.setattr(runner_module.os, "killpg", signal_group)
    result, _ = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is False
    assert result.error == "command timed out after 1 seconds"
    assert result.return_code == -signal.SIGTERM
    assert ready.is_file()
    # The kernel closes this sole writer on either graceful exit or SIGKILL.
    # A Python handler-written marker cannot witness the forced-kill path.
    assert select.select([lifetime_reader], [], [], 1)[0] == [lifetime_reader], (
        "descendant still owns its lifetime descriptor after runner cleanup"
    )
    assert lifetime_reader.read(1) == b""
    assert group_signals[0][1] == signal.SIGTERM
    assert len({process_group for process_group, _ in group_signals}) == 1
    if ignore_term:
        assert any(signum == signal.SIGKILL for _, signum in group_signals)
    assert 1 <= result.duration_seconds - startup_duration < 5


def test_runner_missing_executable_is_structured_error(case_factory, tmp_path: Path) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=["definitely-not-a-real-pvs-test-executable"],
    )

    result, executable = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is False
    assert result.return_code is None
    assert result.error == "executable not found: definitely-not-a-real-pvs-test-executable"
    assert executable == {
        "requested": "definitely-not-a-real-pvs-test-executable",
        "resolved": False,
    }
    assert result.stdout_path.read_bytes() == b""
    assert "executable not found" in result.stderr_path.read_text(encoding="utf-8")


def test_runner_requires_execution_declaration(case_factory, tmp_path: Path) -> None:
    case_path, _ = case_factory()

    with pytest.raises(ExecutionError, match="requires an execution declaration"):
        run_command(load_case(case_path), tmp_path / "logs")


def test_runner_rejects_nonexistent_working_directory(case_factory, tmp_path: Path) -> None:
    case_path, _ = _runner_case(
        case_factory,
        command=[sys.executable, "-c", "pass"],
        working_directory="missing-directory",
    )

    with pytest.raises(ExecutionError, match="working directory does not exist"):
        run_command(load_case(case_path), tmp_path / "logs")


def test_runner_uses_argv_without_shell_interpretation(case_factory, tmp_path: Path) -> None:
    marker = tmp_path / "shell-expansion-would-create-this"
    literal = f"$(touch {marker})"
    case_path, _ = _runner_case(
        case_factory,
        command=[sys.executable, "capture.py", literal],
    )
    (case_path.parent / "capture.py").write_text(
        "import json, sys\nprint(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )

    result, _ = run_command(load_case(case_path), tmp_path / "logs")

    assert result.succeeded is True
    assert json.loads(result.stdout_path.read_text(encoding="utf-8")) == [literal]
    assert not marker.exists()
