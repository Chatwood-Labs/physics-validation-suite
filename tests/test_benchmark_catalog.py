"""The shipped library must be complete, portable and fail closed on bad data."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "tools" / "run-benchmark-packs.py"


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("benchmark_catalog_runner", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def library_copy(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    shutil.copytree(ROOT / "benchmark-packs", root / "benchmark-packs")
    return root


def test_catalog_enumerates_exact_current_scientific_content(runner: ModuleType) -> None:
    packs = runner.load_catalog(ROOT)
    expected = {
        "plasma-physics-reference-v1": ("3.0.0", 31, 52),
        "fusion-reactivity-v1": ("1.0.0", 21, 60),
        "plasma-scales-v1": ("1.0.0", 33, 30),
        "classical-physics-v1": ("1.0.0", 18, 36),
    }
    assert {
        pack["id"]: (pack["case_version"], pack["check_count"], pack["reference_value_count"])
        for pack in packs
    } == expected
    for pack in packs:
        runner.preflight(ROOT / "benchmark-packs" / pack["directory"], pack)
        manifest = json.loads(
            (ROOT / "benchmark-packs" / pack["id"] / "source_manifest.json").read_text()
        )
        assert pack["primary_sources"] == [
            value["primary_locator"] for value in manifest["sources"].values()
        ]
    fusion = next(pack for pack in packs if pack["id"] == "fusion-reactivity-v1")
    assert fusion["data_kinds"] == ["published-tabulation"]


def test_runner_preserves_source_and_truthful_validate_mode(
    runner: ModuleType, tmp_path: Path
) -> None:
    pack_id = "classical-physics-v1"
    before = runner.source_identities(ROOT / "benchmark-packs" / pack_id)
    output = tmp_path / "results"
    assert runner.run_benchmarks(output, pack_id=pack_id, root=ROOT) == 0
    assert runner.source_identities(ROOT / "benchmark-packs" / pack_id) == before
    summary = json.loads((output / "benchmark-summary.json").read_text())
    assert summary["status"] == "PASS"
    assert summary["selected_packs"] == [pack_id]
    result = summary["packs"][0]
    assert result["passed_checks"] == result["check_count"] == 18
    retained = output / pack_id
    producer = json.loads((retained / "producer.json").read_text())
    assert producer["execution_mode"] == "external-reference-producer"
    assert producer["return_code"] == 0
    assert producer["source_files"] == before
    assert result["producer_record_identity"] == runner.identity(retained / "producer.json")
    evidence = json.loads((retained / "evidence" / "evidence.json").read_text())
    assert evidence["record"]["execution"]["mode"] == "validate"
    assert (retained / "evidence" / "case" / "pvs.yaml").read_bytes() == (
        ROOT / "benchmark-packs" / pack_id / "pvs.yaml"
    ).read_bytes()
    for name in ("report.pdf", "report.html", "manifest.json"):
        assert (retained / "evidence" / name).stat().st_size > 0


def test_missing_pack_cannot_be_silently_skipped(
    runner: ModuleType, library_copy: Path, tmp_path: Path
) -> None:
    shutil.rmtree(library_copy / "benchmark-packs" / "plasma-scales-v1")
    output = tmp_path / "results"
    assert runner.run_benchmarks(output, root=library_copy) == 1
    summary = json.loads((output / "benchmark-summary.json").read_text())
    assert summary["status"] == "FAIL"
    assert "missing" in summary["error"]
    assert not summary["packs"]


def test_unknown_selection_cannot_be_an_empty_pass(runner: ModuleType, tmp_path: Path) -> None:
    output = tmp_path / "results"
    assert runner.run_benchmarks(output, root=ROOT, pack_id="does-not-exist") == 1
    summary = json.loads((output / "benchmark-summary.json").read_text())
    assert summary["status"] == "FAIL"
    assert "unknown benchmark pack" in summary["error"]


def test_changed_producer_pin_prevents_execution(
    runner: ModuleType, library_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = library_copy / "benchmark-packs" / "classical-physics-v1" / "derive_reference_values.py"
    path.write_bytes(path.read_bytes() + b"\n# unexpected change\n")

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("a producer with changed SHA-256 must never execute")

    monkeypatch.setattr(runner.subprocess, "run", unexpected)
    output = tmp_path / "results"
    assert runner.run_benchmarks(output, root=library_copy, pack_id="classical-physics-v1") == 1
    summary = json.loads((output / "benchmark-summary.json").read_text())
    assert "SHA-256 pin" in summary["packs"][0]["error"]


def test_nonzero_producer_exit_is_retained_and_fails(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        kwargs["stderr"].write(b"deliberate reference producer failure\n")
        return subprocess.CompletedProcess(command, 17)

    monkeypatch.setattr(runner.subprocess, "run", fail)
    output = tmp_path / "results"
    assert runner.run_benchmarks(output, root=ROOT, pack_id="classical-physics-v1") == 1
    retained = output / "classical-physics-v1"
    record = json.loads((retained / "producer.json").read_text())
    assert record["return_code"] == 17
    assert "code 17" in record["error"]
    assert "deliberate" in (retained / "producer.stderr.txt").read_text()
    assert not (retained / "evidence").exists()


def test_wrong_actual_output_is_not_reported_as_pass(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_run = subprocess.run

    def corrupt(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        completed = original_run(command, **kwargs)
        output = Path(kwargs["cwd"]) / "derived_values.json"
        values = json.loads(output.read_text())
        # Preserve valid JSON but remove the physical quantities expected by the case.
        values.clear()
        output.write_text(json.dumps(values), encoding="utf-8")
        return completed

    monkeypatch.setattr(runner.subprocess, "run", corrupt)
    output = tmp_path / "results"
    assert runner.run_benchmarks(output, root=ROOT, pack_id="classical-physics-v1") == 1
    summary = json.loads((output / "benchmark-summary.json").read_text())
    result = summary["packs"][0]
    assert result["status"] == "FAIL"
    assert result["verification_valid"] is True
    assert result["passed_checks"] < result["check_count"]


def test_source_output_and_existing_results_are_rejected(
    runner: ModuleType, library_copy: Path, tmp_path: Path
) -> None:
    pack_id = "classical-physics-v1"
    (library_copy / "benchmark-packs" / pack_id / "derived_values.json").write_text("{}")
    output = tmp_path / "results"
    assert runner.run_benchmarks(output, root=library_copy, pack_id=pack_id) == 1
    summary = json.loads((output / "benchmark-summary.json").read_text())
    assert "already exists in frozen pack" in summary["packs"][0]["error"]
    retained = (output / "benchmark-summary.json").read_bytes()
    with pytest.raises(FileExistsError):
        runner.run_benchmarks(output, root=library_copy, pack_id=pack_id)
    assert (output / "benchmark-summary.json").read_bytes() == retained
