"""Shared, bounded projection for deterministic human evidence reports.

The renderers deliberately consume only values already recorded in the evidence
envelope.  This module turns those values into a small presentation model; it
does not evaluate checks, infer tolerances, or calculate derived quantities.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# These limits apply only to the human rendering.  evidence.json remains the
# complete, authoritative record and is named whenever a limit is reached.
MAX_SEQUENCE_ITEMS = 10
MAX_CONSERVATION_TERMS = 20
MAX_SECTION_ROWS = 80
MAX_TEXT_CHARACTERS = 4096
MAX_NESTING_DEPTH = 8


@dataclass(frozen=True)
class DetailRow:
    """One label/value pair ready for either renderer."""

    label: str
    value: str
    code: bool = False


@dataclass(frozen=True)
class CheckAudit:
    """Recorded declaration, criterion, and observation for one check."""

    id: str
    type: str
    status: str
    required: bool
    summary: str
    reference_id: str | None
    declaration: tuple[DetailRow, ...]
    criterion: tuple[DetailRow, ...]
    observation: tuple[DetailRow, ...]


@dataclass(frozen=True)
class ReferenceAudit:
    """Scientific-reference metadata and any recorded artefact identity."""

    id: str
    rows: tuple[DetailRow, ...]


@dataclass(frozen=True)
class FindingAudit:
    """Recorded transaction-independent scientific finding identity."""

    status: str
    finding_id: str | None
    reason: str | None
    display: str
    rows: tuple[DetailRow, ...]


@dataclass(frozen=True)
class ReportDetails:
    """The shared audit projection used by the HTML and PDF renderers."""

    checks: tuple[CheckAudit, ...]
    references: tuple[ReferenceAudit, ...]
    finding: FindingAudit


_KEY_ORDER = {
    key: index
    for index, key in enumerate(
        (
            "id",
            "type",
            "status",
            "description",
            "required",
            "reference_id",
            "unit",
            "units",
            "artifact",
            "schema_artifact",
            "source",
            "actual",
            "expected",
            "metric",
            "tolerance",
            "absolute_tolerance",
            "relative_tolerance",
            "scale_floor",
            "minimum",
            "maximum",
            "inclusive_minimum",
            "inclusive_maximum",
            "direction",
            "rule",
            "exists",
            "shape",
            "actual_shape",
            "expected_shape",
            "balance",
            "absolute_error",
            "relative_error",
            "gating_error",
            "permitted_error",
            "l1_error",
            "l2_error",
            "linf_error",
            "relative_l2_error",
            "reference_norm",
            "normalization",
            "finite_count",
            "nonfinite_count",
            "failing_count",
            "failing_indices",
            "errors",
            "terms",
            "coefficient",
            "raw_total",
            "contribution",
            "label",
            "projection",
            "canonicalization",
            "algorithm",
            "digest",
            "finding_id",
            "reason",
        )
    )
}

_PROSE_KEYS = {
    "citation",
    "derivation",
    "description",
    "locator",
    "message",
    "notes",
    "source_location",
    "summary",
}

_LABELS = {
    "id": "ID",
    "reference_id": "Reference ID",
    "artifact": "Artefact",
    "schema_artifact": "Schema artefact",
    "absolute_tolerance": "Absolute tolerance",
    "relative_tolerance": "Relative tolerance",
    "scale_floor": "Scale floor",
    "inclusive_minimum": "Inclusive minimum",
    "inclusive_maximum": "Inclusive maximum",
    "actual_shape": "Actual shape",
    "expected_shape": "Expected shape",
    "absolute_error": "Absolute error",
    "relative_error": "Relative error",
    "gating_error": "Gating error",
    "permitted_error": "Permitted error",
    "l1_error": "L1 error",
    "l2_error": "L2 error",
    "linf_error": "Linf error",
    "relative_l2_error": "Relative L2 error",
    "reference_norm": "Reference norm",
    "finite_count": "Finite count",
    "nonfinite_count": "Non-finite count",
    "failing_count": "Failing count",
    "failing_indices": "Failing samples",
    "raw_total": "Raw total",
    "source_location": "Source location",
    "accessed_utc": "Access date (UTC)",
    "expected_sha256": "Expected SHA-256",
    "sha256": "SHA-256",
    "size_bytes": "Size (bytes)",
    "package_path": "Package path",
    "finding_id": "Finding ID",
}


def _clean_text(value: str) -> str:
    """Make arbitrary recorded text safe for HTML/XML text nodes."""

    cleaned = "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 32
        else "\N{REPLACEMENT CHARACTER}"
        for character in value
    )
    if len(cleaned) <= MAX_TEXT_CHARACTERS:
        return cleaned
    omitted = len(cleaned) - MAX_TEXT_CHARACTERS
    return (
        cleaned[:MAX_TEXT_CHARACTERS]
        + f"\n[report limit: {omitted} characters omitted; see evidence.json]"
    )


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _clean_text(value)
    try:
        rendered = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        rendered = str(value)
    return _clean_text(rendered)


def _json_text(value: Any) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        rendered = str(value)
    return _clean_text(rendered)


def _key_sort(key: Any) -> tuple[int, str]:
    text = str(key)
    return (_KEY_ORDER.get(text, len(_KEY_ORDER)), text)


def _component_label(component: str) -> str:
    base, bracket, index = component.partition("[")
    label = _LABELS[base] if base in _LABELS else base.replace("_", " ").capitalize()
    return f"{label}[{index}" if bracket else label


def _path_label(path: tuple[str, ...]) -> str:
    return " / ".join(_component_label(component) for component in path)


def _is_code_path(path: tuple[str, ...], value: Any) -> bool:
    leaf = path[-1].split("[")[0] if path else ""
    return leaf not in _PROSE_KEYS and not isinstance(value, str)


class _RowCollector:
    def __init__(self) -> None:
        self.rows: list[DetailRow] = []
        self.limited = False

    def append(self, path: tuple[str, ...], value: str, *, code: bool) -> None:
        # Reserve the final slot for an explicit limit marker.
        if len(self.rows) >= MAX_SECTION_ROWS - 1:
            self.limited = True
            return
        self.rows.append(DetailRow(_path_label(path), value, code))

    def finish(self) -> tuple[DetailRow, ...]:
        if self.limited:
            self.rows.append(
                DetailRow(
                    "Report limit",
                    "Additional recorded fields omitted; see evidence.json.",
                )
            )
        return tuple(self.rows)


def _flatten(value: Any, path: tuple[str, ...], collector: _RowCollector) -> None:
    if collector.limited:
        return
    if len(path) >= MAX_NESTING_DEPTH and isinstance(value, (dict, list, tuple)):
        collector.append(
            (*path, "report_limit"),
            f"Nested value omitted beyond {MAX_NESTING_DEPTH} levels; see evidence.json.",
            code=False,
        )
        return
    if isinstance(value, dict):
        if not value:
            collector.append(path or ("details",), "No recorded details.", code=False)
            return
        for key in sorted(value, key=_key_sort):
            _flatten(value[key], (*path, str(key)), collector)
        return
    if isinstance(value, (list, tuple)):
        if not value:
            collector.append(path or ("details",), "[]", code=True)
            return
        limit = MAX_CONSERVATION_TERMS if path and path[-1] == "terms" else MAX_SEQUENCE_ITEMS
        visible = value[:limit]
        if path and path[-1] == "terms":
            for index, item in enumerate(visible, start=1):
                item_path = (*path[:-1], f"{path[-1]}[{index}]")
                if isinstance(item, dict) and all(
                    not isinstance(field, (dict, list, tuple)) for field in item.values()
                ):
                    collector.append(item_path, _json_text(item), code=True)
                else:
                    _flatten(item, item_path, collector)
            if len(value) > limit:
                collector.append(
                    (*path, "report_limit"),
                    f"{len(value) - limit} additional items omitted; see evidence.json.",
                    code=False,
                )
            return
        if all(not isinstance(item, (dict, list, tuple)) for item in visible):
            collector.append(path or ("values",), _json_text(list(visible)), code=True)
        else:
            for index, item in enumerate(visible, start=1):
                item_path = (*path[:-1], f"{path[-1]}[{index}]") if path else (f"item[{index}]",)
                _flatten(item, item_path, collector)
        if len(value) > limit:
            collector.append(
                (*path, "report_limit"),
                f"{len(value) - limit} additional items omitted; see evidence.json.",
                code=False,
            )
        return
    collector.append(path or ("value",), _scalar_text(value), code=_is_code_path(path, value))


def flatten_recorded(value: Any) -> tuple[DetailRow, ...]:
    """Flatten a recorded value into a deterministic, bounded row sequence."""

    collector = _RowCollector()
    _flatten(value, (), collector)
    return collector.finish()


def _reference_rows(
    reference: dict[str, Any], artifacts_by_id: dict[str, dict[str, Any]]
) -> tuple[DetailRow, ...]:
    collector = _RowCollector()
    ordered_fields = (
        "type",
        "citation",
        "locator",
        "artifact",
        "source_location",
        "accessed_utc",
        "derivation",
        "notes",
    )
    rendered_fields = {"id"}
    for field in ordered_fields:
        if field in reference:
            _flatten(reference[field], (field,), collector)
            rendered_fields.add(field)
    for field in sorted(set(reference) - rendered_fields, key=_key_sort):
        _flatten(reference[field], (field,), collector)

    artifact_id = reference.get("artifact")
    artifact = artifacts_by_id.get(str(artifact_id)) if artifact_id is not None else None
    if artifact is not None:
        identity: dict[str, Any] = {}
        for field in ("role", "path", "expected_sha256", "package_path"):
            if field in artifact and artifact[field] is not None:
                identity[field] = artifact[field]
        validation_input = artifact.get("validation_input")
        if isinstance(validation_input, dict):
            for field in ("exists", "sha256", "size_bytes"):
                if field in validation_input and validation_input[field] is not None:
                    identity[field] = validation_input[field]
        if identity:
            _flatten(identity, ("artifact_identity",), collector)
    return collector.finish()


def _finding_audit(integrity: dict[str, Any]) -> FindingAudit:
    finding = integrity.get("finding")
    if not isinstance(finding, dict):
        display = "Not recorded in this evidence envelope."
        return FindingAudit(
            status="NOT_RECORDED",
            finding_id=None,
            reason="NOT_RECORDED_IN_EVIDENCE",
            display=display,
            rows=(DetailRow("Status", "NOT_RECORDED", code=True), DetailRow("Reason", display)),
        )

    status = _clean_text(str(finding.get("status", "NOT_RECORDED")))
    finding_id = (
        _clean_text(str(finding["finding_id"])) if finding.get("finding_id") is not None else None
    )
    reason = _clean_text(str(finding["reason"])) if finding.get("reason") is not None else None
    if status == "ISSUED" and finding_id is not None:
        display = finding_id
    elif status == "NOT_ISSUED":
        display = f"Not issued: {reason or 'reason not recorded'}"
    else:
        display = f"{status}: {reason or 'identity unavailable'}"

    ordered_fields = (
        "status",
        "finding_id",
        "reason",
        "projection",
        "canonicalization",
        "algorithm",
        "digest",
    )
    collector = _RowCollector()
    rendered_fields: set[str] = set()
    for field in ordered_fields:
        if field in finding:
            _flatten(finding[field], (field,), collector)
            rendered_fields.add(field)
    for field in sorted(set(finding) - rendered_fields, key=_key_sort):
        _flatten(finding[field], (field,), collector)
    return FindingAudit(
        status=status,
        finding_id=finding_id,
        reason=reason,
        display=display,
        rows=collector.finish(),
    )


def project_report_details(envelope: dict[str, Any]) -> ReportDetails:
    """Project an evidence envelope without mutating or re-evaluating it."""

    record = envelope["record"]
    resolved = record.get("case", {}).get("resolved_definition", {})
    declarations = resolved.get("checks", []) if isinstance(resolved, dict) else []
    declarations_by_id = {
        str(item.get("id")): item for item in declarations if isinstance(item, dict)
    }

    checks: list[CheckAudit] = []
    for result in record.get("checks", []):
        check_id = str(result.get("id", "-"))
        declaration = declarations_by_id.get(check_id, {})
        criterion = result.get("criterion")
        observation = result.get("observed")
        checks.append(
            CheckAudit(
                id=check_id,
                type=str(result.get("type", "-")),
                status=str(result.get("status", "ERROR")),
                required=bool(result.get("required", True)),
                summary=_clean_text(str(result.get("summary", "-"))),
                reference_id=(
                    _clean_text(str(result["reference_id"]))
                    if result.get("reference_id") is not None
                    else None
                ),
                declaration=flatten_recorded(declaration),
                criterion=flatten_recorded(criterion if criterion is not None else {}),
                observation=flatten_recorded(observation if observation is not None else {}),
            )
        )

    artifacts_by_id = {
        str(item.get("id")): item for item in record.get("artifacts", []) if isinstance(item, dict)
    }
    references: list[ReferenceAudit] = []
    for reference in record.get("references", []):
        if not isinstance(reference, dict):
            continue
        reference_id = _clean_text(str(reference.get("id", "-")))
        references.append(
            ReferenceAudit(
                id=reference_id,
                rows=_reference_rows(reference, artifacts_by_id),
            )
        )

    return ReportDetails(
        checks=tuple(checks),
        references=tuple(references),
        finding=_finding_audit(envelope.get("integrity", {})),
    )


__all__ = [
    "CheckAudit",
    "DetailRow",
    "FindingAudit",
    "ReferenceAudit",
    "ReportDetails",
    "flatten_recorded",
    "project_report_details",
]
