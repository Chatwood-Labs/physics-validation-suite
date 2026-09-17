#!/usr/bin/env python3
"""Assemble and audit a deterministic PVS release bundle.

This tool intentionally accepts every release input explicitly.  It does not
build the wheel, sdist, evidence package, SBOMs, or provenance statement; it
validates those already-produced inputs, packages them deterministically, and
then reopens every archive to verify exact membership and content hashes.
"""

from __future__ import annotations

import argparse
import contextlib
import email.parser
import hashlib
import importlib
import io
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast
from urllib.parse import quote

PROJECT_NAME = "physics-validation-suite"
DIST_NAME = "physics_validation_suite"
METADATA_SCHEMA = "pvs-release-metadata/1"
CONTENT_IDENTITY_PROFILE = "pvs-installed-package-tree-sha256-v1"
SOURCE_ARCHIVE_PROFILE = "pvs-source-archive-sha256-v1"
REQUIRED_SBOM_RUNTIME_ROOTS = {
    "fqdn",
    "idna",
    "isoduration",
    "jsonschema",
    "jsonpointer",
    "netcdf4",
    "numpy",
    "packaging",
    "pypdf",
    "pyyaml",
    "reportlab",
    "rfc3339-validator",
    "rfc3986-validator",
    "rfc3987-syntax",
    "rfc8785",
    "uri-template",
    "webcolors",
}
CYCLONEDX_SPEC_VERSION = "1.6"
SLSA_BUILD_TYPE = "https://chatwoodlabs.com/buildtypes/pvs-python-distribution/v1"
ZIP_MIN_EPOCH = 315532800  # 1980-01-01T00:00:00Z
ZIP_MAX_EPOCH = 4354819198  # Last even ZIP/DOS second in 2107 UTC.
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9.+-]*)?$")
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "CONIN$",
    "CONOUT$",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "\u00b9\u00b2\u00b3"),
}
WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')
EXCLUDED_DIRECTORY_NAMES = {
    ".eggs",
    ".git",
    ".hg",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "acceptance-runs",
    "build",
    "dist",
    "env",
    "htmlcov",
    "release",
    "releases",
    "venv",
}
EXCLUDED_ROOT_FILES = {
    ".coverage",
    "coverage.xml",
    "RELEASE_METADATA.json",
    "SHA256SUMS",
}
REQUIRED_SAMPLE_PATHS = {
    "case/pvs.yaml",
    "evidence.json",
    "manifest.json",
    "report.html",
    "report.pdf",
}


class ReleaseAssemblyError(RuntimeError):
    """The release inputs or assembled output failed a release invariant."""


@dataclass(frozen=True)
class ReleaseInputs:
    version: str
    source_tree: Path
    source_archive: Path
    dist_dir: Path
    wheel: Path
    sdist: Path
    sample_package: Path
    sample_verification: Path
    readme: Path
    release_notes: Path
    powershell_script: Path
    shell_script: Path
    sboms: tuple[Path, ...]
    provenance: Path
    builder_id: str
    build_command: str
    output_dir: Path


@dataclass(frozen=True)
class SampleIdentity:
    evidence_id: str
    finding_id: str
    package_id: str


@dataclass(frozen=True)
class ArchiveFile:
    name: str
    data: bytes
    mode: int = 0o644


FindingMetadataFactory = Callable[[dict[str, Any]], dict[str, Any]]
SampleVerifier = Callable[..., Any]
ReproducibilityChecker = Callable[[Path, Path, Path, int], None]
SbomReproducer = Callable[[Path, Path, Path, int], dict[str, Any]]


def _fail(message: str) -> NoReturn:
    raise ReleaseAssemblyError(message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON member: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    _fail(f"non-finite JSON number is forbidden: {value}")


def _load_json_bytes(data: bytes, *, description: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseAssemblyError(f"{description} is not UTF-8: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ReleaseAssemblyError) as exc:
        raise ReleaseAssemblyError(f"invalid {description}: {exc}") from exc


def _pretty_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def _canonical_digest(value: Any, *, description: str) -> str:
    try:
        import rfc8785
    except ImportError as exc:
        raise ReleaseAssemblyError(
            "bundle assembly requires the release dependency rfc8785"
        ) from exc
    try:
        return _sha256(rfc8785.dumps(value))
    except (TypeError, ValueError) as exc:
        raise ReleaseAssemblyError(f"{description} is not RFC 8785/I-JSON data: {exc}") from exc


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _stat_is_link_or_reparse(metadata: os.stat_result) -> bool:
    """Recognize POSIX links and Windows junction/reparse-point metadata."""

    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    file_attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def _require_directory(path: Path, *, label: str) -> Path:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseAssemblyError(f"missing {label}: {path}") from exc
    if _stat_is_link_or_reparse(metadata):
        _fail(f"{label} must not be a symlink: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        _fail(f"{label} is not a directory: {path}")
    return path.resolve(strict=True)


def _prepare_empty_directory(path: Path, *, label: str) -> Path:
    """Create an output directory, or require an existing one to be empty."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        path.mkdir(parents=True, exist_ok=False)
        return _require_directory(path, label=label)
    if _stat_is_link_or_reparse(metadata):
        _fail(f"{label} must not be a symlink: {path}")
    if not stat.S_ISDIR(metadata.st_mode):
        _fail(f"{label} is not a directory: {path}")
    resolved = path.resolve(strict=True)
    try:
        with os.scandir(resolved) as entries:
            first_entry = next(entries, None)
    except OSError as exc:
        raise ReleaseAssemblyError(f"could not inspect {label}: {path}: {exc}") from exc
    if first_entry is not None:
        _fail(f"{label} must be empty: {path}")
    return resolved


def _read_stable_file(path: Path, *, label: str) -> bytes:
    """Read one regular file while rejecting links and observable mutation."""

    try:
        before_path = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseAssemblyError(f"missing {label}: {path}") from exc
    if _stat_is_link_or_reparse(before_path):
        _fail(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(before_path.st_mode):
        _fail(f"{label} must be a regular file: {path}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseAssemblyError(f"could not open {label} safely: {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail(f"{label} changed to a non-regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseAssemblyError(f"{label} disappeared while being read: {path}") from exc

    # Deliberately exclude atime: reading a file can update it, especially on
    # Windows.  The remaining identity and mutation fields must stay fixed.
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        _fail(f"{label} changed while being read: {path}")
    if any(getattr(before_path, field) != getattr(after_path, field) for field in fields):
        _fail(f"{label} path changed while being read: {path}")
    if before.st_size != sum(map(len, chunks)):
        _fail(f"{label} size changed while being read: {path}")
    return b"".join(chunks)


def _portable_name(name: str, *, label: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        _fail(f"unsafe {label} path: {name!r}")
    pure = PurePosixPath(name)
    if (
        pure.is_absolute()
        or str(pure) != name
        or unicodedata.normalize("NFC", name) != name
    ):
        _fail(f"non-canonical {label} path: {name!r}")
    for part in pure.parts:
        if part in {"", ".", ".."}:
            _fail(f"unsafe {label} path component: {name!r}")
        if part != part.strip() or part.endswith("."):
            _fail(f"non-portable {label} path component: {part!r}")
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            _fail(f"control character in {label} path: {name!r}")
        if any(character in WINDOWS_INVALID_CHARACTERS for character in part):
            _fail(f"non-portable character in {label} path: {name!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED:
            _fail(f"Windows-reserved {label} path component: {part!r}")
    return name


def _ensure_unique_names(names: Iterable[str], *, label: str) -> None:
    seen: dict[str, str] = {}
    for name in names:
        normalized = _portable_name(name, label=label)
        key = normalized.casefold()
        previous = seen.get(key)
        if previous is not None:
            _fail(f"{label} paths collide portably: {previous!r} and {name!r}")
        seen[key] = name


def _excluded_source_path(relative: PurePosixPath, *, outer_bundle_name: str) -> bool:
    if any(
        part in EXCLUDED_DIRECTORY_NAMES
        or part.endswith(".egg-info")
        or part.startswith(".pvs-tmp-")
        for part in relative.parts[:-1]
    ):
        return True
    name = relative.name
    if name in EXCLUDED_ROOT_FILES or name in EXCLUDED_DIRECTORY_NAMES:
        return True
    if len(relative.parts) == 1:
        if name == outer_bundle_name:
            return True
        generated_patterns = (
            "pvs-*-release-bundle.zip",
            "physics_validation_suite-*.whl",
            "physics_validation_suite-*.tar.gz",
            "physics-validation-suite-*-source.zip",
            "pvs-*-sample-plasma-evidence.zip",
        )
        if any(relative.match(pattern) for pattern in generated_patterns):
            return True
    return name.endswith((".pyc", ".pyo"))


def _collect_tree(
    root: Path,
    *,
    prefix: str,
    label: str,
    exclude_source_outputs: bool,
    outer_bundle_name: str,
) -> list[ArchiveFile]:
    root = _require_directory(root, label=label)
    result: list[ArchiveFile] = []
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for directory in sorted(directories):
            path = current_path / directory
            metadata = path.lstat()
            relative = PurePosixPath(path.relative_to(root).as_posix())
            _portable_name(relative.as_posix(), label=label)
            if _stat_is_link_or_reparse(metadata):
                _fail(f"{label} contains a symlink: {relative.as_posix()}")
            if not stat.S_ISDIR(metadata.st_mode):
                _fail(f"{label} contains a non-directory traversal entry: {relative.as_posix()}")
            excluded = exclude_source_outputs and (
                directory in EXCLUDED_DIRECTORY_NAMES
                or directory.endswith(".egg-info")
                or directory.startswith(".pvs-tmp-")
            )
            if not excluded:
                safe_directories.append(directory)
        directories[:] = safe_directories

        for filename in sorted(filenames):
            path = current_path / filename
            relative = PurePosixPath(path.relative_to(root).as_posix())
            _portable_name(relative.as_posix(), label=label)
            metadata = path.lstat()
            if _stat_is_link_or_reparse(metadata):
                _fail(f"{label} contains a symlink: {relative.as_posix()}")
            if not stat.S_ISREG(metadata.st_mode):
                _fail(f"{label} contains a non-regular file: {relative.as_posix()}")
            if exclude_source_outputs and _excluded_source_path(
                relative, outer_bundle_name=outer_bundle_name
            ):
                continue
            archive_name = f"{prefix}/{relative.as_posix()}"
            mode = 0o755 if relative.as_posix() == "test-pvs.sh" else 0o644
            result.append(
                ArchiveFile(
                    archive_name,
                    _read_stable_file(path, label=f"{label} file"),
                    mode,
                )
            )
    if not result:
        _fail(f"{label} contains no release files: {root}")
    result.sort(key=lambda item: item.name)
    _ensure_unique_names((item.name for item in result), label=label)
    return result


def _zip_datetime(source_date_epoch: int) -> tuple[int, int, int, int, int, int]:
    if type(source_date_epoch) is not int:
        _fail("SOURCE_DATE_EPOCH must be an integer")
    if not ZIP_MIN_EPOCH <= source_date_epoch <= ZIP_MAX_EPOCH:
        _fail(
            "SOURCE_DATE_EPOCH is outside the ZIP-compatible UTC range "
            f"[{ZIP_MIN_EPOCH}, {ZIP_MAX_EPOCH}]"
        )
    value = list(time.gmtime(source_date_epoch)[:6])
    value[5] -= value[5] % 2
    return tuple(value)  # type: ignore[return-value]


def _write_zip(path: Path, files: Sequence[ArchiveFile], *, source_date_epoch: int) -> None:
    _ensure_unique_names((item.name for item in files), label="ZIP member")
    timestamp = _zip_datetime(source_date_epoch)
    with zipfile.ZipFile(
        path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for item in sorted(files, key=lambda value: value.name):
            info = zipfile.ZipInfo(item.name, date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.extract_version = 20
            info.external_attr = (stat.S_IFREG | item.mode) << 16
            info.flag_bits |= 0x800
            archive.writestr(info, item.data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _audit_zip(
    path: Path,
    expected: Sequence[ArchiveFile],
    *,
    source_date_epoch: int,
) -> None:
    expected_by_name = {item.name: item for item in expected}
    expected_timestamp = _zip_datetime(source_date_epoch)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            _ensure_unique_names(names, label=f"{path.name} member")
            if names != sorted(expected_by_name):
                _fail(
                    f"{path.name} coverage/order mismatch: expected "
                    f"{sorted(expected_by_name)!r}, got {names!r}"
                )
            for info in infos:
                expected_item = expected_by_name[info.filename]
                if info.is_dir():
                    _fail(f"unexpected directory member in {path.name}: {info.filename}")
                if info.date_time != expected_timestamp:
                    _fail(f"non-deterministic timestamp in {path.name}: {info.filename}")
                mode = (info.external_attr >> 16) & 0o777
                if mode != expected_item.mode:
                    _fail(
                        f"mode mismatch in {path.name}: {info.filename}: "
                        f"expected {expected_item.mode:o}, got {mode:o}"
                    )
                data = archive.read(info)
                if _sha256(data) != _sha256(expected_item.data):
                    _fail(f"content hash mismatch in {path.name}: {info.filename}")
            bad_member = archive.testzip()
            if bad_member is not None:
                _fail(f"CRC failure in {path.name}: {bad_member}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseAssemblyError(f"could not audit {path}: {exc}") from exc


def _validate_version(version: str) -> None:
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        _fail(f"invalid release version: {version!r}")


def _validate_source_version(source_tree: Path, version: str) -> None:
    pyproject = _read_stable_file(source_tree / "pyproject.toml", label="source pyproject.toml")
    try:
        text = pyproject.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseAssemblyError(f"invalid source pyproject.toml: {exc}") from exc
    section = re.search(r"(?ms)^\[project\][ \t]*\r?\n(.*?)(?=^\[|\Z)", text)
    if section is None:
        _fail("source pyproject.toml has no [project] table")

    def project_string(key: str) -> str | None:
        match = re.search(
            rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(['\"])([^'\"\r\n]+)\1[ \t]*\r?$",
            section.group(1),
        )
        return match.group(2) if match is not None else None

    actual = project_string("version")
    if actual != version:
        _fail(f"source project version is {actual!r}; expected {version!r}")
    if project_string("name") != PROJECT_NAME:
        _fail(f"source project name is not {PROJECT_NAME!r}")


def _validate_zip_member_names(archive: zipfile.ZipFile, *, label: str) -> None:
    infos = archive.infolist()
    # ZipInfo.filename normalises os.sep on Windows. Use the raw member name so
    # release audits enforce one canonical archive contract on every host.
    names = [item.orig_filename for item in infos]
    _ensure_unique_names(names, label=label)
    for item in infos:
        mode = (item.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            _fail(f"{label} contains a symlink: {item.filename}")
        if item.is_dir():
            continue
        if mode not in {0, stat.S_IFREG}:
            _fail(f"{label} contains a non-regular member: {item.filename}")


def _validate_wheel(path: Path, *, version: str, dist_dir: Path) -> bytes:
    expected_name = f"{DIST_NAME}-{version}-py3-none-any.whl"
    if path.name != expected_name:
        _fail(f"wheel filename must be {expected_name!r}, got {path.name!r}")
    resolved = path.resolve(strict=False)
    if not _is_relative_to(resolved, dist_dir):
        _fail(f"wheel must be inside the supplied dist directory: {path}")
    data = _read_stable_file(path, label="wheel")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            _validate_zip_member_names(archive, label="wheel")
            metadata_paths = [
                item.filename
                for item in archive.infolist()
                if item.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                _fail("wheel must contain exactly one .dist-info/METADATA")
            metadata_text = archive.read(metadata_paths[0]).decode("utf-8")
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise ReleaseAssemblyError(f"invalid wheel {path}: {exc}") from exc
    metadata = email.parser.Parser().parsestr(metadata_text)
    if metadata.get("Name") != PROJECT_NAME or metadata.get("Version") != version:
        _fail(
            "wheel metadata identity mismatch: "
            f"Name={metadata.get('Name')!r}, Version={metadata.get('Version')!r}"
        )
    return data


def _installed_content_identity_from_wheel(wheel_data: bytes) -> dict[str, Any]:
    """Recompute the installed PVS tree identity directly from wheel bytes."""

    entries: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_data)) as archive:
            _validate_zip_member_names(archive, label="wheel")
            for info in archive.infolist():
                if info.is_dir() or not info.filename.startswith("pvs/"):
                    continue
                relative = info.filename.removeprefix("pvs/")
                parts = PurePosixPath(relative).parts
                if "__pycache__" in parts or relative.endswith((".pyc", ".pyo")):
                    continue
                data = archive.read(info)
                entries.append(
                    {
                        "path": relative,
                        "sha256": _sha256(data),
                        "size_bytes": len(data),
                    }
                )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseAssemblyError(f"could not derive installed wheel identity: {exc}") from exc
    entries.sort(key=lambda item: str(item["path"]))
    _ensure_unique_names(
        (str(item["path"]) for item in entries),
        label="installed PVS content",
    )
    if not entries:
        _fail("wheel contains no importable pvs/ package files")
    manifest = {"profile": CONTENT_IDENTITY_PROFILE, "files": entries}
    return {
        "profile": CONTENT_IDENTITY_PROFILE,
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "digest": _canonical_digest(manifest, description="installed PVS wheel content"),
        "files_count": len(entries),
    }


def _release_build_info(source_archive_digest: str, source_date_epoch: int) -> bytes:
    """Return the only generated runtime file permitted to differ from source."""

    source_identity = {
        "profile": SOURCE_ARCHIVE_PROFILE,
        "algorithm": "sha256",
        "digest": source_archive_digest,
    }
    return (
        '"""Build provenance generated by the PVS build backend."""\n\n'
        f"SOURCE_IDENTITY: dict[str, str] | None = {source_identity!r}\n"
        "SOURCE_REPOSITORY: str | None = None\n"
        "SOURCE_REVISION: str | None = None\n"
        "SOURCE_DIRTY: bool | None = None\n"
        f"SOURCE_DATE_EPOCH: int | None = {source_date_epoch!r}\n"
    ).encode()


def _validate_wheel_source_binding(
    wheel_data: bytes,
    source_files: Sequence[ArchiveFile],
    *,
    version: str,
    source_archive_data: bytes,
    source_date_epoch: int,
) -> None:
    """Require every installed PVS byte to derive from the published source ZIP."""

    prefix = f"physics_validation_suite-{version}/src/pvs/"
    expected = {
        item.name.removeprefix(prefix): item.data
        for item in source_files
        if item.name.startswith(prefix)
    }
    if not expected:
        _fail("source archive contains no src/pvs package files")
    if "_build_info.py" in expected:
        expected["_build_info.py"] = _release_build_info(
            _sha256(source_archive_data), source_date_epoch
        )
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_data)) as archive:
            actual = {
                info.filename.removeprefix("pvs/"): archive.read(info)
                for info in archive.infolist()
                if not info.is_dir() and info.filename.startswith("pvs/")
            }
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseAssemblyError(f"could not inspect wheel PVS payload: {exc}") from exc
    _ensure_unique_names(expected, label="source PVS package")
    _ensure_unique_names(actual, label="wheel PVS package")
    if set(actual) != set(expected):
        _fail(
            "wheel PVS payload does not exactly match published source membership: "
            f"wheel={sorted(actual)!r} source={sorted(expected)!r}"
        )
    mismatches = [name for name in sorted(expected) if actual[name] != expected[name]]
    if mismatches:
        _fail(
            "wheel PVS payload bytes do not match the published source archive: "
            f"{mismatches!r}"
        )


def _run_reproducibility_gate(
    source_tree: Path,
    source_archive: Path,
    dist_dir: Path,
    source_date_epoch: int,
) -> None:
    """Rebuild pristine source twice and bind both distributions before publication."""

    checker = source_tree / "tools" / "check-reproducible-build.py"
    if not checker.is_file():
        _fail(f"source tree is missing reproducibility checker: {checker}")
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(source_date_epoch)
    environment["PVS_BUILD_SOURCE_ARCHIVE"] = str(source_archive)
    process = subprocess.run(
        [
            sys.executable,
            str(checker),
            "--source-date-epoch",
            str(source_date_epoch),
            "--expect-directory",
            str(dist_dir),
        ],
        cwd=source_tree,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if process.returncode != 0:
        _fail(f"released distributions failed pristine reproducibility gate:\n{process.stdout}")


def _reproduce_sbom(
    subject: Path,
    lock_path: Path,
    source_archive: Path,
    source_date_epoch: int,
) -> dict[str, Any]:
    """Regenerate an SBOM from the assembly environment's installed closure."""

    machine = platform.machine().lower()
    libc_name = platform.libc_ver()[0].lower()
    if (
        platform.python_implementation() != "CPython"
        or sys.version_info[:2] != (3, 12)
        or sys.platform != "linux"
        or machine not in {"x86_64", "amd64"}
        or libc_name != "glibc"
    ):
        _fail(
            "official SBOM reproduction requires the canonical CPython 3.12 "
            "glibc Linux x86_64 release environment"
        )
    try:
        from pvs.supply_chain import cyclonedx_sbom

        return cyclonedx_sbom(
            subject,
            lock_path,
            source_archive,
            source_date_epoch=source_date_epoch,
        )
    except Exception as exc:
        raise ReleaseAssemblyError(
            "could not independently reproduce the release SBOM; run assembly in the "
            f"clean, frozen release environment: {exc}"
        ) from exc


def _validate_sdist(path: Path, *, version: str, dist_dir: Path) -> bytes:
    expected_name = f"{DIST_NAME}-{version}.tar.gz"
    if path.name != expected_name:
        _fail(f"sdist filename must be {expected_name!r}, got {path.name!r}")
    resolved = path.resolve(strict=False)
    if not _is_relative_to(resolved, dist_dir):
        _fail(f"sdist must be inside the supplied dist directory: {path}")
    data = _read_stable_file(path, label="sdist")
    top_level = f"{DIST_NAME}-{version}"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            names: list[str] = []
            package_info: bytes | None = None
            for member in archive.getmembers():
                normalized = _portable_name(member.name, label="sdist member")
                names.append(normalized)
                if not (normalized == top_level or normalized.startswith(f"{top_level}/")):
                    _fail(f"sdist member escapes expected top level: {normalized!r}")
                if member.issym() or member.islnk():
                    _fail(f"sdist contains a link: {normalized}")
                if not (member.isdir() or member.isreg()):
                    _fail(f"sdist contains a non-regular member: {normalized}")
                if normalized == f"{top_level}/PKG-INFO":
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        _fail("could not read sdist PKG-INFO")
                    package_info = extracted.read()
            _ensure_unique_names(names, label="sdist member")
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseAssemblyError(f"invalid sdist {path}: {exc}") from exc
    if package_info is None:
        _fail("sdist does not contain top-level PKG-INFO")
    try:
        metadata = email.parser.BytesParser().parsebytes(package_info)
    except (TypeError, ValueError) as exc:
        raise ReleaseAssemblyError(f"invalid sdist PKG-INFO: {exc}") from exc
    if metadata.get("Name") != PROJECT_NAME or metadata.get("Version") != version:
        _fail(
            "sdist metadata identity mismatch: "
            f"Name={metadata.get('Name')!r}, Version={metadata.get('Version')!r}"
        )
    return data


def _validate_identity_field(
    integrity: Mapping[str, Any], *, kind: str, prefix: str, digest: str
) -> str:
    if integrity.get("canonicalization") != "RFC8785":
        _fail(f"sample {kind} does not declare RFC8785 canonicalization")
    if integrity.get("algorithm") != "sha256":
        _fail(f"sample {kind} does not declare sha256")
    if integrity.get("digest") != digest:
        _fail(f"sample {kind} digest does not match recomputed content")
    identity = integrity.get(f"{kind}_id")
    expected = f"{prefix}{digest}"
    if identity != expected:
        _fail(f"sample {kind} ID is {identity!r}; expected {expected!r}")
    return expected


def _validate_sample_using_source(
    source_tree: Path,
    version: str,
    record: dict[str, Any],
    sample_package: Path,
    identity: SampleIdentity,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_package = source_tree / "src"
    if not source_package.is_dir():
        _fail(f"source tree does not contain src/: {source_tree}")
    previous_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "pvs" or name.startswith("pvs.")
    }
    for name in previous_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(source_package))
    try:
        version_module = importlib.import_module("pvs.version")
        finding_module = importlib.import_module("pvs.finding")
        verify_module = importlib.import_module("pvs.verify")
        actual = getattr(version_module, "__version__", None)
        if actual != version:
            _fail(f"imported PVS version is {actual!r}; expected {version!r}")
        for module, label in ((finding_module, "finding"), (verify_module, "verification")):
            origin = getattr(module, "__file__", None)
            if not isinstance(origin, str) or not _is_relative_to(
                Path(origin).resolve(strict=True), source_package
            ):
                _fail(f"imported PVS {label} module did not come from the supplied source tree")
        factory = getattr(finding_module, "create_finding_metadata", None)
        if not callable(factory):
            _fail("supplied PVS source has no callable create_finding_metadata")
        verifier = getattr(verify_module, "verify_target", None)
        if not callable(verifier):
            _fail("supplied PVS source has no callable verify_target")
        recomputed = cast(FindingMetadataFactory, factory)(record)
        # Release assembly first verifies without a Package-ID pin. This makes
        # deterministic report replay mandatory and prevents the verifier's
        # deliberate caller-pinned byte-identity escape hatch from weakening
        # the release gate when renderer versions differ.
        result = cast(SampleVerifier, verifier)(
            sample_package,
            expect_evidence_id=identity.evidence_id,
            expect_finding_id=identity.finding_id,
        )
        if not getattr(result, "valid", False):
            failing = [
                item.get("detail", "unknown verification failure")
                for item in getattr(result, "checks", [])
                if item.get("status") != "PASS"
            ]
            _fail(f"PVS sample verification failed: {'; '.join(failing[:5])}")
        if getattr(result, "package_id", None) != identity.package_id:
            _fail("PVS sample verification returned an unexpected Package ID")

        # A second pass produces the release-facing verification result with
        # all three independently recomputed identities pinned.
        pinned_result = cast(SampleVerifier, verifier)(
            sample_package,
            expect_evidence_id=identity.evidence_id,
            expect_finding_id=identity.finding_id,
            expect_package_id=identity.package_id,
        )
        if not getattr(pinned_result, "valid", False):
            _fail("PVS pinned sample verification failed after unpinned renderer replay")
        as_dict = getattr(pinned_result, "as_dict", None)
        if not callable(as_dict):
            _fail("PVS sample verifier did not expose a machine-readable result")
        verification = as_dict()
        if not isinstance(verification, dict):
            _fail("PVS sample verifier returned a malformed machine-readable result")
        return recomputed, verification
    except ReleaseAssemblyError:
        raise
    except Exception as exc:
        raise ReleaseAssemblyError(
            "supplied PVS source could not recompute and verify the sample package; "
            f"install its release dependencies first: {exc}"
        ) from exc
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(source_package))
        for name in list(sys.modules):
            if name == "pvs" or name.startswith("pvs."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_modules)


def _validate_sample(
    sample_package: Path,
    *,
    version: str,
    source_tree: Path,
    finding_metadata_factory: FindingMetadataFactory | None,
    outer_bundle_name: str,
    expected_content_identity: dict[str, Any],
    expected_source_archive_digest: str,
    source_date_epoch: int,
) -> tuple[list[ArchiveFile], SampleIdentity, dict[str, Any] | None]:
    expected_directory = f"sample-plasma-evidence-v{version}"
    if sample_package.name != expected_directory:
        _fail(
            f"sample package directory must be {expected_directory!r}, got {sample_package.name!r}"
        )
    files = _collect_tree(
        sample_package,
        prefix=expected_directory,
        label="sample package",
        exclude_source_outputs=False,
        outer_bundle_name=outer_bundle_name,
    )
    relative_data = {item.name.removeprefix(f"{expected_directory}/"): item.data for item in files}
    missing = REQUIRED_SAMPLE_PATHS - relative_data.keys()
    if missing:
        _fail(f"sample package is missing required files: {sorted(missing)!r}")

    evidence = _load_json_bytes(relative_data["evidence.json"], description="sample evidence.json")
    manifest = _load_json_bytes(relative_data["manifest.json"], description="sample manifest.json")
    if not isinstance(evidence, dict) or evidence.get("schema") != "pvs-evidence/3":
        _fail("sample evidence must use schema 'pvs-evidence/3'")
    if not isinstance(manifest, dict) or manifest.get("schema") != "pvs-manifest/3":
        _fail("sample manifest must use schema 'pvs-manifest/3'")
    record = evidence.get("record")
    integrity = evidence.get("integrity")
    if not isinstance(record, dict) or not isinstance(integrity, dict):
        _fail("sample evidence envelope is incomplete")
    pvs_record = record.get("pvs")
    if not isinstance(pvs_record, dict) or pvs_record.get("version") != version:
        _fail("sample evidence was not produced by the requested PVS version")
    expected_build = {
        "source_identity": {
            "profile": SOURCE_ARCHIVE_PROFILE,
            "algorithm": "sha256",
            "digest": expected_source_archive_digest,
        },
        "source_repository": None,
        "source_revision": None,
        "source_dirty": None,
        "source_date_epoch": source_date_epoch,
    }
    if pvs_record.get("content_identity") != expected_content_identity:
        _fail("sample PVS content identity does not match the released wheel")
    if pvs_record.get("build") != expected_build:
        _fail("sample PVS build identity does not match the released source archive")
    benchmark_case = _read_stable_file(
        source_tree / "benchmark-packs" / "plasma-physics-reference-v1" / "pvs.yaml",
        label="audited plasma benchmark case",
    )
    if relative_data["case/pvs.yaml"] != benchmark_case:
        _fail("sample case is not the exact audited plasma benchmark from released source")
    checks = record.get("checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(item, dict) or item.get("status") != "PASS" for item in checks
    ):
        _fail("release plasma sample must contain only passing checks")
    evidence_digest = _canonical_digest(record, description="sample evidence record")
    evidence_id = _validate_identity_field(
        integrity,
        kind="evidence",
        prefix="pvs:sha256:",
        digest=evidence_digest,
    )

    declared_finding = integrity.get("finding")
    if not isinstance(declared_finding, dict) or declared_finding.get("status") != "ISSUED":
        _fail("release sample must carry an issued Finding ID")
    finding_id = declared_finding.get("finding_id")
    finding_digest = declared_finding.get("digest")
    if (
        not isinstance(finding_id, str)
        or not isinstance(finding_digest, str)
        or not SHA256_RE.fullmatch(finding_digest)
        or finding_id != f"pvs-finding:v1:sha256:{finding_digest}"
    ):
        _fail("sample Finding ID has an invalid embedded form")

    package = manifest.get("package")
    manifest_integrity = manifest.get("integrity")
    if not isinstance(package, dict) or not isinstance(manifest_integrity, dict):
        _fail("sample manifest envelope is incomplete")
    if package.get("evidence_id") != evidence_id:
        _fail("sample manifest does not bind the sample Evidence ID")
    package_digest = _canonical_digest(package, description="sample manifest package")
    package_id = _validate_identity_field(
        manifest_integrity,
        kind="package",
        prefix="pvs-package:sha256:",
        digest=package_digest,
    )

    declared_files = package.get("files")
    if not isinstance(declared_files, list):
        _fail("sample manifest package.files must be an array")
    expected_paths = sorted(set(relative_data) - {"manifest.json"})
    declared_paths: list[str] = []
    for item in declared_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            _fail("sample manifest contains an invalid file entry")
        path = _portable_name(item["path"], label="sample manifest")
        declared_paths.append(path)
        data = relative_data.get(path)
        if data is None:
            _fail(f"sample manifest names a missing file: {path}")
        if item.get("sha256") != _sha256(data) or item.get("size_bytes") != len(data):
            _fail(f"sample manifest identity mismatch for {path}")
    _ensure_unique_names(declared_paths, label="sample manifest")
    if declared_paths != expected_paths:
        _fail(
            "sample manifest coverage/order mismatch: "
            f"expected {expected_paths!r}, got {declared_paths!r}"
        )
    identity = SampleIdentity(evidence_id, finding_id, package_id)
    expected_verification: dict[str, Any] | None = None
    if finding_metadata_factory is None:
        recomputed_finding, expected_verification = _validate_sample_using_source(
            source_tree,
            version,
            record,
            sample_package,
            identity,
        )
    else:
        try:
            recomputed_finding = finding_metadata_factory(record)
        except Exception as exc:
            raise ReleaseAssemblyError(f"could not recompute sample Finding ID: {exc}") from exc
    if declared_finding != recomputed_finding:
        _fail("sample Finding metadata does not match the recomputed scientific projection")
    return files, identity, expected_verification


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        _fail(
            f"{label} has unexpected members: expected {sorted(expected)!r}, got {sorted(value)!r}"
        )


def _release_timestamp(source_date_epoch: int) -> str:
    try:
        return (
            datetime.fromtimestamp(source_date_epoch, tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError) as exc:
        raise ReleaseAssemblyError(f"invalid release timestamp: {exc}") from exc


def _cyclonedx_properties(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, list):
        _fail(f"{label} properties must be an array")
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            _fail(f"{label} contains a malformed property")
        _require_exact_keys(item, {"name", "value"}, label=f"{label} property")
        name = item.get("name")
        property_value = item.get("value")
        if not isinstance(name, str) or not isinstance(property_value, str):
            _fail(f"{label} property names and values must be strings")
        if name in result:
            _fail(f"{label} contains duplicate property {name!r}")
        result[name] = property_value
    return result


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _locked_package_versions(lock_data: bytes) -> dict[str, set[str]]:
    """Read the name/version identities from uv's deterministic lock format."""

    try:
        text = lock_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseAssemblyError(f"dependency lock is not UTF-8: {exc}") from exc
    packages: dict[str, set[str]] = {}
    blocks = re.split(r"(?m)^\[\[package\]\][ \t]*\r?$", text)[1:]
    for block in blocks:
        name_match = re.search(r'(?m)^name = "([A-Za-z0-9._-]+)"[ \t]*\r?$', block)
        version_match = re.search(r'(?m)^version = "([^"\r\n]+)"[ \t]*\r?$', block)
        if name_match is None or version_match is None:
            continue
        name = _normalized_distribution_name(name_match.group(1))
        packages.setdefault(name, set()).add(version_match.group(1))
    if not packages:
        _fail("dependency lock contains no package name/version identities")
    return packages


def _validate_cyclonedx_components(
    value: Any,
    *,
    root_ref: str,
    expected_runtime: Mapping[str, Mapping[str, Any]],
    label: str,
) -> list[str]:
    if not isinstance(value, list):
        _fail(f"{label} components must be an array")
    component_refs: list[str] = []
    component_names: set[str] = set()
    for index, component in enumerate(value):
        if not isinstance(component, dict):
            _fail(f"{label} component {index} is malformed")
        required = {"type", "bom-ref", "name", "version", "purl"}
        if not required <= set(component) or set(component) - required - {"licenses"}:
            _fail(f"{label} component {index} has an unexpected shape")
        if component.get("type") != "library":
            _fail(f"{label} component {index} is not a library")
        reference = component.get("bom-ref")
        name = component.get("name")
        version = component.get("version")
        if (
            not isinstance(reference, str)
            or reference == root_ref
            or component.get("purl") != reference
            or not reference.startswith("pkg:pypi/")
            or not isinstance(name, str)
            or not isinstance(version, str)
        ):
            _fail(f"{label} component {index} has an invalid package identity")
        normalized_name = _normalized_distribution_name(name)
        expected_reference = (
            f"pkg:pypi/{quote(normalized_name, safe='')}@{quote(version, safe='.+-')}"
        )
        if reference != expected_reference:
            _fail(f"{label} component {index} name/version does not match its purl")
        if normalized_name in component_names:
            _fail(f"{label} contains duplicate normalized component {normalized_name!r}")
        component_names.add(normalized_name)
        expected_package = expected_runtime.get(normalized_name)
        if expected_package is None or version != expected_package.get("version"):
            _fail(
                f"{label} component {name!r} version {version!r} is absent from the "
                "canonical runtime lock closure"
            )
        licenses = component.get("licenses")
        if licenses is not None and (
            not isinstance(licenses, list)
            or not licenses
            or any(
                not isinstance(item, dict)
                or set(item) != {"expression"}
                or not isinstance(item.get("expression"), str)
                for item in licenses
            )
        ):
            _fail(f"{label} component {index} has invalid license metadata")
        component_refs.append(reference)
    if component_refs != sorted(component_refs) or len(component_refs) != len(set(component_refs)):
        _fail(f"{label} components must have unique, sorted package references")
    expected_names = set(expected_runtime)
    missing_components = expected_names - component_names
    unexpected_components = component_names - expected_names
    if missing_components or unexpected_components:
        _fail(
            f"{label} does not exactly cover the canonical runtime lock closure: "
            f"missing={sorted(missing_components)!r}, "
            f"unexpected={sorted(unexpected_components)!r}"
        )
    missing_roots = REQUIRED_SBOM_RUNTIME_ROOTS - expected_names
    if missing_roots:
        _fail(
            "dependency lock is missing required runtime components: "
            f"{sorted(missing_roots)!r}"
        )
    return component_refs


def _validate_cyclonedx_dependencies(
    value: Any,
    *,
    root_ref: str,
    component_refs: list[str],
    expected_runtime: Mapping[str, Mapping[str, Any]],
    label: str,
) -> None:
    if not isinstance(value, list):
        _fail(f"{label} dependencies must be an array")
    expected_refs = [root_ref, *component_refs]
    actual_refs: list[str] = []
    by_ref: dict[str, list[str]] = {}
    for item in value:
        if not isinstance(item, dict):
            _fail(f"{label} contains a malformed dependency entry")
        _require_exact_keys(item, {"ref", "dependsOn"}, label=f"{label} dependency")
        reference = item.get("ref")
        depends_on = item.get("dependsOn")
        if (
            not isinstance(reference, str)
            or not isinstance(depends_on, list)
            or any(not isinstance(dependency, str) for dependency in depends_on)
        ):
            _fail(f"{label} contains an invalid dependency entry")
        if depends_on != sorted(set(depends_on)):
            _fail(f"{label} dependency edges must be unique and sorted")
        if any(
            dependency not in component_refs or dependency == reference for dependency in depends_on
        ):
            _fail(f"{label} dependency edge names an unknown or self component")
        if reference in by_ref:
            _fail(f"{label} contains duplicate dependency reference {reference!r}")
        actual_refs.append(reference)
        by_ref[reference] = cast(list[str], depends_on)
    if actual_refs != expected_refs:
        _fail(
            f"{label} dependency coverage/order mismatch: expected {expected_refs!r}, "
            f"got {actual_refs!r}"
        )
    if by_ref.get(root_ref) != component_refs:
        _fail(f"{label} root dependency does not bind the installed component closure")
    expected_ref_by_name = {
        name: f"pkg:pypi/{quote(name, safe='')}@{quote(str(package['version']), safe='.+-')}"
        for name, package in expected_runtime.items()
    }
    for name, package in expected_runtime.items():
        reference = expected_ref_by_name[name]
        expected_edges = sorted(
            expected_ref_by_name[dependency]
            for dependency in cast(list[str], package["dependencies"])
        )
        if by_ref.get(reference) != expected_edges:
            _fail(
                f"{label} dependency edges for {name!r} do not exactly match "
                "the canonical runtime lock graph"
            )


def _validate_cyclonedx_sbom(
    path: Path,
    *,
    subject_name: str,
    subject_data: bytes,
    version: str,
    source_archive_name: str,
    source_archive_data: bytes,
    lock_name: str,
    lock_data: bytes,
    installed_content_identity: Mapping[str, Any],
    source_date_epoch: int,
) -> ArchiveFile:
    label = f"SBOM {path.name}"
    data = _read_stable_file(path, label=label)
    value = _load_json_bytes(data, description=label)
    if not isinstance(value, dict):
        _fail(f"{label} must contain a JSON object")
    _require_exact_keys(
        value,
        {"bomFormat", "specVersion", "version", "metadata", "components", "dependencies"},
        label=label,
    )
    if (
        value.get("bomFormat") != "CycloneDX"
        or value.get("specVersion") != CYCLONEDX_SPEC_VERSION
        or value.get("version") != 1
        or type(value.get("version")) is not int
    ):
        _fail(f"{label} must be a CycloneDX 1.6 document version 1")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict):
        _fail(f"{label} metadata must be an object")
    _require_exact_keys(metadata, {"timestamp", "component"}, label=f"{label} metadata")
    if metadata.get("timestamp") != _release_timestamp(source_date_epoch):
        _fail(f"{label} timestamp does not match SOURCE_DATE_EPOCH")
    component = metadata.get("component")
    if not isinstance(component, dict):
        _fail(f"{label} root component must be an object")
    _require_exact_keys(
        component,
        {"type", "bom-ref", "group", "name", "version", "purl", "hashes", "properties"},
        label=f"{label} root component",
    )
    root_ref = f"pkg:pypi/{PROJECT_NAME}@{quote(version, safe='.+-')}"
    expected_root = {
        "type": "application",
        "bom-ref": root_ref,
        "group": "Chatwood Labs Ltd",
        "name": PROJECT_NAME,
        "version": version,
        "purl": root_ref,
        "hashes": [{"alg": "SHA-256", "content": _sha256(subject_data)}],
    }
    for key, expected in expected_root.items():
        if component.get(key) != expected:
            _fail(f"{label} root component {key} does not match the release subject")
    expected_properties = {
        "pvs:release-subject": subject_name,
        "pvs:release-subject-size-bytes": str(len(subject_data)),
        "pvs:installed-content-profile": CONTENT_IDENTITY_PROFILE,
        "pvs:installed-content-sha256": str(installed_content_identity["digest"]),
        "pvs:dependency-lock": lock_name,
        "pvs:dependency-lock-sha256": _sha256(lock_data),
        "pvs:source-material": source_archive_name,
        "pvs:source-material-profile": SOURCE_ARCHIVE_PROFILE,
        "pvs:source-material-sha256": _sha256(source_archive_data),
    }
    properties = _cyclonedx_properties(component.get("properties"), label=label)
    if properties != expected_properties:
        _fail(f"{label} properties do not bind the exact subject, source, lock, and installation")

    try:
        from pvs.supply_chain import canonical_runtime_lock_graph

        expected_runtime = canonical_runtime_lock_graph(
            lock_data,
            project_version=version,
        )
    except ImportError as exc:
        raise ReleaseAssemblyError(
            "bundle assembly requires the installed PVS release dependencies"
        ) from exc
    except (RuntimeError, ValueError) as exc:
        _fail(f"dependency lock cannot define the canonical runtime closure: {exc}")
    components = value.get("components")
    component_refs = _validate_cyclonedx_components(
        components,
        root_ref=root_ref,
        expected_runtime=expected_runtime,
        label=label,
    )
    _validate_cyclonedx_dependencies(
        value.get("dependencies"),
        root_ref=root_ref,
        component_refs=component_refs,
        expected_runtime=expected_runtime,
        label=label,
    )
    return ArchiveFile(path.name, data)


def _validate_slsa_provenance(
    path: Path,
    *,
    subjects: Mapping[str, bytes],
    source_archive_name: str,
    source_archive_data: bytes,
    lock_name: str,
    lock_data: bytes,
    source_date_epoch: int,
    builder_id: str,
    build_command: str,
) -> ArchiveFile:
    label = "SLSA provenance"
    data = _read_stable_file(path, label=label)
    value = _load_json_bytes(data, description=label)
    if not isinstance(value, dict):
        _fail(f"{label} must contain a JSON object")
    _require_exact_keys(value, {"_type", "subject", "predicateType", "predicate"}, label=label)
    if value.get("_type") != "https://in-toto.io/Statement/v1":
        _fail(f"{label} is not an in-toto Statement v1")
    if value.get("predicateType") != "https://slsa.dev/provenance/v1":
        _fail(f"{label} is not SLSA Provenance v1")
    expected_subjects = [
        {"name": name, "digest": {"sha256": _sha256(subjects[name])}} for name in sorted(subjects)
    ]
    if value.get("subject") != expected_subjects:
        _fail(f"{label} subjects do not exactly bind the wheel and sdist")
    predicate = value.get("predicate")
    if not isinstance(predicate, dict):
        _fail(f"{label} predicate must be an object")
    _require_exact_keys(predicate, {"buildDefinition", "runDetails"}, label=f"{label} predicate")
    definition = predicate.get("buildDefinition")
    if not isinstance(definition, dict):
        _fail(f"{label} buildDefinition must be an object")
    _require_exact_keys(
        definition,
        {"buildType", "externalParameters", "internalParameters", "resolvedDependencies"},
        label=f"{label} buildDefinition",
    )
    if definition.get("buildType") != SLSA_BUILD_TYPE:
        _fail(f"{label} buildType is not the PVS Python distribution profile")
    expected_parameters = {
        "sourceDateEpoch": source_date_epoch,
        "buildCommand": build_command,
    }
    if definition.get("externalParameters") != expected_parameters:
        _fail(f"{label} external parameters do not bind SOURCE_DATE_EPOCH/build command")
    if definition.get("internalParameters") != {}:
        _fail(f"{label} must declare empty internal parameters")
    expected_materials = [
        {
            "uri": f"file:{quote(source_archive_name)}",
            "digest": {"sha256": _sha256(source_archive_data)},
            "annotations": {"pvs:identityProfile": SOURCE_ARCHIVE_PROFILE},
        },
        {
            "uri": f"file:{quote(lock_name)}",
            "digest": {"sha256": _sha256(lock_data)},
        },
    ]
    if definition.get("resolvedDependencies") != expected_materials:
        _fail(f"{label} materials do not exactly bind the source archive and dependency lock")
    if predicate.get("runDetails") != {"builder": {"id": builder_id}}:
        _fail(f"{label} builder identity does not match the declared release builder")
    return ArchiveFile(path.name, data)


def _validate_sample_verification(
    path: Path,
    *,
    expected_name: str,
    sample_directory_name: str,
    identity: SampleIdentity,
    expected_verification: dict[str, Any] | None = None,
) -> ArchiveFile:
    if path.name != expected_name:
        _fail(f"sample verification filename must be {expected_name!r}")
    value = _load_json_bytes(
        _read_stable_file(path, label="sample verification"),
        description="sample verification",
    )
    if not isinstance(value, dict):
        _fail("sample verification must contain a JSON object")
    expected_keys = {
        "target",
        "valid",
        "level",
        "evidence_id",
        "finding_id",
        "package_id",
        "evidence_status",
        "evidence_identity_pinned",
        "finding_identity_pinned",
        "package_identity_pinned",
        "trust",
        "checks",
    }
    if set(value) != expected_keys:
        _fail(
            "sample verification has unexpected shape: "
            f"expected {sorted(expected_keys)!r}, got {sorted(value)!r}"
        )
    expected_values: dict[str, Any] = {
        "valid": True,
        "level": "package",
        "evidence_id": identity.evidence_id,
        "finding_id": identity.finding_id,
        "package_id": identity.package_id,
        "evidence_status": "PASS",
        "evidence_identity_pinned": True,
        "finding_identity_pinned": True,
        "package_identity_pinned": True,
        "trust": "package-pinned",
    }
    for key, expected in expected_values.items():
        if value.get(key) != expected or type(value.get(key)) is not type(expected):
            _fail(f"sample verification {key} is {value.get(key)!r}; expected {expected!r}")
    checks = value.get("checks")
    if not isinstance(checks, list) or not checks:
        _fail("sample verification must contain at least one verification check")
    for check in checks:
        if not isinstance(check, dict) or check.get("status") != "PASS":
            _fail("sample verification contains a non-PASS or malformed check")
    # CLI output contains the absolute path supplied by its caller.  That path
    # is presentation metadata, not evidence, so normalize it for a portable,
    # byte-reproducible release artifact.
    normalized = dict(value)
    normalized["target"] = sample_directory_name
    if expected_verification is not None:
        expected_normalized = dict(expected_verification)
        expected_normalized["target"] = sample_directory_name
        if normalized != expected_normalized:
            _fail(
                "sample verification does not exactly match the supplied-source verifier output"
            )
    return ArchiveFile(expected_name, _pretty_json(normalized))


def _require_bytes_text_version(data: bytes, *, label: str, version: str) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseAssemblyError(f"{label} is not UTF-8: {exc}") from exc
    if version not in text:
        _fail(f"{label} does not mention release version {version!r}")


def _expected_release_names(version: str) -> dict[str, str]:
    return {
        "wheel": f"{DIST_NAME}-{version}-py3-none-any.whl",
        "sdist": f"{DIST_NAME}-{version}.tar.gz",
        "source_zip": f"physics-validation-suite-{version}-source.zip",
        "sample_evidence_zip": f"pvs-{version}-sample-plasma-evidence.zip",
        "sample_verification": "sample-plasma-verification.json",
        "readme": "README.md",
        "release_notes": f"RELEASE_NOTES_{version}.md",
        "powershell_test": "test-pvs.ps1",
        "shell_test": "test-pvs.sh",
        "provenance": f"pvs-{version}.provenance.intoto.json",
        "metadata": "RELEASE_METADATA.json",
        "checksums": "SHA256SUMS",
        "bundle": f"pvs-{version}-release-bundle.zip",
        "bundle_checksum": f"pvs-{version}-release-bundle.zip.sha256",
    }


def _copy_named_input(path: Path, *, expected_name: str, label: str) -> ArchiveFile:
    if path.name != expected_name:
        _fail(f"{label} filename must be {expected_name!r}, got {path.name!r}")
    mode = 0o755 if expected_name == "test-pvs.sh" else 0o644
    return ArchiveFile(expected_name, _read_stable_file(path, label=label), mode)


def _require_root_source_path(
    path: Path,
    source_tree: Path,
    expected_name: str,
    label: str,
) -> None:
    expected = source_tree / expected_name
    try:
        same = path.resolve(strict=True) == expected.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ReleaseAssemblyError(f"missing {label}: {path}") from exc
    if not same:
        _fail(f"{label} must be the pristine source-tree file {expected}")


def _checksum_document(files: Sequence[ArchiveFile]) -> bytes:
    return "".join(
        f"{_sha256(item.data)}  {item.name}\n" for item in sorted(files, key=lambda x: x.name)
    ).encode("ascii")


def _validate_checksums(data: bytes, files: Sequence[ArchiveFile]) -> None:
    expected = _checksum_document(files)
    if data != expected:
        _fail("SHA256SUMS does not exactly cover every other bundle member")


def create_source_archive(
    source_tree: Path,
    destination: Path,
    *,
    version: str,
    source_date_epoch: int,
) -> Path:
    """Create and audit the source ZIP used to bind subsequent package builds."""

    _validate_version(version)
    source_tree = _require_directory(source_tree, label="source tree")
    _validate_source_version(source_tree, version)
    expected_name = _expected_release_names(version)["source_zip"]
    if destination.name != expected_name:
        _fail(f"source archive filename must be {expected_name!r}")
    try:
        destination_metadata = destination.lstat()
    except FileNotFoundError:
        destination_metadata = None
    if destination_metadata is not None:
        _fail(f"source archive destination already exists: {destination}")
    destination = destination.resolve(strict=False)
    if destination == source_tree or _is_relative_to(destination, source_tree):
        _fail("source archive destination must be outside the pristine source tree")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _require_directory(destination.parent, label="source archive output directory")
    source_files = _collect_tree(
        source_tree,
        prefix=f"physics_validation_suite-{version}",
        label="source tree",
        exclude_source_outputs=True,
        outer_bundle_name=_expected_release_names(version)["bundle"],
    )
    with tempfile.TemporaryDirectory(
        prefix=".pvs-source-stage-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / expected_name
        _write_zip(staged, source_files, source_date_epoch=source_date_epoch)
        _audit_zip(staged, source_files, source_date_epoch=source_date_epoch)
        data = _read_stable_file(staged, label="staged source archive")
        publish = Path(temporary) / "publish-source-archive"
        publish.write_bytes(data)
        os.chmod(publish, 0o644)
        os.replace(publish, destination)
    _audit_zip(destination, source_files, source_date_epoch=source_date_epoch)
    return destination


def assemble_release(
    inputs: ReleaseInputs,
    *,
    source_date_epoch: int,
    finding_metadata_factory: FindingMetadataFactory | None = None,
    reproducibility_checker: ReproducibilityChecker = _run_reproducibility_gate,
    sbom_reproducer: SbomReproducer = _reproduce_sbom,
) -> dict[str, Path]:
    """Validate inputs, construct deterministic artifacts, and audit the result."""

    _validate_version(inputs.version)
    if (
        not isinstance(inputs.builder_id, str)
        or not inputs.builder_id
        or inputs.builder_id != inputs.builder_id.strip()
    ):
        _fail("release builder ID must be a non-empty, surrounding-whitespace-free string")
    if (
        not isinstance(inputs.build_command, str)
        or not inputs.build_command
        or inputs.build_command != inputs.build_command.strip()
    ):
        _fail("release build command must be a non-empty, surrounding-whitespace-free string")
    names = _expected_release_names(inputs.version)
    source_tree = _require_directory(inputs.source_tree, label="source tree")
    dist_dir = _require_directory(inputs.dist_dir, label="dist directory")
    sample_package = _require_directory(inputs.sample_package, label="sample package")
    unresolved_output_dir = inputs.output_dir
    output_dir = unresolved_output_dir.resolve(strict=False)
    if output_dir == source_tree or _is_relative_to(output_dir, source_tree):
        _fail("output directory must be outside the pristine source tree")
    if output_dir == sample_package or _is_relative_to(output_dir, sample_package):
        _fail("output directory must be outside the sample package")
    _validate_source_version(source_tree, inputs.version)
    lock_path = source_tree / "uv.lock"
    lock_data = _read_stable_file(lock_path, label="source dependency lock")

    wheel_data = _validate_wheel(inputs.wheel, version=inputs.version, dist_dir=dist_dir)
    sdist_data = _validate_sdist(inputs.sdist, version=inputs.version, dist_dir=dist_dir)
    installed_content_identity = _installed_content_identity_from_wheel(wheel_data)
    source_files = _collect_tree(
        source_tree,
        prefix=f"physics_validation_suite-{inputs.version}",
        label="source tree",
        exclude_source_outputs=True,
        outer_bundle_name=names["bundle"],
    )
    if inputs.source_archive.name != names["source_zip"]:
        _fail(f"source archive filename must be {names['source_zip']!r}")
    supplied_source_data = _read_stable_file(
        inputs.source_archive,
        label="supplied source archive",
    )
    _audit_zip(
        inputs.source_archive,
        source_files,
        source_date_epoch=source_date_epoch,
    )
    _validate_wheel_source_binding(
        wheel_data,
        source_files,
        version=inputs.version,
        source_archive_data=supplied_source_data,
        source_date_epoch=source_date_epoch,
    )
    reproducibility_checker(
        source_tree,
        inputs.source_archive.resolve(strict=True),
        dist_dir,
        source_date_epoch,
    )
    sample_files, sample_identity, expected_sample_verification = _validate_sample(
        sample_package,
        version=inputs.version,
        source_tree=source_tree,
        finding_metadata_factory=finding_metadata_factory,
        outer_bundle_name=names["bundle"],
        expected_content_identity=installed_content_identity,
        expected_source_archive_digest=_sha256(supplied_source_data),
        source_date_epoch=source_date_epoch,
    )
    sample_verification = _validate_sample_verification(
        inputs.sample_verification,
        expected_name=names["sample_verification"],
        sample_directory_name=f"sample-plasma-evidence-v{inputs.version}",
        identity=sample_identity,
        expected_verification=expected_sample_verification,
    )

    _require_root_source_path(inputs.readme, source_tree, "BUNDLE_README.md", "bundle README")
    _require_root_source_path(
        inputs.release_notes,
        source_tree,
        names["release_notes"],
        "release notes",
    )
    _require_root_source_path(
        inputs.powershell_script,
        source_tree,
        names["powershell_test"],
        "PowerShell acceptance script",
    )
    _require_root_source_path(
        inputs.shell_script,
        source_tree,
        names["shell_test"],
        "shell acceptance script",
    )
    bundle_readme = _copy_named_input(
        inputs.readme, expected_name="BUNDLE_README.md", label="bundle README"
    )
    readme = ArchiveFile(names["readme"], bundle_readme.data, bundle_readme.mode)
    _require_bytes_text_version(readme.data, label="README", version=inputs.version)
    notes = _copy_named_input(
        inputs.release_notes,
        expected_name=names["release_notes"],
        label="release notes",
    )
    _require_bytes_text_version(notes.data, label="release notes", version=inputs.version)
    powershell = _copy_named_input(
        inputs.powershell_script,
        expected_name=names["powershell_test"],
        label="PowerShell acceptance script",
    )
    _require_bytes_text_version(
        powershell.data,
        label="PowerShell acceptance script",
        version=inputs.version,
    )
    shell = _copy_named_input(
        inputs.shell_script,
        expected_name=names["shell_test"],
        label="shell acceptance script",
    )
    _require_bytes_text_version(
        shell.data,
        label="shell acceptance script",
        version=inputs.version,
    )

    expected_sbom_names = {
        f"{names['wheel']}.cdx.json",
        f"{names['sdist']}.cdx.json",
    }
    if (
        len(inputs.sboms) != len(expected_sbom_names)
        or {path.name for path in inputs.sboms} != expected_sbom_names
    ):
        _fail(
            "SBOM inputs must cover the wheel and sdist exactly: "
            f"expected {sorted(expected_sbom_names)!r}"
        )
    sbom_inputs = {path.name: path for path in inputs.sboms}
    subjects = {
        names["wheel"]: wheel_data,
        names["sdist"]: sdist_data,
    }
    sbom_files = [
        _validate_cyclonedx_sbom(
            sbom_inputs[f"{subject_name}.cdx.json"],
            subject_name=subject_name,
            subject_data=subject_data,
            version=inputs.version,
            source_archive_name=names["source_zip"],
            source_archive_data=supplied_source_data,
            lock_name=lock_path.name,
            lock_data=lock_data,
            installed_content_identity=installed_content_identity,
            source_date_epoch=source_date_epoch,
        )
        for subject_name, subject_data in sorted(subjects.items())
    ]
    for sbom in sbom_files:
        subject_name = sbom.name.removesuffix(".cdx.json")
        subject_path = inputs.wheel if subject_name == names["wheel"] else inputs.sdist
        reproduced = sbom_reproducer(
            subject_path.resolve(strict=True),
            lock_path,
            inputs.source_archive.resolve(strict=True),
            source_date_epoch,
        )
        if sbom.data != _pretty_json(reproduced):
            _fail(
                f"SBOM {sbom.name} does not exactly match independent generation from "
                "the clean installed dependency closure"
            )
    if inputs.provenance.name != names["provenance"]:
        _fail(
            f"provenance filename must be {names['provenance']!r}, got {inputs.provenance.name!r}"
        )
    provenance = _validate_slsa_provenance(
        inputs.provenance,
        subjects=subjects,
        source_archive_name=names["source_zip"],
        source_archive_data=supplied_source_data,
        lock_name=lock_path.name,
        lock_data=lock_data,
        source_date_epoch=source_date_epoch,
        builder_id=inputs.builder_id,
        build_command=inputs.build_command,
    )

    _ensure_unique_names(
        [
            names["wheel"],
            names["sdist"],
            names["source_zip"],
            names["sample_evidence_zip"],
            names["sample_verification"],
            readme.name,
            notes.name,
            powershell.name,
            shell.name,
            provenance.name,
            *(item.name for item in sbom_files),
            names["metadata"],
            names["checksums"],
        ],
        label="release member",
    )

    output_dir = _prepare_empty_directory(unresolved_output_dir, label="release output directory")
    with tempfile.TemporaryDirectory(prefix=".pvs-release-stage-", dir=output_dir) as temporary:
        stage = Path(temporary)
        source_zip_path = stage / names["source_zip"]
        sample_zip_path = stage / names["sample_evidence_zip"]
        _write_zip(source_zip_path, source_files, source_date_epoch=source_date_epoch)
        _write_zip(sample_zip_path, sample_files, source_date_epoch=source_date_epoch)
        _audit_zip(source_zip_path, source_files, source_date_epoch=source_date_epoch)
        _audit_zip(sample_zip_path, sample_files, source_date_epoch=source_date_epoch)

        source_zip_data = _read_stable_file(source_zip_path, label="assembled source ZIP")
        if supplied_source_data != source_zip_data:
            _fail(
                "supplied source archive is not byte-identical to the independently "
                "regenerated deterministic source archive"
            )
        sample_zip_data = _read_stable_file(sample_zip_path, label="assembled sample ZIP")
        metadata_value = {
            "schema": METADATA_SCHEMA,
            "project": PROJECT_NAME,
            "version": inputs.version,
            "source_date_epoch": source_date_epoch,
            "release_bundle": names["bundle"],
            "sample": {
                "evidence_id": sample_identity.evidence_id,
                "finding_id": sample_identity.finding_id,
                "package_id": sample_identity.package_id,
            },
            "artifacts": {
                "wheel": names["wheel"],
                "sdist": names["sdist"],
                "source_zip": names["source_zip"],
                "sample_evidence_zip": names["sample_evidence_zip"],
                "sample_verification": names["sample_verification"],
                "readme": names["readme"],
                "release_notes": names["release_notes"],
                "test_scripts": {
                    "powershell": names["powershell_test"],
                    "shell": names["shell_test"],
                },
                "sboms": sorted(item.name for item in sbom_files),
                "provenance": names["provenance"],
            },
        }
        metadata = ArchiveFile(names["metadata"], _pretty_json(metadata_value))
        members_without_checksums = [
            ArchiveFile(names["wheel"], wheel_data),
            ArchiveFile(names["sdist"], sdist_data),
            ArchiveFile(names["source_zip"], source_zip_data),
            ArchiveFile(names["sample_evidence_zip"], sample_zip_data),
            sample_verification,
            readme,
            notes,
            powershell,
            shell,
            *sbom_files,
            provenance,
            metadata,
        ]
        members_without_checksums.sort(key=lambda item: item.name)
        checksums = ArchiveFile(
            names["checksums"],
            _checksum_document(members_without_checksums),
        )
        _validate_checksums(checksums.data, members_without_checksums)
        bundle_members = sorted([*members_without_checksums, checksums], key=lambda item: item.name)
        bundle_path = stage / names["bundle"]
        _write_zip(bundle_path, bundle_members, source_date_epoch=source_date_epoch)
        _audit_zip(bundle_path, bundle_members, source_date_epoch=source_date_epoch)

        # Validate the metadata from the bytes that will be published.
        reparsed_metadata = _load_json_bytes(metadata.data, description="RELEASE_METADATA.json")
        if reparsed_metadata != metadata_value:
            _fail("RELEASE_METADATA.json round-trip mismatch")

        bundle_data = _read_stable_file(bundle_path, label="release bundle")
        bundle_checksum = ArchiveFile(
            names["bundle_checksum"],
            f"{_sha256(bundle_data)}  {names['bundle']}\n".encode("ascii"),
        )
        publish_files = [
            *bundle_members,
            ArchiveFile(names["bundle"], bundle_data),
            bundle_checksum,
        ]
        published: dict[str, Path] = {}
        for item in publish_files:
            destination = output_dir / item.name
            temporary_path = stage / f"publish-{hashlib.sha256(item.name.encode()).hexdigest()}"
            temporary_path.write_bytes(item.data)
            os.chmod(temporary_path, item.mode)
            os.replace(temporary_path, destination)
            published[item.name] = destination

    # Reopen the published archives, not only their staging counterparts.
    _audit_zip(published[names["source_zip"]], source_files, source_date_epoch=source_date_epoch)
    _audit_zip(
        published[names["sample_evidence_zip"]],
        sample_files,
        source_date_epoch=source_date_epoch,
    )
    _audit_zip(published[names["bundle"]], bundle_members, source_date_epoch=source_date_epoch)
    expected_bundle_checksum = (
        f"{_sha256(_read_stable_file(published[names['bundle']], label='published bundle'))}  "
        f"{names['bundle']}\n"
    ).encode("ascii")
    actual_bundle_checksum = _read_stable_file(
        published[names["bundle_checksum"]], label="published bundle checksum"
    )
    if actual_bundle_checksum != expected_bundle_checksum:
        _fail("published bundle checksum sidecar does not identify the release bundle")
    published_checksum_data = _read_stable_file(
        published[names["checksums"]], label="published SHA256SUMS"
    )
    _validate_checksums(published_checksum_data, members_without_checksums)
    for item in bundle_members:
        actual = _read_stable_file(published[item.name], label=f"published {item.name}")
        if _sha256(actual) != _sha256(item.data):
            _fail(f"published artifact hash mismatch: {item.name}")
    return published


def _source_date_epoch_from_environment() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        _fail("SOURCE_DATE_EPOCH must be set for deterministic release assembly")
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise ReleaseAssemblyError(f"invalid SOURCE_DATE_EPOCH: {raw!r}") from exc
    _zip_datetime(value)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    source = commands.add_parser(
        "source",
        help="create the deterministic source ZIP before building wheel/sdist",
    )
    source.add_argument("--version", required=True)
    source.add_argument("--source-tree", required=True, type=Path)
    source.add_argument("--output", required=True, type=Path)

    bundle = commands.add_parser(
        "bundle",
        help="validate built inputs and assemble the complete release bundle",
    )
    bundle.add_argument("--version", required=True)
    bundle.add_argument("--source-tree", required=True, type=Path)
    bundle.add_argument("--source-archive", required=True, type=Path)
    bundle.add_argument("--dist-dir", required=True, type=Path)
    bundle.add_argument("--wheel", required=True, type=Path)
    bundle.add_argument("--sdist", required=True, type=Path)
    bundle.add_argument("--sample-package", required=True, type=Path)
    bundle.add_argument("--sample-verification", required=True, type=Path)
    bundle.add_argument("--readme", required=True, type=Path,
                        help="pristine source BUNDLE_README.md, published as bundle README.md")
    bundle.add_argument("--release-notes", required=True, type=Path)
    bundle.add_argument("--powershell-script", required=True, type=Path)
    bundle.add_argument("--shell-script", required=True, type=Path)
    bundle.add_argument("--sbom", action="append", required=True, type=Path, dest="sboms")
    bundle.add_argument("--provenance", required=True, type=Path)
    bundle.add_argument("--builder-id", required=True)
    bundle.add_argument("--build-command", required=True)
    bundle.add_argument("--output-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        source_date_epoch = _source_date_epoch_from_environment()
        if arguments.command == "source":
            archive = create_source_archive(
                arguments.source_tree,
                arguments.output,
                version=arguments.version,
                source_date_epoch=source_date_epoch,
            )
            digest = _sha256(_read_stable_file(archive, label="published source archive"))
            print(f"Source archive: {archive}")
            print(f"SHA-256: {digest}")
            return 0
        published = assemble_release(
            ReleaseInputs(
                version=arguments.version,
                source_tree=arguments.source_tree,
                source_archive=arguments.source_archive,
                dist_dir=arguments.dist_dir,
                wheel=arguments.wheel,
                sdist=arguments.sdist,
                sample_package=arguments.sample_package,
                sample_verification=arguments.sample_verification,
                readme=arguments.readme,
                release_notes=arguments.release_notes,
                powershell_script=arguments.powershell_script,
                shell_script=arguments.shell_script,
                sboms=tuple(arguments.sboms),
                provenance=arguments.provenance,
                builder_id=arguments.builder_id,
                build_command=arguments.build_command,
                output_dir=arguments.output_dir,
            ),
            source_date_epoch=source_date_epoch,
        )
    except ReleaseAssemblyError as exc:
        print(f"release assembly failed: {exc}", file=sys.stderr)
        return 1
    bundle_name = _expected_release_names(arguments.version)["bundle"]
    bundle = published[bundle_name]
    digest = _sha256(_read_stable_file(bundle, label="published release bundle"))
    print(f"Release bundle: {bundle}")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
