"""v2 requires finite diagnostics even when the selected criterion is defined."""

from __future__ import annotations

from pathlib import Path

import pytest

from pvs.jsonutil import load_strict
from pvs.numeric import stable_norm_v1
from pvs.verify import verify_target


@pytest.mark.parametrize(
    ("fixture", "actual", "expected", "metric", "tolerance", "overflow"),
    [
        (0, [1e308, 1e308], [0.0, 0.0], "linf", 1e308, "L1"),
        (1, [1e308, 1e308], [0.0, 0.0], "l2", 1.5e308, "L1"),
        (2, [1e308, 1e308], [1e308, 1e308], "l1", 0.0, "L1"),
        (3, [1.7e308, 1.7e308], [1.7e308, 1.7e308], "l2", 0.0, "L2"),
    ],
)
def test_diagnostic_overflow_remains_explicit_verified_error(
    fixture: int,
    actual: list[float],
    expected: list[float],
    metric: str,
    tolerance: float,
    overflow: str,
) -> None:
    # Demonstrate that it is a diagnostic, not the selected error, that is undefined.
    difference = [a - e for a, e in zip(actual, expected, strict=True)]
    assert stable_norm_v1(difference, metric) <= tolerance
    path = Path(__file__).parent / "fixtures" / f"evidence-v2-overflow-{fixture}.json"
    envelope = load_strict(path)
    assert envelope["schema"] == "pvs-evidence/2"
    assert envelope["record"]["summary"]["status"] == "ERROR"
    assert envelope["integrity"]["finding"]["status"] == "NOT_ISSUED"
    check = envelope["record"]["checks"][0]
    assert f"{overflow} norm overflowed" in check["summary"]
    assert check["observed"] == check["criterion"] == {}
    assert verify_target(path).valid
