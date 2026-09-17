"""Adversarial norm summaries and independent producer conformance at bounds."""
from __future__ import annotations

import copy
import math
import random
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import base_case, dump_case
from contract_helpers import rehash_record
from test_review_findings_029 import produce_artifact_comparison

from pvs.api import validate_case
from pvs.checks.diagnostics import AvailableDiagnostic, UnavailableDiagnostic
from pvs.errors import NumericOverflowError
from pvs.jsonutil import load_strict, write_pretty
from pvs.models import Status
from pvs.numeric import stable_norm_v1
from pvs.verification.norm_bounds import (
    FiniteUpperBound,
    OverflowPossible,
    norm_upper_bounds,
    validate_norm_summary,
)
from pvs.verify import verify_target


@pytest.mark.parametrize("generation", [2, 3])
@pytest.mark.parametrize(("field", "number"), [
    ("l1_error", math.nextafter(2.0, math.inf)),
    ("l2_error", 1.5),  # Below L1; above the independently necessary L2 bound.
    ("l2_error", math.nextafter(math.sqrt(2.0), math.inf)),
])
def test_individual_upper_norm_contradictions_are_rejected_after_rehash(
    tmp_path: Path, generation: int, field: str, number: float,
) -> None:
    envelope = produce_artifact_comparison(tmp_path, [1.0, 1.0])
    observed = envelope["record"]["checks"][0]["observed"]
    if generation == 2:
        envelope["schema"] = "pvs-evidence/2"
        envelope["record"]["pvs"]["evidence_schema"] = "pvs-evidence/2"
        envelope["record"]["pvs"].pop("comparison_profile")
        envelope["record"]["package"]["manifest_schema"] = "pvs-manifest/2"
        envelope["record"]["package"]["report_profiles"] = {
            "html": "pvs-html/2", "pdf": "pvs-pdf/2",
        }
        observed.pop("profile")
        for key in ("l1_error", "l2_error", "linf_error", "reference_norm"):
            observed[key] = observed[key]["value"]
    # This is a synthetic v2 representation, not a frozen historical fixture.
    assert verify_target(rehash_record(envelope, tmp_path / "control.json")).valid
    observed[field] = number if generation == 2 else {"status": "AVAILABLE", "value": number}
    assert not verify_target(rehash_record(envelope, tmp_path / "forged.json")).valid


@pytest.mark.parametrize("field", ["l1_error", "l2_error"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_zero_requires_available_exact_zero_for_each_norm(
    tmp_path: Path, field: str, unavailable: bool,
) -> None:
    envelope = produce_artifact_comparison(tmp_path, [0.0, -0.0])
    envelope["record"]["checks"][0]["observed"][field] = (
        {"status": "UNAVAILABLE", "reason": "OVERFLOW"} if unavailable
        else {"status": "AVAILABLE", "value": math.ulp(0.0)}
    )
    assert not verify_target(rehash_record(envelope, tmp_path / "forged.json")).valid


@pytest.mark.parametrize(("size", "maximum", "l1_hex", "l2_hex"), [
    (2, 1.0, "0x1.0000000000000p+1", "0x1.6a09e667f3bcdp+0"),
    (3, 1.0, "0x1.8000000000000p+1", "0x1.bb67ae8584cabp+0"),
    (4, 1.0, "0x1.0000000000000p+2", "0x1.0000000000000p+1"),
    (2, math.ulp(0.0), "0x0.0000000000002p-1022", "0x0.0000000000002p-1022"),
    (1, sys.float_info.max, "0x1.fffffffffffffp+1023", "0x1.fffffffffffffp+1023"),
])
def test_frozen_outward_bound_vectors(
    size: int, maximum: float, l1_hex: str, l2_hex: str,
) -> None:
    l1, l2 = norm_upper_bounds(size, maximum)
    assert isinstance(l1, FiniteUpperBound) and l1.value.hex() == l1_hex
    assert isinstance(l2, FiniteUpperBound) and l2.value.hex() == l2_hex


@pytest.mark.parametrize("size", [2**53 - 1, 2**53 + 1, 2**54 + 3, 10**100, 10**400])
def test_large_recorded_sizes_do_not_round_down_or_allocate_arrays(size: int) -> None:
    maximum = math.ulp(0.0)
    l1, l2 = norm_upper_bounds(size, maximum)
    assert isinstance(l1, FiniteUpperBound)
    assert Fraction(l1.value) >= size * Fraction(maximum)
    assert Fraction(math.nextafter(l1.value, 0)) < size * Fraction(maximum)
    if size > sys.float_info.max:
        # Even with a tiny final scale, the sum of scaled squares could overflow.
        assert isinstance(l2, OverflowPossible)
    else:
        assert isinstance(l2, FiniteUpperBound)
        assert Fraction(l2.value) ** 2 >= size * Fraction(maximum) ** 2


@pytest.mark.parametrize("size", [2, 3, 4, 7, 16, 33, 257])
def test_both_sides_of_each_norm_overflow_boundary(size: int) -> None:
    for metric, divisor in (("l1", size), ("l2", math.sqrt(size))):
        centre = sys.float_info.max / divisor
        maxima = [centre]
        for direction in (0.0, math.inf):
            value = centre
            for _ in range(8):
                value = math.nextafter(value, direction)
                maxima.append(value)
        finite = overflow = 0
        for maximum in maxima:
            values = [maximum] * size
            diagnostics = []
            for name in ("l1", "l2"):
                # Independent frozen scalar profile; OverflowError and infinity
                # are handled as the producer must handle them.
                try:
                    result = math.fsum(values) if name == "l1" else (
                        maximum * math.sqrt(math.fsum(
                            (v / maximum) * (v / maximum) for v in values
                        ))
                    )
                except OverflowError:
                    result = math.inf
                diagnostics.append(
                    AvailableDiagnostic(result) if math.isfinite(result)
                    else UnavailableDiagnostic("OVERFLOW")
                )
                if name == metric:
                    finite += math.isfinite(result)
                    overflow += not math.isfinite(result)
            validate_norm_summary(size, diagnostics[0], diagnostics[1], maximum)
        assert finite and overflow, "The test must straddle the actual profile overflow boundary"


@pytest.mark.parametrize("layout", ["array", "strided", "readonly", "iterator"])
def test_generated_profile_outputs_always_satisfy_summary_bounds(layout: str) -> None:
    rng = random.Random(300908)
    vectors = [
        [0.0, -0.0], [286.0, 337.0], [sys.float_info.max, math.ulp(0.0)],
        [1e308, 1e308], [1.7e308, 1.7e308], [math.ulp(0.0)] * 17,
        [float.fromhex("0x0.fffffffffffffp-1022"), float.fromhex("0x1p-1022")],
    ]
    for _ in range(200):
        size = rng.choice([1, 2, 3, 7, 31, 128])
        values = [math.ldexp(rng.uniform(-1, 1), rng.randint(-1074, 1023)) for _ in range(size)]
        vectors.extend([values, [max(abs(v) for v in values)] * size])
    for values in vectors:
        array = np.asarray(values, dtype=np.float64)
        if layout == "strided":
            array = np.repeat(array, 2)[::2]
        if layout == "readonly":
            array.flags.writeable = False
        before = array.tobytes()
        diagnostics = []
        for metric in ("l1", "l2"):
            absolute = [abs(v) for v in values]
            maximum = max(absolute)
            try:
                reference = math.fsum(absolute) if metric == "l1" else 0.0 if maximum == 0 else (
                    maximum * math.sqrt(math.fsum((v / maximum) * (v / maximum) for v in absolute))
                )
            except OverflowError:
                reference = math.inf
            if math.isfinite(reference):
                actual = stable_norm_v1(iter(values) if layout == "iterator" else array, metric)
                assert actual.hex() == reference.hex()
                diagnostics.append(AvailableDiagnostic(actual))
            else:
                with pytest.raises(NumericOverflowError):
                    stable_norm_v1(iter(values) if layout == "iterator" else array, metric)
                diagnostics.append(UnavailableDiagnostic("OVERFLOW"))
        validate_norm_summary(
            len(values), diagnostics[0], diagnostics[1], max(abs(v) for v in values)
        )
        assert array.tobytes() == before


@pytest.mark.parametrize("check_type", ["compare", "reference"])
@pytest.mark.parametrize("metric", ["absolute", "l1", "l2", "linf", "relative", "close"])
def test_artifact_producer_outcomes_verify_across_metrics_and_boundaries(
    tmp_path: Path, check_type: str, metric: str,
) -> None:
    vectors = [[0.0, -0.0], [1, 1], [1, 2, 3], [math.ulp(0.0)] * 3,
               [1e308, 1e308], [1.7e308, 1.7e308], [sys.float_info.max, 0.0]]
    case = base_case()
    case["checks"] = []
    if check_type == "reference":
        case["references"] = [{
            "id": "zero", "type": "analytic", "citation": "Synthetic zero reference",
            "locator": "Zero array in result.json",
        }]
    data: dict[str, Any] = {}
    for index, vector in enumerate(vectors):
        data[str(index)] = {
            "a": vector[0] if metric == "absolute" else vector,
            "e": 0.0 if metric == "absolute" else [0.0] * len(vector),
        }
        for boundary in ("equal", "below"):
            tolerance = max(vector)
            if boundary == "below" and tolerance:
                tolerance = math.nextafter(tolerance, 0)
            check = {
                "id": f"norm-{index}-{boundary}", "type": check_type, "metric": metric, "unit": "1",
                "actual": {"artifact": "result", "pointer": f"/{index}/a", "unit": "1"},
                "expected": {"artifact": "result", "pointer": f"/{index}/e", "unit": "1"},
            }
            if check_type == "reference":
                check["reference_id"] = "zero"
            if metric == "close":
                check.update(absolute_tolerance=tolerance, relative_tolerance=0)
            else:
                check["tolerance"] = tolerance
                if metric == "relative":
                    check["scale_floor"] = 1.0
            case["checks"].append(check)
    case_path = dump_case(tmp_path / "pvs.yaml", case)
    write_pretty(tmp_path / "result.json", data)
    outcome = validate_case(case_path, output_dir=tmp_path / "package")
    assert verify_target(outcome.output_directory).valid
    envelope = load_strict(outcome.evidence_path)
    checks = envelope["record"]["checks"]
    if metric in {"absolute", "linf", "close"}:
        assert checks[2]["status"] == Status.PASS.value
        assert checks[3]["status"] == Status.FAIL.value
    for index, check in enumerate(checks):
        if check["status"] in {"PASS", "FAIL", "WARN"}:
            forged = copy.deepcopy(envelope)
            observed = forged["record"]["checks"][index]["observed"]
            observed["gating_error"] = math.nextafter(observed["gating_error"], 0.0)
            if observed["gating_error"] != check["observed"]["gating_error"]:
                assert not verify_target(rehash_record(forged, tmp_path / "forged.json")).valid
