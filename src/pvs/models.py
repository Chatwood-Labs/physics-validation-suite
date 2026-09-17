"""Small typed result models used by the engine and public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"


STATUS_PRIORITY = {
    Status.PASS: 0,
    Status.SKIP: 1,
    Status.WARN: 2,
    Status.FAIL: 3,
    Status.ERROR: 4,
}


def aggregate_status(statuses: list[Status]) -> Status:
    """Return the most consequential state without conflating FAIL and ERROR."""

    if not statuses:
        return Status.ERROR
    return max(statuses, key=STATUS_PRIORITY.__getitem__)


@dataclass(frozen=True)
class CheckResult:
    id: str
    type: str
    status: Status
    required: bool
    summary: str
    observed: dict[str, Any] = field(default_factory=dict)
    criterion: dict[str, Any] = field(default_factory=dict)
    reference_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "status": self.status.value,
            "required": self.required,
            "summary": self.summary,
            "observed": self.observed,
            "criterion": self.criterion,
        }
        if self.reference_id is not None:
            result["reference_id"] = self.reference_id
        return result


@dataclass(frozen=True)
class ExecutionResult:
    mode: str
    command: list[str] | None
    working_directory: str
    started_at: str
    finished_at: str
    duration_seconds: float
    return_code: int | None
    expected_exit_codes: list[int]
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        if self.mode == "validate":
            return self.error is None
        return self.error is None and self.return_code in self.expected_exit_codes
