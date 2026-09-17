"""Fail known-invalid packages before passing report bytes to a PDF parser."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pypdf
import pytest

import pvs.verify as verify_module
from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.cli import main
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.verify import verify_target


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def _rehash_manifest(package: Path) -> dict:
    manifest = load_strict(package / "manifest.json")
    for entry in manifest["package"]["files"]:
        path = package / entry["path"]
        if path.is_file():
            entry.update(file_identity(path))
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = "pvs-package:sha256:" + digest
    _write_json(package / "manifest.json", manifest)
    return manifest


def _deny_pdf_work(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def parse(*args, **kwargs):
        calls.append("parse")
        raise RuntimeError("benign unexpected PDF parser invocation")

    def render(*args, **kwargs):
        calls.append("render")
        raise RuntimeError("benign unexpected PDF replay invocation")

    monkeypatch.setattr(pypdf, "PdfReader", parse)
    for name in ("render_pdf", "render_pdf_v1", "render_pdf_v2"):
        monkeypatch.setattr(verify_module, name, render)
    return calls


def _observe_pdf_parser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    original = pypdf.PdfReader

    def parse(*args, **kwargs):
        calls.append("parse")
        return original(*args, **kwargs)

    monkeypatch.setattr(pypdf, "PdfReader", parse)
    return calls


def _assert_pdf_not_attempted(result, prerequisite: str) -> None:
    assert result.valid is False
    assert result.trust == "unverified"
    by_name = {item["name"]: item for item in result.checks}
    assert by_name[prerequisite]["status"] == "FAIL"
    for name in ("PDF evidence identity", "PDF deterministic rendering"):
        assert by_name[name]["status"] == "FAIL"
        assert by_name[name]["detail"].startswith("not attempted:")
        assert prerequisite in by_name[name]["detail"]


@pytest.mark.parametrize(
    ("kind", "prefix", "check_name"),
    [
        ("evidence", "pvs:sha256:", "expected Evidence ID"),
        ("finding", "pvs-finding:v1:sha256:", "expected Scientific Finding ID"),
        ("package", "pvs-package:sha256:", "expected Package ID"),
    ],
)
def test_external_identity_mismatch_prevents_pdf_work(
    package_factory, monkeypatch, kind, prefix, check_name
):
    outcome, package = package_factory(html=False)
    expected = {
        "expect_evidence_id": outcome.evidence_id,
        "expect_finding_id": outcome.finding_id,
        "expect_package_id": outcome.package_id,
    }
    expected[f"expect_{kind}_id"] = prefix + "0" * 64
    calls = _deny_pdf_work(monkeypatch)

    result = verify_target(package, **expected)

    assert calls == []
    _assert_pdf_not_attempted(result, check_name)
    assert result.evidence_status == "PASS"
    assert result.evidence_id == outcome.evidence_id
    assert result.finding_id == outcome.finding_id
    assert result.package_id == outcome.package_id
    assert result.as_dict()[f"{kind}_identity_pinned"] is False


@pytest.mark.parametrize(
    "relative",
    ["report.pdf", "report.html", "case/pvs.yaml", "artifacts/output/result/result.json"],
)
@pytest.mark.parametrize("pinned", [False, True])
def test_file_digest_mismatch_prevents_pdf_work_even_with_matching_claimed_pin(
    package_factory, monkeypatch, relative, pinned
):
    outcome, package = package_factory()
    path = package / relative
    path.write_bytes(path.read_bytes() + b"\nbenign corruption\n")
    calls = _deny_pdf_work(monkeypatch)

    result = verify_target(package, expect_package_id=outcome.package_id if pinned else None)

    assert calls == []
    _assert_pdf_not_attempted(result, f"file {relative}")
    # The manifest's claimed identity still matches the pin; file verification
    # establishes that the acquired package does not have that identity.
    assert result.package_identity_pinned is pinned
    assert result.evidence_status == "PASS"


@pytest.mark.parametrize(
    ("mutation", "check_name"),
    [
        ("manifest-digest", "canonical manifest digest"),
        ("coverage", "exact package coverage"),
        ("report-role", "manifest path safety"),
        ("retained-artifact", "retained artifact result"),
        ("retained-case", "retained case bytes"),
        ("evidence-binding", "manifest/evidence identity"),
        ("evidence-digest", "canonical record digest"),
    ],
)
def test_other_invalid_bindings_prevent_pdf_work(
    package_factory, monkeypatch, mutation, check_name
):
    _, package = package_factory(html=False)
    manifest_path = package / "manifest.json"
    manifest = load_strict(manifest_path)
    if mutation == "manifest-digest":
        manifest["integrity"]["digest"] = "0" * 64
        _write_json(manifest_path, manifest)
    elif mutation == "coverage":
        (package / "extra.txt").write_text("undeclared", encoding="utf-8")
    elif mutation == "report-role":
        for entry in manifest["package"]["files"]:
            if entry["path"] == "report.pdf":
                entry["role"] = "artifact"
        _write_json(manifest_path, manifest)
        _rehash_manifest(package)
    elif mutation == "retained-artifact":
        (package / "artifacts/output/result/result.json").write_text(
            '{"value": 99}', encoding="utf-8"
        )
        _rehash_manifest(package)
    elif mutation == "retained-case":
        path = package / "case/pvs.yaml"
        path.write_bytes(path.read_bytes() + b"\n# changed retained bytes\n")
        _rehash_manifest(package)
    elif mutation == "evidence-binding":
        manifest["package"]["evidence_id"] = "pvs:sha256:" + "0" * 64
        _write_json(manifest_path, manifest)
        _rehash_manifest(package)
    else:
        envelope = load_strict(package / "evidence.json")
        envelope["record"]["subject"]["name"] = "unverified subject"
        _write_json(package / "evidence.json", envelope)
        _rehash_manifest(package)
    calls = _deny_pdf_work(monkeypatch)

    result = verify_target(package)

    assert calls == []
    _assert_pdf_not_attempted(result, check_name)


@pytest.mark.parametrize("relative", ["manifest.json", "evidence.json"])
@pytest.mark.parametrize("content", ["not JSON", '{"schema":"unsupported"}'])
def test_invalid_json_or_schema_never_reaches_pdf_parser(
    package_factory, monkeypatch, relative, content
):
    _, package = package_factory(html=False)
    (package / relative).write_text(content, encoding="utf-8")
    if relative == "evidence.json":
        _rehash_manifest(package)
    calls = _deny_pdf_work(monkeypatch)

    result = verify_target(package)

    assert calls == []
    assert result.valid is False
    assert result.trust == "unverified"
    assert any(item["status"] == "FAIL" for item in result.checks)


@pytest.mark.parametrize("pin", [None, "evidence", "finding", "package"])
def test_valid_packages_still_parse_and_replay_pdf(package_factory, monkeypatch, pin):
    outcome, package = package_factory(html=False)
    expected = {} if pin is None else {f"expect_{pin}_id": getattr(outcome, f"{pin}_id")}
    calls = _observe_pdf_parser(monkeypatch)

    result = verify_target(package, **expected)

    assert calls == ["parse"]
    assert result.valid is True
    assert (
        result.trust
        == {
            None: "unpinned",
            "evidence": "record-pinned",
            "finding": "finding-pinned",
            "package": "package-pinned",
        }[pin]
    )
    assert all(item["status"] == "PASS" for item in result.checks)


@pytest.mark.parametrize("status", ["ERROR", "FAIL", "WARN"])
def test_valid_scientific_nonpass_does_not_block_pdf_verification(
    case_factory, tmp_path, monkeypatch, status
):
    from conftest import base_case

    data = base_case()
    data["checks"] = [
        {
            "id": "observation",
            "type": "finite" if status == "ERROR" else "range",
            "source": {
                "artifact": "result",
                "pointer": "/missing" if status == "ERROR" else "/value",
                "unit": "1",
            },
            "unit": "1",
            "required": status != "WARN",
        }
    ]
    if status != "ERROR":
        data["checks"][0]["maximum"] = 0.0
    data["package"] = {"embed_artifacts": True, "html": False, "pdf": True}
    case_path, _ = case_factory(data)
    outcome = validate_case(case_path, output_dir=tmp_path / "evaluated")
    calls = _observe_pdf_parser(monkeypatch)

    result = verify_target(outcome.output_directory)

    assert calls == ["parse"]
    assert result.valid is True
    assert result.evidence_status == status
    assert result.trust == "unpinned"


@pytest.mark.parametrize(
    "fixture",
    ["synthetic-json-evidence-v1.zip", "evidence-v2-0.2.8.zip", "synthetic-json-evidence-v3.zip"],
)
def test_frozen_exact_package_pin_allows_unavailable_renderer_but_still_parses_pdf(
    tmp_path, monkeypatch, fixture
):
    with zipfile.ZipFile(Path(__file__).parent / "fixtures" / fixture) as archive:
        archive.extractall(tmp_path)
    package = next(tmp_path.rglob("manifest.json")).parent
    package_id = load_strict(package / "manifest.json")["integrity"]["package_id"]
    calls = _observe_pdf_parser(monkeypatch)
    original_version = verify_module.importlib.metadata.version

    def mismatched_version(name):
        return "0.unavailable" if name == "reportlab" else original_version(name)

    def unexpected_replay(*args, **kwargs):
        calls.append("render")
        raise RuntimeError("historical replay is unavailable")

    monkeypatch.setattr(verify_module.importlib.metadata, "version", mismatched_version)
    for name in ("render_pdf", "render_pdf_v1", "render_pdf_v2"):
        monkeypatch.setattr(verify_module, name, unexpected_replay)

    result = verify_target(package, expect_package_id=package_id)

    assert calls == ["parse"]
    assert result.valid is True
    assert result.trust == "package-pinned"
    replay = next(item for item in result.checks if item["name"] == "PDF deterministic rendering")
    assert replay["status"] == "PASS"
    assert "bound by the exact expected Package ID" in replay["detail"]


@pytest.mark.parametrize("problem", ["parser-error", "encrypted", "empty-pages", "bad-metadata"])
def test_bound_pdf_still_requires_valid_structure_and_exact_metadata(
    package_factory, monkeypatch, problem
):
    outcome, package = package_factory(html=False)
    calls = []

    def parse(*args, **kwargs):
        calls.append("parse")
        if problem == "parser-error":
            raise ValueError("benign invalid PDF structure")
        record = load_strict(package / "evidence.json")["record"]
        return SimpleNamespace(
            is_encrypted=problem == "encrypted",
            pages=[] if problem == "empty-pages" else [object()],
            metadata={
                "/Subject": "wrong" if problem == "bad-metadata" else outcome.evidence_id,
                "/Title": f"PVS evidence - {record['case']['id']}",
                "/Creator": f"Physics Validation Suite {record['pvs']['version']}",
            },
        )

    monkeypatch.setattr(pypdf, "PdfReader", parse)

    result = verify_target(package, expect_package_id=outcome.package_id)

    assert calls == ["parse"]
    assert result.valid is False
    assert result.trust == "unverified"
    identity = next(item for item in result.checks if item["name"] == "PDF evidence identity")
    assert identity["status"] == "FAIL"


def test_cli_pin_mismatch_keeps_integrity_exit_and_scientific_status(
    package_factory, monkeypatch, capsys
):
    _, package = package_factory(html=False)
    calls = _deny_pdf_work(monkeypatch)

    code = main(
        [
            "verify",
            str(package),
            "--expect-package-id",
            "pvs-package:sha256:" + "0" * 64,
            "--json",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert calls == []
    assert code == 4
    assert result["valid"] is False
    assert result["trust"] == "unverified"
    assert result["evidence_status"] == "PASS"


def test_internally_rehashed_pdf_still_reaches_parser_without_external_pin(
    package_factory, monkeypatch
):
    _, package = package_factory(html=False)
    # Benign text establishes reachability. The prerequisite is not a sandbox:
    # coherent, unpinned package hashes do not authenticate their publisher.
    (package / "report.pdf").write_bytes(b"benign invalid PDF")
    _rehash_manifest(package)
    calls = _observe_pdf_parser(monkeypatch)

    result = verify_target(package)

    assert calls == ["parse"]
    assert result.valid is False
    assert result.trust == "unverified"
    checks = {item["name"]: item for item in result.checks}
    assert checks["file report.pdf"]["status"] == "PASS"
    assert checks["PDF evidence identity"]["status"] == "FAIL"
    assert not checks["PDF evidence identity"]["detail"].startswith("not attempted:")
