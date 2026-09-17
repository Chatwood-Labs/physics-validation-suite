"""Selectors must be valid before structural or numeric reductions run."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import base_case, dump_case

from pvs.api import validate_case
from pvs.artifacts import ResolvedArtifact
from pvs.errors import ArtifactError
from pvs.jsonutil import load_strict
from pvs.models import Status
from pvs.readers.registry import ArtifactReader
from pvs.verify import verify_target


@pytest.mark.parametrize("rows", ["", "12\n"], ids=["header-only", "nonempty"])
@pytest.mark.parametrize("reduction", [None, "size", "first", "sum"])
@pytest.mark.parametrize("required", [True, False], ids=["required", "optional"])
@pytest.mark.parametrize("operand", ["actual", "expected"])
def test_missing_csv_column_publishes_verified_error(
    tmp_path: Path, rows: str, reduction: str | None, required: bool, operand: str
) -> None:
    case = base_case()
    case["artifacts"]["result"].update(path="result.csv", format="csv")
    source = {"artifact": "result", "column": "pressure", "unit": "1"}
    if reduction is not None:
        source["reduce"] = reduction
    check = {
        "id": "column-check", "type": "compare", "required": required,
        "metric": "absolute", "tolerance": 0.0, "unit": "1",
        "actual": {"value": 0, "unit": "1"},
        "expected": {"value": 0, "unit": "1"},
    }
    check[operand] = source
    case["checks"] = [check]
    case["package"]["embed_artifacts"] = True
    path = dump_case(tmp_path / "case/pvs.yaml", case)
    (path.parent / "result.csv").write_text("temperature\n" + rows, encoding="utf-8")

    outcome = validate_case(path, output_dir=tmp_path / "evidence")

    assert outcome.status is Status.ERROR
    assert outcome.finding_id is None
    record = load_strict(outcome.evidence_path)["record"]
    assert record["checks"][0]["summary"] == "CSV column not found: pressure"
    assert record["checks"][0]["observed"] == {}
    assert record["checks"][0]["criterion"] == {}
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("reduction", [None, "size", "first", "last", "sum", "mean", "min", "max"])
def test_existing_empty_csv_column_preserves_structural_count(
    tmp_path: Path, reduction: str | None
) -> None:
    case = base_case()
    case["artifacts"]["result"].update(path="result.csv", format="csv")
    source = {"artifact": "result", "column": "temperature", "unit": "1"}
    if reduction is not None:
        source["reduce"] = reduction
    case["checks"] = [{
        "id": "empty-column", "type": "compare", "metric": "absolute",
        "unit": "1", "tolerance": 0.0, "actual": source,
        "expected": {"value": 0, "unit": "1"},
    }]
    path = dump_case(tmp_path / "case/pvs.yaml", case)
    (path.parent / "result.csv").write_text("temperature\n", encoding="utf-8")

    outcome = validate_case(path, output_dir=tmp_path / "evidence")

    assert outcome.status is (Status.PASS if reduction == "size" else Status.ERROR)
    assert (outcome.finding_id is not None) is (reduction == "size")
    if reduction == "size":
        assert load_strict(outcome.evidence_path)["record"]["checks"][0]["observed"]["actual"] == 0
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("preload", [True, False])
def test_headers_are_cached_with_the_same_rows_and_separated_by_artifact(
    tmp_path: Path, preload: bool
) -> None:
    artifacts = {}
    for name, content in (("a", "temperature\n"), ("b", "pressure\n12\n")):
        (tmp_path / f"{name}.csv").write_text(content, encoding="utf-8")
        artifacts[name] = ResolvedArtifact(
            id=name, root=tmp_path,
            declaration={"path": f"{name}.csv", "role": "output", "format": "csv"},
        )
    reader = ArtifactReader(artifacts)
    if preload:
        assert reader.read("a") == []
        # A cached read must retain both header and row identity after a path changes.
        (tmp_path / "a.csv").write_text("pressure\n12\n", encoding="utf-8")
    assert reader.select({"artifact": "b", "column": "pressure", "reduce": "size"}) == 1
    with pytest.raises(ArtifactError, match="CSV column not found: pressure"):
        reader.select({"artifact": "a", "column": "pressure", "reduce": "size"})
    assert reader.select({"artifact": "a", "column": "temperature", "reduce": "size"}) == 0


@pytest.mark.parametrize("header", ["temperature", "温度", '"temperature, K"', '" temperature "'])
def test_header_only_csv_uses_exact_decoded_header_names(tmp_path: Path, header: str) -> None:
    import csv

    (tmp_path / "data.csv").write_text(header + "\n", encoding="utf-8")
    artifact = ResolvedArtifact(
        id="data", root=tmp_path,
        declaration={"path": "data.csv", "role": "output", "format": "csv"},
    )
    reader = ArtifactReader({"data": artifact})
    name = next(csv.reader([header]))[0]
    assert reader.select({"artifact": "data", "column": name, "reduce": "size"}) == 0
    with pytest.raises(ArtifactError, match="CSV column not found"):
        reader.select({"artifact": "data", "column": name + "-absent", "reduce": "size"})


@pytest.mark.parametrize("content", ['temperature\n"1', 'temperature\n"1"junk\n', '"temperature\n'])
@pytest.mark.parametrize("reduction", ["first", "size"])
def test_strict_dialect_rejects_broken_quotes_before_any_reduction(
    tmp_path: Path, content: str, reduction: str,
) -> None:
    path = tmp_path / "data.csv"
    path.write_text(content, encoding="utf-8")
    artifact = ResolvedArtifact(id="data", root=tmp_path, declaration={
        "path": "data.csv", "format": "csv", "role": "output",
    })
    reader = ArtifactReader({"data": artifact})
    with pytest.raises(ArtifactError, match="could not read artifact data"):
        reader.select({"artifact": "data", "column": "temperature", "reduce": reduction})


@pytest.mark.parametrize(("content", "column", "expected"), [
    ('temperature\r\n"1"\r\n', "temperature", 1),
    ('"temperature, K"\n"1"\n', "temperature, K", 1),
    ('"temperature""sensor"\n"1"\n', 'temperature"sensor', 1),
    ('temperature,notes\n1,"first\nsecond"\n', "temperature", 1),
    ('temperature,notes\n1,"uses ""quotes"""\n', "temperature", 1),
])
def test_explicit_csv_dialect_preserves_valid_quoting(
    tmp_path: Path, content: str, column: str, expected: int,
) -> None:
    (tmp_path / "data.csv").write_bytes(content.encode("utf-8"))
    artifact = ResolvedArtifact(id="data", root=tmp_path, declaration={
        "path": "data.csv", "format": "csv", "role": "output",
    })
    reader = ArtifactReader({"data": artifact})
    assert reader.select({"artifact": "data", "column": column, "reduce": "first"}) == expected
