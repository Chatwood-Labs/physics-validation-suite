[CmdletBinding()]
param(
    [switch]$Quick,
    [switch]$KeepEnvironment,
    [string]$WorkRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

# Release inputs stay anchored to this script. Disposable work and retained
# diagnostics use a short independent directory, even for deeply nested bundles.
$Root = [System.IO.Path]::GetFullPath($PSScriptRoot)
$ChecksumFile = Join-Path $Root "SHA256SUMS"
$ReleaseMetadataPath = Join-Path $Root "RELEASE_METADATA.json"
function New-AcceptanceRunDirectory {
    param([string]$Parent)
    if ([string]::IsNullOrWhiteSpace($Parent)) {
        $Parent = [System.IO.Path]::GetTempPath()
    }
    $Parent = [System.IO.Path]::GetFullPath($Parent)
    $Candidate = Join-Path $Parent ("pvs-{0}" -f [Guid]::NewGuid().ToString("N").Substring(0, 12))
    # Reserve room for dependency files, pytest and nested sdist/rebuild trees.
    # PowerShell 5.1 cannot rely on the OS long-path policy being enabled.
    if ($Candidate.Length -gt 64) {
        $Message = "Acceptance work path is {0} characters; maximum is 64. " +
            "Use -WorkRoot with a shorter writable parent, for example C:\PVS. " +
            "No test gates have run. Candidate: {1}"
        throw ($Message -f $Candidate.Length, $Candidate)
    }
    # Never reuse or recursively delete the caller's parent directory.
    New-Item -ItemType Directory -Path $Candidate -ErrorAction Stop | Out-Null
    return $Candidate
}

$RunRoot = New-AcceptanceRunDirectory -Parent $WorkRoot
$LogPath = Join-Path $RunRoot "acceptance.log"
$NativeLogPath = Join-Path $RunRoot "native-commands.log"
$VenvDirectory = Join-Path $RunRoot ".venv"
$UvVenvDirectory = Join-Path $RunRoot "uv-venv"
$PackageBuildSourceExtract = $null
$ChildTempDirectory = Join-Path $RunRoot "t"
$PreviousTemp = [Environment]::GetEnvironmentVariable("TEMP")
$PreviousTmp = [Environment]::GetEnvironmentVariable("TMP")
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
$StageJournal = Join-Path $RunRoot "acceptance-stages.tsv"
$SummaryPath = Join-Path $RunRoot "acceptance-summary.json"
$SummaryTool = $null
$SelectedPythonPath = $null
$script:CurrentStage = $null
$script:StageStarted = $null
$AcceptanceMode = $(if ($Quick) { "quick" } else { "full" })

function Complete-AcceptanceStage {
    param([string]$Status)
    if ($null -ne $script:CurrentStage) {
        $Line = "{0}`t{1}`t{2}`t{3}`n" -f $script:StageStarted, `
            [DateTime]::UtcNow.ToString("o"), $Status, $script:CurrentStage
        [System.IO.File]::AppendAllText($StageJournal, $Line, $Utf8WithoutBom)
        $script:CurrentStage = $null
    }
}

function Write-AcceptanceSummary {
    param([string]$Status)
    if ($null -eq $SummaryTool -or $null -eq $SelectedPythonPath) {
        throw "Acceptance bootstrap did not reach the summary tool."
    }
    Invoke-NativeChecked "acceptance summary" $SelectedPythonPath @(
        $SummaryTool, "finalize", "--run-root", $RunRoot, "--release-root", $Root,
        "--status", $Status, "--mode", $AcceptanceMode, "--output", $SummaryPath
    ) -ExpectedExitCodes $(if ($Status -eq "FAILED") { @(1) } else { @(0) })
}

function Write-EnvironmentSnapshot {
    param([string]$Profile, [string]$PythonPath, [string]$LockPath)
    Invoke-NativeChecked "$Profile environment identity" $PythonPath @(
        $SummaryTool, "environment", "--profile", $Profile, "--lock", $LockPath,
        "--output", (Join-Path $RunRoot ($Profile + ".json"))
    )
}


function Write-Step {
    param([string]$Message)
    Complete-AcceptanceStage "PASSED"
    $script:CurrentStage = $Message
    $script:StageStarted = [DateTime]::UtcNow.ToString("o")
    Write-Host ""
    Write-Host ("==> {0}" -f $Message) -ForegroundColor Cyan
}

function Add-NativeLogLine {
    param([string]$Line)
    [System.IO.File]::AppendAllText(
        $NativeLogPath,
        $Line + [Environment]::NewLine,
        $Utf8WithoutBom
    )
}

function Invoke-NativeChecked {
    param(
        [string]$Description,
        [string]$Executable,
        [string[]]$Arguments,
        [int[]]$ExpectedExitCodes = @(0)
    )
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = $null
    $ExitCode = $null
    Add-NativeLogLine ""
    Add-NativeLogLine ("===== {0} =====" -f $Description)
    Add-NativeLogLine ("Executable: {0}" -f $Executable)
    Add-NativeLogLine ("Arguments: {0}" -f ($Arguments | ConvertTo-Json -Compress))
    try {
        & $Executable @Arguments 2>&1 | ForEach-Object {
            # Windows PowerShell 5.1 wraps native stderr records as ErrorRecord
            # objects even when the process exits successfully. Re-emit plain
            # text so harmless diagnostics do not look like command failures.
            $Text = [string]$_
            Add-NativeLogLine $Text
            Write-Output $Text
        }
        $ExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PreviousPreference
    }
    Add-NativeLogLine ("Exit code: {0}" -f $ExitCode)
    if ($null -eq $ExitCode) {
        throw ("{0} did not report a native process exit code." -f $Description)
    }
    if ($ExpectedExitCodes -notcontains $ExitCode) {
        throw ("{0} returned exit code {1}; expected {2}." -f `
            $Description, $ExitCode, ($ExpectedExitCodes -join ", "))
    }
}

function New-PythonSnippetFile {
    param(
        [string]$Description,
        [string]$Code
    )
    $SafeDescription = ($Description -replace '[^A-Za-z0-9]+', '-').Trim('-').ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($SafeDescription)) {
        $SafeDescription = "python"
    }
    $SnippetName = "{0}-{1}.py" -f `
        $SafeDescription, `
        [Guid]::NewGuid().ToString("N")
    $SnippetPath = Join-Path $RunRoot $SnippetName
    [System.IO.File]::WriteAllText($SnippetPath, $Code, $Utf8WithoutBom)
    return $SnippetPath
}

function Invoke-PythonSnippetChecked {
    param(
        [string]$Description,
        [string]$Executable,
        [string[]]$PrefixArguments,
        [string]$Code,
        [string[]]$ScriptArguments = @(),
        [int[]]$ExpectedExitCodes = @(0)
    )
    $SnippetPath = New-PythonSnippetFile -Description $Description -Code $Code
    Invoke-NativeChecked -Description $Description -Executable $Executable `
        -Arguments (@($PrefixArguments) + @($SnippetPath) + @($ScriptArguments)) `
        -ExpectedExitCodes $ExpectedExitCodes
}

function Test-SupportedPython {
    param(
        [string]$Executable,
        [string[]]$PrefixArguments
    )
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $ProbeCode = @'
import platform
import sys

supported = (
    platform.python_implementation() == "CPython"
    and (3, 10) <= sys.version_info[:2] < (3, 15)
    and sys.maxsize > 2**32
    and platform.machine().lower() in {"amd64", "x86_64"}
)
raise SystemExit(0 if supported else 1)
'@
        $ProbePath = New-PythonSnippetFile `
            -Description "supported-python-probe" `
            -Code $ProbeCode
        $global:LASTEXITCODE = $null
        & $Executable @PrefixArguments $ProbePath *> $null
        return $null -ne $LASTEXITCODE -and $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $PreviousPreference
    }
}

function Find-SupportedPython {
    $Launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($null -ne $Launcher) {
        foreach ($Version in @("3.12", "3.14", "3.13", "3.11", "3.10")) {
            $Prefix = @("-$Version")
            if (Test-SupportedPython -Executable $Launcher.Path -PrefixArguments $Prefix) {
                return @{
                    Executable = $Launcher.Path
                    Arguments = $Prefix
                }
            }
        }
    }

    foreach ($Name in @("python", "python3")) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
        if ($null -ne $Command -and
            (Test-SupportedPython -Executable $Command.Path -PrefixArguments @())) {
            return @{
                Executable = $Command.Path
                Arguments = @()
            }
        }
    }

    throw "No release-qualified CPython was found. Install 64-bit CPython 3.10, 3.11, 3.12, 3.13, or 3.14 for Windows x86_64."
}

function Assert-SafeLeafName {
    param(
        [string]$Name,
        [string]$Field
    )
    if ([string]::IsNullOrWhiteSpace($Name) -or
        $Name -notmatch '^[A-Za-z0-9][A-Za-z0-9._+-]{0,239}$' -or
        $Name -eq "." -or
        $Name -eq ".." -or
        [System.IO.Path]::GetFileName($Name) -ne $Name) {
        throw "RELEASE_METADATA.json field '$Field' is not a safe leaf name."
    }
}

function Assert-ExactProperties {
    param(
        [object]$Object,
        [string[]]$Expected,
        [string]$Location
    )
    $Actual = @($Object.PSObject.Properties.Name | Sort-Object)
    $SortedExpected = @($Expected | Sort-Object)
    if (($Actual -join "`n") -cne ($SortedExpected -join "`n")) {
        throw ("RELEASE_METADATA.json {0} keys are [{1}]; expected [{2}]." -f `
            $Location, ($Actual -join ", "), ($SortedExpected -join ", "))
    }
}

function Assert-JsonString {
    param(
        [object]$Value,
        [string]$Field
    )
    if ($Value -isnot [string]) {
        throw "RELEASE_METADATA.json field '$Field' must be a string."
    }
}

function Read-ReleaseMetadata {
    if (-not (Test-Path -LiteralPath $ReleaseMetadataPath -PathType Leaf)) {
        throw "Required release file is missing: $ReleaseMetadataPath"
    }
    $Metadata = Get-Content -LiteralPath $ReleaseMetadataPath -Raw | ConvertFrom-Json
    Assert-ExactProperties -Object $Metadata `
        -Expected @(
            "schema", "project", "version", "source_date_epoch",
            "release_bundle", "sample", "artifacts"
        ) `
        -Location "root"
    if ([string]$Metadata.schema -cne "pvs-release-metadata/1") {
        throw "Unsupported RELEASE_METADATA.json schema."
    }
    if ([string]$Metadata.project -cne "physics-validation-suite") {
        throw "RELEASE_METADATA.json identifies the wrong project."
    }
    if ([string]$Metadata.version -cne "1.0.0a1") {
        throw "RELEASE_METADATA.json must describe PVS 1.0.0a1."
    }
    Assert-ExactProperties -Object $Metadata.artifacts -Expected @(
        "wheel", "sdist", "source_zip", "sample_evidence_zip",
        "sample_verification", "readme", "release_notes", "test_scripts",
        "sboms", "provenance"
    ) -Location "artifacts"
    Assert-ExactProperties -Object $Metadata.sample `
        -Expected @("finding_id", "evidence_id", "package_id") -Location "sample"
    Assert-ExactProperties -Object $Metadata.artifacts.test_scripts `
        -Expected @("powershell", "shell") -Location "artifacts.test_scripts"

    $StringFields = [ordered]@{
        "schema" = $Metadata.schema
        "project" = $Metadata.project
        "version" = $Metadata.version
        "release_bundle" = $Metadata.release_bundle
        "sample.finding_id" = $Metadata.sample.finding_id
        "sample.evidence_id" = $Metadata.sample.evidence_id
        "sample.package_id" = $Metadata.sample.package_id
        "artifacts.wheel" = $Metadata.artifacts.wheel
        "artifacts.sdist" = $Metadata.artifacts.sdist
        "artifacts.source_zip" = $Metadata.artifacts.source_zip
        "artifacts.sample_evidence_zip" = $Metadata.artifacts.sample_evidence_zip
        "artifacts.sample_verification" = $Metadata.artifacts.sample_verification
        "artifacts.readme" = $Metadata.artifacts.readme
        "artifacts.release_notes" = $Metadata.artifacts.release_notes
        "artifacts.test_scripts.powershell" = $Metadata.artifacts.test_scripts.powershell
        "artifacts.test_scripts.shell" = $Metadata.artifacts.test_scripts.shell
        "artifacts.provenance" = $Metadata.artifacts.provenance
    }
    foreach ($Field in $StringFields.Keys) {
        Assert-JsonString -Value $StringFields[$Field] -Field ([string]$Field)
    }

    $RawSboms = @($Metadata.artifacts.sboms)
    foreach ($Index in 0..($RawSboms.Count - 1)) {
        Assert-JsonString -Value $RawSboms[$Index] -Field "artifacts.sboms[$Index]"
    }
    $SbomNames = @($RawSboms | ForEach-Object { [string]$_ })
    $UniqueSboms = @($SbomNames | Sort-Object -Unique)
    if ($SbomNames.Count -ne 2 -or $UniqueSboms.Count -ne 2) {
        throw "RELEASE_METADATA.json must identify exactly two distinct SBOM files."
    }

    $Values = [pscustomobject][ordered]@{
        Version = [string]$Metadata.version
        WheelName = [string]$Metadata.artifacts.wheel
        SdistName = [string]$Metadata.artifacts.sdist
        SourceName = [string]$Metadata.artifacts.source_zip
        SampleName = [string]$Metadata.artifacts.sample_evidence_zip
        SampleVerificationName = [string]$Metadata.artifacts.sample_verification
        ReadmeName = [string]$Metadata.artifacts.readme
        ReleaseNotesName = [string]$Metadata.artifacts.release_notes
        PowerShellScriptName = [string]$Metadata.artifacts.test_scripts.powershell
        ShellScriptName = [string]$Metadata.artifacts.test_scripts.shell
        SbomNames = $SbomNames
        ProvenanceName = [string]$Metadata.artifacts.provenance
        ReleaseBundleName = [string]$Metadata.release_bundle
        SourceDirectoryName = "physics_validation_suite-$($Metadata.version)"
        SampleDirectoryName = "sample-plasma-evidence-v$($Metadata.version)"
        FindingId = [string]$Metadata.sample.finding_id
        EvidenceId = [string]$Metadata.sample.evidence_id
        PackageId = [string]$Metadata.sample.package_id
        SourceDateEpoch = [string]$Metadata.source_date_epoch
    }

    $LeafFields = [ordered]@{
        "artifacts.wheel" = $Values.WheelName
        "artifacts.sdist" = $Values.SdistName
        "artifacts.source_zip" = $Values.SourceName
        "artifacts.sample_evidence_zip" = $Values.SampleName
        "artifacts.sample_verification" = $Values.SampleVerificationName
        "artifacts.readme" = $Values.ReadmeName
        "artifacts.release_notes" = $Values.ReleaseNotesName
        "artifacts.test_scripts.powershell" = $Values.PowerShellScriptName
        "artifacts.test_scripts.shell" = $Values.ShellScriptName
        "artifacts.provenance" = $Values.ProvenanceName
        "release_bundle" = $Values.ReleaseBundleName
        "derived source directory" = $Values.SourceDirectoryName
        "derived sample directory" = $Values.SampleDirectoryName
    }
    foreach ($Index in 0..($Values.SbomNames.Count - 1)) {
        $LeafFields["artifacts.sboms[$Index]"] = $Values.SbomNames[$Index]
    }
    foreach ($Field in $LeafFields.Keys) {
        Assert-SafeLeafName -Name ([string]$LeafFields[$Field]) -Field ([string]$Field)
    }
    if ($Values.ReadmeName -cne "README.md" -or
        $Values.PowerShellScriptName -cne "test-pvs.ps1" -or
        $Values.ShellScriptName -cne "test-pvs.sh") {
        throw "RELEASE_METADATA.json does not identify the supplied README/test scripts."
    }

    $ExpectedNames = [ordered]@{
        WheelName = "physics_validation_suite-$($Values.Version)-py3-none-any.whl"
        SdistName = "physics_validation_suite-$($Values.Version).tar.gz"
        SourceName = "physics-validation-suite-$($Values.Version)-source.zip"
        SampleName = "pvs-$($Values.Version)-sample-plasma-evidence.zip"
        SampleVerificationName = "sample-plasma-verification.json"
        ReleaseNotesName = "RELEASE_NOTES_$($Values.Version).md"
        ProvenanceName = "pvs-$($Values.Version).provenance.intoto.json"
        ReleaseBundleName = "pvs-$($Values.Version)-release-bundle.zip"
    }
    foreach ($Property in $ExpectedNames.Keys) {
        if ([string]($Values.$Property) -cne [string]($ExpectedNames[$Property])) {
            throw ("RELEASE_METADATA.json {0} is '{1}'; expected '{2}'." -f `
                $Property, $Values.$Property, $ExpectedNames[$Property])
        }
    }
    $ExpectedSboms = @(
        "$($Values.WheelName).cdx.json",
        "$($Values.SdistName).cdx.json"
    ) | Sort-Object
    $ActualSboms = @($Values.SbomNames | Sort-Object)
    if (($ActualSboms -join "`n") -cne ($ExpectedSboms -join "`n")) {
        throw ("RELEASE_METADATA.json SBOM names are [{0}]; expected [{1}]." -f `
            ($ActualSboms -join ", "), ($ExpectedSboms -join ", "))
    }

    if ($Values.FindingId -cnotmatch '^pvs-finding:v1:sha256:[0-9a-f]{64}$') {
        throw "RELEASE_METADATA.json contains an invalid Scientific Finding ID."
    }
    if ($Values.EvidenceId -cnotmatch '^pvs:sha256:[0-9a-f]{64}$') {
        throw "RELEASE_METADATA.json contains an invalid Evidence ID."
    }
    if ($Values.PackageId -cnotmatch '^pvs-package:sha256:[0-9a-f]{64}$') {
        throw "RELEASE_METADATA.json contains an invalid Package ID."
    }
    $EpochIsInteger = $Metadata.source_date_epoch -is [byte] -or
        $Metadata.source_date_epoch -is [int16] -or
        $Metadata.source_date_epoch -is [int32] -or
        $Metadata.source_date_epoch -is [int64]
    if (-not $EpochIsInteger -or $Values.SourceDateEpoch -notmatch '^[0-9]+$' -or
        [Int64]$Values.SourceDateEpoch -lt 315532800) {
        throw "RELEASE_METADATA.json source_date_epoch must be an integer at or after 1980-01-01."
    }
    return $Values
}

function Assert-ReleaseChecksums {
    param(
        [string[]]$RequiredEntries
    )
    $Separators = [char[]]@(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $RootWithSeparator = $Root.TrimEnd($Separators) + [System.IO.Path]::DirectorySeparatorChar
    $ListedEntries = @{}
    foreach ($Line in Get-Content -LiteralPath $ChecksumFile) {
        if ([string]::IsNullOrWhiteSpace($Line)) {
            throw "SHA256SUMS contains a blank line."
        }
        if ($Line -notmatch '^([0-9A-Fa-f]{64})  (.+)$') {
            throw "Invalid SHA256SUMS line: $Line"
        }
        $Expected = $Matches[1].ToLowerInvariant()
        $RelativePath = $Matches[2]
        if ($RelativePath.Contains("\") -or
            $RelativePath.StartsWith("/") -or
            $RelativePath.Split("/") -contains ".." -or
            $RelativePath.Split("/") -contains ".") {
            throw "Unsafe path in SHA256SUMS: $RelativePath"
        }
        $EntryKey = $RelativePath.ToLowerInvariant()
        if ($ListedEntries.ContainsKey($EntryKey)) {
            throw "Duplicate or case-colliding path in SHA256SUMS: $RelativePath"
        }
        $ListedEntries[$EntryKey] = $RelativePath
        $Candidate = [System.IO.Path]::GetFullPath((Join-Path $Root $RelativePath))
        if (-not $Candidate.StartsWith(
                $RootWithSeparator,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            throw "Unsafe path in SHA256SUMS: $RelativePath"
        }
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            throw "Release file listed in SHA256SUMS is missing: $RelativePath"
        }
        $Item = Get-Item -LiteralPath $Candidate -Force
        if (($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release file listed in SHA256SUMS is a reparse point: $RelativePath"
        }
        $Actual = (Get-FileHash -LiteralPath $Candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Actual -ne $Expected) {
            throw "Checksum mismatch for $RelativePath. Expected $Expected; got $Actual."
        }
        Write-Host ("[PASS] {0}" -f $RelativePath)
    }
    foreach ($RequiredEntry in $RequiredEntries) {
        if (-not $ListedEntries.ContainsKey($RequiredEntry.ToLowerInvariant())) {
            throw "SHA256SUMS does not identify required release file: $RequiredEntry"
        }
    }
    $RequiredKeys = @{}
    foreach ($RequiredEntry in $RequiredEntries) {
        $RequiredKey = $RequiredEntry.ToLowerInvariant()
        if ($RequiredKeys.ContainsKey($RequiredKey)) {
            throw "Release metadata contains duplicate or colliding filenames: $RequiredEntry"
        }
        $RequiredKeys[$RequiredKey] = $true
    }
    foreach ($ListedKey in $ListedEntries.Keys) {
        if (-not $RequiredKeys.ContainsKey($ListedKey)) {
            throw ("SHA256SUMS contains a file absent from release metadata: {0}" -f `
                $ListedEntries[$ListedKey])
        }
    }
}

function Assert-SafeZipArchive {
    param(
        [string]$ArchivePath,
        [string]$ExpectedRoot,
        [string[]]$DestinationPaths = @()
    )
    $ZipAuditCode = @'
import ntpath
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

archive_path = Path(sys.argv[1])
expected_root = sys.argv[2]
destinations = sys.argv[3:]
invalid = set('<>:"|?*')
reserved = {
    "CON", "PRN", "AUX", "CONIN$", "CONOUT$", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "\u00b9\u00b2\u00b3"),
}
seen = {}
regular_files = 0
with zipfile.ZipFile(archive_path) as archive:
    for info in archive.infolist():
        if info.flag_bits & 1:
            raise SystemExit(f"encrypted ZIP member is forbidden: {info.filename!r}")
        raw = info.filename
        name = raw[:-1] if raw.endswith("/") else raw
        pure = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or pure.is_absolute()
            or str(pure) != name
            or unicodedata.normalize("NFC", name) != name
            or not pure.parts
            or pure.parts[0] != expected_root
        ):
            raise SystemExit(f"unsafe or unexpected ZIP member: {raw!r}")
        for part in pure.parts:
            stem = part.split(".", 1)[0].upper()
            if (
                part in {"", ".", ".."}
                or part != part.strip()
                or part.endswith(".")
                or any(ord(character) < 32 or ord(character) == 127 for character in part)
                or any(character in invalid for character in part)
                or stem in reserved
            ):
                raise SystemExit(f"non-portable ZIP member: {raw!r}")
        for destination in destinations:
            target = ntpath.join(destination, *pure.parts)
            # Windows counts UTF-16 code units, including surrogate pairs.
            length = len(target.encode("utf-16-le")) // 2
            if length > 240:
                raise SystemExit(
                    f"Windows extraction/build path is {length} characters; maximum is 240: "
                    f"{target!r}. Use -WorkRoot with a shorter writable parent. "
                    "No archive members have been extracted by this check."
                )
        key = name.casefold()
        if key in seen:
            raise SystemExit(f"duplicate/colliding ZIP members: {seen[key]!r} and {raw!r}")
        seen[key] = raw
        file_type = (info.external_attr >> 16) & 0o170000
        if info.is_dir():
            if raw[-1:] != "/" or file_type not in {0, stat.S_IFDIR}:
                raise SystemExit(f"malformed ZIP directory member: {raw!r}")
        else:
            if file_type not in {0, stat.S_IFREG}:
                raise SystemExit(f"linked or non-regular ZIP member: {raw!r}")
            regular_files += 1
if regular_files == 0:
    raise SystemExit(f"ZIP archive contains no regular files: {archive_path}")
print(f"[PASS] Safe ZIP membership: {archive_path.name}")
'@
    Invoke-PythonSnippetChecked -Description "safe ZIP membership check" `
        -Executable $BaseExecutable -PrefixArguments $BaseArguments `
        -Code $ZipAuditCode -ScriptArguments (@($ArchivePath, $ExpectedRoot) + $DestinationPaths)
}

function Assert-EvidencePass {
    param([string]$PackagePath)
    $EvidenceStatusCode = @'
import json
import sys
from pathlib import Path

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate evidence.json member: {key!r}")
        result[key] = value
    return result

def reject_constant(value):
    raise SystemExit(f"non-finite evidence.json number: {value}")

path = Path(sys.argv[1]) / "evidence.json"
with path.open("r", encoding="utf-8") as stream:
    evidence = json.load(
        stream,
        object_pairs_hook=strict_object,
        parse_constant=reject_constant,
    )
status = evidence.get("record", {}).get("summary", {}).get("status")
if status != "PASS":
    raise SystemExit(f"scientific evidence status is {status!r}; expected 'PASS'")
print(f"[PASS] Scientific evidence status: {path.parent}")
'@
    Invoke-PythonSnippetChecked -Description "scientific PASS assertion" `
        -Executable $script:PvsPython -PrefixArguments @() `
        -Code $EvidenceStatusCode -ScriptArguments @($PackagePath)
}

function Remove-TestEnvironment {
    param(
        [string]$Candidate,
        [string]$Expected,
        [string]$Description
    )
    if (-not (Test-Path -LiteralPath $Candidate -PathType Container)) {
        return
    }
    $ActualPath = [System.IO.Path]::GetFullPath($Candidate)
    $ExpectedPath = [System.IO.Path]::GetFullPath($Expected)
    if ($ActualPath -ne $ExpectedPath) {
        Write-Warning "Refusing to remove an unexpected ${Description}: $ActualPath"
        return
    }
    try {
        Remove-Item -LiteralPath $ActualPath -Recurse -Force -ErrorAction Stop
        Write-Host "Removed the disposable $Description. Use -KeepEnvironment to retain it."
    }
    catch {
        Write-Warning ("Could not remove the {0}: {1}" -f $Description, $_.Exception.Message)
    }
}

$null = New-Item -ItemType Directory -Path $ChildTempDirectory
$TranscriptStarted = $false
$RootLocationPushed = $false
$Succeeded = $false

try {
    Start-Transcript -Path $LogPath | Out-Null
    $TranscriptStarted = $true
    Write-Host ("Run output:  {0}" -f $RunRoot)
    Write-Host ("Summary:     {0}" -f $SummaryPath)
    # Python tempfile, pip, uv and the pytest launcher inherit this bounded root.
    $env:TEMP = $ChildTempDirectory
    $env:TMP = $ChildTempDirectory
    Push-Location -LiteralPath $Root
    $RootLocationPushed = $true

    Write-Step "Selecting a supported Python"
    $BasePython = Find-SupportedPython
    $BaseExecutable = [string]$BasePython.Executable
    $BaseArguments = [string[]]$BasePython.Arguments
    Invoke-NativeChecked "selected-Python version check" $BaseExecutable `
        (@($BaseArguments) + @("--version"))

    $InterpreterCode = @'
import json
import platform
import sys
from pathlib import Path

base = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
identity = {"executable": str(Path(sys.executable).absolute()), "base_executable": base,
            "version": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": sys.platform, "machine": platform.machine()}
Path(sys.argv[1]).write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
'@
    $InterpreterPath = Join-Path $RunRoot "selected-interpreter.json"
    Invoke-PythonSnippetChecked -Description "selected interpreter identity" `
        -Executable $BaseExecutable -PrefixArguments $BaseArguments `
        -Code $InterpreterCode -ScriptArguments @($InterpreterPath)
    $SelectedInterpreter = Get-Content -LiteralPath $InterpreterPath -Raw | ConvertFrom-Json
    $SelectedPythonPath = [string]$SelectedInterpreter.base_executable
    $BaseExecutable = $SelectedPythonPath
    $BaseArguments = [string[]]@()

    Write-Step "Validating release metadata and SHA-256 checksums"
    if (-not (Test-Path -LiteralPath $ChecksumFile -PathType Leaf)) {
        throw "Required release file is missing: $ChecksumFile"
    }
    if (-not (Test-Path -LiteralPath $ReleaseMetadataPath -PathType Leaf)) {
        throw "Required release file is missing: $ReleaseMetadataPath"
    }
    $StrictJsonCode = @'
import json
import sys

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate RELEASE_METADATA.json member: {key!r}")
        result[key] = value
    return result

def reject_constant(value):
    raise SystemExit(f"non-finite RELEASE_METADATA.json number: {value}")

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    json.load(stream, object_pairs_hook=strict_object, parse_constant=reject_constant)
'@
    Invoke-PythonSnippetChecked -Description "strict release-metadata JSON check" `
        -Executable $BaseExecutable -PrefixArguments $BaseArguments `
        -Code $StrictJsonCode -ScriptArguments @($ReleaseMetadataPath)
    $Release = Read-ReleaseMetadata
    $Wheel = Join-Path $Root $Release.WheelName
    $Sdist = Join-Path $Root $Release.SdistName
    $SourceArchive = Join-Path $Root $Release.SourceName
    $SampleArchive = Join-Path $Root $Release.SampleName
    $RequiredEntries = @(
        $Release.WheelName,
        $Release.SdistName,
        $Release.SourceName,
        $Release.SampleName,
        $Release.SampleVerificationName,
        $Release.ReadmeName,
        $Release.ReleaseNotesName,
        $Release.PowerShellScriptName,
        $Release.ShellScriptName,
        $Release.ProvenanceName,
        "RELEASE_METADATA.json"
    ) + @($Release.SbomNames)
    Assert-ReleaseChecksums -RequiredEntries $RequiredEntries
    foreach ($Required in @($Wheel, $Sdist, $SourceArchive, $SampleArchive)) {
        if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
            throw "Required release file is missing: $Required. Extract the complete release bundle and run its own test-pvs.ps1."
        }
    }

    Write-Host ("Physics Validation Suite {0} - Windows release acceptance" -f $Release.Version)
    Write-Host ("Bundle root: {0}" -f $Root)
    Write-Host ("Mode:        {0}" -f $(if ($Quick) { "quick" } else { "full" }))

    Write-Step "Checking Windows work and archive paths"
    $SampleExtract = Join-Path $RunRoot "sample"
    $SourceExtract = Join-Path $RunRoot "source"
    $DevelopmentSourceExtract = Join-Path $RunRoot "source-development"
    $PackageBuildSourceExtract = Join-Path $RunRoot "b"
    # Audit every direct extraction and the nested source copy setuptools makes.
    # Also reserve the known tempfile suffix used by the repeatability checker.
    $BuildSourceDestinations = @($SourceExtract)
    if (-not $Quick) {
        $ReproDestination = Join-Path $ChildTempDirectory "pvs-repro-XXXXXXXX\source-a"
        $BuildSourceDestinations += @(
            $DevelopmentSourceExtract,
            $PackageBuildSourceExtract,
            (Join-Path $PackageBuildSourceExtract $Release.SourceDirectoryName),
            (Join-Path $ReproDestination $Release.SourceDirectoryName)
        )
    }
    Assert-SafeZipArchive -ArchivePath $SampleArchive `
        -ExpectedRoot $Release.SampleDirectoryName -DestinationPaths @($SampleExtract)
    Assert-SafeZipArchive -ArchivePath $SourceArchive `
        -ExpectedRoot $Release.SourceDirectoryName -DestinationPaths $BuildSourceDestinations

    Write-Step "Creating an isolated environment"
    Invoke-NativeChecked "virtual-environment creation" $BaseExecutable `
        (@($BaseArguments) + @("-m", "venv", $VenvDirectory))
    $script:PvsPython = Join-Path $VenvDirectory "Scripts\python.exe"

    Write-Step "Installing the released wheel with all runtime adapters"
    Invoke-NativeChecked "pip upgrade" $script:PvsPython `
        @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-NativeChecked "released-wheel installation" $script:PvsPython @(
        "-m", "pip", "install", "--only-binary=:all:", ($Wheel + "[all]")
    )
    Invoke-NativeChecked "released-wheel dependency check" $script:PvsPython `
        @("-m", "pip", "check")
    $VersionOutput = @(
        Invoke-NativeChecked "PVS version check" $script:PvsPython `
            @("-m", "pvs", "--version")
    )
    $VersionText = (($VersionOutput | ForEach-Object { "$_" }) -join "`n").Trim()
    $VersionOutput | ForEach-Object { Write-Host $_ }
    if ($VersionText -cne ("PVS {0}" -f $Release.Version)) {
        throw "Installed version output was '$VersionText'; expected exactly 'PVS $($Release.Version)'."
    }

    Write-Step "Extracting the pinned sample and source examples"
    Expand-Archive -LiteralPath $SampleArchive -DestinationPath $SampleExtract
    Expand-Archive -LiteralPath $SourceArchive -DestinationPath $SourceExtract
    $SamplePackage = Join-Path $SampleExtract $Release.SampleDirectoryName
    $SourceRoot = Join-Path $SourceExtract $Release.SourceDirectoryName
    if (-not (Test-Path -LiteralPath $SamplePackage -PathType Container)) {
        throw "The sample archive did not contain '$($Release.SampleDirectoryName)'."
    }
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw "The source archive did not contain '$($Release.SourceDirectoryName)'."
    }

    $SummaryTool = Join-Path $SourceRoot "tools\acceptance-summary.py"
    Write-EnvironmentSnapshot "runtime-all" $script:PvsPython (Join-Path $SourceRoot "uv.lock")

    Write-Step "Verifying the supplied sample against all pinned identities"
    Assert-EvidencePass -PackagePath $SamplePackage
    Invoke-NativeChecked "pinned sample verification" $script:PvsPython @(
        "-m", "pvs", "verify", $SamplePackage,
        "--expect-finding-id", $Release.FindingId,
        "--expect-evidence-id", $Release.EvidenceId,
        "--expect-package-id", $Release.PackageId
    )

    Write-Step "Generating and verifying fresh Windows evidence"
    $JsonCase = Join-Path $SourceRoot "examples\json-existing\pvs.yaml"
    $JsonEvidence = Join-Path $RunRoot "windows-json-evidence"
    Invoke-NativeChecked "case inspection" $script:PvsPython `
        @("-m", "pvs", "inspect", $JsonCase)
    Invoke-NativeChecked "existing-JSON validation" $script:PvsPython `
        @("-m", "pvs", "validate", $JsonCase, "--output", $JsonEvidence)
    Assert-EvidencePass -PackagePath $JsonEvidence
    Invoke-NativeChecked "fresh-package verification" $script:PvsPython `
        @("-m", "pvs", "verify", $JsonEvidence)

    Write-Step "Exercising the public Python API"
    $PythonApiWork = Join-Path $RunRoot "python-api"
    New-Item -ItemType Directory -Path $PythonApiWork | Out-Null
    Push-Location -LiteralPath $PythonApiWork
    try {
        Invoke-NativeChecked "Python API example" $script:PvsPython `
            @(Join-Path $SourceRoot "examples\python-api\integrate.py")
    }
    finally {
        Pop-Location
    }
    Assert-EvidencePass -PackagePath (Join-Path $PythonApiWork "pvs-api-evidence")

    Write-Step "Proving that tampering is rejected"
    $TamperPackage = Join-Path $RunRoot "tampered-sample"
    Copy-Item -LiteralPath $SamplePackage -Destination $TamperPackage -Recurse
    $TamperTarget = Join-Path $TamperPackage "report.html"
    if (-not (Test-Path -LiteralPath $TamperTarget -PathType Leaf)) {
        $TamperTarget = Join-Path $TamperPackage "report.pdf"
    }
    if (-not (Test-Path -LiteralPath $TamperTarget -PathType Leaf)) {
        $TamperTarget = Join-Path $TamperPackage "evidence.json"
    }
    [System.IO.File]::AppendAllText(
        $TamperTarget,
        "`nPVS deliberate acceptance-test tamper`n",
        $Utf8WithoutBom
    )
    Invoke-NativeChecked "tampered-package rejection" $script:PvsPython `
        @("-m", "pvs", "verify", $TamperPackage) @(4)
    Write-Host "[PASS] Deliberately tampered package was rejected with exit code 4."

    if (-not $Quick) {
        Write-Step "Validating existing CSV artefacts"
        $CsvEvidence = Join-Path $RunRoot "windows-csv-evidence"
        Invoke-NativeChecked "existing-CSV validation" $script:PvsPython @(
            "-m", "pvs", "validate",
            (Join-Path $SourceRoot "examples\csv-existing\pvs.yaml"),
            "--output", $CsvEvidence
        )
        Assert-EvidencePass -PackagePath $CsvEvidence
        Invoke-NativeChecked "CSV package verification" $script:PvsPython `
            @("-m", "pvs", "verify", $CsvEvidence)

        Write-Step "Preparing NetCDF externally, then validating existing artefacts"
        $NetcdfCaseRoot = Join-Path $SourceRoot "examples\netcdf-command"
        Push-Location -LiteralPath $NetcdfCaseRoot
        try {
            Invoke-NativeChecked "external NetCDF fixture generation" $script:PvsPython `
                @("model.py")
        }
        finally {
            Pop-Location
        }
        $NetcdfEvidence = Join-Path $RunRoot "windows-netcdf-evidence"
        Invoke-NativeChecked "existing-NetCDF validation" $script:PvsPython @(
            "-m", "pvs", "validate", (Join-Path $NetcdfCaseRoot "pvs.yaml"),
            "--output", $NetcdfEvidence
        )
        Assert-EvidencePass -PackagePath $NetcdfEvidence
        Invoke-NativeChecked "NetCDF package verification" $script:PvsPython `
            @("-m", "pvs", "verify", $NetcdfEvidence)

        Write-Step "Qualifying all built-in benchmark packs"
        Invoke-NativeChecked "benchmark catalogue qualification" $script:PvsPython @(
            (Join-Path $SourceRoot "tools\run-benchmark-packs.py"),
            "--output", (Join-Path $RunRoot "benchmark-evidence")
        )

        Write-Step "Preparing the plasma fixture externally, then validating it"
        $PlasmaCaseRoot = Join-Path $SourceRoot "benchmark-packs\plasma-physics-reference-v1"
        Push-Location -LiteralPath $PlasmaCaseRoot
        try {
            Invoke-NativeChecked "external plasma fixture generation" $script:PvsPython @(
                "derive_reference_values.py", "--output", "derived_values.json"
            )
        }
        finally {
            Pop-Location
        }
        $PlasmaEvidence = Join-Path $RunRoot "windows-plasma-evidence"
        Invoke-NativeChecked "existing plasma-reference validation" $script:PvsPython @(
            "-m", "pvs", "validate",
            (Join-Path $PlasmaCaseRoot "pvs.yaml"),
            "--output", $PlasmaEvidence
        )
        Assert-EvidencePass -PackagePath $PlasmaEvidence
        Invoke-NativeChecked "plasma package verification" $script:PvsPython `
            @("-m", "pvs", "verify", $PlasmaEvidence)

        Invoke-NativeChecked "exact plasma Finding projection comparison" $script:PvsPython @(
            $SummaryTool, "finding-diff", (Join-Path $SamplePackage "evidence.json"),
            (Join-Path $PlasmaEvidence "evidence.json"),
            "--output", (Join-Path $RunRoot "plasma-finding-diff.json")
        )

        Write-Step "Extracting a pristine source tree for engineering gates"
        Expand-Archive -LiteralPath $SourceArchive -DestinationPath $DevelopmentSourceExtract
        $DevelopmentSourceRoot = Join-Path $DevelopmentSourceExtract $Release.SourceDirectoryName
        if (-not (Test-Path -LiteralPath $DevelopmentSourceRoot -PathType Container)) {
            throw "The source archive did not contain its expected development source directory."
        }

        Write-Step "Installing development and frozen-lock tooling"
        $DevSpecification = "${DevelopmentSourceRoot}[dev,all]"
        Invoke-NativeChecked "development dependency installation" $script:PvsPython @(
            "-m", "pip", "install", "--only-binary=:all:",
            "--editable", $DevSpecification
        )
        Invoke-NativeChecked "uv installation" $script:PvsPython @(
            "-m", "pip", "install", "--only-binary=:all:", "uv==0.11.33"
        )
        Invoke-NativeChecked "development dependency check" $script:PvsPython `
            @("-m", "pip", "check")
        $UvExecutable = Join-Path $VenvDirectory "Scripts\uv.exe"
        if (-not (Test-Path -LiteralPath $UvExecutable -PathType Leaf)) {
            throw "uv was installed but its executable was not found."
        }

        Write-EnvironmentSnapshot "ordinary-dev-all" $script:PvsPython `
            (Join-Path $DevelopmentSourceRoot "uv.lock")

        Write-Step "Running pytest, Ruff, and strict cross-platform mypy"
        $PytestArguments = @("tools\run-pytest.py")
        if ($KeepEnvironment) {
            $PytestArguments += "--keep-temp"
        }
        Push-Location -LiteralPath $DevelopmentSourceRoot
        try {
            Invoke-NativeChecked "pytest" $script:PvsPython ($PytestArguments + @(
                "--junitxml", (Join-Path $RunRoot "ordinary-pytest.xml")
            ))
            Invoke-NativeChecked "Ruff" $script:PvsPython @("-m", "ruff", "check", ".")
            Invoke-NativeChecked "strict mypy (native Windows target)" $script:PvsPython `
                @("-m", "mypy")
            Invoke-NativeChecked "strict mypy (Linux target)" $script:PvsPython `
                @("-m", "mypy", "--platform", "linux")
            Invoke-NativeChecked "strict mypy (macOS target)" $script:PvsPython `
                @("-m", "mypy", "--platform", "darwin")

            Write-Step "Qualifying the exact frozen dependency closure"
            $PreviousUvEnvironment = [Environment]::GetEnvironmentVariable("UV_PROJECT_ENVIRONMENT")
            try {
                $env:UV_PROJECT_ENVIRONMENT = $UvVenvDirectory
                Invoke-NativeChecked "uv lock check" $UvExecutable @("lock", "--check", "--python", $SelectedPythonPath)
                Invoke-NativeChecked "uv frozen sync" $UvExecutable @(
                    "sync", "--frozen", "--extra", "dev", "--extra", "all",
                    "--python", $SelectedPythonPath
                )
                Write-EnvironmentSnapshot "frozen-dev-all" `
                    (Join-Path $UvVenvDirectory "Scripts\python.exe") `
                    (Join-Path $DevelopmentSourceRoot "uv.lock")
                Invoke-NativeChecked "uv frozen pytest" $UvExecutable (@(
                    "run", "--frozen", "--extra", "dev", "--extra", "all",
                    "--python", $SelectedPythonPath,
                    "python"
                ) + $PytestArguments + @(
                    "--junitxml", (Join-Path $RunRoot "frozen-pytest.xml")
                ))
            }
            finally {
                if ($null -eq $PreviousUvEnvironment) {
                    Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
                }
                else {
                    $env:UV_PROJECT_ENVIRONMENT = $PreviousUvEnvironment
                }
            }

            Write-Step "Proving same-host distribution repeatability and checking metadata"
            $RebuiltDirectory = Join-Path $RunRoot "rebuilt-dist"
            $PreviousEpoch = [Environment]::GetEnvironmentVariable("SOURCE_DATE_EPOCH")
            $PreviousSourceArchive = [Environment]::GetEnvironmentVariable(
                "PVS_BUILD_SOURCE_ARCHIVE"
            )
            $PreviousReproUvEnvironment = [Environment]::GetEnvironmentVariable(
                "UV_PROJECT_ENVIRONMENT"
            )
            try {
                $env:SOURCE_DATE_EPOCH = $Release.SourceDateEpoch
                $env:PVS_BUILD_SOURCE_ARCHIVE = $SourceArchive
                $env:UV_PROJECT_ENVIRONMENT = $UvVenvDirectory
                # Consumer acceptance proves repeatability on this host. Exact
                # equality to the published Linux-built archives is enforced by
                # the canonical release-assembly gate before publication.
                Invoke-NativeChecked "same-host reproducible build comparison" $UvExecutable @(
                    "run", "--frozen", "--extra", "dev", "--extra", "all",
                    "--python", $SelectedPythonPath,
                    "python", "tools\check-reproducible-build.py"
                )
                # This separate pristine source tree shares the bounded work root;
                # its direct and nested build paths were audited before extraction.
                Expand-Archive -LiteralPath $SourceArchive `
                    -DestinationPath $PackageBuildSourceExtract
                $PackageBuildSourceRoot = Join-Path `
                    $PackageBuildSourceExtract `
                    $Release.SourceDirectoryName
                if (-not (Test-Path -LiteralPath $PackageBuildSourceRoot -PathType Container)) {
                    throw "The source archive did not contain its expected package-build directory."
                }
                Write-Host ("Package-build source: {0}" -f $PackageBuildSourceRoot)
                Push-Location -LiteralPath $PackageBuildSourceRoot
                try {
                    Invoke-NativeChecked "package build" $UvExecutable @(
                        "run", "--frozen", "--extra", "dev", "--extra", "all",
                    "--python", $SelectedPythonPath,
                        "python", "-m", "build", "--no-isolation", "--outdir", $RebuiltDirectory
                    )
                }
                finally {
                    Pop-Location
                }
                $BuiltWheels = @(Get-ChildItem -LiteralPath $RebuiltDirectory -Filter "*.whl" -File)
                $BuiltSdists = @(Get-ChildItem -LiteralPath $RebuiltDirectory -Filter "*.tar.gz" -File)
                if ($BuiltWheels.Count -ne 1 -or $BuiltSdists.Count -ne 1) {
                    throw "The package build must produce exactly one wheel and one source distribution."
                }
                Invoke-NativeChecked "released-distribution Twine metadata check" $UvExecutable @(
                    "run", "--frozen", "--extra", "dev", "--extra", "all",
                    "--python", $SelectedPythonPath,
                    "python", "-m", "twine", "check", $Wheel, $Sdist
                )
                Invoke-NativeChecked "rebuilt-distribution Twine metadata check" $UvExecutable @(
                    "run", "--frozen", "--extra", "dev", "--extra", "all",
                    "--python", $SelectedPythonPath,
                    "python", "-m", "twine", "check",
                    $BuiltWheels[0].FullName, $BuiltSdists[0].FullName
                )
            }
            finally {
                if ($null -eq $PreviousEpoch) {
                    Remove-Item Env:SOURCE_DATE_EPOCH -ErrorAction SilentlyContinue
                }
                else {
                    $env:SOURCE_DATE_EPOCH = $PreviousEpoch
                }
                if ($null -eq $PreviousSourceArchive) {
                    Remove-Item Env:PVS_BUILD_SOURCE_ARCHIVE -ErrorAction SilentlyContinue
                }
                else {
                    $env:PVS_BUILD_SOURCE_ARCHIVE = $PreviousSourceArchive
                }
                if ($null -eq $PreviousReproUvEnvironment) {
                    Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
                }
                else {
                    $env:UV_PROJECT_ENVIRONMENT = $PreviousReproUvEnvironment
                }
            }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Host ""
        Write-Host "Quick mode: skipped CSV/NetCDF/plasma and repository quality, lock, reproducibility and supply-chain gates."
    }

    Complete-AcceptanceStage "PASSED"
    Write-AcceptanceSummary "PASSED"
    Write-Host ""
    Write-Host "==> Windows release acceptance passed" -ForegroundColor Cyan
    Write-Host ("Summary:   {0}" -f $SummaryPath)
    Write-Host ("Fresh PDF: {0}" -f (Join-Path $JsonEvidence "report.pdf"))
    Write-Host ("Host log:   {0}" -f $LogPath)
    Write-Host ("Native log: {0}" -f $NativeLogPath)
    Write-Host "PVS run mode was not invoked: subject execution remains deliberately POSIX-only in v0.3."
    $Succeeded = $true
}
catch {
    $FailureMessage = $_.Exception.Message
    Complete-AcceptanceStage "FAILED"
    try {
        Write-AcceptanceSummary "FAILED"
    }
    catch {
        # Record an early failure even when no trusted source/tool was extracted.
        $Fallback = @{
            schema = "pvs-acceptance-summary/1"; status = "FAILED"; mode = $AcceptanceMode
            problems = @($FailureMessage, $_.Exception.Message)
            stage_journal = "acceptance-stages.tsv"
        } | ConvertTo-Json -Depth 5
        [System.IO.File]::WriteAllText($SummaryPath, $Fallback + "`n", $Utf8WithoutBom)
    }
    Write-Host ""
    Write-Host "PVS RELEASE ACCEPTANCE FAILED" -ForegroundColor Red
    Write-Host $FailureMessage -ForegroundColor Red
    Write-Host ("Diagnostic output is retained under: {0}" -f $RunRoot)
    Write-Host ("Native command output: {0}" -f $NativeLogPath)
}
finally {
    if (-not $KeepEnvironment) {
        Remove-TestEnvironment `
            -Candidate $VenvDirectory `
            -Expected (Join-Path $RunRoot ".venv") `
            -Description "acceptance virtual environment"
        Remove-TestEnvironment `
            -Candidate $UvVenvDirectory `
            -Expected (Join-Path $RunRoot "uv-venv") `
            -Description "frozen-lock virtual environment"
        if (-not [string]::IsNullOrWhiteSpace([string]$PackageBuildSourceExtract)) {
            Remove-TestEnvironment `
                -Candidate $PackageBuildSourceExtract `
                -Expected (Join-Path $RunRoot "b") `
                -Description "short-path package-build source tree"
        }
    }
    foreach ($SavedVariable in @(
        @{ Name = "TEMP"; Value = $PreviousTemp },
        @{ Name = "TMP"; Value = $PreviousTmp }
    )) {
        [Environment]::SetEnvironmentVariable($SavedVariable.Name, $SavedVariable.Value)
    }
    if (-not $KeepEnvironment) {
        Remove-TestEnvironment -Candidate $ChildTempDirectory `
            -Expected (Join-Path $RunRoot "t") -Description "child-process temporary directory"
    }
    if ($RootLocationPushed) {
        Pop-Location
    }
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}

if (-not $Succeeded) {
    exit 1
}
exit 0
