#!/usr/bin/env python3
"""Run the trusted shipped reference producers, then validate their outputs.

This portable helper executes producers outside PVS supervision. Every resulting
PVS record truthfully has execution mode ``validate``. Producer command identity,
logs and source/output hashes are retained separately; no production physics
solver is run and no measured-data agreement is inferred from a passing result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from pvs import __version__, validate_case, verify_target
from pvs.case import load_case
from pvs.jsonutil import load_strict
from pvs.paths import path_is_link_or_reparse

SOURCE_ROOT = Path(__file__).resolve().parents[1]
DATA_KINDS = {"analytically-derived", "published-tabulation", "physical-constants"}
ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def identity(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )


def confined_file(root: Path, relative: Any) -> Path:
    """Require an existing ordinary file below a trusted pack directory."""

    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError(f"unsafe pack path: {relative!r}")
    parts = PurePosixPath(relative).parts
    if relative.startswith("/") or any(part in {".", ".."} for part in parts):
        raise ValueError(f"unsafe pack path: {relative!r}")
    path = root
    for part in parts:
        path /= part
        if path_is_link_or_reparse(path):
            raise ValueError(f"linked pack path is unsupported: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"missing or unsafe pack file: {relative}")
    return path


def source_identities(root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.relative_to(root).parts:
            continue
        if path_is_link_or_reparse(path):
            raise ValueError(f"linked pack entry is unsupported: {path.name}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = identity(path)
    return result


def load_catalog(root: Path = SOURCE_ROOT) -> list[dict[str, Any]]:
    library = root / "benchmark-packs"
    if path_is_link_or_reparse(library):
        raise ValueError("linked benchmark library is unsupported")
    catalog = load_strict(confined_file(library, "catalog.json"))
    if not isinstance(catalog, dict) or catalog.get("schema") != "pvs-benchmark-catalog/1":
        raise ValueError("unsupported benchmark catalog")
    packs = catalog.get("packs")
    if not isinstance(packs, list) or not packs:
        raise ValueError("benchmark catalog must contain packs")
    seen = set()
    for pack in packs:
        if not isinstance(pack, dict):
            raise ValueError("invalid benchmark catalog entry")
        pack_id = pack.get("id")
        if not isinstance(pack_id, str) or not ID_PATTERN.fullmatch(pack_id) or pack_id in seen:
            raise ValueError(f"invalid or duplicate benchmark id: {pack_id!r}")
        seen.add(pack_id)
        if pack.get("directory") != pack_id:
            raise ValueError(f"pack directory must equal its id: {pack_id}")
        directory = library / pack_id
        if path_is_link_or_reparse(directory) or not directory.is_dir():
            raise ValueError(f"missing or linked benchmark directory: {pack_id}")
        if not set(pack.get("data_kinds", [])).issubset(DATA_KINDS) or not pack.get("data_kinds"):
            raise ValueError(f"invalid benchmark data kinds: {pack_id}")
        for name in ("check_count", "reference_value_count", "source_count"):
            if type(pack.get(name)) is not int or pack[name] <= 0:
                raise ValueError(f"invalid {name}: {pack_id}")
        for name in ("title", "case_version", "scope", "limitations"):
            if not isinstance(pack.get(name), str) or not pack[name]:
                raise ValueError(f"missing {name}: {pack_id}")
        if not isinstance(pack.get("primary_sources"), list) or not pack["primary_sources"]:
            raise ValueError(f"missing primary sources: {pack_id}")
        for url in pack["primary_sources"]:
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError(f"invalid primary source locator: {pack_id}")
        case_path = confined_file(directory, "pvs.yaml")
        confined_file(directory, "source_manifest.json")
        case = load_case(case_path)
        if case.data["version"] != pack["case_version"]:
            raise ValueError(f"catalog case version differs: {pack_id}")
        if len(case.data["checks"]) != pack["check_count"]:
            raise ValueError(f"catalog check count differs: {pack_id}")
        if len(case.data["references"]) != pack["source_count"]:
            raise ValueError(f"catalog source count differs: {pack_id}")
        command = case.data.get("execution", {}).get("command")
        expected_command = ["python", pack.get("producer_script"), "--output", pack.get("output")]
        if command != expected_command:
            raise ValueError(f"catalog producer command differs: {pack_id}")
        confined_file(directory, pack["producer_script"])
    declared = {pack["directory"] for pack in packs}
    present = {
        path.name for path in library.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    if declared != present:
        raise ValueError("catalog does not enumerate exactly the shipped benchmark directories")
    return packs


def preflight(directory: Path, pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Check every immutable artifact pin before launching any producer code."""

    case = load_case(confined_file(directory, "pvs.yaml"))
    pinned_paths = set()
    outputs = []
    for name, artifact in case.data["artifacts"].items():
        relative = artifact["path"]
        if artifact["role"] == "output":
            if not isinstance(relative, str) or PurePosixPath(relative).name != relative:
                raise ValueError(f"producer output must be a simple filename: {name}")
            if (directory / relative).exists():
                raise ValueError(f"producer output already exists in frozen pack: {relative}")
            outputs.append(relative)
            continue
        path = confined_file(directory, relative)
        expected = artifact.get("expected_sha256")
        if not expected or identity(path)["sha256"] != expected:
            raise ValueError(f"missing or mismatched pre-execution SHA-256 pin: {name}")
        pinned_paths.add(relative)
    if outputs != [pack["output"]]:
        raise ValueError("producer output differs from the single declared case output")
    if pack["producer_script"] not in pinned_paths:
        raise ValueError("reference producer must be pinned as an immutable artifact")
    return source_identities(directory)


def run_pack(root: Path, pack: dict[str, Any], output: Path) -> dict[str, Any]:
    pack_id = pack["id"]
    directory = root / "benchmark-packs" / pack_id
    retained = output / pack_id
    retained.mkdir()
    result: dict[str, Any] = {
        "id": pack_id,
        "status": "FAIL",
        "data_kinds": pack["data_kinds"],
        "scope": pack["scope"],
        "limitations": pack["limitations"],
        "pvs_execution_mode": "validate",
        "producer_execution_mode": "external-reference-producer",
    }
    producer: dict[str, Any] = {
        "execution_mode": "external-reference-producer",
        "interpreter": {"path": sys.executable, **identity(Path(sys.executable))},
        "stdout": "producer.stdout.txt",
        "stderr": "producer.stderr.txt",
    }
    try:
        frozen = preflight(directory, pack)
        result["source_files"] = frozen
        with tempfile.TemporaryDirectory(prefix="pvs-bp-") as temporary:
            copied = Path(temporary) / "case"
            shutil.copytree(
                directory, copied, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )
            if source_identities(copied) != frozen:
                raise ValueError("copied pack differs from the preflight source identity")
            case = load_case(copied / "pvs.yaml")
            execution = case.data["execution"]
            command = [sys.executable, *execution["command"][1:]]
            producer["command"] = command
            producer["working_directory"] = str(copied)
            producer["source_files"] = frozen
            producer["started_at"] = datetime.now(timezone.utc).isoformat()
            environment = os.environ.copy()
            environment.update(execution.get("environment", {}))
            # Avoid adding cache files to a frozen benchmark copy.
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            producer["explicit_environment"] = {
                **execution.get("environment", {}), "PYTHONDONTWRITEBYTECODE": "1"
            }
            started = time.perf_counter()
            with (
                (retained / "producer.stdout.txt").open("wb") as stdout,
                (retained / "producer.stderr.txt").open("wb") as stderr,
            ):
                completed = subprocess.run(
                    command,
                    cwd=copied,
                    env=environment,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=execution.get("timeout_seconds", 30),
                    check=False,
                )
            producer["duration_seconds"] = time.perf_counter() - started
            producer["finished_at"] = datetime.now(timezone.utc).isoformat()
            producer["return_code"] = completed.returncode
            if completed.returncode != 0:
                raise ValueError(f"reference producer exited with code {completed.returncode}")
            after = source_identities(copied)
            output_identity = after.pop(pack["output"], None)
            if after != frozen:
                raise ValueError("reference producer changed immutable pack files")
            if output_identity is None:
                raise ValueError("reference producer did not create the declared output")
            if source_identities(directory) != frozen:
                raise ValueError("shipped pack changed while the reference producer ran")
            producer["output"] = {"path": pack["output"], **output_identity}
            outcome = validate_case(
                copied / "pvs.yaml",
                output_dir=retained / "evidence",
                embed_artifacts=True,
                render_html_report=True,
                render_pdf_report=True,
            )
            verification = verify_target(outcome.output_directory)
            evidence = load_strict(outcome.evidence_path)
            checks = evidence["record"]["checks"]
            result.update(
                evidence_id=outcome.evidence_id,
                package_id=outcome.package_id,
                evidence="evidence/evidence.json",
                check_count=len(checks),
                passed_checks=sum(check["status"] == "PASS" for check in checks),
                verification_valid=verification.valid,
                evidence_identity=identity(outcome.evidence_path),
                manifest_identity=identity(outcome.manifest_path),
            )
            if (
                outcome.status.value != "PASS"
                or not verification.valid
                or len(checks) != pack["check_count"]
                or any(check["status"] != "PASS" for check in checks)
            ):
                raise ValueError("benchmark checks or evidence verification did not all pass")
            result["status"] = "PASS"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        producer["error"] = result["error"]
    finally:
        for name in ("producer.stdout.txt", "producer.stderr.txt"):
            path = retained / name
            if path.exists():
                producer[name] = identity(path)
        write_json(retained / "producer.json", producer)
        result["producer_record"] = "producer.json"
        result["producer_record_identity"] = identity(retained / "producer.json")
    return result


def run_benchmarks(output: Path, *, pack_id: str | None = None, root: Path = SOURCE_ROOT) -> int:
    output = output.resolve()
    if output.is_relative_to((root / "benchmark-packs").resolve()):
        raise ValueError("output must be outside the frozen benchmark library")
    output.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {
        "schema": "pvs-benchmark-run/1",
        "status": "FAIL",
        "pvs_version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "pvs_execution_mode": "validate",
        "producer_execution_mode": "external-reference-producer",
        "scope": "Checks of shipped reference producers; no external physics solver is executed.",
        "packs": [],
    }
    try:
        packs = load_catalog(root)
        summary["catalog_identity"] = identity(root / "benchmark-packs" / "catalog.json")
        summary["runner_identity"] = identity(Path(__file__))
        if pack_id is not None:
            packs = [pack for pack in packs if pack["id"] == pack_id]
            if not packs:
                raise ValueError(f"unknown benchmark pack: {pack_id}")
        summary["selected_packs"] = [pack["id"] for pack in packs]
        for pack in packs:
            result = run_pack(root, pack, output)
            summary["packs"].append(result)
            print(f"{result['status']}: {pack['id']}", flush=True)
        if all(pack["status"] == "PASS" for pack in summary["packs"]):
            summary["status"] = "PASS"
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
    write_json(output / "benchmark-summary.json", summary)
    return 0 if summary["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Fresh result directory")
    parser.add_argument("--pack", help="Run one catalog id; default: all shipped packs")
    arguments = parser.parse_args()
    try:
        return run_benchmarks(arguments.output, pack_id=arguments.pack)
    except (OSError, ValueError) as exc:
        print(f"benchmark runner failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
