from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.verify import VerificationResult, verify_target


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _set_evidence_integrity(envelope: dict[str, Any]) -> str:
    digest = canonical_sha256(envelope["record"])
    evidence_id = f"pvs:sha256:{digest}"
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = evidence_id
    return evidence_id


def _set_manifest_integrity(manifest: dict[str, Any]) -> str:
    digest = canonical_sha256(manifest["package"])
    package_id = f"pvs-package:sha256:{digest}"
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = package_id
    return package_id


def _refresh_manifest_files(package: Path, *, evidence_id: str | None = None) -> dict[str, Any]:
    """Refresh an initially valid manifest after deliberate in-package file changes."""

    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    for entry in manifest["package"]["files"]:
        target = package / entry["path"]
        if target.is_file() and not target.is_symlink():
            entry.update(file_identity(target))
    if evidence_id is not None:
        manifest["package"]["evidence_id"] = evidence_id
    _set_manifest_integrity(manifest)
    _write_json(manifest_path, manifest)
    return manifest


def _check(result: VerificationResult, name: str) -> dict[str, Any]:
    return next(item for item in result.checks if item["name"] == name)


def _make_package(
    case_factory,
    tmp_path: Path,
    *,
    name: str,
    result_text: str,
    html: bool = True,
    pdf: bool = True,
) -> tuple[Any, Path]:
    data = base_case()
    data["package"] = {"embed_artifacts": True, "html": html, "pdf": pdf}
    case_path, _ = case_factory(data, result_text=result_text, name=f"{name}-case")
    package = tmp_path / f"{name}-package"
    return validate_case(case_path, output_dir=package), package


def test_real_evidence_identity_is_independent_of_json_layout(package_factory) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    reordered = {
        "integrity": dict(reversed(list(envelope["integrity"].items()))),
        "record": dict(reversed(list(envelope["record"].items()))),
        "schema": envelope["schema"],
    }
    evidence_path.write_text(
        json.dumps(reordered, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    result = verify_target(evidence_path, expect_evidence_id=outcome.evidence_id)

    assert result.valid is True
    assert result.evidence_id == outcome.evidence_id
    assert _check(result, "canonical record digest")["status"] == "PASS"


def test_digest_and_evidence_id_are_verified_independently(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["integrity"]["evidence_id"] = "pvs:sha256:" + "0" * 64
    _write_json(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert _check(result, "canonical record digest")["status"] == "PASS"
    assert _check(result, "evidence ID")["status"] == "FAIL"


def test_rehashed_evidence_missing_nested_required_field_is_schema_invalid(
    package_factory,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    del envelope["record"]["case"]["raw_sha256"]
    _set_evidence_integrity(envelope)
    _write_json(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "FAIL"


@pytest.mark.parametrize("hostile_id", [["not", "hashable"], {"not": "a string"}, None])
def test_rehashed_malformed_check_id_fails_cleanly(
    package_factory,
    hostile_id: Any,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["record"]["checks"][0]["id"] = hostile_id
    _set_evidence_integrity(envelope)
    _write_json(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False
    assert _check(result, "evidence schema")["status"] == "FAIL"


def test_rehashed_summary_checks_total_forgery_is_detected(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["record"]["summary"]["checks_total"] += 100
    _set_evidence_integrity(envelope)
    _write_json(evidence_path, envelope)

    result = verify_target(evidence_path)

    assert result.valid is False


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.json",
        "/absolute/outside.json",
        "case/../pvs.yaml",
        "windows\\separator.json",
        "directory//file.json",
    ],
)
def test_rehashed_manifest_rejects_unsafe_declared_paths(
    package_factory,
    unsafe_path: str,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["package"]["files"][0]["path"] = unsafe_path
    _set_manifest_integrity(manifest)
    _write_json(manifest_path, manifest)

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "manifest path safety")["status"] == "FAIL"


def test_rehashed_manifest_rejects_file_role_inconsistent_with_path(package_factory) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    evidence_entry = next(
        item for item in manifest["package"]["files"] if item["path"] == "evidence.json"
    )
    evidence_entry["role"] = "artifact"
    _set_manifest_integrity(manifest)
    _write_json(manifest_path, manifest)

    result = verify_target(package)

    assert result.valid is False


def test_casefold_colliding_extra_file_is_detected_as_an_unsafe_extra(
    package_factory, case_sensitive_filesystem
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    (package / "EVIDENCE.JSON").write_bytes((package / "evidence.json").read_bytes())

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "safe package paths")["status"] == "FAIL"
    assert "path normalization collision" in _check(result, "safe package paths")["detail"]


def test_manifest_file_itself_must_not_be_a_symlink(
    package_factory, tmp_path: Path, symlink_factory
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    manifest_path = package / "manifest.json"
    external_manifest = tmp_path / "external-manifest.json"
    shutil.move(manifest_path, external_manifest)
    symlink_factory(manifest_path, external_manifest)

    result = verify_target(package)

    assert result.valid is False
    assert any("symlink" in item["detail"] for item in result.checks if item["status"] == "FAIL")


@pytest.mark.parametrize("package_path", ["../outside.json", "/tmp/outside.json"])
def test_rehashed_evidence_cannot_traverse_to_retained_artifact(
    package_factory,
    package_path: str,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    envelope["record"]["artifacts"][0]["package_path"] = package_path
    evidence_id = _set_evidence_integrity(envelope)
    _write_json(evidence_path, envelope)
    _refresh_manifest_files(package, evidence_id=evidence_id)

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "retained artifact result")["status"] == "FAIL"


def test_copying_evidence_from_another_package_breaks_manifest_binding(package_factory) -> None:
    first_outcome, first = package_factory(html=False, pdf=False, embed=False)
    second_outcome, second = package_factory(html=False, pdf=False, embed=False)
    assert first_outcome.evidence_id != second_outcome.evidence_id
    shutil.copyfile(second / "evidence.json", first / "evidence.json")
    _refresh_manifest_files(first)

    result = verify_target(first)

    assert result.valid is False
    assert result.evidence_id == second_outcome.evidence_id
    assert _check(result, "manifest/evidence identity")["status"] == "FAIL"


def test_copying_artifact_from_another_package_survives_manifest_hash_but_not_evidence_binding(
    case_factory,
    tmp_path: Path,
) -> None:
    _, first = _make_package(
        case_factory,
        tmp_path,
        name="first-artifact",
        result_text='{"value": 1.0, "values": [1.0]}\n',
        html=False,
        pdf=False,
    )
    _, second = _make_package(
        case_factory,
        tmp_path,
        name="second-artifact",
        result_text='{"value": 2.0, "values": [2.0]}\n',
        html=False,
        pdf=False,
    )
    relative = Path("artifacts/output/result/result.json")
    shutil.copyfile(second / relative, first / relative)
    _refresh_manifest_files(first)

    result = verify_target(first)

    assert _check(result, f"file {relative.as_posix()}")["status"] == "PASS"
    assert _check(result, "retained artifact result")["status"] == "FAIL"
    assert result.valid is False


def test_foreign_pdf_cannot_be_cross_bound_by_appending_expected_evidence_id(
    package_factory,
) -> None:
    first_outcome, first = package_factory(html=True, pdf=True, embed=False)
    second_outcome, second = package_factory(html=True, pdf=True, embed=False)
    assert first_outcome.evidence_id != second_outcome.evidence_id
    foreign_pdf = (second / "report.pdf").read_bytes()
    assert second_outcome.evidence_id.encode("ascii") in foreign_pdf
    assert first_outcome.evidence_id.encode("ascii") not in foreign_pdf
    (first / "report.pdf").write_bytes(
        foreign_pdf + b"\n% appended decoy " + first_outcome.evidence_id.encode("ascii") + b"\n"
    )
    _refresh_manifest_files(first)

    result = verify_target(first, expect_evidence_id=first_outcome.evidence_id)

    assert result.valid is False


def test_invalid_pdf_with_evidence_id_bytes_is_not_accepted_after_manifest_rehash(
    package_factory,
) -> None:
    outcome, package = package_factory(html=True, pdf=True, embed=False)
    (package / "report.pdf").write_bytes(
        b"this is not a PDF; claimed evidence=" + outcome.evidence_id.encode("ascii")
    )
    _refresh_manifest_files(package)

    result = verify_target(package, expect_evidence_id=outcome.evidence_id)

    assert result.valid is False
