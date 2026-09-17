"""Typed effective criteria shared by evaluation and semantic verification.

Declarations remain untrusted mappings until runtime schema and semantic checks
succeed. Typed results describe the exact keys emitted by the criterion builder.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from ..errors import ArtifactError


class ExistsCriterion(TypedDict):
    artifact: str
    required_type: Literal["regular_file"]


class SchemaCriterion(TypedDict):
    artifact: str
    schema_artifact: str
    dialect: Literal["https://json-schema.org/draft/2020-12/schema"]


class FiniteCriterion(TypedDict):
    nonfinite_count: Literal[0]
    unit: str


class RangeCriterion(TypedDict):
    minimum: float | None
    maximum: float | None
    inclusive_minimum: bool
    inclusive_maximum: bool
    unit: str


class MonotonicCriterion(TypedDict):
    direction: str
    absolute_tolerance: float
    unit: str


class NormCriterion(TypedDict):
    metric: str
    unit: str
    tolerance: float


class RelativeCriterion(NormCriterion):
    scale_floor: float


class CloseCriterion(TypedDict):
    metric: Literal["close"]
    unit: str
    absolute_tolerance: float
    relative_tolerance: float
    rule: Literal["abs(actual-expected) <= absolute + relative*abs(expected)"]


class ConservationCriterion(TypedDict):
    absolute_tolerance: float
    relative_tolerance: float
    rule: Literal["abs(balance-expected) <= absolute + relative*normalization"]
    unit: str


Criterion = (
    ExistsCriterion | SchemaCriterion | FiniteCriterion | RangeCriterion
    | MonotonicCriterion | NormCriterion | RelativeCriterion | CloseCriterion
    | ConservationCriterion
)


def expected_criterion(declaration: dict[str, Any]) -> Criterion:
    """Derive the exact effective criterion recorded for a valid declaration."""

    check_type = str(declaration["type"])
    if check_type == "exists":
        return {
            "artifact": declaration["artifact"],
            "required_type": "regular_file",
        }
    if check_type == "schema":
        return {
            "artifact": declaration["artifact"],
            "schema_artifact": declaration["schema_artifact"],
            "dialect": "https://json-schema.org/draft/2020-12/schema",
        }
    if check_type == "finite":
        return {"nonfinite_count": 0, "unit": declaration["unit"]}
    if check_type == "range":
        return {
            "minimum": declaration.get("minimum"),
            "maximum": declaration.get("maximum"),
            "inclusive_minimum": bool(declaration.get("inclusive_minimum", True)),
            "inclusive_maximum": bool(declaration.get("inclusive_maximum", True)),
            "unit": declaration["unit"],
        }
    if check_type == "monotonic":
        return {
            "direction": declaration["direction"],
            "absolute_tolerance": float(declaration["absolute_tolerance"]),
            "unit": declaration["unit"],
        }
    if check_type in {"compare", "reference"}:
        metric = str(declaration["metric"])
        if metric == "relative":
            return {
                "metric": "relative", "unit": declaration["unit"],
                "scale_floor": float(declaration["scale_floor"]),
                "tolerance": float(declaration["tolerance"]),
            }
        if metric == "close":
            return {
                "metric": "close", "unit": declaration["unit"],
                "absolute_tolerance": float(declaration["absolute_tolerance"]),
                "relative_tolerance": float(declaration["relative_tolerance"]),
                "rule": "abs(actual-expected) <= absolute + relative*abs(expected)",
            }
        return {
            "metric": metric, "unit": declaration["unit"],
            "tolerance": float(declaration["tolerance"]),
        }
    if check_type == "conservation":
        return {
            "absolute_tolerance": float(declaration["absolute_tolerance"]),
            "relative_tolerance": float(declaration["relative_tolerance"]),
            "rule": "abs(balance-expected) <= absolute + relative*normalization",
            "unit": declaration["unit"],
        }
    raise ArtifactError(f"unknown check type: {check_type}")

