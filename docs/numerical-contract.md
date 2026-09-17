# Numerical ingestion and norm contract

PVS 0.2.6 introduced `pvs-binary64-norm/1` as a shared, small primitive used
by the evaluator and the offline verifier. The producer version is recorded in
`record.pvs.version`; that historical patch introduced no new evidence
field or schema. The profile freezes the evaluator's existing multiplication-based
arithmetic and corrects the verifier's inconsistent exponentiation path.

PVS 0.2.7 through 1.0.0a1 retain this profile and its exact results. The implementation uses
owned binary64 scratch storage and separate elementwise NumPy divide/multiply
operations. It keeps `math.fsum` in C element order, `math.sqrt`, and the final
binary64 scale multiplication. It does not replace accumulation with a NumPy
reduction or a BLAS norm, and it never mutates the caller's input. A scalar
reference implementation in the tests independently checks exact hexadecimal
results for arrays, strided/read-only views, iterators and extreme exponents.
See [local performance measurements](norm-performance.md).

## Source values

From producer version 0.3.11, integer NetCDF `scale_factor` and `add_offset`
attributes pass the same safe-integer check before binary64 conversion. Integer
values outside `[-9007199254740991, 9007199254740991]` produce ERROR rather than
being silently rounded. Existing scalar/type and finite-result rules remain;
floating-point attribute arithmetic is unchanged. Packed-domain fill, missing
and validity sentinels are not blanket-rejected by this arithmetic-operand rule.

Python sequences and object arrays retain their scalar types through structural
component selection. Numeric operands and every numeric reduction validate all
selected source scalars before conversion to binary64. Booleans, strings,
complex numbers and nonnumeric objects are rejected. Integer values must be in
the inclusive interval `[-9007199254740991, 9007199254740991]`, including in
mixed integer/float sequences. Typed integer arrays receive the same range
check. PVS cannot recover types or precision already discarded by a caller
before handing it an array.

Component selection still happens before reduction, takes the final axis, and
preserves scalar types. Unselected components are outside that numeric operand.
Format-specific selectors must exist before any reduction. CSV columns are
checked against the headers cached with the rows, including header-only input.
An existing empty column has size zero; a missing column is an ERROR.
CSV parsing uses UTF-8, comma delimiters, double-quoted fields, doubled quote
escaping, no escape character, no initial-space skipping, `QUOTE_MINIMAL`, and
Python CSV `strict=True`, with `newline=""`. Quoted commas, embedded line breaks
and doubled quotes are supported. Bad quoted-field input is ERROR before any
selector or reduction. This is an explicit Python CSV dialect, not a claim of
complete RFC CSV conformance. Headers must be nonempty and unique; each data
row must have exactly one field per header.
`size` remains a structural count and permits empty and nonnumeric data; it
does not convert source values to numbers. Every other reduction validates the
entire selected operand, including `first` and `last`.

### Recorded value domains and evaluation dtype

From 0.3.8, an artifact operand retains both its known scalar shape and any
operation-derived value domain during verification. `reduce: size` records an
element count after numeric ingestion: finite, nonnegative, integral and at most
`9007199254740991`. `2.0` is admissible; requiring a serialized Python `int` would
incorrectly reject ordinary binary64 observations. Zero is admissible, including
for an empty or nonnumeric selected input. A count above the safe-integer limit
cannot complete current numeric ingestion. Other reductions acquire no count
constraint merely because they return a scalar.

This domain is enforced for actual and expected comparison/reference scalars,
finite/range extrema and finite counts, conservation term `raw_total`, expected
balance and explicit normalization. Normalization takes an absolute value; a size
is already nonnegative. A negative term coefficient may produce a negative
contribution and balance; the raw count remains nonnegative. A scalar size cannot
complete a monotonic check requiring at least two elements.

Finite/range `dtype` describes the **evaluated** array after `numeric_array`, so
it must be exactly `float64`. It is not the JSON/CSV/NetCDF storage dtype. The
permitted label is explicit for each supported evidence generation. V1's
metadata path applies these count/dtype facts while preserving its legacy unit
and arithmetic policy; it does not impose v2's mandatory units on old records.
V2/v3 retain their respective exact arithmetic and diagnostic rules. ERROR/SKIP
make no completed value/dtype claim. Frozen schemas, fixtures and report profiles
are unchanged.

These checks reject contradictions visible in the declaration and observation.
They do not prove that an otherwise possible count matches an inaccessible
artifact and do not rerun its scientific processing. External pins continue to
bind exact identities; rehashing changed evidence cannot preserve a trusted pin.

## Norm profile version 1

Traverse the array in C order. Convert each value to binary64 and take its
absolute value. Inputs must be nonempty and finite.

- L1: `math.fsum` of those absolute values.
- L-infinity: their maximum.
- L2: let `scale` be their maximum. Return positive zero if it is zero.
  Otherwise divide each absolute value by `scale`, square each quotient using
  one binary64 multiplication `item * item`, accumulate with `math.fsum`,
  apply `math.sqrt`, then multiply by `scale`.

The primitive raises `NumericOverflowError` when its result cannot be represented
as finite binary64. Other invalid input raises `ArtifactError`. Comparison
profile 2 below distinguishes a failed gate from an unavailable supplementary
norm; the primitive never returns infinity or a sentinel. Verification requires
exact equality for recomputed observations. No `isclose` allowance or extra
scientific tolerance is introduced.

Fixed conformance vectors in `tests/test_numeric_contract.py` record the exact
binary64 outputs in hexadecimal, including zero, `[3, -4]`, `[286, 337]`,
very small values and very large finite values. The suite also exercises
generated literal comparisons through the public API across all six metrics,
including a one-ULP tolerance boundary. This is same-contract conformance, not
a claim of universal bitwise reproducibility on unqualified runtimes.

## Diagnostic overflow policy

### Floating-point error settings

From 0.3.7, array subtraction, monotonic differences, elementwise closeness
and NetCDF unpacking explicitly allow binary64 underflow to a subnormal or
zero. Each NumPy arithmetic scope restores the caller's settings on exit,
including on evaluation errors. Overflow and invalid operations are checked
through the resulting finite-value predicates; a caller's warning, trap or
callback policy does not determine the check's status. No process-wide NumPy
settings are changed.

Closeness still rounds the relative-tolerance multiplication before adding
the absolute tolerance. NetCDF still rounds scale multiplication before offset
addition. Underflowed products are not replaced by a positive floor and no
extra tolerance is added. The norm primitive already had this underflow rule;
its fixed operation order and hexadecimal conformance vectors are unchanged.

PVS 0.2.9 introduced, and 1.0.0a1 retains, `pvs-evidence/3` using `pvs-comparison/2`.
The comparison profile is required in `record.pvs.comparison_profile` and in every completed
comparison/reference observation's `profile` field. It incorporates norm
profile `pvs-binary64-norm/1` without changing its operation order.

### Selected criterion

Every operand must be admissible, nonempty and finite, have identical shapes,
and declare the check's exact unit. Binary64 subtraction must be finite.
No broadcasting, implicit rescaling or unit conversion occurs.

| Metric | Required finite gating calculation | Permitted error |
| --- | --- | --- |
| `absolute` | Absolute scalar difference; exactly one element required | `tolerance` |
| `l1`, `l2`, `linf` | Selected norm of the difference | `tolerance` |
| `relative` | L2 difference norm divided by `max(L2 expected norm, scale_floor)`; both norms and the ratio must be finite | `tolerance` |
| `close` | Each absolute difference compared to `absolute_tolerance + relative_tolerance * abs(expected)`; every permitted element must be finite | Per-element permitted error |

The first five metrics pass exactly when the gating error is `<=` the permitted
error. `close` passes exactly when its failing count is zero. Its recorded
gating/permitted errors are the largest absolute difference and largest
permitted element; these maxima alone do not determine its outcome for varying
tolerances. Its sample contains the first ten failing indices in C order.
An unavailable selected norm, overflowing relative denominator or ratio,
subtraction overflow, or closeness-tolerance overflow produces ERROR. The
producer records empty observation/criterion objects and issues no Finding ID.

### Supplementary diagnostics

`l1_error`, `l2_error`, and `linf_error` are always present after a completed
gate. `reference_norm` is present for `l1`, `l2`, `linf` and `relative`; for
`relative` it is the required L2 denominator norm before the scale floor.
Each diagnostic has exactly one of these closed representations:

```json
{"status": "AVAILABLE", "value": 442.0011312202718}
```

```json
{"status": "UNAVAILABLE", "reason": "OVERFLOW"}
```

AVAILABLE values are finite and nonnegative. UNAVAILABLE has no numeric value;
null, an infinity token, an unknown reason, or extra fields are invalid.
The evaluator records OVERFLOW only for `NumericOverflowError` from the frozen
norm primitive. It does not swallow input, shape, unit or arithmetic errors.
Finite quantities needed by the gate must be AVAILABLE. In particular,
L-infinity error/reference norms of finite operands cannot be unavailable.

For singleton operands, `actual`, `expected` and `absolute_error` remain finite
numbers. The supplementary `relative_error` uses the same tagged representation,
also permitting `ZERO_REFERENCE` when the expected scalar is zero. Division
overflow uses OVERFLOW. This replaces v2's nullable scalar relative diagnostic.

Examples now accepted by the v3 producer include:

- `[1e308, 1e308]` versus zero, `linf`, tolerance `1e308`: PASS with unavailable
  L1 error, finite L2 error and finite selected L-infinity error.
- Identical `[1e308, 1e308]`, `l1`, zero tolerance: PASS with zero error norms
  and an unavailable supplementary L1 reference norm.
- Identical `[1.7e308, 1.7e308]`, `l2`, zero tolerance: PASS with unavailable
  supplementary L2 reference norm. The same operands with `relative` are ERROR
  because that reference norm is needed by the gate.

The verifier parses diagnostics into explicit AVAILABLE/UNAVAILABLE types,
requires finite gate quantities and exact thresholds, checks scalar identities,
norm ordering and bounds, and exactly recomputes every diagnostic when both
literal operands are known. A known expected literal binds its reference norm
independently of whether the actual values are available. The recorded size and
L-infinity error impose separate, outward-rounded L1 and L2 upper bounds,
described below. A finite bound also requires the corresponding diagnostic
to be AVAILABLE. Unknown artifact values are not
reconstructed: consistent but unprovable claims about those values still require
trust in the evidence source or an external identity pin.

HTML/PDF profile 3 renders unavailable diagnostics with their reason and labels
them as supplementary. Full structured values remain in the evidence record.
`tests/test_comparison_v3_contract.py` covers gates, diagnostics, reports, exact
one-ULP mutations and generated extreme-value comparisons.

### Record-visible norm bounds (0.3.0)

`pvs.verification.norm_bounds` checks the same summary constraints for completed
comparisons and reference comparisons in evidence v2 and v3. Legacy v1 uses the
shared size-domain, evaluation-dtype and source-relation metadata checks; its
identical-source comparisons require zero differences, but general distinct-source
norm bounds are not checked. V1 compatibility does not establish the v2/v3
arithmetic or summary-interval contract.

For v2/v3, let `n` be the exact integer product of the recorded shape and `M` the
finite L-infinity error.
`n` must be positive. If `M == 0`, L1 and L2 must both be AVAILABLE and exactly
zero. No rounding allowance applies to this identity.

For nonzero `M`, define `up(x)` as the **least binary64 value not below the exact
nonnegative value x**. If no such finite value exists, that stage cannot prove
finiteness. Define `sqrt_up(x)` analogously for the exact square root. The bounds
are:

| Quantity | Outward-rounded upper bound | Reason |
| --- | --- | --- |
| L1 | `up(n * M)` | Each absolute difference is at most M; their exact sum is at most n times M. |
| Sum of scaled squares for L2 | `up(n)` | Each binary64 division and multiplication produces a value in [0, 1]. |
| Scaled L2 | `up(M * sqrt_up(up(n)))` | Bound the sum, square root and final multiplication in operation order. |

These bounds enclose the binary64 round-to-nearest operations of norm profile 1,
including its accurate `math.fsum` accumulation. Upward rounding at each stage
prevents a mathematical bound rounded downward by the verifier from rejecting
a legitimate producer result. A bound calculated by rounding only the final
real expression `M * sqrt(n)` would omit the intermediate rounding points.

The implementation uses exact `Fraction` products, exact integer shape products,
and comparisons of rational squares to bracket the square root. The host square
root provides only an initial candidate for that bracket. There is no arbitrary
epsilon, `isclose`, decimal precision setting, array allocation, or float
conversion of the element count before it has been bounded. Intermediate
accumulation overflow is distinguished from overflow of the scaled result.

A finite bound has two implications: an AVAILABLE value must not exceed it,
and UNAVAILABLE/OVERFLOW is impossible. An unbounded envelope means only that
this test cannot prove finiteness; it neither requires nor proves overflow.
The existing lower ordering, L1 >= L2 >= M, remains enforced. Consequently finite
L1 requires finite L2, but unavailable L1 does not imply unavailable L2.
For `n = 2, M = 1e308`, L1 can overflow while the L2 bound is finite. For two
`1.7e308` differences both auxiliary norms can legitimately overflow.

Bounds are necessary summary constraints, not a complete inverse solution for
all vectors with a given triple of norms. Outward rounding can leave uncertainty
near a representability boundary. When operands are literals, exact profile
recomputation still applies in addition to these constraints. Selected gating
values, reference denominators and tolerances still use exact equality; the
summary envelopes never add scientific tolerance or alter producer arithmetic.

The runtime assumptions remain those of norm profile 1 and its supported-host
conformance checks. Python documents the IEEE-754/rounding assumptions and an
extended-precision caveat for [math.fsum](https://docs.python.org/3/library/math.html#math.fsum).
This release does not claim arbitrary-platform bitwise reproducibility.
`tests/test_norm_summary_bounds.py` checks fixed outward-bound vectors, exact
zero, one representable step above a bound, nonrepresentable large element
counts, both sides of actual overflow boundaries, independent scalar-profile
outputs, and public-API artifact comparisons across all six metrics.

### Historical evidence

Evidence v2 retains its original closed schema and observation policy: overflow
of any required auxiliary norm caused ERROR, even with a finite selected gate.
`tests/test_diagnostic_overflow_policy.py` verifies frozen ERROR records actually
emitted by the 0.2.8 wheel. V1/v2 packages replay their original renderers and
are never rewritten or reinterpreted as v3. New evaluations can therefore have
new outcomes and Finding IDs; old identities remain identities of old records.
The Finding projection stays at version 1 because its field selection is
unchanged and includes the entire observed object, including the new profile
and diagnostic tags. No value substitution or profile stripping occurs.

## Optional missing inputs

Only `MissingArtifactError`, which carries the artifact ID and path, can turn
an optional input-dependent check into `SKIP`. Malformed input, invalid selectors,
unreadable files, non-regular files and snapshot acquisition errors produce
`ERROR`, regardless of their message text. JSON, CSV and NetCDF use the same
missing-file distinction. Optional `exists` checks retain their PASS/WARN rule.

For v2 and v3 evidence, the verifier requires a SKIP to reference at least one declared
check input whose validation snapshot records absence without a resolution
error. An unrelated missing artifact cannot justify the SKIP. This verifies
the recorded absence contract; it does not authenticate historical claims
about the producer's filesystem.

Completed input-dependent checks require recorded available validation inputs.
The `exists` predicate can legitimately observe absence; lifecycle failures
still require incomplete provenance and an overall ERROR.

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
