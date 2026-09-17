from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

import pvs.paths as paths_module
import pvs.verify as verify_module
from pvs.canonical import canonical_sha256
from pvs.errors import IntegrityError
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.package import create_manifest, normalized_package_path
from pvs.verify import verify_target


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _refresh_manifest(package: Path) -> dict:
    """Refresh declared file identities and the manifest's own JCS identity."""

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
    return manifest


def _check(result, name: str) -> dict:
    return next(item for item in result.checks if item["name"] == name)


def test_complete_generated_package_verifies_and_manifest_has_exact_coverage(
    package_factory,
) -> None:
    outcome, package = package_factory()
    result = verify_target(package)
    manifest = load_strict(package / "manifest.json")

    assert result.valid is True
    assert result.level == "package"
    assert result.evidence_id == outcome.evidence_id
    assert result.package_id == outcome.package_id
    assert all(item["status"] == "PASS" for item in result.checks)

    declared = [entry["path"] for entry in manifest["package"]["files"]]
    actual = sorted(
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    assert declared == sorted(declared)
    assert declared == actual
    assert "manifest.json" not in declared
    assert {entry["role"] for entry in manifest["package"]["files"]} >= {
        "case",
        "evidence",
        "report",
        "artifact",
    }


def test_v2_pdf_replay_does_not_require_producer_source_date_epoch(
    package_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1000000000")
    _, package = package_factory()
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "2000000000")

    result = verify_target(package)

    assert result.valid is True
    assert _check(result, "PDF deterministic rendering")["status"] == "PASS"


def test_standalone_verifier_rejects_live_file_swap_after_snapshot_acquisition(
    package_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_outcome, first_package = package_factory(html=False, pdf=False, embed=False)
    _, second_package = package_factory(html=False, pdf=False, embed=False)
    target = (first_package / "evidence.json").resolve()
    replacement = tmp_path / "replacement-evidence.json"
    shutil.copyfile(second_package / "evidence.json", replacement)
    replacement_bytes = replacement.read_bytes()
    original_snapshot = verify_module.snapshot_file
    swapped = False

    def snapshot_then_swap(source: Path, destination: Path) -> dict:
        nonlocal swapped
        acquired = original_snapshot(source, destination)
        if source.resolve() == target and not swapped:
            replacement.replace(source)
            swapped = True
        return acquired

    monkeypatch.setattr(verify_module, "snapshot_file", snapshot_then_swap)

    result = verify_target(target, expect_evidence_id=first_outcome.evidence_id)

    assert swapped is True
    assert target.read_bytes() == replacement_bytes
    assert result.valid is False
    assert result.evidence_id is None
    assert _check(result, "immutable verification snapshot")["status"] == "FAIL"
    assert (
        "changed during snapshot acquisition"
        in _check(result, "immutable verification snapshot")["detail"]
    )


def test_package_verifier_rejects_declared_file_swap_after_snapshot_acquisition(
    package_factory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, first_package = package_factory(html=False, pdf=False, embed=False)
    _, second_package = package_factory(html=False, pdf=False, embed=False)
    target = (first_package / "evidence.json").resolve()
    replacement = tmp_path / "replacement-package-evidence.json"
    shutil.copyfile(second_package / "evidence.json", replacement)
    original_snapshot = verify_module.snapshot_file
    swapped = False

    def snapshot_then_swap(source: Path, destination: Path) -> dict:
        nonlocal swapped
        acquired = original_snapshot(source, destination)
        if source.resolve() == target and not swapped:
            replacement.replace(source)
            swapped = True
        return acquired

    monkeypatch.setattr(verify_module, "snapshot_file", snapshot_then_swap)

    result = verify_target(first_package)

    assert swapped is True
    assert result.valid is False
    assert result.evidence_id is None
    assert _check(result, "immutable verification snapshot")["status"] == "FAIL"
    assert (
        "file changed during snapshot acquisition: evidence.json"
        in _check(result, "immutable verification snapshot")["detail"]
    )


def test_expected_ids_accept_exact_values_and_reject_mismatch(package_factory) -> None:
    outcome, package = package_factory()

    accepted = verify_target(
        package,
        expect_evidence_id=outcome.evidence_id,
        expect_package_id=outcome.package_id,
    )
    wrong_evidence = verify_target(
        package,
        expect_evidence_id="pvs:sha256:" + "0" * 64,
    )
    wrong_package = verify_target(
        package,
        expect_package_id="pvs-package:sha256:" + "0" * 64,
    )

    assert accepted.valid is True
    assert wrong_evidence.valid is False
    assert _check(wrong_evidence, "expected Evidence ID")["status"] == "FAIL"
    assert wrong_package.valid is False
    assert _check(wrong_package, "expected Package ID")["status"] == "FAIL"


def test_rehashed_v2_manifest_cannot_change_evidence_completion_timestamp(
    package_factory,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["package"]["created_at"] = "2030-01-01T00:00:00Z"
    _write_json(manifest_path, manifest)
    _refresh_manifest(package)

    result = verify_target(package)

    assert result.valid is False
    contract = _check(result, "evidence/manifest contract")
    assert contract["status"] == "FAIL"
    assert "completion timestamp" in contract["detail"]


@pytest.mark.parametrize(
    "relative",
    [
        "evidence.json",
        "report.pdf",
        "report.html",
        "case/pvs.yaml",
        "artifacts/output/result/result.json",
    ],
)
def test_one_byte_tampering_of_any_material_package_file_is_detected(
    package_factory, relative: str
) -> None:
    _, package = package_factory()
    target = package / relative
    target.write_bytes(target.read_bytes() + b"X")

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, f"file {relative}")["status"] == "FAIL"


@pytest.mark.parametrize(
    "relative",
    [
        "evidence.json",
        "report.pdf",
        "artifacts/output/result/result.json",
    ],
)
def test_missing_declared_file_is_detected(package_factory, relative: str) -> None:
    _, package = package_factory()
    (package / relative).unlink()

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "exact package coverage")["status"] == "FAIL"
    assert _check(result, f"file {relative}")["detail"] == "missing"


def test_extra_regular_file_is_detected(package_factory) -> None:
    _, package = package_factory()
    (package / "unmanifested.bin").write_bytes(b"not declared")

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "exact package coverage")["status"] == "FAIL"


def test_extra_symlink_is_detected(package_factory, tmp_path: Path, symlink_factory) -> None:
    _, package = package_factory()
    external = tmp_path / "external.txt"
    external.write_text("external", encoding="utf-8")
    symlink_factory(package / "unmanifested-link", external)

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "safe package paths")["status"] == "FAIL"
    assert "symlink" in _check(result, "safe package paths")["detail"]


def test_declared_file_replaced_by_symlink_is_detected(
    package_factory, tmp_path: Path, symlink_factory
) -> None:
    _, package = package_factory()
    target = package / "report.html"
    external = tmp_path / "external-report.html"
    shutil.copyfile(target, external)
    target.unlink()
    symlink_factory(target, external)

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "safe package paths")["status"] == "FAIL"
    assert _check(result, "exact package coverage")["status"] == "FAIL"


def test_manifest_content_tampering_is_detected(package_factory) -> None:
    _, package = package_factory()
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["package"]["created_at"] = "2030-01-01T00:00:00Z"
    _write_json(manifest_path, manifest)

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "canonical manifest digest")["status"] == "FAIL"


def test_rehashed_manifest_cannot_hide_retained_artifact_tampering(package_factory) -> None:
    _, package = package_factory()
    artifact = package / "artifacts/output/result/result.json"
    artifact.write_text('{"value": 999}\n', encoding="utf-8")
    _refresh_manifest(package)

    result = verify_target(package)

    assert _check(result, f"file {artifact.relative_to(package).as_posix()}")["status"] == "PASS"
    assert _check(result, "retained artifact result")["status"] == "FAIL"
    assert result.valid is False


def test_rehashed_manifest_cannot_hide_retained_case_tampering(package_factory) -> None:
    _, package = package_factory()
    case_path = package / "case/pvs.yaml"
    case_path.write_text(case_path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    _refresh_manifest(package)

    result = verify_target(package)

    assert _check(result, "file case/pvs.yaml")["status"] == "PASS"
    assert _check(result, "retained case bytes")["status"] == "FAIL"
    assert result.valid is False


def test_rehashed_manifest_cannot_hide_html_evidence_id_substitution(package_factory) -> None:
    outcome, package = package_factory()
    html = package / "report.html"
    text = html.read_text(encoding="utf-8")
    assert outcome.evidence_id in text
    html.write_text(text.replace(outcome.evidence_id, "pvs:sha256:" + "0" * 64), encoding="utf-8")
    _refresh_manifest(package)

    result = verify_target(package)

    assert _check(result, "file report.html")["status"] == "PASS"
    assert _check(result, "HTML evidence identity")["status"] == "FAIL"
    rendering = _check(result, "HTML deterministic rendering")
    assert rendering["status"] == "FAIL"
    assert rendering["detail"] == "report bytes differ from a fresh rendering of evidence.json"
    assert result.valid is False


def test_rehashed_manifest_cannot_hide_pdf_evidence_id_substitution(package_factory) -> None:
    outcome, package = package_factory()
    pdf = package / "report.pdf"
    content = pdf.read_bytes()
    original = outcome.evidence_id.encode("ascii")
    assert original in content
    pdf.write_bytes(content.replace(original, ("pvs:sha256:" + "0" * 64).encode("ascii")))
    _refresh_manifest(package)

    result = verify_target(package)

    assert _check(result, "file report.pdf")["status"] == "PASS"
    assert _check(result, "PDF evidence identity")["status"] == "FAIL"
    rendering = _check(result, "PDF deterministic rendering")
    assert rendering["status"] == "FAIL"
    assert rendering["detail"] == "report bytes differ from a fresh rendering of evidence.json"
    assert result.valid is False


def _force_reportlab_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    real_version = verify_module.importlib.metadata.version

    def mismatched_version(name: str) -> str:
        if name == "reportlab":
            return "0.invalid-verifier-version"
        return real_version(name)

    monkeypatch.setattr(verify_module.importlib.metadata, "version", mismatched_version)


def _tamper_pdf_and_refresh_manifest(package: Path) -> dict:
    pdf = package / "report.pdf"
    # A trailing PDF comment preserves the parsed identity metadata while making
    # the file non-faithful to the deterministic evidence renderer.
    pdf.write_bytes(pdf.read_bytes() + b"\n% adversarial report content\n")
    return _refresh_manifest(package)


def test_pdf_replay_version_mismatch_fails_with_only_evidence_id_pinned(
    package_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome, package = package_factory()
    _tamper_pdf_and_refresh_manifest(package)
    _force_reportlab_version_mismatch(monkeypatch)

    result = verify_target(package, expect_evidence_id=outcome.evidence_id)

    assert _check(result, "PDF evidence identity")["status"] == "PASS"
    replay = _check(result, "PDF deterministic rendering")
    assert replay["status"] == "FAIL"
    assert "pins the exact Package ID" in replay["detail"]
    assert result.valid is False
    assert result.trust == "unverified"


def test_exact_package_pin_binds_pdf_when_renderer_replay_is_unavailable(
    package_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, package = package_factory()
    manifest = _tamper_pdf_and_refresh_manifest(package)
    expected_package_id = manifest["integrity"]["package_id"]
    _force_reportlab_version_mismatch(monkeypatch)

    result = verify_target(package, expect_package_id=expected_package_id)

    replay = _check(result, "PDF deterministic rendering")
    assert replay["status"] == "PASS"
    assert "bound by the exact expected Package ID" in replay["detail"]
    assert result.valid is True
    assert result.trust == "package-pinned"


def test_v1_pdf_replay_version_mismatch_obeys_the_same_fail_closed_rule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "synthetic-json-evidence-v1.zip"
    with zipfile.ZipFile(fixture) as archive:
        archive.extractall(tmp_path)
    package = tmp_path / "synthetic-json-evidence-v1"
    evidence_id = (
        "pvs:sha256:"
        "b6443ea3a2b2128f70c5a3539d01466f2b6a14c8f338e190869504a671161efd"
    )
    manifest = _tamper_pdf_and_refresh_manifest(package)
    expected_package_id = manifest["integrity"]["package_id"]
    _force_reportlab_version_mismatch(monkeypatch)

    evidence_pinned = verify_target(package, expect_evidence_id=evidence_id)
    package_pinned = verify_target(package, expect_package_id=expected_package_id)

    assert _check(evidence_pinned, "PDF deterministic rendering")["status"] == "FAIL"
    assert evidence_pinned.valid is False
    replay = _check(package_pinned, "PDF deterministic rendering")
    assert replay["status"] == "PASS"
    assert "bound by the exact expected Package ID" in replay["detail"]
    assert package_pinned.valid is True


def test_rehashed_manifest_evidence_binding_mismatch_is_detected(package_factory) -> None:
    _, package = package_factory()
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    manifest["package"]["evidence_id"] = "pvs:sha256:" + "0" * 64
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{digest}"
    _write_json(manifest_path, manifest)

    result = verify_target(package)

    assert _check(result, "canonical manifest digest")["status"] == "PASS"
    assert _check(result, "package ID")["status"] == "PASS"
    assert _check(result, "manifest/evidence identity")["status"] == "FAIL"
    assert result.valid is False


def test_duplicate_manifest_path_is_detected_even_when_manifest_is_rehashed(
    package_factory,
) -> None:
    _, package = package_factory()
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    duplicate = dict(manifest["package"]["files"][0])
    duplicate["path"] = duplicate["path"].swapcase()
    manifest["package"]["files"].append(duplicate)
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{digest}"
    _write_json(manifest_path, manifest)

    result = verify_target(package)

    assert _check(result, "canonical manifest digest")["status"] == "PASS"
    assert _check(result, "manifest path uniqueness")["status"] == "FAIL"
    assert result.valid is False


@pytest.mark.parametrize(
    "unsafe",
    [
        "/absolute/file",
        "../escape",
        "directory/../../escape",
        "windows\\separator",
        "nul\x00byte",
        "directory/./file",
        "directory//file",
        "directory/",
        "C:drive-relative",
        "file:alternate-stream",
        "NUL.txt",
        "directory/COM1.log",
        "trailing-dot.",
        "trailing-space ",
        "invalid?.txt",
        "control\x01.txt",
    ],
)
def test_package_path_normalizer_rejects_noncanonical_or_unsafe_paths(unsafe: str) -> None:
    with pytest.raises(IntegrityError):
        normalized_package_path(unsafe)


def test_package_path_normalizer_accepts_canonical_relative_posix_path() -> None:
    assert normalized_package_path("artifacts/output/result.json") == "artifacts/output/result.json"


def test_manifest_creation_rejects_symlinked_files(tmp_path: Path, symlink_factory) -> None:
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    package = tmp_path / "package"
    package.mkdir()
    symlink_factory(package / "linked", external)

    with pytest.raises(IntegrityError, match="must not be symlinks"):
        create_manifest(package, "pvs:sha256:" + "0" * 64, "2026-01-01T00:00:00Z")


def test_manifest_creation_rejects_casefold_path_collision(
    tmp_path: Path, case_sensitive_filesystem
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "Result.txt").write_text("one", encoding="utf-8")
    (package / "result.txt").write_text("two", encoding="utf-8")

    with pytest.raises(IntegrityError, match="duplicate package path after normalization"):
        create_manifest(package, "pvs:sha256:" + "0" * 64, "2026-01-01T00:00:00Z")


def test_manifest_creation_fails_closed_when_a_subdirectory_cannot_be_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "package"
    hidden = package / "hidden"
    hidden.mkdir(parents=True)
    (hidden / "undeclared.bin").write_bytes(b"must not be silently omitted")
    real_scandir = paths_module.os.scandir

    def guarded_scandir(path):
        if Path(path) == hidden:
            raise PermissionError("deliberate scan denial")
        return real_scandir(path)

    monkeypatch.setattr(paths_module.os, "scandir", guarded_scandir)

    with pytest.raises(IntegrityError, match="could not safely enumerate package"):
        create_manifest(package, "pvs:sha256:" + "0" * 64, "2026-01-01T00:00:00Z")


def test_verification_fails_closed_when_a_subdirectory_cannot_be_scanned(
    package_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, package = package_factory()
    blocked = package / "case"
    real_scandir = paths_module.os.scandir

    def guarded_scandir(path):
        if not isinstance(path, int) and Path(path) == blocked:
            raise PermissionError("deliberate scan denial")
        return real_scandir(path)

    monkeypatch.setattr(paths_module.os, "scandir", guarded_scandir)

    result = verify_target(package)

    assert result.valid is False
    assert _check(result, "immutable verification snapshot")["status"] == "FAIL"
    assert "could not enumerate package" in _check(
        result, "immutable verification snapshot"
    )["detail"]


def test_missing_or_malformed_package_fails_cleanly(tmp_path: Path) -> None:
    missing = verify_target(tmp_path / "missing")
    assert missing.valid is False
    assert missing.level == "record"
    assert _check(missing, "target present")["status"] == "FAIL"

    package = tmp_path / "package"
    package.mkdir()
    no_manifest = verify_target(package)
    assert no_manifest.valid is False
    assert _check(no_manifest, "manifest present")["status"] == "FAIL"

    (package / "manifest.json").write_text('{"schema":NaN}', encoding="utf-8")
    malformed = verify_target(package)
    assert malformed.valid is False
    assert _check(malformed, "strict manifest JSON")["status"] == "FAIL"
