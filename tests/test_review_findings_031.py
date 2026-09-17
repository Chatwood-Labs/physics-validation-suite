"""Record-visible consistency regressions for the uploaded PVS 0.3.1.

Place in the project's tests/ directory and run:
    python tools/run-pytest.py tests/test_pvs_review_regressions.py -q

Four rejection tests currently expose gaps; four valid controls should pass.
These test the real observation-validation boundary, not end-to-end package
rehashing, report generation, cryptographic pins or scientific artifact replay.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from pvs.constants import EVIDENCE_SCHEMA
from pvs.verification.observations import (
    CompletedObservation,
    InvalidObservation,
    validate_observation,
)


def _examples() -> list[tuple[str, dict[str, Any], dict[str, Any], bool]]:
    source = {"artifact": "result", "pointer": "/x", "unit": "1"}
    summary = {
        "dtype": "float64", "shape": [1], "size": 1,
        "finite_count": 1, "nonfinite_count": 0,
        "minimum": 0.0, "maximum": 1.0, "unit": "1",
    }
    finite = {"id": "f", "type": "finite", "required": True,
              "unit": "1", "source": source}
    ranged = {"id": "r", "type": "range", "required": True,
              "unit": "1", "source": source, "minimum": 0.0, "maximum": 1.0}
    range_summary = {
        "dtype": "float64", "shape": [3], "size": 3,
        "finite_count": 3, "nonfinite_count": 0,
        "minimum": 2.0, "maximum": 3.0, "unit": "1",
        "failing_count": 1, "failing_indices": [[0]],
    }
    monotonic = {
        "id": "m", "type": "monotonic", "required": True,
        "unit": "1", "source": source,
        "direction": "increasing", "absolute_tolerance": 0.0,
    }
    monotonic_summary = {
        "shape": [4], "minimum_step": -2.0, "maximum_step": -1.0,
        "failing_count": 1, "failing_indices": [0], "unit": "1",
    }
    close = {
        "id": "c", "type": "compare", "required": True, "unit": "1",
        "actual": {"artifact": "actual", "pointer": "/x", "unit": "1"},
        "expected": {"artifact": "expected", "pointer": "/x", "unit": "1"},
        "metric": "close", "absolute_tolerance": 0.1, "relative_tolerance": 0.1,
    }
    close_summary = {
        "profile": "pvs-comparison/2", "shape": [2], "unit": "1",
        "l1_error": {"status": "AVAILABLE", "value": 0.0},
        "l2_error": {"status": "AVAILABLE", "value": 0.0},
        "linf_error": {"status": "AVAILABLE", "value": 0.0},
        "gating_error": 0.0, "permitted_error": 0.2,
        "failing_count": 1, "failing_indices": [[0]],
    }
    return [
        ("finite-singleton-extrema", finite, summary, True),
        ("range-all-outside-count", ranged, range_summary, False),
        ("monotonic-all-failing-count", monotonic, monotonic_summary, False),
        ("close-zero-error-failure", close, close_summary, False),
    ]


EXAMPLES = _examples()


@pytest.mark.parametrize(
    "name,declared,observed,claimed_pass", EXAMPLES,
    ids=[example[0] for example in EXAMPLES],
)
def test_reject_impossible_observation(
    name: str, declared: dict[str, Any], observed: dict[str, Any], claimed_pass: bool,
) -> None:
    result = validate_observation(
        deepcopy(declared),
        {"status": "PASS" if claimed_pass else "FAIL", "observed": deepcopy(observed)},
        evidence_schema=EVIDENCE_SCHEMA,
    )
    assert isinstance(result, InvalidObservation), (
        f"{name}: impossible observation accepted as {result!r}"
    )


@pytest.mark.parametrize(
    "name,declared,observed,claimed_pass", EXAMPLES,
    ids=[example[0] for example in EXAMPLES],
)
def test_accept_valid_neighbor(
    name: str, declared: dict[str, Any], observed: dict[str, Any], claimed_pass: bool,
) -> None:
    observed = deepcopy(observed)
    if name == "finite-singleton-extrema":
        observed["maximum"] = observed["minimum"]
    elif name == "range-all-outside-count":
        observed.update(failing_count=3, failing_indices=[[0], [1], [2]])
    elif name == "monotonic-all-failing-count":
        observed.update(failing_count=3, failing_indices=[0, 1, 2])
    else:
        observed.update(failing_count=0, failing_indices=[])
        claimed_pass = True
    result = validate_observation(
        deepcopy(declared),
        {"status": "PASS" if claimed_pass else "FAIL", "observed": observed},
        evidence_schema=EVIDENCE_SCHEMA,
    )
    assert isinstance(result, CompletedObservation), result
    assert result.passed is claimed_pass
