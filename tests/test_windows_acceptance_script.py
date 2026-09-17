from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "test-pvs.ps1"
POSIX_SCRIPT = ROOT / "test-pvs.sh"
RELEASE_ASSEMBLER = ROOT / "tools" / "assemble-release.py"


def test_windows_harness_never_transports_python_source_with_dash_c() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    # Windows PowerShell 5.1 reconstructs native command lines and can strip
    # embedded double quotes from a Python ``-c`` argument. Keep Python source
    # in UTF-8 helper files instead.
    assert re.search(r"(?<![A-Za-z0-9])-c(?![A-Za-z0-9])", text) is None


def test_windows_harness_routes_multiline_python_through_helper_files() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "[System.IO.File]::WriteAllText($SnippetPath, $Code, $Utf8WithoutBom)" in text
    assert "& $Executable @PrefixArguments $ProbePath" in text
    for variable in ("ZipAuditCode", "EvidenceStatusCode", "StrictJsonCode"):
        assert f"-Code ${variable}" in text


def test_consumer_harnesses_use_same_host_reproducibility_contract() -> None:
    powershell = SCRIPT.read_text(encoding="utf-8")
    shell = POSIX_SCRIPT.read_text(encoding="utf-8")

    assert '"python", "tools\\check-reproducible-build.py"' in powershell
    assert "same-host reproducible build comparison" in powershell
    assert "python tools/check-reproducible-build.py" in shell
    assert "same-host distribution repeatability" in shell
    assert "--expect-directory" not in powershell
    assert "--expect-directory" not in shell


def test_canonical_release_gates_still_bind_published_distribution_bytes() -> None:
    assembler = RELEASE_ASSEMBLER.read_text(encoding="utf-8")

    assert '"--expect-directory",\n            str(dist_dir),' in assembler


def test_windows_harness_checks_released_and_rebuilt_distribution_metadata() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    repeatability = text.index('Invoke-NativeChecked "same-host reproducible build comparison"')
    package_build = text.index('Invoke-NativeChecked "package build"')
    released_twine = text.index(
        'Invoke-NativeChecked "released-distribution Twine metadata check"'
    )
    rebuilt_twine = text.index(
        'Invoke-NativeChecked "rebuilt-distribution Twine metadata check"'
    )
    assert repeatability < package_build < released_twine < rebuilt_twine
    assert '"python", "-m", "twine", "check", $Wheel, $Sdist' in text
    assert "$BuiltWheels[0].FullName, $BuiltSdists[0].FullName" in text


def test_windows_harness_preflights_all_staged_source_trees() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    temporary_root = text.index("[System.IO.Path]::GetTempPath()")
    source_extract = text.index(
        "Expand-Archive -LiteralPath $SourceArchive `\n"
        "                    -DestinationPath $PackageBuildSourceExtract"
    )
    source_validation = text.index(
        "Test-Path -LiteralPath $PackageBuildSourceRoot -PathType Container"
    )
    source_push = text.index("Push-Location -LiteralPath $PackageBuildSourceRoot")
    package_build = text.index('Invoke-NativeChecked "package build"')
    source_pop = text.index("Pop-Location", package_build)
    cleanup = text.index('Description "short-path package-build source tree"')

    preflight = text.index('-ExpectedRoot $Release.SourceDirectoryName -DestinationPaths')
    install = text.index('Write-Step "Creating an isolated environment"')
    assert temporary_root < preflight < install < source_extract < source_validation
    assert '$PackageBuildSourceExtract = Join-Path $RunRoot "b"' in text
    assert '(Join-Path $PackageBuildSourceExtract $Release.SourceDirectoryName)' in text
    assert '(Join-Path $ReproDestination $Release.SourceDirectoryName)' in text
    assert source_validation < source_push < package_build < source_pop < cleanup
    assert "$PackageBuildSourceExtract = $null" in text
    assert "test-pvs-windows-hotfix.ps1" not in text


def test_windows_harness_reemits_native_output_as_plain_text() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "$Text = [string]$_" in text
    assert "Add-NativeLogLine $Text" in text
    assert "Write-Output $Text" in text
    assert "Write-Output $_" not in text
