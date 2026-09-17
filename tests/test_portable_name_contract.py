"""The installed package and four bootstrap paths agree on device names.

The bootstrap auditors must work before the package has been installed or its
source archive trusted. Exercise their actual code with one conformance matrix,
without creating device-named files on the host filesystem.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from pvs.errors import ArtifactError, IntegrityError
from pvs.paths import portable_relative_path

ROOT = Path(__file__).parents[1]
BOUNDARIES = ("runtime", "assembly", "rebuild", "powershell", "posix")
# Independent, explicit vectors: do not generate expectations from production's list.
RESERVED = ("COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3")
ALLOWED = (
    "COM0",
    "COM10.json",
    "LPT0",
    "LPT10.foo",
    "COM\u2074.json",
    "LPT\u2074",
    "xCOM\u00b9.json",
    "COM\u00b9x",
    "LPT\u00b2-data.csv",
    "data/temperature\u00b2.csv",
)


def _load_tool(filename: str, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "tools" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


assembly = _load_tool("assemble-release.py", "pvs_portability_assembly")
rebuild = _load_tool("check-reproducible-build.py", "pvs_portability_rebuild")


@pytest.fixture(scope="module")
def zip_auditor(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("zip-auditor")
    auditors = {}
    for name, filename, pattern in (
        ("powershell", "test-pvs.ps1", r"\$ZipAuditCode = @'\n(.*?)\n'@"),
        ("posix", "test-pvs.sh", r"<<'PY'\n(import stat\n.*?)\nPY"),
    ):
        match = re.search(pattern, (ROOT / filename).read_text(encoding="utf-8"), re.DOTALL)
        assert match is not None
        code = match.group(1)
        # Windows PowerShell 5.1 may decode BOM-less UTF-8 as its ANSI codepage.
        assert code.isascii()
        # Helpers share a directory on sys.path. A platform name such as
        # posix.py can shadow a standard-library import on another host.
        path = root / ("pvs_zip_audit_" + name + ".py")
        path.write_text(code, encoding="utf-8")
        auditors[name] = path
    return auditors


def _check(
    boundary: str,
    name: str,
    *,
    allowed: bool,
    tmp_path: Path,
    zip_auditor: dict[str, Path],
) -> None:
    if boundary == "runtime":
        for integrity, error in ((False, ArtifactError), (True, IntegrityError)):
            if allowed:
                assert portable_relative_path(name, integrity=integrity) == name
            else:
                with pytest.raises(error, match="reserved portable"):
                    portable_relative_path(name, integrity=integrity)
    elif boundary == "assembly":
        if allowed:
            assembly._portable_name(name, label="conformance")
        else:
            with pytest.raises(assembly.ReleaseAssemblyError, match="Windows-reserved"):
                assembly._portable_name(name, label="conformance")
    else:
        archive = tmp_path / "input.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(f"root/{name}", b"data")
        if boundary == "rebuild":
            # Rejection must happen before any member is materialized on Windows.
            if allowed:
                assert str(rebuild._safe_member_path(f"root/{name}")) == f"root/{name}"
            else:
                destination = tmp_path / "extract"
                with pytest.raises(rebuild.ReproducibleBuildError, match="portable"):
                    rebuild._extract_source_archive(archive, destination)
                assert list(destination.iterdir()) == []
                destination.rmdir()
        else:
            assert boundary in {"powershell", "posix"}
            result = subprocess.run(
                [sys.executable, str(zip_auditor[boundary]), str(archive), "root"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
            if allowed:
                assert result.returncode == 0, result.stderr
                assert "Safe ZIP membership" in result.stdout
            else:
                assert result.returncode != 0
                assert "non-portable ZIP member" in result.stderr


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("stem", RESERVED)
def test_superscript_device_names_are_rejected_at_every_boundary(
    boundary: str,
    stem: str,
    tmp_path: Path,
    zip_auditor: dict[str, Path],
) -> None:
    for name in (
        stem,
        f"{stem}.json",
        f"{stem.lower()}.foo",
        f"data/{stem}.tar.gz",
        f"{stem}/value.json",
        f"data/{stem.lower()}/values.csv",
    ):
        _check(boundary, name, allowed=False, tmp_path=tmp_path, zip_auditor=zip_auditor)


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("name", ALLOWED)
def test_similar_nondevice_names_remain_portable(
    boundary: str,
    name: str,
    tmp_path: Path,
    zip_auditor: dict[str, Path],
) -> None:
    _check(boundary, name, allowed=True, tmp_path=tmp_path, zip_auditor=zip_auditor)


def test_generated_auditor_names_cannot_shadow_the_standard_library(
    zip_auditor: dict[str, Path],
) -> None:
    # Includes modules unavailable on this host: Linux's built-in posix masks a
    # sibling posix.py, but Windows can import that file from posixpath instead.
    collisions = {
        path.name for path in zip_auditor.values() if path.stem in sys.stdlib_module_names
    }
    assert not collisions, f"auditor helpers shadow standard-library imports: {sorted(collisions)}"
