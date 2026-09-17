"""Numerical conversion, summaries and comparison metrics."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..errors import ArtifactError
from ..numeric import NumericArray, stable_norm_v1
from ..numeric import numeric_array as numeric_array


def finite_summary(array: NumericArray) -> dict[str, Any]:
    finite = np.isfinite(array)
    summary: dict[str, Any] = {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "size": int(array.size),
        "finite_count": int(np.count_nonzero(finite)),
        "nonfinite_count": int(array.size - np.count_nonzero(finite)),
    }
    finite_values = array[finite]
    if finite_values.size:
        summary["minimum"] = float(np.min(finite_values))
        summary["maximum"] = float(np.max(finite_values))
    return summary


def error_norms(actual: NumericArray, expected: NumericArray) -> dict[str, float]:
    if actual.shape != expected.shape:
        raise ArtifactError(
            f"comparison requires identical shapes, got {actual.shape} and {expected.shape}"
        )
    if actual.size == 0:
        raise ArtifactError("comparison cannot evaluate empty arrays")
    if not np.all(np.isfinite(actual)) or not np.all(np.isfinite(expected)):
        raise ArtifactError("comparison operands contain NaN or infinity")
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        difference = actual - expected
    if not np.all(np.isfinite(difference)):
        raise ArtifactError("comparison difference overflowed the finite numeric range")
    flat = difference.reshape(-1)
    norms = {
        "l1": stable_norm_v1(flat, "l1"),
        "l2": stable_norm_v1(flat, "l2"),
        "linf": stable_norm_v1(flat, "linf"),
    }
    if not all(np.isfinite(value) for value in norms.values()):
        raise ArtifactError("comparison error norm overflowed the finite numeric range")
    return norms


def reference_norm(expected: NumericArray, metric: str) -> float:
    flat = expected.reshape(-1)
    value = stable_norm_v1(flat, metric if metric in {"l1", "l2"} else "linf")
    if not np.isfinite(value):
        raise ArtifactError("reference norm overflowed the finite numeric range")
    return value
