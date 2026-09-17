"""Reconstructed 0.2.9 review cases: rehashed contradictions and valid controls.

The review described these five cases but did not attach its test file. These
public-API tests deliberately change identities; they test internal consistency.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.finding import create_finding_metadata
from pvs.jsonutil import load_strict, write_pretty
from pvs.models import Status
from pvs.verify import verify_target


def produce_artifact_comparison(tmp_path: Path, values: list[float]) -> dict[str, Any]:
    case = {
        "schema": "pvs-case/2", "id": "review-norms", "version": "1.0.0",
        "title": "Artifact norm consistency", "classifications": ["verification"],
        "subject": {"name": "synthetic", "version": "1"},
        "artifacts": {"data": {"path": "data.json", "role": "output", "format": "json"}},
        "checks": [{
            "id": "norms", "type": "compare", "unit": "1", "metric": "linf",
            "tolerance": max(values),
            "actual": {"artifact": "data", "pointer": "/actual", "unit": "1"},
            "expected": {"artifact": "data", "pointer": "/expected", "unit": "1"},
        }],
        "package": {"embed_artifacts": False, "html": False, "pdf": False},
    }
    (tmp_path / "data.json").write_text(json.dumps({
        "actual": values, "expected": [0.0] * len(values),
    }), encoding="utf-8")
    case_path = tmp_path / "pvs.yaml"
    case_path.write_text(yaml.safe_dump(case), encoding="utf-8")
    outcome = validate_case(case_path, output_dir=tmp_path / "package")
    assert outcome.status is Status.PASS
    assert outcome.finding_id is not None
    assert verify_target(outcome.output_directory).valid
    return load_strict(outcome.evidence_path)


def rehash(envelope: dict[str, Any], target: Path) -> Path:
    record = envelope["record"]
    envelope["integrity"]["finding"] = create_finding_metadata(record)
    digest = canonical_sha256(record)
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = f"pvs:sha256:{digest}"
    write_pretty(target, envelope)
    return target


@pytest.mark.parametrize(("maximum", "claimed"), [
    (0.0, {"status": "AVAILABLE", "value": 1.0}),
    (1.0, {"status": "AVAILABLE", "value": 100.0}),
    (1e308, {"status": "UNAVAILABLE", "reason": "OVERFLOW"}),
], ids=["zero-maximum", "excessive-norms", "impossible-l2-overflow"])
def test_rejects_mutually_impossible_norms(
    tmp_path: Path, maximum: float, claimed: dict[str, Any],
) -> None:
    envelope = produce_artifact_comparison(tmp_path, [maximum, maximum])
    original_id = envelope["integrity"]["evidence_id"]
    observation = envelope["record"]["checks"][0]["observed"]
    observation["l1_error"] = dict(claimed)
    observation["l2_error"] = dict(claimed)
    path = rehash(envelope, tmp_path / "rehashed.json")
    assert envelope["integrity"]["evidence_id"] != original_id
    result = verify_target(path)
    assert not result.valid, "Verifier accepted internally impossible artifact norms"


@pytest.mark.parametrize("values", [[3.0, 4.0], [1e308, 1e308]])
def test_valid_artifact_norms_remain_accepted(tmp_path: Path, values: list[float]) -> None:
    envelope = produce_artifact_comparison(tmp_path, values)
    observation = envelope["record"]["checks"][0]["observed"]
    if values[0] == 1e308:
        assert observation["l1_error"] == {"status": "UNAVAILABLE", "reason": "OVERFLOW"}
        assert observation["l2_error"]["status"] == "AVAILABLE"
    assert verify_target(rehash(envelope, tmp_path / "rehashed-control.json")).valid
