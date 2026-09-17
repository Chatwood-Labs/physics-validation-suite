from __future__ import annotations

import copy
import errno
import itertools
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from pvs.api import RunOutcome, validate_case

pytest_plugins = ["qualification_plugin"]


def base_case() -> dict[str, Any]:
    """Return a new, schema-valid, domain-neutral PVS case."""

    return {
        "schema": "pvs-case/2",
        "id": "test-basic-case",
        "version": "1.0.0",
        "title": "Internal test case",
        "classifications": ["verification"],
        "subject": {"name": "synthetic-subject", "version": "1.0.0"},
        "artifacts": {
            "result": {
                "path": "result.json",
                "role": "output",
                "format": "json",
            }
        },
        "checks": [{"id": "result-exists", "type": "exists", "artifact": "result"}],
        "package": {"embed_artifacts": False, "html": False, "pdf": False},
    }


def dump_case(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def minimal_case() -> dict[str, Any]:
    return base_case()


@pytest.fixture
def symlink_factory() -> Callable[..., Path]:
    """Create a link or skip when the host exposes no usable link capability."""

    def create(
        link: Path,
        target: str | os.PathLike[str],
        *,
        target_is_directory: bool = False,
    ) -> Path:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except NotImplementedError as exc:
            pytest.skip(f"host cannot create symbolic links for this test: {exc}")
        except OSError as exc:
            unavailable = {
                errno.EACCES,
                errno.ENOSYS,
                errno.ENOTSUP,
                errno.EOPNOTSUPP,
                errno.EPERM,
            }
            if getattr(exc, "winerror", None) == 1314 or exc.errno in unavailable:
                pytest.skip(f"host cannot create symbolic links for this test: {exc}")
            raise
        return link

    return create


@pytest.fixture
def case_sensitive_filesystem(tmp_path: Path) -> None:
    """Require a directory that can represent two names differing only by case."""

    lower = tmp_path / "pvs-case-sensitivity-probe"
    upper = tmp_path / "PVS-CASE-SENSITIVITY-PROBE"
    lower.write_bytes(b"probe")
    if upper.exists():
        pytest.skip("filesystem is case-insensitive and cannot represent the collision fixture")


@pytest.fixture
def case_factory(
    tmp_path: Path,
) -> Callable[..., tuple[Path, dict[str, Any]]]:
    counter = itertools.count()

    def create(
        data: dict[str, Any] | None = None,
        *,
        result_text: str = '{"value": 1.0, "values": [0.0, 0.5, 1.0]}\n',
        name: str | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        value = copy.deepcopy(data if data is not None else base_case())
        root = tmp_path / (name or f"case-{next(counter)}")
        root.mkdir(parents=True, exist_ok=False)
        (root / "result.json").write_text(result_text, encoding="utf-8")
        case_path = dump_case(root / "pvs.yaml", value)
        return case_path, value

    return create


@pytest.fixture
def package_factory(
    tmp_path: Path,
) -> Callable[..., tuple[RunOutcome, Path]]:
    counter = itertools.count()

    def create(
        *, html: bool = True, pdf: bool = True, embed: bool = True
    ) -> tuple[RunOutcome, Path]:
        number = next(counter)
        case_root = tmp_path / f"package-case-{number}"
        case_root.mkdir()
        (case_root / "result.json").write_text(
            '{"value": 1.0, "values": [0.0, 0.5, 1.0]}\n',
            encoding="utf-8",
        )
        data = base_case()
        data["package"] = {
            "embed_artifacts": embed,
            "html": html,
            "pdf": pdf,
        }
        dump_case(case_root / "pvs.yaml", data)
        output = tmp_path / f"package-{number}"
        outcome = validate_case(case_root, output_dir=output)
        return outcome, output

    return create
