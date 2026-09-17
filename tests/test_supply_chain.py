from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pvs import supply_chain
from pvs.artifacts import ResolvedArtifact
from pvs.canonical import canonical_sha256
from pvs.errors import ArtifactError, ReportError
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict
from pvs.provenance import (
    CONTENT_IDENTITY_PROFILE,
    build_provenance,
    installed_content_identity,
    installed_content_manifest,
)
from pvs.readers import ArtifactReader
from pvs.reporting import render_pdf
from pvs.supply_chain import cyclonedx_sbom, slsa_provenance, source_material_identity
from pvs.verify import verify_target


def test_installed_content_identity_binds_normalized_package_bytes(tmp_path: Path) -> None:
    package = tmp_path / "pvs"
    package.mkdir()
    (package / "alpha.py").write_text("value = 1\n", encoding="utf-8")
    data = package / "schemas" / "contract.json"
    data.parent.mkdir()
    data.write_text('{"schema":1}\n', encoding="utf-8")
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "alpha.cpython-312.pyc").write_bytes(b"derived bytecode")

    manifest = installed_content_manifest(package)
    identity = installed_content_identity(package)

    assert manifest["profile"] == CONTENT_IDENTITY_PROFILE
    assert [entry["path"] for entry in manifest["files"]] == [
        "alpha.py",
        "schemas/contract.json",
    ]
    assert identity == {
        "profile": CONTENT_IDENTITY_PROFILE,
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "digest": canonical_sha256(manifest),
        "files_count": 2,
    }

    before = identity["digest"]
    data.write_text('{"schema":2}\n', encoding="utf-8")
    assert installed_content_identity(package)["digest"] != before


def test_installed_content_identity_rejects_linked_content(
    tmp_path: Path, symlink_factory
) -> None:
    package = tmp_path / "pvs"
    package.mkdir()
    target = tmp_path / "outside.py"
    target.write_text("outside = True\n", encoding="utf-8")
    symlink_factory(package / "linked.py", target)

    with pytest.raises(OSError, match="linked file"):
        installed_content_identity(package)


def test_build_provenance_has_explicit_unknowns_in_an_editable_tree() -> None:
    value = build_provenance()

    assert value["source_identity"] is None or len(value["source_identity"]["digest"]) == 64
    assert value["source_repository"] is None or value["source_repository"].startswith(
        "https://"
    )
    assert value["source_revision"] is None or len(value["source_revision"]) in {40, 64}
    assert value["source_dirty"] is None or isinstance(value["source_dirty"], bool)
    assert value["source_date_epoch"] is None or value["source_date_epoch"] >= 0


def test_importing_core_does_not_eagerly_import_optional_or_report_stacks() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, pvs; "
            "assert 'netCDF4' not in sys.modules; "
            "assert 'pypdf' not in sys.modules; "
            "assert 'reportlab' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr


def test_missing_netcdf_extra_fails_closed_with_install_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "field.nc"
    path.write_bytes(b"not reached")
    artifact = ResolvedArtifact(
        id="field",
        root=tmp_path,
        declaration={"path": path.name, "role": "output", "format": "netcdf"},
    )
    original_import = __import__

    def blocked_import(name: str, *args, **kwargs):
        if name == "netCDF4":
            raise ImportError("blocked optional dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked_import)

    with pytest.raises(ArtifactError, match=r"physics-validation-suite\[netcdf\]"):
        ArtifactReader({"field": artifact}).select(
            {
                "artifact": "field",
                "variable": "u",
                "unit": "1",
                "netcdf_decoding": "pvs-netcdf/1",
            }
        )


def test_broken_pdf_generation_install_fails_closed_with_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(sys.modules, "pvs.reporting.pdf", raising=False)
    original_import = __import__

    def blocked_import(name: str, *args, **kwargs):
        if name == "reportlab" or name.startswith("reportlab."):
            error = ModuleNotFoundError("blocked PDF dependency")
            error.name = name
            raise error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked_import)

    with pytest.raises(ReportError, match=r"reinstall physics-validation-suite.*pip check"):
        render_pdf({}, tmp_path / "unwritten.pdf")


def test_broken_pdf_verification_install_fails_closed_with_actionable_error(
    package_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, package = package_factory()
    original_import = __import__

    def blocked_import(name: str, *args, **kwargs):
        if name == "pypdf" or name.startswith("pypdf."):
            error = ModuleNotFoundError("blocked PDF dependency")
            error.name = name
            raise error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked_import)

    result = verify_target(package)
    check = next(item for item in result.checks if item["name"] == "PDF evidence identity")
    assert result.valid is False
    assert check["status"] == "FAIL"
    assert "reinstall physics-validation-suite" in check["detail"]


def test_generated_evidence_records_implementation_and_build_identity(
    package_factory,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    pvs_record = load_strict(package / "evidence.json")["record"]["pvs"]

    assert pvs_record["content_identity"] == installed_content_identity()
    assert pvs_record["build"] == build_provenance()
    assert len(pvs_record["content_identity"]["digest"]) == 64


def test_public_and_packaged_evidence_schemas_remain_identical() -> None:
    root = Path(__file__).resolve().parents[1]
    public = json.loads((root / "schemas" / "pvs-evidence.schema.json").read_text("utf-8"))
    packaged = json.loads(
        (root / "src" / "pvs" / "schemas" / "pvs-evidence.schema.json").read_text("utf-8")
    )

    assert public == packaged


def test_cyclonedx_sbom_binds_external_subject_lock_and_installed_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = tmp_path / "physics_validation_suite-1.0.0a1-py3-none-any.whl"
    subject.write_bytes(b"external wheel bytes")
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    source = tmp_path / "source.tar"
    source.write_bytes(b"released source")
    distribution = SimpleNamespace(
        metadata={"Name": "runtime-package"},
        version="1.2.3",
        requires=[],
    )
    monkeypatch.setattr(
        supply_chain,
        "_installed_distributions",
        lambda: {"runtime-package": distribution},
    )
    monkeypatch.setattr(
        supply_chain,
        "canonical_runtime_lock_graph",
        lambda _data: {
            "runtime-package": {"version": "1.2.3", "dependencies": []}
        },
    )

    first = cyclonedx_sbom(subject, lock, source, source_date_epoch=1_700_000_000)
    second = cyclonedx_sbom(subject, lock, source, source_date_epoch=1_700_000_000)
    component = first["metadata"]["component"]

    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.6"
    assert component["hashes"] == [
        {"alg": "SHA-256", "content": file_identity(subject)["sha256"]}
    ]
    properties = {item["name"]: item["value"] for item in component["properties"]}
    assert properties["pvs:release-subject"] == subject.name
    assert properties["pvs:dependency-lock-sha256"] == file_identity(lock)["sha256"]
    assert properties["pvs:installed-content-sha256"] == installed_content_identity()["digest"]
    assert properties["pvs:source-material-sha256"] == file_identity(source)["sha256"]


def test_real_rfc8785_development_self_requirement_is_not_a_runtime_edge() -> None:
    distribution = importlib.metadata.distribution("rfc8785")

    assert any(
        requirement.startswith("rfc8785[") and 'extra == "dev"' in requirement
        for requirement in distribution.requires or []
    )
    assert "rfc8785" not in supply_chain._dependency_names(distribution)
    assert "build" not in supply_chain._dependency_names(distribution)


def test_cyclonedx_generation_rejects_an_incomplete_locked_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    lock = root / "uv.lock"
    runtime = supply_chain.canonical_runtime_lock_graph(lock.read_bytes())
    distributions = {
        name: SimpleNamespace(metadata={"Name": name}, version=package["version"], requires=[])
        for name, package in runtime.items()
        if name != "tzdata"
    }
    monkeypatch.setattr(
        supply_chain,
        "_installed_distributions",
        lambda: distributions,
    )
    subject = tmp_path / "pvs.whl"
    subject.write_bytes(b"wheel")
    source = tmp_path / "source.zip"
    source.write_bytes(b"source")

    with pytest.raises(RuntimeError, match=r"missing=\['tzdata'\]"):
        cyclonedx_sbom(
            subject,
            lock,
            source,
            source_date_epoch=1_700_000_000,
        )


def test_source_material_identity_supports_archive_and_pristine_tree(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    archive.write_bytes(b"archive")
    archive_identity = source_material_identity(archive)

    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    first = source_material_identity(source)
    second = source_material_identity(source)

    assert archive_identity == {
        "profile": "pvs-source-archive-sha256-v1",
        "algorithm": "sha256",
        "digest": file_identity(archive)["sha256"],
        "size_bytes": len(b"archive"),
    }
    assert first == second
    assert first["profile"] == "pvs-source-tree-sha256-v1"
    assert first["canonicalization"] == "RFC8785"
    assert first["files_count"] == 1


def test_slsa_statement_binds_wheel_sdist_source_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = tmp_path / "pvs.whl"
    wheel.write_bytes(b"wheel")
    sdist = tmp_path / "pvs.tar.gz"
    sdist.write_bytes(b"sdist")
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"lock")
    source = tmp_path / "source.tar"
    source.write_bytes(b"released source")
    revision = "a" * 40
    source_identity = source_material_identity(source)
    monkeypatch.setattr(
        "pvs.supply_chain.build_provenance",
        lambda: {
            "source_identity": {
                "profile": source_identity["profile"],
                "algorithm": source_identity["algorithm"],
                "digest": source_identity["digest"],
            },
            "source_repository": None,
            "source_revision": None,
            "source_dirty": False,
            "source_date_epoch": 1_700_000_000,
        },
    )

    statement = slsa_provenance(
        [sdist, wheel],
        lock,
        source,
        source_date_epoch=1_700_000_000,
        builder_id="https://example.invalid/builder",
        build_command="python -m build",
        source_repository="https://example.invalid/pvs",
        source_revision=revision,
    )

    assert statement["_type"] == "https://in-toto.io/Statement/v1"
    assert statement["predicateType"] == "https://slsa.dev/provenance/v1"
    assert statement["subject"] == [
        {"name": "pvs.tar.gz", "digest": {"sha256": file_identity(sdist)["sha256"]}},
        {"name": "pvs.whl", "digest": {"sha256": file_identity(wheel)["sha256"]}},
    ]
    materials = statement["predicate"]["buildDefinition"]["resolvedDependencies"]
    assert materials[0]["digest"] == {"sha256": file_identity(source)["sha256"]}
    assert materials[1]["digest"] == {"sha256": file_identity(lock)["sha256"]}
    assert materials[2]["digest"] == {"gitCommit": revision}


def test_slsa_statement_rejects_missing_or_mismatched_embedded_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = tmp_path / "pvs.whl"
    subject.write_bytes(b"wheel")
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"lock")
    source = tmp_path / "source.tar"
    source.write_bytes(b"source")

    for source_identity, message in (
        (None, "embedded source identity"),
        (
            {
                "profile": "pvs-source-archive-sha256-v1",
                "algorithm": "sha256",
                "digest": "f" * 64,
            },
            "does not match",
        ),
    ):
        monkeypatch.setattr(
            "pvs.supply_chain.build_provenance",
            lambda source_identity=source_identity: {
                "source_identity": source_identity,
                "source_repository": None,
                "source_revision": None,
                "source_dirty": None,
                "source_date_epoch": 1_700_000_000,
            },
        )
        with pytest.raises(RuntimeError, match=message):
            slsa_provenance(
                [subject],
                lock,
                source,
                source_date_epoch=1_700_000_000,
                builder_id="https://example.invalid/builder",
                build_command="python -m build",
            )


def test_slsa_statement_never_infers_a_public_vcs_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = tmp_path / "pvs.whl"
    subject.write_bytes(b"wheel")
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"lock")
    source = tmp_path / "source.tar"
    source.write_bytes(b"source")
    identity = source_material_identity(source)
    monkeypatch.setattr(
        "pvs.supply_chain.build_provenance",
        lambda: {
            "source_identity": {
                "profile": identity["profile"],
                "algorithm": identity["algorithm"],
                "digest": identity["digest"],
            },
            "source_repository": "https://github.com/example/unresolvable",
            "source_revision": "a" * 40,
            "source_dirty": False,
            "source_date_epoch": 1_700_000_000,
        },
    )

    statement = slsa_provenance(
        [subject],
        lock,
        source,
        source_date_epoch=1_700_000_000,
        builder_id="https://example.invalid/builder",
        build_command="python -m build",
    )

    materials = statement["predicate"]["buildDefinition"]["resolvedDependencies"]
    assert [material["uri"] for material in materials] == [
        "file:source.tar",
        "file:uv.lock",
    ]
