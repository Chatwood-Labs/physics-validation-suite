"""Packaged JSON Schemas."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

SCHEMA_FILES = {
    "case": "pvs-case.schema.json",
    "case-v1": "pvs-case-v1.schema.json",
    "case-v2": "pvs-case-v2.schema.json",
    "evidence": "pvs-evidence.schema.json",
    "evidence-v1": "pvs-evidence-v1.schema.json",
    "evidence-v3": "pvs-evidence-v3.schema.json",
    "evidence-v2": "pvs-evidence-v2.schema.json",
    "manifest": "pvs-manifest.schema.json",
    "manifest-v1": "pvs-manifest-v1.schema.json",
    "manifest-v3": "pvs-manifest-v3.schema.json",
    "manifest-v2": "pvs-manifest-v2.schema.json",
}


def schema_text(kind: str) -> str:
    try:
        filename = SCHEMA_FILES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown schema kind: {kind}") from exc
    return files(__package__).joinpath(filename).read_text(encoding="utf-8")


def load_schema(kind: str) -> dict[str, Any]:
    value = json.loads(schema_text(kind))
    if not isinstance(value, dict):
        raise ValueError(f"packaged {kind} schema is not a JSON object")
    return value
