"""Frozen packages must retain their generation's exact schema and report bytes."""

from __future__ import annotations

import copy
from pathlib import Path
from zipfile import ZipFile

import pytest
from contract_helpers import rehash_record

from pvs.jsonutil import load_strict
from pvs.verify import verify_target


@pytest.mark.parametrize(
    "filename",
    [
        "synthetic-json-evidence-v1.zip",
        "evidence-v2-0.2.8.zip",
        "synthetic-json-evidence-v3.zip",
    ],
)
def test_frozen_packages_verify_including_exact_html_and_pdf_replay(
    tmp_path: Path, filename: str,
) -> None:
    with ZipFile(Path(__file__).parent / "fixtures" / filename) as archive:
        archive.extractall(tmp_path)
    root = next(tmp_path.rglob("evidence.json")).parent
    result = verify_target(root)
    assert result.valid
    assert any("HTML" in c["name"] and c["status"] == "PASS" for c in result.checks)
    assert any("PDF" in c["name"] and c["status"] == "PASS" for c in result.checks)


@pytest.mark.parametrize("mutation", ["envelope", "producer", "profile"])
def test_generation_mixing_cannot_reinterpret_new_observations(
    package_factory,
    tmp_path: Path,
    mutation: str,
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=False)
    envelope = load_strict(package / "evidence.json")
    if mutation == "envelope":
        envelope["schema"] = "pvs-evidence/2"
    elif mutation == "producer":
        envelope["record"]["pvs"]["evidence_schema"] = "pvs-evidence/2"
    else:
        envelope["record"]["pvs"]["comparison_profile"] = "pvs-comparison/1"
    assert not verify_target(rehash_record(envelope, tmp_path / "mixed.json")).valid


def test_v2_literal_contradictions_are_rejected_without_changing_legacy_schema(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures/evidence-v2-0.2.8.zip"
    with ZipFile(fixture) as archive:
        archive.extractall(tmp_path / "v2")
    evidence_path = next((tmp_path / "v2").rglob("evidence.json"))
    original = load_strict(evidence_path)
    assert verify_target(evidence_path).valid
    for mutation in ("units", "scalar", "shapes", "subtraction"):
        envelope = copy.deepcopy(original)
        definition = envelope["record"]["case"]["resolved_definition"]
        index = next(
                i for i, check in enumerate(definition["checks"]) if check["type"] == "compare"
        )
        declared = definition["checks"][index]
        observed = envelope["record"]["checks"][index]["observed"]
        if mutation == "units":
            declared["actual"]["unit"] = "s"
        elif mutation == "scalar":
            observed["shape"] = [2]
        elif mutation == "shapes":
            declared["actual"] = {"value": [0, 0], "unit": declared["unit"]}
            declared["expected"] = {"value": [0], "unit": declared["unit"]}
        else:
            declared["actual"] = {"value": [1e308, 1e308], "unit": declared["unit"]}
            declared["expected"] = {"value": [-1e308, -1e308], "unit": declared["unit"]}
        assert not verify_target(rehash_record(envelope, tmp_path / f"{mutation}.json")).valid
