"""Public machine-oriented API for running and validating PVS cases."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import (
    ResolvedArtifact,
    acquire_validation_snapshot,
    embed_artifacts,
    evidence_artifacts,
    resolve_artifacts,
    snapshot,
)
from .case import CaseDefinition, load_case
from .checks import evaluate_checks
from .errors import CaseError, IntegrityError
from .evidence import create_evidence
from .hashing import file_identity
from .jsonutil import write_pretty
from .models import ExecutionResult, Status
from .package import resolve_package_policy, write_manifest
from .reporting import render_html, render_pdf
from .runner import ingest_execution, run_command
from .timeutil import iso_utc, utc_now
from .verify import verify_target

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004


@dataclass(frozen=True)
class RunOutcome:
    status: Status
    run_id: str
    evidence_id: str
    finding_id: str | None
    package_id: str
    output_directory: Path
    evidence_path: Path
    manifest_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "run_id": self.run_id,
            "evidence_id": self.evidence_id,
            "finding_id": self.finding_id,
            "package_id": self.package_id,
            "output_directory": str(self.output_directory),
            "evidence_path": str(self.evidence_path),
            "manifest_path": str(self.manifest_path),
        }


def _snapshots(artifacts: dict[str, ResolvedArtifact]) -> dict[str, dict[str, Any]]:
    return {artifact_id: snapshot(artifact) for artifact_id, artifact in artifacts.items()}


def _identity_changed(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("exists") != right.get("exists")
        or left.get("sha256") != right.get("sha256")
        or left.get("size_bytes") != right.get("size_bytes")
    )


def _preflight_issues(
    artifacts: dict[str, ResolvedArtifact],
    values: dict[str, dict[str, Any]],
) -> list[str]:
    issues: list[str] = []
    for artifact_id, artifact in artifacts.items():
        value = values[artifact_id]
        if value.get("resolution_error"):
            issues.append(f"unsafe artifact path before execution: {artifact_id}")
            continue
        if artifact.role == "output" and value["exists"]:
            issues.append(f"output artifact already exists before execution: {artifact_id}")
        if artifact.role != "output" and artifact.required and not value["exists"]:
            issues.append(f"required pre-execution artifact is missing: {artifact_id}")
        expected = artifact.declaration.get("expected_sha256")
        if (
            artifact.role != "output"
            and expected
            and value.get("exists")
            and value.get("sha256") != expected
        ):
            issues.append(f"artifact hash mismatch before execution: {artifact_id}")
    return issues


def _provenance_issues(
    case: CaseDefinition,
    artifacts: dict[str, ResolvedArtifact],
    pre_execution: dict[str, dict[str, Any]],
    validation_input: dict[str, dict[str, Any]],
    post_validation: dict[str, dict[str, Any]],
    *,
    ran_subject: bool,
) -> list[str]:
    issues: list[str] = []
    for artifact_id, artifact in artifacts.items():
        validation = validation_input[artifact_id]
        final = post_validation[artifact_id]
        if validation.get("resolution_error"):
            issues.append(f"unsafe artifact path at validation: {artifact_id}")
        if final.get("resolution_error"):
            issues.append(f"unsafe artifact path after validation: {artifact_id}")
        if artifact.required and not validation["exists"]:
            issues.append(f"required artifact is missing at validation: {artifact_id}")
        expected = artifact.declaration.get("expected_sha256")
        if expected and validation.get("exists") and validation.get("sha256") != expected:
            issues.append(f"artifact hash mismatch: {artifact_id}")
        changed_before_validation = _identity_changed(pre_execution[artifact_id], validation)
        if ran_subject and artifact.role != "output" and changed_before_validation:
            issues.append(
                f"immutable {artifact.role} artifact changed during execution: {artifact_id}"
            )
        if not ran_subject and changed_before_validation:
            issues.append(f"artifact changed before its validation snapshot: {artifact_id}")
        if _identity_changed(validation, final):
            issues.append(f"artifact changed while checks were evaluated: {artifact_id}")
    current_case = file_identity(case.path)
    if current_case["sha256"] != case.raw_identity["sha256"]:
        issues.append("case definition changed during the PVS transaction")
    return issues


def _failed_execution(
    case: CaseDefinition,
    log_directory: Path,
    issues: list[str],
) -> ExecutionResult:
    now = iso_utc(utc_now())
    log_directory.mkdir(parents=True, exist_ok=True)
    stdout_path = log_directory / "stdout.txt"
    stderr_path = log_directory / "stderr.txt"
    stdout_path.touch()
    stderr_path.write_text("\n".join(issues) + "\n", encoding="utf-8")
    declaration = case.data.get("execution", {})
    return ExecutionResult(
        mode="run",
        command=[str(item) for item in declaration.get("command", [])],
        working_directory=str(declaration.get("working_directory", ".")),
        started_at=now,
        finished_at=now,
        duration_seconds=0.0,
        return_code=None,
        expected_exit_codes=[int(value) for value in declaration.get("expected_exit_codes", [0])],
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        error="pre-execution provenance failed; subject was not executed",
    )


def _output_target(case: CaseDefinition, output_dir: str | Path | None, run_id: str) -> Path:
    if output_dir is not None:
        return Path(output_dir).resolve()
    leaf = f"{case.id}-{run_id.rsplit(':', 1)[-1][:8]}"
    return (case.root / "pvs-output" / leaf).resolve()


def _libc_function(name: str) -> Any:
    """Resolve a required libc publication primitive without an unsafe fallback."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:  # pragma: no cover - defensive platform failure
        raise IntegrityError(
            "atomic no-replace package publication is unavailable: "
            f"could not load the process C library for {name}: {exc}"
        ) from exc
    try:
        return getattr(libc, name)
    except AttributeError as exc:
        raise IntegrityError(
            "atomic no-replace package publication is unavailable: "
            f"the process C library does not expose {name}"
        ) from exc


def _publication_os_error(operation: str, target: Path, error_number: int) -> None:
    """Map native publication failures to stable, actionable PVS errors."""

    error_name = errno.errorcode.get(error_number, f"errno {error_number}")
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise CaseError(f"output directory already exists: {target}")
    if error_number == errno.ENOSYS:
        raise IntegrityError(
            "atomic no-replace package publication is unavailable: "
            f"{operation} is not implemented by the running kernel ({error_name})"
        )
    if error_number == errno.EINVAL:
        raise IntegrityError(
            "atomic no-replace package publication is unavailable: "
            f"{operation} rejected the required no-replace operation; the running "
            f"kernel or target filesystem does not provide the required semantics ({error_name})"
        )
    unsupported = {
        value
        for value in (
            getattr(errno, "EOPNOTSUPP", None),
            getattr(errno, "ENOTSUP", None),
        )
        if value is not None
    }
    if error_number in unsupported:
        raise IntegrityError(
            "atomic no-replace package publication is unavailable: "
            f"the target filesystem does not support {operation} "
            f"(EOPNOTSUPP/ENOTSUP, errno {error_number})"
        )
    description = os.strerror(error_number) if error_number else "native call set no errno"
    raise IntegrityError(
        "could not atomically publish evidence package with "
        f"{operation}: [{error_name}] {description}: {target}"
    )


def _publish_no_replace(source: Path, target: Path) -> None:
    """Atomically publish a completed directory without replacing any target."""

    current_platform = sys.platform
    try:
        if current_platform.startswith("linux"):
            renameat2 = _libc_function("renameat2")
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                _AT_FDCWD,
                os.fsencode(source),
                _AT_FDCWD,
                os.fsencode(target),
                _RENAME_NOREPLACE,
            )
            if result != 0:
                error_number = ctypes.get_errno()
                _publication_os_error("renameat2(RENAME_NOREPLACE)", target, error_number)
            return
        if current_platform == "darwin":  # pragma: no cover - platform CI
            renamex_np = _libc_function("renamex_np")
            renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            renamex_np.restype = ctypes.c_int
            result = renamex_np(os.fsencode(source), os.fsencode(target), _RENAME_EXCL)
            if result != 0:
                error_number = ctypes.get_errno()
                _publication_os_error("renamex_np(RENAME_EXCL)", target, error_number)
            return
        if os.name == "nt":  # pragma: no cover - platform CI
            os.rename(source, target)
            return
        raise IntegrityError("atomic no-replace package publication is unsupported on this OS")
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise CaseError(f"output directory already exists: {target}") from exc
        _publication_os_error("native no-replace rename", target, exc.errno or 0)


def _remove_publication_probe(path: Path) -> None:
    """Remove one private probe path without following a substituted symlink."""

    try:
        path.lstat()
    except FileNotFoundError:
        return
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _preflight_atomic_publication(parent: Path) -> None:
    """Prove no-replace publication semantics on the actual target filesystem."""

    probe_paths: list[Path] = []
    operation_error: BaseException | None = None
    operation_cause: Exception | None = None

    try:
        collision_source = Path(
            tempfile.mkdtemp(prefix=".pvs-publish-probe-source-", dir=parent)
        )
        probe_paths.append(collision_source)
        occupied_target = Path(
            tempfile.mkdtemp(prefix=".pvs-publish-probe-occupied-", dir=parent)
        )
        probe_paths.append(occupied_target)
        success_source = Path(
            tempfile.mkdtemp(prefix=".pvs-publish-probe-source-", dir=parent)
        )
        probe_paths.append(success_source)
        success_target = parent / f".pvs-publish-probe-target-{uuid.uuid4().hex}"
        probe_paths.append(success_target)

        try:
            _publish_no_replace(collision_source, occupied_target)
        except CaseError:
            pass
        else:
            raise IntegrityError(
                "atomic no-replace package publication preflight failed: "
                "the native operation replaced an existing directory"
            )
        if not collision_source.is_dir() or not occupied_target.is_dir():
            raise IntegrityError(
                "atomic no-replace package publication preflight failed: "
                "a rejected collision changed a probe directory"
            )

        _publish_no_replace(success_source, success_target)
        if success_source.exists() or not success_target.is_dir():
            raise IntegrityError(
                "atomic no-replace package publication preflight failed: "
                "a successful probe did not perform one directory rename"
            )
    except OSError as exc:
        operation_error = IntegrityError(
            "atomic publication preflight could not create or inspect its private "
            f"probe paths in {parent}: {exc}"
        )
        operation_cause = exc
    except BaseException as exc:
        operation_error = exc

    cleanup_errors: list[str] = []
    for path in probe_paths:
        try:
            _remove_publication_probe(path)
        except OSError as exc:
            cleanup_errors.append(f"{path.name}: {exc}")
    if operation_error is not None and not isinstance(operation_error, Exception):
        # Cancellation still owns these private probes. Attempt every cleanup,
        # then propagate the original interruption even if cleanup was denied.
        raise operation_error
    if cleanup_errors:
        raise IntegrityError(
            "atomic publication preflight could not clean its private probe paths: "
            + "; ".join(cleanup_errors)
        ) from operation_error
    if operation_error is not None:
        if operation_cause is not None:
            raise operation_error from operation_cause
        raise operation_error


def _retain_case(case: CaseDefinition, destination: Path) -> None:
    """Retain the acquired declaration without reopening its mutable source."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(case.raw_bytes)
    if file_identity(destination) != case.raw_identity:
        raise IntegrityError("retained case bytes differ from the acquired declaration")


def _execute_case(
    case_path: str | Path,
    *,
    mode: str,
    output_dir: str | Path | None,
    embed: bool | None,
    html: bool | None,
    pdf: bool | None,
) -> RunOutcome:
    case = load_case(case_path)
    if mode == "run" and "execution" not in case.data:
        raise CaseError("pvs run requires an execution declaration")
    package_policy = resolve_package_policy(
        case.data.get("package"),
        embed_artifacts=embed,
        render_html_report=html,
        render_pdf_report=pdf,
    )
    run_id = f"urn:uuid:{uuid.uuid4()}"
    target = _output_target(case, output_dir, run_id)
    if target.exists():
        raise CaseError(f"output directory already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _preflight_atomic_publication(target.parent)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.pvs-tmp-", dir=target.parent))
    validation_snapshot_root: Path | None = None
    try:
        case_copy = staging / "case" / "pvs.yaml"
        _retain_case(case, case_copy)

        artifacts = resolve_artifacts(case)
        pre_execution = _snapshots(artifacts)
        executable: dict[str, Any] | None = None
        preflight = _preflight_issues(artifacts, pre_execution) if mode == "run" else []
        if mode == "run":
            if preflight:
                execution = _failed_execution(case, staging / "logs", preflight)
            else:
                execution, executable = run_command(case, staging / "logs")
        else:
            started = utc_now()
            started_clock = time.perf_counter()

        validation_snapshot_root = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.pvs-validation-", dir=target.parent)
        )
        validation_artifacts, validation_input = acquire_validation_snapshot(
            validation_snapshot_root,
            artifacts,
        )
        check_results = evaluate_checks(case.data["checks"], validation_artifacts)
        if mode == "validate":
            finished = utc_now()
            execution = ingest_execution(
                iso_utc(started),
                iso_utc(finished),
                time.perf_counter() - started_clock,
            )
        post_validation = _snapshots(artifacts)
        provenance_issues = list(preflight)
        provenance_issues.extend(
            _provenance_issues(
                case,
                artifacts,
                pre_execution,
                validation_input,
                post_validation,
                ran_subject=mode == "run" and not preflight,
            )
        )

        embed_enabled = package_policy.embed_artifacts.effective
        html_enabled = package_policy.html.effective
        pdf_enabled = package_policy.pdf.effective
        package_paths = embed_artifacts(staging, validation_artifacts) if embed_enabled else {}
        for artifact_id, relative in package_paths.items():
            retained = file_identity(staging / relative)
            expected = validation_input[artifact_id]
            if retained.get("sha256") != expected.get("sha256") or retained.get(
                "size_bytes"
            ) != expected.get("size_bytes"):
                provenance_issues.append(
                    f"retained artifact bytes differ from validated bytes: {artifact_id}"
                )
        shutil.rmtree(validation_snapshot_root)
        validation_snapshot_root = None

        artifact_records = evidence_artifacts(
            case,
            artifacts,
            pre_execution,
            validation_input,
            post_validation,
        )
        envelope = create_evidence(
            run_id=run_id,
            case=case,
            execution=execution,
            executable=executable,
            artifact_records=artifact_records,
            check_results=check_results,
            provenance_issues=provenance_issues,
            package_paths=package_paths,
            package_policy=package_policy,
        )
        evidence_path = staging / "evidence.json"
        write_pretty(evidence_path, envelope)
        if html_enabled:
            render_html(envelope, staging / "report.html")
        if pdf_enabled:
            render_pdf(envelope, staging / "report.pdf")
        manifest = write_manifest(
            staging,
            envelope["integrity"]["evidence_id"],
            envelope["record"]["run"]["finished_at"],
            policy=package_policy,
        )
        verification = verify_target(staging)
        if not verification.valid:
            failures = [item["name"] for item in verification.checks if item["status"] != "PASS"]
            raise IntegrityError(
                "generated evidence package failed self-verification: " + ", ".join(failures[:10])
            )
        _publish_no_replace(staging, target)
    except BaseException:
        # This scope owns only unpublished staging. Cancellation must propagate
        # after cleanup just like an ordinary failure; publication is a rename.
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if validation_snapshot_root is not None:
            shutil.rmtree(validation_snapshot_root, ignore_errors=True)

    status = Status(envelope["record"]["summary"]["status"])
    return RunOutcome(
        status=status,
        run_id=run_id,
        evidence_id=envelope["integrity"]["evidence_id"],
        finding_id=envelope["integrity"]["finding"].get("finding_id"),
        package_id=manifest["integrity"]["package_id"],
        output_directory=target,
        evidence_path=target / "evidence.json",
        manifest_path=target / "manifest.json",
    )


def validate_case(
    case_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    embed_artifacts: bool | None = None,
    render_html_report: bool | None = None,
    render_pdf_report: bool | None = None,
) -> RunOutcome:
    """Validate existing case artefacts without executing the subject."""

    return _execute_case(
        case_path,
        mode="validate",
        output_dir=output_dir,
        embed=embed_artifacts,
        html=render_html_report,
        pdf=render_pdf_report,
    )


def run_case(
    case_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    embed_artifacts: bool | None = None,
    render_html_report: bool | None = None,
    render_pdf_report: bool | None = None,
) -> RunOutcome:
    """Execute the declared command and validate its resulting artefacts."""

    return _execute_case(
        case_path,
        mode="run",
        output_dir=output_dir,
        embed=embed_artifacts,
        html=render_html_report,
        pdf=render_pdf_report,
    )
