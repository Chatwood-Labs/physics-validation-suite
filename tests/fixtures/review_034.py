"""Regression gates from review of the PVS 0.3.4 release bundle.

Run from the extracted source root in a normal PVS development environment:
    python -m pytest -q /path/to/test_pvs_release_review.py

Optionally set PVS_REVIEW_SOURCE_ROOT to the extracted source root.
These tests express the desired fixed behavior, so the affected tests FAIL
on the reviewed 0.3.4 source. No production files are changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from pvs.case import load_case
from pvs.cli import main
from pvs.errors import CaseError


def _case_text(subject_name: str) -> str:
    case = {
        "schema": "pvs-case/2",
        "id": "review-case-read-binding",
        "version": "1.0.0",
        "title": "Case-byte binding regression",
        "classifications": ["verification"],
        "subject": {"name": subject_name, "version": "1.0.0"},
        "artifacts": {
            "result": {"path": "result.json", "role": "output", "format": "json"}
        },
        "checks": [{"id": "present", "type": "exists", "artifact": "result"}],
        "package": {"embed_artifacts": False, "html": False, "pdf": False},
    }
    return yaml.safe_dump(case, sort_keys=False)


def _install_read_race(
    path: Path, replacement: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replace the live file just after its original text has been acquired."""
    original_read = Path.read_text
    swapped = False

    def read_then_replace(self: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal swapped
        text = original_read(self, *args, **kwargs)
        if self == path and not swapped:
            swapped = True
            path.write_text(replacement, encoding="utf-8")
        return text

    monkeypatch.setattr(Path, "read_text", read_then_replace)


def test_active_ci_release_versions_match_pyproject() -> None:
    root = Path(os.environ.get("PVS_REVIEW_SOURCE_ROOT", str(Path.cwd())))
    project_file = root / "pyproject.toml"
    workflow_file = root / ".github" / "workflows" / "ci.yml"
    assert project_file.is_file(), "Run from the extracted PVS source root."
    assert workflow_file.is_file(), "The reviewed source ZIP includes this workflow."
    version = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]["version"]
    workflow = workflow_file.read_text(encoding="utf-8")
    patterns = (
        r"(?:physics[_-]validation[_-]suite|pvs)[-_]v?(\d+\.\d+\.\d+)",
        r"RELEASE_NOTES_(\d+\.\d+\.\d+)",
        r"--version\s+(\d+\.\d+\.\d+)",
    )
    mismatches = []
    for number, line in enumerate(workflow.splitlines(), start=1):
        for pattern in patterns:
            for found in re.findall(pattern, line):
                if found != version:
                    mismatches.append(f"ci.yml:{number}: {found} != {version}: {line.strip()}")
    assert not mismatches, "Stale active release references:\n" + "\n".join(mismatches)


def test_unchanged_case_binds_raw_hash(tmp_path: Path) -> None:
    text = _case_text("subject-A")
    path = tmp_path / "pvs.yaml"
    path.write_text(text, encoding="utf-8")
    loaded = load_case(path)
    assert loaded.data["subject"]["name"] == "subject-A"
    assert loaded.raw_identity["sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_load_case_binds_raw_hash_to_the_parsed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    texts = {name: _case_text(name) for name in ("subject-A", "subject-B")}
    path = tmp_path / "pvs.yaml"
    path.write_text(texts["subject-A"], encoding="utf-8")
    _install_read_race(path, texts["subject-B"], monkeypatch)
    try:
        loaded = load_case(path)
    except CaseError:
        return  # Explicitly rejecting a detected concurrent edit is acceptable.
    subject = loaded.data["subject"]["name"]
    expected = hashlib.sha256(texts[subject].encode()).hexdigest()
    assert loaded.raw_identity["sha256"] == expected, (
        f"Parsed {subject}, but the raw identity describes different bytes."
    )


def test_case_inspection_never_mixes_subject_and_raw_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    texts = {name: _case_text(name) for name in ("subject-A", "subject-B")}
    path = tmp_path / "pvs.yaml"
    path.write_text(texts["subject-A"], encoding="utf-8")
    _install_read_race(path, texts["subject-B"], monkeypatch)
    exit_code = main(["inspect", str(path), "--json"])
    output = json.loads(capsys.readouterr().out)
    if exit_code != 0:
        assert output.get("status") == "ERROR"
        assert output.get("kind") in {"case", "integrity"}
        return
    subject = output["subject"]["name"]
    assert output["raw_sha256"] == hashlib.sha256(texts[subject].encode()).hexdigest(), (
        "Successful inspection paired one case's subject with another case's raw hash."
    )
