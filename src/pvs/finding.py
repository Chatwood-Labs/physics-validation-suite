"""Transaction-independent identity for an exact scientific finding."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_sha256
from .constants import (
    CANONICALIZATION,
    FINDING_PREFIX,
    FINDING_PROJECTION,
    HASH_ALGORITHM,
)


def _source(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize a selector while admitting only v1 scientific fields."""

    normalized: dict[str, Any] = {
        "artifact": value["artifact"],
        "pointer": value.get("pointer"),
        "column": value.get("column"),
        "variable": value.get("variable"),
        "component": value.get("component"),
        "reduce": value.get("reduce"),
        "netcdf_decoding": value.get("netcdf_decoding"),
    }
    # Unit metadata is a scientific field in pvs-case/2. Keeping the field in
    # this v1 projection makes omission and an explicit null equivalent while
    # remaining compatible with pvs-case/1 records.
    normalized["unit"] = value.get("unit")
    return normalized


def _operand(value: Any) -> Any:
    if isinstance(value, dict) and "artifact" in value:
        return {"kind": "source", "value": _source(value)}
    return {"kind": "literal", "value": value}


def _term(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": _operand(value["value"]),
        "coefficient": value["coefficient"],
        "label": value.get("label"),
        "unit": value.get("unit"),
    }


def _check_declaration(value: dict[str, Any]) -> dict[str, Any]:
    """Return default-normalized scientific semantics for one declaration."""

    check_type = value["type"]
    result: dict[str, Any] = {
        "id": value["id"],
        "type": check_type,
        "required": value.get("required", True),
        "unit": value.get("unit"),
    }
    if check_type == "exists":
        result["artifact"] = value["artifact"]
    elif check_type == "schema":
        result.update(
            {
                "artifact": value["artifact"],
                "schema_artifact": value["schema_artifact"],
            }
        )
    elif check_type == "finite":
        result["source"] = _source(value["source"])
    elif check_type == "range":
        result.update(
            {
                "source": _source(value["source"]),
                "minimum": value.get("minimum"),
                "maximum": value.get("maximum"),
                "inclusive_minimum": value.get("inclusive_minimum", True),
                "inclusive_maximum": value.get("inclusive_maximum", True),
            }
        )
    elif check_type == "monotonic":
        result.update(
            {
                "source": _source(value["source"]),
                "direction": value["direction"],
                "absolute_tolerance": value["absolute_tolerance"],
            }
        )
    elif check_type in {"compare", "reference"}:
        metric = value["metric"]
        result.update(
            {
                "actual": _operand(value["actual"]),
                "expected": _operand(value["expected"]),
                "metric": metric,
                "reference_id": value.get("reference_id"),
            }
        )
        if metric in {"absolute", "relative", "l1", "l2", "linf"}:
            result["tolerance"] = value["tolerance"]
        if metric == "relative":
            result["scale_floor"] = value["scale_floor"]
        if metric == "close":
            result.update(
                {
                    "absolute_tolerance": value["absolute_tolerance"],
                    "relative_tolerance": value["relative_tolerance"],
                }
            )
    elif check_type == "conservation":
        expected = _operand(value["expected"])
        result.update(
            {
                "terms": [_term(term) for term in value["terms"]],
                "expected": expected,
                # Omitted normalization means abs(expected), so normalize it
                # to the same operand rather than preserving syntax.
                "normalization": (
                    _operand(value["normalization"])
                    if "normalization" in value
                    else expected
                ),
                "absolute_tolerance": value["absolute_tolerance"],
                "relative_tolerance": value["relative_tolerance"],
            }
        )
    else:  # Evidence-schema validation protects callers in normal operation.
        raise ValueError(f"unsupported check type in Finding ID projection: {check_type}")
    return result


def _artifact_ids_used_by_checks(checks: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for check in checks:
        for key in ("artifact", "schema_artifact"):
            value = check.get(key)
            if isinstance(value, str):
                result.add(value)
        for key in ("source", "actual", "expected", "normalization"):
            value = check.get(key)
            if isinstance(value, dict) and isinstance(value.get("artifact"), str):
                result.add(value["artifact"])
        for term in check.get("terms", []):
            value = term.get("value")
            if isinstance(value, dict) and isinstance(value.get("artifact"), str):
                result.add(value["artifact"])
    return result


def _reference(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize the complete scientific reference declaration."""

    return {
        "id": value["id"],
        "type": value["type"],
        "citation": value["citation"],
        "locator": value["locator"],
        "artifact": value.get("artifact"),
        "source_location": value.get("source_location"),
        "accessed_utc": value.get("accessed_utc"),
        "derivation": value.get("derivation"),
        "notes": value.get("notes"),
    }


def finding_projection(record: dict[str, Any]) -> dict[str, Any]:
    """Build the allowlisted RFC 8785 input for Finding ID profile v1.

    Output artifact byte identities are deliberately absent. Their selected,
    scientifically checked meaning is represented only by exact structured
    observations. Unselected or otherwise unchecked output bytes therefore do
    not change a Finding ID; the Evidence ID continues to bind those bytes.
    """

    definition = record["case"]["resolved_definition"]
    declarations = definition["checks"]
    used_by_checks = _artifact_ids_used_by_checks(declarations)
    artifacts: list[dict[str, Any]] = []
    for artifact in record["artifacts"]:
        role = artifact["role"]
        if role not in {"subject", "input", "reference"} and artifact["id"] not in used_by_checks:
            continue
        item: dict[str, Any] = {
            "id": artifact["id"],
            "role": role,
            "format": artifact["format"],
            "required": artifact["required"],
            "unit": artifact.get("unit"),
        }
        if role in {"subject", "input", "reference"}:
            snapshot = artifact["validation_input"]
            item["validation_identity"] = {
                "exists": snapshot["exists"],
                "sha256": snapshot.get("sha256"),
                "size_bytes": snapshot.get("size_bytes"),
            }
        else:
            # Output/auxiliary declarations can define selector semantics, but
            # their whole-file byte identity is intentionally not a finding.
            item["validation_identity"] = None
        artifacts.append(item)

    check_results = {item["id"]: item for item in record["checks"]}
    checks = []
    for declaration in sorted(declarations, key=lambda item: item["id"]):
        outcome = check_results[declaration["id"]]
        checks.append(
            {
                "declaration": _check_declaration(declaration),
                "status": outcome["status"],
                "criterion": outcome["criterion"],
                "observed": outcome["observed"],
                "reference_id": outcome.get("reference_id"),
            }
        )

    subject = record["subject"]
    return {
        "projection": FINDING_PROJECTION,
        "subject": {
            "name": subject["name"],
            "version": subject["version"],
            "revision": subject.get("revision"),
            "source_repository": subject.get("source_repository"),
        },
        "case": {
            "id": definition["id"],
            "version": definition["version"],
            "classifications": sorted(definition["classifications"]),
        },
        "artifacts": sorted(artifacts, key=lambda item: item["id"]),
        "references": sorted(
            (_reference(value) for value in record["references"]),
            key=lambda item: item["id"],
        ),
        "checks": checks,
    }


def finding_ineligibility_reason(record: dict[str, Any]) -> str | None:
    summary = record["summary"]
    if summary["provenance_status"] != "COMPLETE":
        return "INCOMPLETE_PROVENANCE"
    if (
        not record["execution"]["succeeded"]
        or summary["status"] == "ERROR"
        or any(check["status"] == "ERROR" for check in record["checks"])
    ):
        return "ERROR_PRESENT"
    return None


def create_finding_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Return the closed issued/not-issued Finding ID integrity metadata."""

    base = {
        "projection": FINDING_PROJECTION,
        "canonicalization": CANONICALIZATION,
        "algorithm": HASH_ALGORITHM,
    }
    reason = finding_ineligibility_reason(record)
    if reason is not None:
        return {**base, "status": "NOT_ISSUED", "reason": reason}
    digest = canonical_sha256(finding_projection(record))
    return {
        **base,
        "status": "ISSUED",
        "digest": digest,
        "finding_id": f"{FINDING_PREFIX}{digest}",
    }
