# Classical analytical physics benchmarks

This pack provides **36 frozen scalar targets across three ideal problems**, with
18 PVS checks. These are independently calculated analytical verification values,
not measured data, simulations presented as experiments, or published table
transcriptions. The inputs below are deliberate benchmark choices.

| Problem | Inputs and assumptions | Frozen outputs |
|---|---|---|
| Harmonic oscillator | Frictionless horizontal mass-spring system; mass 2 kg, stiffness 8 N/m, initial displacement +0.25 m, initial velocity 0 m/s; times 0, 0.5, 1, 2, 3 s | Five positions, five velocities, five total energies |
| Heat diffusion | Uniform 1 m rod, constant diffusivity 0.1 m²/s, insulated sides, no internal source, ends at fixed common temperature; initial temperature excess 10 sin(πx/L) K | Temperature excess at x = 0, 0.25, 0.5, 0.75, 1 m and t = 0, 0.5, 2 s, stored as rows of time and columns of position |
| Circular Kepler orbit | Newtonian point source, negligible test-particle mass, prescribed μ = 1 m³/s²; three circular radii 1, 4, 9 m; no drag, perturbations or relativity | Three circular speeds and three orbital periods |

Temperature excess is relative to the fixed boundary temperature, not an absolute
temperature of zero kelvin. The chosen diffusivity is not a measured material
property. The orbital parameters describe ideal problems, not measured celestial
bodies. For each circular orbit, initial position is `(r, 0, 0)` m and initial
velocity is `(0, sqrt(μ/r), 0)` m/s in an inertial Cartesian frame.

## Equations and provenance

The oscillator uses `ω = sqrt(k/m)`, `x(t) = A cos(ωt)`,
`v(t) = -Aω sin(ωt)`, and `E = kx²/2 + mv²/2 = kA²/2`.
The sources are OpenStax University Physics Volume 1,
[Section 15.1, Equations 15.3, 15.4 and 15.9](https://openstax.org/books/university-physics-volume-1/pages/15-1-simple-harmonic-motion)
and [Section 15.2, Equation 15.12](https://openstax.org/books/university-physics-volume-1/pages/15-2-energy-in-simple-harmonic-motion).

The diffusion problem solves `∂u/∂t = κ ∂²u/∂x²` with zero excess at both
ends. Its solution is `u(x,t) = A sin(πx/L) exp[-κ(π/L)²t]`.
This follows from the dimensional scaling and separated sine mode in
[M. J. Hancock, MIT 18.303, The 1-D Heat Equation](https://ocw.mit.edu/courses/18-303-linear-partial-differential-equations-fall-2006/d11b374a85c3fde55ec971fe587f8a50_heateqni.pdf),
Equations (2), (4)-(7) and (18)-(20), printed pages 2-7.

Circular orbital speed is `v = sqrt(μ/r)` and period is
`T = 2π sqrt(r³/μ)`. The gravitational parameter replaces `g R_E²` in
[NASA Goddard, Kepler's Third Law, section on Earth satellites](https://pwg.gsfc.nasa.gov/stargaze/Skepl3rd.htm).
No terrestrial measured constants are imported.

`source_manifest.json` records exact source locations, retrieved-byte hashes and
admission limits. Source documents are not redistributed. This pack supplies its
own code, chosen input cases, calculations and explanatory wording under the
repository licence; it does not grant rights to the cited publications.

## Reproduce and verify

From this directory, with PVS installed, these commands work on Windows and POSIX:

```text
python derive_reference_values.py --output derived_values.json
python -m pvs validate pvs.yaml --output evidence
python -m pvs verify evidence
python verify_frozen_values.py
```

The first script uses binary64 arithmetic and standard-library trigonometric
functions. It does not read the frozen reference file. The separate read-only
verifier uses 80-digit Decimal arithmetic, an oscillator ODE series, exact spatial
radicals, Decimal exponential and Machin's identity for π. The frozen JSON was
created from that independent calculation, not the demonstration producer.
PVS pins the reference file, schema, source manifest and both calculation scripts
with SHA-256. Changes require an explicit reviewed case revision.

## Compare your own solver

Make a working copy of this pack and implement the stated initial/boundary
conditions in your solver. Export its results with the exact sampling order,
SI units and JSON structure in `reference_values.schema.json`. Keep the frozen
reference, schema and their pins intact. Point the output artifact to your
exported file, update the case/subject identity, and replace the demonstration
source artifact and execution command with your own provenance. Run PVS
`validate` on that adapted case to compare the existing solver output.

The supplied tolerance is **2e-14 relative**, plus absolute tolerances of 1e-14
for oscillator quantities and orbital speed, and 1e-12 for temperature excess
and orbital period, all in their declared SI units. This admits floating-point
roundoff near exact zeros. It is deliberately a tight analytic reproduction
test. A discretized solver must document and justify its own error budget and
mesh/time-step convergence; do not loosen tolerances merely to obtain PASS.

The demonstration's PASS establishes analytical reproduction and evidence
handling. It does not validate a production solver, experimental agreement,
long-time conservation, stability, or convergence. Those require separate cases
and measurements or resolution studies.
