"""Outward-rounded envelopes for pvs-binary64-norm/1, without reading arrays.

These are necessary conditions on a summary, not reconstruction of its inputs.
See docs/numerical-contract.md for the operation-by-operation derivation.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from fractions import Fraction

from ..checks.diagnostics import AvailableDiagnostic, Diagnostic
from ..errors import ArtifactError

_MAX_FINITE = Fraction(sys.float_info.max)


@dataclass(frozen=True)
class FiniteUpperBound:
    value: float


@dataclass(frozen=True)
class OverflowPossible:
    """The envelope cannot prove finiteness; it does not assert overflow."""


UpperBound = FiniteUpperBound | OverflowPossible


def _round_up(value: Fraction) -> UpperBound:
    """Least binary64 number >= a nonnegative rational, or no finite bound."""

    if value > _MAX_FINITE:
        return OverflowPossible()
    rounded = float(value)
    if Fraction(rounded) < value:
        rounded = math.nextafter(rounded, math.inf)
    return FiniteUpperBound(rounded)


def _sqrt_up(value: float) -> float:
    """Bracket sqrt by exact rational squares; never trust a rounded square."""

    exact = Fraction(value)
    result = math.sqrt(value)
    while Fraction(result) * Fraction(result) < exact:
        result = math.nextafter(result, math.inf)
    previous = math.nextafter(result, 0.0)
    while previous < result and Fraction(previous) * Fraction(previous) >= exact:
        result = previous
        previous = math.nextafter(result, 0.0)
    return result


def norm_upper_bounds(size: int, maximum: float) -> tuple[UpperBound, UpperBound]:
    """Bound L1 and the scaled L2 separately, including each rounding point."""

    if size < 1 or not math.isfinite(maximum) or maximum < 0:
        raise ArtifactError("norm bounds require positive size and finite nonnegative maximum")
    if maximum == 0:
        return FiniteUpperBound(0.0), FiniteUpperBound(0.0)
    scale = Fraction(maximum)
    l1 = _round_up(size * scale)
    # Each rounded quotient and square is in [0, 1]. The fsum input total is
    # bounded by the exact integer size, even when size is not representable.
    squared_sum = _round_up(Fraction(size))
    if isinstance(squared_sum, OverflowPossible):
        return l1, OverflowPossible()
    factor = _sqrt_up(squared_sum.value)
    l2 = _round_up(scale * Fraction(factor))
    return l1, l2


def validate_norm_summary(size: int, l1: Diagnostic, l2: Diagnostic, maximum: float) -> None:
    """Reject contradictory finite values and impossible OVERFLOW assertions."""

    upper_l1, upper_l2 = norm_upper_bounds(size, maximum)
    for name, diagnostic, upper in (("L1", l1, upper_l1), ("L2", l2, upper_l2)):
        if isinstance(upper, FiniteUpperBound):
            if not isinstance(diagnostic, AvailableDiagnostic):
                raise ArtifactError(f"{name} error cannot overflow its finite norm-profile bound")
            if diagnostic.value > upper.value:
                raise ArtifactError(f"{name} error exceeds its norm-profile upper bound")
        if isinstance(diagnostic, AvailableDiagnostic) and diagnostic.value < maximum:
            raise ArtifactError(f"{name} error is below L-infinity error")
    if isinstance(l1, AvailableDiagnostic) and (
        not isinstance(l2, AvailableDiagnostic) or l1.value < l2.value
    ):
        raise ArtifactError("L2 error must be available and bounded by finite L1 error")
