from __future__ import annotations

import itertools
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.package import (
    create_manifest,
    package_file_role,
    resolve_package_policy,
)
from pvs.verify import verify_target


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _set_manifest_integrity(manifest: dict[str, Any]) -> str:
    digest = canonical_sha256(manifest["package"])
    package_id = f"pvs-package:sha256:{digest}"
    manifest["integrity"]["digest"] = digest
    manifest["integrity"]["package_id"] = package_id
    return package_id


def _rewrite_manifest_inventory(package: Path, manifest: dict[str, Any]) -> None:
    manifest["package"]["files"] = [
        {
            "path": path.relative_to(package).as_posix(),
            "role": package_file_role(path.relative_to(package).as_posix()),
            **file_identity(path),
        }
        for path in sorted(package.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    _set_manifest_integrity(manifest)
    _write_json(package / "manifest.json", manifest)


def _set_evidence_integrity(envelope: dict[str, Any]) -> str:
    digest = canonical_sha256(envelope["record"])
    evidence_id = f"pvs:sha256:{digest}"
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = evidence_id
    return evidence_id


def _check(result, name: str) -> dict[str, Any]:
    return next(item for item in result.checks if item["name"] == name)


@pytest.mark.parametrize(
    ("embed", "html", "pdf"),
    list(itertools.product((False, True), repeat=3)),
)
def test_explicit_case_policy_matrix_self_verifies_and_controls_exact_contents(
    package_factory,
    embed: bool,
    html: bool,
    pdf: bool,
) -> None:
    _, package = package_factory(embed=embed, html=html, pdf=pdf)
    manifest = load_strict(package / "manifest.json")
    policy = manifest["package"]["policy"]

    assert manifest["schema"] == "pvs-manifest/3"
    assert manifest["package"]["report_profiles"] == {
        "html": "pvs-html/3",
        "pdf": "pvs-pdf/3",
    }
    assert policy == {
        "embed_artifacts": {"effective": embed, "source": "case"},
        "html": {"effective": html, "source": "case"},
        "pdf": {"effective": pdf, "source": "case"},
    }
    assert (package / "report.html").is_file() is html
    assert (package / "report.pdf").is_file() is pdf
    assert bool(list((package / "artifacts").rglob("*"))) is embed
    assert verify_target(package).valid is True


def test_absent_case_keys_are_recorded_as_schema_defaults(
    case_factory,
    tmp_path: Path,
) -> None:
    data = base_case()
    data.pop("package")
    case_path, _ = case_factory(data)

    outcome = validate_case(case_path, output_dir=tmp_path / "default-policy")
    manifest = load_strict(outcome.manifest_path)

    assert manifest["package"]["policy"] == {
        "embed_artifacts": {"effective": False, "source": "schema_default"},
        "html": {"effective": True, "source": "schema_default"},
        "pdf": {"effective": True, "source": "schema_default"},
    }
    assert verify_target(outcome.output_directory).valid is True


@pytest.mark.parametrize(
    ("embed", "html", "pdf"),
    list(itertools.product((False, True), repeat=3)),
)
def test_equal_valued_runtime_override_matrix_preserves_override_provenance(
    case_factory,
    tmp_path: Path,
    embed: bool,
    html: bool,
    pdf: bool,
) -> None:
    data = base_case()
    data["package"] = {"embed_artifacts": embed, "html": html, "pdf": pdf}
    case_path, _ = case_factory(data)

    outcome = validate_case(
        case_path,
        output_dir=tmp_path / "equal-overrides",
        embed_artifacts=embed,
        render_html_report=html,
        render_pdf_report=pdf,
    )
    policy = load_strict(outcome.manifest_path)["package"]["policy"]

    assert all(setting["source"] == "runtime_override" for setting in policy.values())
    assert {name: setting["effective"] for name, setting in policy.items()} == {
        "embed_artifacts": embed,
        "html": html,
        "pdf": pdf,
    }
    assert verify_target(outcome.output_directory).valid is True


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("embed_artifacts", 1),
        ("embed_artifacts", "true"),
        ("render_html_report", 0),
        ("render_html_report", "false"),
        ("render_pdf_report", []),
        ("render_pdf_report", 1.0),
    ],
)
def test_api_rejects_non_boolean_policy_overrides_without_publishing(
    case_factory,
    tmp_path: Path,
    argument: str,
    value: object,
) -> None:
    case_path, _ = case_factory()
    output = tmp_path / f"invalid-{argument}-{type(value).__name__}"

    with pytest.raises(TypeError, match="must be bool or None"):
        validate_case(case_path, output_dir=output, **{argument: value})  # type: ignore[arg-type]

    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.pvs-tmp-*"))


def test_policy_source_changes_package_identity_even_when_effective_values_match(
    tmp_path: Path,
) -> None:
    package = tmp_path / "manifest-input"
    package.mkdir()
    (package / "evidence.json").write_text("{}\n", encoding="utf-8")
    defaults = resolve_package_policy(None)
    equal_overrides = resolve_package_policy(
        None,
        embed_artifacts=False,
        render_html_report=True,
        render_pdf_report=True,
    )

    default_manifest = create_manifest(
        package,
        "pvs:sha256:" + "0" * 64,
        "2026-01-01T00:00:00Z",
        policy=defaults,
    )
    override_manifest = create_manifest(
        package,
        "pvs:sha256:" + "0" * 64,
        "2026-01-01T00:00:00Z",
        policy=equal_overrides,
    )

    assert {
        name: setting["effective"]
        for name, setting in default_manifest["package"]["policy"].items()
    } == {
        name: setting["effective"]
        for name, setting in override_manifest["package"]["policy"].items()
    }
    assert default_manifest["integrity"]["package_id"] != override_manifest["integrity"][
        "package_id"
    ]


def test_rehashed_manifest_cannot_remove_a_policy_required_report(
    package_factory,
) -> None:
    _, package = package_factory(embed=False, html=True, pdf=False)
    (package / "report.html").unlink()
    manifest = load_strict(package / "manifest.json")
    _rewrite_manifest_inventory(package, manifest)

    result = verify_target(package)

    assert _check(result, "canonical manifest digest")["status"] == "PASS"
    assert _check(result, "exact package coverage")["status"] == "PASS"
    assert _check(result, "package HTML report policy")["status"] == "FAIL"
    assert result.valid is False


def test_rehashed_case_sourced_policy_forgery_is_rejected_by_retained_case(
    package_factory,
) -> None:
    _, package = package_factory(embed=False, html=True, pdf=False)
    (package / "report.html").unlink()
    manifest = load_strict(package / "manifest.json")
    manifest["package"]["policy"]["html"]["effective"] = False
    _rewrite_manifest_inventory(package, manifest)

    result = verify_target(package)

    assert _check(result, "package HTML report policy")["status"] == "PASS"
    assert _check(result, "evidence/manifest contract")["status"] == "FAIL"
    assert result.valid is False


def test_rehashed_evidence_and_manifest_cannot_hide_missing_required_embedding(
    package_factory,
) -> None:
    _, package = package_factory(embed=True, html=False, pdf=False)
    evidence_path = package / "evidence.json"
    envelope = load_strict(evidence_path)
    artifact = envelope["record"]["artifacts"][0]
    embedded = package / artifact["package_path"]
    embedded.unlink()
    artifact["package_path"] = None
    evidence_id = _set_evidence_integrity(envelope)
    _write_json(evidence_path, envelope)
    manifest = load_strict(package / "manifest.json")
    manifest["package"]["evidence_id"] = evidence_id
    _rewrite_manifest_inventory(package, manifest)

    result = verify_target(package)

    assert _check(result, "canonical record digest")["status"] == "PASS"
    assert _check(result, "canonical manifest digest")["status"] == "PASS"
    assert _check(result, "exact package coverage")["status"] == "PASS"
    assert _check(result, "package artifact embedding policy")["status"] == "FAIL"
    assert result.valid is False


@pytest.mark.parametrize(
    "mutation",
    [
        {"effective": 1, "source": "case"},
        {"effective": True, "source": "invented"},
        {"effective": True, "source": "case", "extra": False},
    ],
)
def test_manifest_v2_policy_values_are_closed_and_typed(
    package_factory,
    mutation: dict[str, Any],
) -> None:
    _, package = package_factory(embed=False, html=False, pdf=False)
    manifest = load_strict(package / "manifest.json")
    manifest["package"]["policy"]["html"] = mutation
    _set_manifest_integrity(manifest)
    _write_json(package / "manifest.json", manifest)

    result = verify_target(package)

    assert _check(result, "manifest schema")["status"] == "FAIL"
    assert result.valid is False


def test_v2_evidence_cannot_be_downgraded_to_a_v1_manifest(
    package_factory,
) -> None:
    outcome, package = package_factory(embed=False, html=True, pdf=False)
    (package / "report.html").unlink()
    manifest = load_strict(package / "manifest.json")
    manifest["schema"] = "pvs-manifest/1"
    del manifest["package"]["policy"]
    del manifest["package"]["report_profiles"]
    _rewrite_manifest_inventory(package, manifest)

    result = verify_target(package, expect_evidence_id=outcome.evidence_id)

    assert _check(result, "canonical manifest digest")["status"] == "PASS"
    assert _check(result, "manifest schema")["status"] == "PASS"
    assert _check(result, "evidence/manifest contract")["status"] == "FAIL"
    assert result.valid is False


def test_runtime_override_policy_forgery_cannot_preserve_evidence_identity(
    package_factory,
) -> None:
    outcome, package = package_factory(embed=False, html=True, pdf=False)
    (package / "report.html").unlink()
    manifest = load_strict(package / "manifest.json")
    manifest["package"]["policy"]["html"] = {
        "effective": False,
        "source": "runtime_override",
    }
    _rewrite_manifest_inventory(package, manifest)

    result = verify_target(package, expect_evidence_id=outcome.evidence_id)

    assert _check(result, "package HTML report policy")["status"] == "PASS"
    assert _check(result, "evidence/manifest contract")["status"] == "FAIL"
    assert result.valid is False


def test_frozen_v1_evidence_and_manifest_remain_verifiable(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "synthetic-json-evidence-v1.zip"
    with zipfile.ZipFile(fixture) as archive:
        archive.extractall(tmp_path)
    package = tmp_path / "synthetic-json-evidence-v1"

    result = verify_target(
        package,
        expect_evidence_id=(
            "pvs:sha256:"
            "b6443ea3a2b2128f70c5a3539d01466f2b6a14c8f338e190869504a671161efd"
        ),
        expect_package_id=(
            "pvs-package:sha256:"
            "f8572abd3b7c321f1b159350f20b67f987b35d410dc3fdd7dc56f267045675f6"
        ),
    )

    assert _check(result, "evidence/manifest contract")["status"] == "PASS"
    assert result.valid is True
