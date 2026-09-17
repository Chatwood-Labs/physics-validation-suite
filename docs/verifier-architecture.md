# Offline verifier contracts

PVS 1.0.0a1 retains `pvs.verify.verify_target` and `VerificationResult` as the
public entry points. The orchestration path acquires immutable bytes, validates
the schema, checks producer and observation contracts, binds package contents,
checks any supplied external identity pins, then replays the correct report
generation. Failed package/evidence bindings or mismatching supplied pins prevent
PDF parsing and replay; named failed checks retain the reason parsing was not attempted.

The internal `_verify_target` result pairs that public verification object with
the exact parsed record from the private snapshot. It exposes no record on failed
verification. `inspect` consumes this pair; `verify_target` returns only the public
verification object. Its signature and serialized result stay unchanged. Neither
consumer reloads the mutable original to obtain trusted display fields.

## Review map

| Module | Responsibility | Main entry points/types |
| --- | --- | --- |
| `pvs.verify` | Package acquisition, orchestration, reports, external pins | `verify_target`, `_verify_package` |
| `pvs.verification.schemas` | Select immutable named schema generations | `_schema_errors`, `_manifest_schema_errors` |
| `pvs.checks.inspection` | Inspect available operand facts and completed-check eligibility | `LiteralOperand`, `UnavailableOperand`, `ValueDomain`, `inspect_comparison` |
| `pvs.checks.units` | Exact declared-unit relationships shared with evaluation | Source, comparison and conservation unit contracts |
| `pvs.verification.observations` | Observation validation and scientific decisions | `validate_observation`, `CompletedObservation`, `UnevaluatedObservation`, `InvalidObservation` |
| `pvs.verification.comparisons` | Parse and verify comparison profile 2 | `ComparisonObservation`, `ScalarObservation`, `CloseObservation` |
| `pvs.verification.summary_constraints` | Exact absolute-tolerance floors and failure-count constraints from finite extrema | `close_absolute_floor`, `interval_failure_count` |
| `pvs.verification.norm_bounds` | Separate outward-rounded L1 and scaled L2 envelopes, exact zero and overflow feasibility | `FiniteUpperBound`, `OverflowPossible`, `validate_norm_summary` |
| `pvs.verification.producer` | Declarations, snapshots, execution, statuses, provenance | `_producer_invariants`, `_recomputed_status` |
| `pvs.verification.policies` | Effective policies, embedding, manifest/evidence agreement | Policy and package invariant functions |
| `pvs.verification.results` | Named invariant results and public verification object | `VerificationCheck`, `VerificationResult` |
| `pvs.checks.contracts` | Exact effective criteria | `expected_criterion`, `Criterion` |
| `pvs.checks.diagnostics` | Explicit finite/unavailable diagnostic values | `AvailableDiagnostic`, `UnavailableDiagnostic`, typed serialized records |
| `pvs.numeric` | Lossless numeric ingestion and frozen binary64 norms | `numeric_array`, `stable_norm_v1` |

JSON/YAML dictionaries remain at the untrusted boundary. Schema and semantic
validation precede internal inspection. A literal inspection is either valid,
known-invalid with a reason, or unavailable because values reside in an external
artifact. An unavailable operand may still have a known scalar shape after a
reduction and an element-count domain for `size`. The shared
`require_recorded_value_domains` maps that domain to comparison scalars, summary
extrema, conservation raw totals and normalization. A comparison inspection has the same explicit distinction; known
invalidity cannot fall through the unavailable-data path.

For v2/v3, completed PASS/FAIL/WARN observations must be eligible under the
checked record-visible facts: units, known shapes, scalar-only restrictions,
finite literal subtraction and conservation operand restrictions. ERROR and SKIP make no scientific decision;
their empty observations remain valid. SKIPs also require recorded missing input
for that check. Completed checks that consume inputs require available snapshots.
The `exists` predicate retains its legitimate absent-input outcome, while the
artifact lifecycle contract binds acquisition failures to overall ERROR provenance.

The observation-validation boundary returns a typed completed decision, an
unevaluated observation, or an invalid observation with a reason. It does not
use `None` to mean both invalid and unavailable. The retained v2 arithmetic path
is isolated from the typed v3 comparison parser. No broad approximate equality
is accepted, and no declaration is silently moved from evaluation into case
loading merely to avoid generating an ERROR package.

## Contract generations and trust

Evidence/manifest/report v3 introduces explicit diagnostic availability under
`pvs-comparison/2`. Case v2 and the binary64 norm/1 calculation are unchanged.
Historical evidence v1/v2 uses immutable schemas and retained renderers. V1
receives shared size-domain, evaluation-dtype and complete-source metadata checks
through its own compatibility path, including conservation normalization and
identical-source zero-difference/PASS rules. It does not receive the general
v2/v3 arithmetic, norm-bound, summary-interval or mandatory-unit checks for
distinct sources. V2's finite-diagnostic rules remain distinct from v3. Finding
projection v1 continues to bind the complete structured observations, including profile and diagnostic
tags. See [the numerical contract](numerical-contract.md).

Verification enforces contradictions visible in the record; it does not open
scientific artifacts to rerun the checks. Unknown values remain unknown, not
implicitly valid. Internally consistent unsigned evidence does not authenticate
historical filesystem contents, input parsing or the issuer. Re-evaluate old
CSV-dependent results if their quoted input may have been malformed.

## Adversarial coverage

`test_producer_verifier_consistency.py` crosses all eight check types with valid
producer outcomes, opposite claimed outcomes, declared units, reductions, missing
inputs and malformed optional data. Mutations recompute case, Finding and Evidence
identifiers before verification. `test_comparison_v3_contract.py` adds explicit
availability, invalid tags, selected overflow, bounds, extreme values and one-ULP
mutations. Original review tests are retained unchanged. Frozen v1/v2 package and
overflow fixtures keep compatibility checks independent of the current producer.

The earlier 0.2.8 split of verifier responsibilities is preserved. Release
assembly remains a separate tool with strict source/wheel/sample/rebuild gates.

## 0.3.0 consistency correction

Completed v2/v3 comparison summaries enforce available upper values and distinct
L1/L2 overflow feasibility from the recorded shape and L-infinity error. The
pure bound module is shared by v3 and the historical v2 observation path; legacy
v1 does not execute these general norm-bound checks.
Its exact-rational, outward-rounded derivation is documented in the numerical
contract. Evidence schemas, report profiles, Finding projection and producer
calculation remain unchanged. Frozen v3 output from the 0.2.9 wheel joins the
historical v1/v2 compatibility fixtures. Rehashed artifact cases and independent
scalar-profile boundary cases exercise both rejection and producer acceptance.

## 0.3.2 summary consistency correction

Evidence v2/v3 enforce the absolute-tolerance floor for closeness, including
unknown artifact operands with positive relative tolerance. The summary rules
below apply to those generations; legacy v1 receives the narrower metadata
checks described above, not this general summary-arithmetic contract.
A maximum error at or below the absolute tolerance forces zero failing elements;
the recorded maximum permitted error cannot be below that absolute tolerance.

A single finite element has identical minimum and maximum, even when other
recorded elements are nonfinite. Range and monotonic summaries use a shared
interval constraint: finite extrema are attained witnesses. If the entire
observed interval fails, every corresponding value must fail; if it passes,
none may fail. Distinct failing extrema force at least two failures, and each
passing extremum forces a passing value. Range counts include all nonfinite
values as failures. Monotonic rules apply the four declared directions to the
step interval with their exact inclusive or exclusive tolerance boundaries.
When every element fails, the bounded sample must contain the first ten indices
(or all indices for a shorter array), in the producer's traversal order.

These are necessary record-visible conditions, not reconstruction of unknown
arrays. They preserve exact scientific comparisons and producer arithmetic.
`test_summary_consistency.py` exercises rehashed current and historical v2 records,
required failures and optional warnings, and independently enumerated valid
vectors. The unchanged eight review tests are in `test_review_findings_031.py`.

## Identical conservation source selections

A completed conservation check must give identical unweighted raw totals for
identical source specifications. Source identity uses canonical JSON bytes of
the complete specification, so key order is irrelevant while artifact reference,
selectors, component, reduction and unit remain significant. The artifact
definition and decoding profile are fixed by the referenced artifact in that case.
Coefficients and labels are not part of source identity; negative or zero
coefficients cannot excuse different raw totals. Exact numeric equality is used,
including equality of signed zero. No tolerance is widened. Distinct specifications
are not inferred equivalent, and this rule does not replay artifacts or establish
global cross-check expression equivalence. Unevaluated ERROR/SKIP results make no
raw-total claim. Historical schema/unit/arithmetic policies remain in force.

## Complete-source relationships in 0.3.10

Source equality applies throughout each completed conservation declaration:
term raw totals agree; an expected scalar agrees with an identical term selection;
an explicit normalization agrees with the absolute value of an identical term
or expected selection. Default normalization equals `abs(expected)` and all
normalization values are finite and nonnegative. Successful expected/normalization
evaluation establishes that the shared source contains exactly one element,
even when its array shape
is not rank zero. General vector totals are not equated with different scalar
selections. The full canonical specification remains the identity premise.

Compare and reference checks with identical full source specifications require
equal recorded scalar operands, zero error norms, zero closeness failures and
a completed PASS under their nonnegative tolerance.
Current and historical verification paths receive the same equality premise,
while each retains its own arithmetic and diagnostic-availability contract.
A relative diagnostic may remain unavailable when its reference norm overflows;
identical inputs do not require an overflowing supplementary reference norm to
be finite. ERROR/SKIP remain outside completed-value assertions.

These are relations within one declaration, not global expression equivalence,
artifact replay or relationships inferred between different selections.
