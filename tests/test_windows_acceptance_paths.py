"""Exercise the actual pre-extraction auditor with legacy Windows path budgets.

These portable tests execute the Python helper embedded in the PowerShell runner;
no files with Windows paths need to be materialised on the current host.
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from pvs.case import load_case

SCRIPT = Path(__file__).parents[1] / "test-pvs.ps1"
SOURCE_ROOT = "physics-validation-suite-1.2.3"
LONG_MEMBER = (
    f"{SOURCE_ROOT}/benchmark-packs/plasma-physics-reference-v1/published_table_anchors.json"
)


@pytest.fixture
def archive_auditor(tmp_path: Path) -> Path:
    match = re.search(
        r"\$ZipAuditCode = @'\n(.*?)\n'@", SCRIPT.read_text(encoding="utf-8"), re.DOTALL
    )
    assert match is not None
    script = tmp_path / "archive_audit.py"
    script.write_text(match.group(1), encoding="utf-8")
    return script


def audit(
    auditor: Path, member: str, *destinations: str
) -> subprocess.CompletedProcess[str]:
    archive = auditor.parent / "source.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(member, b"reference")
    return subprocess.run(
        [sys.executable, str(auditor), str(archive), SOURCE_ROOT, *destinations],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=30,
    )


def test_legacy_development_extraction_is_rejected_before_unpacking(
    archive_auditor: Path,
) -> None:
    # Recreate the reported 263-character failure without including a user's path.
    suffix = r"\acceptance-runs\20000101T000000000Z-12345-12345678-windows\source-development"
    destination = "C:\\" + "x" * (263 - len(suffix) - len(LONG_MEMBER) - 4) + suffix
    assert len(ntpath.join(destination, *LONG_MEMBER.split("/"))) == 263
    result = audit(archive_auditor, LONG_MEMBER, destination)
    assert result.returncode != 0
    assert "263 characters" in result.stderr
    assert "-WorkRoot" in result.stderr
    assert "No archive members have been extracted" in result.stderr
    assert not (archive_auditor.parent / SOURCE_ROOT).exists()


def test_short_work_root_accepts_source_development_and_nested_builds(
    archive_auditor: Path,
) -> None:
    run = r"C:\PVS\pvs-0123456789ab"
    destinations = (
        ntpath.join(run, "source"),
        ntpath.join(run, "source-development"),
        ntpath.join(run, "b"),
        ntpath.join(run, "b", SOURCE_ROOT),
        ntpath.join(run, "t", "pvs-repro-12345678", "source-a", SOURCE_ROOT),
    )
    result = audit(archive_auditor, LONG_MEMBER, *destinations)
    assert result.returncode == 0, result.stderr
    assert "Safe ZIP membership" in result.stdout


@pytest.mark.parametrize("length", [240, 241])
def test_extraction_budget_boundary(archive_auditor: Path, length: int) -> None:
    member = f"{SOURCE_ROOT}/values.json"
    destination = "C:\\" + "x" * (length - len(member) - 4)
    assert len(ntpath.join(destination, *member.split("/"))) == length
    result = audit(archive_auditor, member, destination)
    assert (result.returncode == 0) is (length == 240)


def test_preflight_rejects_nested_build_even_if_direct_extraction_fits(
    archive_auditor: Path,
) -> None:
    destination = "C:\\" + "x" * (230 - len(LONG_MEMBER) - 4)
    direct = audit(archive_auditor, LONG_MEMBER, destination)
    assert direct.returncode == 0, direct.stderr
    nested = audit(
        archive_auditor, LONG_MEMBER, destination, ntpath.join(destination, SOURCE_ROOT)
    )
    assert nested.returncode != 0
    assert "Windows extraction/build path" in nested.stderr


def test_windows_budget_counts_utf16_code_units(archive_auditor: Path) -> None:
    member = f"{SOURCE_ROOT}/values.json"
    # Supplementary characters occupy two Windows UTF-16 units, even though
    # Python len() counts each as one code point.
    destination = "C:\\" + "x" * 125 + "\U0001f52c" * 40
    target = ntpath.join(destination, *member.split("/"))
    assert len(target) < 240 < len(target.encode("utf-16-le")) // 2
    result = audit(archive_auditor, member, destination)
    assert result.returncode != 0
    assert "maximum is 240" in result.stderr


def test_native_runner_parse_allocation_and_early_rejection(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is unavailable; native path allocation runs in Windows CI")
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        r'''param([string]$ScriptPath)
$ErrorActionPreference = "Stop"
$Tokens = $null
$Errors = $null
$Ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $ScriptPath, [ref]$Tokens, [ref]$Errors)
if ($Errors.Count -ne 0) { throw ($Errors | Out-String) }
$Function = $Ast.Find({ param($Node)
    $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $Node.Name -eq "New-AcceptanceRunDirectory"
}, $false)
Invoke-Expression $Function.Extent.Text
$Parent = [System.IO.Path]::GetTempPath()
$LongParent = Join-Path $Parent ("x" * 65)
try {
    New-AcceptanceRunDirectory -Parent $LongParent | Out-Null
    throw "Overlong parent unexpectedly accepted."
}
catch {
    if ($_.Exception.Message -notmatch "Use -WorkRoot") { throw }
    if (Test-Path -LiteralPath $LongParent) { throw "Rejected parent was created." }
}
# A deliberately short acceptance root can still give its child TEMP a longer
# path than this allocator accepts as a parent. The full harness tests that case
# through its outer -WorkRoot; do not make this probe require drive-root access.
if ((Join-Path $Parent "pvs-0123456789ab").Length -gt 64) {
    try {
        New-AcceptanceRunDirectory -Parent $Parent | Out-Null
        throw "Overlong default parent unexpectedly accepted."
    }
    catch {
        if ($_.Exception.Message -notmatch "Use -WorkRoot") { throw }
    }
    @{ allocated = $false; rejected = $true } | ConvertTo-Json -Compress
    exit 0
}
$First = $null
$Second = $null
try {
    $First = New-AcceptanceRunDirectory -Parent $Parent
    $Second = New-AcceptanceRunDirectory -Parent $Parent
    if ($First -eq $Second) { throw "Run directories collide." }
    if ($First.Length -gt 64 -or $Second.Length -gt 64) { throw "Budget exceeded." }
    if (-not (Test-Path -LiteralPath $First -PathType Container)) { throw "Missing run." }
    @{ allocated = $true; first = $First; second = $Second; rejected = $true } |
        ConvertTo-Json -Compress
}
finally {
    foreach ($Owned in @($First, $Second)) {
        if ($null -ne $Owned) { Remove-Item -LiteralPath $Owned -Force }
    }
}
''',
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(probe), str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)
    assert record["rejected"] is True
    if record["allocated"]:
        assert record["first"] != record["second"]
        assert not Path(record["first"]).exists()
        assert not Path(record["second"]).exists()
    else:
        candidate = str(Path(tempfile.gettempdir()) / "pvs-0123456789ab")
        assert len(candidate.encode("utf-16-le")) // 2 > 64
    assert Path(tempfile.gettempdir()).is_dir()


def test_shipped_catalog_fits_windows_staging_budget() -> None:
    """Catch pack-name/artifact growth that ZIP membership alone cannot cover."""
    root = SCRIPT.parent
    catalog = json.loads((root / "benchmark-packs" / "catalog.json").read_text())
    assert catalog["packs"]
    # Exercise the longest work root the Windows runner accepts. Eight-character
    # tempfile suffixes and the 32-character publication UUID are runtime layouts.
    run = "C:\\" + "r" * 61
    assert len(run) == 64
    child_temp = ntpath.join(run, "t")
    source_root = "physics_validation_suite-" + json.loads(
        subprocess.check_output(
            [sys.executable, str(root / "tools" / "release_contract.py")],
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    )["version"]
    paths = []
    for pack in catalog["packs"]:
        directory = root / "benchmark-packs" / pack["directory"]
        case = load_case(directory / "pvs.yaml")
        retained = ntpath.join(run, "benchmark-evidence", pack["id"])
        staged = ntpath.join(retained, ".evidence.pvs-tmp-XXXXXXXX")
        snapshots = ntpath.join(retained, ".evidence.pvs-validation-XXXXXXXX")
        verification = ntpath.join(child_temp, "pvs-package-snapshot-XXXXXXXX", "package")
        paths.append(ntpath.join(retained, ".pvs-publish-probe-target-" + "x" * 32))
        for index, (artifact_id, artifact) in enumerate(sorted(case.data["artifacts"].items())):
            name = Path(artifact["path"]).name
            embedded = ntpath.join("artifacts", artifact["role"], artifact_id, name)
            paths.extend([
                ntpath.join(staged, embedded),
                ntpath.join(verification, embedded),
                ntpath.join(snapshots, f"{index:04d}", name),
            ])
        for file in directory.rglob("*"):
            if not file.is_file() or "__pycache__" in file.parts:
                continue
            relative = file.relative_to(directory).parts
            paths.extend([
                ntpath.join(child_temp, "pvs-bp-XXXXXXXX", "case", *relative),
                ntpath.join(run, "source-development", source_root,
                            "benchmark-packs", pack["directory"], *relative),
                ntpath.join(child_temp, "pvs-repro-XXXXXXXX", "source-a", source_root,
                            source_root, "benchmark-packs", pack["directory"], *relative),
            ])
    longest_length, longest_path = max(
        (len(path.encode("utf-16-le")) // 2, path) for path in paths
    )
    assert longest_length <= 240, (
        f"Shipped benchmark staging exceeds the Windows budget: "
        f"{longest_length} UTF-16 units at {longest_path}"
    )
