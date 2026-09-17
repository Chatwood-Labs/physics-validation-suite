"""Mutation helpers that deliberately preserve all internal content hashes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pvs.canonical import canonical_sha256
from pvs.finding import create_finding_metadata
from pvs.jsonutil import write_pretty
from pvs.models import STATUS_PRIORITY, Status


def rehash_record(envelope: dict[str, Any], target: Path) -> Path:
    record = envelope["record"]
    record["case"]["semantic_sha256"] = canonical_sha256(record["case"]["resolved_definition"])
    statuses = [Status(check["status"]) for check in record["checks"]]
    record["summary"]["counts"] = {status.value: statuses.count(status) for status in Status}
    if record["summary"]["provenance_status"] != "COMPLETE" or not record["execution"]["succeeded"]:
        statuses.append(Status.ERROR)
    record["summary"]["status"] = max(statuses, key=STATUS_PRIORITY.__getitem__).value
    envelope["integrity"]["finding"] = create_finding_metadata(record)
    digest = canonical_sha256(record)
    envelope["integrity"]["digest"] = digest
    envelope["integrity"]["evidence_id"] = f"pvs:sha256:{digest}"
    write_pretty(target, envelope)
    return target
