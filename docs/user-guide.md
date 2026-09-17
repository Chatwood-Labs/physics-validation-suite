# PVS user guide

See the [README](../README.md) for installation and alpha status.

## Claim boundary

A PVS PASS means that all declared checks passed, subject execution or
ingestion succeeded, and PVS found no provenance failure in the transaction.
It says nothing beyond the scope and quality of the declared case and evidence.

PVS makes an independent-reference workflow reviewable; the case author must
establish the reference's independence and scientific suitability. A frozen hash
prevents unnoticed byte changes relative to that declaration. It cannot prove
how the reference values were originally obtained or prevent an author from
declaring a self-comparison. Publish or retain independently reviewed reference
identities through a trusted channel when that separation is required.

PVS does not:

- certify universal physical validity or operational safety;
- replace peer review, experimental validation or engineering judgement;
- decide whether a publication, dataset or model is scientifically suitable;
- make two agreeing but incorrect models correct;
- infer tolerances or silently convert units;
- guarantee a hermetic or reconstructable execution environment;
- prove that an unsigned package was issued by a named organisation;
- require the subject software to be Python or open source.

The `subject` name, version and revision are declarations. In run mode PVS
resolves the command executable, acquires its bytes through one open handle into
a mode-0700 private staging directory, launches that snapshot by its private
pathname while preserving the declared argument vector in evidence, and
re-identifies the acquired bytes afterward.
It does not automatically bind scripts passed as arguments, imported modules,
shared libraries, repositories, containers or the subject's dependencies.
Declare those as artefacts when they materially identify what was tested.

Within PVS's local threat model, the operating-system user running PVS and its
private staging directory are trusted not to be maliciously modified by another
same-user process between acquisition and pathname-based launch. PVS is not a
descriptor-backed execution sandbox and does not claim resistance to a hostile
same-UID actor. It also cannot prove which files or byte versions arbitrary
subject code chose to open internally. Use an appropriate sandbox, syscall
audit, isolated service account or hermetic execution system when that stronger
claim is required.

The evidence records launch profile `pvs-private-executable-snapshot/1`.
Because the acquired copy has a private path, programs that locate resources
through their executable path (`$0`, `/proc/self/exe`, `$ORIGIN`,
`@executable_path` or sibling-file lookup) may observe different location
semantics or fail. Prefer an explicitly declared interpreter plus a declared
script artefact, or a self-contained executable that does not depend on its
original filesystem location. PVS does not claim transparent support for every
possible executable-location convention.

## Platform details

Snapshot stability deliberately does not treat access-time changes caused by
reading a file as content mutation. Identity, byte length, write/change
metadata and the SHA-256 of the acquired bytes provide the relevant boundary;
this avoids the Windows `atime` false positive without ignoring content
changes.

Linux publication requires a C library exposing `renameat2` and a running kernel
and target filesystem supporting `RENAME_NOREPLACE`. macOS publication requires
`renamex_np` with `RENAME_EXCL`. Before any subject command runs, PVS probes the
required collision and success semantics in the output target's parent
directory. Missing symbols and `ENOSYS`, `EINVAL` or `EOPNOTSUPP` failures stop
the transaction without executing the subject. PVS never falls back to a plain
POSIX rename that could replace an existing package.

Windows run mode returns exit code 3 without publishing a package. It remains
disabled until equivalent Windows Job Object containment is implemented.

## Command-line workflows

```text
pvs inspect TARGET [--json]
pvs validate CASE [-o DIR] [options]
pvs run CASE [-o DIR] [options]
pvs verify TARGET [options]
```

`python -m pvs` is equivalent to the `pvs` console command.

### Inspect a case or evidence

```bash
pvs inspect examples/json-existing/pvs.yaml
pvs inspect examples/json-existing --json
```

A case directory must contain exactly one of `pvs.yaml` or `pvs.yml`. An
explicitly supplied case file may have another filename.

Evidence can also be inspected:

```bash
pvs inspect evidence-package/evidence.json
pvs inspect evidence-package
```

A directory containing `evidence.json` is treated as evidence. Evidence must
pass integrity verification before inspection succeeds. Displayed fields come
from the exact parsed record verified inside a private snapshot, with the matching
verified IDs and trust result. Inspection never rereads the mutable original for
trusted display content.

### Validate existing artefacts

Use `validate` when another program, experiment or pipeline has already
produced its files:

```bash
pvs validate examples/json-existing/pvs.yaml \
  --output json-evidence

pvs verify json-evidence
```

`validate` never executes `execution.command`, even when one is declared.

### Execute then validate

On Linux or macOS:

```bash
pvs run examples/command-python/pvs.yaml \
  --output command-evidence

pvs verify command-evidence
```

`run` requires an `execution` declaration. Before launching the subject, PVS
requires every declared output artefact to be absent and all required
non-output artefacts to be present. It never deletes stale subject output.

### Verify a record or package

Any file target is treated as standalone evidence:

```bash
pvs verify evidence-package/evidence.json
```

A directory target is treated as a package and requires `manifest.json`:

```bash
pvs verify evidence-package
```

Pin an identity obtained through an independent trusted channel:

```bash
pvs verify evidence-package \
  --expect-finding-id "pvs-finding:v1:sha256:<64-hex-digest>" \
  --expect-evidence-id "pvs:sha256:<64-hex-digest>" \
  --expect-package-id "pvs-package:sha256:<64-hex-digest>"
```

A standalone evidence file has no Package ID. Supplying
`--expect-package-id` for it always fails.

Successful `verify` means integrity checks passed. It does **not** mean the
scientific evidence status was PASS. An intact package whose authoritative
evidence status is FAIL or ERROR correctly verifies with exit code 0.

### Machine-readable operation

Every subcommand accepts `--json` and emits one compact JSON object on stdout,
including controlled error responses:

```bash
pvs validate case/pvs.yaml \
  --output evidence \
  --json > pvs-outcome.json
```

For `validate` and `run`, this JSON contains outcome metadata and paths, not the
complete check records. The authoritative check detail is in `evidence.json`.
`--json` does not change exit status.

### Validate/run options

| Option | Effect |
| --- | --- |
| `-o`, `--output DIR` | Select a new package directory |
| `--json` | Emit compact machine-readable outcome JSON |
| `--fail-on-warn` | Return 1 for an overall WARN; evidence remains WARN |
| `--embed-artifacts` | Force retained copies of present validated artefacts |
| `--no-embed-artifacts` | Disable artefact retention |
| `--no-html` | Force HTML reporting off |
| `--no-pdf` | Force PDF reporting off |

There are no CLI flags to force HTML or PDF on when a case disables them. The
Python API can explicitly override either setting.

An explicit output path is resolved relative to the caller's current working
directory. Its parent is created, but the target itself must not exist. Without
`--output`, PVS uses:

```text
<case-root>/pvs-output/<case-id>-<first-8-UUID-characters>/
```

## Writing a case

A case is an unaliased, I-JSON-compatible UTF-8 YAML mapping. This minimal case
validates an already-produced `result.json`:

```yaml
schema: pvs-case/2
id: cooling-series-001
version: 1.0.0
title: Cooling-series verification
description: Checks an existing temperature history.
classifications: [verification]

subject:
  name: example-thermal-model
  version: 1.0.0

artifacts:
  result:
    path: result.json
    role: output
    format: json

checks:
  - id: temperature-finite
    type: finite
    source: {artifact: result, pointer: /temperature, unit: K}
    unit: K

  - id: temperature-bounded
    type: range
    source: {artifact: result, pointer: /temperature, unit: K}
    unit: K
    minimum: 0.0
    maximum: 2000.0

  - id: temperature-nonincreasing
    type: monotonic
    source: {artifact: result, pointer: /temperature, unit: K}
    unit: K
    direction: nonincreasing
    absolute_tolerance: 0.0

  - id: final-temperature
    type: compare
    actual: {artifact: result, pointer: /temperature, reduce: last, unit: K}
    expected: {value: 300.0, unit: K}
    unit: K
    metric: relative
    tolerance: 1.0e-12
    scale_floor: 1.0e-300

package:
  embed_artifacts: true
  html: true
  pdf: true
```

With a neighbouring `result.json`:

```json
{
  "temperature": [1200.0, 800.0, 500.0, 300.0]
}
```

run:

```bash
pvs inspect pvs.yaml
pvs validate pvs.yaml --output evidence
pvs verify evidence
```

### Top-level fields

| Field | Purpose |
| --- | --- |
| `schema` | Must be `pvs-case/2` |
| `id`, `version`, `title` | Stable case identity and human title |
| `description` | Optional case description |
| `classifications` | `verification`, `validation`, `regression` and/or `cross-code-comparison` |
| `subject` | Declared name/version and optional revision/repository |
| `execution` | Optional local command used only by `run` |
| `artifacts` | Named subject, input, reference, output and auxiliary files |
| `references` | Scientific citations and derivation provenance |
| `checks` | Ordered generic checks and explicit criteria |
| `package` | Embedding and report policy |
| `metadata` | Caller-defined metadata object |

The normative schema is packaged at
`src/pvs/schemas/pvs-case.schema.json`; a reviewable distribution copy is at
`schemas/pvs-case.schema.json`. PVS authors and executes only
`pvs-case/2`; offline verification dispatches retained `pvs-case/1` definitions
to the frozen `pvs-case-v1.schema.json` contract.

PVS rejects duplicate keys, aliases, non-string mapping keys, unsafe YAML tags,
implicit date objects, unknown core fields, non-finite numbers, integers outside
the RFC 8785 interoperable range and invalid cross-references.

Evidence retains two case hashes:

- `raw_sha256`: exact YAML-byte identity;
- `semantic_sha256`: RFC 8785 identity of the parsed case definition.

### Migrating a v0.1 case

PVS verifies frozen v0.1/v0.2 evidence, but new transactions use
`pvs-case/2`. Migrate a case deliberately:

1. change `schema` to `pvs-case/2`;
2. remove any properties irrelevant to their check type or comparison metric;
3. add `unit` to every numeric source and check, using `"1"` when
   dimensionless;
4. replace bare numeric operands with `{value: ..., unit: ...}`;
5. add `netcdf_decoding: pvs-netcdf/1` to every NetCDF selector; and
6. split heterogeneous arrays with `component` or normalize them upstream so a
   single operand never carries multiple physical units.

Run `pvs inspect` before executing or validating. The closed schema reports a
case-definition error rather than guessing what a legacy field meant.

### Execution

```yaml
execution:
  command: [./my-solver, --input, input.json, --output, output/result.json]
  working_directory: .
  timeout_seconds: 600
  environment:
    OMP_NUM_THREADS: "1"
    SOLVER_MODE: deterministic
  expected_exit_codes: [0]
  random_seed: 12345
```

The command is an argument vector executed with `shell=False`. There is no
interpolation, pipe, redirection, globbing or shell expansion. PVS resolves and
snapshots `command[0]`, then launches that private byte snapshot while retaining
the declared command in evidence. The working directory must already exist
inside the case root. Standard input is disabled; stdout and stderr are captured
rather than streamed. The POSIX process group is contained through normal exit,
timeout or interruption.

The child inherits the caller's environment and receives the declared string
overrides. Evidence records only those overrides, not inherited variables.
`random_seed` is recorded in the resolved case; PVS does not set or enforce it
for arbitrary subject software.

### Artefacts

```yaml
artifacts:
  solver-source:
    path: solver.py
    role: subject
    format: text

  configuration:
    path: input.json
    role: input
    format: json

  published-values:
    path: reference.json
    role: reference
    format: json
    expected_sha256: abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789

  result:
    path: output/result.nc
    role: output
    format: netcdf
```

Roles are `subject`, `input`, `reference`, `output` and `auxiliary`. Formats
are `json`, `csv`, `netcdf`, `text` and `binary`.

Reference-role artefacts must declare a lowercase 64-hex `expected_sha256`.
Other roles may also be pinned. Paths use portable forward-slash relative
syntax, must be NFC-normalised and must stay inside the case root. Absolute
paths, backslashes, parent traversal, NULs, empty/dot segments, trailing
slashes and symlink components are rejected. Artefacts must be regular files.

During run mode, required non-output artefacts and expected hashes are checked
before execution. Changes to live non-output artefacts during execution,
changes to any artefact during validation, case mutation, missing required
artefacts or hash mismatches make provenance incomplete and the overall result
ERROR. Checks and embedded artefacts use the same acquired byte snapshots.

### Scientific references

```yaml
references:
  - id: published-decay-reference
    type: published
    citation: "Author, A. Example decay result, Journal 10 (2026)."
    locator: "https://doi.org/10.example/example"
    artifact: published-values
    source_location: "Table 2, row 4"
    accessed_utc: "2026-08-28"
    derivation: "Value transcribed directly and independently checked."
    notes: "Model assumptions and known limitations."
```

Reference types are `analytic`, `published`, `experimental`,
`canonical-fixture` and `cross-code`. PVS records this metadata but does not
retrieve or authenticate the source or judge its scientific suitability.

## Artefacts, readers and selectors

Checks access a declared artefact through a source selector:

```yaml
source:
  artifact: result
  pointer: /series/temperature
  reduce: last
  unit: K
```

At most one format-specific selector may be present:

| Format | Selector | Notes |
| --- | --- | --- |
| JSON | RFC 6901 `pointer` | Canonical non-negative array indexes only |
| CSV | `column` | Header name |
| NetCDF | `variable` | Requires `netcdf_decoding: pvs-netcdf/1` |

Text and binary artefacts can be identified, retained and checked for
existence, but PVS exposes no numeric selector for them.

- JSON is strict UTF-8. Duplicate keys, NaN/infinity, overflowing floats,
  unsafe integers and invalid Unicode are rejected.
- CSV uses an explicit comma/double-quote dialect with doubled-quote escaping,
  no escape character or initial-space skipping, and Python CSV `strict=True`.
  Malformed quoted fields produce ERROR; valid quoted commas, line breaks and
  doubled quotes remain supported. This is not a complete RFC CSV validator.
  Headers must be present, non-empty and unique, and every row must have
  exactly one field per header. Cells are parsed as integer, then float, then
  retained as strings.
  Column existence is checked against the cached headers before any reduction,
  including on header-only tables. An existing empty column has `size: 0`;
  a nonexistent column is an ERROR, including for optional checks.
- NetCDF uses the explicit `pvs-netcdf/1` profile: packed missing/validity
  values are masked, `_Unsigned` is applied, scale/offset storage decoding is
  performed in binary64, and masked integer or float values become NaN.
- JSON, CSV, text and binary artefacts, and selected NetCDF variables, are
  loaded into memory; PVS numeric checks are not streaming.

Reductions are `sum`, `mean`, `min`, `max`, `first`, `last` and `size`.
`size` permits empty or non-numeric data; every other reduction requires a
non-empty numeric value. Numeric operations use float64-compatible real values;
boolean and complex values are rejected.

Every numeric selector, literal operand and numeric check declares a unit,
including explicit dimensionless `"1"`. Unit strings are opaque and must match
exactly; PVS does not parse, normalise or convert them. A mismatch is an ERROR.
Literal operands use `{value: ..., unit: ...}`. The optional zero-based
`component` selector extracts one component along the final array axis before
reduction, allowing heterogeneous table columns to be checked as separate
quantities instead of assigning a false unit to an entire row.

For NetCDF, `pvs-netcdf/1` disables the library's automatic mask/scale path and
applies a documented order: raw packed read; optional signed-to-unsigned
reinterpretation; packed-domain fill/missing/validity masking; binary64
`scale_factor`/`add_offset` storage decoding; then NaN representation of masked
values. This is storage decoding, not unit conversion. A variable `units`
attribute, when present, must exactly match the selector unit (except a `size`
reduction, whose result is dimensionless). See
[`docs/case-format.md`](case-format.md)
for the normative edge cases.

## Checks and metrics

| Type | Purpose |
| --- | --- |
| `exists` | Require a regular-file artefact |
| `schema` | Validate JSON against a declared Draft 2020-12 schema artefact |
| `finite` | Require every selected numeric value to be finite |
| `range` | Apply declared lower and/or upper bounds |
| `monotonic` | Test a one-dimensional sequence and declared direction |
| `compare` | Compare actual and expected operands with a generic metric |
| `reference` | Perform the same comparison while recording a known `reference_id` |
| `conservation` | Evaluate a weighted scalar balance and tolerances |

JSON Schema checks enable format checking and allow only fragment-local
references. They use Draft 2020-12 when `$schema` is omitted and reject other
declared dialects, including in embedded resources. Literal data in `const`,
`enum`, `default` and `examples` is not treated as schema instructions. Remote,
external and file references are forbidden, and local references cannot promote
unchecked object data into schemas. See the [schema-check policy](schema-checks.md).

Checks are required by default. A false required criterion is FAIL; a false
non-required criterion is WARN. Reader or evaluation failures are ERROR, even
for a non-required check. A non-required source check with a missing artefact
can be SKIP; a non-required `exists` check on that same missing file is WARN
because the existence criterion evaluated normally. Checks continue after an
earlier check errors.

Each check type and each comparison metric is a separately closed schema
shape. Known but irrelevant properties are invalid—for example, an `exists`
check cannot carry a tolerance or conservation terms, and a `close` comparison
cannot carry the scalar `tolerance` used by other metrics. A criterion cannot
look operative while its evaluator silently ignores it.

If the artefact itself is required, its absence is also a provenance failure,
so the overall evidence is ERROR even if an individual optional check is SKIP.

### Range and monotonicity

Bounds are inclusive unless `inclusive_minimum` or `inclusive_maximum` is
false. Non-finite range values fail the criterion.

Monotonic checks require a one-dimensional array with at least two finite
values. Strict increasing/decreasing requires each step to exceed the declared
absolute tolerance; nondecreasing/nonincreasing permits opposing movement up
to that tolerance.

### Comparison metrics

Actual and expected operands must be non-empty, real, finite numeric values
with identical shapes. Their declared units and the check unit must match
exactly. PVS performs no NumPy broadcasting and no unit conversion.

| Metric | Gate |
| --- | --- |
| `absolute` | Scalar `abs(actual - expected) <= tolerance` |
| `relative` | `L2(actual - expected) / max(L2(expected), scale_floor) <= tolerance` |
| `l1` | `L1(actual - expected) <= tolerance` |
| `l2` | `L2(actual - expected) <= tolerance` |
| `linf` | `L∞(actual - expected) <= tolerance` |
| `close` | Elementwise `abs(actual-expected) <= atol + rtol*abs(expected)` |

`relative` requires an explicit positive `scale_floor`. `close` uses the case
fields `absolute_tolerance` and `relative_tolerance`. Every other metric uses
`tolerance`. An absolute tolerance has the check's unit; relative tolerances
are dimensionless, and `scale_floor` inherits the check's unit. No tolerance is
inferred or hidden inside the engine.

Evidence v3 uses comparison profile `pvs-comparison/2`. Selected gating
quantities must be finite. Supplementary error/reference norms use explicit
AVAILABLE values or UNAVAILABLE/OVERFLOW reasons. Thus `[1e308, 1e308]` against
zero can pass a `linf` check at tolerance `1e308` while recording an unavailable
L1 diagnostic. An overflowing selected norm, relative denominator, subtraction,
or closeness tolerance still produces ERROR. The norm arithmetic and exact
scientific tolerance boundaries are unchanged. Older v2 records retain their
original diagnostic-overflow policy. See the
[versioned numerical contract](numerical-contract.md#diagnostic-overflow-policy).

A `reference` check performs the same numerical operation as `compare` and
records the declared `reference_id`. It does not retrieve or authenticate the
citation. The case author remains responsible for binding the expected operand
to the intended reference artefact.

### Conservation

For every term, PVS sums the full selected array and applies its coefficient.
The scalar contributions are summed into `balance`, then gated by:

```text
abs(balance - expected)
    <= absolute_tolerance + relative_tolerance * abs(normalization)
```

`expected` and an optional explicit `normalization` must each resolve to one
finite scalar with the balance unit. Every term declares that same unit and its
coefficient is dimensionless in the case v2 profile. Without `normalization`, PVS
uses `abs(expected)`.

## Evidence packages

A normal package looks like:

```text
evidence-package/
├── case/
│   └── pvs.yaml
├── artifacts/              present when embedding is enabled
├── logs/                   present for run mode
│   ├── stdout.txt
│   └── stderr.txt
├── evidence.json           authoritative record
├── report.html             optional human rendering
├── report.pdf              optional human rendering
└── manifest.json           package file identities
```

`evidence.json` and `manifest.json` are always present in a successfully
published package. `case/pvs.yaml` retains the exact case bytes, even when the
original file was named `pvs.yml`. Validate mode does not produce execution
logs. Report and embedding settings follow the case unless explicitly
overridden through the CLI or Python API.

### Effective package policy

The three package switches are resolved before report or package bytes are
created:

| Switch | Schema default | Runtime override |
| --- | ---: | --- |
| `embed_artifacts` | `false` | CLI/API can force on or off |
| `html` | `true` | CLI can force off; API can force either value |
| `pdf` | `true` | CLI can force off; API can force either value |

Both `evidence.json` and `manifest.json` record every switch as, for example:

```json
"pdf": {"effective": false, "source": "runtime_override"}
```

The source is exactly one of `schema_default`, `case` or `runtime_override`.
The evidence also binds the manifest generation and `pvs-html/3` and
`pvs-pdf/3` report profiles. Verification requires the evidence and manifest
contracts to agree and requires the actual package contents to match the
effective policy. Deleting a required report, relabelling an override as a
case setting, or wrapping v3 evidence in a legacy manifest is therefore an
integrity failure. Package-policy differences change the Evidence and Package
IDs but do not change the Scientific Finding ID when the scientific projection
is otherwise identical.

PVS builds a package in a private sibling staging directory, verifies it, then
publishes it with a no-replace atomic operation. It never overwrites an
existing target directory.

### Authoritative evidence

The evidence record includes:

- run ID, mode, timestamps and PVS/schema versions;
- exact and semantic case identities plus the resolved definition;
- declared subject and reference metadata;
- command, working directory, declared environment overrides and seed;
- private launched-executable identity and declared-path boundary identities;
- normalized installed PVS implementation identity and build/source facts;
- runtime and dependency metadata;
- artefact identities across the transaction;
- every check declaration, observation, criterion and status;
- execution, provenance and overall summaries.

HTML and PDF are projections of this object. Their renderers do not evaluate
checks or tolerances independently. Each report begins with the concise status
table and adds a bounded per-check audit appendix showing the structured
declaration, actual and expected observations, metric, tolerances, errors,
units, failing samples or conservation terms as applicable. Referenced checks
also show the full citation, locator, source location, derivation, notes and
reference artefact identity carried by the evidence record.

### Embedded artefacts

With:

```yaml
package:
  embed_artifacts: true
```

PVS retains the exact snapshot bytes used by the checks beneath
`artifacts/<role>/<artifact-id>/`. A later machine can therefore rehash those
bytes while verifying the package. Without embedding, the evidence still
records the original artefact hash and size, but a transferred package does
not contain those external bytes for independent rehashing.

Embedding improves portability but can materially increase package size.
Review data-distribution rights and confidentiality before embedding large or
restricted inputs, references or outputs.

### Manifest coverage

`manifest.json` identifies every regular package file except itself by
normalised relative path, role, byte size and SHA-256. Verification requires
exact coverage. A missing declared file, undeclared extra file, changed byte,
unsafe path, symlink, non-regular entry or case-insensitive path collision
fails package integrity.

## Evidence identity and verification

PVS uses four identifiers:

| Identifier | Construction | Meaning |
| --- | --- | --- |
| Run ID | UUID for a run or validate transaction | Which transaction is being discussed |
| Evidence ID | `pvs:sha256:` + SHA-256 of RFC 8785 canonical `record` | Exact authoritative evidence content |
| Scientific Finding ID | `pvs-finding:v1:sha256:` + SHA-256 of a versioned RFC 8785 projection | Exact declared finding independent of transaction metadata |
| Package ID | `pvs-package:sha256:` + SHA-256 of canonical manifest `package` | Exact manifest content and declared file set |

`evidence.json` is an envelope:

```json
{
  "schema": "pvs-evidence/3",
  "record": {
    "...": "authoritative evidence"
  },
  "integrity": {
    "canonicalization": "RFC8785",
    "algorithm": "sha256",
    "digest": "...",
    "evidence_id": "pvs:sha256:...",
    "finding": {
      "projection": "pvs-finding-projection/1",
      "canonicalization": "RFC8785",
      "algorithm": "sha256",
      "status": "ISSUED",
      "digest": "...",
      "finding_id": "pvs-finding:v1:sha256:..."
    }
  }
}
```

The digest is outside `record`, avoiding a recursive self-hash. Run UUIDs,
timestamps and durations are inside the record, so scientifically equivalent
reruns normally have different Evidence IDs.

The Scientific Finding ID is issued only for complete provenance with no
`ERROR`. It projects default-normalised scientific declarations, the validation
identities acquired for declared subject/input/reference artefacts, exact
structured criteria, observations and statuses. It excludes transaction
metadata and display prose. It also excludes whole output byte hashes:
unchecked output content can differ while the declared findings remain exact.
Complete scientific reference declarations, including derivation and source
notes, remain bound. Declare executable/source bytes as a `subject` artefact
when they must be part of the scientific identity. The Finding ID describes
equality of the declared scientific finding; it does not prove the subject
causally read every declared input. See
[`docs/evidence-and-verification.md`](evidence-and-verification.md)
for the
precise boundary.

### PDF identity

The PDF carries the full Evidence ID in its metadata, report body and footer
on every page. Its background watermark uses a shortened Evidence digest for
legibility. This is not the PDF's own hash: the final PDF SHA-256 is stored in
`manifest.json`, because putting a file's final digest inside that same file
would change the digest.

The `pvs-pdf/3` renderer derives its PDF creation/modification timestamp and
document-ID seed from the evidence `run.finished_at` value, truncated to a UTC
second. It deliberately ignores the verifier process's `SOURCE_DATE_EPOCH`, so
plain offline verification can replay a v3 report without reconstructing the
producer's build environment. Frozen v1/v2 renderers retain their original
behavior for byte-compatible legacy verification.

### What verification establishes

For standalone evidence, `pvs verify` checks strict JSON, schema validity,
canonical digest, Evidence ID, case semantics, selected cross-field
invariants, identifier uniqueness and recomputed summary counts/status. Evidence
v2/v3 also receive type-specific criterion/observation/status checks, numerical
summary constraints and Scientific Finding eligibility, digest and ID checks.
Legacy v1 receives the shared size-domain, evaluation-dtype and complete-source
metadata checks, including zero-difference/PASS requirements for identical-source
comparisons and conservation normalization relationships. It does not receive
the general v2/v3 arithmetic, norm-bound or summary-interval checks for distinct
sources; verifying v1 does not establish those stronger consistency claims.

For a package it additionally checks the manifest and Package ID, safe paths,
exact coverage, retained file hashes and sizes, retained case bytes and
semantics, embedded artefact and log bindings, and report identity.
Deterministic HTML replay uses the supported schema generation’s frozen
renderer. Deterministic PDF replay also requires the recorded and current
ReportLab versions to match. If exact replay is unavailable, package
verification fails closed unless the caller pins the exact Package ID; that
pin independently binds the manifest entry containing the final PDF hash.

PVS 1.0.0a1 verifies frozen evidence/manifest v1 and v2 packages with their original
HTML/PDF renderers. It never upgrades or rewrites those bytes. New transactions
accept `pvs-case/2` and issue `pvs-evidence/3` with `pvs-manifest/3` and report
profiles v3. Older v2-only verifiers cannot validate v3 packages. A v0.1 case
still requires explicit migration. Finding projection v1 remains unchanged;
it binds the full observation, including the comparison profile and diagnostic
availability tags, so re-evaluated comparisons generally receive new Finding IDs.

Verification snapshots files through single open handles before interpreting
them, then re-enumerates and re-identifies the source namespace. This prevents
verification from hashing one file and parsing a swapped replacement.

Verification does **not**:

- execute the subject;
- independently recalculate observations from absent original artefacts (v2/v3
  verification checks that recorded observations and criteria imply the recorded
  status; legacy v1 receives the narrower metadata checks described above);
- read original artefacts that were not embedded;
- decide whether a source or model is scientifically correct;
- authenticate who issued an unsigned package.

### Implementation and release provenance

Every current record contains a `pvs-installed-package-tree-sha256-v1` identity.
It covers normalized paths, sizes and SHA-256 values for the importable `pvs/`
tree while excluding derived bytecode and installer metadata, then hashes that
manifest using RFC 8785. Build provenance separately binds the exact released
source archive using `pvs-source-archive-sha256-v1` and records the
`SOURCE_DATE_EPOCH`. The official assembled-release profile deliberately
records repository, revision and dirty state as null and binds the published
source ZIP directly; PVS does not turn an arbitrary local Git checkout into a
public-source claim. The lower-level build API can carry explicit VCS facts,
but that is not an accepted release bundle profile.

Wheel and source-distribution hashes cannot be safely embedded into the files
that define those hashes. They remain external release facts in `SHA256SUMS`,
CycloneDX 1.6 SBOMs and the unsigned in-toto Statement using the SLSA
Provenance v1 predicate. `uv.lock` is the exact tested dependency closure;
compatible dependency ranges remain a separate forward-compatibility test.
See
[`docs/release-integrity.md`](release-integrity.md).

### Trust labels

| Trust | Meaning |
| --- | --- |
| `unpinned` | Internally consistent unsigned content; caller supplied no identity |
| `finding-pinned` | Scientific Finding ID equals the caller-supplied identity |
| `record-pinned` | Evidence ID equals the caller-supplied identity |
| `package-pinned` | Package ID equals the caller-supplied identity |
| `unverified` | At least one integrity or pinning check failed |

A malicious party can rewrite an unsigned package consistently and obtain new,
valid IDs. Pinning proves equality with a value obtained through an independent
trusted channel; it does not prove who first published that value. Digital
signatures and trusted issuer keys are roadmap work.

## Integrating PVS with other software

PVS is machine-in/machine-out infrastructure. The subject can be Python, C++,
Fortran, Rust, MATLAB, a laboratory export pipeline or any other system that
produces declared files.

### Validate outputs your software already created

Have the subject produce a stable artefact such as:

```text
validation/output/result.json
```

Place `pvs.yaml`, its reference files and schemas under `validation/`, then
call:

```bash
pvs validate validation/pvs.yaml \
  --output validation-evidence \
  --json > validation-outcome.json

pvs verify validation-evidence \
  --json > validation-verification.json
```

Use the process exit code for the automation gate and the JSON response for
IDs and paths. Preserve the whole evidence package as the durable result. This
pattern works on Windows, Linux and macOS.

Windows PowerShell 5.1 callers should capture native exit codes explicitly and
write JSON as UTF-8 without a BOM:

```powershell
$utf8 = New-Object System.Text.UTF8Encoding($false)

$outcomeLines = @(& $PY -m pvs validate .\validation\pvs.yaml `
  --output .\validation-evidence --json)
$validationExit = $LASTEXITCODE
$outcomeText = ($outcomeLines | ForEach-Object { "$_" }) -join "`n"
[System.IO.File]::WriteAllText(
  (Join-Path $PWD "validation-outcome.json"), $outcomeText + "`n", $utf8
)
if ($validationExit -ne 0) { throw "PVS validation exited $validationExit" }
$evidence = Get-Content .\validation-evidence\evidence.json -Raw | ConvertFrom-Json
if ($evidence.record.summary.status -cne "PASS") {
  throw "Scientific validation status was $($evidence.record.summary.status), not PASS"
}

$verificationLines = @(& $PY -m pvs verify .\validation-evidence --json)
$verificationExit = $LASTEXITCODE
$verificationText = ($verificationLines | ForEach-Object { "$_" }) -join "`n"
[System.IO.File]::WriteAllText(
  (Join-Path $PWD "validation-verification.json"),
  $verificationText + "`n",
  $utf8
)
if ($verificationExit -ne 0) { throw "PVS verification exited $verificationExit" }
```

### Let PVS launch the subject

On Linux or macOS, declare a no-shell command:

```yaml
execution:
  command: [./solver, --input, input.json, --output, output/result.json]
  working_directory: .
  timeout_seconds: 900
  environment:
    OMP_NUM_THREADS: "1"
  expected_exit_codes: [0]
```

Then run:

```bash
pvs run validation/pvs.yaml \
  --output validation-evidence \
  --json > validation-outcome.json
```

The executable need not know that PVS exists. It needs only a stable command
contract and declared file outputs.

### Python API

```python
from pathlib import Path

from pvs import validate_case, verify_target

outcome = validate_case(
    Path("validation/pvs.yaml"),
    output_dir="validation-evidence",
    embed_artifacts=True,
    render_html_report=True,
    render_pdf_report=True,
)

verification = verify_target(outcome.output_directory)
if not verification.valid:
    raise RuntimeError("generated evidence failed integrity verification")

print(outcome.status.value)
print(outcome.run_id)
print(outcome.evidence_id)
print(outcome.finding_id)
print(outcome.package_id)
```

On Linux or macOS, `run_case(...)` uses the case's execution declaration.
Pinned verification is also available:

```python
from pvs import verify_target

verification = verify_target(
    "validation-evidence",
    expect_finding_id="pvs-finding:v1:sha256:<expected-digest>",
    expect_evidence_id="pvs:sha256:<expected-digest>",
    expect_package_id="pvs-package:sha256:<expected-digest>",
)
```

The public API exports `RunOutcome`, `VerificationResult`, `run_case`,
`validate_case`, `verify_target` and `__version__`.

### CI gate

```bash
set -euo pipefail

pvs validate validation/pvs.yaml \
  --output pvs-evidence \
  --fail-on-warn \
  --json > pvs-outcome.json

pvs verify pvs-evidence --json > pvs-verification.json
```

Archive `pvs-evidence/`, `pvs-outcome.json` and
`pvs-verification.json`. Do not reduce the integration to a green badge: keep
the package and publish or retain its Evidence ID when later comparison
matters.

### Adapter boundary

An adapter may know how to invoke a solver, locate its output, expose build
metadata, or translate a proprietary result into JSON, CSV or NetCDF. It must
not silently choose the accepted model, scientific reference or tolerance.
Those judgements belong in a reviewed case or benchmark pack.

### Integration checklist

For a new solver, laboratory pipeline or proprietary application:

1. Make the application write a stable JSON, CSV or NetCDF artefact. A thin
   adapter may normalize a proprietary format, but keep its source as a
   declared `subject` artefact.
2. Put a versioned `pvs.yaml`, reference artefacts and any JSON schemas beside
   the integration, not inside PVS core.
3. Declare exact units on every numeric selector, literal and check. Use `"1"`
   for dimensionless quantities and convert units in a reviewed upstream
   adapter if conversion is needed.
4. Pin reference-role artefacts with `expected_sha256` and retain citation,
   locator, source location, derivation and anomaly notes.
5. Use `validate` if your system already owns execution. Use `run` only when a
   local POSIX no-shell command is the correct containment boundary.
6. Give every transaction a new output directory, gate on the process exit
   code, and retain the complete package plus machine-readable CLI response.
7. When comparing machines or reruns, choose the identity that answers the
   question: Finding for the declared scientific result, Evidence for the exact
   transaction, or Package for the exact transferred file set.

PVS does not require an SDK inside the subject. A normal external pipeline is
simply:

```bash
set -euo pipefail

my-solver --config validation/input.json --output validation/result.json
pvs validate validation/pvs.yaml --output "pvs-evidence-${CI_JOB_ID}" --json
pvs verify "pvs-evidence-${CI_JOB_ID}" --json
```

## Examples and adapters

| Example | Purpose |
| --- | --- |
| `examples/json-existing` | Validate existing JSON without execution |
| `examples/csv-existing` | Validate an existing numeric CSV series |
| `examples/command-python` | Run a small Python model and validate JSON |
| `examples/netcdf-command` | Run a model that writes NetCDF and validate a variable |
| `examples/python-api` | Invoke PVS as a Python library |
| `examples/boutpp-command` | Reviewable generic BOUT++ command/NetCDF template |

The BOUT++ example is a template, not a bundled runnable benchmark. Replace
its software version, invocation, output names, variables, checks, tolerances
and references for the actual case. It contains no application-specific physics
and adds no BOUT++ branch to core.

### Built-in benchmark library

The source archive includes four versioned packs in
[`benchmark-packs/catalog.json`](../benchmark-packs/catalog.json):

| Pack | Reference content | Demonstration scope |
| --- | --- | --- |
| `fusion-reactivity-v1` | 30 published NRL D-T, total D-D and D-He3 reactivities | SI conversion and reaction-rate normalisation |
| `plasma-scales-v1` | 30 analytical values across three declared deuterium states | Screening, frequencies, thermal and magnetic scales, E-cross-B drift |
| `classical-physics-v1` | 36 analytical values | Harmonic oscillation, heat diffusion and circular orbits |
| `plasma-physics-reference-v1` | Existing equations, constants and published table anchors | Collisions, equilibration, radiation, reactivity and stopping fractions |

With PVS installed, run all packs from the extracted source root on Windows,
Linux or macOS:

```bash
python tools/run-benchmark-packs.py --output benchmark-evidence
```

Use a fresh output directory. The helper externally prepares each example,
validates its output, verifies the package, and retains a summary and per-case
JSON/HTML/PDF evidence. It leaves the source packs and frozen references unchanged.
The packs are distributed in the source archive; installing the wheel alone
does not install this example library.

The new reaction table contains published numerical benchmarks, not experimental
measurements. Its example tests an adapter, not a physical reactivity predictor.
Plasma scales and classical cases are analytical verification targets at declared
inputs. See the [benchmark guide](benchmark-library.md) for source scope,
tolerances and replacing the demonstration outputs with your own solver results.

### Audited plasma-physics reference pack

`benchmark-packs/plasma-physics-reference-v1` is a public-source reference case
pack. It is separate from PVS core and is not a solver adapter. It contains
published equations, small numerical table transcriptions and explicit reference
derivations. Private implementation audits and their fingerprints are excluded.

It independently derives or transcribes anchors from the NRL Plasma Formulary,
Bosch-Hale, Spitzer-Harm, Evans and CODATA sources. The pack records
derivation decisions, transcription corrections, reviewed source-copy identities
and the unresolved Bosch-Hale erratum. Visual review of the Evans source confirms that the two disputed
table entries are printed as 0.45 and 0.83; the earlier 0.65 and 0.63 readings
were OCR/transcription errors in the pack, not anomalies in the publication.
The revised case is version 3.0.0 within the retained pack-family directory.
Its closed case v2 declaration separates heterogeneous Evans table components
into explicitly unit-bearing checks. The insufficiently sourced ionisation
fixture is excluded. The pack guide records the limits of each reviewed source.

On Linux or macOS:

```bash
pvs run benchmark-packs/plasma-physics-reference-v1/pvs.yaml \
  --output plasma-reference-evidence

pvs verify plasma-reference-evidence
```

A PASS means the independent derivation reproduced the frozen values at the
declared regression tolerances and the retained artefacts matched their
declared identities. Testing a production solver requires its outputs as the
declared subject of a separate case. The reference example does not establish
that any production solver is valid, or universally validate the source models.
See
[`docs/physics-source-audit.md`](physics-source-audit.md)
and the
[pack README](../benchmark-packs/plasma-physics-reference-v1/README.md).

## Architecture and boundaries

| Layer | Responsibility |
| --- | --- |
| Cases and benchmark packs | Scientific meaning, sources, tolerances and claim scope |
| Adapters and commands | Translation between external software and generic artefacts |
| PVS core | Parsing, readers, checks, provenance, identity, reporting and verification |

Core understands generic files, arrays, scalars, commands, tolerances,
metrics, hashes, provenance, evidence and reports. It contains no policy branch
for any solver, physical process, laboratory or particular published model.

During validation PVS confines paths, rejects symlinks, snapshots artefacts
through single open handles, evaluates checks only against those immutable
bytes, and re-identifies the live artefacts afterward. During run mode it also
requires clean output paths, acquires and launches a private byte snapshot of
the resolved executable, verifies that acquired executable after execution,
checks non-output artefacts at transaction boundaries, captures logs, and
contains the POSIX process group through completion or timeout. These boundary
identities do not establish which files arbitrary subject code opened.

If execution or provenance is incomplete, the overall evidence becomes ERROR
even when individual numerical checks happen to pass.

## Status and exit codes

Status priority is:

```text
ERROR > FAIL > WARN > SKIP > PASS
```

| State | Meaning |
| --- | --- |
| `PASS` | All checks passed, execution/ingestion succeeded and provenance is complete |
| `WARN` | A declared non-required criterion was false |
| `FAIL` | A required scientific or numerical criterion was false |
| `ERROR` | PVS could not establish a complete result because of execution, parsing, evaluation, provenance or reporting |
| `SKIP` | A declared optional source check could not be performed |

FAIL and ERROR are intentionally different: FAIL establishes that a criterion
was not satisfied; ERROR means a trustworthy result could not be established.
Check counts cover declared checks only, so a provenance error can make the
overall status ERROR while the check count contains no ERROR result.

| Exit | Meaning |
| ---: | --- |
| `0` | Successful inspect; valid integrity verification; or PASS/WARN/SKIP outcome unless warned otherwise |
| `1` | Overall FAIL, or WARN with `--fail-on-warn` |
| `2` | CLI usage or case-definition error |
| `3` | Overall ERROR, unsupported execution platform, runtime/report/internal error |
| `4` | Evidence/package integrity or identity failure |
| `130` | Interrupted by the user |

Automation must use the exit code rather than scrape terminal banners. A valid
integrity verification returns 0 regardless of whether the evidence's
scientific status is PASS, WARN, FAIL or ERROR.

## Security

A case with an `execution` declaration requests execution of a local program
with the current user's permissions. PVS uses an argument vector and no shell,
launches a private byte snapshot of `command[0]`, rejects unsafe artefact paths,
symbolic links and Windows reparse points,
forbids external JSON Schema references, and snapshots bytes before
interpreting them. Portable paths also reject Windows drive-relative names,
alternate data streams, device names, control/invalid characters and trailing
dot/space aliases on every platform. Package walking fails closed on scan
errors rather than silently omitting an unreadable subtree. These controls do
not make it a security sandbox and do not trace the subject's own file opens.

Do not run an untrusted case merely because it is valid YAML. Review its
command and use an external sandbox, container, VM or isolated worker suitable
for the risk. Commands, declared environment values, logs and embedded
artefacts can contain secrets; PVS does not redact them.

PDF reports are parsed only after package, evidence and supplied identity checks
succeed. The supported pypdf dependency is at least 6.18.1, including in the
frozen lock. Parsing is still performed in the verifier process: valid hashes
do not make untrusted PDF content safe or impose CPU/memory limits. Deploy
verification of hostile packages in an externally resource-limited worker.

Report vulnerabilities privately using
[`SECURITY.md`](../SECURITY.md),
not a public issue before a fix is available.

## Limitations and roadmap

v1.0.0a1 deliberately excludes:

- Windows subject execution;
- digital signatures and issuer authentication;
- remote reference retrieval;
- scheduler, MPI and container orchestration;
- arbitrary expressions or `eval`;
- inferred tolerances, unit parsing or unit conversion;
- uncertainty covariance and propagation;
- native HDF5 readers beyond NetCDF-compatible workflows;
- convergence or manufactured-solution orchestration;
- solver-specific scientific policy in core.

Other practical limits are that citations and human version strings are
recorded rather than independently proven, inherited environment state is not
exhaustively recorded, unembedded original artefacts cannot be rehashed from a
transferred package, arbitrary subject file consumption is not traced, numeric
readers load selected data into memory, outputs are immutable publication
targets, and schema compatibility is not guaranteed before v1.0.

Roadmap candidates include Ed25519 or equivalent signatures, trusted issuer
keys, Windows Job Object containment, convergence/MMS workflows, scheduler and
HPC metadata, HDF5, JUnit projections, cross-code orchestration, uncertainty
handling and independently published evidence indexes. These are not present
in v1.0.0a1 and have no implied delivery date.

