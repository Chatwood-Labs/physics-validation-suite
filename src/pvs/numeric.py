"""Lossless source inspection and the PVS binary64 norm contract, version 1."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .errors import ArtifactError, NumericOverflowError
from .jsonutil import MAX_SAFE_INTEGER

NumericArray = NDArray[np.float64]


def source_array(value: Any) -> np.ndarray[Any, Any]:
    """Preserve scalar types until numeric validation or structural selection.

    Object storage prevents mixed Python values becoming floats, integers or
    strings before inspection. Already typed arrays retain their source dtype.
    """

    try:
        array = value if isinstance(value, np.ndarray) else np.asarray(value, dtype=object)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ArtifactError(f"value requires a rectangular array: {exc}") from exc
    if array.dtype.kind == "O" and any(
        isinstance(item, (list, tuple, np.ndarray)) for item in array.flat
    ):
        raise ArtifactError("value requires a rectangular array of scalar values")
    return array


def numeric_array(value: Any) -> NumericArray:
    """Validate original real scalars before conversion to binary64."""

    array = source_array(value)
    kind = array.dtype.kind
    if kind == "O":
        for item in array.flat:
            if isinstance(item, (bool, np.bool_)):
                raise ArtifactError("numeric value required, got boolean")
            if isinstance(item, (complex, np.complexfloating)):
                raise ArtifactError("complex numeric values are not supported")
            if not isinstance(item, (int, float, np.integer, np.floating)):
                raise ArtifactError(f"numeric value required, got {type(item).__name__}")
            if isinstance(item, (int, np.integer)) and abs(int(item)) > MAX_SAFE_INTEGER:
                raise ArtifactError("integer value is outside the exact binary64 range")
    elif kind == "c":
        raise ArtifactError("complex numeric values are not supported")
    elif kind not in "iuf":
        raise ArtifactError(f"numeric value required, got dtype {array.dtype}")
    elif kind in "iu" and array.size and (
        np.any(array > MAX_SAFE_INTEGER)
        or (kind == "i" and np.any(array < -MAX_SAFE_INTEGER))
    ):
        raise ArtifactError("integer value is outside the exact binary64 range")
    if array.size == 0:
        raise ArtifactError("numeric value must contain at least one element (empty value)")
    try:
        return np.asarray(array, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ArtifactError(f"value cannot be converted to float64: {exc}") from exc


def stable_norm_v1(values: Iterable[float], metric: str) -> float:
    """Norm profile pvs-binary64-norm/1; see docs/numerical-contract.md.

    Keep each scaled square as one binary64 multiplication. Exponentiation
    can round differently and is not an interchangeable operation here.
    """

    if isinstance(values, np.ndarray) and values.dtype == np.float64 and values.ndim == 1:
        # Own the scratch array: scaling must never mutate the caller's input.
        absolute = np.abs(values)
    else:
        absolute = np.fromiter((abs(float(value)) for value in values), dtype=np.float64)
    if absolute.size == 0 or not np.all(np.isfinite(absolute)):
        raise ArtifactError("numeric norm requires nonempty finite values")
    try:
        if metric == "linf":
            result = float(np.max(absolute))
        elif metric == "l1":
            result = math.fsum(absolute.flat)
        elif metric == "l2":
            scale = float(np.max(absolute))
            if scale == 0.0:
                return 0.0
            # Separate binary64 divide/multiply operations preserve the v1
            # rounding points. Keep math.fsum in C element order, not np.sum,
            # dot, einsum or a BLAS norm. Underflow to zero is valid binary64.
            with np.errstate(under="ignore"):
                np.divide(absolute, scale, out=absolute)
                np.multiply(absolute, absolute, out=absolute)
            result = scale * math.sqrt(math.fsum(absolute.flat))
        else:
            raise ArtifactError(f"unknown norm metric: {metric}")
    except OverflowError as exc:
        raise NumericOverflowError(
            f"numeric {metric.upper()} norm overflowed the finite range"
        ) from exc
    if not math.isfinite(result):
        raise NumericOverflowError(f"numeric {metric.upper()} norm overflowed the finite range")
    return result
