"""Local no-shell command runner."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from .case import CaseDefinition
from .errors import ExecutionError
from .hashing import file_identity, snapshot_file
from .models import ExecutionResult
from .paths import confined_path
from .timeutil import iso_utc, utc_now


def _effective_search_path(environment: dict[str, str], cwd: Path) -> str | None:
    raw = environment.get("PATH")
    if raw is None:
        return None
    entries: list[str] = []
    for item in raw.split(os.pathsep):
        entry = Path(item) if item else cwd
        if not entry.is_absolute():
            entry = cwd / entry
        entries.append(str(entry.resolve(strict=False)))
    return os.pathsep.join(entries)


def _resolved_executable(
    command: list[str],
    cwd: Path,
    case_root: Path,
    environment: dict[str, str],
) -> tuple[dict[str, Any], Path | None]:
    requested = command[0]
    requested_path = Path(requested)
    resolved: str | None
    has_path_component = requested_path.is_absolute() or any(
        separator and separator in requested for separator in (os.sep, os.altsep)
    )
    if has_path_component:
        candidate = requested_path if requested_path.is_absolute() else cwd / requested_path
        resolved = str(candidate.resolve(strict=False))
    else:
        resolved = shutil.which(requested, path=_effective_search_path(environment, cwd))
    if resolved is None:
        return {"requested": requested, "resolved": False}, None
    try:
        path = Path(resolved).resolve(strict=True)
    except OSError:
        return {"requested": requested, "resolved": False}, None
    is_file = path.is_file()
    is_executable = is_file and os.access(path, os.X_OK)
    result: dict[str, Any] = {
        "requested": requested,
        "resolved": is_executable,
        "filename": path.name,
    }
    if is_executable:
        result["launch_profile"] = "pvs-private-executable-snapshot/1"
    if is_file:
        resolved_path = path
        root = case_root.resolve()
        if resolved_path == root or root in resolved_path.parents:
            result["case_relative_path"] = resolved_path.relative_to(root).as_posix()
    return result, path if result["resolved"] else None


if sys.platform == "win32":

    def _terminate_group(process: subprocess.Popen[Any]) -> None:
        """Best-effort fallback for a process not created by PVS run mode."""
        if process.poll() is not None:  # pragma: no cover - Windows CI
            return
        try:  # pragma: no cover - Windows CI
            process.terminate()
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):  # pragma: no cover
            with suppress(ProcessLookupError):
                process.kill()

else:

    def _terminate_group(process: subprocess.Popen[Any]) -> None:
        """Terminate the POSIX session created for a subject command."""
        # The group may still contain descendants after its leader exits.
        # Always signal the session created by start_new_session=True.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.01)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def run_command(
    case: CaseDefinition,
    log_directory: Path,
) -> tuple[ExecutionResult, dict[str, Any] | None]:
    if os.name != "posix":  # pragma: no cover - exercised by platform CI
        raise ExecutionError(
            "PVS run mode requires POSIX process-group containment; "
            "validate and verify remain available on this platform"
        )
    execution = case.data.get("execution")
    if not execution:
        raise ExecutionError("pvs run requires an execution declaration")
    command = [str(item) for item in execution["command"]]
    cwd = confined_path(case.root, execution.get("working_directory", "."))
    if not cwd.is_dir():
        raise ExecutionError(f"execution working directory does not exist: {cwd}")
    timeout = execution.get("timeout_seconds")
    expected = [int(value) for value in execution.get("expected_exit_codes", [0])]
    environment = os.environ.copy()
    environment.update(
        {str(key): str(value) for key, value in execution.get("environment", {}).items()}
    )
    executable, executable_path = _resolved_executable(
        command,
        cwd,
        case.root,
        environment,
    )

    log_directory.mkdir(parents=True, exist_ok=True)
    stdout_path = log_directory / "stdout.txt"
    stderr_path = log_directory / "stderr.txt"
    started_datetime = utc_now()
    started_clock = time.perf_counter()
    return_code: int | None = None
    error: str | None = None
    process: subprocess.Popen[Any] | None = None
    executable_snapshot_directory: Path | None = None
    executable_snapshot_path: Path | None = None
    try:
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                if executable_path is None:
                    raise FileNotFoundError(command[0])
                executable_snapshot_directory = Path(
                    tempfile.mkdtemp(prefix=".pvs-executable-", dir=log_directory)
                )
                executable_snapshot_path = executable_snapshot_directory / executable_path.name
                executable.update(
                    snapshot_file(
                        executable_path,
                        executable_snapshot_path,
                        executable=True,
                    )
                )
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    executable=str(executable_snapshot_path),
                    shell=False,
                    start_new_session=True,
                )
                try:
                    return_code = process.wait(
                        timeout=float(timeout) if timeout is not None else None
                    )
                except subprocess.TimeoutExpired:
                    _terminate_group(process)
                    try:
                        return_code = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        return_code = process.wait(timeout=1)
                    error = f"command timed out after {timeout} seconds"
        except FileNotFoundError:
            error = f"executable not found: {command[0]}"
            if executable is not None and executable.get("resolved") is True:
                executable["unchanged_during_execution"] = False
            stderr_path.write_text(f"{error}\n", encoding="utf-8")
            stdout_path.touch()
        except OSError as exc:
            error = f"could not acquire or execute command: {exc}"
            if executable is not None and executable.get("resolved") is True:
                executable["unchanged_during_execution"] = False
            stderr_path.write_text(f"{error}\n", encoding="utf-8")
            stdout_path.touch()
        finally:
            if process is not None:
                _terminate_group(process)

        if executable_snapshot_path is not None and executable.get("sha256") is not None:
            try:
                post_identity = file_identity(executable_snapshot_path)
                executable["post_execution_sha256"] = post_identity["sha256"]
                executable["post_execution_size_bytes"] = post_identity["size_bytes"]
                executable["unchanged_during_execution"] = (
                    executable.get("sha256") == post_identity["sha256"]
                    and executable.get("size_bytes") == post_identity["size_bytes"]
                )
            except OSError:
                executable["unchanged_during_execution"] = False
            if not executable["unchanged_during_execution"] and error is None:
                error = "acquired executable changed during execution"
    finally:
        if executable_snapshot_directory is not None:
            try:
                shutil.rmtree(executable_snapshot_directory)
            except OSError as exc:
                raise ExecutionError(
                    f"could not remove private executable snapshot: {exc}"
                ) from exc
    finished_datetime = utc_now()
    duration = time.perf_counter() - started_clock
    if error is None and return_code not in expected:
        error = f"command exited with {return_code}; expected one of {expected}"
    result = ExecutionResult(
        mode="run",
        command=command,
        working_directory=Path(execution.get("working_directory", ".")).as_posix(),
        started_at=iso_utc(started_datetime),
        finished_at=iso_utc(finished_datetime),
        duration_seconds=duration,
        return_code=return_code,
        expected_exit_codes=expected,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        error=error,
    )
    return result, executable


def ingest_execution(started_at: str, finished_at: str, duration_seconds: float) -> ExecutionResult:
    return ExecutionResult(
        mode="validate",
        command=None,
        working_directory=".",
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        return_code=None,
        expected_exit_codes=[],
    )
