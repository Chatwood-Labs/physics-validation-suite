# Case format v2

A case is a UTF-8 YAML mapping named `pvs.yaml` by convention. PVS rejects
duplicate YAML keys, YAML aliases, non-string mapping keys and documents that do
not satisfy the packaged JSON Schema. Unknown fields are rejected throughout
the versioned core vocabulary.

PVS creates and executes only `pvs-case/2`. Offline verification retains
the immutable `pvs-case/1` schema and v1 semantic rules so evidence produced by
v0.1 remains independently verifiable. Legacy loading is an explicit verifier
path; it is not a way to author or run new v1 cases.

Use `pvs inspect path/to/pvs.yaml` to validate and summarise a declaration
without running it.

## Identity and subject

Every case declares `schema: pvs-case/2`, a stable case `id`, case `version`,
title, one or more classifications, and the name/version of the software or
workflow under examination. A source revision and repository locator may be
added where available.

The case has two hashes in evidence:

- `raw_sha256` identifies the exact YAML bytes;
- `semantic_sha256` identifies the RFC 8785 canonical parsed definition.

## Execution

`execution.command` is a YAML list of arguments, not a shell command string.
PVS never invokes a shell. `working_directory` remains confined to the case
directory. Optional environment values are additive declarations passed to the
child process and recorded in provenance. Inherited environment values are not
recorded, because they may contain secrets.

`pvs validate` never executes this declaration. `pvs run` requires it.

## Artefacts and references

Each artefact declares a relative path, role and format. Roles are `subject`,
`input`, `reference`, `output` and `auxiliary`; formats are JSON, CSV, NetCDF,
text and binary. Reference artefacts must declare `expected_sha256`, so a cited
source cannot silently drift while retaining the same filename.

A `references` entry records scientific provenance such as citation, locator,
source location, derivation and notes. The optional `artifact` field binds that
metadata to a declared reference artefact.

PVS records all declarations. It does not infer that a citation is correct or
that a chosen model is appropriate.

## Sources and reductions

Checks read values through a source selector:

```yaml
source:
  artifact: result
  pointer: /series/temperature
  reduce: last
  unit: K
```

At most one format-specific selector may be supplied: RFC 6901 JSON `pointer`,
CSV `column`, or NetCDF `variable`. JSON may omit `pointer` to select the whole
document; CSV and NetCDF require `column` and `variable` respectively. Optional reductions are `sum`, `mean`,
`min`, `max`, `first`, `last` and `size`. Without a reduction, arrays remain
arrays. A zero-based `component` selector takes one component along the final
array axis before reduction. This permits a heterogeneous row such as
`[energy_MeV, fraction_1]` to be checked as two scientifically meaningful
quantities rather than assigning one false unit to the whole row. Invalid,
non-integral or out-of-range components are errors. A `size` reduction is
dimensionless and therefore requires `unit: "1"`.

## Units

Every numeric check declares `unit`. Every numeric source and literal operand
also declares its unit, including the explicit dimensionless unit `"1"`.
Literal operands use a closed quantity object:

```yaml
actual: {artifact: result, pointer: /temperature, reduce: last, unit: K}
expected: {value: 300.0, unit: K}
unit: K
```

Unit strings are opaque, case-sensitive metadata. PVS performs no unit parsing,
normalisation or conversion in v2. The source, actual, expected, conservation
term and normalization units must exactly equal the check unit; a mismatch is
an `ERROR`, not a failed numerical comparison. Dimensional tolerances and a
relative metric's `scale_floor` inherit the check unit. Relative tolerances are
dimensionless. Conservation coefficients are dimensionless in this profile, so
every term has the balance unit.

Numeric coercion and norm arithmetic are specified in the
[numerical contract](numerical-contract.md). Original scalar types survive
component selection; numeric reductions reject booleans and unsafe integers
before binary64 conversion. `size` remains a structural count.

## NetCDF decoding profile

Every NetCDF source declares `netcdf_decoding: pvs-netcdf/1`. PVS disables the
netCDF4 library's automatic mask/scale behaviour and applies this versioned
profile itself, in this order:

1. Read the variable's raw packed storage values.
2. If `_Unsigned` is the case-insensitive string `true`, reinterpret a signed
   integer variable as the same-width unsigned integer. Other `_Unsigned`
   values are errors.
3. In the packed (and, where applicable, unsigned-reinterpreted) domain, mask
   `_FillValue` or the NetCDF default fill value, every `missing_value`, and
   values outside `valid_range` or `valid_min`/`valid_max`. Declaring
   `valid_range` together with either individual bound is an error.
4. Convert unmasked real values to binary64 and apply storage unpacking as
   `decoded = packed * scale_factor + add_offset`, with defaults `1` and `0`.
   This is storage decoding, not unit conversion.
5. Return a binary64 array of the original shape with every masked value
   represented by `NaN`. This rule applies equally to integer and floating
   variables.

Mask/scale attributes must be numeric, structurally valid and finite where
required. Integer `scale_factor` and `add_offset` values must lie in the inclusive
safe-integer interval `[-9007199254740991, 9007199254740991]` before binary64
conversion. Packed-domain integer fill, missing and validity sentinels retain
their storage-domain treatment. Unpacking overflow and unsafe unmasked integer
data values are errors. Character, string and complex variables are not numeric
operands.

The case selector's `unit` is authoritative when the variable has no `units`
attribute. When that attribute is present, it must be a string exactly equal to
the declared unit; PVS never converts between them. A `size` reduction does not
compare the variable attribute because its result is the dimensionless element
count.

## Checks

| Type | Purpose |
| --- | --- |
| `exists` | Required artefact exists |
| `schema` | JSON artefact satisfies a declared JSON Schema artefact |
| `finite` | Every selected numeric value is finite |
| `range` | Values lie inside declared bounds |
| `monotonic` | Sequence has the declared direction within a declared absolute tolerance |
| `compare` | Actual and expected operands satisfy a generic metric/tolerance |
| `reference` | Same numerical operation as compare, additionally bound to reference metadata |
| `conservation` | Weighted terms satisfy declared absolute and relative residual tolerances |

Schema checks use the fixed [Draft 2020-12 policy](schema-checks.md), including
the default when `$schema` is omitted, rejection of unsupported embedded
dialects, literal-data handling and fragment-local reference restrictions.

Comparison metrics are absolute error, relative error, L1, L2, L-infinity and
elementwise closeness. Relative comparisons require an explicit positive scale
floor. Closeness requires both absolute and relative tolerances. Each metric is
a separately closed schema variant: fields belonging to another metric are
rejected rather than ignored. Tolerances are never hidden implementation
constants.

Every check type is likewise a separately closed schema shape. A declared but
irrelevant known field is invalid—for example, an `exists` check cannot carry a
metric, tolerance, terms or unit. This prevents criteria from looking operative
while an evaluator silently ignores them.

Each check is required by default. A failed required check yields `FAIL`; a
failed non-required check yields `WARN`. Reader or evaluation failure yields
`ERROR`. Optional missing data can yield `SKIP` only where the check declaration
permits it.

The complete normative v2 shape is
`src/pvs/schemas/pvs-case.schema.json`. The frozen v1 verifier schema is
`src/pvs/schemas/pvs-case-v1.schema.json`. Copies under `schemas/` are included
for convenient review.
