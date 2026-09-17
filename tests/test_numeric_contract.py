"""Boundary vectors and public producer/verifier conformance for 0.2.6."""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import base_case, dump_case

from pvs.api import validate_case
from pvs.errors import ArtifactError, MissingArtifactError
from pvs.jsonutil import MAX_SAFE_INTEGER, load_strict
from pvs.models import Status
from pvs.numeric import numeric_array, stable_norm_v1
from pvs.readers.registry import _component, _reduce
from pvs.verify import verify_target


@pytest.mark.parametrize(
    "value",
    [
        [True, 1.0],
        [[1.0, False], [2, 3]],
        (np.bool_(True), np.float64(1.0)),
        [MAX_SAFE_INTEGER + 1, 1.0],
        [MAX_SAFE_INTEGER + 2, 1.0],
        [-MAX_SAFE_INTEGER - 2, 1.0],
        [np.uint64(2**64 - 1), 1.0],
        np.array([2**64 - 1], dtype=np.uint64),
        np.array([1.0, True], dtype=object),
        ["1", 1.0],
        [None, 1.0],
        [1 + 0j, 1.0],
    ],
)
@pytest.mark.parametrize("operation", [None, "first", "last", "sum", "mean", "min", "max"])
def test_original_scalar_rejection_before_numeric_reduction(value: Any, operation: Any) -> None:
    with pytest.raises(ArtifactError):
        numeric_array(_reduce(value, operation))


@pytest.mark.parametrize("value", [True, False, MAX_SAFE_INTEGER + 2, -MAX_SAFE_INTEGER - 2])
@pytest.mark.parametrize("operation", [None, "first", "last", "sum", "mean", "min", "max"])
def test_component_selection_cannot_erase_invalid_scalar_types(value: Any, operation: Any) -> None:
    selected = _component([[value, 1.0], [value, 2.0]], 0)
    with pytest.raises(ArtifactError):
        numeric_array(_reduce(selected, operation))


@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER, 0])
def test_safe_integer_boundary_survives_mixed_float_component_selection(value: int) -> None:
    selected = _component([[value, "label"], [1.0, False]], 0)
    assert numeric_array(selected).tolist() == [float(value), 1.0]
    assert _reduce(selected, "first") == value


@pytest.mark.parametrize("value", [[], [True, "label", 2**80], [["a", "b"], [None, False]]])
def test_size_remains_a_structural_count(value: Any) -> None:
    assert _reduce(value, "size") == (4 if len(value) == 2 else len(value))


@pytest.mark.parametrize(
    ("values", "l1", "l2", "linf"),
    [
        ([0.0, -0.0], "0x0.0p+0", "0x0.0p+0", "0x0.0p+0"),
        ([3.0, -4.0], "0x1.c000000000000p+2", "0x1.4000000000000p+2", "0x1.0000000000000p+2"),
        (
            [286.0, 337.0],
            "0x1.3780000000000p+9", "0x1.ba004a22ba127p+8", "0x1.5100000000000p+8",
        ),
        (
            [1e-300, 1e-300],
            "0x1.56e1fc2f8f359p-996", "0x1.e4e8d12762226p-997", "0x1.56e1fc2f8f359p-997",
        ),
        (
            [1e300, 1e300],
            "0x1.7e43c8800759cp+997", "0x1.0e4d50f99b211p+997", "0x1.7e43c8800759cp+996",
        ),
    ],
)
def test_norm_v1_fixed_binary64_conformance_vectors(
    values: list[float], l1: str, l2: str, linf: str
) -> None:
    for metric, expected in [("l1", l1), ("l2", l2), ("linf", linf)]:
        assert stable_norm_v1(values, metric).hex() == expected


@pytest.mark.parametrize("metric", ["l1", "l2", "linf", "relative", "close", "absolute"])
def test_generated_literal_results_verify_across_metrics(tmp_path: Path, metric: str) -> None:
    rng = random.Random(260907)
    vectors = [[0.0, 0.0], [286.0, 337.0], [1e-150, -1e-150], [1e150, -1e150]]
    vectors.extend([[rng.uniform(-1e4, 1e4) for _ in range(5)] for _ in range(16)])
    case = base_case()
    checks = []
    for index, values in enumerate(vectors):
        actual: Any = values[0] if metric == "absolute" else values
        expected: Any = values[-1] if metric == "absolute" else list(reversed(values))
        check: dict[str, Any] = {
            "id": f"vector-{index}", "type": "compare", "metric": metric, "unit": "1",
            "actual": {"value": actual, "unit": "1"},
            "expected": {"value": expected, "unit": "1"},
        }
        if metric == "close":
            check.update(absolute_tolerance=0.0, relative_tolerance=0.25)
        else:
            check["tolerance"] = 500.0
            if metric == "relative":
                check["scale_floor"] = 1e-200
        checks.append(check)
    case["checks"] = checks
    case_path = dump_case(tmp_path / "case/pvs.yaml", case)
    (case_path.parent / "result.json").write_text("{}\n", encoding="utf-8")
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    assert outcome.status in {Status.PASS, Status.FAIL}
    assert verify_target(outcome.output_directory).valid


def test_l2_exact_tolerance_boundary_is_not_relaxed(tmp_path: Path) -> None:
    case = base_case()
    norm = float.fromhex("0x1.ba004a22ba127p+8")
    case["checks"] = [
        {
            "id": name, "type": "compare", "metric": "l2", "unit": "1",
            "actual": {"value": [286.0, 337.0], "unit": "1"},
            "expected": {"value": [0.0, 0.0], "unit": "1"}, "tolerance": tolerance,
        }
        for name, tolerance in [("equal", norm), ("below", math.nextafter(norm, 0.0))]
    ]
    case_path = dump_case(tmp_path / "case/pvs.yaml", case)
    (case_path.parent / "result.json").write_text("{}\n", encoding="utf-8")
    outcome = validate_case(case_path, output_dir=tmp_path / "evidence")
    results = load_strict(outcome.evidence_path)["record"]["checks"]
    assert [item["status"] for item in results] == ["PASS", "FAIL"]
    assert verify_target(outcome.output_directory).valid


def test_missing_artifact_exception_carries_identity() -> None:
    error = MissingArtifactError("optional-result", "result.json")
    assert error.artifact_id == "optional-result"
    assert error.path == "result.json"
