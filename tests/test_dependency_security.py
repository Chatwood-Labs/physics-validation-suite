"""The runtime and frozen PDF parser must exclude the published affected range."""

from importlib.metadata import version as installed_version
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
# GHSA-jw7q-gvrg-4vj3, GHSA-g9cg-prrw-2r8q and GHSA-fp3h-c4fm-7vvf
# (2026-09-11) include 6.18.0 in the affected range. Its earlier patch only
# addressed GHSA-5jq2-8x83-x246 (2026-09-07).
PATCHED = Version("6.18.1")


def pdf_requirement() -> Requirement:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return next(
        requirement for line in project["project"]["dependencies"]
        if (requirement := Requirement(line)).name == "pypdf"
    )


@pytest.mark.parametrize(
    "affected", ["5.0.0", "5.9.0", "6.16.2", "6.17.0", "6.17.99", "6.18.0"]
)
def test_runtime_requirement_rejects_advisory_affected_versions(affected: str) -> None:
    assert Version(affected) not in pdf_requirement().specifier


def test_runtime_requirement_accepts_the_patched_parser() -> None:
    assert PATCHED in pdf_requirement().specifier


def test_runtime_requirement_retains_the_reviewed_major_version_boundary() -> None:
    assert Version("7.0.0") not in pdf_requirement().specifier


def test_frozen_pdf_parser_is_patched_and_satisfies_runtime_requirement() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    parsers = [p for p in lock["package"] if p["name"] == "pypdf"]
    assert len(parsers) == 1
    version = Version(parsers[0]["version"])
    assert version >= PATCHED
    assert version in pdf_requirement().specifier


def test_installed_pdf_parser_is_patched_and_satisfies_runtime_requirement() -> None:
    version = Version(installed_version("pypdf"))
    assert version >= PATCHED
    assert version in pdf_requirement().specifier
