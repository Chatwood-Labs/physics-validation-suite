"""Machine-readable qualification facts; stage outcomes come from the harness."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            json.dump(value, file, indent=2, sort_keys=True, allow_nan=False)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interpreter() -> dict[str, str]:
    return {
        "executable": str(Path(sys.executable).absolute()),
        "base_executable": str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": sys.platform,
        "machine": platform.machine(),
    }


def environment(profile: str, lock: Path) -> dict[str, Any]:
    # Enumerate actual installed versions, independently of the requested lock.
    versions = {
        (re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower(), distribution.version)
        for distribution in importlib.metadata.distributions()
    }
    # Editable installs can expose the same metadata through more than one path.
    packages = [{"name": name, "version": version} for name, version in sorted(versions)]
    encoded = json.dumps(packages, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "profile": profile,
        "interpreter": interpreter(),
        "packages": packages,
        "dependency_versions_id": "pvs-dependency-versions:sha256:"
        + hashlib.sha256(encoded).hexdigest(),
        "lock_sha256": digest(lock),
    }


def test_report(path: Path) -> dict[str, Any]:
    tree = ET.parse(path)
    cases = list(tree.getroot().iter("testcase"))
    failures = sum(case.find("failure") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    skips = []
    capabilities: dict[str, Counter[str]] = {}
    for case in cases:
        skip = case.find("skipped")
        outcome = (
            "skipped"
            if skip is not None
            else "failed"
            if case.find("failure") is not None
            else "error"
            if case.find("error") is not None
            else "passed"
        )
        if skip is not None:
            skips.append(
                {
                    "test": f"{case.get('classname', '')}::{case.get('name', '')}",
                    "reason": skip.get("message") or skip.text or "unspecified",
                }
            )
        for prop in case.findall("./properties/property"):
            if prop.get("name") == "pvs_capability":
                capabilities.setdefault(str(prop.get("value")), Counter())[outcome] += 1
    reasons = Counter(skip["reason"] for skip in skips)
    return {
        "status": "FAILED" if failures or errors or not cases else "PASSED",
        "tests": len(cases),
        "passed": len(cases) - failures - errors - len(skips),
        "failed": failures,
        "errors": errors,
        "skipped": len(skips),
        "skip_reasons": [
            {"reason": reason, "count": count} for reason, count in sorted(reasons.items())
        ],
        "skipped_tests": skips,
        "capabilities": {name: dict(counts) for name, counts in sorted(capabilities.items())},
        "junit_file": path.name,
        "junit_sha256": digest(path),
    }


def projection_diff(left: Any, right: Any, pointer: str = "") -> list[dict[str, Any]]:
    from pvs.canonical import canonical_bytes

    if canonical_bytes(left) == canonical_bytes(right):
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        differences = []
        for key in sorted(left.keys() | right.keys()):
            child = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in left or key not in right:
                differences.append(
                    {
                        "path": child,
                        "left_present": key in left,
                        "right_present": key in right,
                        "left": left.get(key),
                        "right": right.get(key),
                    }
                )
            else:
                differences.extend(projection_diff(left[key], right[key], child))
        return differences
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [
            difference
            for i, (a, b) in enumerate(zip(left, right, strict=True))
            for difference in projection_diff(a, b, f"{pointer}/{i}")
        ]
    result = {"path": pointer, "left": left, "right": right}
    for name, value in (("left", left), ("right", right)):
        if type(value) is float:
            result[name + "_binary64"] = value.hex()
    return [result]


def compare_findings(left: Path, right: Path) -> dict[str, Any]:
    from pvs.finding import finding_projection
    from pvs.jsonutil import load_strict
    from pvs.verify import verify_target

    records = []
    for path in (left, right):
        # The evidence record is sufficient for the projection. Package bytes
        # and deterministic reports are checked separately by acceptance.
        verification = verify_target(path)
        if not verification.valid:
            raise ValueError(f"Finding comparison requires valid evidence: {path}")
        record = load_strict(path)
        if record["integrity"]["finding"].get("finding_id") is None:
            raise ValueError(f"Finding comparison requires an eligible Finding: {path}")
        records.append(record)
    projections = [finding_projection(value["record"]) for value in records]
    differences = projection_diff(*projections)
    return {
        "schema": "pvs-finding-diff/1",
        "status": "DIFFERENT" if differences else "IDENTICAL",
        "left_finding_id": records[0]["integrity"]["finding"]["finding_id"],
        "right_finding_id": records[1]["integrity"]["finding"]["finding_id"],
        "left_evidence_sha256": digest(left),
        "right_evidence_sha256": digest(right),
        "differences": differences,
    }


def finalize(run_root: Path, release_root: Path, status: str, mode: str) -> dict[str, Any]:
    stages = []
    journal = run_root / "acceptance-stages.tsv"
    if journal.exists():
        for line in journal.read_text(encoding="utf-8").splitlines():
            started, finished, outcome, name = line.split("\t", 3)
            stages.append(
                {"name": name, "status": outcome, "started_utc": started, "finished_utc": finished}
            )
    result: dict[str, Any] = {
        "schema": "pvs-acceptance-summary/1",
        "status": status,
        "mode": mode,
        "host": {"platform": sys.platform, "machine": platform.machine()},
        "stages": stages,
        "environments": [],
        "test_runs": [],
        "problems": [],
        "subject_execution": "POSIX_ONLY_NOT_RUN"
        if sys.platform == "win32"
        else "NOT_RUN_QUICK_MODE"
        if mode == "quick"
        else "TESTED"
        if status == "PASSED"
        else "INCOMPLETE",
    }
    selected = run_root / "selected-interpreter.json"
    result["selected_interpreter"] = json.loads(selected.read_text()) if selected.exists() else None
    for profile in ("runtime-all", "ordinary-dev-all", "frozen-dev-all"):
        path = run_root / (profile + ".json")
        if path.exists():
            snapshot = json.loads(path.read_text())
            result["environments"].append(snapshot)
            if result["selected_interpreter"] is not None:
                expected = result["selected_interpreter"]
                actual = snapshot["interpreter"]
                if any(actual[key] != expected[key] for key in ("version", "implementation")) or (
                    os.path.normcase(actual["base_executable"])
                    != os.path.normcase(expected["base_executable"])
                ):
                    result["problems"].append(f"{profile}: interpreter differs from selected base")
        elif status == "PASSED" and (mode == "full" or profile == "runtime-all"):
            result["problems"].append(f"missing environment snapshot: {profile}")
    for profile in ("ordinary", "frozen"):
        path = run_root / (profile + "-pytest.xml")
        if path.exists():
            report = test_report(path)
            result["test_runs"].append({"profile": profile, **report})
            if report["status"] != "PASSED":
                result["problems"].append(f"{profile}: test failures or empty suite")
        else:
            result["test_runs"].append({"profile": profile, "status": "NOT_RUN"})
            if status == "PASSED" and mode == "full":
                result["problems"].append(f"missing test report: {profile}")
    metadata = release_root / "RELEASE_METADATA.json"
    result["release"] = json.loads(metadata.read_text()) if metadata.exists() else None
    result["artifact_identities"] = []
    sums = release_root / "SHA256SUMS"
    if sums.exists():
        for line in sums.read_text().splitlines():
            expected_digest, name = line.split(maxsplit=1)
            # Release membership has already been checked by the harness. Keep
            # summary acquisition confined even on an early failed invocation.
            if Path(name).name != name or "\\" in name or name in {".", ".."}:
                raise ValueError("invalid checksum member in acceptance summary")
            path = release_root / name
            actual_digest = digest(path) if path.is_file() else None
            result["artifact_identities"].append(
                {
                    "file": name,
                    "sha256": actual_digest,
                    "matches_checksum": actual_digest == expected_digest,
                }
            )
            if actual_digest != expected_digest:
                result["problems"].append(f"release checksum mismatch: {name}")
    elif status == "PASSED":
        result["problems"].append("missing release checksums")
    diff = run_root / "plasma-finding-diff.json"
    result["finding_comparison"] = json.loads(diff.read_text()) if diff.exists() else None
    if status == "PASSED":
        if not stages or any(stage["status"] != "PASSED" for stage in stages):
            result["problems"].append("missing or failed stage outcomes")
        if mode == "full" and not diff.exists():
            result["problems"].append("missing plasma Finding comparison")
        if result["selected_interpreter"] is None or result["release"] is None:
            result["problems"].append("missing interpreter or release identity")
    if result["problems"]:
        result["status"] = "FAILED"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    env = commands.add_parser("environment")
    env.add_argument("--profile", required=True)
    env.add_argument("--lock", type=Path, required=True)
    env.add_argument("--output", type=Path, required=True)
    diff = commands.add_parser("finding-diff")
    diff.add_argument("left", type=Path)
    diff.add_argument("right", type=Path)
    diff.add_argument("--output", type=Path, required=True)
    finish = commands.add_parser("finalize")
    finish.add_argument("--run-root", type=Path, required=True)
    finish.add_argument("--release-root", type=Path, required=True)
    finish.add_argument("--status", choices=("PASSED", "FAILED"), required=True)
    finish.add_argument("--mode", choices=("full", "quick"), required=True)
    finish.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "environment":
        result = environment(args.profile, args.lock)
    elif args.command == "finding-diff":
        result = compare_findings(args.left, args.right)
        print(
            f"Finding projections: {result['status']}; "
            f"{len(result['differences'])} exact field differences"
        )
        for difference in result["differences"][:10]:
            print(json.dumps(difference, sort_keys=True))
    else:
        result = finalize(args.run_root, args.release_root, args.status, args.mode)
    write_json(args.output, result)
    print(f"Qualification facts: {args.output}")
    return 1 if result.get("status") == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
