"""Evaluation of the deliberately small check vocabulary."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.exceptions import NoSuchResource

from ..artifacts import ResolvedArtifact
from ..errors import ArtifactError, MissingArtifactError
from ..formats import FormatSupportError, required_format_checker
from ..models import CheckResult, Status
from ..numeric import stable_norm_v1
from ..readers import ArtifactReader
from .contracts import expected_criterion as expected_criterion
from .diagnostics import (
    COMPARISON_PROFILE,
    AvailableDiagnostic,
    norm_diagnostic,
    relative_diagnostic,
)
from .metrics import finite_summary, numeric_array
from .schema_policy import validate_local_schema_references, validate_schema_policy
from .units import _comparison_unit_contract, _conservation_unit_contract, _source_unit_contract


def _status(passed: bool, required: bool) -> Status:
    if passed:
        return Status.PASS
    return Status.FAIL if required else Status.WARN


def _require_finite_declaration_numbers(value: Any, location: str = "check") -> None:
    """Defend the evaluator even when called without case-schema validation."""

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        try:
            finite = math.isfinite(value)
        except (OverflowError, TypeError, ValueError):
            finite = False
        if not finite:
            raise ArtifactError(f"{location} contains a non-finite numeric declaration")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_finite_declaration_numbers(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require_finite_declaration_numbers(item, f"{location}.{key}")


def _result(
    declaration: dict[str, Any],
    passed: bool,
    summary: str,
    *,
    observed: dict[str, Any] | None = None,
    criterion: Mapping[str, object] | None = None,
) -> CheckResult:
    required = bool(declaration.get("required", True))
    return CheckResult(
        id=str(declaration["id"]),
        type=str(declaration["type"]),
        status=_status(passed, required),
        required=required,
        summary=summary,
        observed=observed or {},
        criterion=dict(criterion) if criterion is not None else {},
        reference_id=declaration.get("reference_id"),
    )


def _operand(reader: ArtifactReader, value: Any) -> Any:
    if isinstance(value, dict) and "artifact" in value:
        return reader.select(value)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    raise ArtifactError("numeric operand must be a source or a literal value with a unit")


def _check_exists(
    declaration: dict[str, Any],
    reader: ArtifactReader,
    artifacts: dict[str, ResolvedArtifact],
) -> CheckResult:
    del reader
    artifact = artifacts[str(declaration["artifact"])]
    exists = artifact.path.is_file()
    return _result(
        declaration,
        exists,
        "artifact exists" if exists else "artifact does not exist",
        observed={"exists": exists},
        criterion=expected_criterion(declaration),
    )


def _check_schema(
    declaration: dict[str, Any],
    reader: ArtifactReader,
    artifacts: dict[str, ResolvedArtifact],
) -> CheckResult:
    del artifacts
    actual = reader.read(str(declaration["artifact"]))
    schema = reader.read(str(declaration["schema_artifact"]))
    if not isinstance(schema, dict):
        raise ArtifactError("JSON Schema artifact must contain an object")
    Draft202012Validator.check_schema(schema)
    validate_schema_policy(schema)

    def reject_retrieval(uri: str) -> Any:
        raise NoSuchResource(uri)

    try:
        format_checker = required_format_checker()
    except FormatSupportError as exc:
        raise ArtifactError(str(exc)) from exc
    # ``referencing.Registry`` is attrs-generated; its distributed type
    # information does not expose the runtime-supported constructor keyword.
    registry = Registry(retrieve=reject_retrieval)  # type: ignore[call-arg]
    validate_local_schema_references(schema, registry)
    validator = Draft202012Validator(schema, format_checker=format_checker, registry=registry)
    errors = sorted(validator.iter_errors(actual), key=lambda item: list(item.absolute_path))
    samples = []
    for error in errors[:10]:
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        samples.append({"path": path, "message": error.message})
    return _result(
        declaration,
        not errors,
        "JSON document satisfies schema" if not errors else "JSON document violates schema",
        observed={"error_count": len(errors), "errors": samples},
        criterion=expected_criterion(declaration),
    )


def _check_finite(
    declaration: dict[str, Any],
    reader: ArtifactReader,
    artifacts: dict[str, ResolvedArtifact],
) -> CheckResult:
    del artifacts
    unit = _source_unit_contract(declaration)
    array = numeric_array(reader.select(declaration["source"]))
    observed = finite_summary(array)
    observed["unit"] = unit
    passed = observed["nonfinite_count"] == 0
    return _result(
        declaration,
        passed,
        "all values are finite" if passed else "one or more values are non-finite",
        observed=observed,
        criterion=expected_criterion(declaration),
    )


def _check_range(
    declaration: dict[str, Any],
    reader: ArtifactReader,
    artifacts: dict[str, ResolvedArtifact],
) -> CheckResult:
    del artifacts
    unit = _source_unit_contract(declaration)
    array = numeric_array(reader.select(declaration["source"]))
    observed = finite_summary(array)
    observed["unit"] = unit
    minimum = declaration.get("minimum")
    maximum = declaration.get("maximum")
    inclusive_minimum = bool(declaration.get("inclusive_minimum", True))
    inclusive_maximum = bool(declaration.get("inclusive_maximum", True))
    mask = np.isfinite(array)
    if minimum is not None:
        mask &= array >= minimum if inclusive_minimum else array > minimum
    if maximum is not None:
        mask &= array <= maximum if inclusive_maximum else array < maximum
    failing = np.argwhere(~mask)
    observed["failing_count"] = int(failing.shape[0])
    observed["failing_indices"] = [index.tolist() for index in failing[:10]]
    passed = failing.shape[0] == 0
    return _result(
        declaration,
        passed,
        "all values are within range" if passed else "one or more values are outside range",
        observed=observed,
        criterion=expected_criterion(declaration),
    )


def _check_monotonic(
    declaration: dict[str, Any],
    reader: ArtifactReader,
    artifacts: dict[str, ResolvedArtifact],
) -> CheckResult:
    del artifacts
    unit = _source_unit_contract(declaration)
    array = numeric_array(reader.select(declaration["source"]))
    if array.ndim != 1:
        raise ArtifactError(f"monotonic check requires a 1-D array, got shape {array.shape}")
    if array.size < 2:
        raise ArtifactError("monotonic check requires at least two values")
    if not np.all(np.isfinite(array)):
        raise ArtifactError("monotonic check operand contains NaN or infinity")
    tolerance = float(declaration["absolute_tolerance"])
    direction = str(declaration["direction"])
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        differences = np.diff(array)
    if not np.all(np.isfinite(differences)):
        raise ArtifactError("monotonic differences overflowed the finite numeric range")
    if direction == "increasing":
        passing = differences > tolerance
    elif direction == "decreasing":
        passing = differences < -tolerance
    elif direction == "nondecreasing":
        passing = differences >= -tolerance
    elif direction == "nonincreasing":
        passing = differences <= tolerance
    else:  # schema-protected
        raise ArtifactError(f"unknown monotonic direction: {direction}")
    failing = np.flatnonzero(~passing)
    passed = failing.size == 0
    return _result(
        declaration,
        passed,
        f"series is {direction}" if passed else f"series is not {direction}",
        observed={
            "shape": list(array.shape),
            "minimum_step": float(np.min(differences)),
            "maximum_step": float(np.max(differences)),
            "failing_count": int(failing.size),
            "failing_indices": [int(index) for index in failing[:10]],
            "unit": unit,
        },
        criterion=expected_criterion(declaration),
    )


def _check_compare(
    declaration: dict[str, Any],
    reader: ArtifactReader,
    artifacts: dict[str, ResolvedArtifact],
) -> CheckResult:
    del artifacts
    unit = _comparison_unit_contract(declaration)
    actual = numeric_array(_operand(reader, declaration["actual"]))
    expected = numeric_array(_operand(reader, declaration["expected"]))
    if actual.shape != expected.shape:
        raise ArtifactError(
            f"comparison requires identical shapes, got {actual.shape} and {expected.shape}"
        )
    if not np.all(np.isfinite(actual)) or not np.all(np.isfinite(expected)):
        raise ArtifactError("comparison operands contain NaN or infinity")
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        difference = actual - expected
    if not np.all(np.isfinite(difference)):
        raise ArtifactError("comparison difference overflowed the finite numeric range")
    flat = difference.reshape(-1)
    metric = str(declaration["metric"])
    observed: dict[str, Any] = {
        "profile": COMPARISON_PROFILE,
        "shape": list(actual.shape),
        "unit": unit,
    }
    criterion = expected_criterion(declaration)
    gating_norms: dict[str, float] = {}

    if metric == "absolute":
        if actual.size != 1:
            raise ArtifactError("absolute metric is scalar-only; use linf for arrays")
        measured = stable_norm_v1(flat, "linf")
        gating_norms["linf"] = measured
        permitted = float(declaration["tolerance"])
        passed = measured <= permitted
    elif metric == "relative":
        scale_floor = float(declaration["scale_floor"])
        expected_norm = stable_norm_v1(expected.reshape(-1), "l2")
        denominator = max(expected_norm, scale_floor)
        gating_norms["l2"] = stable_norm_v1(flat, "l2")
        measured = gating_norms["l2"] / denominator
        observed["reference_norm"] = AvailableDiagnostic(expected_norm).as_dict()
        permitted = float(declaration["tolerance"])
        passed = measured <= permitted
        observed["relative_l2_error"] = measured
    elif metric in {"l1", "l2", "linf"}:
        measured = stable_norm_v1(flat, metric)
        gating_norms[metric] = measured
        permitted = float(declaration["tolerance"])
        passed = measured <= permitted
        observed["reference_norm"] = norm_diagnostic(expected.reshape(-1), metric).as_dict()
    elif metric == "close":
        absolute_tolerance = float(declaration["absolute_tolerance"])
        relative_tolerance = float(declaration["relative_tolerance"])
        # Gradual underflow (including zero) is valid binary64. Keep separate
        # multiply/add rounding and explicit overflow checks, independent of
        # the embedding application's floating-point trap/warning policy.
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            difference = np.abs(difference)
            allowed = absolute_tolerance + relative_tolerance * np.abs(expected)
        if not np.all(np.isfinite(difference)) or not np.all(np.isfinite(allowed)):
            raise ArtifactError("closeness calculation overflowed the finite numeric range")
        passing = difference <= allowed
        failing = np.argwhere(~passing)
        measured = float(np.max(difference))
        gating_norms["linf"] = measured
        permitted = float(np.max(allowed))
        passed = failing.shape[0] == 0
        observed["failing_count"] = int(failing.shape[0])
        observed["failing_indices"] = [index.tolist() for index in failing[:10]]
    else:  # schema-protected
        raise ArtifactError(f"unknown comparison metric: {metric}")

    if not math.isfinite(measured) or not math.isfinite(permitted):
        raise ArtifactError("comparison ratio overflowed the finite numeric range")
    # Only unavailable supplementary diagnostics can survive a completed gate.
    for name in ("l1", "l2", "linf"):
        diagnostic = (
            AvailableDiagnostic(gating_norms[name]) if name in gating_norms
            else norm_diagnostic(flat, name)
        )
        observed[f"{name}_error"] = diagnostic.as_dict()
    if actual.size == 1:
        actual_scalar = float(actual.reshape(-1)[0])
        expected_scalar = float(expected.reshape(-1)[0])
        absolute_error = abs(actual_scalar - expected_scalar)
        observed.update({
            "actual": actual_scalar, "expected": expected_scalar,
            "absolute_error": absolute_error,
            "relative_error": relative_diagnostic(absolute_error, expected_scalar).as_dict(),
        })
    observed["gating_error"] = measured
    observed["permitted_error"] = permitted
    return _result(
        declaration,
        passed,
        "comparison is within tolerance" if passed else "comparison exceeds tolerance",
        observed=observed,
        criterion=criterion,
    )


def _check_conservation(
    declaration: dict[str, Any],
    reader: ArtifactReader,
    artifacts: dict[str, ResolvedArtifact],
) -> CheckResult:
    del artifacts
    unit = _conservation_unit_contract(declaration)
    contributions: list[dict[str, Any]] = []
    contribution_values: list[float] = []
    for term in declaration["terms"]:
        array = numeric_array(_operand(reader, term["value"]))
        if not np.all(np.isfinite(array)):
            raise ArtifactError("conservation term contains NaN or infinity")
        try:
            raw_total = math.fsum(float(item) for item in array.reshape(-1))
        except OverflowError as exc:
            raise ArtifactError("conservation sum overflowed the finite numeric range") from exc
        coefficient = float(term["coefficient"])
        contribution = coefficient * raw_total
        if not all(math.isfinite(value) for value in (raw_total, contribution)):
            raise ArtifactError("conservation calculation overflowed the finite numeric range")
        contribution_values.append(contribution)
        contributions.append(
            {
                "label": term.get("label"),
                "coefficient": coefficient,
                "raw_total": raw_total,
                "contribution": contribution,
                "unit": unit,
            }
        )
    try:
        balance = math.fsum(contribution_values)
    except OverflowError as exc:
        raise ArtifactError("conservation sum overflowed the finite numeric range") from exc
    if not math.isfinite(balance):
        raise ArtifactError("conservation calculation overflowed the finite numeric range")
    expected_array = numeric_array(_operand(reader, declaration["expected"]))
    if expected_array.size != 1 or not np.all(np.isfinite(expected_array)):
        raise ArtifactError("conservation expected value must be one finite scalar")
    expected = float(expected_array.reshape(-1)[0])
    if "normalization" in declaration:
        normalization_array = numeric_array(_operand(reader, declaration["normalization"]))
        if normalization_array.size != 1 or not np.all(np.isfinite(normalization_array)):
            raise ArtifactError("conservation normalization must be one finite scalar")
        normalization = abs(float(normalization_array.reshape(-1)[0]))
    else:
        normalization = abs(expected)
    absolute_tolerance = float(declaration["absolute_tolerance"])
    relative_tolerance = float(declaration["relative_tolerance"])
    error = abs(balance - expected)
    permitted = absolute_tolerance + relative_tolerance * normalization
    if not math.isfinite(error) or not math.isfinite(permitted):
        raise ArtifactError(
            "conservation tolerance calculation overflowed the finite numeric range"
        )
    passed = error <= permitted
    return _result(
        declaration,
        passed,
        "conservation residual is within tolerance"
        if passed
        else "conservation residual exceeds tolerance",
        observed={
            "balance": balance,
            "expected": expected,
            "absolute_error": error,
            "normalization": normalization,
            "permitted_error": permitted,
            "terms": contributions,
            "unit": unit,
        },
        criterion=expected_criterion(declaration),
    )


Evaluator = Callable[[dict[str, Any], ArtifactReader, dict[str, ResolvedArtifact]], CheckResult]

_EVALUATORS: dict[str, Evaluator] = {
    "exists": _check_exists,
    "schema": _check_schema,
    "finite": _check_finite,
    "range": _check_range,
    "monotonic": _check_monotonic,
    "compare": _check_compare,
    "reference": _check_compare,
    "conservation": _check_conservation,
}


def evaluate_checks(
    declarations: list[dict[str, Any]],
    artifacts: dict[str, ResolvedArtifact],
) -> list[CheckResult]:
    reader = ArtifactReader(artifacts)
    results: list[CheckResult] = []
    for declaration in declarations:
        required = bool(declaration.get("required", True))
        try:
            _require_finite_declaration_numbers(declaration)
            evaluator = _EVALUATORS[str(declaration["type"])]
            result = evaluator(declaration, reader, artifacts)
        except ArtifactError as exc:
            status = Status.ERROR
            if not required and isinstance(exc, MissingArtifactError):
                status = Status.SKIP
            result = CheckResult(
                id=str(declaration["id"]),
                type=str(declaration["type"]),
                status=status,
                required=required,
                summary=str(exc),
                reference_id=declaration.get("reference_id"),
            )
        except Exception as exc:  # defensive evidence: a validator crash is ERROR
            result = CheckResult(
                id=str(declaration["id"]),
                type=str(declaration["type"]),
                status=Status.ERROR,
                required=required,
                summary=f"check evaluation error: {type(exc).__name__}: {exc}",
                reference_id=declaration.get("reference_id"),
            )
        results.append(result)
    return results
