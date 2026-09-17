"""Contradictions survive rehashing but must fail semantic verification."""

from __future__ import annotations

import copy
import hashlib
import itertools
import math
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case, dump_case
from contract_helpers import rehash_record

from pvs.api import validate_case
from pvs.jsonutil import load_strict
from pvs.verification.summary_constraints import interval_failure_count
from pvs.verify import verify_target

CASES = [
    ("finite-singleton", "finite", [0.0], {}, {"maximum": 1.0}),
    ("one-finite-among-nonfinite", "finite", [math.nan, 0.0, math.inf], {}, {"maximum": 1.0}),
    (
        "range-above",
        "range",
        [2, 2.5, 3],
        {"minimum": 0, "maximum": 1},
        {"failing_count": 1, "failing_indices": [[0]]},
    ),
    (
        "range-below",
        "range",
        [-3, -2, -1],
        {"minimum": 0, "maximum": 1},
        {"failing_count": 1, "failing_indices": [[0]]},
    ),
    (
        "range-only-nonfinite-fails",
        "range",
        [math.nan, 0.5, 1],
        {"minimum": 0, "maximum": 1},
        {"failing_count": 2, "failing_indices": [[0], [1]]},
    ),
    (
        "range-two-failing-extrema",
        "range",
        [-1, 0.5, 2],
        {"minimum": 0, "maximum": 1},
        {"failing_count": 1, "failing_indices": [[0]]},
    ),
    (
        "range-passing-extremum",
        "range",
        [0, 2, 3],
        {"minimum": 0, "maximum": 1},
        {"failing_count": 3, "failing_indices": [[0], [1], [2]]},
    ),
    (
        "range-exclusive-boundary",
        "range",
        [1, 1, 1],
        {"maximum": 1, "inclusive_maximum": False},
        {"failing_count": 1, "failing_indices": [[0]]},
    ),
    (
        "range-all-failing-prefix",
        "range",
        [2] * 12,
        {"maximum": 1},
        {"failing_indices": [[i] for i in range(1, 11)]},
    ),
    (
        "increasing-all-failing",
        "monotonic",
        [3, 2, 1, 0],
        {"direction": "increasing", "absolute_tolerance": 0},
        {"failing_count": 1, "failing_indices": [0]},
    ),
    (
        "decreasing-all-failing",
        "monotonic",
        [0, 1, 2, 3],
        {"direction": "decreasing", "absolute_tolerance": 0},
        {"failing_count": 1, "failing_indices": [0]},
    ),
    (
        "nondecreasing-all-failing",
        "monotonic",
        [3, 2, 1, 0],
        {"direction": "nondecreasing", "absolute_tolerance": 0.5},
        {"failing_count": 1, "failing_indices": [0]},
    ),
    (
        "nonincreasing-all-failing",
        "monotonic",
        [0, 1, 2, 3],
        {"direction": "nonincreasing", "absolute_tolerance": 0.5},
        {"failing_count": 1, "failing_indices": [0]},
    ),
    (
        "single-step-extrema",
        "monotonic",
        [1, 2],
        {"direction": "nondecreasing", "absolute_tolerance": 0},
        {"maximum_step": 2.0},
    ),
    (
        "monotonic-passing-extremum",
        "monotonic",
        [0, 1, 0, -1],
        {"direction": "increasing", "absolute_tolerance": 0},
        {"failing_count": 3, "failing_indices": [0, 1, 2]},
    ),
    (
        "monotonic-all-failing-prefix",
        "monotonic",
        list(range(13)),
        {"direction": "decreasing", "absolute_tolerance": 0},
        {"failing_indices": list(range(1, 11))},
    ),
    (
        "close-zero",
        "compare",
        [0, 0],
        {"metric": "close", "absolute_tolerance": 0.1, "relative_tolerance": 0.1},
        {"failing_count": 1, "failing_indices": [[0]]},
    ),
    (
        "close-exact-absolute-floor",
        "compare",
        [0.1, 0],
        {"metric": "close", "absolute_tolerance": 0.1, "relative_tolerance": 0.1},
        {"failing_count": 1, "failing_indices": [[0]]},
    ),
    (
        "close-permitted-below-floor",
        "reference",
        [0, 0],
        {"metric": "close", "absolute_tolerance": 0.1, "relative_tolerance": 0.1},
        {"permitted_error": math.nextafter(0.1, 0)},
    ),
    (
        "close-all-failing-prefix",
        "compare",
        [2] * 12,
        {"metric": "close", "absolute_tolerance": 0.1, "relative_tolerance": 0.1},
        {"failing_indices": [[i] for i in range(1, 11)]},
    ),
]


def _produce(
    root: Path,
    kind: str,
    values: list[float],
    settings: dict[str, Any],
    required: bool,
) -> dict[str, Any]:
    root.mkdir()
    case = base_case()
    case["artifacts"]["result"].update(path="result.csv", format="csv")
    (root / "result.csv").write_text("x\n" + "\n".join(map(str, values)) + "\n")
    source = {"artifact": "result", "column": "x", "unit": "1"}
    check = dict(id="observed", type=kind, required=required, unit="1", **settings)
    if kind in {"compare", "reference"}:
        (root / "expected.csv").write_text("x\n" + "0\n" * len(values))
        case["artifacts"]["expected"] = {
            "path": "expected.csv",
            "format": "csv",
            "role": "reference",
            "expected_sha256": hashlib.sha256((root / "expected.csv").read_bytes()).hexdigest(),
        }
        check.update(actual=source, expected={"artifact": "expected", "column": "x", "unit": "1"})
        if kind == "reference":
            check["reference_id"] = "anchor"
            case["references"] = [
                {
                    "id": "anchor",
                    "type": "analytic",
                    "citation": "Zero vector",
                    "locator": "Declared zero reference",
                }
            ]
    else:
        check["source"] = source
    case["checks"] = [check]
    outcome = validate_case(dump_case(root / "pvs.yaml", case), output_dir=root / "package")
    assert verify_target(outcome.output_directory).valid
    envelope = load_strict(outcome.evidence_path)
    assert envelope["record"]["checks"][0]["status"] in {"PASS", "FAIL", "WARN"}
    return envelope


def _legacy(envelope: dict[str, Any]) -> None:
    """A synthetic v2 control; frozen historical samples are tested separately."""
    envelope["schema"] = "pvs-evidence/2"
    record = envelope["record"]
    record["pvs"]["evidence_schema"] = "pvs-evidence/2"
    record["pvs"].pop("comparison_profile")
    record["package"]["manifest_schema"] = "pvs-manifest/2"
    record["package"]["report_profiles"] = {"html": "pvs-html/2", "pdf": "pvs-pdf/2"}
    observed = record["checks"][0]["observed"]
    if "profile" in observed:
        observed.pop("profile")
        for field in ("l1_error", "l2_error", "linf_error"):
            observed[field] = observed[field]["value"]


@pytest.mark.parametrize("generation", [2, 3])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("name,kind,values,settings,changes", CASES, ids=[c[0] for c in CASES])
def test_rehashed_summary_contradictions_are_rejected(
    tmp_path: Path,
    generation: int,
    required: bool,
    name: str,
    kind: str,
    values: list[float],
    settings: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    envelope = _produce(tmp_path / "case", kind, values, settings, required)
    if generation == 2:
        _legacy(envelope)
    assert verify_target(rehash_record(envelope, tmp_path / "control.json")).valid
    result = envelope["record"]["checks"][0]
    result["observed"].update(copy.deepcopy(changes))
    if result["observed"].get("failing_count", 0):
        result["status"] = "FAIL" if required else "WARN"
    verification = verify_target(rehash_record(envelope, tmp_path / "forged.json"))
    assert not verification.valid, name
    checks = {check["name"]: check["status"] for check in verification.checks}
    assert checks["canonical record digest"] == "PASS"
    assert checks["Scientific Finding digest"] == "PASS"
    assert checks["check observation contract"] == "FAIL"


@pytest.mark.parametrize(
    "inclusive_lower,inclusive_upper", itertools.product([False, True], repeat=2)
)
def test_finite_interval_envelopes_accept_enumerated_real_vectors(
    inclusive_lower: bool,
    inclusive_upper: bool,
) -> None:
    values = [-1.0, math.nextafter(0.0, -math.inf), 0.0, math.nextafter(0.0, math.inf), 1.0]
    for size in range(1, 5):
        for vector in itertools.product(values, repeat=size):
            for lower, upper in ((None, 0.0), (0.0, None), (-1.0, 1.0), (0.0, 0.0)):
                passing = [
                    (lower is None or (v >= lower if inclusive_lower else v > lower))
                    and (upper is None or (v <= upper if inclusive_upper else v < upper))
                    for v in vector
                ]
                assert interval_failure_count(
                    size,
                    min(vector),
                    max(vector),
                    size - sum(passing),
                    lower=lower,
                    upper=upper,
                    inclusive_lower=inclusive_lower,
                    inclusive_upper=inclusive_upper,
                )
