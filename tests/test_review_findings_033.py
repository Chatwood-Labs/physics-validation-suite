"""Reproductions reconstructed from the 0.3.3 review's documented scenarios.

The review's test file was not supplied. These exercise real verification and
public API/CLI entry points without alternate dependency implementations.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import pvs.api as api_module
import pvs.cli as cli_module
from pvs.api import validate_case
from pvs.jsonutil import load_strict, write_pretty
from pvs.verify import verify_target


@pytest.mark.parametrize("package_target", [False, True], ids=["record", "package"])
@pytest.mark.parametrize("keep_claimed_id", [False, True], ids=["forged-id", "genuine-id"])
def test_inspect_displays_only_the_record_actually_verified(
    package_factory, monkeypatch, capsys, package_target: bool, keep_claimed_id: bool
) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    path = outcome.evidence_path
    pristine_bytes = path.read_bytes()
    pristine = load_strict(path)
    altered = copy.deepcopy(pristine)
    altered["record"]["subject"]["name"] = "FORGED SUBJECT NOT VERIFIED"
    if not keep_claimed_id:
        altered["integrity"]["evidence_id"] = "pvs:sha256:" + "0" * 64
    write_pretty(path, altered)
    target = package if package_target else path
    assert not verify_target(target).valid
    reads = 0

    def load_then_restore(source: Path):
        nonlocal reads
        value = load_strict(source)
        if source == path:
            reads += 1
            replacement = path.with_name("restored-evidence.tmp")
            replacement.write_bytes(pristine_bytes)
            replacement.replace(path)
        return value

    monkeypatch.setattr(cli_module, "load_strict", load_then_restore)
    assert cli_module.main(["inspect", str(target), "--json"]) == 0
    output = capsys.readouterr()
    displayed = json.loads(output.out)
    assert reads == 1
    assert output.err == ""
    assert verify_target(target).valid
    assert displayed["integrity_valid"] is True
    assert displayed["subject"] == pristine["record"]["subject"]
    assert displayed["evidence_id"] == outcome.evidence_id
    assert displayed["finding_id"] == outcome.finding_id
    assert displayed["run_id"] == outcome.run_id
    assert displayed["checks"] == pristine["record"]["summary"]["counts"]
    assert displayed["verification_level"] == ("package" if package_target else "record")


def test_interruption_cleans_unpublished_staging(case_factory, tmp_path, monkeypatch) -> None:
    case_path, _ = case_factory()
    output = tmp_path / "interrupted"
    interruption = KeyboardInterrupt("review cancellation")

    def interrupt(*args, **kwargs):
        assert list(tmp_path.glob(".interrupted.pvs-tmp-*"))
        assert list(tmp_path.glob(".interrupted.pvs-validation-*"))
        raise interruption

    monkeypatch.setattr(api_module, "evaluate_checks", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        validate_case(case_path, output_dir=output)
    assert caught.value is interruption
    assert not output.exists()
    assert not list(tmp_path.glob(".interrupted.pvs-*"))


def test_validate_timing_includes_snapshot_acquisition_and_checks(
    case_factory, tmp_path, monkeypatch
) -> None:
    case_path, _ = case_factory()
    elapsed = 0.0
    origin = datetime(2026, 9, 8, tzinfo=timezone.utc)
    acquire, evaluate = api_module.acquire_validation_snapshot, api_module.evaluate_checks

    def timed_acquire(*args, **kwargs):
        nonlocal elapsed
        result = acquire(*args, **kwargs)
        elapsed += 2.0
        return result

    def timed_evaluate(*args, **kwargs):
        nonlocal elapsed
        result = evaluate(*args, **kwargs)
        elapsed += 3.0
        return result

    monkeypatch.setattr(api_module, "time", SimpleNamespace(perf_counter=lambda: elapsed))
    monkeypatch.setattr(api_module, "utc_now", lambda: origin + timedelta(seconds=elapsed))
    monkeypatch.setattr(api_module, "acquire_validation_snapshot", timed_acquire)
    monkeypatch.setattr(api_module, "evaluate_checks", timed_evaluate)
    outcome = validate_case(case_path, output_dir=tmp_path / "timed")
    record = load_strict(outcome.evidence_path)["record"]
    run = record["run"]
    assert run["mode"] == "validate"
    assert run["duration_seconds"] == 5.0
    assert datetime.fromisoformat(run["finished_at"].replace("Z", "+00:00")) == (
        origin + timedelta(seconds=5)
    )
    for key in ("mode", "started_at", "finished_at", "duration_seconds"):
        assert record["execution"][key] == run[key]
    assert record["execution"]["command"] is None
    assert record["execution"]["return_code"] is None
    assert record["summary"]["status"] == "PASS"
    assert verify_target(outcome.output_directory).valid
