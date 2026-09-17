"""Active release consistency, prerelease metadata and future-version propagation."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_contract", ROOT / "tools/release_contract.py"
)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


@pytest.fixture
def release_tree(tmp_path):
    names = [*contract.ACTIVE_FILES, "pyproject.toml", "uv.lock", "CITATION.cff",
             "src/pvs/version.py", ".github/workflows/ci.yml",
             "docs/README.md"]
    for name in names:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    return tmp_path


def test_active_release_tree_is_consistent():
    metadata = contract.check_release_tree(ROOT)
    from pvs import __version__
    assert metadata["version"] == __version__


@pytest.mark.parametrize("replacement", ["https://chatwoodlabs.com", "stale-tag"])
def test_documentation_url_cannot_drift_from_the_current_release(release_tree, replacement):
    tag = contract.release_metadata(release_tree)["tag"]
    path = release_tree / "pyproject.toml"
    contents = path.read_text(encoding="utf-8")
    url = contract._toml(path)["project"]["urls"]["Documentation"]
    if replacement == "stale-tag":
        replacement = url.replace(f"/{tag}/", "/v99.88.77/")
    path.write_text(contents.replace(url, replacement), encoding="utf-8")
    with pytest.raises(ValueError, match="Documentation URL"):
        contract.check_release_tree(release_tree)


def test_documentation_entry_point_must_be_in_the_release_tree(release_tree):
    (release_tree / "docs/README.md").unlink()
    with pytest.raises(ValueError, match=r"requires docs/README\.md"):
        contract.check_release_tree(release_tree)


@pytest.mark.parametrize("name", ["src/pvs/version.py", "uv.lock", "CITATION.cff",
                                  "README.md", "BUNDLE_README.md", "test-pvs.sh", "test-pvs.ps1",
                                  "tests/test_cli.py"])
def test_gate_rejects_stale_active_metadata(release_tree, name):
    version = contract.release_metadata(release_tree)["version"]
    path = release_tree / name
    path.write_text(path.read_text(encoding="utf-8").replace(version, "99.88.77"),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="release version drift"):
        contract.check_release_tree(release_tree)


@pytest.mark.parametrize("extension", ["yaml", "yml"])
def test_gate_scans_new_workflows_in_hidden_subdirectories(release_tree, extension):
    version = contract.release_metadata(release_tree)["version"]
    relative = f".github/workflows/additional/release.{extension}"
    path = release_tree / relative
    path.parent.mkdir()
    path.write_text("run: python tools/assemble-release.py source --version 99.88.77\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="release version drift") as caught:
        contract.check_release_tree(release_tree)
    assert str(caught.value) == (
        f"release version drift:\n{relative}:1: 99.88.77 != {version}"
    )


@pytest.mark.parametrize("root", [
    PurePosixPath("/release builds/PVS"),
    PureWindowsPath(r"D:\release builds\PVS"),
    PureWindowsPath(r"\\build-server\releases\PVS"),
], ids=["posix", "windows-drive", "windows-unc"])
def test_version_drift_diagnostics_are_portable_and_keep_all_stale_references(root):
    # Pure paths exercise both flavours on every host; no Windows I/O is simulated.
    relative = ".github/workflows/nested folder/qualifi\u00e9.yaml"
    version = contract.release_metadata(ROOT)["version"]
    contents = (
        "# independent subject version: 1.2.3\r\n"
        f"wheel: physics_validation_suite-{version}-py3-none-any.whl\r\n"
        "run: source --version 99.88.77\r\n"
        f"files: pvs-99.88.76-release-bundle.zip RELEASE_NOTES_{version}.md\r\n"
    )
    errors = contract._version_drift_errors(root, root / relative, contents, version)
    assert errors == [
        f"{relative}:3: 99.88.77 != {version}",
        f"{relative}:4: 99.88.76 != {version}",
    ]


def test_cli_rejects_drift_without_emitting_metadata_or_job_outputs(release_tree, tmp_path):
    relative = ".github/workflows/stale.yml"
    (release_tree / relative).write_text("bundle: pvs-99.88.77-release-bundle.zip\n",
                                        encoding="utf-8")
    output = tmp_path / "github-output"
    output.write_bytes(b"existing=value\n")
    version = contract.release_metadata(release_tree)["version"]
    run = subprocess.run(
        [sys.executable, str(ROOT / "tools/release_contract.py"), "--root", str(release_tree),
         "--check", "--github-output", str(output)], capture_output=True, text=True,
    )
    assert run.returncode == 1
    assert run.stdout == ""
    assert run.stderr == f"release version drift:\n{relative}:1: 99.88.77 != {version}\n"
    assert output.read_bytes() == b"existing=value\n"


@pytest.mark.parametrize("version, tag", [
    ("12.34.56", "v12.34.56"),
    ("12.34.56a2", "v12.34.56-alpha.2"),
    ("12.34.56b1", "v12.34.56-beta.1"),
    ("12.34.56rc1", "v12.34.56-rc.1"),
])
def test_future_version_changes_all_names_without_editing_ci(release_tree, version, tag):
    previous = contract.release_metadata(release_tree)
    workflow = (release_tree / ".github/workflows/ci.yml").read_bytes()
    for path in release_tree.rglob("*"):
        if path.is_file() and ".github" not in path.parts:
            path.write_text(path.read_text(encoding="utf-8").replace(
                previous["tag"], tag).replace(previous["version"], version), encoding="utf-8")
    changed = contract.check_release_tree(release_tree)
    assert changed["version"] == version
    assert changed["tag"] == tag
    assert all("12.34.56" in value for value in changed.values())
    assert (release_tree / ".github/workflows/ci.yml").read_bytes() == workflow


def test_source_release_does_not_require_notes_or_relabel_historical_records(release_tree):
    notes = release_tree / contract.release_metadata(release_tree)["release_notes"]
    assert not notes.exists()
    (release_tree / "RELEASE_NOTES_99.88.77.md").write_text("Historical release.\n")
    contract.check_release_tree(release_tree)
    notes.write_text("Mismatched current release notes.\n")
    with pytest.raises(ValueError, match="current release notes"):
        contract.check_release_tree(release_tree)


def test_historical_records_do_not_participate_in_active_version_checks(release_tree):
    path = release_tree / "docs/qualification-99.88.77.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text('{"version": "99.88.77"}\n')
    contract.check_release_tree(release_tree)
    assert json.loads(path.read_text())["version"] == "99.88.77"


def test_ci_metadata_command_writes_the_same_names_as_json_and_job_outputs(tmp_path):
    output = tmp_path / "github-output"
    run = subprocess.run([sys.executable, str(ROOT / "tools/release_contract.py"), "--check",
                          "--github-output", str(output)], check=True, capture_output=True,
                         text=True)
    metadata = json.loads(run.stdout)
    assert dict(line.split("=", 1) for line in output.read_text().splitlines()) == metadata
    assert metadata == contract.check_release_tree(ROOT)
