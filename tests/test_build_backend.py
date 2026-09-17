from __future__ import annotations

import importlib.util
import io
import os
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest


def _load_backend() -> ModuleType:
    path = Path(__file__).parents[1] / "build_backend.py"
    spec = importlib.util.spec_from_file_location("pvs_build_backend", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backend = _load_backend()


def _write_input(path: Path, input_mode: int) -> None:
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo("physics_validation_suite-1.0.0a1")
        directory.type = tarfile.DIRTYPE
        directory.mode = input_mode
        archive.addfile(directory)
        for name in ("test-pvs.sh", "module.py"):
            data = name.encode()
            member = tarfile.TarInfo(f"physics_validation_suite-1.0.0a1/{name}")
            member.mode = input_mode
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def test_sdist_normalization_ignores_host_input_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1787940000")
    path = tmp_path / "package.tar.gz"
    _write_input(path, 0o755)
    backend._normalize_sdist(path)
    first = path.read_bytes()

    _write_input(path, 0o666)
    backend._normalize_sdist(path)
    second = path.read_bytes()

    assert first == second
    # Windows maps chmod(0o644) back to its synthetic 0o666 mode. The archive
    # bytes and member modes below are the portable contract; the containing
    # file mode is an additional POSIX assertion only.
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o644
    with tarfile.open(path, "r:gz") as archive:
        modes = {member.name: member.mode for member in archive.getmembers()}
    assert modes == {
        "physics_validation_suite-1.0.0a1": 0o755,
        "physics_validation_suite-1.0.0a1/module.py": 0o644,
        "physics_validation_suite-1.0.0a1/test-pvs.sh": 0o755,
    }
