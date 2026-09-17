"""Exact conformance of array execution to the frozen scalar norm contract."""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from pvs.errors import ArtifactError
from pvs.numeric import stable_norm_v1


def _scalar_contract(values: list[float], metric: str) -> float:
    # Frozen 0.2.6 arithmetic, kept in tests as an independent execution path.
    absolute = [abs(float(value)) for value in values]
    if metric == "l1":
        return math.fsum(absolute)
    if metric == "linf":
        return max(absolute)
    scale = max(absolute)
    if scale == 0.0:
        return 0.0
    scaled = (value / scale for value in absolute)
    return scale * math.sqrt(math.fsum(item * item for item in scaled))


@pytest.mark.parametrize("metric", ["l1", "l2", "linf"])
@pytest.mark.parametrize("layout", ["array", "strided", "readonly", "iterator"])
def test_array_norms_match_scalar_contract_exactly(metric: str, layout: str) -> None:
    rng = random.Random(270907)
    vectors = [
        [0.0, -0.0], [286.0, 337.0],
        [math.ulp(0.0), -math.ulp(0.0), 1e-300], [1e300, 1e-300],
    ]
    for _ in range(40):
        vectors.append([
            math.ldexp(rng.uniform(-1.0, 1.0), rng.randint(-1074, 990)) for _ in range(32)
        ])
    for values in vectors:
        array = np.asarray(values, dtype=np.float64)
        if layout == "strided":
            backing = np.repeat(array, 2)
            array = backing[::2]
        if layout == "readonly":
            array.flags.writeable = False
        before = array.tobytes()
        operand = iter(values) if layout == "iterator" else array
        # Scalar binary64 underflow never raised; vector execution must retain
        # that behavior even when a caller enables NumPy floating-point traps.
        with np.errstate(all="raise"):
            actual = stable_norm_v1(operand, metric)
        assert actual.hex() == _scalar_contract(values, metric).hex()
        assert array.tobytes() == before


@pytest.mark.parametrize("metric", ["l1", "l2", "linf"])
@pytest.mark.parametrize("values", [[], [float("nan")], [float("inf")], [-float("inf")]])
def test_norms_still_reject_empty_and_nonfinite_inputs(metric: str, values: list[float]) -> None:
    with pytest.raises(ArtifactError, match="nonempty finite"):
        stable_norm_v1(np.asarray(values, dtype=np.float64), metric)


@pytest.mark.parametrize("metric", ["l1", "l2"])
def test_norms_still_reject_finite_range_overflow(metric: str) -> None:
    with pytest.raises(ArtifactError, match="overflowed"):
        stable_norm_v1(np.asarray([1.7e308, 1.7e308]), metric)
