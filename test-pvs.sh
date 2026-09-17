#!/usr/bin/env bash
set -euo pipefail

# PVS v1.0.0a1 release acceptance. Every release path is anchored to this
# script, so the caller's current directory is irrelevant.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
QUICK=0
KEEP_ENVIRONMENT=0

while (( $# )); do
    case "$1" in
        --quick)
            QUICK=1
            ;;
        --keep-environment)
            KEEP_ENVIRONMENT=1
            ;;
        -h|--help)
            printf 'Usage: bash ./test-pvs.sh [--quick] [--keep-environment]\n'
            exit 0
            ;;
        *)
            printf 'Usage: bash ./test-pvs.sh [--quick] [--keep-environment]\n' >&2
            exit 2
            ;;
    esac
    shift
done

case "$(uname -s)" in
    Linux)
        PLATFORM_LABEL="linux"
        ;;
    Darwin)
        PLATFORM_LABEL="macos"
        ;;
    *)
        printf 'test-pvs.sh supports Linux and macOS. Use test-pvs.ps1 on Windows.\n' >&2
        exit 2
        ;;
esac

CHECKSUM_FILE="$ROOT/SHA256SUMS"
RELEASE_METADATA="$ROOT/RELEASE_METADATA.json"
RUN_STAMP="$(date -u +'%Y%m%dT%H%M%SZ')-$$"
RUN_PARENT="$ROOT/acceptance-runs"
mkdir -p -- "$RUN_PARENT"
RUN_ROOT="$(mktemp -d "$RUN_PARENT/${RUN_STAMP}-${PLATFORM_LABEL}.XXXXXX")"
LOG_PATH="$RUN_ROOT/acceptance.log"
VENV_DIRECTORY="$RUN_ROOT/.venv"
UV_VENV_DIRECTORY="$RUN_ROOT/uv-venv"
METADATA_VALUES="$RUN_ROOT/release-metadata-values.txt"
STAGE_JOURNAL="$RUN_ROOT/acceptance-stages.tsv"
SUMMARY_PATH="$RUN_ROOT/acceptance-summary.json"
SUMMARY_TOOL=""
SUMMARY_WRITTEN=0
CURRENT_STAGE=""
STAGE_STARTED=""
SELECTED_PYTHON=""
ACCEPTANCE_MODE="full"
(( QUICK )) && ACCEPTANCE_MODE="quick"


exec > >(tee -a "$LOG_PATH") 2>&1

finish_stage() {
    if [[ -n "$CURRENT_STAGE" ]]; then
        printf '%s\t%s\t%s\t%s\n' "$STAGE_STARTED" "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
            "$1" "$CURRENT_STAGE" >> "$STAGE_JOURNAL"
        CURRENT_STAGE=""
    fi
}

step() {
    finish_stage PASSED
    CURRENT_STAGE="$1"
    STAGE_STARTED="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    printf '\n==> %s\n' "$1"
}

write_summary() {
    "$SELECTED_PYTHON" "$SUMMARY_TOOL" finalize --run-root "$RUN_ROOT" \
        --release-root "$ROOT" --status "$1" --mode "$ACCEPTANCE_MODE" --output "$SUMMARY_PATH"
}

finish_acceptance() {
    local exit_status=$?
    trap - EXIT ERR
    set +e
    if (( ! SUMMARY_WRITTEN )); then
        finish_stage FAILED
        if [[ -n "$SELECTED_PYTHON" && -f "$SUMMARY_TOOL" ]]; then
            write_summary FAILED
        fi
        if [[ ! -f "$SUMMARY_PATH" ]]; then
            # Even bootstrap failures (including no Python) leave a machine-readable outcome.
            printf '{"schema":"pvs-acceptance-summary/1","status":"FAILED","mode":"%s","problems":["Bootstrap or summary generation failed; inspect acceptance.log"],"stage_journal":"acceptance-stages.tsv"}\n' \
                "$ACCEPTANCE_MODE" > "$SUMMARY_PATH"
        fi
        (( exit_status == 0 )) && exit_status=1
    fi
    cleanup_environments
    exit "$exit_status"
}

fail() {
    printf '\nPVS RELEASE ACCEPTANCE FAILED\n%s\n' "$1" >&2
    printf 'Diagnostic output is retained under: %s\n' "$RUN_ROOT" >&2
    trap - ERR
    exit 1
}

remove_test_environment() {
    local candidate="$1"
    local expected="$2"
    local description="$3"
    [[ -d "$candidate" ]] || return
    if [[ "$candidate" != "$expected" || "$candidate" != "$RUN_ROOT"/* ]]; then
        printf 'Refusing to remove an unexpected %s path: %s\n' "$description" "$candidate" >&2
        return
    fi
    if rm -rf -- "$candidate"; then
        printf 'Removed the disposable %s. Use --keep-environment to retain it.\n' "$description"
    else
        printf 'Warning: could not remove the %s: %s\n' "$description" "$candidate" >&2
    fi
}

cleanup_environments() {
    (( KEEP_ENVIRONMENT )) && return
    remove_test_environment "$VENV_DIRECTORY" "$RUN_ROOT/.venv" "acceptance virtual environment"
    remove_test_environment "$UV_VENV_DIRECTORY" "$RUN_ROOT/uv-venv" "frozen-lock virtual environment"
}

assert_evidence_pass() {
    local package_path="$1"
    "$PVS_PYTHON" - "$package_path/evidence.json" <<'PY'
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

path = Path(sys.argv[1])
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
PY
}

trap 'status=$?; printf "\nPVS RELEASE ACCEPTANCE FAILED (exit %s)\nDiagnostic output is retained under: %s\n" "$status" "$RUN_ROOT" >&2' ERR
trap finish_acceptance EXIT

for command_name in cp date sed tee tr unzip wc; do
    command -v "$command_name" >/dev/null 2>&1 || fail "Required command is missing: $command_name"
done

select_python() {
    local candidate
    for candidate in python3.12 python3.14 python3.13 python3.11 python3.10 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import platform, sys; label = sys.argv[1]; machine = platform.machine().lower(); supported_machine = machine in ({"x86_64", "amd64"} if label == "linux" else {"arm64", "aarch64"}); supported_libc = label != "linux" or platform.libc_ver()[0].lower() == "glibc"; raise SystemExit(0 if platform.python_implementation() == "CPython" and (3, 10) <= sys.version_info[:2] < (3, 15) and sys.maxsize > 2**32 and supported_machine and supported_libc else 1)' "$PLATFORM_LABEL" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

step "Selecting a supported Python"
BASE_PYTHON="$(select_python)" || fail "No release-qualified CPython was found. Install 64-bit CPython 3.10-3.14 for glibc Linux x86_64 or macOS arm64."
"$BASE_PYTHON" --version
SELECTED_PYTHON="$("$BASE_PYTHON" - "$RUN_ROOT/selected-interpreter.json" <<'PY'
import json
import platform
import sys
from pathlib import Path

base = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
identity = {"executable": str(Path(sys.executable).absolute()), "base_executable": base,
            "version": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": sys.platform, "machine": platform.machine()}
Path(sys.argv[1]).write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
print(base)
PY
)"
BASE_PYTHON="$SELECTED_PYTHON"

for required in "$CHECKSUM_FILE" "$RELEASE_METADATA"; do
    [[ -f "$required" ]] || fail "Required release file is missing: $required. Extract the complete release bundle and run its own test-pvs.sh."
done

step "Validating release metadata"
"$BASE_PYTHON" - "$RELEASE_METADATA" > "$METADATA_VALUES" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])

def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"duplicate RELEASE_METADATA.json member: {key!r}")
        result[key] = value
    return result

def reject_constant(value):
    raise SystemExit(f"non-finite RELEASE_METADATA.json number: {value}")

with path.open("r", encoding="utf-8") as stream:
    data = json.load(
        stream,
        object_pairs_hook=strict_object,
        parse_constant=reject_constant,
    )

def required(mapping, name, kind):
    value = mapping.get(name)
    if not isinstance(value, kind):
        raise SystemExit(f"RELEASE_METADATA.json field {name!r} has the wrong type")
    return value

def exact_keys(mapping, expected, location):
    actual = set(mapping)
    if actual != set(expected):
        raise SystemExit(
            f"RELEASE_METADATA.json {location} keys are {sorted(actual)}; "
            f"expected {sorted(expected)}"
        )

exact_keys(
    data,
    {"schema", "project", "version", "source_date_epoch", "release_bundle", "sample", "artifacts"},
    "root",
)
if required(data, "schema", str) != "pvs-release-metadata/1":
    raise SystemExit("unsupported RELEASE_METADATA.json schema")
if required(data, "project", str) != "physics-validation-suite":
    raise SystemExit("release metadata project is not 'physics-validation-suite'")
version = required(data, "version", str)
if version != "1.0.0a1":
    raise SystemExit(f"release metadata version is {version!r}; expected '1.0.0a1'")
sample = required(data, "sample", dict)
artifacts = required(data, "artifacts", dict)
exact_keys(
    artifacts,
    {
        "wheel", "sdist", "source_zip", "sample_evidence_zip", "sample_verification",
        "readme", "release_notes", "test_scripts", "sboms", "provenance",
    },
    "artifacts",
)
exact_keys(sample, {"finding_id", "evidence_id", "package_id"}, "sample")
test_scripts = required(artifacts, "test_scripts", dict)
exact_keys(test_scripts, {"powershell", "shell"}, "artifacts.test_scripts")

leaf_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,239}$")
def leaf(mapping, name):
    value = required(mapping, name, str)
    if not leaf_pattern.fullmatch(value) or value in {".", ".."}:
        raise SystemExit(f"release metadata field {name!r} is not a safe leaf name")
    return value

wheel = leaf(artifacts, "wheel")
sdist = leaf(artifacts, "sdist")
source_archive = leaf(artifacts, "source_zip")
sample_archive = leaf(artifacts, "sample_evidence_zip")
sample_verification = leaf(artifacts, "sample_verification")
release_notes = leaf(artifacts, "release_notes")
if leaf(artifacts, "readme") != "README.md":
    raise SystemExit("release metadata must identify README.md")
if leaf(test_scripts, "powershell") != "test-pvs.ps1" or leaf(test_scripts, "shell") != "test-pvs.sh":
    raise SystemExit("release metadata test-script names do not match the acceptance scripts")
provenance = leaf(artifacts, "provenance")
release_bundle = leaf(data, "release_bundle")
sboms = required(artifacts, "sboms", list)
if len(sboms) != 2 or len(set(sboms)) != 2 or any(not isinstance(item, str) for item in sboms):
    raise SystemExit("release metadata must identify exactly two distinct SBOM leaf filenames")
for item in sboms:
    if not leaf_pattern.fullmatch(item) or item in {".", ".."}:
        raise SystemExit("release metadata contains an unsafe SBOM filename")
expected_names = {
    "wheel": f"physics_validation_suite-{version}-py3-none-any.whl",
    "sdist": f"physics_validation_suite-{version}.tar.gz",
    "source_zip": f"physics-validation-suite-{version}-source.zip",
    "sample_evidence_zip": f"pvs-{version}-sample-plasma-evidence.zip",
    "sample_verification": "sample-plasma-verification.json",
    "release_notes": f"RELEASE_NOTES_{version}.md",
    "provenance": f"pvs-{version}.provenance.intoto.json",
    "release_bundle": f"pvs-{version}-release-bundle.zip",
}
actual_names = {
    "wheel": wheel,
    "sdist": sdist,
    "source_zip": source_archive,
    "sample_evidence_zip": sample_archive,
    "sample_verification": sample_verification,
    "release_notes": release_notes,
    "provenance": provenance,
    "release_bundle": release_bundle,
}
for name, expected in expected_names.items():
    if actual_names[name] != expected:
        raise SystemExit(
            f"release metadata field {name!r} is {actual_names[name]!r}; expected {expected!r}"
        )
expected_sboms = {f"{wheel}.cdx.json", f"{sdist}.cdx.json"}
if set(sboms) != expected_sboms:
    raise SystemExit(
        f"release metadata SBOM names are {sorted(sboms)}; expected {sorted(expected_sboms)}"
    )
all_leaf_names = [
    wheel,
    sdist,
    source_archive,
    sample_archive,
    sample_verification,
    "README.md",
    release_notes,
    "test-pvs.ps1",
    "test-pvs.sh",
    provenance,
    release_bundle,
    *sboms,
]
folded_names = [name.casefold() for name in all_leaf_names]
if len(folded_names) != len(set(folded_names)):
    raise SystemExit("release metadata contains duplicate or case-colliding filenames")
source_directory = f"physics_validation_suite-{version}"
sample_directory = f"sample-plasma-evidence-v{version}"

finding_id = required(sample, "finding_id", str)
evidence_id = required(sample, "evidence_id", str)
package_id = required(sample, "package_id", str)
patterns = {
    "finding_id": r"pvs-finding:v1:sha256:[0-9a-f]{64}",
    "evidence_id": r"pvs:sha256:[0-9a-f]{64}",
    "package_id": r"pvs-package:sha256:[0-9a-f]{64}",
}
for name, value in (("finding_id", finding_id), ("evidence_id", evidence_id), ("package_id", package_id)):
    if re.fullmatch(patterns[name], value) is None:
        raise SystemExit(f"release metadata contains an invalid {name}")

epoch = required(data, "source_date_epoch", int)
if isinstance(epoch, bool) or epoch < 315532800:
    raise SystemExit("release metadata source_date_epoch must be an integer at or after 1980-01-01")

for value in (
    version, wheel, sdist, source_archive, sample_archive, sample_verification,
    release_notes, source_directory, sample_directory, finding_id, evidence_id,
    package_id, str(epoch),
):
    print(value)
PY

[[ "$(wc -l < "$METADATA_VALUES" | tr -d ' ')" == "13" ]] || fail "RELEASE_METADATA.json did not produce the expected metadata contract."
RELEASE_VERSION="$(sed -n '1p' "$METADATA_VALUES")"
WHEEL_NAME="$(sed -n '2p' "$METADATA_VALUES")"
SDIST_NAME="$(sed -n '3p' "$METADATA_VALUES")"
SOURCE_NAME="$(sed -n '4p' "$METADATA_VALUES")"
SAMPLE_NAME="$(sed -n '5p' "$METADATA_VALUES")"
SAMPLE_VERIFICATION_NAME="$(sed -n '6p' "$METADATA_VALUES")"
RELEASE_NOTES_NAME="$(sed -n '7p' "$METADATA_VALUES")"
SOURCE_DIRECTORY_NAME="$(sed -n '8p' "$METADATA_VALUES")"
SAMPLE_DIRECTORY_NAME="$(sed -n '9p' "$METADATA_VALUES")"
FINDING_ID="$(sed -n '10p' "$METADATA_VALUES")"
EVIDENCE_ID="$(sed -n '11p' "$METADATA_VALUES")"
PACKAGE_ID="$(sed -n '12p' "$METADATA_VALUES")"
SOURCE_DATE_EPOCH="$(sed -n '13p' "$METADATA_VALUES")"

WHEEL="$ROOT/$WHEEL_NAME"
SDIST="$ROOT/$SDIST_NAME"
SOURCE_ARCHIVE="$ROOT/$SOURCE_NAME"
SAMPLE_ARCHIVE="$ROOT/$SAMPLE_NAME"

step "Checking every listed release SHA-256"
"$BASE_PYTHON" - "$ROOT" "$CHECKSUM_FILE" "$RELEASE_METADATA" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1]).resolve()
checksum_file = Path(sys.argv[2])
metadata = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
artifacts = metadata["artifacts"]
required = {
    "RELEASE_METADATA.json",
    artifacts["wheel"],
    artifacts["sdist"],
    artifacts["source_zip"],
    artifacts["sample_evidence_zip"],
    artifacts["sample_verification"],
    artifacts["readme"],
    artifacts["release_notes"],
    artifacts["test_scripts"]["powershell"],
    artifacts["test_scripts"]["shell"],
    artifacts["provenance"],
    *artifacts["sboms"],
}
seen = {}
line_pattern = re.compile(r"^([0-9A-Fa-f]{64})  (.+)$")
for number, line in enumerate(checksum_file.read_text(encoding="utf-8").splitlines(), 1):
    if not line:
        raise SystemExit(f"blank SHA256SUMS line {number}")
    match = line_pattern.fullmatch(line)
    if match is None:
        raise SystemExit(f"invalid SHA256SUMS line {number}")
    expected, relative_text = match.groups()
    relative = PurePosixPath(relative_text)
    if relative.is_absolute() or "\\" in relative_text or any(part in {"", ".", ".."} for part in relative.parts):
        raise SystemExit(f"unsafe SHA256SUMS path: {relative_text}")
    key = relative_text.casefold()
    if key in seen:
        raise SystemExit(
            f"duplicate/colliding SHA256SUMS paths: {seen[key]} and {relative_text}"
        )
    seen[key] = relative_text
    candidate = root.joinpath(*relative.parts)
    if not candidate.is_file() or candidate.is_symlink():
        raise SystemExit(f"listed release file is missing or linked: {relative_text}")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if digest != expected.lower():
        raise SystemExit(f"checksum mismatch for {relative_text}: expected {expected.lower()}, got {digest}")
    print(f"[PASS] {relative_text}")
missing = sorted(name for name in required if name.casefold() not in seen)
if missing:
    raise SystemExit(f"required release files are not in SHA256SUMS: {missing}")
extra = sorted(name for key, name in seen.items() if key not in {item.casefold() for item in required})
if extra:
    raise SystemExit(f"SHA256SUMS contains files absent from release metadata: {extra}")
PY

for required in "$WHEEL" "$SDIST" "$SOURCE_ARCHIVE" "$SAMPLE_ARCHIVE"; do
    [[ -f "$required" ]] || fail "Required release file is missing: $required"
done

printf 'Physics Validation Suite %s - %s release acceptance\n' "$RELEASE_VERSION" "$PLATFORM_LABEL"
printf 'Bundle root: %s\n' "$ROOT"
printf 'Run output:  %s\n' "$RUN_ROOT"
if (( QUICK )); then
    printf 'Mode:        quick\n'
else
    printf 'Mode:        full\n'
fi

step "Creating an isolated environment"
"$BASE_PYTHON" -m venv "$VENV_DIRECTORY"
PVS_PYTHON="$VENV_DIRECTORY/bin/python"
export PATH="$VENV_DIRECTORY/bin:$PATH"

step "Installing the released wheel with all runtime adapters"
"$PVS_PYTHON" -m pip install --upgrade pip
"$PVS_PYTHON" -m pip install --only-binary=:all: "$WHEEL[all]"
"$PVS_PYTHON" -m pip check
VERSION_OUTPUT="$("$PVS_PYTHON" -m pvs --version)"
printf '%s\n' "$VERSION_OUTPUT"
[[ "$VERSION_OUTPUT" == "PVS $RELEASE_VERSION" ]] || fail "Installed version output was '$VERSION_OUTPUT'; expected exactly 'PVS $RELEASE_VERSION'."

step "Extracting the pinned sample and source examples"
SAMPLE_EXTRACT="$RUN_ROOT/sample"
SOURCE_EXTRACT="$RUN_ROOT/source"
"$BASE_PYTHON" - \
    "$SAMPLE_ARCHIVE" "$SAMPLE_DIRECTORY_NAME" \
    "$SOURCE_ARCHIVE" "$SOURCE_DIRECTORY_NAME" <<'PY'
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

invalid = set('<>:"|?*')
reserved = {
    "CON", "PRN", "AUX", "CONIN$", "CONOUT$", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "\u00b9\u00b2\u00b3"),
}

for archive_text, expected_root in zip(sys.argv[1::2], sys.argv[2::2], strict=True):
    archive_path = Path(archive_text)
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
PY
unzip -q "$SAMPLE_ARCHIVE" -d "$SAMPLE_EXTRACT"
unzip -q "$SOURCE_ARCHIVE" -d "$SOURCE_EXTRACT"
SAMPLE_PACKAGE="$SAMPLE_EXTRACT/$SAMPLE_DIRECTORY_NAME"
SOURCE_ROOT="$SOURCE_EXTRACT/$SOURCE_DIRECTORY_NAME"
SUMMARY_TOOL="$SOURCE_ROOT/tools/acceptance-summary.py"
"$PVS_PYTHON" "$SUMMARY_TOOL" environment --profile runtime-all --lock "$SOURCE_ROOT/uv.lock" \
    --output "$RUN_ROOT/runtime-all.json"
[[ -d "$SAMPLE_PACKAGE" ]] || fail "The sample archive did not contain '$SAMPLE_DIRECTORY_NAME'."
[[ -d "$SOURCE_ROOT" ]] || fail "The source archive did not contain '$SOURCE_DIRECTORY_NAME'."

step "Verifying the supplied sample against all pinned identities"
assert_evidence_pass "$SAMPLE_PACKAGE"
"$PVS_PYTHON" -m pvs verify "$SAMPLE_PACKAGE" \
    --expect-finding-id "$FINDING_ID" \
    --expect-evidence-id "$EVIDENCE_ID" \
    --expect-package-id "$PACKAGE_ID"

step "Generating and verifying fresh existing-JSON evidence"
JSON_CASE="$SOURCE_ROOT/examples/json-existing/pvs.yaml"
JSON_EVIDENCE="$RUN_ROOT/${PLATFORM_LABEL}-json-evidence"
"$PVS_PYTHON" -m pvs inspect "$JSON_CASE"
"$PVS_PYTHON" -m pvs validate "$JSON_CASE" --output "$JSON_EVIDENCE"
assert_evidence_pass "$JSON_EVIDENCE"
"$PVS_PYTHON" -m pvs verify "$JSON_EVIDENCE"

step "Exercising the public Python API"
PYTHON_API_WORK="$RUN_ROOT/python-api"
mkdir -p -- "$PYTHON_API_WORK"
(
    cd -- "$PYTHON_API_WORK"
    "$PVS_PYTHON" "$SOURCE_ROOT/examples/python-api/integrate.py"
)
assert_evidence_pass "$PYTHON_API_WORK/pvs-api-evidence"

step "Proving that tampering is rejected"
TAMPER_PACKAGE="$RUN_ROOT/tampered-sample"
cp -a -- "$SAMPLE_PACKAGE" "$TAMPER_PACKAGE"
if [[ -f "$TAMPER_PACKAGE/report.html" ]]; then
    TAMPER_TARGET="$TAMPER_PACKAGE/report.html"
elif [[ -f "$TAMPER_PACKAGE/report.pdf" ]]; then
    TAMPER_TARGET="$TAMPER_PACKAGE/report.pdf"
else
    TAMPER_TARGET="$TAMPER_PACKAGE/evidence.json"
fi
printf '\nPVS deliberate acceptance-test tamper\n' >> "$TAMPER_TARGET"
if "$PVS_PYTHON" -m pvs verify "$TAMPER_PACKAGE"; then
    TAMPER_EXIT_CODE=0
else
    TAMPER_EXIT_CODE=$?
fi
if (( TAMPER_EXIT_CODE != 4 )); then
    fail "Tampered-package verification returned $TAMPER_EXIT_CODE; expected integrity exit code 4."
fi
printf '[PASS] Deliberately tampered package was rejected with exit code 4.\n'

if (( ! QUICK )); then
    step "Validating the generic existing-CSV adapter"
    CSV_OUTPUT="$RUN_ROOT/csv-evidence"
    "$PVS_PYTHON" -m pvs validate "$SOURCE_ROOT/examples/csv-existing/pvs.yaml" --output "$CSV_OUTPUT"
    assert_evidence_pass "$CSV_OUTPUT"
    "$PVS_PYTHON" -m pvs verify "$CSV_OUTPUT"

    step "Running the generic command-Python adapter"
    COMMAND_OUTPUT="$RUN_ROOT/command-python-evidence"
    "$PVS_PYTHON" -m pvs run "$SOURCE_ROOT/examples/command-python/pvs.yaml" --output "$COMMAND_OUTPUT"
    assert_evidence_pass "$COMMAND_OUTPUT"
    "$PVS_PYTHON" -m pvs verify "$COMMAND_OUTPUT"

    step "Running the generic NetCDF command adapter"
    NETCDF_OUTPUT="$RUN_ROOT/netcdf-evidence"
    "$PVS_PYTHON" -m pvs run "$SOURCE_ROOT/examples/netcdf-command/pvs.yaml" --output "$NETCDF_OUTPUT"
    assert_evidence_pass "$NETCDF_OUTPUT"
    "$PVS_PYTHON" -m pvs verify "$NETCDF_OUTPUT"

    step "Qualifying all built-in benchmark packs"
    "$PVS_PYTHON" "$SOURCE_ROOT/tools/run-benchmark-packs.py" \
        --output "$RUN_ROOT/benchmark-evidence"

    step "Running the audited plasma reference benchmark"
    PLASMA_OUTPUT="$RUN_ROOT/plasma-evidence"
    "$PVS_PYTHON" -m pvs run "$SOURCE_ROOT/benchmark-packs/plasma-physics-reference-v1/pvs.yaml" --output "$PLASMA_OUTPUT"
    assert_evidence_pass "$PLASMA_OUTPUT"
    "$PVS_PYTHON" -m pvs verify "$PLASMA_OUTPUT"
    "$PVS_PYTHON" "$SUMMARY_TOOL" finding-diff "$SAMPLE_PACKAGE/evidence.json" \
        "$PLASMA_OUTPUT/evidence.json" --output "$RUN_ROOT/plasma-finding-diff.json"

    step "Extracting a pristine source tree for engineering gates"
    DEVELOPMENT_SOURCE_EXTRACT="$RUN_ROOT/source-development"
    unzip -q "$SOURCE_ARCHIVE" -d "$DEVELOPMENT_SOURCE_EXTRACT"
    DEVELOPMENT_SOURCE_ROOT="$DEVELOPMENT_SOURCE_EXTRACT/$SOURCE_DIRECTORY_NAME"
    [[ -d "$DEVELOPMENT_SOURCE_ROOT" ]] || fail "The source archive did not contain its expected development source directory."

    step "Installing development and frozen-lock tooling"
    "$PVS_PYTHON" -m pip install --only-binary=:all: --editable "$DEVELOPMENT_SOURCE_ROOT[dev,all]"
    "$PVS_PYTHON" -m pip install --only-binary=:all: "uv==0.11.33"
    "$PVS_PYTHON" -m pip check
    UV_BIN="$VENV_DIRECTORY/bin/uv"
    [[ -x "$UV_BIN" ]] || fail "uv was installed but its executable was not found."

    "$PVS_PYTHON" "$SUMMARY_TOOL" environment --profile ordinary-dev-all \
        --lock "$DEVELOPMENT_SOURCE_ROOT/uv.lock" --output "$RUN_ROOT/ordinary-dev-all.json"

    step "Running pytest, Ruff, and strict cross-platform mypy"
    PYTEST_ARGUMENTS=("tools/run-pytest.py")
    if (( KEEP_ENVIRONMENT )); then
        PYTEST_ARGUMENTS+=(--keep-temp)
    fi
    (
        cd -- "$DEVELOPMENT_SOURCE_ROOT"
        "$PVS_PYTHON" "${PYTEST_ARGUMENTS[@]}" --junitxml "$RUN_ROOT/ordinary-pytest.xml"
        "$PVS_PYTHON" -m ruff check .
        "$PVS_PYTHON" -m mypy
        "$PVS_PYTHON" -m mypy --platform linux
        "$PVS_PYTHON" -m mypy --platform darwin
        "$PVS_PYTHON" -m mypy --platform win32
    )

    step "Qualifying the exact frozen dependency closure"
    (
        cd -- "$DEVELOPMENT_SOURCE_ROOT"
        export UV_PROJECT_ENVIRONMENT="$UV_VENV_DIRECTORY"
        "$UV_BIN" lock --check --python "$SELECTED_PYTHON"
        "$UV_BIN" sync --frozen --extra dev --extra all --python "$SELECTED_PYTHON"
        "$UV_VENV_DIRECTORY/bin/python" "$SUMMARY_TOOL" environment --profile frozen-dev-all \
            --lock "$DEVELOPMENT_SOURCE_ROOT/uv.lock" --output "$RUN_ROOT/frozen-dev-all.json"
        "$UV_BIN" run --frozen --extra dev --extra all --python "$SELECTED_PYTHON" python "${PYTEST_ARGUMENTS[@]}" --junitxml "$RUN_ROOT/frozen-pytest.xml"
    )

    step "Proving same-host distribution repeatability and checking metadata"
    REBUILT_DIRECTORY="$RUN_ROOT/rebuilt-dist"
    (
        cd -- "$DEVELOPMENT_SOURCE_ROOT"
        export UV_PROJECT_ENVIRONMENT="$UV_VENV_DIRECTORY"
        SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" \
            PVS_BUILD_SOURCE_ARCHIVE="$SOURCE_ARCHIVE" \
            "$UV_BIN" run --frozen --extra dev --extra all --python "$SELECTED_PYTHON" \
            python tools/check-reproducible-build.py
        SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" \
            PVS_BUILD_SOURCE_ARCHIVE="$SOURCE_ARCHIVE" \
            "$UV_BIN" run --frozen --extra dev --extra all --python "$SELECTED_PYTHON" \
            python -m build --no-isolation --outdir "$REBUILT_DIRECTORY"
        "$UV_BIN" run --frozen --extra dev --extra all --python "$SELECTED_PYTHON" \
            python -m twine check "$WHEEL" "$SDIST"
        "$UV_BIN" run --frozen --extra dev --extra all --python "$SELECTED_PYTHON" \
            python -m twine check "$REBUILT_DIRECTORY"/*
    )
else
    printf '\nQuick mode: skipped CSV/POSIX execution and repository quality, lock, reproducibility and supply-chain gates.\n'
fi

finish_stage PASSED
write_summary PASSED
SUMMARY_WRITTEN=1
trap - ERR
printf '\n==> %s release acceptance passed\n' "$PLATFORM_LABEL"
printf 'Summary:  %s\n' "$SUMMARY_PATH"
printf 'Fresh PDF: %s\n' "$JSON_EVIDENCE/report.pdf"
printf 'Full log:  %s\n' "$LOG_PATH"
