"""Generate NEW synthetic compatibility packages; never emulate an old producer.

Run from the source root with its development dependencies installed:
    python tests/fixtures/generate_synthetic_compatibility.py --output /tmp/fixtures

The v3 package is ordinary current-producer output. The v1 package is explicitly
assembled from that new synthetic record, with v1 observations and frozen v1
renderers. Its producer version suffix and case metadata identify the conversion;
it is not an assertion that an earlier PVS release ran. New runs have new actual
timestamps and IDs. Exact report replay of each frozen package is tested normally.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import yaml

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.case import load_case
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict, write_pretty
from pvs.package import create_manifest
from pvs.reporting import render_html_v1, render_pdf_v1
from pvs.verify import verify_target
from pvs.version import __version__


def without_units(value: Any) -> Any:
    """Remove the modern units metadata absent from this v1 test scenario."""
    if isinstance(value, dict):
        return {key: without_units(item) for key, item in value.items() if key != "unit"}
    if isinstance(value, list):
        return [without_units(item) for item in value]
    return value


def assemble_v1(modern: Path, target: Path) -> None:
    """Project one known finite synthetic case into the documented v1 format."""
    shutil.copytree(modern, target)
    envelope = copy.deepcopy(load_strict(target / "evidence.json"))
    record = envelope["record"]
    envelope["schema"] = "pvs-evidence/1"
    record.pop("package")
    producer = record["pvs"]
    for key in ("content_identity", "build", "comparison_profile"):
        producer.pop(key)
    producer.update(version=f"{__version__}+synthetic-v1", case_schema="pvs-case/1",
                    evidence_schema="pvs-evidence/1")
    definition = without_units(record["case"]["resolved_definition"])
    definition["schema"] = "pvs-case/1"
    definition["metadata"]["fixture_assembly"] = (
        "New synthetic v1-format projection by generate_synthetic_compatibility.py; "
        f"underlying validation and environment from PVS {__version__}, "
        "not output of a historical release."
    )
    case_path = target / "case/pvs.yaml"
    case_path.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
    legacy_case = load_case(case_path, allow_legacy=True)
    record["case"].update(resolved_definition=legacy_case.data,
                          semantic_sha256=legacy_case.semantic_sha256,
                          raw_sha256=legacy_case.raw_identity["sha256"],
                          raw_size_bytes=legacy_case.raw_identity["size_bytes"])
    record["checks"] = without_units(record["checks"])
    for result in record["checks"]:
        for key, value in list(result["observed"].items()):
            if isinstance(value, dict) and value.get("status") == "AVAILABLE":
                result["observed"][key] = value["value"]
    envelope["integrity"].pop("finding")
    digest = canonical_sha256(record)
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    write_pretty(target / "evidence.json", envelope)
    render_html_v1(envelope, target / "report.html")
    render_pdf_v1(envelope, target / "report.pdf")
    manifest = create_manifest(target, envelope["integrity"]["evidence_id"],
                               record["execution"]["finished_at"])
    manifest["schema"] = "pvs-manifest/1"
    manifest["package"].pop("policy")
    manifest["package"].pop("report_profiles")
    digest = canonical_sha256(manifest["package"])
    manifest["integrity"].update(digest=digest, package_id=f"pvs-package:sha256:{digest}")
    write_pretty(target / "manifest.json", manifest)


def archive_package(folder: Path, target: Path) -> None:
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                item = ZipInfo(f"{folder.name}/{path.relative_to(folder).as_posix()}")
                item.compress_type = ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                archive.writestr(item, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="pvs-synthetic-compatibility-") as temporary:
        root = Path(temporary)
        case = root / "input-case"
        shutil.copytree(script.parent / "synthetic-compatibility", case)
        shutil.copyfile(script, case / script.name)
        definition = yaml.safe_load((case / "pvs.yaml").read_text(encoding="utf-8"))
        definition["artifacts"]["fixture-generator"] = {
            "path": script.name, "role": "subject", "format": "text",
            "expected_sha256": file_identity(script)["sha256"],
        }
        definition["artifacts"]["expected"]["expected_sha256"] = (
            file_identity(case / "expected.json")["sha256"]
        )
        (case / "pvs.yaml").write_text(yaml.safe_dump(definition, sort_keys=False),
                                      encoding="utf-8")
        modern = root / "synthetic-json-evidence-v3"
        outcome = validate_case(case, output_dir=modern)
        if outcome.status.value != "PASS":
            raise RuntimeError(f"synthetic validation failed: {outcome.status}")
        legacy = root / "synthetic-json-evidence-v1"
        assemble_v1(modern, legacy)
        inventory = []
        for package in (legacy, modern):
            verification = verify_target(package)
            if not verification.valid:
                raise RuntimeError(json.dumps(verification.checks, indent=2))
            target = args.output / f"{package.name}.zip"
            archive_package(package, target)
            inventory.append({"path": target.name, **file_identity(target),
                              "evidence_id": verification.evidence_id,
                              "package_id": verification.package_id})
        write_pretty(args.output / "synthetic-compatibility-identities.json", {
            "scope": "New synthetic fixtures; no historical-release provenance claim.",
            "generator": file_identity(script), "pvs_version": __version__,
            "fixtures": inventory,
        })


if __name__ == "__main__":
    main()
