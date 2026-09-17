# Evidence format compatibility fixtures

These packages test software compatibility. They are not scientific reference
datasets, physical measurements, or evidence of a production simulation.

## Newly generated synthetic packages

`synthetic-json-evidence-v3.zip` contains ordinary PVS 0.3.12 output for the
deliberately synthetic, dimensionless values `[0.0, 0.5, 1.0]`. It exercises
finite, range and reference checks, embedded artifacts, and exact HTML/PDF replay.

`synthetic-json-evidence-v1.zip` is a **newly assembled v1-format fixture**.
The retained generator projects the same new synthetic validation into the v1
case/evidence/manifest representation and uses the frozen v1 report renderers.
Its producer version `0.3.12+synthetic-v1` and case metadata identify the assembly.
It is not output from an old PVS release. Its execution times describe the
underlying current validation; environment metadata describes the actual runtime.
No historical producer identity or execution record was copied into it.

`synthetic-compatibility-identities.json` records the exact generator, archive,
Evidence ID and Package ID hashes. The generator is embedded in both packages.
The input case and two synthetic JSON files are retained in
`synthetic-compatibility/`. Regenerate new fixtures from the source root with the
project's development dependencies installed:

```console
python tests/fixtures/generate_synthetic_compatibility.py --output /path/to/new-fixtures
```

Generation performs a fresh validation and creates new timestamps and run IDs;
it does not promise byte equality between separate runs. Each generated package
must pass offline verification, including exact deterministic report replay.
If intentionally replacing the frozen ZIPs, update the identity inventory and
the explicit v1 package/evidence pins in the package policy/verification tests.

## Preserved generic historical v2 records

`evidence-v2-0.2.8.zip` is unchanged output of the unmodified 0.2.8 wheel.
It contains a synthetic JSON accounting/temperature/fraction example and six
synthetic comparisons covering every comparison metric. All embedded data,
case declarations, evidence provenance, and report content were reviewed before
retention. The example is generic toy arithmetic, not private solver output.

`evidence-v2-overflow-0.json` through `-3.json` are unchanged 0.2.8 ERROR records
for auxiliary L1/L2 error/reference overflow using synthetic `1e308`-scale
operands. They remain valid historical evidence even though a newer evaluation
can complete its selected criterion.

The v2 package SHA-256 is
`c3a83925127eed21a4f4cffc2b09a93012bd4d10b4fdc256e90f95f6294d9474`.
The historical v2 records retain their real producer/runtime facts; they are not
current release qualification or issuer authentication.

`frozen-schema-sha256.json` pins the immutable v1/v2 schemas to the reviewed
0.2.8 source archive. Both packaged and public schema bytes are checked.
`review_034.py` retains generic software regression cases from the 0.3.4 review.
