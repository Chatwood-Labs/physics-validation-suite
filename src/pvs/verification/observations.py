"""Pure observation consistency and scientific outcome checks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from ..checks.diagnostics import AvailableDiagnostic
from ..checks.inspection import (
    InvalidComparison,
    InvalidOperand,
    LiteralComparison,
    LiteralOperand,
    inspect_comparison,
    inspect_operand,
    require_completed_declaration,
    require_recorded_value_domains,
    require_repeated_source_totals,
)
from ..constants import EVIDENCE_SCHEMA, LEGACY_EVIDENCE_SCHEMA, V2_EVIDENCE_SCHEMA
from ..errors import ArtifactError
from ..numeric import stable_norm_v1
from .comparisons import flat_index, require_self_comparison, validate_comparison
from .norm_bounds import validate_norm_summary
from .summary_constraints import close_absolute_floor, interval_failure_count

# Each supported generation summarizes the evaluated binary64 array, never
# the artifact's storage dtype. New generations must choose this deliberately.
_FINITE_SUMMARY_DTYPES = {
    LEGACY_EVIDENCE_SCHEMA: "float64",
    V2_EVIDENCE_SCHEMA: "float64",
    EVIDENCE_SCHEMA: "float64",
}


def _is_integer(value: Any) -> bool:
    return type(value) is int


def _is_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _shape_size(value: Any) -> int | None:
    if not isinstance(value, list) or any(
        not _is_integer(item) or item < 0 for item in value
    ):
        return None
    return math.prod(value) if value else 1


def _sample_count(value: Any, count: Any) -> bool:
    return (
        isinstance(value, list)
        and _is_integer(count)
        and count >= 0
        and len(value) == min(count, 10)
    )


def _finite_observation_shape(
    observed: dict[str, Any],
    *,
    unit: str,
    extra_keys: set[str] | None = None,
) -> bool:
    extra = extra_keys or set()
    base = {
        "dtype",
        "shape",
        "size",
        "finite_count",
        "nonfinite_count",
        "unit",
    }
    optional = {"minimum", "maximum"}
    if not (base | extra) <= set(observed) or set(observed) - (base | extra | optional):
        return False
    size = observed.get("size")
    finite_count = observed.get("finite_count")
    nonfinite_count = observed.get("nonfinite_count")
    shape_size = _shape_size(observed.get("shape"))
    if not all(_is_integer(value) for value in (size, finite_count, nonfinite_count)):
        return False
    size_value = cast(int, size)
    finite_count_value = cast(int, finite_count)
    nonfinite_count_value = cast(int, nonfinite_count)
    counts_ok = (
        size_value > 0
        and finite_count_value >= 0
        and nonfinite_count_value >= 0
        and finite_count_value + nonfinite_count_value == size_value
        and shape_size == size_value
    )
    extrema_present = "minimum" in observed or "maximum" in observed
    extrema_ok = (
        (finite_count_value == 0 and not extrema_present)
        or (
            finite_count_value > 0
            and set(optional) <= set(observed)
            and _is_number(observed["minimum"])
            and _is_number(observed["maximum"])
            and observed["minimum"] <= observed["maximum"]
            and (finite_count_value != 1 or observed["minimum"] == observed["maximum"])
        )
    )
    return (
        counts_ok
        and extrema_ok
        and observed.get("unit") == unit
    )


def _index_samples(
    value: Any,
    count: Any,
    dimensions: int,
    *,
    scalar_indices: bool = False,
    bounds: list[int] | None = None,
) -> bool:
    if not _sample_count(value, count):
        return False
    if bounds is not None and count == math.prod(bounds):
        expected = (
            list(range(min(count, 10))) if scalar_indices
            else [list(flat_index(i, tuple(bounds))) for i in range(min(count, 10))]
        )
        if value != expected:
            return False
    if scalar_indices:
        upper = bounds[0] if bounds else None
        valid = all(
            _is_integer(item)
            and item >= 0
            and (upper is None or item < upper)
            for item in value
        )
        return valid and value == sorted(set(value))
    valid = all(
        isinstance(index, list)
        and len(index) == dimensions
        and all(
            _is_integer(item)
            and item >= 0
            and (bounds is None or item < bounds[position])
            for position, item in enumerate(index)
        )
        for index in value
    )
    canonical_indices = [tuple(index) for index in value] if valid else []
    return valid and canonical_indices == sorted(set(canonical_indices))


def _literal_scalar(operand: Any) -> float | None:
    value = inspect_operand(operand)
    if isinstance(value, InvalidOperand):
        raise ArtifactError(value.reason)
    if isinstance(value, LiteralOperand) and len(value.values) == 1:
        return value.values[0]
    return None


def _literal_values(operand: Any) -> tuple[list[float], list[int]] | None:
    value = inspect_operand(operand)
    if isinstance(value, InvalidOperand):
        raise ArtifactError(value.reason)
    if isinstance(value, LiteralOperand):
        return list(value.values), list(value.shape)
    return None


def _stable_observed_norm(values: list[float], metric: str) -> float | None:
    try:
        return stable_norm_v1(values, metric)
    except ArtifactError:
        return None


def _literal_comparison(
    declared: dict[str, Any],
) -> tuple[list[float], list[float], list[float], list[int]] | None:
    inspection = inspect_comparison(declared)
    if isinstance(inspection, InvalidComparison):
        raise ArtifactError(inspection.reason)
    if isinstance(inspection, LiteralComparison):
        return (list(inspection.actual.values), list(inspection.expected.values),
                list(inspection.differences), list(inspection.actual.shape))
    return None


def _literal_total(operand: Any) -> float | None:
    values = _literal_values(operand)
    if values is None:
        return None
    try:
        total = math.fsum(values[0])
    except OverflowError:
        return None
    return total if math.isfinite(total) else None


def require_observation_metadata(
    declared: dict[str, Any], result: dict[str, Any], *, evidence_schema: str,
) -> None:
    """Check source relations, count domains and dtype in every generation.

    V1 keeps its historical arithmetic/unit policy; these metadata facts do not
    introduce v2 unit requirements or recompute old scientific observations.
    """

    if result["status"] in {"ERROR", "SKIP"}:
        return
    observed = result["observed"]
    if not isinstance(observed, dict):
        raise ArtifactError("observed result must be an object")
    require_recorded_value_domains(declared, observed)
    require_repeated_source_totals(declared, observed)
    if (
        require_self_comparison(declared, observed, evidence_schema=evidence_schema)
        and result["status"] != "PASS"
    ):
        raise ArtifactError("identical comparison sources require a passing completed result")
    if declared["type"] in {"finite", "range"} and (
        evidence_schema not in _FINITE_SUMMARY_DTYPES
        or observed.get("dtype") != _FINITE_SUMMARY_DTYPES[evidence_schema]
    ):
        raise ArtifactError("finite/range dtype must describe the evaluated float64 array")


def _observed_outcome_impl(
    declared: dict[str, Any],
    result: dict[str, Any],
    *,
    artifact_exists: bool | None = None,
    evidence_schema: str,
) -> tuple[bool, bool | None, str]:
    """Validate recorded arithmetic under the explicit evidence generation."""

    observed = result["observed"]
    status = result["status"]
    if status in {"ERROR", "SKIP"}:
        valid = observed == {}
        return valid, None, "ERROR and SKIP observations must be empty"
    if not isinstance(observed, dict):
        return False, None, "observed result is not an object"
    require_observation_metadata(declared, result, evidence_schema=evidence_schema)
    require_completed_declaration(declared, observed)
    check_type = declared["type"]
    unit = str(declared.get("unit", ""))
    if check_type == "exists":
        valid = (
            set(observed) == {"exists"}
            and isinstance(observed.get("exists"), bool)
            and artifact_exists is not None
            and observed["exists"] is artifact_exists
        )
        return valid, observed.get("exists") if valid else None, "exists requires one boolean"
    if check_type == "schema":
        error_count = observed.get("error_count")
        errors = observed.get("errors")
        valid = (
            set(observed) == {"error_count", "errors"}
            and isinstance(errors, list)
            and _sample_count(errors, error_count)
            and all(
                isinstance(item, dict)
                and set(item) == {"path", "message"}
                and isinstance(item["path"], str)
                and isinstance(item["message"], str)
                for item in errors
            )
        )
        return valid, error_count == 0 if valid else None, "schema requires exact error evidence"
    if check_type == "finite":
        valid = _finite_observation_shape(observed, unit=unit)
        return (
            valid,
            observed.get("nonfinite_count") == 0 if valid else None,
            "finite requires a coherent finite-value summary",
        )
    if check_type == "range":
        valid = _finite_observation_shape(
            observed,
            unit=unit,
            extra_keys={"failing_count", "failing_indices"},
        )
        shape = observed.get("shape")
        valid = valid and isinstance(shape, list) and _index_samples(
            observed.get("failing_indices"),
            observed.get("failing_count"),
            len(shape),
            bounds=shape,
        )
        if valid:
            failing_count = cast(int, observed["failing_count"])
            size = cast(int, observed["size"])
            nonfinite_count = cast(int, observed["nonfinite_count"])
            finite_count = size - nonfinite_count
            valid = nonfinite_count <= failing_count <= size
            if valid and finite_count:
                valid = interval_failure_count(
                    finite_count, observed["minimum"], observed["maximum"],
                    failing_count - nonfinite_count,
                    lower=declared.get("minimum"), upper=declared.get("maximum"),
                    inclusive_lower=declared.get("inclusive_minimum", True),
                    inclusive_upper=declared.get("inclusive_maximum", True),
                )
        return (
            valid,
            observed.get("failing_count") == 0 if valid else None,
            "range requires a coherent failing-index summary",
        )
    if check_type == "monotonic":
        expected_keys = {
            "shape",
            "minimum_step",
            "maximum_step",
            "failing_count",
            "failing_indices",
            "unit",
        }
        shape = observed.get("shape")
        valid = (
            set(observed) == expected_keys
            and isinstance(shape, list)
            and len(shape) == 1
            and _is_integer(shape[0])
            and shape[0] >= 2
            and _is_number(observed.get("minimum_step"))
            and _is_number(observed.get("maximum_step"))
            and observed["minimum_step"] <= observed["maximum_step"]
            and observed.get("unit") == unit
            and _index_samples(
                observed.get("failing_indices"),
                observed.get("failing_count"),
                1,
                scalar_indices=True,
                bounds=[shape[0] - 1],
            )
        )
        if valid:
            shape_values = cast(list[int], shape)
            tolerance = float(declared["absolute_tolerance"])
            direction = declared["direction"]
            minimum_step = observed["minimum_step"]
            maximum_step = observed["maximum_step"]
            failing_count = cast(int, observed["failing_count"])
            lower, upper, inclusive = {
                "increasing": (tolerance, None, False),
                "decreasing": (None, -tolerance, False),
                "nondecreasing": (-tolerance, None, True),
                "nonincreasing": (None, tolerance, True),
            }[direction]
            valid = interval_failure_count(
                shape_values[0] - 1, minimum_step, maximum_step, failing_count,
                lower=lower, upper=upper, inclusive_lower=inclusive, inclusive_upper=inclusive,
            )
        return (
            valid,
            observed.get("failing_count") == 0 if valid else None,
            "monotonic requires a coherent failing-index summary",
        )
    if check_type in {"compare", "reference"} and evidence_schema == EVIDENCE_SCHEMA:
        passed = validate_comparison(declared, observed)
        return True, passed, "comparison profile requires exact finite gating evidence"
    if check_type in {"compare", "reference"}:
        metric = declared["metric"]
        expected_keys = {
            "shape",
            "l1_error",
            "l2_error",
            "linf_error",
            "unit",
            "gating_error",
            "permitted_error",
        }
        shape_size = _shape_size(observed.get("shape"))
        if metric == "relative":
            expected_keys.add("relative_l2_error")
        elif metric in {"l1", "l2", "linf"}:
            expected_keys.add("reference_norm")
        elif metric == "close":
            expected_keys.update({"failing_count", "failing_indices"})
        if shape_size == 1:
            expected_keys.update(
                {"actual", "expected", "absolute_error", "relative_error"}
            )
        allowed_keys = expected_keys | {"relative_error_overflow"}
        valid = (
            shape_size is not None
            and shape_size > 0
            and expected_keys <= set(observed)
            and not set(observed) - allowed_keys
            and observed.get("unit") == unit
            and all(
                _is_number(observed.get(name)) and observed[name] >= 0
                for name in (
                    "l1_error",
                    "l2_error",
                    "linf_error",
                    "gating_error",
                    "permitted_error",
                )
            )
        )
        l1_error = observed.get("l1_error")
        l2_error = observed.get("l2_error")
        linf_error = observed.get("linf_error")
        if valid:
            l1_value = cast(float, l1_error)
            l2_value = cast(float, l2_error)
            linf_value = cast(float, linf_error)
            assert shape_size is not None
            validate_norm_summary(
                shape_size,
                AvailableDiagnostic(l1_value),
                AvailableDiagnostic(l2_value),
                linf_value,
            )
        if shape_size == 1:
            valid = valid and all(
                _is_number(observed.get(name))
                for name in ("actual", "expected", "absolute_error")
            )
            relative_error = observed.get("relative_error")
            valid = valid and (
                relative_error is None
                or (_is_number(relative_error) and relative_error >= 0)
            )
            if valid:
                actual = float(observed["actual"])
                expected = float(observed["expected"])
                absolute_error = abs(actual - expected)
                valid = (
                    observed["absolute_error"] == absolute_error
                    and l1_value == absolute_error
                    and l2_value == absolute_error
                    and linf_value == absolute_error
                )
                actual_literal = _literal_scalar(declared["actual"])
                expected_literal = _literal_scalar(declared["expected"])
                if actual_literal is not None:
                    valid = valid and actual == actual_literal
                if expected_literal is not None:
                    valid = valid and expected == expected_literal
                if expected == 0.0:
                    valid = valid and relative_error is None and (
                        "relative_error_overflow" not in observed
                    )
                else:
                    try:
                        expected_relative = absolute_error / abs(expected)
                    except OverflowError:
                        expected_relative = math.inf
                    if math.isfinite(expected_relative):
                        valid = valid and relative_error == expected_relative and (
                            "relative_error_overflow" not in observed
                        )
                    else:
                        valid = valid and relative_error is None and (
                            observed.get("relative_error_overflow") is True
                        )
        if "relative_error_overflow" in observed:
            valid = valid and (
                shape_size == 1
                and observed["relative_error_overflow"] is True
                and observed.get("relative_error") is None
            )
        if metric == "relative":
            valid = valid and (
                _is_number(observed.get("relative_l2_error"))
                and observed["relative_l2_error"] >= 0
                and observed["relative_l2_error"] == observed["gating_error"]
            )
            if valid and shape_size == 1:
                denominator = max(
                    abs(float(observed["expected"])),
                    float(declared["scale_floor"]),
                )
                valid = observed["gating_error"] == l2_value / denominator
        elif metric in {"l1", "l2", "linf"}:
            valid = valid and (
                _is_number(observed.get("reference_norm"))
                and observed["reference_norm"] >= 0
                and observed["gating_error"] == observed[f"{metric}_error"]
            )
            if valid and shape_size == 1:
                valid = observed["reference_norm"] == abs(float(observed["expected"]))
        elif metric == "close":
            shape = observed.get("shape")
            valid = valid and isinstance(shape, list) and _index_samples(
                observed.get("failing_indices"),
                observed.get("failing_count"),
                len(shape),
                bounds=shape,
            )
            if valid:
                failing_count = cast(int, observed["failing_count"])
                valid = (
                    failing_count <= cast(int, shape_size)
                    and observed["gating_error"] == linf_error
                    and close_absolute_floor(
                        observed["gating_error"], observed["permitted_error"],
                        float(declared["absolute_tolerance"]), failing_count,
                    )
                    and (
                        failing_count != 0
                        or observed["gating_error"] <= observed["permitted_error"]
                    )
                )
                if valid and shape_size == 1:
                    expected_permitted = float(declared["absolute_tolerance"]) + float(
                        declared["relative_tolerance"]
                    ) * abs(float(observed["expected"]))
                    valid = (
                        observed["permitted_error"] == expected_permitted
                        and (failing_count == 0)
                        is (observed["absolute_error"] <= expected_permitted)
                    )
                if valid and float(declared["relative_tolerance"]) == 0.0:
                    valid = observed["permitted_error"] == float(
                        declared["absolute_tolerance"]
                    )
        if metric == "absolute":
            valid = valid and observed["gating_error"] == linf_error
        if metric != "close":
            valid = valid and observed["permitted_error"] == float(declared["tolerance"])
        literal_comparison = _literal_comparison(declared)
        if valid and literal_comparison is not None:
            _actual_values, expected_values, differences, literal_shape = literal_comparison
            l1_literal = _stable_observed_norm(differences, "l1")
            l2_literal = _stable_observed_norm(differences, "l2")
            linf_literal = _stable_observed_norm(differences, "linf")
            literal_norms = {
                "l1_error": l1_literal,
                "l2_error": l2_literal,
                "linf_error": linf_literal,
            }
            valid = (
                all(value is not None for value in literal_norms.values())
                and observed["shape"] == literal_shape
                and all(observed[name] == value for name, value in literal_norms.items())
            )
            if valid and metric == "relative":
                expected_l2 = _stable_observed_norm(expected_values, "l2")
                if expected_l2 is None:
                    valid = False
                else:
                    denominator = max(expected_l2, float(declared["scale_floor"]))
                    valid = observed["gating_error"] == cast(float, l2_literal) / denominator
            elif valid and metric in {"l1", "l2", "linf"}:
                expected_norm = _stable_observed_norm(expected_values, metric)
                valid = (
                    expected_norm is not None
                    and observed["reference_norm"] == expected_norm
                    and observed["gating_error"] == literal_norms[f"{metric}_error"]
                )
            elif valid and metric == "close":
                absolute_tolerance = float(declared["absolute_tolerance"])
                relative_tolerance = float(declared["relative_tolerance"])
                permitted_values = [
                    absolute_tolerance + relative_tolerance * abs(expected)
                    for expected in expected_values
                ]
                valid = all(math.isfinite(value) for value in permitted_values)
                failing_positions = [
                    index
                    for index, (difference, permitted) in enumerate(
                        zip(differences, permitted_values, strict=True)
                    )
                    if abs(difference) > permitted
                ]
                failing_indices = (
                    [[] for _index in failing_positions[:10]]
                    if not literal_shape
                    else [[index] for index in failing_positions[:10]]
                )
                valid = valid and (
                    observed["gating_error"] == linf_literal
                    and observed["permitted_error"] == max(permitted_values)
                    and observed["failing_count"] == len(failing_positions)
                    and observed["failing_indices"] == failing_indices
                )
            elif valid and metric == "absolute":
                valid = observed["gating_error"] == linf_literal
        passed = (
            observed.get("failing_count") == 0
            if metric == "close"
            else observed.get("gating_error", math.inf)
            <= observed.get("permitted_error", -math.inf)
        )
        return valid, passed if valid else None, "comparison requires exact gating evidence"
    if check_type == "conservation":
        expected_keys = {
            "balance",
            "expected",
            "absolute_error",
            "normalization",
            "permitted_error",
            "terms",
            "unit",
        }
        terms = observed.get("terms")
        valid = (
            set(observed) == expected_keys
            and observed.get("unit") == unit
            and all(
                _is_number(observed.get(name))
                for name in (
                    "balance",
                    "expected",
                    "absolute_error",
                    "normalization",
                    "permitted_error",
                )
            )
            and observed.get("absolute_error", -1) >= 0
            and observed.get("normalization", -1) >= 0
            and observed.get("permitted_error", -1) >= 0
            and isinstance(terms, list)
            and len(terms) == len(declared["terms"])
            and all(
                isinstance(term, dict)
                and set(term)
                == {"label", "coefficient", "raw_total", "contribution", "unit"}
                and term["unit"] == unit
                and all(
                    _is_number(term.get(name))
                    for name in ("coefficient", "raw_total", "contribution")
                )
                for term in terms
            )
        )
        if valid:
            term_values = cast(list[dict[str, Any]], terms)
            contributions: list[float] = []
            for term, declaration in zip(
                term_values, declared["terms"], strict=True
            ):
                coefficient = float(declaration["coefficient"])
                raw_total = float(term["raw_total"])
                contribution = coefficient * raw_total
                declared_total = _literal_total(declaration["value"])
                if not math.isfinite(contribution) or (
                    term["label"] != declaration.get("label")
                    or term["coefficient"] != coefficient
                    or (
                        declared_total is not None
                        and raw_total != declared_total
                    )
                    or term["contribution"] != contribution
                ):
                    valid = False
                    break
                contributions.append(contribution)
            if valid:
                try:
                    balance = math.fsum(contributions)
                    absolute_error = abs(balance - float(observed["expected"]))
                    permitted = float(declared["absolute_tolerance"]) + float(
                        declared["relative_tolerance"]
                    ) * float(observed["normalization"])
                except OverflowError:
                    valid = False
                else:
                    valid = (
                        math.isfinite(balance)
                        and math.isfinite(absolute_error)
                        and math.isfinite(permitted)
                        and observed["balance"] == balance
                        and observed["absolute_error"] == absolute_error
                        and observed["permitted_error"] == permitted
                    )
            expected_literal = _literal_scalar(declared["expected"])
            if valid and expected_literal is not None:
                valid = observed["expected"] == expected_literal
            if valid:
                if "normalization" not in declared:
                    valid = observed["normalization"] == abs(float(observed["expected"]))
                else:
                    normalization_literal = _literal_scalar(declared["normalization"])
                    if normalization_literal is not None:
                        valid = observed["normalization"] == abs(normalization_literal)
        passed = observed.get("absolute_error", math.inf) <= observed.get(
            "permitted_error", -math.inf
        )
        return valid, passed if valid else None, "conservation requires exact residual evidence"
    return False, None, f"unsupported check type {check_type!r}"


@dataclass(frozen=True)
class CompletedObservation:
    passed: bool


@dataclass(frozen=True)
class UnevaluatedObservation:
    """An empty ERROR/SKIP observation makes no scientific decision."""


@dataclass(frozen=True)
class InvalidObservation:
    reason: str


ObservationValidation = CompletedObservation | UnevaluatedObservation | InvalidObservation


def validate_observation(
    declared: dict[str, Any], result: dict[str, Any], *, artifact_exists: bool | None = None,
    evidence_schema: str,
) -> ObservationValidation:
    try:
        valid, passed, detail = _observed_outcome_impl(
            declared, result, artifact_exists=artifact_exists, evidence_schema=evidence_schema
        )
    except (ArtifactError, ArithmeticError, KeyError, TypeError, ValueError) as exc:
        return InvalidObservation(f"observation contradicts evaluation eligibility: {exc}")
    if not valid:
        return InvalidObservation(detail)
    if result["status"] in {"ERROR", "SKIP"}:
        return UnevaluatedObservation()
    if passed is None:
        return InvalidObservation("completed observation has no scientific decision")
    return CompletedObservation(passed)
