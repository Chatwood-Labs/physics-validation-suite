from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _load_module() -> ModuleType:
    path = Path(__file__).parents[1] / "tools" / "check-reproducible-build.py"
    spec = importlib.util.spec_from_file_location("pvs_reproducible_build", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


repro = _load_module()


def test_safe_zip_extraction_returns_single_pristine_source_root(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("physics_validation_suite-1.0.0a1/pyproject.toml", "[project]\n")
        output.writestr("physics_validation_suite-1.0.0a1/src/pvs/__init__.py", "")

    root = repro._extract_source_archive(archive, tmp_path / "extract")

    assert root.name == "physics_validation_suite-1.0.0a1"
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == "[project]\n"
    assert (root / "src" / "pvs" / "__init__.py").is_file()


@pytest.mark.parametrize(
    "member",
    [
        "../outside.txt",
        "/absolute.txt",
        "root/../../outside.txt",
        "root\\windows.txt",
    ],
)
def test_zip_extraction_rejects_unsafe_member_paths(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        # ZipInfo normalises the host separator in its constructor on Windows.
        # Assign the stored name afterwards so this exercises the same hostile
        # archive spelling on every platform.
        info = zipfile.ZipInfo("placeholder")
        info.filename = member
        info.orig_filename = member
        output.writestr(info, "unsafe")

    with pytest.raises(repro.ReproducibleBuildError, match="unsafe source archive member"):
        repro._extract_source_archive(archive, tmp_path / "extract")


@pytest.mark.parametrize(
    "member",
    [
        "root/C:/file",
        "root/trailing.",
        "root/space ",
        "root/CON.txt",
        "root/question?.txt",
        "root/cafe\u0301.txt",
        "root/bad\x00name",
    ],
)
def test_member_validation_rejects_nonportable_components(member: str) -> None:
    with pytest.raises(repro.ReproducibleBuildError):
        repro._safe_member_path(member)


def test_zip_extraction_rejects_symbolic_links(tmp_path: Path) -> None:
    archive = tmp_path / "linked.zip"
    link = zipfile.ZipInfo("root/link")
    link.create_system = 3
    link.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(link, "target")

    with pytest.raises(repro.ReproducibleBuildError, match="symbolic link"):
        repro._extract_source_archive(archive, tmp_path / "extract")


def test_zip_extraction_rejects_multiple_roots_and_casefold_duplicates(
    tmp_path: Path,
) -> None:
    multiple = tmp_path / "multiple.zip"
    with zipfile.ZipFile(multiple, "w") as output:
        output.writestr("first/file", "one")
        output.writestr("second/file", "two")
    with pytest.raises(repro.ReproducibleBuildError, match="one top-level directory"):
        repro._extract_source_archive(multiple, tmp_path / "multiple")

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as output:
        output.writestr("root/File", "one")
        output.writestr("root/file", "two")
    with pytest.raises(repro.ReproducibleBuildError, match="duplicate"):
        repro._extract_source_archive(duplicate, tmp_path / "duplicate")


def test_tar_extraction_rejects_links_and_special_files(tmp_path: Path) -> None:
    linked = tmp_path / "linked.tar"
    with tarfile.open(linked, "w") as output:
        link = tarfile.TarInfo("root/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "target"
        output.addfile(link)
    with pytest.raises(repro.ReproducibleBuildError, match="contains a link"):
        repro._extract_source_archive(linked, tmp_path / "linked")

    special = tmp_path / "special.tar"
    with tarfile.open(special, "w") as output:
        fifo = tarfile.TarInfo("root/fifo")
        fifo.type = tarfile.FIFOTYPE
        output.addfile(fifo)
    with pytest.raises(repro.ReproducibleBuildError, match="special file"):
        repro._extract_source_archive(special, tmp_path / "special")


def test_safe_tar_extraction_reads_regular_content(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    data = b"[project]\n"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("root/pyproject.toml")
        member.mode = 0o644
        member.size = len(data)
        output.addfile(member, io.BytesIO(data))

    root = repro._extract_source_archive(archive, tmp_path / "extract")

    assert (root / "pyproject.toml").read_bytes() == data


def test_distribution_comparison_binds_released_artifacts(tmp_path: Path) -> None:
    directories = [tmp_path / name for name in ("first", "second", "released")]
    for directory in directories:
        directory.mkdir()
        (directory / "package.whl").write_bytes(b"wheel")
        (directory / "package.tar.gz").write_bytes(b"sdist")
    first, second, released = (repro._distribution_files(path) for path in directories)

    assert repro._compare(first, second, released) is True

    (directories[2] / "package.whl").write_bytes(b"different")
    assert repro._compare(first, second, repro._distribution_files(directories[2])) is False


def test_distribution_comparison_proves_same_host_repeatability(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    directories = [tmp_path / name for name in ("first", "second")]
    for directory in directories:
        directory.mkdir()
        (directory / "package.whl").write_bytes(b"host-wheel")
        (directory / "package.tar.gz").write_bytes(b"host-sdist")
    first, second = (repro._distribution_files(path) for path in directories)

    assert repro._compare(first, second, None) is True
    success_output = capsys.readouterr().out
    assert success_output.count("(same-host builds match)") == 2
    assert "released=" not in success_output

    (directories[1] / "package.tar.gz").write_bytes(b"changed")
    assert repro._compare(first, repro._distribution_files(directories[1]), None) is False
    failure_output = capsys.readouterr().out
    assert "first=" in failure_output
    assert "second=" in failure_output
    assert "released=" not in failure_output


def test_main_requires_declared_source_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PVS_BUILD_SOURCE_ARCHIVE", raising=False)

    with pytest.raises(SystemExit, match="PVS_BUILD_SOURCE_ARCHIVE is required"):
        repro.main(["--source-date-epoch", "1700000000"])
