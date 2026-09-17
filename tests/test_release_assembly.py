from __future__ import annotations

import copy
import errno
import hashlib
import importlib.util
import io
import json
import stat
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import rfc8785
from test_sdist_contents import relative_readme_links

from pvs import supply_chain

VERSION = "1.0.0a1"
EPOCH = 1787918400
BUILDER_ID = "https://build.example.invalid/pvs/v0.2"
BUILD_COMMAND = "python -m build"


def _load_release_module() -> ModuleType:
    path = Path(__file__).parents[1] / "tools" / "assemble-release.py"
    spec = importlib.util.spec_from_file_location("pvs_release_assembly", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_release_module()


def test_windows_reparse_metadata_is_link_like() -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )
    assert release._stat_is_link_or_reparse(metadata)


def _digest(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def _write_wheel(
    path: Path,
    *,
    version: str = VERSION,
    unsafe_name: str | None = None,
    package_bytes: bytes = b"",
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"physics_validation_suite-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: physics-validation-suite\nVersion: {version}\n\n",
        )
        archive.writestr("pvs/__init__.py", package_bytes)
        if unsafe_name is not None:
            archive.writestr(unsafe_name, b"unsafe")


def _write_sdist(path: Path, *, version: str = VERSION) -> None:
    top = f"physics_validation_suite-{version}"
    package_info = (
        f"Metadata-Version: 2.4\nName: physics-validation-suite\nVersion: {version}\n\n"
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(f"{top}/PKG-INFO")
        member.size = len(package_info)
        archive.addfile(member, io.BytesIO(package_info))


def _sbom(
    subject: Path,
    *,
    source_archive: Path,
    lock: Path,
    installed_digest: str,
) -> dict[str, Any]:
    subject_data = subject.read_bytes()
    root_ref = "pkg:pypi/physics-validation-suite@1.0.0a1"
    runtime = supply_chain.canonical_runtime_lock_graph(lock.read_bytes())
    ref_by_name = {
        name: f"pkg:pypi/{name}@{package['version']}"
        for name, package in runtime.items()
    }
    component_refs = sorted(ref_by_name.values())
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": release._release_timestamp(EPOCH),
            "component": {
                "type": "application",
                "bom-ref": root_ref,
                "group": "Chatwood Labs Ltd",
                "name": "physics-validation-suite",
                "version": VERSION,
                "purl": root_ref,
                "hashes": [{"alg": "SHA-256", "content": hashlib.sha256(subject_data).hexdigest()}],
                "properties": [
                    {"name": "pvs:release-subject", "value": subject.name},
                    {
                        "name": "pvs:release-subject-size-bytes",
                        "value": str(len(subject_data)),
                    },
                    {
                        "name": "pvs:installed-content-profile",
                        "value": "pvs-installed-package-tree-sha256-v1",
                    },
                    {"name": "pvs:installed-content-sha256", "value": installed_digest},
                    {"name": "pvs:dependency-lock", "value": lock.name},
                    {
                        "name": "pvs:dependency-lock-sha256",
                        "value": hashlib.sha256(lock.read_bytes()).hexdigest(),
                    },
                    {"name": "pvs:source-material", "value": source_archive.name},
                    {
                        "name": "pvs:source-material-profile",
                        "value": "pvs-source-archive-sha256-v1",
                    },
                    {
                        "name": "pvs:source-material-sha256",
                        "value": hashlib.sha256(source_archive.read_bytes()).hexdigest(),
                    },
                ],
            },
        },
        "components": [
            {
                "type": "library",
                "bom-ref": ref_by_name[name],
                "name": name,
                "version": package["version"],
                "purl": ref_by_name[name],
            }
            for name, package in sorted(
                runtime.items(),
                key=lambda item: ref_by_name[item[0]],
            )
        ],
        "dependencies": [
            {"ref": root_ref, "dependsOn": component_refs},
            *(
                {
                    "ref": ref_by_name[name],
                    "dependsOn": sorted(
                        ref_by_name[dependency]
                        for dependency in package["dependencies"]
                    ),
                }
                for name, package in sorted(
                    runtime.items(),
                    key=lambda item: ref_by_name[item[0]],
                )
            ),
        ],
    }


def _provenance(wheel: Path, sdist: Path, source_archive: Path, lock: Path) -> dict[str, Any]:
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": subject.name,
                "digest": {"sha256": hashlib.sha256(subject.read_bytes()).hexdigest()},
            }
            for subject in sorted((wheel, sdist), key=lambda item: item.name)
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://chatwoodlabs.com/buildtypes/pvs-python-distribution/v1",
                "externalParameters": {
                    "sourceDateEpoch": EPOCH,
                    "buildCommand": BUILD_COMMAND,
                },
                "internalParameters": {},
                "resolvedDependencies": [
                    {
                        "uri": f"file:{source_archive.name}",
                        "digest": {
                            "sha256": hashlib.sha256(source_archive.read_bytes()).hexdigest()
                        },
                        "annotations": {"pvs:identityProfile": "pvs-source-archive-sha256-v1"},
                    },
                    {
                        "uri": f"file:{lock.name}",
                        "digest": {"sha256": hashlib.sha256(lock.read_bytes()).hexdigest()},
                    },
                ],
            },
            "runDetails": {"builder": {"id": BUILDER_ID}},
        },
    }


@dataclass
class ReleaseFixture:
    inputs: Any
    finding: dict[str, Any]
    installed_identity: dict[str, Any]

    def finding_factory(self, record: dict[str, Any]) -> dict[str, Any]:
        assert record["pvs"]["version"] == VERSION
        return copy.deepcopy(self.finding)

    def reproducibility_checker(
        self,
        source_tree: Path,
        source_archive: Path,
        dist_dir: Path,
        source_date_epoch: int,
    ) -> None:
        assert source_tree == self.inputs.source_tree.resolve()
        assert source_archive.name == self.inputs.source_archive.name
        assert dist_dir == self.inputs.dist_dir.resolve()
        assert source_date_epoch == EPOCH

    def sbom_reproducer(
        self,
        subject: Path,
        lock: Path,
        source_archive: Path,
        source_date_epoch: int,
    ) -> dict[str, Any]:
        assert source_date_epoch == EPOCH
        return _sbom(
            subject,
            source_archive=source_archive,
            lock=lock,
            installed_digest=self.installed_identity["digest"],
        )


def _make_release_fixture(tmp_path: Path) -> ReleaseFixture:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "physics-validation-suite"\nversion = "1.0.0a1"\n',
        encoding="utf-8",
    )
    (source / "README.md").write_text("# PVS 1.0.0a1\n", encoding="utf-8")
    (source / "BUNDLE_README.md").write_bytes(
        (Path(__file__).parents[1] / "BUNDLE_README.md").read_bytes()
    )
    (source / "RELEASE_NOTES_1.0.0a1.md").write_text(
        "# PVS 1.0.0a1 release notes\n", encoding="utf-8"
    )
    (source / "test-pvs.ps1").write_text("# PVS 1.0.0a1\n", encoding="utf-8")
    (source / "test-pvs.sh").write_text("#!/bin/sh\n# PVS 1.0.0a1\n", encoding="utf-8")
    lock = source / "uv.lock"
    runtime_names = sorted(release.REQUIRED_SBOM_RUNTIME_ROOTS | {"attrs"})
    root_dependencies = "".join(f'    {{ name = "{name}" }},\n' for name in runtime_names)
    lock.write_text(
        "version = 1\nrevision = 3\n\n"
        "[[package]]\n"
        'name = "physics-validation-suite"\n'
        'version = "1.0.0a1"\n'
        "dependencies = [\n"
        f"{root_dependencies}"
        "]\n\n"
        "[package.optional-dependencies]\n"
        "all = []\n"
        + "".join(
            f'\n[[package]]\nname = "{name}"\nversion = "1.0.0"\n'
            + ('dependencies = [{ name = "attrs" }]\n' if name == "jsonschema" else "")
            for name in runtime_names
        ),
        encoding="utf-8",
    )
    (source / "kept.txt").write_text("kept\n", encoding="utf-8")
    (source / "src" / "pvs").mkdir(parents=True)
    (source / "src" / "pvs" / "__init__.py").write_bytes(b"")
    benchmark_case = b"schema: pvs-case/2\n"
    benchmark_root = source / "benchmark-packs" / "plasma-physics-reference-v1"
    benchmark_root.mkdir(parents=True)
    (benchmark_root / "pvs.yaml").write_bytes(benchmark_case)
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("not released\n", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "junk.pyc").write_bytes(b"junk")

    dist = source / "dist"
    dist.mkdir()
    wheel = dist / "physics_validation_suite-1.0.0a1-py3-none-any.whl"
    sdist = dist / "physics_validation_suite-1.0.0a1.tar.gz"
    _write_wheel(wheel)
    _write_sdist(sdist)
    source_archive = tmp_path / "source-input" / "physics-validation-suite-1.0.0a1-source.zip"
    release.create_source_archive(
        source,
        source_archive,
        version=VERSION,
        source_date_epoch=EPOCH,
    )

    sample = tmp_path / "sample-plasma-evidence-v1.0.0a1"
    (sample / "case").mkdir(parents=True)
    installed_identity = release._installed_content_identity_from_wheel(wheel.read_bytes())
    record = {
        "pvs": {
            "version": VERSION,
            "content_identity": installed_identity,
            "build": {
                "source_identity": {
                    "profile": "pvs-source-archive-sha256-v1",
                    "algorithm": "sha256",
                    "digest": hashlib.sha256(source_archive.read_bytes()).hexdigest(),
                },
                "source_repository": None,
                "source_revision": None,
                "source_dirty": None,
                "source_date_epoch": EPOCH,
            },
        },
        "checks": [{"status": "PASS"}],
        "scientific_result": {"value": 1.25},
    }
    finding_digest = _digest({"test_projection": record})
    finding = {
        "projection": "pvs-finding-projection/1",
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "status": "ISSUED",
        "digest": finding_digest,
        "finding_id": f"pvs-finding:v1:sha256:{finding_digest}",
    }
    evidence_digest = _digest(record)
    evidence_id = f"pvs:sha256:{evidence_digest}"
    evidence = {
        "schema": "pvs-evidence/3",
        "record": record,
        "integrity": {
            "canonicalization": "RFC8785",
            "algorithm": "sha256",
            "digest": evidence_digest,
            "evidence_id": evidence_id,
            "finding": finding,
        },
    }
    sample_files = {
        "case/pvs.yaml": benchmark_case,
        "evidence.json": _json_bytes(evidence),
        "report.html": b"<!doctype html><title>PVS 1.0.0a1</title>\n",
        "report.pdf": b"%PDF-1.4\n% deterministic fixture\n",
    }
    for name, data in sample_files.items():
        path = sample / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest_files = [
        {
            "path": name,
            "role": "fixture",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
        for name, data in sorted(sample_files.items())
    ]
    package = {
        "evidence_id": evidence_id,
        "created_at": "2026-08-28T12:00:00Z",
        "policy": {},
        "report_profiles": {},
        "files": manifest_files,
    }
    package_digest = _digest(package)
    manifest = {
        "schema": "pvs-manifest/3",
        "package": package,
        "integrity": {
            "canonicalization": "RFC8785",
            "algorithm": "sha256",
            "digest": package_digest,
            "package_id": f"pvs-package:sha256:{package_digest}",
        },
    }
    (sample / "manifest.json").write_bytes(_json_bytes(manifest))

    wheel_sbom = tmp_path / f"{wheel.name}.cdx.json"
    wheel_sbom.write_bytes(
        _json_bytes(
            _sbom(
                wheel,
                source_archive=source_archive,
                lock=lock,
                installed_digest=installed_identity["digest"],
            )
        )
    )
    sdist_sbom = tmp_path / f"{sdist.name}.cdx.json"
    sdist_sbom.write_bytes(
        _json_bytes(
            _sbom(
                sdist,
                source_archive=source_archive,
                lock=lock,
                installed_digest=installed_identity["digest"],
            )
        )
    )
    provenance = tmp_path / "pvs-1.0.0a1.provenance.intoto.json"
    provenance.write_bytes(_json_bytes(_provenance(wheel, sdist, source_archive, lock)))
    sample_verification = tmp_path / "sample-plasma-verification.json"
    sample_verification.write_bytes(
        _json_bytes(
            {
                "target": str(sample.resolve()),
                "valid": True,
                "level": "package",
                "evidence_id": evidence_id,
                "finding_id": finding["finding_id"],
                "package_id": manifest["integrity"]["package_id"],
                "evidence_status": "PASS",
                "evidence_identity_pinned": True,
                "finding_identity_pinned": True,
                "package_identity_pinned": True,
                "trust": "package-pinned",
                "checks": [
                    {
                        "name": "fixture verification",
                        "status": "PASS",
                        "detail": "verified",
                    }
                ],
            }
        )
    )

    inputs = release.ReleaseInputs(
        version=VERSION,
        source_tree=source,
        source_archive=source_archive,
        dist_dir=dist,
        wheel=wheel,
        sdist=sdist,
        sample_package=sample,
        sample_verification=sample_verification,
        readme=source / "BUNDLE_README.md",
        release_notes=source / "RELEASE_NOTES_1.0.0a1.md",
        powershell_script=source / "test-pvs.ps1",
        shell_script=source / "test-pvs.sh",
        sboms=(wheel_sbom, sdist_sbom),
        provenance=provenance,
        builder_id=BUILDER_ID,
        build_command=BUILD_COMMAND,
        output_dir=tmp_path / "release-one",
    )
    return ReleaseFixture(inputs, finding, installed_identity)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_source_version_validation_accepts_portable_line_endings(
    tmp_path: Path,
    newline: bytes,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source.joinpath("pyproject.toml").write_bytes(
        newline.join(
            [
                b"[project]",
                b'name = "physics-validation-suite"',
                b'version = "1.0.0a1"',
                b"",
            ]
        )
    )

    release._validate_source_version(source, VERSION)


def test_assembly_is_reproducible_and_has_exact_coverage(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    first = release.assemble_release(
        fixture.inputs,
        source_date_epoch=EPOCH,
        finding_metadata_factory=fixture.finding_factory,
        reproducibility_checker=fixture.reproducibility_checker,
        sbom_reproducer=fixture.sbom_reproducer,
    )
    second_inputs = release.ReleaseInputs(
        **{
            **fixture.inputs.__dict__,
            "output_dir": tmp_path / "release-two",
        }
    )
    second = release.assemble_release(
        second_inputs,
        source_date_epoch=EPOCH,
        finding_metadata_factory=fixture.finding_factory,
        reproducibility_checker=fixture.reproducibility_checker,
        sbom_reproducer=fixture.sbom_reproducer,
    )

    assert set(first) == set(second)
    for name in first:
        assert first[name].read_bytes() == second[name].read_bytes(), name

    bundle = first["pvs-1.0.0a1-release-bundle.zip"]
    bundle_checksum = first["pvs-1.0.0a1-release-bundle.zip.sha256"].read_text(
        encoding="ascii"
    )
    assert bundle_checksum == f"{hashlib.sha256(bundle.read_bytes()).hexdigest()}  {bundle.name}\n"
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert "SHA256SUMS" in names
        assert "pvs-1.0.0a1-release-bundle.zip" not in names
        assert "pvs-1.0.0a1-release-bundle.zip.sha256" not in names
        assert "test-pvs-windows-hotfix.ps1" not in names
        assert archive.read("test-pvs.ps1") == fixture.inputs.powershell_script.read_bytes()
        readme = archive.read("README.md")
        assert readme == fixture.inputs.readme.read_bytes()
        assert readme != (fixture.inputs.source_tree / "README.md").read_bytes()
        assert relative_readme_links(readme.decode()) <= set(names)
        checksums = archive.read("SHA256SUMS").decode().splitlines()
        covered = [line.split("  ", 1)[1] for line in checksums]
        assert covered == sorted(set(names) - {"SHA256SUMS"})
        for line in checksums:
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest

    with zipfile.ZipFile(first["physics-validation-suite-1.0.0a1-source.zip"]) as archive:
        names = archive.namelist()
        assert all(name.startswith("physics_validation_suite-1.0.0a1/") for name in names)
        assert "physics_validation_suite-1.0.0a1/kept.txt" in names
        assert not any("/.git/" in name or "/__pycache__/" in name for name in names)
        assert not any("/dist/" in name for name in names)

    metadata = json.loads(first["RELEASE_METADATA.json"].read_text(encoding="utf-8"))
    assert metadata["version"] == VERSION
    assert metadata["release_bundle"] == bundle.name
    assert metadata["sample"]["finding_id"] == fixture.finding["finding_id"]
    assert metadata["artifacts"]["sboms"] == sorted(path.name for path in fixture.inputs.sboms)
    verification = json.loads(first["sample-plasma-verification.json"].read_text())
    assert verification["target"] == "sample-plasma-evidence-v1.0.0a1"


def test_assembly_requires_the_source_bound_bundle_guide(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    inputs = release.ReleaseInputs(**{
        **fixture.inputs.__dict__, "readme": fixture.inputs.source_tree / "README.md"
    })
    with pytest.raises(release.ReleaseAssemblyError, match="bundle README must be the pristine"):
        release.assemble_release(
            inputs, source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_two_phase_source_archive_is_revalidated_and_reused(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    source_archive = tmp_path / "prebuilt" / "physics-validation-suite-1.0.0a1-source.zip"
    release.create_source_archive(
        fixture.inputs.source_tree,
        source_archive,
        version=VERSION,
        source_date_epoch=EPOCH,
    )
    inputs = release.ReleaseInputs(
        **{
            **fixture.inputs.__dict__,
            "source_archive": source_archive,
        }
    )
    published = release.assemble_release(
        inputs,
        source_date_epoch=EPOCH,
        finding_metadata_factory=fixture.finding_factory,
        reproducibility_checker=fixture.reproducibility_checker,
        sbom_reproducer=fixture.sbom_reproducer,
    )
    assert published[source_archive.name].read_bytes() == source_archive.read_bytes()

    source_archive.write_bytes(source_archive.read_bytes() + b"trailing bytes")
    tampered_inputs = release.ReleaseInputs(
        **{
            **inputs.__dict__,
            "output_dir": tmp_path / "release-after-source-tamper",
        }
    )
    with pytest.raises(release.ReleaseAssemblyError):
        release.assemble_release(
            tampered_inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_tampered_sample_file_is_rejected(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    (fixture.inputs.sample_package / "report.pdf").write_bytes(b"tampered")
    with pytest.raises(release.ReleaseAssemblyError, match="manifest identity mismatch"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_forged_finding_metadata_is_rejected(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    evidence_path = fixture.inputs.sample_package / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["integrity"]["finding"]["digest"] = "0" * 64
    evidence["integrity"]["finding"]["finding_id"] = f"pvs-finding:v1:sha256:{'0' * 64}"
    evidence_bytes = _json_bytes(evidence)
    evidence_path.write_bytes(evidence_bytes)
    manifest_path = fixture.inputs.sample_package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["package"]["files"] if item["path"] == "evidence.json")
    entry["sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
    entry["size_bytes"] = len(evidence_bytes)
    package_digest = _digest(manifest["package"])
    manifest["integrity"]["digest"] = package_digest
    manifest["integrity"]["package_id"] = f"pvs-package:sha256:{package_digest}"
    manifest_path.write_bytes(_json_bytes(manifest))
    with pytest.raises(release.ReleaseAssemblyError, match="Finding metadata"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_unpinned_sample_verification_is_rejected(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    path = fixture.inputs.sample_verification
    verification = json.loads(path.read_text(encoding="utf-8"))
    verification["finding_identity_pinned"] = False
    path.write_bytes(_json_bytes(verification))
    with pytest.raises(release.ReleaseAssemblyError, match="finding_identity_pinned"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


@pytest.mark.parametrize("mutation", ["change", "remove", "invent"])
def test_sample_verification_must_match_exact_verifier_output(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _make_release_fixture(tmp_path)
    path = fixture.inputs.sample_verification
    expected = json.loads(path.read_text(encoding="utf-8"))
    supplied = copy.deepcopy(expected)
    if mutation == "change":
        supplied["checks"][0]["detail"] = "invented detail"
    elif mutation == "remove":
        supplied["checks"] = []
    elif mutation == "invent":
        supplied["checks"].append(
            {"name": "invented gate", "status": "PASS", "detail": "not executed"}
        )
    else:  # pragma: no cover - parametrization is closed above.
        raise AssertionError(mutation)
    path.write_bytes(_json_bytes(supplied))
    evidence = json.loads(
        (fixture.inputs.sample_package / "evidence.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (fixture.inputs.sample_package / "manifest.json").read_text(encoding="utf-8")
    )
    identity = release.SampleIdentity(
        evidence["integrity"]["evidence_id"],
        evidence["integrity"]["finding"]["finding_id"],
        manifest["integrity"]["package_id"],
    )
    with pytest.raises(
        release.ReleaseAssemblyError,
        match=r"verification (?:must contain|does not exactly match)",
    ):
        release._validate_sample_verification(
            path,
            expected_name="sample-plasma-verification.json",
            sample_directory_name="sample-plasma-evidence-v1.0.0a1",
            identity=identity,
            expected_verification=expected,
        )


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("spec", "CycloneDX 1.6"),
        ("version", "root component version"),
        ("subject_hash", "root component hashes"),
        ("subject_size", "properties do not bind"),
        ("lock_hash", "properties do not bind"),
        ("source_profile", "properties do not bind"),
        ("source_hash", "properties do not bind"),
        ("installed_profile", "properties do not bind"),
        ("installed_hash", "properties do not bind"),
        ("timestamp", "SOURCE_DATE_EPOCH"),
        ("empty_components", "does not exactly cover"),
        ("component_purl", "invalid package identity"),
        ("component_version", "absent from the canonical runtime lock closure"),
        ("dependency_graph", "canonical runtime lock graph"),
    ],
)
def test_cyclonedx_semantic_tampering_is_rejected(
    tmp_path: Path,
    tamper: str,
    message: str,
) -> None:
    fixture = _make_release_fixture(tmp_path)
    path = fixture.inputs.sboms[0]
    document = json.loads(path.read_text(encoding="utf-8"))
    component = document["metadata"]["component"]
    properties = {item["name"]: item for item in component["properties"]}
    if tamper == "spec":
        document["specVersion"] = "1.5"
    elif tamper == "version":
        component["version"] = "0.1.0"
    elif tamper == "subject_hash":
        component["hashes"][0]["content"] = "0" * 64
    elif tamper == "subject_size":
        properties["pvs:release-subject-size-bytes"]["value"] = "1"
    elif tamper == "lock_hash":
        properties["pvs:dependency-lock-sha256"]["value"] = "0" * 64
    elif tamper == "source_profile":
        properties["pvs:source-material-profile"]["value"] = "pvs-source-tree-sha256-v1"
    elif tamper == "source_hash":
        properties["pvs:source-material-sha256"]["value"] = "0" * 64
    elif tamper == "installed_profile":
        properties["pvs:installed-content-profile"]["value"] = "invented-profile"
    elif tamper == "installed_hash":
        properties["pvs:installed-content-sha256"]["value"] = "0" * 64
    elif tamper == "timestamp":
        document["metadata"]["timestamp"] = "2000-01-01T00:00:00Z"
    elif tamper == "empty_components":
        document["components"] = []
        document["dependencies"] = [{"ref": component["bom-ref"], "dependsOn": []}]
    elif tamper == "component_purl":
        document["components"][0]["purl"] = "pkg:pypi/invented@1.0.0"
    elif tamper == "component_version":
        item = document["components"][0]
        item["version"] = "9.9.9"
        name = item["name"]
        item["bom-ref"] = f"pkg:pypi/{name}@9.9.9"
        item["purl"] = item["bom-ref"]
    elif tamper == "dependency_graph":
        item = next(
            dependency
            for dependency in document["dependencies"]
            if dependency["ref"] == "pkg:pypi/jsonschema@1.0.0"
        )
        item["dependsOn"] = []
    else:  # pragma: no cover - parametrization is closed above.
        raise AssertionError(tamper)
    path.write_bytes(_json_bytes(document))

    with pytest.raises(release.ReleaseAssemblyError, match=message):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_generated_full_lock_sbom_round_trips_release_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    lock = tmp_path / "uv.lock"
    lock.write_bytes((root / "uv.lock").read_bytes())
    runtime = supply_chain.canonical_runtime_lock_graph(lock.read_bytes())
    distributions = {
        name: SimpleNamespace(metadata={"Name": name}, version=package["version"], requires=[])
        for name, package in runtime.items()
    }
    installed_identity = {
        "profile": release.CONTENT_IDENTITY_PROFILE,
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "digest": "a" * 64,
        "files_count": 1,
    }
    monkeypatch.setattr(
        supply_chain,
        "_installed_distributions",
        lambda: distributions,
    )
    monkeypatch.setattr(
        supply_chain,
        "installed_content_identity",
        lambda: installed_identity,
    )
    subject = tmp_path / "physics_validation_suite-1.0.0a1-py3-none-any.whl"
    subject.write_bytes(b"released wheel")
    source = tmp_path / "physics-validation-suite-1.0.0a1-source.zip"
    source.write_bytes(b"released source")
    value = supply_chain.cyclonedx_sbom(
        subject,
        lock,
        source,
        source_date_epoch=EPOCH,
    )
    path = tmp_path / f"{subject.name}.cdx.json"
    path.write_bytes(_json_bytes(value))

    component_refs = [component["bom-ref"] for component in value["components"]]
    dependency_refs = [dependency["ref"] for dependency in value["dependencies"]]
    assert component_refs == sorted(component_refs)
    assert dependency_refs == [value["metadata"]["component"]["bom-ref"], *component_refs]
    assert component_refs.index("pkg:pypi/jsonschema-specifications@2025.9.1") < (
        component_refs.index("pkg:pypi/jsonschema@4.26.0")
    )
    rfc_dependency = next(
        item for item in value["dependencies"] if item["ref"] == "pkg:pypi/rfc8785@0.1.4"
    )
    assert rfc_dependency["dependsOn"] == []

    validated = release._validate_cyclonedx_sbom(
        path,
        subject_name=subject.name,
        subject_data=subject.read_bytes(),
        version=VERSION,
        source_archive_name=source.name,
        source_archive_data=source.read_bytes(),
        lock_name=lock.name,
        lock_data=lock.read_bytes(),
        installed_content_identity=installed_identity,
        source_date_epoch=EPOCH,
    )
    assert validated.data == path.read_bytes()


def test_sbom_rejects_internally_consistent_but_incomplete_runtime_closure(
    tmp_path: Path,
) -> None:
    fixture = _make_release_fixture(tmp_path)
    path = fixture.inputs.sboms[0]
    document = json.loads(path.read_text(encoding="utf-8"))
    omitted = sorted(release.REQUIRED_SBOM_RUNTIME_ROOTS)[-1]
    omitted_ref = f"pkg:pypi/{omitted}@1.0.0"
    document["components"] = [
        item for item in document["components"] if item["bom-ref"] != omitted_ref
    ]
    document["dependencies"] = [
        {
            **item,
            "dependsOn": [edge for edge in item["dependsOn"] if edge != omitted_ref],
        }
        for item in document["dependencies"]
        if item["ref"] != omitted_ref
    ]
    path.write_bytes(_json_bytes(document))

    with pytest.raises(release.ReleaseAssemblyError, match="does not exactly cover"):
        release._validate_cyclonedx_sbom(
            path,
            subject_name=fixture.inputs.wheel.name,
            subject_data=fixture.inputs.wheel.read_bytes(),
            version=VERSION,
            source_archive_name=fixture.inputs.source_archive.name,
            source_archive_data=fixture.inputs.source_archive.read_bytes(),
            lock_name="uv.lock",
            lock_data=(fixture.inputs.source_tree / "uv.lock").read_bytes(),
            installed_content_identity=fixture.installed_identity,
            source_date_epoch=EPOCH,
        )


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("subject_hash", "subjects"),
        ("missing_subject", "subjects"),
        ("source_hash", "materials"),
        ("source_profile", "materials"),
        ("lock_hash", "materials"),
        ("epoch", "external parameters"),
        ("command", "external parameters"),
        ("builder", "builder identity"),
        ("signature", "unexpected members"),
    ],
)
def test_slsa_semantic_tampering_is_rejected(
    tmp_path: Path,
    tamper: str,
    message: str,
) -> None:
    fixture = _make_release_fixture(tmp_path)
    path = fixture.inputs.provenance
    document = json.loads(path.read_text(encoding="utf-8"))
    definition = document["predicate"]["buildDefinition"]
    if tamper == "subject_hash":
        document["subject"][0]["digest"]["sha256"] = "0" * 64
    elif tamper == "missing_subject":
        document["subject"].pop()
    elif tamper == "source_hash":
        definition["resolvedDependencies"][0]["digest"]["sha256"] = "0" * 64
    elif tamper == "source_profile":
        definition["resolvedDependencies"][0]["annotations"]["pvs:identityProfile"] = (
            "pvs-source-tree-sha256-v1"
        )
    elif tamper == "lock_hash":
        definition["resolvedDependencies"][1]["digest"]["sha256"] = "0" * 64
    elif tamper == "epoch":
        definition["externalParameters"]["sourceDateEpoch"] += 1
    elif tamper == "command":
        definition["externalParameters"]["buildCommand"] = "python -m build --wheel"
    elif tamper == "builder":
        document["predicate"]["runDetails"]["builder"]["id"] = "attacker"
    elif tamper == "signature":
        document["signatures"] = [{"keyid": "not-supported", "sig": "forged"}]
    else:  # pragma: no cover - parametrization is closed above.
        raise AssertionError(tamper)
    path.write_bytes(_json_bytes(document))

    with pytest.raises(release.ReleaseAssemblyError, match=message):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_duplicate_or_wrong_subject_sbom_set_is_rejected(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    inputs = release.ReleaseInputs(
        **{
            **fixture.inputs.__dict__,
            "sboms": (
                fixture.inputs.sboms[0],
                fixture.inputs.sboms[0],
                fixture.inputs.sboms[1],
            ),
        }
    )
    with pytest.raises(release.ReleaseAssemblyError, match="cover the wheel and sdist exactly"):
        release.assemble_release(
            inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_source_symlink_is_rejected(tmp_path: Path, symlink_factory) -> None:
    fixture = _make_release_fixture(tmp_path)
    link = fixture.inputs.source_tree / "linked.txt"
    symlink_factory(link, fixture.inputs.source_tree / "kept.txt")
    with pytest.raises(release.ReleaseAssemblyError, match="contains a symlink"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_portable_casefold_collision_is_rejected(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    lower = fixture.inputs.source_tree / "collision.txt"
    upper = fixture.inputs.source_tree / "COLLISION.TXT"
    lower.write_text("lower", encoding="utf-8")
    upper.write_text("upper", encoding="utf-8")
    if lower.samefile(upper):
        pytest.skip("filesystem is case-insensitive")
    with pytest.raises(release.ReleaseAssemblyError, match="collide portably"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


@pytest.mark.parametrize("name", ["question?.txt", "CON.txt", "cafe\u0301.txt"])
def test_nonportable_source_name_policy_is_rejected(name: str) -> None:
    with pytest.raises(
        release.ReleaseAssemblyError,
        match=r"non-portable|non-canonical|Windows-reserved",
    ):
        release._portable_name(name, label="source tree")


@pytest.mark.parametrize("name", ["question?.txt", "CON.txt", "cafe\u0301.txt"])
def test_nonportable_source_tree_entry_is_rejected(tmp_path: Path, name: str) -> None:
    fixture = _make_release_fixture(tmp_path)
    path = fixture.inputs.source_tree / name
    try:
        path.write_text("not portable", encoding="utf-8")
    except OSError as exc:
        if sys.platform == "win32" and (
            exc.errno == errno.EINVAL or getattr(exc, "winerror", None) == 123
        ):
            pytest.skip(f"host cannot create non-portable-name fixture: {exc}")
        raise

    with pytest.raises(
        release.ReleaseAssemblyError,
        match=r"non-portable|non-canonical|Windows-reserved",
    ):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_unsafe_wheel_member_is_rejected(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    _write_wheel(fixture.inputs.wheel, unsafe_name="../escape")
    with pytest.raises(release.ReleaseAssemblyError, match="wheel path"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_wheel_member_validation_uses_raw_archive_names() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        info = zipfile.ZipInfo("placeholder")
        info.filename = "pvs\\module.py"
        info.orig_filename = info.filename
        archive.writestr(info, b"unsafe")

    with (
        zipfile.ZipFile(io.BytesIO(payload.getvalue())) as archive,
        pytest.raises(release.ReleaseAssemblyError, match="unsafe wheel path"),
    ):
        release._validate_zip_member_names(archive, label="wheel")


def test_wheel_runtime_bytes_must_derive_from_published_source(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    _write_wheel(fixture.inputs.wheel, package_bytes=b"tampered runtime\n")

    with pytest.raises(release.ReleaseAssemblyError, match="wheel PVS payload bytes"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_release_assembly_requires_the_reproducibility_gate(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)

    def failed_gate(
        source_tree: Path,
        source_archive: Path,
        dist_dir: Path,
        source_date_epoch: int,
    ) -> None:
        del source_tree, source_archive, dist_dir, source_date_epoch
        raise release.ReleaseAssemblyError("independent pristine rebuild failed")

    with pytest.raises(release.ReleaseAssemblyError, match="pristine rebuild failed"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=failed_gate,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("content", "content identity does not match"),
        ("build", "build identity does not match"),
        ("case", "not the exact audited plasma benchmark"),
        ("checks", "only passing checks"),
    ],
)
def test_release_sample_is_bound_to_released_pvs_and_audited_benchmark(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    fixture = _make_release_fixture(tmp_path)
    evidence_path = fixture.inputs.sample_package / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if mutation == "content":
        evidence["record"]["pvs"]["content_identity"]["digest"] = "0" * 64
        evidence_path.write_bytes(_json_bytes(evidence))
    elif mutation == "build":
        evidence["record"]["pvs"]["build"]["source_identity"]["digest"] = "0" * 64
        evidence_path.write_bytes(_json_bytes(evidence))
    elif mutation == "case":
        (fixture.inputs.sample_package / "case" / "pvs.yaml").write_text(
            "schema: pvs-case/2\nid: trivial\n",
            encoding="utf-8",
        )
    elif mutation == "checks":
        evidence["record"]["checks"][0]["status"] = "FAIL"
        evidence_path.write_bytes(_json_bytes(evidence))
    else:  # pragma: no cover - parametrization is closed above.
        raise AssertionError(mutation)

    with pytest.raises(release.ReleaseAssemblyError, match=message):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_release_assembly_rejects_sbom_not_reproduced_from_installed_closure(
    tmp_path: Path,
) -> None:
    fixture = _make_release_fixture(tmp_path)

    def different_closure(
        subject: Path,
        lock: Path,
        source_archive: Path,
        source_date_epoch: int,
    ) -> dict[str, Any]:
        result = fixture.sbom_reproducer(
            subject,
            lock,
            source_archive,
            source_date_epoch,
        )
        result["metadata"]["component"]["group"] = "invented closure"
        return result

    with pytest.raises(release.ReleaseAssemblyError, match="does not exactly match"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=different_closure,
        )
def test_wrong_source_version_is_rejected(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    (fixture.inputs.source_tree / "pyproject.toml").write_text(
        '[project]\nname = "physics-validation-suite"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    with pytest.raises(release.ReleaseAssemblyError, match="source project version"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


@pytest.mark.parametrize("epoch", [0, 4354819200])
def test_non_zip_source_date_epoch_is_rejected(tmp_path: Path, epoch: int) -> None:
    fixture = _make_release_fixture(tmp_path)
    with pytest.raises(release.ReleaseAssemblyError, match="ZIP-compatible"):
        release.assemble_release(
            fixture.inputs,
            source_date_epoch=epoch,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )


def test_source_date_epoch_environment_is_mandatory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    with pytest.raises(release.ReleaseAssemblyError, match="must be set"):
        release._source_date_epoch_from_environment()
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(EPOCH))
    assert release._source_date_epoch_from_environment() == EPOCH


def test_output_must_be_outside_source_tree(tmp_path: Path) -> None:
    fixture = _make_release_fixture(tmp_path)
    inputs = release.ReleaseInputs(
        **{
            **fixture.inputs.__dict__,
            "output_dir": fixture.inputs.source_tree / "release-output",
        }
    )
    with pytest.raises(release.ReleaseAssemblyError, match="outside the pristine source"):
        release.assemble_release(
            inputs,
            source_date_epoch=EPOCH,
            finding_metadata_factory=fixture.finding_factory,
            reproducibility_checker=fixture.reproducibility_checker,
            sbom_reproducer=fixture.sbom_reproducer,
        )
