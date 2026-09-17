"""Missing-input SKIPs are structured and bound to recorded artifact state."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import base_case, dump_case

import pvs.artifacts as artifacts_module
from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.errors import ArtifactError
from pvs.finding import create_finding_metadata
from pvs.jsonutil import load_strict, write_pretty
from pvs.models import Status
from pvs.verify import verify_target


def _optional_case(tmp_path: Path, artifact_format: str = "json") -> Path:
    case = base_case()
    case["artifacts"]["result"].update(required=False, format=artifact_format)
    source: dict[str, Any] = {"artifact": "result", "unit": "1"}
    if artifact_format == "netcdf":
        source.update(variable="value", netcdf_decoding="pvs-netcdf/1")
    elif artifact_format == "csv":
        source["column"] = "value"
    case["checks"] = [
        {"id": "optional", "type": "finite", "required": False, "unit": "1", "source": source}
    ]
    return dump_case(tmp_path / "case/pvs.yaml", case)


@pytest.mark.parametrize("artifact_format", ["json", "csv", "netcdf"])
def test_absent_optional_input_still_publishes_a_verifiable_skip(
    tmp_path: Path, artifact_format: str
) -> None:
    case_path = _optional_case(tmp_path, artifact_format)
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status is Status.SKIP
    assert outcome.finding_id is not None
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("failure", ["malformed", "directory", "snapshot"])
def test_existing_invalid_optional_input_is_error(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    case_path = _optional_case(tmp_path)
    source = case_path.parent / "result.json"
    if failure == "directory":
        source.mkdir()
    else:
        source.write_text('{"does not exist": 1, "does not exist": 2}', encoding="utf-8")
    if failure == "snapshot":
        def fail_snapshot(*args: Any, **kwargs: Any) -> Any:
            raise ArtifactError("injected snapshot error: does not exist")

        monkeypatch.setattr(artifacts_module, "snapshot_file", fail_snapshot)
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status is Status.ERROR
    assert outcome.finding_id is None
    assert load_strict(outcome.evidence_path)["record"]["checks"][0]["status"] == "ERROR"
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("forgery", ["present", "unrelated", "resolution_error", "literal"])
def test_rehashed_skip_requires_the_check_input_to_be_absent(
    tmp_path: Path, forgery: str
) -> None:
    case_path = _optional_case(tmp_path)
    if forgery == "unrelated":
        # Keep the same optional check while adding a separate absent artifact.
        case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
        case["artifacts"]["unrelated"] = {
            "path": "unrelated.json", "role": "output", "format": "json", "required": False,
        }
        dump_case(case_path, case)
    if forgery in {"present", "unrelated", "literal"}:
        (case_path.parent / "result.json").write_text("[1.0]\n", encoding="utf-8")
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    envelope = load_strict(outcome.evidence_path)
    record = envelope["record"]
    definition = record["case"]["resolved_definition"]
    result = record["checks"][0]
    result.update(status="SKIP", observed={}, criterion={})
    record["summary"]["status"] = "SKIP"
    record["summary"]["counts"] = {name: int(name == "SKIP") for name in Status.__members__}
    if forgery == "resolution_error":
        record["artifacts"][0]["validation_input"]["resolution_error"] = "could not read"
        record["summary"].update(status="ERROR", provenance_status="INCOMPLETE")
    elif forgery == "literal":
        definition["checks"][0] = {
            "id": "optional", "type": "compare", "required": False, "unit": "1",
            "metric": "absolute", "tolerance": 0.0,
            "actual": {"value": 1.0, "unit": "1"},
            "expected": {"value": 1.0, "unit": "1"},
        }
        result["type"] = "compare"
    record["case"]["semantic_sha256"] = canonical_sha256(definition)
    envelope["integrity"]["finding"] = create_finding_metadata(record)
    digest = canonical_sha256(record)
    envelope["integrity"].update(digest=digest, evidence_id=f"pvs:sha256:{digest}")
    target = tmp_path / "forged-evidence.json"
    write_pretty(target, envelope)
    verification = verify_target(target)
    assert not verification.valid
    assert next(
        item["status"] for item in verification.checks
        if item["name"] == "check missing-input SKIP contract"
    ) == "FAIL"
