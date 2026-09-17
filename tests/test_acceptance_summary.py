"""Qualification must expose omissions, failures and exact scientific differences."""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import base_case, dump_case

from pvs.api import validate_case

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "acceptance_summary", ROOT / "tools/acceptance-summary.py"
)
assert SPEC is not None and SPEC.loader is not None
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def _complete_run(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "run"
    release = tmp_path / "release"
    run.mkdir()
    release.mkdir()
    summary.write_json(release / "RELEASE_METADATA.json", {"version": "fixture"})
    (release / "source.zip").write_bytes(b"fixture")
    (release / "SHA256SUMS").write_text(summary.digest(release / "source.zip") + "  source.zip\n")
    (run / "acceptance-stages.tsv").write_text("start\tend\tPASSED\tqualification\n")
    summary.write_json(run / "selected-interpreter.json", summary.interpreter())
    (tmp_path / "lock").write_bytes(b"lock fixture")
    env = summary.environment("runtime-all", tmp_path / "lock")
    for profile in ("runtime-all", "ordinary-dev-all", "frozen-dev-all"):
        summary.write_json(run / (profile + ".json"), {**env, "profile": profile})
    for profile in ("ordinary", "frozen"):
        (run / (profile + "-pytest.xml")).write_text(
            '<testsuites><testsuite><testcase classname="tests" name="passing"/>'
            '<testcase classname="tests" name="link"><properties>'
            '<property name="pvs_capability" value="windows_filesystem"/>'
            '</properties><skipped message="host has no symlink privilege"/>'
            "</testcase></testsuite></testsuites>"
        )
    summary.write_json(run / "plasma-finding-diff.json", {"status": "IDENTICAL", "differences": []})
    return run, release


def test_summary_records_real_skip_reasons_and_environment_identities(tmp_path: Path) -> None:
    run, release = _complete_run(tmp_path)
    result = summary.finalize(run, release, "PASSED", "full")
    assert result["status"] == "PASSED"
    tests = result["test_runs"][0]
    assert (tests["passed"], tests["skipped"], tests["failed"]) == (1, 1, 0)
    assert tests["skip_reasons"] == [{"reason": "host has no symlink privilege", "count": 1}]
    assert tests["capabilities"] == {"windows_filesystem": {"skipped": 1}}
    assert len(result["environments"]) == 3
    packages = result["environments"][0]["packages"]
    assert len(packages) == len({(p["name"], p["version"]) for p in packages})
    assert result["environments"][0]["interpreter"]["executable"] == str(
        Path(sys.executable).absolute()
    )
    assert result["artifact_identities"][0]["matches_checksum"]
    assert result["environments"][0]["dependency_versions_id"].startswith(
        "pvs-dependency-versions:sha256:"
    )


@pytest.mark.parametrize("field", ["version", "implementation", "base_executable"])
def test_interpreter_drift_cannot_receive_passing_summary(tmp_path: Path, field: str) -> None:
    run, release = _complete_run(tmp_path)
    path = run / "frozen-dev-all.json"
    env = json.loads(path.read_text())
    env["interpreter"][field] += "-different"
    summary.write_json(path, env)
    result = summary.finalize(run, release, "PASSED", "full")
    assert result["status"] == "FAILED"
    assert "frozen-dev-all: interpreter differs from selected base" in result["problems"]


@pytest.mark.parametrize(
    "missing",
    [
        "ordinary-pytest.xml",
        "frozen-pytest.xml",
        "frozen-dev-all.json",
        "selected-interpreter.json",
        "plasma-finding-diff.json",
        "acceptance-stages.tsv",
    ],
)
def test_missing_qualification_evidence_cannot_receive_passing_summary(
    tmp_path: Path,
    missing: str,
) -> None:
    run, release = _complete_run(tmp_path)
    (run / missing).unlink()
    result = summary.finalize(run, release, "PASSED", "full")
    assert result["status"] == "FAILED"
    assert result["problems"]


def test_failing_junit_and_changed_release_bytes_are_not_hidden(tmp_path: Path) -> None:
    run, release = _complete_run(tmp_path)
    (run / "ordinary-pytest.xml").write_text(
        '<testsuite><testcase name="bad"><failure message="bug"/></testcase></testsuite>'
    )
    (release / "source.zip").write_bytes(b"changed")
    result = summary.finalize(run, release, "PASSED", "full")
    assert result["status"] == "FAILED"
    assert len(result["problems"]) == 2
    assert not result["artifact_identities"][0]["matches_checksum"]
    assert result["test_runs"][0]["failed"] == 1


def test_quick_mode_distinguishes_gates_not_run(tmp_path: Path) -> None:
    run, release = _complete_run(tmp_path)
    for name in (
        "ordinary-pytest.xml",
        "frozen-pytest.xml",
        "ordinary-dev-all.json",
        "frozen-dev-all.json",
        "plasma-finding-diff.json",
    ):
        (run / name).unlink()
    result = summary.finalize(run, release, "PASSED", "quick")
    assert result["status"] == "PASSED"
    assert all(test["status"] == "NOT_RUN" for test in result["test_runs"])
    assert len(result["environments"]) == 1
    assert "NOT_RUN" in result["subject_execution"]


def test_atomic_summary_failure_preserves_previous_file_and_cleans_temp(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_bytes(b"previous")
    with pytest.raises(ValueError):
        summary.write_json(path, {"invalid": math.nan})
    assert path.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [path]


def test_exact_projection_diff_distinguishes_observations_from_transaction_metadata(
    tmp_path: Path,
) -> None:
    case = base_case()
    case["checks"] = [
        {
            "id": "finite",
            "type": "finite",
            "unit": "1",
            "source": {"artifact": "result", "unit": "1"},
        }
    ]
    paths = []
    for index, value in enumerate([2.0, 2.0, math.nextafter(2.0, math.inf)]):
        root = tmp_path / str(index)
        root.mkdir()
        (root / "result.json").write_text(json.dumps([1.0, value]))
        outcome = validate_case(
            dump_case(root / "pvs.yaml", copy.deepcopy(case)), output_dir=root / "package"
        )
        paths.append(outcome.evidence_path)
    same = summary.compare_findings(paths[0], paths[1])
    assert same["status"] == "IDENTICAL"
    assert same["left_evidence_sha256"] != same["right_evidence_sha256"]
    different = summary.compare_findings(paths[0], paths[2])
    assert different["status"] == "DIFFERENT"
    assert different["left_finding_id"] != different["right_finding_id"]
    assert len(different["differences"]) == 1
    change = different["differences"][0]
    assert change["path"].endswith("/observed/maximum")
    assert change["left_binary64"] == (2.0).hex()
    assert change["right_binary64"] == math.nextafter(2.0, math.inf).hex()
    envelope = json.loads(paths[2].read_text())
    envelope["record"]["checks"][0]["observed"]["maximum"] = 3.0
    paths[2].write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="valid evidence"):
        summary.compare_findings(paths[0], paths[2])


@pytest.mark.parametrize(
    "code",
    [
        'def test_probe():\n    import pytest\n    pytest.skip("missing capability")\n',
        'import pytest\npytest.skip("missing capability", allow_module_level=True)\n',
    ],
)
def test_strict_capability_lane_fails_on_execution_and_collection_skips(
    tmp_path: Path,
    code: str,
) -> None:
    probe = tmp_path / "test_probe.py"
    probe.write_text(code)
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "",
        "PYTHONPATH": str(ROOT / "tests"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "qualification_plugin",
            "--require-no-skips",
            str(probe),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Required capability qualification contains skips" in result.stdout


def test_embedded_bootstrap_python_is_valid_and_uv_uses_selected_executable() -> None:
    powershell = (ROOT / "test-pvs.ps1").read_text()
    for code in re.findall(r"= @'\n(.*?)\n'@", powershell, re.DOTALL):
        compile(code, "PowerShell embedded Python", "exec")
    for arguments in re.findall(r"\$UvExecutable @\(?\((.*?)\)", powershell, re.DOTALL):
        assert '"--python", $SelectedPythonPath' in arguments
    shell = (ROOT / "test-pvs.sh").read_text()
    invocations = re.findall(r'"\$UV_BIN" (?:lock|sync|run)[^\n]+', shell)
    assert len(invocations) == 7
    assert all('--python "$SELECTED_PYTHON"' in invocation for invocation in invocations)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX acceptance bootstrap")
def test_posix_bootstrap_failure_leaves_a_failed_summary(tmp_path: Path) -> None:
    script = tmp_path / "test-pvs.sh"
    shutil.copyfile(ROOT / "test-pvs.sh", script)
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    summaries = list(tmp_path.glob("acceptance-runs/*/acceptance-summary.json"))
    assert len(summaries) == 1
    assert json.loads(summaries[0].read_text())["status"] == "FAILED"
    assert "release acceptance passed" not in result.stdout
