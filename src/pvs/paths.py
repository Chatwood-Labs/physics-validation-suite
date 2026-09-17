"""Path confinement used by cases and untrusted package manifests."""

from __future__ import annotations

import os
import stat as stat_module
import unicodedata
from collections.abc import Iterator
from pathlib import Path

from .errors import ArtifactError, IntegrityError

_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_STEMS = frozenset(
    {
        "AUX",
        "CON",
        "CONIN$",
        "CONOUT$",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
        # Windows also reserves the ISO-8859-1 superscript digits 1, 2 and 3.
        *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "\u00b9\u00b2\u00b3"),
    }
)


def _validate_portable_component(component: str, error_type: type[Exception]) -> None:
    if (
        component.endswith((" ", "."))
        or any(character in _WINDOWS_INVALID_CHARACTERS for character in component)
        or any(ord(character) < 32 for character in component)
    ):
        raise error_type(f"invalid portable relative path component: {component!r}")
    reserved_stem = component.split(".", 1)[0].upper()
    if reserved_stem in _WINDOWS_RESERVED_STEMS:
        raise error_type(f"reserved portable relative path component: {component!r}")


def stat_is_link_or_reparse(metadata: os.stat_result) -> bool:
    """Return whether metadata identifies a symlink or Windows reparse point."""

    reparse_flag = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat_module.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def path_is_link_or_reparse(path: Path) -> bool:
    """Inspect a path itself without following a symlink or reparse point."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat_is_link_or_reparse(metadata)


def walk_directory_entries(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Walk without following links/reparse points or suppressing scan failures."""

    pending = [root]
    while pending:
        directory = pending.pop()
        before = os.stat(directory, follow_symlinks=False)
        if stat_is_link_or_reparse(before) or not stat_module.S_ISDIR(before.st_mode):
            raise OSError(f"package directory changed or is unsafe: {directory}")
        with os.scandir(directory) as iterator:
            entries = [
                (Path(entry.path), entry.stat(follow_symlinks=False))
                for entry in sorted(iterator, key=lambda item: item.name)
            ]
        after = os.stat(directory, follow_symlinks=False)
        if (
            stat_is_link_or_reparse(after)
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
        ):
            raise OSError(f"package directory changed while it was scanned: {directory}")
        children: list[Path] = []
        for path, metadata in entries:
            yield path, metadata
            if stat_module.S_ISDIR(metadata.st_mode) and not stat_is_link_or_reparse(metadata):
                children.append(path)
        pending.extend(reversed(children))


def portable_relative_path(
    relative: str,
    *,
    allow_root: bool = False,
    integrity: bool = False,
) -> str:
    error_type = IntegrityError if integrity else ArtifactError
    if relative == "." and allow_root:
        return relative
    if "\\" in relative or "\x00" in relative:
        raise error_type(f"invalid portable relative path: {relative!r}")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise error_type(f"absolute path is not allowed: {relative}")
    if ".." in relative.split("/"):
        raise error_type(f"path escapes its declared root: {relative}")
    if relative.endswith("/") or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise error_type(f"unsafe relative path: {relative!r}")
    normalized = unicodedata.normalize("NFC", candidate.as_posix())
    if normalized != relative:
        raise error_type(f"relative path is not in canonical NFC form: {relative!r}")
    for component in normalized.split("/"):
        _validate_portable_component(component, error_type)
    return normalized


def confined_path(
    root: Path,
    relative: str,
    *,
    integrity: bool = False,
    reject_symlinks: bool = False,
) -> Path:
    error_type = IntegrityError if integrity else ArtifactError
    canonical = portable_relative_path(
        relative,
        allow_root=True,
        integrity=integrity,
    )
    candidate = Path(canonical)
    resolved_root = root.resolve()
    if reject_symlinks:
        current = resolved_root
        for part in candidate.parts:
            if part in {"", "."}:
                continue
            if part == "..":
                raise error_type(f"parent traversal is not allowed: {relative}")
            current = current / part
            try:
                link_or_reparse = path_is_link_or_reparse(current)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise error_type(f"could not safely inspect artifact path: {relative}") from exc
            if link_or_reparse:
                raise error_type(
                    "symbolic links and reparse points are not allowed in "
                    f"artifact paths: {relative}"
                )
    resolved = (resolved_root / candidate).resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise error_type(f"path escapes its declared root: {relative}")
    return resolved
