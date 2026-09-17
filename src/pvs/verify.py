"""Offline integrity verification for evidence records and complete packages."""

from __future__ import annotations

import importlib.metadata
import stat as stat_module
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .canonical import canonical_sha256
from .case import load_case
from .constants import (
    EVIDENCE_PREFIX,
    EVIDENCE_SCHEMA,
    LEGACY_EVIDENCE_SCHEMA,
    MANIFEST_SCHEMA,
    V2_EVIDENCE_SCHEMA,
    V2_MANIFEST_SCHEMA,
)
from .errors import ReportError
from .finding import create_finding_metadata
from .hashing import file_identity, snapshot_file
from .jsonutil import load_strict
from .models import Status
from .package import (
    normalized_package_path,
    package_file_role,
    report_profiles,
)
from .paths import (
    confined_path,
    path_is_link_or_reparse,
    stat_is_link_or_reparse,
    walk_directory_entries,
)
from .reporting import (
    render_html,
    render_html_v1,
    render_html_v2,
    render_pdf,
    render_pdf_v1,
    render_pdf_v2,
)
from .verification.policies import (
    _verify_embedding_policy,
    _verify_evidence_manifest_contract,
    _verify_report_policy,
)
from .verification.producer import (
    _producer_invariants,
    _recomputed_status,
)
from .verification.results import VerificationCheck, _append, _VerifiedTarget
from .verification.results import VerificationResult as VerificationResult
from .verification.schemas import _manifest_schema_errors, _schema_errors


def _verify_evidence_value(
    envelope: Any,
    checks: list[VerificationCheck],
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    errors = _schema_errors(envelope, "evidence")
    _append(checks, "evidence schema", not errors, "; ".join(errors[:5]) or "schema valid")
    if errors or not isinstance(envelope, dict):
        return None, None, None
    record = envelope["record"]
    integrity = envelope["integrity"]
    digest = canonical_sha256(record)
    digest_ok = digest == integrity["digest"]
    _append(checks, "canonical record digest", digest_ok, f"computed sha256:{digest}")
    expected_id = f"{EVIDENCE_PREFIX}{digest}"
    id_ok = expected_id == integrity["evidence_id"]
    _append(checks, "evidence ID", id_ok, f"computed {expected_id}")

    # Establish the producer contract before projecting a Scientific Finding.
    # The evidence schema intentionally treats resolved_definition as an embedded
    # object, so hostile but schema-valid structure must fail verification rather
    # than reaching the projection code unchecked.
    _producer_invariants(record, checks, evidence_schema=envelope["schema"])

    finding_id: str | None = None
    if envelope["schema"] in {V2_EVIDENCE_SCHEMA, EVIDENCE_SCHEMA}:
        claimed_finding = integrity["finding"]
        if claimed_finding["status"] == "ISSUED":
            finding_id = claimed_finding["finding_id"]
        try:
            recomputed_finding = create_finding_metadata(record)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            _append(
                checks,
                "Scientific Finding recomputation",
                False,
                f"could not build projection: {exc}",
            )
            recomputed_finding = None
        if recomputed_finding is None:
            eligibility_ok = False
            eligibility_detail = "Finding ID projection could not be recomputed"
        else:
            eligibility_ok = claimed_finding["status"] == recomputed_finding[
                "status"
            ] and claimed_finding.get("reason") == recomputed_finding.get("reason")
            eligibility_detail = (
                "Finding ID eligible"
                if recomputed_finding["status"] == "ISSUED"
                else f"not issued: {recomputed_finding['reason']}"
            )
        _append(
            checks,
            "Scientific Finding eligibility",
            eligibility_ok,
            eligibility_detail,
        )
        if claimed_finding["status"] == "ISSUED":
            expected_finding_id = (
                recomputed_finding.get("finding_id") if recomputed_finding else None
            )
            digest_ok = (
                claimed_finding["digest"] == recomputed_finding.get("digest")
                if recomputed_finding
                else False
            )
            finding_id_ok = finding_id == expected_finding_id
            detail = str(expected_finding_id or "Finding ID is not eligible")
            _append(checks, "Scientific Finding digest", digest_ok, detail)
            _append(checks, "Scientific Finding ID", finding_id_ok, detail)

    check_ids = [item.get("id") for item in record.get("checks", [])]
    _append(
        checks,
        "check ID uniqueness",
        len(check_ids) == len(set(check_ids)),
        f"{len(check_ids)} check records",
    )
    artifact_ids = [item.get("id") for item in record.get("artifacts", [])]
    _append(
        checks,
        "artifact ID uniqueness",
        len(artifact_ids) == len(set(artifact_ids)),
        f"{len(artifact_ids)} artifact records",
    )
    reference_ids = [item.get("id") for item in record.get("references", [])]
    _append(
        checks,
        "reference ID uniqueness",
        len(reference_ids) == len(set(reference_ids)),
        f"{len(reference_ids)} reference records",
    )

    actual_counts = Counter(item.get("status") for item in record.get("checks", []))
    declared_counts = record.get("summary", {}).get("counts", {})
    counts_ok = all(
        actual_counts.get(status.value, 0) == declared_counts.get(status.value, 0)
        for status in Status
    )
    _append(checks, "summary counts", counts_ok, f"recomputed {dict(actual_counts)}")
    recomputed_status = _recomputed_status(record)
    status_ok = recomputed_status == record.get("summary", {}).get("status")
    _append(checks, "summary status", status_ok, f"recomputed {recomputed_status}")
    return integrity["evidence_id"], finding_id, record


def _verify_evidence_file(
    path: Path, checks: list[VerificationCheck]
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    try:
        envelope = load_strict(path)
    except (OSError, UnicodeError, ValueError) as exc:
        _append(checks, "strict evidence JSON", False, str(exc))
        return None, None, None
    _append(checks, "strict evidence JSON", True, "UTF-8, unique keys and finite numbers")
    return _verify_evidence_value(envelope, checks)


def _package_files(root: Path) -> tuple[dict[str, Path], list[str]]:
    files: dict[str, Path] = {}
    errors: list[str] = []
    collision_keys: set[str] = {"manifest.json"}
    for path, metadata in walk_directory_entries(root):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        if stat_is_link_or_reparse(metadata):
            errors.append(f"symlink or reparse point is forbidden: {relative}")
            continue
        if stat_module.S_ISDIR(metadata.st_mode):
            continue
        if not stat_module.S_ISREG(metadata.st_mode):
            errors.append(f"non-regular package entry is forbidden: {relative}")
            continue
        try:
            normalized = normalized_package_path(relative)
        except Exception as exc:
            errors.append(str(exc))
            continue
        collision_key = normalized.casefold()
        if collision_key in collision_keys:
            errors.append(f"path normalization collision: {relative}")
            continue
        collision_keys.add(collision_key)
        files[normalized] = path
    return files, errors


def _snapshot_package(root: Path, destination: Path) -> list[str]:
    """Acquire a private, single-open snapshot before interpreting package bytes."""

    try:
        files, errors = _package_files(root)
    except OSError as exc:
        return [f"could not enumerate package: {exc}"]
    manifest_path = root / "manifest.json"
    try:
        if path_is_link_or_reparse(manifest_path):
            errors.append("manifest.json must not be a symlink or reparse point")
        elif not manifest_path.is_file():
            errors.append("manifest.json not found")
        else:
            files["manifest.json"] = manifest_path
    except OSError as exc:
        errors.append(f"could not inspect manifest.json: {exc}")
    if errors:
        return errors

    acquired: dict[str, dict[str, Any]] = {}
    for relative in sorted(files):
        try:
            acquired[relative] = snapshot_file(files[relative], destination / relative)
        except OSError as exc:
            errors.append(f"could not snapshot {relative}: {exc}")
    if errors:
        return errors

    try:
        final_files, final_errors = _package_files(root)
    except OSError as exc:
        return [f"could not re-enumerate package: {exc}"]
    final_manifest = root / "manifest.json"
    try:
        final_manifest_safe = not path_is_link_or_reparse(final_manifest)
        final_manifest_present = final_manifest.is_file()
    except OSError as exc:
        final_manifest_safe = False
        final_manifest_present = False
        final_errors.append(f"could not re-inspect manifest.json: {exc}")
    if final_manifest_safe and final_manifest_present:
        final_files["manifest.json"] = final_manifest
    else:
        final_errors.append("manifest.json changed during snapshot acquisition")
    if final_errors:
        errors.extend(final_errors)
    if set(final_files) != set(acquired):
        errors.append(
            "package namespace changed during snapshot acquisition: "
            f"before={sorted(acquired)} after={sorted(final_files)}"
        )
    for relative in sorted(set(final_files) & set(acquired)):
        try:
            current = file_identity(final_files[relative])
        except OSError as exc:
            errors.append(f"could not confirm {relative}: {exc}")
            continue
        if current != acquired[relative]:
            errors.append(f"file changed during snapshot acquisition: {relative}")
    return errors


def _retarget_verification(
    result: VerificationResult,
    target: Path,
    snapshot_errors: list[str],
) -> VerificationResult:
    checks = list(result.checks)
    if snapshot_errors:
        checks.insert(
            0,
            {
                "name": "immutable verification snapshot",
                "status": "FAIL",
                "detail": "; ".join(snapshot_errors),
            },
        )
    valid = result.valid and not snapshot_errors
    return VerificationResult(
        target=target,
        valid=valid,
        level=result.level,
        evidence_id=result.evidence_id,
        finding_id=result.finding_id,
        package_id=result.package_id,
        checks=checks,
        evidence_status=result.evidence_status,
        evidence_identity_pinned=result.evidence_identity_pinned,
        finding_identity_pinned=result.finding_identity_pinned,
        package_identity_pinned=result.package_identity_pinned,
        trust=result.trust if valid else "unverified",
    )


def _snapshot_failure_checks(errors: list[str]) -> list[VerificationCheck]:
    detail = "; ".join(errors)
    if any(error == "manifest.json not found" for error in errors):
        return [{"name": "manifest present", "status": "FAIL", "detail": detail}]
    if any("manifest.json must not be a symlink" in error for error in errors):
        return [{"name": "manifest safe file", "status": "FAIL", "detail": detail}]
    if any(
        "symlink or reparse point is forbidden" in error
        or "path normalization collision" in error
        or "non-regular package entry" in error
        for error in errors
    ):
        return [
            {"name": "safe package paths", "status": "FAIL", "detail": detail},
            {
                "name": "exact package coverage",
                "status": "FAIL",
                "detail": "unsafe entries could not be included in the verification snapshot",
            },
        ]
    return [
        {
            "name": "immutable verification snapshot",
            "status": "FAIL",
            "detail": detail,
        }
    ]


def _verify_package(
    root: Path,
    expect_evidence_id: str | None,
    expect_finding_id: str | None,
    expect_package_id: str | None,
) -> _VerifiedTarget:
    checks: list[VerificationCheck] = []
    manifest_path = root / "manifest.json"
    evidence_path = root / "evidence.json"
    if path_is_link_or_reparse(manifest_path):
        _append(
            checks,
            "manifest safe file",
            False,
            "manifest.json must not be a symlink or reparse point",
        )
        return _VerifiedTarget(VerificationResult(root, False, "package", checks=checks))
    if not manifest_path.is_file():
        _append(checks, "manifest present", False, "manifest.json not found")
        return _VerifiedTarget(VerificationResult(root, False, "package", checks=checks))
    try:
        manifest = load_strict(manifest_path)
    except (OSError, UnicodeError, ValueError) as exc:
        _append(checks, "strict manifest JSON", False, str(exc))
        return _VerifiedTarget(VerificationResult(root, False, "package", checks=checks))
    _append(checks, "strict manifest JSON", True, "UTF-8, unique keys and finite numbers")
    schema_errors = _manifest_schema_errors(manifest)
    _append(
        checks, "manifest schema", not schema_errors, "; ".join(schema_errors[:5]) or "schema valid"
    )
    if schema_errors:
        return _VerifiedTarget(VerificationResult(root, False, "package", checks=checks))

    manifest_schema = manifest["schema"]
    package = manifest["package"]
    digest = canonical_sha256(package)
    digest_ok = digest == manifest["integrity"]["digest"]
    _append(checks, "canonical manifest digest", digest_ok, f"computed sha256:{digest}")
    package_id = f"pvs-package:sha256:{digest}"
    package_id_ok = package_id == manifest["integrity"]["package_id"]
    _append(checks, "package ID", package_id_ok, f"computed {package_id}")

    actual_files, path_errors = _package_files(root)
    _append(
        checks, "safe package paths", not path_errors, "; ".join(path_errors) or "all paths safe"
    )
    declared_entries = package["files"]
    declared_paths = [entry["path"] for entry in declared_entries]
    declared_path_errors: list[str] = []
    for entry in declared_entries:
        try:
            normalized = normalized_package_path(entry["path"])
            if normalized != entry["path"]:
                declared_path_errors.append(f"non-canonical path: {entry['path']!r}")
            expected_role = package_file_role(entry["path"])
            if entry["role"] != expected_role:
                declared_path_errors.append(
                    f"wrong role for {entry['path']!r}: {entry['role']!r} != {expected_role!r}"
                )
        except Exception as exc:
            declared_path_errors.append(str(exc))
    duplicate_declared = len(declared_paths) != len(set(path.casefold() for path in declared_paths))
    _append(
        checks,
        "manifest path safety",
        not declared_path_errors,
        "; ".join(declared_path_errors) or "declared paths and roles are canonical",
    )
    _append(
        checks,
        "manifest path uniqueness",
        not duplicate_declared,
        f"{len(declared_paths)} entries",
    )
    coverage_ok = set(declared_paths) == set(actual_files)
    detail = f"declared={sorted(declared_paths)} actual={sorted(actual_files)}"
    _append(checks, "exact package coverage", coverage_ok, detail)

    if manifest_schema in {V2_MANIFEST_SCHEMA, MANIFEST_SCHEMA}:
        profiles_ok = package["report_profiles"] == report_profiles(manifest_schema)
        _append(
            checks,
            "report renderer profiles",
            profiles_ok,
            f"declared={package['report_profiles']} supported={report_profiles(manifest_schema)}",
        )
        _verify_report_policy(package["policy"], actual_files, checks)

    for entry in declared_entries:
        path_text = entry["path"]
        path = actual_files.get(path_text)
        if path is None:
            _append(checks, f"file {path_text}", False, "missing")
            continue
        identity = file_identity(path)
        passed = (
            identity["sha256"] == entry["sha256"] and identity["size_bytes"] == entry["size_bytes"]
        )
        _append(
            checks,
            f"file {path_text}",
            passed,
            f"sha256:{identity['sha256']} size={identity['size_bytes']}",
        )

    evidence_id: str | None = None
    finding_id: str | None = None
    record: dict[str, Any] | None = None
    if evidence_path.is_file():
        evidence_id, finding_id, record = _verify_evidence_file(evidence_path, checks)
    else:
        _append(checks, "evidence present", False, "evidence.json not found")
    manifest_id_matches = evidence_id is not None and evidence_id == package["evidence_id"]
    _append(checks, "manifest/evidence identity", manifest_id_matches, str(package["evidence_id"]))

    if expect_evidence_id is not None:
        _append(
            checks, "expected Evidence ID", evidence_id == expect_evidence_id, expect_evidence_id
        )
    if expect_finding_id is not None:
        _append(
            checks,
            "expected Scientific Finding ID",
            finding_id == expect_finding_id,
            (
                expect_finding_id
                if finding_id is not None
                else "evidence did not issue a Scientific Finding ID"
            ),
        )
    if expect_package_id is not None:
        _append(checks, "expected Package ID", package_id == expect_package_id, expect_package_id)

    if record is not None:
        contract_ok = _verify_evidence_manifest_contract(manifest_schema, package, record, checks)
        if manifest_schema in {V2_MANIFEST_SCHEMA, MANIFEST_SCHEMA} and contract_ok:
            _verify_embedding_policy(package["policy"], record, actual_files, checks)
            expected_logs = (
                {"logs/stdout.txt", "logs/stderr.txt"} if record["run"]["mode"] == "run" else set()
            )
            actual_logs = {path for path in actual_files if package_file_role(path) == "log"}
            _append(
                checks,
                "package execution log coverage",
                actual_logs == expected_logs,
                f"expected={sorted(expected_logs)} actual={sorted(actual_logs)}",
            )
        case_path = root / "case" / "pvs.yaml"
        case_ok = False
        case_semantics_ok = False
        if case_path.is_file() and not path_is_link_or_reparse(case_path):
            identity = file_identity(case_path)
            case_ok = (
                identity["sha256"] == record["case"]["raw_sha256"]
                and identity["size_bytes"] == record["case"]["raw_size_bytes"]
            )
            try:
                retained_case = load_case(case_path, allow_legacy=True)
                case_semantics_ok = (
                    retained_case.data == record["case"]["resolved_definition"]
                    and retained_case.semantic_sha256 == record["case"]["semantic_sha256"]
                )
            except Exception:
                case_semantics_ok = False
        _append(
            checks,
            "retained case bytes",
            case_ok,
            "case/pvs.yaml matches evidence raw hash and size",
        )
        _append(
            checks,
            "retained case semantics",
            case_semantics_ok,
            "retained YAML parses to the resolved case definition",
        )
        for artifact in record.get("artifacts", []):
            package_path = artifact.get("package_path")
            if not package_path:
                continue
            try:
                retained = confined_path(root, package_path, integrity=True)
                identity = file_identity(retained)
                expected = artifact.get("validation_input") or {}
                passed = identity["sha256"] == expected.get("sha256") and identity[
                    "size_bytes"
                ] == expected.get("size_bytes")
            except Exception:
                passed = False
            _append(checks, f"retained artifact {artifact.get('id')}", passed, str(package_path))
        for stream_name in ("stdout", "stderr"):
            stream = record.get("execution", {}).get(stream_name)
            if stream is None:
                continue
            package_path = stream.get("package_path")
            try:
                retained = confined_path(root, package_path, integrity=True)
                identity = file_identity(retained)
                passed = identity["sha256"] == stream.get("sha256") and identity[
                    "size_bytes"
                ] == stream.get("size_bytes")
            except Exception:
                passed = False
            _append(checks, f"retained log {stream_name}", passed, str(package_path))
        envelope = load_strict(evidence_path)
        html_path = root / "report.html"
        if html_path.is_file() and evidence_id is not None:
            try:
                carries_id = evidence_id in html_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                carries_id = False
            _append(
                checks,
                "HTML evidence identity",
                carries_id,
                "full Evidence ID present",
            )
            html_renderer = (
                render_html_v1
                if envelope.get("schema") == LEGACY_EVIDENCE_SCHEMA
                else render_html_v2
                if envelope.get("schema") == V2_EVIDENCE_SCHEMA
                else render_html
            )
            if envelope.get("schema") in {
                LEGACY_EVIDENCE_SCHEMA,
                V2_EVIDENCE_SCHEMA,
                EVIDENCE_SCHEMA,
            }:
                try:
                    with tempfile.TemporaryDirectory(prefix="pvs-verify-html-") as directory:
                        rendered = Path(directory) / "report.html"
                        html_renderer(envelope, rendered)
                        faithful = rendered.read_bytes() == html_path.read_bytes()
                except Exception:
                    faithful = False
                _append(
                    checks,
                    "HTML deterministic rendering",
                    faithful,
                    (
                        "report bytes match a fresh rendering of evidence.json"
                        if faithful
                        else "report bytes differ from a fresh rendering of evidence.json"
                    ),
                )
        pdf_path = root / "report.pdf"
        if pdf_path.is_file() and evidence_id is not None:
            # Parsing a bound report cannot rescue a package already rejected by
            # its identities, file bindings, producer contract or other reports.
            # Keep those diagnostics, but do not expose known-invalid PDF bytes
            # to the parser or replay their rendering. This is a prerequisite,
            # not a resource limit for internally coherent unpinned packages.
            failed_prerequisites = [item["name"] for item in checks if item["status"] != "PASS"]
            if failed_prerequisites:
                detail = "not attempted: failed verification prerequisites: " + ", ".join(
                    failed_prerequisites
                )
                _append(checks, "PDF evidence identity", False, detail)
                _append(checks, "PDF deterministic rendering", False, detail)
            else:
                try:
                    from pypdf import PdfReader
                except ModuleNotFoundError as exc:
                    if exc.name == "pypdf" or (exc.name or "").startswith("pypdf."):
                        _append(
                            checks,
                            "PDF evidence identity",
                            False,
                            "PDF verification dependencies are missing; reinstall "
                            "physics-validation-suite and run 'python -m pip check'",
                        )
                        PdfReader = None  # type: ignore[misc,assignment]
                    else:
                        raise
                if PdfReader is not None:
                    try:
                        pdf_reader = PdfReader(str(pdf_path), strict=True)
                        metadata: Any = pdf_reader.metadata or {}
                        carries_id = (
                            not pdf_reader.is_encrypted
                            and len(pdf_reader.pages) > 0
                            and metadata.get("/Subject") == evidence_id
                            and metadata.get("/Title") == f"PVS evidence - {record['case']['id']}"
                            and metadata.get("/Creator")
                            == f"Physics Validation Suite {record['pvs']['version']}"
                        )
                    except Exception:
                        carries_id = False
                    _append(
                        checks,
                        "PDF evidence identity",
                        carries_id,
                        "valid PDF structure and exact evidence metadata",
                    )
                recorded_reportlab = next(
                    (
                        item["version"]
                        for item in record["environment"]["dependencies"]
                        if item["name"] == "reportlab"
                    ),
                    None,
                )
                try:
                    current_reportlab = importlib.metadata.version("reportlab")
                except importlib.metadata.PackageNotFoundError:
                    current_reportlab = None
                pdf_renderer = (
                    render_pdf_v1
                    if envelope.get("schema") == LEGACY_EVIDENCE_SCHEMA
                    else render_pdf_v2
                    if envelope.get("schema") == V2_EVIDENCE_SCHEMA
                    else render_pdf
                )
                renderer_supported = envelope.get("schema") in {
                    LEGACY_EVIDENCE_SCHEMA,
                    V2_EVIDENCE_SCHEMA,
                    EVIDENCE_SCHEMA,
                }
                renderer_version_matches = (
                    recorded_reportlab is not None and recorded_reportlab == current_reportlab
                )
                if renderer_supported and renderer_version_matches:
                    try:
                        with tempfile.TemporaryDirectory(prefix="pvs-verify-pdf-") as directory:
                            rendered = Path(directory) / "report.pdf"
                            pdf_renderer(envelope, rendered)
                            faithful = rendered.read_bytes() == pdf_path.read_bytes()
                    except ReportError as exc:
                        faithful = False
                        rendering_detail = str(exc)
                    except Exception:
                        faithful = False
                        rendering_detail = (
                            "report bytes differ from a fresh rendering of evidence.json"
                        )
                    else:
                        rendering_detail = (
                            "report bytes match a fresh rendering of evidence.json"
                            if faithful
                            else "report bytes differ from a fresh rendering of evidence.json"
                        )
                    _append(
                        checks,
                        "PDF deterministic rendering",
                        faithful,
                        rendering_detail,
                    )
                elif renderer_supported:
                    package_identity_pinned = (
                        expect_package_id is not None and package_id == expect_package_id
                    )
                    if package_identity_pinned:
                        detail = (
                            "byte identity is bound by the exact expected Package ID; "
                            "deterministic renderer replay is unavailable because "
                            f"recorded ReportLab={recorded_reportlab!r} and "
                            f"installed ReportLab={current_reportlab!r}"
                        )
                    else:
                        detail = (
                            "deterministic renderer replay is required unless the caller "
                            "pins the exact Package ID; "
                            f"recorded ReportLab={recorded_reportlab!r} and "
                            f"installed ReportLab={current_reportlab!r}"
                        )
                    _append(
                        checks,
                        "PDF deterministic rendering",
                        package_identity_pinned,
                        detail,
                    )

    valid = all(item["status"] == "PASS" for item in checks)
    evidence_pinned = expect_evidence_id is not None and evidence_id == expect_evidence_id
    finding_pinned = expect_finding_id is not None and finding_id == expect_finding_id
    package_pinned = expect_package_id is not None and package_id == expect_package_id
    trust = "unpinned"
    if valid and package_pinned:
        trust = "package-pinned"
    elif valid and evidence_pinned:
        trust = "record-pinned"
    elif valid and finding_pinned:
        trust = "finding-pinned"
    elif not valid:
        trust = "unverified"
    verification = VerificationResult(
        target=root,
        valid=valid,
        level="package",
        evidence_id=evidence_id,
        finding_id=finding_id,
        package_id=package_id,
        checks=checks,
        evidence_status=record.get("summary", {}).get("status") if record else None,
        evidence_identity_pinned=evidence_pinned,
        finding_identity_pinned=finding_pinned,
        package_identity_pinned=package_pinned,
        trust=trust,
    )
    return _VerifiedTarget(verification, record if valid else None)


def _verify_target(
    target: str | Path,
    *,
    expect_evidence_id: str | None = None,
    expect_finding_id: str | None = None,
    expect_package_id: str | None = None,
) -> _VerifiedTarget:
    path = Path(target).resolve()
    if path.is_dir():
        with tempfile.TemporaryDirectory(prefix="pvs-package-snapshot-") as directory:
            snapshot_root = Path(directory) / "package"
            snapshot_root.mkdir()
            snapshot_errors = _snapshot_package(path, snapshot_root)
            if snapshot_errors:
                snapshot_checks = _snapshot_failure_checks(snapshot_errors)
                return _VerifiedTarget(
                    VerificationResult(path, False, "package", checks=snapshot_checks)
                )
            result = _verify_package(
                snapshot_root,
                expect_evidence_id,
                expect_finding_id,
                expect_package_id,
            )
            verification = _retarget_verification(result.verification, path, [])
            return _VerifiedTarget(verification, result.record)
    checks: list[VerificationCheck] = []
    if not path.is_file():
        _append(checks, "target present", False, "target does not exist")
        return _VerifiedTarget(VerificationResult(path, False, "record", checks=checks))
    with tempfile.TemporaryDirectory(prefix="pvs-record-snapshot-") as directory:
        snapshot_path = Path(directory) / "evidence.json"
        try:
            acquired = snapshot_file(path, snapshot_path)
            current = file_identity(path)
            snapshot_ok = current == acquired
            snapshot_detail = (
                "record bytes acquired consistently"
                if snapshot_ok
                else "record changed during snapshot acquisition"
            )
        except OSError as exc:
            snapshot_ok = False
            snapshot_detail = str(exc)
        if not snapshot_ok:
            _append(checks, "immutable verification snapshot", False, snapshot_detail)
            return _VerifiedTarget(VerificationResult(path, False, "record", checks=checks))
        evidence_id, finding_id, record = _verify_evidence_file(snapshot_path, checks)
    if expect_evidence_id is not None:
        _append(
            checks, "expected Evidence ID", evidence_id == expect_evidence_id, expect_evidence_id
        )
    if expect_finding_id is not None:
        _append(
            checks,
            "expected Scientific Finding ID",
            finding_id == expect_finding_id,
            (
                expect_finding_id
                if finding_id is not None
                else "evidence did not issue a Scientific Finding ID"
            ),
        )
    if expect_package_id is not None:
        _append(checks, "expected Package ID", False, "standalone evidence has no Package ID")
    valid = all(item["status"] == "PASS" for item in checks)
    evidence_pinned = expect_evidence_id is not None and evidence_id == expect_evidence_id
    finding_pinned = expect_finding_id is not None and finding_id == expect_finding_id
    trust = "unpinned"
    if valid and evidence_pinned:
        trust = "record-pinned"
    elif valid and finding_pinned:
        trust = "finding-pinned"
    if not valid:
        trust = "unverified"
    verification = VerificationResult(
        target=path,
        valid=valid,
        level="record",
        evidence_id=evidence_id,
        finding_id=finding_id,
        checks=checks,
        evidence_status=record.get("summary", {}).get("status") if record else None,
        evidence_identity_pinned=evidence_pinned,
        finding_identity_pinned=finding_pinned,
        trust=trust,
    )
    return _VerifiedTarget(verification, record if valid else None)


def verify_target(
    target: str | Path,
    *,
    expect_evidence_id: str | None = None,
    expect_finding_id: str | None = None,
    expect_package_id: str | None = None,
) -> VerificationResult:
    """Verify one acquired snapshot; keep the public result and JSON contract stable."""

    return _verify_target(
        target,
        expect_evidence_id=expect_evidence_id,
        expect_finding_id=expect_finding_id,
        expect_package_id=expect_package_id,
    ).verification
