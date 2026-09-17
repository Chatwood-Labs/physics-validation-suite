"""RFC 8785 canonical JSON helpers."""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785

from .constants import EVIDENCE_PREFIX


def canonical_bytes(value: Any) -> bytes:
    """Return RFC 8785 canonical bytes or raise on non-I-JSON content."""

    return rfc8785.dumps(value)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def evidence_id(value: Any) -> str:
    return f"{EVIDENCE_PREFIX}{canonical_sha256(value)}"
