# Using and extending the benchmark library

PVS packages a portable evidence framework and a small, separately versioned
library of scientific reference cases. The [catalog](../benchmark-packs/catalog.json)
and [pack index](../benchmark-packs/README.md) enumerate all four shipped packs.
They contain 103 declarative checks and 178 scalar output targets. The published
fusion table contributes 30 source reactivities; converting those values and
calculating event rates gives 60 targets, not 60 independent physical observations.

## What the data mean

| Data kind | Origin and use | What PASS can establish |
|---|---|---|
| Published tabulation | Values transcribed from identified publications, with source location and printed precision retained | Correct handling or agreement within a justified source-based tolerance; independence depends on how the tested subject uses the table |
| Analytical derivation | Values separately calculated from stated equations at deliberately chosen inputs | Verification against that ideal mathematical problem and sampling scheme |
| Physical constants | Identified published central values and uncertainties | Correct use of the declared values and unit conventions; numerical tolerance does not reduce their physical uncertainty |
| Measured experimental data | Observations with instrument, processing, calibration and uncertainty provenance | Agreement within a defined validation domain and error budget; none are currently bundled |

The fusion-reactivity pack is deliberately a table adapter example. It reads the
published reactivities as inputs and checks SI conversion and event-rate pair
counting. It is not an independent fusion reaction model. The DD column is total
DD reactivity, not two independently qualified branches. The existing DT
Bosch-Hale calculation belongs to the separate plasma reference audit and retains
that pack's unreviewed-erratum limitation.

The plasma-scales and classical-physics packs use independent read-only reference
checkers beside the demonstration producers. Their chosen states, oscillator
parameters, diffusivity and orbital parameter are benchmark inputs, not device
measurements or measured material/astronomical properties. Tight arithmetic
tolerances qualify reproduction of the equations. They are not experimental
uncertainties or a claim of corresponding physical accuracy.

## Run the shipped examples

Install the released PVS wheel, or install PVS from the source directory. With that
Python environment active, run from a fresh extracted source directory:

```powershell
python tools/run-benchmark-packs.py --output benchmark-results
```

Run one catalog ID with:

```powershell
python tools/run-benchmark-packs.py --pack classical-physics-v1 --output classical-results
```

Choose a fresh output directory for every run. The helper runs on native Windows
and POSIX without rewriting case definitions or relying on a `python` command
elsewhere on PATH. It uses the interpreter that launched the helper, records its
path and SHA-256, and applies the declared producer environment and timeout.
Previously generated outputs inside a source pack are rejected: use a clean pack
copy rather than treating stale data as a new calculation.

Before execution, the helper verifies every immutable artifact's declared SHA-256
pin. It works in a temporary copy, checks that source files did not change, then
calls PVS `validate` followed by package verification. Missing catalog entries,
missing packs, changed pins, producer errors, missing outputs, non-PASS checks and
integrity failures produce a nonzero exit status. An unknown `--pack` cannot turn
into an empty passing run.

Each result directory contains:

- `benchmark-summary.json`: software/platform identity, selected packs, counts,
  limitations, producer-record hashes and evidence identities.
- One directory per pack with `producer.json`, `producer.stdout.txt` and
  `producer.stderr.txt`: actual external command, timing, interpreter identity,
  explicit environment overrides, source/output hashes and any failure.
- An `evidence` directory per successful validation transaction: canonical JSON,
  manifest, embedded declared inputs and outputs, and HTML/PDF reports. A failed
  numerical comparison can also retain a valid evidence package reporting FAIL.

PVS's own execution mode is **validate**. The external producer record describes
the helper's separate subprocess; it does not acquire PVS-supervised execution
semantics by being next to the evidence. Its hash is recorded in the runner
summary, while the PVS manifest binds the evidence package itself. These hashes
identify bytes and detect changes against a retained identity; they do not
authenticate a publisher without a separate trust mechanism.

## Compare an external implementation

1. Choose a pack whose model, species, units, initial/boundary conditions and
   validity range match the implementation being checked.
2. Create your own case with a new identity. Keep the admitted reference artifacts
   and SHA-256 pins, and declare your actual subject, input files and output
   artifacts. Map the output quantities and sampling order to the reference case.
3. Review the numerical error budget. A discretized solver needs justified spatial
   and temporal convergence criteria; the demonstration's roundoff tolerance is
   not automatically appropriate. Document changes to thresholds explicitly.
4. Produce the output and validate it with PVS. Inspect the evidence, provenance,
   per-check result and limitations as well as the overall status.

Do not edit frozen answers to match the solver. Do not call a table lookup an
independent prediction when that same table is the expected result. Use the
external implementation's actual provenance, not the demonstration producer's
source artifact or identity. The library helper accepts only its shipped catalog;
user-authored cases use the ordinary PVS run/validate interfaces.

## Admit new reference content

Every new pack needs a reviewable source record before it becomes a passing
release example. Record the primary source and exact edition, source location,
retrieved-byte identity, data origin, units, assumptions, validity range,
uncertainties, errata and unresolved limitations. Preserve printed values and
their resolution separately from conversions or derived results. Record why the
specific included content may be redistributed; a public download link alone is
not permission to redistribute a database or publication.

For derived references, retain an independently implemented calculation and
source review that does not import the tested producer. For measurements, retain
the experiment, processing chain, uncertainties and calibration context. Never
silently present fabricated fixtures, solver output or synthetic examples as
measurements. Test meaningful failure modes such as unit conversions, signs,
species factors, boundaries and altered answers.

Freeze admitted references and their source/derivation records by SHA-256, version
the case when scientific content changes, update the catalog, run all packs and
verify retained evidence/report packages. Keep original third-party publications
out of an export unless their inclusion has specifically been cleared.

Useful later additions include independently qualified DD branches and D-He3
reaction models, collision/transport cases with validity-domain checks, explicit
mesh/time-step convergence studies, and one rights-cleared experimental fusion
dataset with measured uncertainties. Those are future additions, not claims made
by the current library.
