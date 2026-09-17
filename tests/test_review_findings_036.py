"""Public-API reproductions of the three 0.3.6 review findings and controls."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from conftest import base_case, dump_case

from pvs.api import validate_case
from pvs.jsonutil import load_strict
from pvs.models import Status
from pvs.verify import verify_target


@pytest.mark.parametrize("declared_dialect", [True, False])
def test_schema_dialect_is_enforced(tmp_path: Path, declared_dialect: bool) -> None:
    case = base_case()
    case["artifacts"]["schema"] = {
        "path": "schema.json", "role": "auxiliary", "format": "json",
    }
    case["checks"] = [{
        "id": "schema", "type": "schema", "artifact": "result", "schema_artifact": "schema",
    }]
    schema = {"type": "object", "dependencies": {"credit_card": ["billing_address"]}}
    if declared_dialect:
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    (tmp_path / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (tmp_path / "result.json").write_text('{"credit_card": "1234"}', encoding="utf-8")
    outcome = validate_case(dump_case(tmp_path / "pvs.yaml", case), output_dir=tmp_path / "out")
    assert outcome.status is (Status.ERROR if declared_dialect else Status.PASS)
    assert (outcome.finding_id is None) is declared_dialect
    check = load_strict(outcome.evidence_path)["record"]["checks"][0]
    if declared_dialect:
        assert "unsupported JSON Schema dialect" in check["summary"]
    else:
        assert check["criterion"]["dialect"] == "https://json-schema.org/draft/2020-12/schema"
    assert verify_target(outcome.output_directory).valid


def test_literal_ref_is_data(tmp_path: Path) -> None:
    case = base_case()
    case["artifacts"]["schema"] = {
        "path": "schema.json", "role": "auxiliary", "format": "json",
    }
    case["checks"] = [{
        "id": "schema", "type": "schema", "artifact": "result", "schema_artifact": "schema",
    }]
    value = {"$ref": "urn:example:dataset:1"}
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "const": value}
    (tmp_path / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (tmp_path / "result.json").write_text(json.dumps(value), encoding="utf-8")
    outcome = validate_case(dump_case(tmp_path / "pvs.yaml", case), output_dir=tmp_path / "out")
    assert outcome.status is Status.PASS
    assert outcome.finding_id is not None
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("underflow", ["ignore", "raise"])
def test_close_underflow_is_caller_independent(tmp_path: Path, underflow: str) -> None:
    case = base_case()
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    case["checks"] = [{
        "id": "close", "type": "compare", "metric": "close", "unit": "1",
        "actual": {"value": 1e-310, "unit": "1"},
        "expected": {"value": 1e-310, "unit": "1"},
        "absolute_tolerance": 0, "relative_tolerance": 1e-20,
    }]
    path = dump_case(tmp_path / "pvs.yaml", case)
    before = np.geterr()
    with np.errstate(all="raise", under=underflow):
        settings = np.geterr()
        outcome = validate_case(path, output_dir=tmp_path / "out")
        assert np.geterr() == settings
        assert outcome.status is Status.PASS
        assert verify_target(outcome.output_directory).valid
    assert np.geterr() == before
