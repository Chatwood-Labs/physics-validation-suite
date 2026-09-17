"""Typed comparison observations for pvs-comparison/2 in evidence v3."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..checks.diagnostics import (
    COMPARISON_PROFILE,
    AvailableDiagnostic,
    Diagnostic,
    norm_diagnostic,
    parse_diagnostic,
    relative_diagnostic,
    require_available,
)
from ..checks.inspection import (
    InvalidComparison,
    LiteralComparison,
    LiteralOperand,
    UnavailableOperand,
    inspect_comparison,
    inspect_operand,
    require_value_domain,
)
from ..constants import EVIDENCE_SCHEMA
from ..errors import ArtifactError
from .norm_bounds import validate_norm_summary
from .summary_constraints import close_absolute_floor


def finite_number(value: Any, *, nonnegative: bool = True) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ArtifactError("observation requires a finite real number")
    if nonnegative and value < 0:
        raise ArtifactError("observation requires a nonnegative number")
    return float(value)


@dataclass(frozen=True)
class ScalarObservation:
    actual: float
    expected: float
    absolute_error: float
    relative_error: Diagnostic


@dataclass(frozen=True)
class CloseObservation:
    failing_count: int
    failing_indices: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class ComparisonObservation:
    shape: tuple[int, ...]
    l1: Diagnostic
    l2: Diagnostic
    linf: Diagnostic
    gating_error: float
    permitted_error: float
    reference: Diagnostic | None
    scalar: ScalarObservation | None
    close: CloseObservation | None


def parse_comparison(declared: dict[str, Any], observed: dict[str, Any]) -> ComparisonObservation:
    shape = observed.get("shape")
    if not isinstance(shape, list) or not all(type(n) is int and n >= 0 for n in shape):
        raise ArtifactError("comparison shape must contain nonnegative integer dimensions")
    size = math.prod(shape)
    if size == 0:
        raise ArtifactError("comparison cannot evaluate empty arrays")
    metric = declared["metric"]
    keys = {
        "profile",
        "shape",
        "unit",
        "l1_error",
        "l2_error",
        "linf_error",
        "gating_error",
        "permitted_error",
    }
    reference = None
    if metric in {"l1", "l2", "linf", "relative"}:
        keys.add("reference_norm")
        reference = parse_diagnostic(observed.get("reference_norm"))
    if metric == "relative":
        keys.add("relative_l2_error")
        if finite_number(observed.get("relative_l2_error")) != observed.get("gating_error"):
            raise ArtifactError("relative L2 error must equal the gating error")
    scalar = None
    if size == 1:
        keys.update({"actual", "expected", "absolute_error", "relative_error"})
        scalar = ScalarObservation(
            finite_number(observed.get("actual"), nonnegative=False),
            finite_number(observed.get("expected"), nonnegative=False),
            finite_number(observed.get("absolute_error")),
            parse_diagnostic(observed.get("relative_error"), allow_zero_reference=True),
        )
    close = None
    if metric == "close":
        keys.update({"failing_count", "failing_indices"})
        count, indices = observed.get("failing_count"), observed.get("failing_indices")
        if type(count) is not int or not 0 <= count <= size:
            raise ArtifactError("invalid closeness failing count")
        if not isinstance(indices, list) or len(indices) != min(count, 10):
            raise ArtifactError("closeness requires the exact bounded failing sample")
        if not all(
            isinstance(index, list)
            and len(index) == len(shape)
            and all(type(n) is int and 0 <= n < shape[i] for i, n in enumerate(index))
            for index in indices
        ):
            raise ArtifactError("closeness index is outside the recorded shape")
        canonical = tuple(tuple(index) for index in indices)
        if canonical != tuple(sorted(set(canonical))):
            raise ArtifactError("closeness indices must be unique and ordered")
        if count == size and canonical != tuple(
            flat_index(i, tuple(shape)) for i in range(min(size, 10))
        ):
            raise ArtifactError("all-failing closeness samples must start at the first element")
        close = CloseObservation(count, canonical)
    if set(observed) != keys or observed["profile"] != COMPARISON_PROFILE:
        raise ArtifactError("comparison observation has unexpected fields or profile")
    if observed["unit"] != declared["unit"]:
        raise ArtifactError("comparison observation unit does not match the declaration")
    return ComparisonObservation(
        tuple(shape),
        parse_diagnostic(observed["l1_error"]),
        parse_diagnostic(observed["l2_error"]),
        parse_diagnostic(observed["linf_error"]),
        finite_number(observed["gating_error"]),
        finite_number(observed["permitted_error"]),
        reference,
        scalar,
        close,
    )


def flat_index(index: int, shape: tuple[int, ...]) -> tuple[int, ...]:
    coordinates = []
    for dimension in reversed(shape):
        index, coordinate = divmod(index, dimension)
        coordinates.append(coordinate)
    return tuple(reversed(coordinates))


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ArtifactError(reason)


def require_self_comparison(
    declared: dict[str, Any], observed: dict[str, Any], *, evidence_schema: str,
) -> bool:
    """Enforce equality of the complete source expressions in a completed check.

    The same finite selection subtracted from itself is exactly zero. This fact
    does not require reading its values or applying modern units/arithmetic to
    historical observations. Return whether that equality premise applies.
    This does not constrain reference-norm availability:
    a zero difference can coexist with an overflowing supplementary reference
    norm, or a scalar relative diagnostic unavailable at a zero reference.
    """

    if declared["type"] not in {"compare", "reference"}:
        return False
    actual_source, expected_source = declared.get("actual"), declared.get("expected")
    if not all(isinstance(source, dict) and "artifact" in source
               for source in (actual_source, expected_source)):
        return False
    actual = inspect_operand(actual_source)
    expected = inspect_operand(expected_source)
    if not (
        isinstance(actual, UnavailableOperand)
        and isinstance(expected, UnavailableOperand)
        and actual.source_identity is not None
        and actual.source_identity == expected.source_identity
    ):
        return False
    reason = "identical comparison sources require zero differences"
    for name in ("l1_error", "l2_error", "linf_error"):
        value = observed.get(name)
        if evidence_schema == EVIDENCE_SCHEMA:
            _require(parse_diagnostic(value) == AvailableDiagnostic(0.0), reason)
        else:
            _require(finite_number(value) == 0.0, reason)
    _require(finite_number(observed.get("gating_error")) == 0.0, reason)
    shape = observed.get("shape")
    scalar_shape = (
        isinstance(shape, list) and all(type(n) is int and n > 0 for n in shape)
        and math.prod(shape) == 1
    )
    if scalar_shape or "actual" in observed or "expected" in observed:
        expected_scalar = finite_number(observed.get("expected"), nonnegative=False)
        _require(
            finite_number(observed.get("actual"), nonnegative=False)
            == expected_scalar,
            "identical comparison sources require identical scalar values",
        )
        _require(finite_number(observed.get("absolute_error")) == 0.0, reason)
        if evidence_schema == EVIDENCE_SCHEMA:
            _require(
                parse_diagnostic(observed.get("relative_error"), allow_zero_reference=True)
                == relative_diagnostic(0.0, expected_scalar),
                reason,
            )
        else:
            _require(
                "relative_error" in observed
                and (observed.get("relative_error") is None if expected_scalar == 0.0
                 else finite_number(observed.get("relative_error")) == 0.0)
                and "relative_error_overflow" not in observed,
                reason,
            )
    if declared["metric"] == "relative":
        _require(finite_number(observed.get("relative_l2_error")) == 0.0, reason)
    if declared["metric"] == "close":
        _require(
            type(observed.get("failing_count")) is int
            and observed["failing_count"] == 0
            and observed.get("failing_indices") == [],
            "identical comparison sources cannot have closeness failures",
        )
    return True


def validate_comparison(declared: dict[str, Any], observed: dict[str, Any]) -> bool:
    """Return the exact gate outcome, or reject contradictory evidence."""

    inspection = inspect_comparison(declared)
    if isinstance(inspection, InvalidComparison):
        raise ArtifactError(inspection.reason)
    value = parse_comparison(declared, observed)
    require_self_comparison(declared, observed, evidence_schema=EVIDENCE_SCHEMA)
    metric = declared["metric"]
    size = math.prod(value.shape)
    for operand in (inspection.actual, inspection.expected):
        _require(
            operand.shape is None or operand.shape == value.shape, "known operand shape mismatch"
        )
    _require(metric != "absolute" or size == 1, "absolute metric is scalar-only")
    linf = require_available(value.linf, quantity="L-infinity error")
    norms = {"l1": value.l1, "l2": value.l2, "linf": value.linf}
    validate_norm_summary(size, value.l1, value.l2, linf)

    if value.scalar is not None:
        scalar = value.scalar
        error = abs(scalar.actual - scalar.expected)
        _require(math.isfinite(error), "scalar subtraction overflowed")
        _require(scalar.absolute_error == error, "scalar absolute error is incorrect")
        _require(
            all(diagnostic == AvailableDiagnostic(error) for diagnostic in norms.values()),
            "scalar error norms must equal the absolute error",
        )
        _require(
            scalar.relative_error == relative_diagnostic(error, scalar.expected),
            "scalar relative diagnostic is incorrect",
        )
        for operand, recorded in (
            (inspection.actual, scalar.actual),
            (inspection.expected, scalar.expected),
        ):
            require_value_domain(operand, recorded)
            if isinstance(operand, LiteralOperand):
                _require(recorded == operand.values[0], "recorded scalar differs from literal")

    if isinstance(inspection, LiteralComparison):
        for name, diagnostic in norms.items():
            _require(
                diagnostic == norm_diagnostic(inspection.differences, name),
                f"literal {name} diagnostic is incorrect",
            )
    if value.reference is not None:
        reference_metric = "l2" if metric == "relative" else metric
        if reference_metric == "linf":
            require_available(value.reference, quantity="finite L-infinity reference norm")
        if isinstance(inspection.expected, LiteralOperand):
            _require(
                value.reference == norm_diagnostic(inspection.expected.values, reference_metric),
                "literal reference norm diagnostic is incorrect",
            )
        if value.scalar is not None:
            _require(
                value.reference == AvailableDiagnostic(abs(value.scalar.expected)),
                "scalar reference norm must be available and exact",
            )

    if metric in {"absolute", "l1", "l2", "linf"}:
        name = "linf" if metric == "absolute" else metric
        gate = require_available(norms[name], quantity=f"selected {name} error")
        _require(value.gating_error == gate, "selected error and gating error differ")
    elif metric == "relative":
        _require(value.reference is not None, "relative metric requires its reference norm")
        assert value.reference is not None
        reference = require_available(value.reference, quantity="relative denominator norm")
        numerator = require_available(value.l2, quantity="relative numerator norm")
        denominator = max(reference, float(declared["scale_floor"]))
        gate = numerator / denominator
        _require(
            math.isfinite(gate) and value.gating_error == gate,
            "relative gating error contradicts numerator and denominator",
        )
    elif metric == "close":
        _require(value.close is not None, "close metric requires failing samples")
        assert value.close is not None
        _require(value.gating_error == linf, "close gating error must equal L-infinity")
        actual_pass = value.close.failing_count == 0
        _require(
            not actual_pass or linf <= value.permitted_error,
            "passing closeness error exceeds the largest permitted error",
        )
        expected_values = (
            inspection.expected.values
            if isinstance(inspection.expected, LiteralOperand)
            else (value.scalar.expected,)
            if value.scalar is not None
            else None
        )
        atol, rtol = float(declared["absolute_tolerance"]), float(declared["relative_tolerance"])
        _require(
            close_absolute_floor(linf, value.permitted_error, atol, value.close.failing_count),
            "closeness contradicts the absolute-tolerance floor",
        )
        permitted = (
            [atol + rtol * abs(e) for e in expected_values]
            if expected_values is not None
            else [atol]
            if rtol == 0
            else None
        )
        if permitted is not None:
            _require(all(math.isfinite(p) for p in permitted), "closeness tolerance overflowed")
            _require(value.permitted_error == max(permitted), "closeness maximum tolerance differs")
            if linf <= min(permitted):
                _require(actual_pass, "all errors are bounded by the smallest tolerance")
            if min(permitted) == max(permitted):
                _require(
                    actual_pass is (linf <= permitted[0]), "constant-tolerance outcome differs"
                )
        differences = (
            inspection.differences
            if isinstance(inspection, LiteralComparison)
            else (value.scalar.actual - value.scalar.expected,)
            if value.scalar is not None
            else None
        )
        if differences is not None:
            assert permitted is not None
            positions = [
                i for i, (d, p) in enumerate(zip(differences, permitted, strict=True)) if abs(d) > p
            ]
            _require(value.close.failing_count == len(positions), "literal failing count differs")
            _require(
                value.close.failing_indices
                == tuple(flat_index(i, value.shape) for i in positions[:10]),
                "literal failing samples differ",
            )
        return actual_pass
    else:
        raise ArtifactError("unsupported comparison metric")
    _require(value.permitted_error == float(declared["tolerance"]), "recorded tolerance differs")
    return value.gating_error <= value.permitted_error
