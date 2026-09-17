"""The fixed, local-reference JSON Schema policy of the schema check."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from referencing import Registry
from referencing.jsonschema import DRAFT202012

from ..errors import ArtifactError

DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _schemas(schema: Any) -> Iterator[Any]:
    """Walk schema positions using the resolver's public dialect specification.

    Call only after Draft202012Validator.check_schema. This includes $defs,
    applicators, contentSchema and the supported legacy definitions container,
    but never interprets const/enum/default/examples/extension data as schemas.
    """

    yield schema
    for child in DRAFT202012.subresources_of(schema):
        yield from _schemas(child)


def validate_schema_policy(schema: dict[str, Any]) -> None:
    """Reject unsupported declarations before constructing a user registry."""

    for current in _schemas(schema):
        if isinstance(current, bool):
            continue
        if "$schema" in current and current["$schema"] not in {DIALECT, DIALECT + "#"}:
            raise ArtifactError(f"unsupported JSON Schema dialect: {current['$schema']!r}")
        for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
            reference = current.get(keyword)
            if isinstance(reference, str) and not reference.startswith("#"):
                raise ArtifactError(
                    f"remote or external JSON Schema {keyword} is forbidden: {reference}"
                )


def validate_local_schema_references(schema: dict[str, Any], registry: Registry[Any]) -> None:
    """Bind local references to the schema positions already checked above.

    The same resolver used by validation handles pointers, anchors and embedded
    resource bases. A pointer into literal/extension object data must not turn
    that unchecked object into an executable schema. Boolean targets contain
    no instructions and retain their ordinary true/false schema semantics.
    Retrieval remains disabled by the supplied registry independently of this
    preflight. References are checked even in branches not used by the instance.
    """

    known = {id(current) for current in _schemas(schema)}
    root = DRAFT202012.create_resource(schema)
    pending = [(root.contents, registry.resolver_with_root(root))]
    while pending:
        current, resolver = pending.pop()
        if isinstance(current, bool):
            continue
        for keyword in ("$ref", "$dynamicRef"):
            if keyword in current:
                reference = current[keyword]
                target = resolver.lookup(reference).contents
                if not isinstance(target, bool) and id(target) not in known:
                    raise ArtifactError(
                        "JSON Schema reference target is outside recognized schema positions: "
                        f"{reference}"
                    )
        for child in DRAFT202012.subresources_of(current):
            pending.append((child, resolver.in_subresource(DRAFT202012.create_resource(child))))
