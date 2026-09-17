"""Typed inspection of facts available without opening scientific artifacts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..canonical import canonical_bytes
from ..errors import ArtifactError
from ..jsonutil import MAX_SAFE_INTEGER
from ..numeric import numeric_array
from .units import (
    _comparison_unit_contract,
    _conservation_unit_contract,
    _source_unit_contract,
)


class ValueDomain(Enum):
    """Value facts established by an operation without reading its artifacts."""

    ELEMENT_COUNT = "element-count"


@dataclass(frozen=True)
class UnavailableOperand:
    """Unknown artifact values with known shape, domain and source expression."""

    shape: tuple[int, ...] | None = None
    value_domain: ValueDomain | None = None
    source_identity: bytes | None = None


@dataclass(frozen=True)
class LiteralOperand:
    values: tuple[float, ...]
    shape: tuple[int, ...]


@dataclass(frozen=True)
class InvalidOperand:
    reason: str


OperandInspection = UnavailableOperand | LiteralOperand | InvalidOperand
UsableOperand = UnavailableOperand | LiteralOperand


def require_value_domain(operand: UsableOperand, value: Any) -> None:
    """Enforce declaration-derived value facts before any numeric coercion.

    Size is returned as an integer, then subjected to numeric ingestion. Its
    recorded binary64 value can be 2.0, but cannot be fractional, negative,
    nonfinite or outside the safe integer range. Unknown unrestricted artifact
    values and literals remain subject to their existing observation contracts.
    """

    if not isinstance(operand, UnavailableOperand):
        return
    if operand.value_domain is ValueDomain.ELEMENT_COUNT and (
        type(value) not in {int, float}
        or not 0 <= value <= MAX_SAFE_INTEGER
        or not math.isfinite(value)
        or int(value) != value
    ):
        raise ArtifactError(
            "size reduction requires a finite nonnegative integral value "
            f"no greater than {MAX_SAFE_INTEGER}"
        )


def inspect_operand(operand: Any) -> OperandInspection:
    if not isinstance(operand, dict):
        return InvalidOperand("operand must be a declared source or literal")
    if "artifact" in operand:
        reduction = operand.get("reduce")
        return UnavailableOperand(
            () if reduction is not None else None,
            ValueDomain.ELEMENT_COUNT if reduction == "size" else None,
            canonical_bytes(operand),
        )
    if "value" not in operand:
        return InvalidOperand("operand has no source or literal value")
    try:
        array = numeric_array(operand["value"])
    except ArtifactError as exc:
        return InvalidOperand(str(exc))
    values = tuple(float(value) for value in array.flat)
    if not all(math.isfinite(value) for value in values):
        return InvalidOperand("literal operand contains NaN or infinity")
    return LiteralOperand(values, tuple(array.shape))


@dataclass(frozen=True)
class UnavailableComparison:
    actual: UsableOperand
    expected: UsableOperand


@dataclass(frozen=True)
class LiteralComparison:
    actual: LiteralOperand
    expected: LiteralOperand
    differences: tuple[float, ...]


@dataclass(frozen=True)
class InvalidComparison:
    reason: str


ComparisonInspection = UnavailableComparison | LiteralComparison | InvalidComparison


def inspect_comparison(declared: dict[str, Any]) -> ComparisonInspection:
    try:
        _comparison_unit_contract(declared)
    except ArtifactError as exc:
        return InvalidComparison(str(exc))
    actual = inspect_operand(declared.get("actual"))
    expected = inspect_operand(declared.get("expected"))
    if isinstance(actual, InvalidOperand):
        return InvalidComparison(actual.reason)
    if isinstance(expected, InvalidOperand):
        return InvalidComparison(expected.reason)
    if actual.shape is not None and expected.shape is not None and actual.shape != expected.shape:
        return InvalidComparison("comparison requires identical operand shapes")
    if declared["metric"] == "absolute" and any(
        value.shape is not None and math.prod(value.shape) != 1 for value in (actual, expected)
    ):
        return InvalidComparison("absolute metric is scalar-only")
    if isinstance(actual, LiteralOperand) and isinstance(expected, LiteralOperand):
        differences = tuple(a - e for a, e in zip(actual.values, expected.values, strict=True))
        if not all(math.isfinite(value) for value in differences):
            return InvalidComparison("literal comparison subtraction overflowed")
        return LiteralComparison(actual, expected, differences)
    return UnavailableComparison(actual, expected)


def require_recorded_value_domains(declared: dict[str, Any], observed: dict[str, Any]) -> None:
    """Check operation-derived facts without imposing modern unit rules on v1."""

    def constrained(source: Any) -> UnavailableOperand | None:
        if isinstance(source, dict) and "artifact" in source:
            operand = inspect_operand(source)
            if isinstance(operand, UnavailableOperand) and operand.value_domain is not None:
                return operand
        return None

    kind = declared["type"]
    if kind in {"compare", "reference"}:
        for name in ("actual", "expected"):
            operand = constrained(declared[name])
            if operand is not None:
                if observed.get("shape") != list(operand.shape or ()):
                    raise ArtifactError("recorded shape contradicts the size reduction")
                require_value_domain(operand, observed.get(name))
    elif kind in {"finite", "range", "monotonic"}:
        operand = constrained(declared["source"])
        if operand is not None:
            if kind == "monotonic":
                raise ArtifactError("monotonic cannot evaluate a scalar size reduction")
            counts = {"size": 1, "finite_count": 1, "nonfinite_count": 0}
            if observed.get("shape") != [] or any(
                type(observed.get(key)) is not int or observed[key] != expected
                for key, expected in counts.items()
            ):
                raise ArtifactError("size reduction must summarize one finite element count")
            require_value_domain(operand, observed.get("minimum"))
            require_value_domain(operand, observed.get("maximum"))
            if observed["minimum"] != observed["maximum"]:
                raise ArtifactError("a scalar size reduction requires identical extrema")
    elif kind == "conservation":
        for index, term in enumerate(declared["terms"]):
            operand = constrained(term["value"])
            if operand is not None:
                terms = observed.get("terms")
                if (
                    not isinstance(terms, list) or index >= len(terms)
                    or not isinstance(terms[index], dict)
                ):
                    raise ArtifactError("conservation requires each size term's raw total")
                require_value_domain(operand, terms[index].get("raw_total"))
        for name in ("expected", "normalization"):
            operand = constrained(declared.get(name))
            if operand is not None:
                # A size is already nonnegative: normalization's abs preserves it.
                require_value_domain(operand, observed.get(name))


def require_repeated_source_totals(declared: dict[str, Any], observed: dict[str, Any]) -> None:
    """Bind all uses of an identical source within one completed conservation check.

    Compare canonical bytes of the complete source specification, including its
    artifact reference, selectors, component, reduction and unit. The artifact
    definition and decoding profile are shared by that reference in this case.
    Coefficients and labels belong to terms, not their unweighted source values.
    This deliberately does not infer equivalence between different specifications.

    A completed expected/normalization use establishes that its selected array
    has exactly one finite element, whether its shape is scalar or size-one.
    Summing that same selection as a term preserves the element's value. Thus
    an expected value agrees with its matching raw total, and normalization
    agrees with the absolute value of a matching raw total or expected value.
    An omitted normalization is defined as abs(expected) for every source kind.
    General array totals are only related to exactly matching selections.
    Numeric equality deliberately treats the two signed zeros as equal.
    """

    if declared["type"] != "conservation":
        return
    normalization = observed.get("normalization")
    if (not isinstance(normalization, (int, float)) or isinstance(normalization, bool)
            or not math.isfinite(normalization) or normalization < 0):
        raise ArtifactError("conservation normalization must record a finite nonnegative scalar")
    terms = observed.get("terms")
    if not isinstance(terms, list) or len(terms) != len(declared["terms"]):
        raise ArtifactError("conservation requires one observation per term")
    totals: dict[bytes, float] = {}
    for declaration, term in zip(declared["terms"], terms, strict=True):
        operand = inspect_operand(declaration["value"])
        if not isinstance(operand, UnavailableOperand) or operand.source_identity is None:
            continue
        if not isinstance(term, dict):
            raise ArtifactError("conservation term must record its raw total")
        raw_total = term.get("raw_total")
        if (not isinstance(raw_total, (int, float)) or isinstance(raw_total, bool)
                or not math.isfinite(raw_total)):
            raise ArtifactError("conservation raw total must be a finite number")
        previous = totals.setdefault(operand.source_identity, raw_total)
        if raw_total != previous:
            raise ArtifactError("identical conservation sources require identical raw totals")

    for name in ("expected", "normalization"):
        operand = inspect_operand(declared.get(name))
        if not isinstance(operand, UnavailableOperand) or operand.source_identity is None:
            continue
        scalar = observed.get(name)
        if (not isinstance(scalar, (int, float)) or isinstance(scalar, bool)
                or not math.isfinite(scalar)):
            raise ArtifactError(f"conservation {name} must record one finite scalar")
        if name == "expected":
            previous = totals.setdefault(operand.source_identity, scalar)
            if scalar != previous:
                raise ArtifactError(
                    "identical conservation sources require expected to equal the raw total"
                )
        elif operand.source_identity in totals and scalar != abs(totals[operand.source_identity]):
            raise ArtifactError(
                "identical conservation sources require normalization to equal the absolute value"
            )

    if "normalization" not in declared:
        expected = observed.get("expected")
        if (not isinstance(expected, (int, float)) or isinstance(expected, bool)
                or not math.isfinite(expected) or normalization != abs(expected)):
            raise ArtifactError("implicit conservation normalization must equal abs(expected)")


def require_completed_declaration(declared: dict[str, Any], observed: dict[str, Any]) -> None:
    """Reject known contradictions only for completed scientific observations."""

    kind = declared["type"]
    if kind in {"compare", "reference"}:
        inspection = inspect_comparison(declared)
        if isinstance(inspection, InvalidComparison):
            raise ArtifactError(inspection.reason)
        for operand in (inspection.actual, inspection.expected):
            if operand.shape is not None and list(operand.shape) != observed.get("shape"):
                raise ArtifactError("recorded shape contradicts a known operand shape")
        if declared["metric"] == "absolute":
            shape = observed.get("shape")
            if not isinstance(shape, list) or math.prod(shape) != 1:
                raise ArtifactError("absolute metric is scalar-only")
    elif kind in {"finite", "range", "monotonic"}:
        _source_unit_contract(declared)
        source = inspect_operand(declared["source"])
        if isinstance(source, InvalidOperand):
            raise ArtifactError(source.reason)
        if source.shape is not None and list(source.shape) != observed.get("shape"):
            raise ArtifactError("recorded shape contradicts the source reduction")
    elif kind == "conservation":
        _conservation_unit_contract(declared)
        operands = [
            *(("term", term["value"]) for term in declared["terms"]),
            ("expected", declared["expected"]),
        ]
        if "normalization" in declared:
            operands.append(("normalization", declared["normalization"]))
        for name, operand in operands:
            value = inspect_operand(operand)
            if isinstance(value, InvalidOperand):
                raise ArtifactError(value.reason)
            if name != "term" and value.shape is not None and math.prod(value.shape) != 1:
                raise ArtifactError(f"conservation {name} must be one finite scalar")
            if name == "term" and isinstance(value, LiteralOperand):
                try:
                    total = math.fsum(value.values)
                except OverflowError as exc:
                    raise ArtifactError("literal conservation sum overflowed") from exc
                if not math.isfinite(total):
                    raise ArtifactError("literal conservation sum overflowed")
