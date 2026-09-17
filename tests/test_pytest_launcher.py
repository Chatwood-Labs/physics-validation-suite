"""Exercise isolated pytest runs without touching any pre-existing base path."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
LAUNCHER = ROOT / "tools/run-pytest.py"


def _invoke(probe: Path, *arguments: str, addopts: str = "") -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS=addopts)
    return subprocess.run(
        [sys.executable, str(LAUNCHER), "-q", str(probe), *arguments],
        cwd=probe.parent, env=environment, capture_output=True, text=True, timeout=30,
    )


def _temporary_root(output: str) -> Path:
    prefix = "pytest temporary root: "
    matches = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    assert len(matches) == 1, output
    root = Path(matches[0])
    assert root.parent.resolve() == Path(tempfile.gettempdir()).resolve()
    assert root.name.startswith("pvs-test-")
    return root


def test_invocations_have_distinct_short_bases_and_preserve_unrelated_files(tmp_path: Path) -> None:
    unrelated = tmp_path / "must-survive"
    unrelated.mkdir()
    sentinel = unrelated / "sentinel.txt"
    sentinel.write_text("retain this", encoding="utf-8")
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "def test_owned_temp(tmp_path):\n"
        "    assert tmp_path.parent.name == 'tests'\n"
        "    (tmp_path / 'probe').write_text('ok')\n",
        encoding="utf-8",
    )
    # Explicit launcher CLI options must override any inherited shared base.
    first = _invoke(probe, addopts=f'--basetemp="{unrelated}"')
    assert first.returncode == 0, first.stdout + first.stderr
    first_root = _temporary_root(first.stdout)
    assert not first_root.exists()
    second = _invoke(probe, "--keep-temp")
    assert second.returncode == 0, second.stdout + second.stderr
    second_root = _temporary_root(second.stdout)
    try:
        assert first_root != second_root
        assert (second_root / "tests").is_dir()
        assert sentinel.read_text(encoding="utf-8") == "retain this"
        assert probe.is_file()
    finally:
        shutil.rmtree(second_root)


def test_pytest_failure_exit_code_is_preserved_and_owned_base_is_cleaned(tmp_path: Path) -> None:
    probe = tmp_path / "test_probe.py"
    probe.write_text("def test_failure(tmp_path):\n    assert False, 'expected probe failure'\n")
    result = _invoke(probe)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "expected probe failure" in result.stdout
    assert not _temporary_root(result.stdout).exists()


@pytest.mark.parametrize("arguments", [("--basetemp",), ("--basetemp=",), ("--",)])
def test_launcher_refuses_caller_selected_basetemp(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    probe = tmp_path / "sentinel.txt"
    probe.write_text("preserve", encoding="utf-8")
    if arguments == ("--basetemp",):
        arguments = (*arguments, str(tmp_path))
    elif arguments == ("--basetemp=",):
        arguments = (f"--basetemp={tmp_path}",)
    result = _invoke(probe, *arguments)
    assert result.returncode == 2
    assert "managed by this launcher" in result.stderr
    assert "pytest temporary root:" not in result.stdout
    assert probe.read_text(encoding="utf-8") == "preserve"


def test_both_acceptance_environments_use_the_isolated_launcher() -> None:
    powershell = (ROOT / "test-pvs.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "test-pvs.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '$PytestArguments = @("tools\\run-pytest.py")' in powershell
    assert 'Invoke-NativeChecked "pytest" $script:PvsPython ($PytestArguments + @(' in powershell
    assert ') + $PytestArguments + @(' in powershell
    assert '$PytestArguments += "--keep-temp"' in powershell
    assert '"$PVS_PYTHON" "${PYTEST_ARGUMENTS[@]}"' in shell
    assert 'python "${PYTEST_ARGUMENTS[@]}"' in shell
    assert 'PYTEST_ARGUMENTS+=(--keep-temp)' in shell
    assert "python -m pytest" not in workflow
    assert "python tools/run-pytest.py" in workflow
