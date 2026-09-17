# Benchmark packs

The shipped library contains four versioned packs, **103 PVS checks and 178 scalar
output targets**. Counts exclude input coordinates, metadata and additional source
table anchors. Several targets are unit conversions or calculations from the same
published input: 178 does not mean 178 independent measurements or experiments.
There are currently no measured experimental validation datasets in this library.

| Pack | Content | Data origin | Targets | Checks |
|---|---|---|---:|---:|
| [Plasma reference audit](plasma-physics-reference-v1/README.md) | Collisions, equilibration, bremsstrahlung, DT reactivity, transport-table and alpha-stopping anchors, constants | Public equations, published tables and constants | 52 | 31 |
| [Fusion reactivity](fusion-reactivity-v1/README.md) | DT, DD-total and D-He3 at ten published temperatures; SI conversion and reaction-event rate density | 30 published tabulated reactivities, converted and normalized | 60 | 21 |
| [Plasma scales](plasma-scales-v1/README.md) | Debye length, plasma/cyclotron frequencies, thermal speed, gyroradius, Alfvén speed, beta, inertial length and E-cross-B drift | Independently evaluated analytical equations at chosen inputs | 30 | 33 |
| [Classical physics](classical-physics-v1/README.md) | Harmonic oscillator, heat diffusion and circular Kepler orbit | Independently evaluated analytical equations at chosen inputs | 36 | 18 |

The machine-readable [catalog](catalog.json) records each pack's case version,
data kinds, counts, producer, primary sources and limitations. Each pack documents
equations, units and applicability in its guide, case and reference artifacts. Its
`source_manifest.json` records source locations, source-byte identities and
admission or inspection limits. The existing plasma reference audit retains its
explicit unreviewed Bosch-Hale erratum limitation.

Benchmark packs are scientific content separate from PVS core. Their supplied
subjects are reference calculations or data adapters, not production solvers.
The fusion table adapter reads the published table as an input: its PASS checks
unit conversion and identical-particle pair counting, not an independent
prediction of fusion reactivity. Analytical values are truthfully labelled as
calculations, with chosen inputs and ideal assumptions visible.

With PVS installed, run every pack from a fresh source directory on Windows or POSIX:

```powershell
python tools/run-benchmark-packs.py --output benchmark-results
```

The output directory must be new. Select one pack with, for example,
`--pack plasma-scales-v1`. The helper checks SHA-256 pins, copies each pack to a
temporary working directory, executes its reference producer with the current
Python interpreter, validates the resulting data, and independently verifies
each evidence package. It preserves the frozen source packs and case bytes.

Each pack retains machine-readable evidence, embedded declared artifacts, HTML
and PDF reports, plus external producer command records, stdout/stderr and hashes.
The top-level `benchmark-summary.json` names every selected pack. The helper exits
with failure if a pack is missing, a producer fails, a check is not PASS or package
verification fails. PVS records execution mode `validate`; producer execution is
documented separately and does not claim PVS-supervised subject execution.

See [the benchmark library guide](../docs/benchmark-library.md) for interpretation,
adapting an external solver, and admitting future datasets. Never regenerate a
frozen reference from the production model it is supposed to test.
