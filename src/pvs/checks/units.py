"""Exact declared-unit contracts used by evaluation and verification."""

from __future__ import annotations

from typing import Any

from ..errors import ArtifactError


def _declared_unit(value: Any, location: str) -> str:
    if not isinstance(value, dict):
        raise ArtifactError(f"{location} must declare an explicit unit")
    unit = value.get("unit")
    if not isinstance(unit, str) or not unit:
        raise ArtifactError(f"{location} must declare an explicit unit")
    return unit


def _check_unit(declaration: dict[str, Any]) -> str:
    unit = declaration.get("unit")
    if not isinstance(unit, str) or not unit:
        raise ArtifactError("numeric check must declare an explicit unit")
    return unit


def _source_unit_contract(declaration: dict[str, Any]) -> str:
    unit = _check_unit(declaration)
    source_unit = _declared_unit(declaration.get("source"), "source")
    if source_unit != unit:
        raise ArtifactError(
            f"source unit {source_unit!r} does not exactly match check unit {unit!r}"
        )
    return unit


def _comparison_unit_contract(declaration: dict[str, Any]) -> str:
    unit = _check_unit(declaration)
    for name in ("actual", "expected"):
        operand_unit = _declared_unit(declaration.get(name), name)
        if operand_unit != unit:
            raise ArtifactError(
                f"{name} unit {operand_unit!r} does not exactly match check unit {unit!r}"
            )
    return unit


def _conservation_unit_contract(declaration: dict[str, Any]) -> str:
    unit = _check_unit(declaration)
    operands: list[tuple[str, Any]] = [
        (f"term[{index}]", term.get("value"))
        for index, term in enumerate(declaration.get("terms", []))
    ]
    operands.append(("expected", declaration.get("expected")))
    if "normalization" in declaration:
        operands.append(("normalization", declaration["normalization"]))
    for location, operand in operands:
        operand_unit = _declared_unit(operand, location)
        if operand_unit != unit:
            raise ArtifactError(
                f"{location} unit {operand_unit!r} does not exactly match check unit {unit!r}"
            )
    return unit

