# JSON Schema check policy

The `schema` check evaluates a JSON artifact against a separate JSON Schema
artifact. Its recorded criterion fixes the dialect to
`https://json-schema.org/draft/2020-12/schema` and enables the required format
checker. The schema artifact must be an object; boolean subschemas are supported.

An omitted `$schema` means Draft 2020-12. An explicit declaration must be the
URI above, optionally followed by an empty `#` fragment. Older drafts, custom
dialects, the HTTP spelling and malformed declarations produce ERROR. This
applies throughout schema positions, including unused embedded resources and
conditional branches. PVS does not switch validators according to the input.
A schema without `$schema` uses 2020-12 keyword semantics: for example,
`dependentRequired` and `dependentSchemas` apply, while the old `dependencies`
keyword does not acquire Draft-07 semantics merely by being present.

Preflight uses the resolver's public Draft 2020-12 subresource traversal. It
visits the applicator schemas, `$defs`, `contentSchema` and the compatible
`definitions` container. Objects beneath `const`, `enum`, `default`, `examples`
and unknown extension keywords are data. Their `$ref`, `$dynamicRef`,
`$recursiveRef` and `$schema` members are literal members, not instructions.

Actual `$ref` and `$dynamicRef` instructions must start with `#`. Their pointers
or anchors must resolve locally, even in unused branches. Embedded `$id` values
establish resource bases; they do not authorize retrieval. The same resolver
used during validation checks each target. Object targets must already occupy
recognized schema positions. A pointer into literal or extension object data
is an ERROR; place reusable schemas under `$defs` instead. Boolean targets keep
their true/false schema meanings. This closes the route by which a local
pointer could otherwise cause unchecked data to be executed as a schema.

The legacy `$recursiveRef` keyword retains the external-reference prohibition;
it does not gain older-dialect evaluation semantics. Use `$dynamicRef` for
Draft 2020-12 recursion. A registry retrieval callback independently refuses
every retrieval request. These are separate boundaries: the static policy
checks declarations, and the registry prevents fetching resources at runtime.

Unsupported or malformed schemas and prohibited/unresolvable references yield
ERROR, with no Finding ID. Valid schemas applied to invalid data yield FAIL
(WARN for an optional check). A coherent ERROR evidence package can still pass
integrity verification. Verification checks recorded schema-check observations
and identities; it does not rerun validation against original artifacts. Old
evidence is not rewritten or retroactively evaluated under this producer fix.

The fixed subset is informed by the resource and reference rules in the
[Draft 2020-12 core specification](https://json-schema.org/draft/2020-12/json-schema-core)
and the registry behavior documented by
[python-jsonschema](https://python-jsonschema.readthedocs.io/en/stable/referencing/).
It is deliberately narrower than arbitrary JSON Schema dialect support.
