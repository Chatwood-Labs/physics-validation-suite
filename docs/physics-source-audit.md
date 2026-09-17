# Physics sources and claim scope

The [benchmark library guide](benchmark-library.md) identifies the complete
four-pack catalogue. Three new version 1.0.0 cases add the following content:

| Pack | Primary scientific basis | Scope |
| --- | --- | --- |
| Fusion reactivity | NRL Plasma Formulary 2023, printed p.45 | 30 published reactivity values; SI conversion and binary-reaction event-rate checks. The supplied adapter does not predict reactivity. |
| Plasma scales | NRL 2023, printed pp.18-19 and 28-29; CODATA 2022 | 30 independently calculated SI characteristic values with explicit species and thermal conventions. |
| Classical physics | OpenStax harmonic-motion equations; MIT heat-equation notes; NASA orbital-mechanics equations | 36 independently calculated analytical targets at chosen inputs. These are not measurements or a solver-convergence study. |

Each new pack retains a source manifest, frozen values, a closed values schema,
its standalone example and independent reference checks. Model uncertainty,
printed precision and numerical reproduction tolerance are separate concepts.
No source PDF, source image, experimental database or private simulation is
redistributed. The existing plasma case and its limitations are described below.

The case in `benchmark-packs/plasma-physics-reference-v1` contains reference
work derived from identified public scientific sources. Its case version is
3.0.0; the directory name retains the pack-family identity. Published table
values, equation-derived values and project choices are explicitly distinguished.
Agreement with a frozen number alone does not establish that number's accuracy.

The runnable example checks the reference derivation. It does not execute or
certify a production solver. No private source code, production-data snapshot,
application configuration, implementation fingerprint or implementation audit
is part of the public reference pack.

| Source | Included use | Source review and limits |
| --- | --- | --- |
| A. Beresnyak, NRL Plasma Formulary 2023 | Collision, equilibration and bremsstrahlung equations | Official PDF identity and exact locators are recorded. Equal-number D-T equilibration sums D and T contributions separately; the harmonic effective mass ratio is 2.3972733786507514. The electron equation time and coupled temperature-difference time differ by two. |
| Bosch & Hale 1992 | D-T Eq. (12), Table VII coefficients and Table VIII checks | The inspected journal reproduction is byte-identified. The 1993 erratum remains uninspected. Equation reproduction and regression tolerances do not certify model accuracy; this case evaluates the declared D-T temperature grid only. |
| Spitzer & Harm 1953 | Table III transcription | The reviewed reproduction is byte-identified and is not APS-hosted. Retaining and checking the transcription is not an end-to-end transport calculation. |
| F. Evans, LA-5448-MS (1973) | Instantaneous alpha stopping partition and Table I | The official PDF is byte-identified. Visual review of printed page 5 (PDF page 8) confirms 0.45 and 0.83. Integrated slowing-down fractions are outside this case. |
| Peter J. Mohr et al., CODATA 2022 / NIST Version 9.0 | Constants and isotope mass ratios | The official NIST PDF is byte-identified. The JPCRD citation is volume 54, article 033105 (2025), DOI 10.1063/5.0279860. |

The [source manifest](../benchmark-packs/plasma-physics-reference-v1/source_manifest.json)
records per-source locators, byte sizes, reviewed-source hashes and inspection
limits. These hashes identify public source copies. They do not establish a
redistribution licence or prove the scientific interpretation. Source PDFs are
not bundled. The [pack guide](../benchmark-packs/plasma-physics-reference-v1/README.md)
explains the numerical checks and their scope.

The insufficiently source-qualified ionisation fixture has been removed from
this version. A future ionisation case requires direct public-source inspection,
explicit units and applicability, justified tolerances and independent numerical
verification before admission.

## Evans printed-table correction

| Electron temperature | Alpha energy | Printed ion stopping fraction |
| --- | --- | --- |
| 50 keV | 2.5 MeV | 0.45 |
| 100 keV | 1.5 MeV | 0.83 |

These values agree with the stopping-partition equation to the printed precision.
Earlier 0.65/0.63 readings were project OCR/transcription errors, not errors in
the publication. The original source is
[Evans, DOI 10.2172/4398792](https://doi.org/10.2172/4398792).

## Independence and public content

A case author must establish that its reference is independent of the production
implementation under examination. The reference-derivation example and its
frozen values share a declared derivation, so their agreement is a regression
check. Direct printed-table comparisons and separately implemented source
reproductions provide additional, scoped checks; they do not turn the example
into experimental validation.

Scientific equations, assumptions and tolerances belong to the pack, not PVS
core. Additions must meet the [public-content policy](public-content-policy.md).
