"""Load and validate declarative PVS cases."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .canonical import canonical_sha256
from .constants import CASE_SCHEMA, LEGACY_CASE_SCHEMA
from .errors import ArtifactError, CaseError
from .formats import FormatSupportError, required_format_checker
from .hashing import acquire_file_bytes
from .jsonutil import MAX_SAFE_INTEGER
from .paths import confined_path, portable_relative_path
from .schemas import load_schema


@dataclass(frozen=True)
class CaseDefinition:
    path: Path
    root: Path
    data: dict[str, Any]
    raw_identity: dict[str, Any]
    semantic_sha256: str
    raw_bytes: bytes = field(repr=False)

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def version(self) -> str:
        return str(self.data["version"])


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""

    def compose_node(self, parent: Any, index: Any) -> yaml.nodes.Node:
        if self.check_event(yaml.AliasEvent):
            raise CaseError("YAML aliases are not allowed in a PVS case")
        node = super().compose_node(parent, index)
        if node is None:  # Defensive: PyYAML's public typing permits this result.
            raise CaseError("YAML document ended while composing a node")
        return node


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise CaseError(f"YAML mapping keys must be strings, got {type(key).__name__}")
        if key in mapping:
            raise CaseError(f"duplicate YAML mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _case_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_dir():
        matches = [item for name in ("pvs.yaml", "pvs.yml") if (item := candidate / name).is_file()]
        if not matches:
            raise CaseError(f"no pvs.yaml or pvs.yml in case directory: {candidate}")
        if len(matches) > 1:
            raise CaseError(f"case directory contains both pvs.yaml and pvs.yml: {candidate}")
        candidate = matches[0]
    if not candidate.is_file():
        raise CaseError(f"case file does not exist: {candidate}")
    return candidate.resolve()


def _format_error(error: Any) -> str:
    location = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{location}: {error.message}"


def _validate_case_filename(filename: str) -> None:
    """Require a basename that can be represented by the evidence contract."""

    try:
        portable_relative_path(filename)
    except ArtifactError as exc:
        raise CaseError(f"case filename is not portable: {filename!r}: {exc}") from exc
    if len(filename) > 255:
        raise CaseError(f"case filename exceeds the 255-character evidence limit: {filename!r}")


def case_schema_kind(data: dict[str, Any], *, allow_legacy: bool) -> str:
    """Select the immutable schema for a parsed case discriminator."""

    declared = data.get("schema")
    if declared == LEGACY_CASE_SCHEMA:
        if not allow_legacy:
            raise CaseError(
                f"{LEGACY_CASE_SCHEMA} is verification-only; new cases must use {CASE_SCHEMA}"
            )
        return "case-v1"
    return "case-v2"


def _source_specs(check: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for key in ("source", "actual", "expected", "normalization"):
        value = check.get(key)
        if isinstance(value, dict) and "artifact" in value:
            yield value
    for term in check.get("terms", []):
        value = term.get("value")
        if isinstance(value, dict) and "artifact" in value:
            yield value


def validate_case_semantics(data: dict[str, Any], root: Path | None) -> None:
    artifacts = data["artifacts"]
    references = data.get("references", [])
    checks = data["checks"]

    if data.get("schema") == CASE_SCHEMA:
        artifact_ids = list(artifacts)
        if len({artifact_id.casefold() for artifact_id in artifact_ids}) != len(artifact_ids):
            raise CaseError("artifact IDs must be unique under portable case-insensitive matching")
        for artifact_id in artifact_ids:
            try:
                portable_relative_path(artifact_id)
            except ArtifactError as exc:
                raise CaseError(
                    f"artifact ID {artifact_id!r} is not a portable component: {exc}"
                ) from exc

    check_ids = [item["id"] for item in checks]
    if len(set(check_ids)) != len(check_ids):
        raise CaseError("check IDs must be unique")

    reference_ids = [item["id"] for item in references]
    if len(set(reference_ids)) != len(reference_ids):
        raise CaseError("reference IDs must be unique")

    for artifact_id, declaration in artifacts.items():
        try:
            portable_relative_path(declaration["path"])
            if root is not None:
                confined_path(root, declaration["path"])
        except Exception as exc:
            raise CaseError(f"artifact {artifact_id!r}: {exc}") from exc
        if declaration["role"] == "reference" and not declaration.get("expected_sha256"):
            raise CaseError(f"reference artifact {artifact_id!r} must declare expected_sha256")

    execution = data.get("execution")
    if execution:
        try:
            working_directory = execution.get("working_directory", ".")
            portable_relative_path(working_directory, allow_root=True)
            if root is not None:
                confined_path(root, working_directory)
        except Exception as exc:
            raise CaseError(f"execution working_directory: {exc}") from exc

    known_artifacts = set(artifacts)
    known_references = set(reference_ids)
    for reference in references:
        artifact_id = reference.get("artifact")
        if artifact_id is not None and artifact_id not in known_artifacts:
            raise CaseError(f"reference {reference['id']!r} names unknown artifact {artifact_id!r}")
        if artifact_id is not None and artifacts[artifact_id]["role"] != "reference":
            raise CaseError(
                f"reference {reference['id']!r} names artifact {artifact_id!r} "
                "whose role is not 'reference'"
            )

    for check in checks:
        for key in ("artifact", "schema_artifact"):
            artifact_id = check.get(key)
            if artifact_id is not None and artifact_id not in known_artifacts:
                raise CaseError(f"check {check['id']!r} names unknown artifact {artifact_id!r}")
        for source in _source_specs(check):
            if source["artifact"] not in known_artifacts:
                raise CaseError(
                    f"check {check['id']!r} names unknown artifact {source['artifact']!r}"
                )
            if data.get("schema") == CASE_SCHEMA:
                artifact_format = str(artifacts[source["artifact"]]["format"])
                selector = next(
                    (name for name in ("pointer", "column", "variable") if name in source),
                    None,
                )
                expected_selector = {
                    "json": "pointer",
                    "csv": "column",
                    "netcdf": "variable",
                }.get(artifact_format)
                if selector is not None and selector != expected_selector:
                    raise CaseError(
                        f"check {check['id']!r} uses {selector!r} on {artifact_format!r} "
                        f"artifact {source['artifact']!r}"
                    )
                if artifact_format in {"csv", "netcdf"} and selector != expected_selector:
                    raise CaseError(
                        f"check {check['id']!r} requires a {expected_selector!r} selector for "
                        f"{artifact_format!r} artifact {source['artifact']!r}"
                    )
                if artifact_format not in {"json", "csv", "netcdf"}:
                    raise CaseError(
                        f"check {check['id']!r} cannot select numeric values from "
                        f"{artifact_format!r} artifact {source['artifact']!r}"
                    )
                component = source.get("component")
                if component is not None and (
                    isinstance(component, bool) or not isinstance(component, int)
                ):
                    raise CaseError(f"check {check['id']!r} component selector must be an integer")
        reference_id = check.get("reference_id")
        if reference_id is not None and reference_id not in known_references:
            raise CaseError(f"check {check['id']!r} names unknown reference {reference_id!r}")


def _require_i_json_numbers(value: Any, location: str = "<root>") -> None:
    """Reject values that RFC 8785 cannot represent interoperably."""

    if isinstance(value, bool) or value is None or isinstance(value, str):
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise CaseError(
                    f"{location}: string contains an unpaired Unicode surrogate"
                ) from exc
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CaseError(f"{location}: integer is outside the RFC 8785 interoperable range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CaseError(f"{location}: non-finite YAML numbers are not allowed")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_i_json_numbers(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require_i_json_numbers(key, f"{location}.<key>")
            _require_i_json_numbers(item, f"{location}.{key}")
        return
    raise CaseError(f"{location}: unsupported YAML value type {type(value).__name__}")


def load_case(path: str | Path, *, allow_legacy: bool = False) -> CaseDefinition:
    case_path = _case_path(path)
    try:
        raw_bytes, raw_identity = acquire_file_bytes(case_path)
        parsed = yaml.load(raw_bytes.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CaseError(f"could not read YAML case {case_path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CaseError("case YAML root must be a mapping")

    _require_i_json_numbers(parsed)

    schema_kind = case_schema_kind(parsed, allow_legacy=allow_legacy)
    if schema_kind != "case-v1":
        _validate_case_filename(case_path.name)
    schema = load_schema(schema_kind)
    schema_name = LEGACY_CASE_SCHEMA if schema_kind == "case-v1" else CASE_SCHEMA
    Draft202012Validator.check_schema(schema)
    try:
        format_checker = required_format_checker()
    except FormatSupportError as exc:
        raise CaseError(str(exc)) from exc
    validator = Draft202012Validator(schema, format_checker=format_checker)
    errors = sorted(validator.iter_errors(parsed), key=lambda item: list(item.absolute_path))
    if errors:
        detail = "\n".join(f"  - {_format_error(error)}" for error in errors[:20])
        remainder = len(errors) - min(len(errors), 20)
        if remainder:
            detail += f"\n  - ... and {remainder} more"
        raise CaseError(f"case does not satisfy {schema_name}:\n{detail}")

    validate_case_semantics(parsed, case_path.parent)
    return CaseDefinition(
        path=case_path,
        root=case_path.parent,
        data=parsed,
        raw_identity=raw_identity,
        semantic_sha256=canonical_sha256(parsed),
        raw_bytes=raw_bytes,
    )
