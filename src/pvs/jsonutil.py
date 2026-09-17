"""Strict JSON parsing and deterministic human-readable serialization."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON number is outside the finite binary64 range: {value}")
    return parsed


def _safe_integer(value: str) -> int:
    parsed = int(value)
    if abs(parsed) > MAX_SAFE_INTEGER:
        raise ValueError(f"JSON integer is outside the RFC 8785 interoperable range: {value}")
    return parsed


def _require_unicode_scalars(value: Any, location: str = "<root>") -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{location}: string contains an unpaired Unicode surrogate") from exc
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_unicode_scalars(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require_unicode_scalars(key, f"{location}.<key>")
            _require_unicode_scalars(item, f"{location}.{key}")


def loads_strict(text: str) -> Any:
    value = json.loads(
        text,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        parse_int=_safe_integer,
    )
    _require_unicode_scalars(value)
    return value


def load_strict(path: Path) -> Any:
    return loads_strict(path.read_text(encoding="utf-8"))


def dumps_pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_pretty(path: Path, value: Any) -> None:
    path.write_text(dumps_pretty(value), encoding="utf-8", newline="\n")
