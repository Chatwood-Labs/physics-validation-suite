"""Build pristine source material twice and require byte-for-byte distributions."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

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


class ReproducibleBuildError(RuntimeError):
    """The reproducible-build contract could not be established."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        default=int(os.environ["SOURCE_DATE_EPOCH"])
        if "SOURCE_DATE_EPOCH" in os.environ
        else None,
    )
    parser.add_argument(
        "--expect-directory",
        type=Path,
        help="also require both pristine builds to equal the released wheel and sdist here",
    )
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_path(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise ReproducibleBuildError(f"unsafe source archive member: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or str(path) != name
        or unicodedata.normalize("NFC", name) != name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReproducibleBuildError(f"unsafe source archive member: {name!r}")
    for part in path.parts:
        stem = part.split(".", 1)[0].upper()
        if (
            part != part.strip()
            or part.endswith(".")
            or any(character in WINDOWS_INVALID_CHARACTERS for character in part)
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or stem in WINDOWS_RESERVED
        ):
            raise ReproducibleBuildError(
                f"non-portable source archive member: {name!r}"
            )
    return path


def _validate_members(members: list[tuple[PurePosixPath, bool]]) -> str:
    if not members:
        raise ReproducibleBuildError("source archive is empty")
    roots = {path.parts[0] for path, _ in members}
    if len(roots) != 1:
        raise ReproducibleBuildError(
            f"source archive must contain exactly one top-level directory; found {sorted(roots)}"
        )
    root = next(iter(roots))
    if any(len(path.parts) == 1 and not is_directory for path, is_directory in members):
        raise ReproducibleBuildError("source archive top-level entry must be a directory")

    exact: set[str] = set()
    folded: set[str] = set()
    files: set[str] = set()
    for path, is_directory in members:
        text = path.as_posix().rstrip("/")
        folded_text = text.casefold()
        if text in exact or folded_text in folded:
            raise ReproducibleBuildError(f"duplicate source archive member: {text!r}")
        exact.add(text)
        folded.add(folded_text)
        if not is_directory:
            files.add(text)
    for text in exact:
        parents = PurePosixPath(text).parents
        if any(parent.as_posix() in files for parent in parents if parent.as_posix() != "."):
            raise ReproducibleBuildError(
                f"source archive file/directory collision involving {text!r}"
            )
    return root


def _write_member(destination: Path, relative: PurePosixPath, data: bytes, mode: int) -> None:
    target = destination.joinpath(*relative.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ReproducibleBuildError(f"duplicate extracted member: {relative.as_posix()!r}")
    target.write_bytes(data)
    permissions = mode & 0o777
    if permissions:
        target.chmod(permissions)


def _extract_zip(archive_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        normalized: list[tuple[zipfile.ZipInfo, PurePosixPath, bool, int]] = []
        descriptions: list[tuple[PurePosixPath, bool]] = []
        for info in infos:
            # ZipInfo.filename normalises os.sep on Windows. Validate the raw
            # archive spelling so canonical-member policy is host-independent.
            raw_name = info.orig_filename
            path = _safe_member_path(raw_name.rstrip("/") or raw_name)
            mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            is_directory = info.is_dir()
            if file_type == stat.S_IFLNK:
                raise ReproducibleBuildError(
                    f"source ZIP contains a symbolic link: {path.as_posix()!r}"
                )
            if not is_directory and file_type not in {0, stat.S_IFREG}:
                raise ReproducibleBuildError(
                    f"source ZIP contains a special file: {path.as_posix()!r}"
                )
            normalized.append((info, path, is_directory, mode))
            descriptions.append((path, is_directory))
        root_name = _validate_members(descriptions)
        for info, path, is_directory, mode in normalized:
            target = destination.joinpath(*path.parts)
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
            else:
                _write_member(destination, path, archive.read(info), mode)
    root = destination / root_name
    if not root.is_dir():
        raise ReproducibleBuildError("source ZIP did not create its top-level directory")
    return root


def _extract_tar(archive_path: Path, destination: Path) -> Path:
    with tarfile.open(archive_path, mode="r:*") as archive:
        members = archive.getmembers()
        normalized: list[tuple[tarfile.TarInfo, PurePosixPath, bool]] = []
        descriptions: list[tuple[PurePosixPath, bool]] = []
        for member in members:
            path = _safe_member_path(member.name.rstrip("/") or member.name)
            if member.issym() or member.islnk():
                raise ReproducibleBuildError(
                    f"source TAR contains a link: {path.as_posix()!r}"
                )
            if not (member.isdir() or member.isfile()):
                raise ReproducibleBuildError(
                    f"source TAR contains a special file: {path.as_posix()!r}"
                )
            normalized.append((member, path, member.isdir()))
            descriptions.append((path, member.isdir()))
        root_name = _validate_members(descriptions)
        for member, path, is_directory in normalized:
            target = destination.joinpath(*path.parts)
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
                permissions = member.mode & 0o777
                if permissions:
                    target.chmod(permissions)
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ReproducibleBuildError(
                    f"could not read source TAR member: {path.as_posix()!r}"
                )
            _write_member(destination, path, stream.read(), member.mode)
    root = destination / root_name
    if not root.is_dir():
        raise ReproducibleBuildError("source TAR did not create its top-level directory")
    return root


def _extract_source_archive(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    if zipfile.is_zipfile(archive_path):
        return _extract_zip(archive_path, destination)
    if tarfile.is_tarfile(archive_path):
        return _extract_tar(archive_path, destination)
    raise ReproducibleBuildError(f"unsupported source archive: {archive_path}")


def _snapshot_source_archive(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ReproducibleBuildError(f"PVS_BUILD_SOURCE_ARCHIVE is not a regular file: {source}")
    with source.open("rb") as stream:
        before = os.fstat(stream.fileno())
        data = stream.read()
        after = os.fstat(stream.fileno())
    identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
        raise ReproducibleBuildError("PVS_BUILD_SOURCE_ARCHIVE changed while being snapshotted")
    destination.write_bytes(data)


def _distribution_files(directory: Path) -> dict[str, Path]:
    result = {
        path.name: path
        for path in directory.iterdir()
        if path.is_file() and (path.name.endswith(".whl") or path.name.endswith(".tar.gz"))
    }
    if len(result) != 2 or not any(name.endswith(".whl") for name in result) or not any(
        name.endswith(".tar.gz") for name in result
    ):
        raise ReproducibleBuildError(
            f"expected exactly one wheel and one sdist in {directory}, found {sorted(result)}"
        )
    return result


def _build(
    source_root: Path,
    destination: Path,
    epoch: int,
    source_archive: Path,
) -> dict[str, Path]:
    destination.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)
    environment["PVS_BUILD_SOURCE_ARCHIVE"] = str(source_archive.resolve())
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--outdir",
            str(destination),
        ],
        cwd=source_root,
        check=False,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if process.returncode != 0:
        raise ReproducibleBuildError(f"distribution build failed:\n{process.stdout}")
    return _distribution_files(destination)


def _compare(
    first: dict[str, Path],
    second: dict[str, Path],
    expected: dict[str, Path] | None,
) -> bool:
    if set(first) != set(second):
        raise ReproducibleBuildError(
            f"distribution names differ: {sorted(first)} != {sorted(second)}"
        )
    if expected is not None and set(first) != set(expected):
        raise ReproducibleBuildError(
            f"released distribution names differ: {sorted(first)} != {sorted(expected)}"
        )
    failed = False
    for name in sorted(first):
        left_digest = _sha256(first[name])
        right_digest = _sha256(second[name])
        if expected is None:
            if left_digest != right_digest:
                failed = True
                print(f"[FAIL] {name}: first={left_digest} second={right_digest}")
            else:
                print(
                    f"[PASS] {name}: sha256:{left_digest} "
                    "(same-host builds match)"
                )
            continue

        released_digest = _sha256(expected[name])
        if left_digest != right_digest or left_digest != released_digest:
            failed = True
            print(
                f"[FAIL] {name}: first={left_digest} second={right_digest} "
                f"released={released_digest}"
            )
        else:
            print(f"[PASS] {name}: sha256:{left_digest} (matches released artifact)")
    return not failed


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.source_date_epoch is None:
        raise SystemExit("SOURCE_DATE_EPOCH or --source-date-epoch is required")
    if args.source_date_epoch < 315532800:
        raise SystemExit("SOURCE_DATE_EPOCH must be at or after 1980-01-01 for wheel ZIPs")
    source_value = os.environ.get("PVS_BUILD_SOURCE_ARCHIVE")
    if not source_value:
        raise SystemExit("PVS_BUILD_SOURCE_ARCHIVE is required")
    source_archive = Path(source_value).resolve()
    expected_directory = args.expect_directory.resolve() if args.expect_directory else None

    try:
        with tempfile.TemporaryDirectory(prefix="pvs-repro-") as temporary_text:
            temporary = Path(temporary_text)
            snapshot = temporary / "declared-source-archive"
            _snapshot_source_archive(source_archive, snapshot)
            first_source = _extract_source_archive(snapshot, temporary / "source-a")
            second_source = _extract_source_archive(snapshot, temporary / "source-b")
            first = _build(
                first_source,
                temporary / "dist-a",
                args.source_date_epoch,
                snapshot,
            )
            second = _build(
                second_source,
                temporary / "dist-b",
                args.source_date_epoch,
                snapshot,
            )
            expected = (
                _distribution_files(expected_directory)
                if expected_directory is not None
                else None
            )
            return 0 if _compare(first, second, expected) else 1
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ReproducibleBuildError) as exc:
        print(f"[FAIL] reproducible build: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
