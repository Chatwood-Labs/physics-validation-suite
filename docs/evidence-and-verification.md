# Evidence and verification

PVS uses three content identities and one execution identifier.

| Identifier | Construction | Meaning |
| --- | --- | --- |
| Run ID | UUID generated for the transaction | Which execution or ingestion event is being discussed |
| Evidence ID | `pvs:sha256:` plus SHA-256 of RFC 8785 canonical `record` bytes | Exact identity of the authoritative scientific evidence record |
| Scientific Finding ID | `pvs-finding:v1:sha256:` plus SHA-256 of the RFC 8785 canonical Finding projection | Exact scientific claim independent of transaction metadata |
| Package ID | `pvs-package:sha256:` plus SHA-256 of RFC 8785 canonical `package` bytes | Exact identity of the package manifest and its declared file set |

## Evidence envelope

`evidence.json` has three top-level members: a schema label, the authoritative
`record`, and `integrity`. The digest is outside `record`, so it can identify the
record without a recursive self-hash.

Strict JSON parsing rejects duplicate object keys and non-finite numbers.
Canonicalisation is RFC 8785 and the hash algorithm is SHA-256. All check
parameters, observed values, declared tolerances and statuses are recorded in
the same object that is hashed.

PVS 1.0.0a1 emits evidence v3, manifest v3 and HTML/PDF profiles v3. Case syntax
remains `pvs-case/2`. Comparison observations use `pvs-comparison/2`, with
explicit finite or unavailable supplementary diagnostics and finite scientific
gates. See [the numerical contract](numerical-contract.md). Named v1/v2 schemas
are immutable; their packages retain their original deterministic renderers.
Older verifiers that support only evidence v2 cannot validate v3 packages.

## Activity timing

From producer version 0.3.4, `record.run.mode` defines the interval measured by
`started_at`, `finished_at` and `duration_seconds`:

| Mode | Measured interval | Subject executed? |
| --- | --- | --- |
| `validate` | Starts immediately before private validation-snapshot acquisition; ends after every check has returned | No; command and return code remain null |
| `run` | The runner's subject-command interval, including its completion or failure | Yes, unless execution preflight prevented it |

Duration uses a monotonic performance counter; UTC timestamps describe the
interval endpoints. Validate timing includes snapshot acquisition and all check
outcomes, including evaluated ERROR results. It excludes case loading, initial
artifact resolution, post-validation provenance checks, artifact retention,
report generation, self-verification and atomic publication. These fields are
activity timing, not the complete API-call or package-publication duration.

For compatibility, `record.execution` mirrors the same timing fields exactly;
its `mode` must be interpreted with them. An execution object with mode
`validate` describes ingestion, not a subject process. No new timing field or
schema generation is introduced, and verifier equality is unchanged. Older
validate records retain their original timings; before 0.3.4 those timestamps
were captured before snapshot acquisition and did not measure validation work.
They cannot be retroactively interpreted as validation duration.

## Scientific Finding ID

`pvs-evidence/2` and `pvs-evidence/3` carry closed issued/not-issued Finding metadata under
`integrity.finding`. A verifier rebuilds the allowlisted
`pvs-finding-projection/1` directly from the authoritative record; it never
trusts a stored projection. The projection contains:

- declared subject name, version, revision and source repository;
- case ID, version and classifications;
- default-normalised check declarations and selectors;
- complete scientific reference declarations, including source locators,
  access date, derivation and notes;
- validation-time SHA-256 and size for every declared subject, input and
  reference artefact;
- exact structured criterion, observation, status and reference linkage for
  every check.

It excludes run UUIDs, mode, commands, executable resolution, run timestamps,
durations, environment, PVS version/build data, logs, package and report paths,
case/report formatting, descriptions, result summaries, notes and derivation
prose outside scientific reference declarations. Consequently a `run`
transaction and a later `validate` transaction can have the same Finding ID.
To make executable or source bytes part of the
scientific identity, declare them as a `subject` artefact; the opportunistic
resolved-executable hash is transaction provenance and is not projected.

A Finding ID is issued only when provenance is `COMPLETE`, execution/ingestion
succeeded, the overall status is not `ERROR`, and no check has status `ERROR`.
`FAIL`, `WARN` and `SKIP` are eligible outcomes: they are scientific findings,
not failures to establish provenance.

The projection remains version 1: its field-selection rule is unchanged and
already includes each complete observation, so comparison profiles and explicit
diagnostic availability are bound to the Finding ID. Re-evaluating a v2
comparison under the v3 contract generally changes its Finding ID, even when
the selected scientific outcome is unchanged. Existing IDs are never relabelled.

The Finding ID is deliberately not a whole-output digest. For output and
auxiliary artefacts, the projection records the declaration needed to interpret
selectors and the exact structured observations produced by checks, but not
the artefact SHA-256. Unselected fields, formatting and other unchecked output
bytes can therefore differ without changing the Finding ID. That equivalence
means only “PVS established the same declared, observed findings”; it does not
mean the complete outputs were byte-identical or scientifically equivalent in
any undeclared respect. Evidence IDs and recorded artefact snapshots retain the
whole-file identities.

## Package manifest

`manifest.json` lists every regular file in the package except itself. Each
entry records a normalised relative path, role, size and SHA-256. Verification
requires exact coverage: undeclared extra files and declared missing files both
fail. Absolute paths, parent traversal, path normalisation collisions and
symlinks are rejected.

The PDF contains the full Evidence ID in its metadata, report body and visible
footer on every page; a shortened digest is used only in the background
watermark. The PDF's final byte hash is stored in the manifest. This avoids the
impossible requirement for a file to contain its own final digest.

## What `pvs verify` establishes

For a standalone `evidence.json`, verification checks strict JSON, schema,
canonical digest, Evidence ID, identifier uniqueness and internally recomputed
summary counts/status. For `pvs-evidence/2` and `/3`, it also recomputes Finding
eligibility, digest and ID. Standalone `pvs-evidence/1` records remain
verifiable but do not issue a Finding ID.

V2/v3 consistency checks enforce declared units, known operand shapes,
scalar-only metrics and finite literal subtraction before accepting completed
PASS/FAIL/WARN observations. Literal reconstruction distinguishes unavailable
artifact values from valid and known-invalid operands. Recomputed identifiers
cannot make these contradictions consistent. Honest ERROR observations remain
verifiable as evidence that evaluation failed; their messages do not establish
the historical cause. Missing-input SKIPs require a missing input used by that
check, without a recorded resolution error. Completed input-dependent checks
require available validation snapshots.

Legacy v1 uses the shared size-domain, evaluation-dtype and complete-source
metadata checks, including conservation normalization and identical-source
zero-difference/PASS requirements. It does not run the general v2/v3 criterion,
arithmetic, norm-bound or summary-interval checks for distinct sources. A valid
v1 result therefore establishes a narrower observation-consistency contract.

For a package directory it additionally checks the manifest schema and digest,
Package ID, safe paths, exact coverage, every file hash and size, retained case
and artefact identities, and the Evidence ID carried by the HTML/PDF reports.
PVS first acquires each regular file through one open handle into a private
snapshot and interprets only those bytes. It re-enumerates and re-identifies the
source namespace before returning, so a file swap cannot make it hash one
record and parse another.

Evidence inspection uses this same verification path. Its preliminary JSON read
only identifies the input format. All displayed scientific and run fields come
from the exact parsed record validated inside the private snapshot; IDs and trust
come from that verification result. The private snapshot is removed before return,
while the verified record remains in memory. No display field is reread from the
original target. A failed package contract also prevents inspection even when its
standalone record would verify. Changes after acquisition cannot change the
displayed record, but verification is not a promise that a mutable source stays
unchanged after that acquisition.

Verification is deliberately an integrity operation. It does not re-run the
subject software and does not re-evaluate the scientific checks. `pvs validate`
performs checks against existing artefacts; `pvs run` executes the declared
subject and then performs them.

## Trust boundary

A valid unsigned PVS package proves that its content is internally consistent
with its identifiers. It does not prove who issued it. Anyone can construct a
different valid package with a different Evidence ID. Issuer authenticity,
trusted keys and digital signatures are roadmap features.

Until signatures exist, an Evidence ID becomes externally meaningful when it
is independently pinned: for example in a release checksum file, source-control
record, paper supplement or other trusted channel.

Use `--expect-finding-id`, `--expect-evidence-id` or `--expect-package-id` to
compare against such a trusted value. Verification output calls a successful
unsigned result `unpinned`, `finding-pinned`, `record-pinned` or
`package-pinned`; every invalid result is `unverified`. A Finding pin establishes
only the projected scientific claim, while Evidence and Package pins establish
the progressively wider exact transaction records. Pinning proves equality to
the caller-supplied identity, not who originally issued it.

## Norm summary consistency in 0.3.0

Offline verification uses recorded shape and L-infinity error to enforce exact
zero and separate binary64-aware upper bounds on L1 and scaled L2. Available
values above their bound and unavailable diagnostics with a finite bound are
rejected, even after all internal identifiers have been recomputed. These rules
apply to completed comparisons under historical v2 and current v3. Legacy v1
receives the source-relation zero-difference check for identical sources, but
does not execute these general norm bounds. The producer's gating arithmetic
and evidence/report formats have not changed.

The bounds are necessary consistency tests, not a reconstruction of unknown
artifact arrays or proof that every remaining summary is realizable. Literal
operands continue to be checked by exact recomputation. This correction changes
acceptance of contradictory records; it does not create new identifiers for
valid existing records or weaken the meaning of externally pinned identities.
See [the derivation](numerical-contract.md#record-visible-norm-bounds-030).

## Summary-only implications

Verification also rejects contradictions established by extrema, counts and
absolute tolerance without reading an artifact. PVS 0.3.2 adds the closeness
absolute floor, single-finite-value extrema equality, range/monotonic failure
count bounds and deterministic first-index samples when every value fails.
These apply to v2/v3 observations, not the legacy v1 metadata-only path. They
add no approximate equality allowance and do not change the producer's
numerical profile.
See [the verifier review map](verifier-architecture.md#032-summary-consistency-correction).


## Operation-derived observation constraints

The verifier carries `size` as a finite, nonnegative safe integer domain as well
as a scalar shape. It checks every recorded occurrence, including comparison
operands, finite/range extrema and conservation raw totals/normalization. Integral
binary64 observations such as `2.0` remain valid. Finite/range `dtype` is the
post-conversion `float64` evaluation dtype, not the artifact storage dtype.
These facts apply to all supported evidence generations through their respective
compatibility paths. Historical schemas, fixtures, units and renderers are not
rewritten. See [the numerical contract](numerical-contract.md) for exact scope.

## Identical source selections

A completed conservation check must record equal unweighted totals for identical
complete source specifications. Its expected scalar must agree with a matching
term source; explicit normalization must agree with the absolute value of a
matching term or expected source. Without explicit normalization, its value is
`abs(expected)`. Normalization is always finite and nonnegative. Successful
scalar uses include size-one arrays. Coefficients do not change raw source values.

A completed compare/reference check selecting the same source twice requires
identical scalar values, zero error norms, zero closeness failures and PASS under
the declared nonnegative tolerances. Relative diagnostic availability follows the
applicable evidence generation; supplementary reference overflow remains distinct
from an exactly zero difference.

Source identity uses canonical JSON bytes of the complete specification. Key
order is irrelevant; artifact references, selectors, components, reductions and
units remain significant. Signed zeros compare equal. These relations apply to
current and historical completed observations without adding modern unit or norm
rules to older formats. ERROR/SKIP make no completed-value claim. Different
expressions and separate checks are not inferred equivalent, and scientific
artifacts are not replayed. See [the verifier contract](verifier-architecture.md#complete-source-relationships-in-0310).
