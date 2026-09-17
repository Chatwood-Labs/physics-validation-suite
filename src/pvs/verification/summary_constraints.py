"""Exact necessary conditions from extrema and counts of unknown arrays.

These predicates use recorded binary64 values directly. They do not reconstruct
artifacts or relax the producer's tolerance arithmetic.
"""

from __future__ import annotations


def interval_failure_count(
    count: int,
    minimum: float,
    maximum: float,
    failing_count: int,
    *,
    lower: float | None = None,
    upper: float | None = None,
    inclusive_lower: bool = True,
    inclusive_upper: bool = True,
) -> bool:
    """Check necessary count constraints from finite extrema and an interval."""
    if count < 1 or not 0 <= failing_count <= count or minimum > maximum:
        return False
    if count == 1 and minimum != maximum:
        return False

    def below(value: float) -> bool:
        return lower is not None and (value < lower if inclusive_lower else value <= lower)

    def above(value: float) -> bool:
        return upper is not None and (value > upper if inclusive_upper else value >= upper)

    empty_interval = (
        lower is not None
        and upper is not None
        and (lower > upper or (lower == upper and not (inclusive_lower and inclusive_upper)))
    )
    if empty_interval or below(maximum) or above(minimum):
        return failing_count == count
    low_fails = below(minimum) or above(minimum)
    high_fails = below(maximum) or above(maximum)
    if not low_fails and not high_fails:
        return failing_count == 0
    # Both extrema are attained. Distinct extrema require distinct witnesses:
    # each failing endpoint forces a failure, and each passing endpoint a pass.
    endpoints = (low_fails,) if minimum == maximum else (low_fails, high_fails)
    forced_failures = sum(endpoints)
    forced_passes = len(endpoints) - forced_failures
    return forced_failures <= failing_count <= count - forced_passes


def close_absolute_floor(
    linf: float,
    permitted: float,
    absolute_tolerance: float,
    failing_count: int,
) -> bool:
    """Every finite elementwise closeness tolerance is at least its absolute part."""
    return permitted >= absolute_tolerance and (linf > absolute_tolerance or failing_count == 0)
