"""Schema positions, dialect declarations and the no-retrieval boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pvs.checks.engine as engine
from pvs.artifacts import ResolvedArtifact
from pvs.checks.engine import evaluate_checks
from pvs.models import Status

DIALECT = "https://json-schema.org/draft/2020-12/schema"
SINGLE = [
    "additionalProperties", "unevaluatedProperties", "propertyNames", "items",
    "unevaluatedItems", "contains", "not", "if", "then", "else", "contentSchema",
]
ARRAY = ["prefixItems", "allOf", "anyOf", "oneOf"]
MAPPING = ["$defs", "definitions", "properties", "patternProperties", "dependentSchemas"]


def evaluate(tmp_path, schema, data=None):
    artifacts = {}
    for name, value in (("schema", schema), ("data", data)):
        (tmp_path / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
        artifacts[name] = ResolvedArtifact(
            id=name, root=tmp_path,
            declaration={"path": f"{name}.json", "format": "json", "role": "auxiliary"},
        )
    return evaluate_checks([{
        "id": "schema", "type": "schema", "artifact": "data", "schema_artifact": "schema",
    }], artifacts)[0]


@pytest.mark.parametrize("position", ["root", *SINGLE, *ARRAY, *MAPPING])
@pytest.mark.parametrize("rule", [
    {"$schema": "http://json-schema.org/draft-07/schema#"},
    {"$ref": "urn:example:external"},
])
def test_every_schema_position_is_checked(tmp_path: Path, position, rule) -> None:
    if position == "root":
        schema = rule
    elif position in SINGLE:
        schema = {position: rule}
    elif position in ARRAY:
        schema = {position: [rule]}
    else:
        schema = {position: {"entry": rule}}
    result = evaluate(tmp_path, schema)
    assert result.status is Status.ERROR
    assert ("unsupported JSON Schema dialect" if "$schema" in rule else
            "remote or external JSON Schema $ref is forbidden") in result.summary


@pytest.mark.parametrize("position", ["const", "enum", "default", "examples", "x-annotation"])
@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef", "$schema"])
def test_literal_keywords_are_never_schema_instructions(tmp_path, position, keyword) -> None:
    value = {keyword: "urn:example:literal", "nested": [{"$ref": "file:///data"}]}
    schema = {"$schema": DIALECT, position: [value] if position in {"enum", "examples"} else value}
    result = evaluate(tmp_path, schema, value)
    assert result.status is Status.PASS
    if position in {"const", "enum"}:
        assert evaluate(tmp_path, schema, {}).status is Status.FAIL


@pytest.mark.parametrize("dialect", [DIALECT, DIALECT + "#", None])
def test_embedded_resources_share_the_fixed_dialect(tmp_path, dialect) -> None:
    child = {
        "$id": "https://example.test/child",
        "$defs": {"value": {"type": "integer"}}, "$ref": "#/$defs/value",
    }
    if dialect is not None:
        child["$schema"] = dialect
    schema = {"$schema": DIALECT, "$defs": {"child": child}, "$ref": "#/$defs/child"}
    assert evaluate(tmp_path, schema, 3).status is Status.PASS
    assert evaluate(tmp_path, schema, "3").status is Status.FAIL


@pytest.mark.parametrize("dialect", [
    "http://json-schema.org/draft-07/schema#", "https://json-schema.org/draft/2019-09/schema",
    "urn:example:custom-dialect", "http://json-schema.org/draft/2020-12/schema",
])
def test_unused_embedded_dialect_is_rejected(tmp_path, dialect) -> None:
    schema = {"$defs": {"unused": {"$id": "https://example.test/unused", "$schema": dialect}}}
    assert evaluate(tmp_path, schema).status is Status.ERROR


@pytest.mark.parametrize("reference", ["#/$defs/a~1b~0c", "#/%24defs/a~1b~0c", "#local"])
def test_local_pointer_and_anchor_controls(tmp_path, reference) -> None:
    schema = {"$defs": {"a/b~c": {"$anchor": "local", "type": "integer"}}, "$ref": reference}
    assert evaluate(tmp_path, schema, 5).status is Status.PASS
    assert evaluate(tmp_path, schema, "5").status is Status.FAIL


def test_dynamic_recursive_local_schema(tmp_path) -> None:
    schema = {
        "$dynamicAnchor": "node", "type": "object",
        "properties": {"child": {"$dynamicRef": "#node"}},
    }
    assert evaluate(tmp_path, schema, {"child": {"child": {}}}).status is Status.PASS
    assert evaluate(tmp_path, schema, {"child": 1}).status is Status.FAIL


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
@pytest.mark.parametrize("position", ["const", "default", "x-annotation"])
def test_local_ref_cannot_promote_unchecked_literal_schema(tmp_path, keyword, position) -> None:
    schema = {
        position: {"$schema": "http://json-schema.org/draft-07/schema#", "type": "integer"},
        keyword: f"#/{position}",
    }
    result = evaluate(tmp_path, schema, 1)
    assert result.status is Status.ERROR
    assert "outside recognized schema positions" in result.summary


@pytest.mark.parametrize("target", [True, False])
def test_local_boolean_schemas(tmp_path, target) -> None:
    schema = {"$defs": {"value": target}, "$ref": "#/$defs/value"}
    assert evaluate(tmp_path, schema).status is (Status.PASS if target else Status.FAIL)


def test_dangling_reference_is_rejected_even_in_unused_branch(tmp_path) -> None:
    schema = {"$defs": {"unused": {"$ref": "#/$defs/absent"}}}
    assert evaluate(tmp_path, schema).status is Status.ERROR


def test_registry_remains_fail_closed_without_static_guards(tmp_path, monkeypatch) -> None:
    registry = engine.Registry
    attempted = []

    def guarded_registry(*, retrieve):
        def record(uri):
            attempted.append(uri)
            return retrieve(uri)
        return registry(retrieve=record)

    monkeypatch.setattr(engine, "validate_schema_policy", lambda schema: None)
    monkeypatch.setattr(engine, "validate_local_schema_references", lambda schema, registry: None)
    monkeypatch.setattr(engine, "Registry", guarded_registry)
    result = evaluate(tmp_path, {"$ref": "https://example.test/never-fetch"})
    assert result.status is Status.ERROR
    assert attempted == ["https://example.test/never-fetch"]
