# Plasma reference audit pack, case 3.0.0

This case checks public plasma equations, published table transcriptions and
physical constants. It is scientific example content, separate from PVS core
and solver adapters.

The directory suffix `-v1` identifies the original pack family. The case and
subject versions are `3.0.0`, and the generated values use
`pvs-plasma-reference-values/3`. The versioned schema contains only the
source-reviewed reference groups listed below.

The case runs a standard-library Python derivation, compares its results with
separately frozen values and selected printed table entries, and binds its
source manifest and reviewed artifacts to hashes. The subject uses only the
Python standard library and does not generate or update the expected data.
PVS checks the declared reference hashes. This case does not run or validate a
production plasma solver.

There are 31 declared checks and 52 equation-derived reference values. The
five pinned artifacts bind the derivation source, frozen values, values schema,
published tables and source manifest to the case. Table entries and physical
constants are additional reference content and use separate counts.

## Included content and qualification

| Content | What was checked | Qualification boundary |
| --- | --- | --- |
| NRL 2023 electron collisions, D-T equilibration and bremsstrahlung | Official equations, locations and unit conversions; independently recomputed values | Equation reproduction at declared inputs, not universal model validity |
| Bosch-Hale 1992 D-T reactivity | Visually reviewed Eqs. (12)-(14), Table VII coefficients and selected Table VIII values | Reviewed 1992 edition; 1993 erratum remains unreviewed |
| Spitzer-Harm 1953 Table III | All 20 coefficients in the four finite-Z rows visually checked | Frozen transcription with finite/range checks; no production transport comparison |
| Evans 1973 Table I | All 19 printed fractions visually checked; two selected values compared with the stopping equation | Instantaneous differential fraction; no integrated birth-to-thermal claim |
| CODATA 2022 constants | All eight retained central values checked against the official NIST publication | Exact decimal central-value transcription, not zero physical uncertainty |

## Corrected Evans transcription

The actual Table I prints **0.45 at 50 keV / 2.5 MeV** and **0.83 at
100 keV / 1.5 MeV**. Earlier pack revisions mistook OCR output, 0.65 and 0.63,
for printed values and incorrectly described these as published anomalies.
Those were errors in the pack's transcription and interpretation. They were
not errors in Evans's printed table.

The corrected values are in the table rows. A separately labelled historical
OCR correction record preserves what changed. The source locator is printed
page 5, PDF physical page 8. Eq. (27a) on printed page 4 gives the ratio from
which the instantaneous ion fraction is derived. The table's displayed
energies and fractions have limited precision; exact pointwise reproduction
of every printed fraction from rounded equal-stopping energies is not claimed.

Each Evans row stores `[energy_MeV, fraction_1]`. The case selects the energy
and fraction separately, with `MeV` and dimensionless `"1"` units.

## PASS and source limitations

A PASS means the declared equation and transcription checks succeeded
for the exact identified artifacts. It does not certify the original model,
production implementation, independence of authorship or source qualification.
A matching SHA-256 proves byte identity, not scientific correctness.

The Bosch-Hale 1992 scan was reviewed and identified by hash; its separate 1993
erratum was not retrieved. No claim of qualification against all published
corrections is made. These limitations remain in the generated evidence.

Scientific/model uncertainty is separate from regression tolerance. Bosch-Hale
reports a maximum D-T fit deviation of 0.25% and approximately 3% absolute
reactivity uncertainty. Tighter PVS tolerances test reproduction of the same
equation. NRL equilibration uses its near-common-temperature approximation and
additive D/T species coefficients. The two temperature-difference time scales
assume a common ion temperature and equal total electron/ion number densities.

NRL, CODATA and Evans have exact official source-byte hashes. Spitzer-Harm and
Bosch-Hale identify reviewed reproductions, not publisher-hosted source bytes.
The Bosch-Hale erratum is not pinned. The pack therefore does not claim complete
source qualification. Publications are identified, not redistributed.

## Run

From this directory on a POSIX system:

```bash
pvs inspect pvs.yaml
pvs run pvs.yaml --output plasma-reference-evidence
pvs verify plasma-reference-evidence
```

Remove any previous `derived_values.json` before running; PVS refuses to
overwrite stale declared outputs. See `source_manifest.json` and
`../../docs/physics-source-audit.md`.
