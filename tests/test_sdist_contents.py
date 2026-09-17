"""Inspect the real distribution, not just files present in the checkout."""

from __future__ import annotations

import email.parser
import json
import os
import posixpath
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from markdown_it import MarkdownIt
from packaging.requirements import Requirement
from packaging.version import Version

from pvs.hashing import file_identity
from pvs.verify import verify_target
from pvs.version import __version__

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).parents[1]


def relative_readme_links(markdown: str) -> set[str]:
    """Resolve CommonMark inline/reference links and images, ignoring remote URLs."""
    paths = set()
    for token in MarkdownIt("commonmark").parse(markdown):
        for child in token.children or ():
            target = child.attrGet("href") if child.type == "link_open" else child.attrGet("src")
            if target is None:
                continue
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            path = posixpath.normpath(unquote(url.path))
            assert not path.startswith(("/", "../")) and path != "..", target
            paths.add(path)
    return paths


@pytest.fixture(scope="module")
def built_sdist() -> Iterator[Path]:
    # Use a short disposable root even when the checkout / pytest root is deep
    # on Windows. The build never writes egg-info or dist files into the checkout.
    with tempfile.TemporaryDirectory(prefix="pvs-sdist-") as folder:
        root = Path(folder)
        source_zip = root / f"physics-validation-suite-{__version__}-source.zip"
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PVS_BUILD_", "PVS_SOURCE_"))
        }
        env.update(SOURCE_DATE_EPOCH="1788825600", PYTHONUTF8="1")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/assemble-release.py"),
                "source",
                "--version",
                __version__,
                "--source-tree",
                str(ROOT),
                "--output",
                str(source_zip),
            ],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        with zipfile.ZipFile(source_zip) as archive:
            archive.extractall(root / "source")
        source = root / "source" / f"physics_validation_suite-{__version__}"
        env["PVS_BUILD_SOURCE_ARCHIVE"] = str(source_zip)
        result = subprocess.run(
            [sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", str(root)],
            cwd=source,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        yield root / f"physics_validation_suite-{__version__}.tar.gz"


@pytest.mark.distribution
def test_every_relative_readme_file_link_resolves_in_built_sdist(built_sdist: Path) -> None:
    with tarfile.open(built_sdist, "r:gz") as archive:
        members = {member.name for member in archive.getmembers() if member.isfile()}
        root = f"physics_validation_suite-{__version__}"
        stream = archive.extractfile(f"{root}/README.md")
        assert stream is not None
        links = relative_readme_links(stream.read().decode("utf-8"))
        assert "docs/user-guide.md" in links
        missing = sorted(path for path in links if f"{root}/{path}" not in members)
        assert not missing, f"README links missing from built sdist: {missing}"


@pytest.mark.distribution
def test_built_sdist_can_execute_the_release_consistency_gate(built_sdist: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="pvs-sdist-gate-") as folder:
        with tarfile.open(built_sdist, "r:gz") as archive:
            # This archive was built above from our source. Use the explicit
            # data filter where available (including maintained Python 3.10).
            if hasattr(tarfile, "data_filter"):
                archive.extractall(folder, filter="data")
            else:  # Older Python 3.10 patch releases lack extraction filters.
                archive.extractall(folder)
        source = Path(folder) / f"physics_validation_suite-{__version__}"
        result = subprocess.run(
            [sys.executable, "tools/release_contract.py", "--check"],
            cwd=source, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (source / ".github/workflows/ci.yml").read_bytes() == (
            ROOT / ".github/workflows/ci.yml"
        ).read_bytes()
        assert (source / "BUNDLE_README.md").read_bytes() == (
            ROOT / "BUNDLE_README.md"
        ).read_bytes()


@pytest.mark.distribution
def test_built_sdist_retains_patched_pdf_dependency(built_sdist: Path) -> None:
    with tarfile.open(built_sdist, "r:gz") as archive:
        stream = archive.extractfile(f"physics_validation_suite-{__version__}/PKG-INFO")
        assert stream is not None
        metadata = email.parser.BytesParser().parsebytes(stream.read())
    parsers = [
        requirement
        for value in metadata.get_all("Requires-Dist", [])
        if (requirement := Requirement(value)).name == "pypdf"
    ]
    assert len(parsers) == 1
    parser = parsers[0]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = [
        requirement
        for value in project["project"]["dependencies"]
        if (requirement := Requirement(value)).name == "pypdf"
    ]
    assert parsers == declared
    assert parser.marker is None
    assert Version("6.18.0") not in parser.specifier
    assert Version("6.18.1") in parser.specifier
    assert Version("7.0.0") not in parser.specifier


@pytest.mark.distribution
def test_built_sdist_retains_fixture_regeneration_inputs(built_sdist: Path) -> None:
    fixture_root = ROOT / "tests/fixtures"
    inputs = {
        fixture_root / "README.md",
        fixture_root / "generate_synthetic_compatibility.py",
        *(path for path in (fixture_root / "synthetic-compatibility").rglob("*")
          if path.is_file()),
    }
    assert fixture_root / "synthetic-compatibility/pvs.yaml" in inputs
    with tarfile.open(built_sdist, "r:gz") as archive:
        members = {member.name for member in archive.getmembers() if member.isfile()}
        for source_path in sorted(inputs):
            relative = source_path.relative_to(ROOT).as_posix()
            name = f"physics_validation_suite-{__version__}/{relative}"
            assert name in members, f"Fixture regeneration input missing from sdist: {relative}"
            stream = archive.extractfile(name)
            assert stream is not None
            assert stream.read() == source_path.read_bytes(), relative


@pytest.mark.distribution
def test_built_sdist_can_regenerate_and_verify_compatibility_fixtures(
    built_sdist: Path,
) -> None:
    # Exercise the documented workflow from the distribution in a disposable
    # directory, using the same qualified interpreter/dependencies as this test.
    # Never overwrite the frozen historical fixture packages in the checkout.
    with tempfile.TemporaryDirectory(prefix="pvs-fixtures-") as folder:
        root = Path(folder)
        with tarfile.open(built_sdist, "r:gz") as archive:
            if hasattr(tarfile, "data_filter"):
                archive.extractall(root / "source", filter="data")
            else:
                archive.extractall(root / "source")
        source = root / "source" / f"physics_validation_suite-{__version__}"
        output = root / "generated"
        result = subprocess.run(
            [
                sys.executable,
                "tests/fixtures/generate_synthetic_compatibility.py",
                "--output",
                str(output),
            ],
            cwd=source, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        inventory = json.loads(
            (output / "synthetic-compatibility-identities.json").read_text(encoding="utf-8")
        )
        assert inventory["pvs_version"] == __version__
        assert inventory["generator"] == file_identity(
            source / "tests/fixtures/generate_synthetic_compatibility.py"
        )
        assert {entry["path"] for entry in inventory["fixtures"]} == {
            "synthetic-json-evidence-v1.zip",
            "synthetic-json-evidence-v3.zip",
        }
        for entry in inventory["fixtures"]:
            package = output / entry["path"]
            identity = file_identity(package)
            assert entry["sha256"] == identity["sha256"]
            assert entry["size_bytes"] == identity["size_bytes"]
            with zipfile.ZipFile(package) as archive:
                archive.extractall(root / "verify")
            verification = verify_target(root / "verify" / package.stem)
            assert verification.valid, verification.checks
            assert entry["evidence_id"] == verification.evidence_id
            assert entry["package_id"] == verification.package_id


def test_readme_link_check_uses_markdown_semantics() -> None:
    assert relative_readme_links(
        "[notes](RELEASE_NOTES_9.8.7.md) [section](docs/contract.md#section)\n"
        "[guide][ref] ![image](docs/plot.svg) [web](https://example.org/page)\n"
        "[self](#heading) [space](<docs/a b.md>) ` [code](missing.md) `\n"
        '\n[ref]: docs/guide.md "Guide"\n'
        "\n```python\ntext = '[example](also-missing.md)'\n```\n"
    ) == {
        "RELEASE_NOTES_9.8.7.md",
        "docs/contract.md",
        "docs/guide.md",
        "docs/plot.svg",
        "docs/a b.md",
    }
