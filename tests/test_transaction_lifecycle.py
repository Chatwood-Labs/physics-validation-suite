"""Cancellation respects staging ownership and the validation timing boundary."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest
from conftest import base_case

import pvs.api as api_module
from pvs.cli import main
from pvs.jsonutil import load_strict
from pvs.verify import verify_target


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("phase", ["setup", "rename"])
def test_publication_preflight_cleans_probes_on_unsuccessful_exit(
    tmp_path, monkeypatch, failure, phase
) -> None:
    error = failure("preflight cancelled")
    mkdir = api_module.tempfile.mkdtemp
    count = 0

    def stop_setup(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise error
        return mkdir(*args, **kwargs)

    def stop_rename(*args, **kwargs):
        raise error

    if phase == "setup":
        monkeypatch.setattr(api_module.tempfile, "mkdtemp", stop_setup)
    else:
        monkeypatch.setattr(api_module, "_publish_no_replace", stop_rename)
    with pytest.raises(failure) as caught:
        api_module._preflight_atomic_publication(tmp_path)
    assert caught.value is error
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit, RuntimeError])
@pytest.mark.parametrize("phase", ["_retain_case", "acquire_validation_snapshot", "render_html",
                                   "verify_target", "_publish_no_replace"])
def test_unsuccessful_exit_cleans_private_work_and_preserves_exception(
    case_factory, tmp_path, monkeypatch, failure, phase
) -> None:
    data = base_case()
    data["package"]["html"] = True
    case_path, _ = case_factory(data)
    target = tmp_path / "cancelled"
    sentinel = tmp_path / "unrelated"
    sentinel.mkdir()
    (sentinel / "keep").write_bytes(b"not owned by this transaction")
    owner = api_module
    real = getattr(owner, phase)
    error = failure("injected unsuccessful exit")

    def stop(*args, **kwargs):
        if phase == "_publish_no_replace" and ".pvs-tmp-" not in args[0].name:
            return real(*args, **kwargs)  # Allow the native capability preflight.
        assert list(tmp_path.glob(".cancelled.pvs-tmp-*"))
        raise error

    monkeypatch.setattr(owner, phase, stop)
    with pytest.raises(failure) as caught:
        api_module.validate_case(case_path, output_dir=target)
    assert caught.value is error
    assert not target.exists()
    assert not list(tmp_path.glob(".cancelled.pvs-*"))
    assert (sentinel / "keep").read_bytes() == b"not owned by this transaction"


@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
def test_cli_interruption_returns_130_and_cleans_staging(
    case_factory, tmp_path, monkeypatch, capsys, json_output
) -> None:
    case_path, _ = case_factory()
    target = tmp_path / "cli-cancelled"

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(api_module, "evaluate_checks", interrupt)
    args = ["validate", str(case_path), "--output", str(target)]
    assert main(args + (["--json"] if json_output else [])) == 130
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out) == {
            "status": "ERROR", "kind": "interrupted", "message": "PVS interrupted"
        }
        assert output.err == ""
    else:
        assert output.out == ""
        assert output.err == "PVS interrupted\n"
    assert not target.exists()
    assert not list(tmp_path.glob(".cli-cancelled.pvs-*"))


def test_interrupt_after_atomic_rename_preserves_the_published_package(
    case_factory, tmp_path, monkeypatch
) -> None:
    case_path, _ = case_factory()
    target = tmp_path / "committed"
    publish = api_module._publish_no_replace

    def publish_then_interrupt(source, destination):
        publish(source, destination)
        if destination == target:
            raise KeyboardInterrupt

    monkeypatch.setattr(api_module, "_publish_no_replace", publish_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        api_module.validate_case(case_path, output_dir=target)
    assert target.is_dir()
    assert not list(tmp_path.glob(".committed.pvs-*"))
    assert verify_target(target).valid


def test_validation_timing_stops_before_report_generation(case_factory, tmp_path, monkeypatch):
    data = base_case()
    data["package"]["html"] = True
    case_path, _ = case_factory(data)
    clock = 0.0
    evaluate, render = api_module.evaluate_checks, api_module.render_html

    def checks(*args, **kwargs):
        nonlocal clock
        result = evaluate(*args, **kwargs)
        clock += 5.0
        return result

    def report(*args, **kwargs):
        nonlocal clock
        clock += 100.0
        return render(*args, **kwargs)

    monkeypatch.setattr(api_module, "time", SimpleNamespace(perf_counter=lambda: clock))
    monkeypatch.setattr(api_module, "evaluate_checks", checks)
    monkeypatch.setattr(api_module, "render_html", report)
    outcome = api_module.validate_case(case_path, output_dir=tmp_path / "timed-report")
    assert clock == 105.0
    assert load_strict(outcome.evidence_path)["record"]["run"]["duration_seconds"] == 5.0
    assert verify_target(outcome.output_directory).valid


@pytest.mark.skipif(os.name != "posix", reason="PVS subject execution is POSIX-only")
def test_run_timing_remains_the_subject_execution_interval(case_factory, tmp_path, monkeypatch):
    data = base_case()
    data["artifacts"]["result"]["role"] = "input"
    data["execution"] = {"command": [sys.executable, "-c", "pass"]}
    case_path, _ = case_factory(data)
    captured = []
    run_command = api_module.run_command

    def subject(*args, **kwargs):
        result = run_command(*args, **kwargs)
        captured.append(result[0])
        return result

    def no_validation_clock():
        pytest.fail("run mode must use the runner's subject interval")

    monkeypatch.setattr(api_module, "run_command", subject)
    monkeypatch.setattr(api_module, "time", SimpleNamespace(perf_counter=no_validation_clock))
    outcome = api_module.run_case(case_path, output_dir=tmp_path / "subject-timing")
    run = load_strict(outcome.evidence_path)["record"]["run"]
    assert len(captured) == 1
    for key in ("mode", "started_at", "finished_at", "duration_seconds"):
        assert run[key] == getattr(captured[0], key)
    assert run["mode"] == "run"
    assert verify_target(outcome.output_directory).valid
