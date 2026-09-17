"""Versioned finite-or-unavailable diagnostic values; gates must remain finite."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, TypedDict

from ..constants import COMPARISON_PROFILE as COMPARISON_PROFILE
from ..errors import ArtifactError, NumericOverflowError
from ..numeric import stable_norm_v1

UnavailableReason = Literal["OVERFLOW", "ZERO_REFERENCE"]


class AvailableRecord(TypedDict):
    status: Literal["AVAILABLE"]
    value: float


class UnavailableRecord(TypedDict):
    status: Literal["UNAVAILABLE"]
    reason: UnavailableReason


DiagnosticRecord = AvailableRecord | UnavailableRecord


@dataclass(frozen=True)
class AvailableDiagnostic:
    value: float

    def as_dict(self) -> AvailableRecord:
        return {"status": "AVAILABLE", "value": self.value}


@dataclass(frozen=True)
class UnavailableDiagnostic:
    reason: UnavailableReason

    def as_dict(self) -> UnavailableRecord:
        return {"status": "UNAVAILABLE", "reason": self.reason}


Diagnostic = AvailableDiagnostic | UnavailableDiagnostic


def norm_diagnostic(values: Iterable[float], metric: str) -> Diagnostic:
    try:
        return AvailableDiagnostic(stable_norm_v1(values, metric))
    except NumericOverflowError:
        return UnavailableDiagnostic("OVERFLOW")


def relative_diagnostic(error: float, reference: float) -> Diagnostic:
    if reference == 0:
        return UnavailableDiagnostic("ZERO_REFERENCE")
    value = error / abs(reference)
    return AvailableDiagnostic(value) if math.isfinite(value) else UnavailableDiagnostic("OVERFLOW")


def parse_diagnostic(value: object, *, allow_zero_reference: bool = False) -> Diagnostic:
    if isinstance(value, dict):
        if set(value) == {"status", "value"} and value["status"] == "AVAILABLE":
            number = value["value"]
            if type(number) in {int, float} and math.isfinite(number) and number >= 0:
                return AvailableDiagnostic(float(number))
        if set(value) == {"status", "reason"} and value["status"] == "UNAVAILABLE":
            if value["reason"] == "OVERFLOW":
                return UnavailableDiagnostic("OVERFLOW")
            if allow_zero_reference and value["reason"] == "ZERO_REFERENCE":
                return UnavailableDiagnostic("ZERO_REFERENCE")
    raise ArtifactError("diagnostic requires an exact AVAILABLE value or UNAVAILABLE reason")


def require_available(value: Diagnostic, *, quantity: str) -> float:
    if isinstance(value, UnavailableDiagnostic):
        raise NumericOverflowError(f"selected {quantity} is unavailable: {value.reason}")
    return value.value
