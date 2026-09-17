"""Finite scientific gates, explicit diagnostics, and exact adversarial verification."""

from __future__ import annotations

import copy
import math
import random
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import base_case, dump_case
from contract_helpers import rehash_record
from pypdf import PdfReader

from pvs.api import validate_case
from pvs.checks.engine import evaluate_checks
from pvs.jsonutil import load_strict
from pvs.models import Status
from pvs.verification.observations import CompletedObservation, validate_observation
from pvs.verify import verify_target

OVERFLOW = {"status": "UNAVAILABLE", "reason": "OVERFLOW"}


@pytest.mark.parametrize(
    ("metric", "field"),
    [
        ("l1", "l1_error"),
        ("l2", "l2_error"),
        ("linf", "linf_error"),
        ("relative", "l2_error"),
        ("relative", "reference_norm"),
        ("close", "linf_error"),
        ("absolute", "linf_error"),
    ],
)
def test_selected_quantities_must_be_available_even_without_artifact_values(
    metric: str,
    field: str,
) -> None:
    check = comparison(
        3 if metric == "absolute" else [3, 4], 0 if metric == "absolute" else [0, 0], metric, 10
    )
    result = evaluate_checks([check], {})[0].as_dict()
    for name in ("actual", "expected"):
        check[name] = {"artifact": name, "unit": "1"}
    assert isinstance(
        validate_observation(check, result, evidence_schema="pvs-evidence/3"), CompletedObservation
    )
    result["observed"][field] = OVERFLOW
    assert not isinstance(
        validate_observation(check, result, evidence_schema="pvs-evidence/3"), CompletedObservation
    )


def comparison(actual: Any, expected: Any, metric: str, tolerance: float = 0) -> dict[str, Any]:
    check = {
        "id": "overflow-policy",
        "type": "compare",
        "unit": "1",
        "metric": metric,
        "actual": {"value": actual, "unit": "1"},
        "expected": {"value": expected, "unit": "1"},
    }
    if metric == "close":
        check.update(absolute_tolerance=tolerance, relative_tolerance=0)
    else:
        check["tolerance"] = tolerance
        if metric == "relative":
            check["scale_floor"] = 1e-300
    return check


def produce(tmp_path: Path, check: dict[str, Any], *, reports: bool = False):
    case = base_case()
    case["checks"] = [check]
    case["package"].update(html=reports, pdf=reports)
    path = dump_case(tmp_path / "case/pvs.yaml", case)
    (path.parent / "result.json").write_text("{}\n", encoding="utf-8")
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert verify_target(outcome.output_directory).valid
    return outcome, load_strict(outcome.evidence_path)


@pytest.mark.parametrize(
    ("actual", "expected", "metric", "tolerance", "field"),
    [
        ([1e308, 1e308], [0.0, 0.0], "linf", 1e308, "l1_error"),
        ([1e308, 1e308], [0.0, 0.0], "l2", 1.5e308, "l1_error"),
        ([1e308, 1e308], [1e308, 1e308], "l1", 0.0, "reference_norm"),
        ([1.7e308, 1.7e308], [1.7e308, 1.7e308], "l2", 0.0, "reference_norm"),
        ([1.7e308, 1.7e308], [0.0, 0.0], "linf", 1.7e308, "l2_error"),
        ([1.7e308, 1.7e308], [0.0, 0.0], "close", 1.7e308, "l2_error"),
    ],
)
def test_finite_gate_with_unavailable_diagnostic_publishes_verified_pass(
    tmp_path: Path,
    actual: list[float],
    expected: list[float],
    metric: str,
    tolerance: float,
    field: str,
) -> None:
    outcome, envelope = produce(tmp_path, comparison(actual, expected, metric, tolerance))
    assert outcome.status is Status.PASS
    assert outcome.finding_id is not None
    assert envelope["schema"] == "pvs-evidence/3"
    assert envelope["record"]["pvs"]["comparison_profile"] == "pvs-comparison/2"
    observed = envelope["record"]["checks"][0]["observed"]
    assert observed["profile"] == "pvs-comparison/2"
    assert observed[field] == OVERFLOW
    assert math.isfinite(observed["gating_error"])


@pytest.mark.parametrize("metric", ["l1", "l2", "relative"])
def test_overflowing_selected_quantity_remains_verified_error(tmp_path: Path, metric: str) -> None:
    actual, expected = [1.7e308, 1.7e308], [0.0, 0.0]
    if metric == "relative":
        expected = list(actual)  # A zero numerator still requires a finite denominator.
    outcome, envelope = produce(tmp_path, comparison(actual, expected, metric, 1.7e308))
    assert outcome.status is Status.ERROR
    assert outcome.finding_id is None
    result = envelope["record"]["checks"][0]
    assert result["observed"] == result["criterion"] == {}
    assert "overflow" in result["summary"]


@pytest.mark.parametrize("required", [True, False])
def test_unavailable_diagnostics_never_relax_the_selected_tolerance(tmp_path: Path, required: bool):
    check = comparison([1e308, 1e308], [0, 0], "linf", math.nextafter(1e308, 0))
    check["required"] = required
    outcome, envelope = produce(tmp_path, check)
    assert outcome.status is (Status.FAIL if required else Status.WARN)
    assert envelope["record"]["checks"][0]["observed"]["l1_error"] == OVERFLOW


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("l1_error", None),
        ("l1_error", 0),
        ("l1_error", {"status": "AVAILABLE"}),
        ("l1_error", {"status": "AVAILABLE", "value": 0, "reason": "OVERFLOW"}),
        ("l1_error", {"status": "UNAVAILABLE", "reason": "UNKNOWN"}),
        ("l1_error", {"status": "UNAVAILABLE", "reason": "ZERO_REFERENCE"}),
        ("l1_error", {"status": "AVAILABLE", "value": False}),
        ("l1_error", {"status": "AVAILABLE", "value": -1}),
        ("l1_error", {"status": "AVAILABLE", "value": 1e308}),
        ("linf_error", OVERFLOW),
        ("reference_norm", OVERFLOW),
        ("profile", "pvs-comparison/1"),
        ("gating_error", math.nextafter(1e308, 0)),
    ],
)
def test_rehashed_false_or_malformed_diagnostics_are_rejected(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    _, envelope = produce(tmp_path, comparison([1e308, 1e308], [0, 0], "linf", 1e308))
    envelope["record"]["checks"][0]["observed"][field] = value
    assert not verify_target(rehash_record(envelope, tmp_path / "forged.json")).valid


@pytest.mark.parametrize("known", ["both", "actual", "expected", "neither"])
def test_false_error_overflow_is_rejected_with_partial_or_unavailable_values(known: str) -> None:
    check = comparison([1, 2], [0, 0], "linf", 2)
    result = evaluate_checks([check], {})[0].as_dict()
    for name in ("actual", "expected"):
        if known not in {"both", name}:
            check[name] = {"artifact": name, "pointer": "/values", "unit": "1"}
    assert isinstance(
        validate_observation(check, result, evidence_schema="pvs-evidence/3"), CompletedObservation
    )
    result["observed"]["l1_error"] = OVERFLOW
    assert not isinstance(
        validate_observation(check, result, evidence_schema="pvs-evidence/3"), CompletedObservation
    )


@pytest.mark.parametrize(
    ("actual", "expected", "reason"),
    [
        (1, 0, "ZERO_REFERENCE"),
        (10, 1e-308, "OVERFLOW"),
    ],
)
def test_scalar_relative_diagnostic_has_explicit_unavailability(
    tmp_path: Path,
    actual: float,
    expected: float,
    reason: str,
) -> None:
    outcome, envelope = produce(tmp_path, comparison(actual, expected, "absolute", 10))
    assert outcome.status is Status.PASS
    assert envelope["record"]["checks"][0]["observed"]["relative_error"] == {
        "status": "UNAVAILABLE",
        "reason": reason,
    }


def test_reports_explain_unavailability_and_replay_exactly(tmp_path: Path) -> None:
    outcome, _ = produce(tmp_path, comparison([1e308, 1e308], [0, 0], "linf", 1e308), reports=True)
    html = (outcome.output_directory / "report.html").read_text(encoding="utf-8")
    pdf = "\n".join(
        page.extract_text() for page in PdfReader(outcome.output_directory / "report.pdf").pages
    )
    for report in (html, pdf):
        assert "Unavailable (binary64 overflow)" in report
        assert "supplementary diagnostic" in report
    assert verify_target(outcome.output_directory).valid


@pytest.mark.parametrize("metric", ["absolute", "l1", "l2", "linf", "relative", "close"])
def test_generated_extreme_values_produce_verifiable_exact_outcomes(tmp_path: Path, metric: str):
    rng = random.Random(290907)
    exponents = [-1074, -1022, -512, -1, 0, 511, 1023]
    vectors = [[0.0, -0.0], [sys.float_info.max, sys.float_info.max]]
    vectors.extend(
        [
            [math.ldexp(rng.uniform(-1, 1), exponent) for _ in range(4)]
            for exponent in exponents
            for _ in range(5)
        ]
    )
    case = base_case()
    checks = []
    for index, vector in enumerate(vectors):
        a: Any = vector[0] if metric == "absolute" else vector
        e: Any = vector[-1] if metric == "absolute" else list(reversed(vector))
        check = comparison(a, e, metric, 0)
        check["id"] = f"extreme-{index}"
        checks.append(check)
    case["checks"] = checks
    path = dump_case(tmp_path / "case/pvs.yaml", case)
    (path.parent / "result.json").write_text("{}\n", encoding="utf-8")
    outcome = validate_case(path, output_dir=tmp_path / "package")
    assert verify_target(outcome.output_directory).valid
    envelope = load_strict(outcome.evidence_path)
    for index, result in enumerate(envelope["record"]["checks"]):
        if result["status"] in {"PASS", "FAIL", "WARN"}:
            mutated = copy.deepcopy(envelope)
            observed = mutated["record"]["checks"][index]["observed"]
            observed["gating_error"] = math.nextafter(observed["gating_error"], math.inf)
            assert not verify_target(rehash_record(mutated, tmp_path / "one-ulp.json")).valid
