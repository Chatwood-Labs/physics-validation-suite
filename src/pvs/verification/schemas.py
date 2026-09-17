"""Dispatch and validate immutable evidence, manifest and case schemas."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from ..constants import (
    EVIDENCE_SCHEMA,
    LEGACY_MANIFEST_SCHEMA,
    MANIFEST_SCHEMA,
    V2_EVIDENCE_SCHEMA,
    V2_MANIFEST_SCHEMA,
)
from ..formats import FormatSupportError, required_format_checker
from ..schemas import load_schema


def _schema_errors(value: Any, kind: str) -> list[str]:
    if kind == "evidence" and isinstance(value, dict) and value.get("schema") == "pvs-evidence/1":
        kind = "evidence-v1"
    elif kind == "evidence" and isinstance(value, dict) and value.get("schema") == EVIDENCE_SCHEMA:
        kind = "evidence-v3"
    elif (
        kind == "evidence" and isinstance(value, dict) and value.get("schema") == V2_EVIDENCE_SCHEMA
    ):
        kind = "evidence-v2"
    schema = load_schema(kind)
    try:
        format_checker = required_format_checker()
    except FormatSupportError as exc:
        return [f"/<format-support>: {exc}"]
    validator = Draft202012Validator(schema, format_checker=format_checker)
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    result = []
    for error in errors:
        location = "/" + "/".join(str(part) for part in error.absolute_path)
        result.append(f"{location}: {error.message}")
    return result


def _manifest_schema_errors(value: Any) -> list[str]:
    """Select the immutable manifest schema named by the envelope."""

    if not isinstance(value, dict):
        return _schema_errors(value, "manifest")
    identifier = value.get("schema")
    if identifier == MANIFEST_SCHEMA:
        return _schema_errors(value, "manifest-v3")
    if identifier == V2_MANIFEST_SCHEMA:
        return _schema_errors(value, "manifest-v2")
    if identifier == LEGACY_MANIFEST_SCHEMA:
        return _schema_errors(value, "manifest-v1")
    return [
        "/schema: unsupported manifest schema; expected "
        f"{LEGACY_MANIFEST_SCHEMA!r}, {V2_MANIFEST_SCHEMA!r} or {MANIFEST_SCHEMA!r}"
    ]
