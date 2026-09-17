"""Typed verification results and invariant check records."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict


class VerificationCheck(TypedDict):
    """One named invariant and its deterministic verdict, serialized unchanged."""

    name: str
    status: Literal["PASS", "FAIL"]
    detail: str


@dataclass(frozen=True)
class VerificationResult:
    target: Path
    valid: bool
    level: str
    evidence_id: str | None = None
    finding_id: str | None = None
    package_id: str | None = None
    checks: list[VerificationCheck] = field(default_factory=list)
    evidence_status: str | None = None
    evidence_identity_pinned: bool = False
    finding_identity_pinned: bool = False
    package_identity_pinned: bool = False
    trust: str = "unverified"

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": str(self.target),
            "valid": self.valid,
            "level": self.level,
            "evidence_id": self.evidence_id,
            "finding_id": self.finding_id,
            "package_id": self.package_id,
            "evidence_status": self.evidence_status,
            "evidence_identity_pinned": self.evidence_identity_pinned,
            "finding_identity_pinned": self.finding_identity_pinned,
            "package_identity_pinned": self.package_identity_pinned,
            "trust": self.trust,
            "checks": self.checks,
        }


@dataclass(frozen=True)
class _VerifiedTarget:
    """Internal acquisition result; a record is exposed only after full verification.

    The record is the parsed object checked inside the private snapshot, retained
    in memory after snapshot cleanup. It is never reread from the mutable target.
    This payload is deliberately absent from VerificationResult's public JSON.
    """

    verification: VerificationResult
    record: dict[str, Any] | None = None


def _append(checks: list[VerificationCheck], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})
