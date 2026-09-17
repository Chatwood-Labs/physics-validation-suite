"""Inspection binds every displayed field to the acquired verification record."""

from __future__ import annotations

import copy
import json

import pytest

import pvs.cli as cli_module
import pvs.verify as verify_module
from pvs.jsonutil import load_strict, write_pretty


@pytest.mark.parametrize("package_target", [False, True], ids=["record", "package"])
@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_inspect_never_rereads_the_original_after_verification(
    package_factory, monkeypatch, capsys, package_target, mutation
) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    original = load_strict(outcome.evidence_path)
    target = package if package_target else outcome.evidence_path
    verify_value = verify_module._verify_evidence_value
    altered = copy.deepcopy(original)
    altered["record"]["subject"]["name"] = "unverified replacement"
    verified_records = []

    def verify_then_mutate(envelope, checks):
        result = verify_value(envelope, checks)
        verified_records.append(result[2])
        if mutation == "delete":
            outcome.evidence_path.unlink()
        else:
            write_pretty(outcome.evidence_path, altered)
        return result

    monkeypatch.setattr(verify_module, "_verify_evidence_value", verify_then_mutate)
    assert cli_module.main(["inspect", str(target), "--json"]) == 0
    display = json.loads(capsys.readouterr().out)
    assert len(verified_records) == 1
    assert display["subject"] == verified_records[0]["subject"] == original["record"]["subject"]
    assert display["evidence_id"] == outcome.evidence_id
    assert display["finding_id"] == outcome.finding_id
    assert display["integrity_valid"] is True
    assert display["trust"] == "unpinned"


@pytest.mark.parametrize("package_target", [False, True], ids=["record", "package"])
@pytest.mark.parametrize("json_output", [False, True], ids=["human", "json"])
def test_inspect_rejects_stable_invalid_content_without_displaying_it(
    package_factory, capsys, package_target, json_output
) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    altered = load_strict(outcome.evidence_path)
    altered["record"]["subject"]["name"] = "FORGED SUBJECT NOT VERIFIED"
    write_pretty(outcome.evidence_path, altered)
    target = package if package_target else outcome.evidence_path
    args = ["inspect", str(target)] + (["--json"] if json_output else [])
    assert cli_module.main(args) == 4
    output = capsys.readouterr()
    assert "FORGED" not in output.out + output.err
    if json_output:
        assert json.loads(output.out)["kind"] == "integrity"
        assert output.err == ""
    else:
        assert output.out == ""
        assert "failed integrity" in output.err


@pytest.mark.parametrize("package_target", [False, True], ids=["record", "package"])
def test_verified_record_is_the_parsed_object_and_not_public_json(
    package_factory, monkeypatch, package_target
) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)
    target = package if package_target else outcome.evidence_path
    verify_value = verify_module._verify_evidence_value
    records = []

    def track_verified_object(envelope, checks):
        result = verify_value(envelope, checks)
        records.append(result[2])
        return result

    monkeypatch.setattr(verify_module, "_verify_evidence_value", track_verified_object)
    acquired = verify_module._verify_target(target, expect_evidence_id=outcome.evidence_id)
    assert acquired.verification.valid
    assert len(records) == 1
    assert acquired.record is records[0]
    assert acquired.verification.trust == "record-pinned"
    assert acquired.verification.target == target.resolve()
    assert "record" not in acquired.verification.as_dict()
    assert acquired.verification.as_dict() == verify_module.verify_target(
        target, expect_evidence_id=outcome.evidence_id
    ).as_dict()
    mismatched = verify_module._verify_target(target, expect_evidence_id="pvs:sha256:" + "0" * 64)
    assert not mismatched.verification.valid
    assert mismatched.record is None


def test_inspection_requires_package_integrity_even_when_record_is_valid(
    package_factory, capsys
) -> None:
    outcome, package = package_factory(html=True, pdf=False, embed=False)
    (package / "report.html").write_text("altered report", encoding="utf-8")
    assert verify_module.verify_target(outcome.evidence_path).valid
    result = verify_module._verify_target(package)
    assert not result.verification.valid
    assert result.record is None
    assert cli_module.main(["inspect", str(package), "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["kind"] == "integrity"
