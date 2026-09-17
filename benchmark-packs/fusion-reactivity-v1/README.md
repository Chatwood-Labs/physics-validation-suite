# Published fusion reactivity data and normalisation

Case version **1.0.0** provides **30 published reference values**: D-T,
D-D total (both branches together), and D-He3 at 1, 2, 5, 10, 20, 50, 100,
200, 500 and 1000 keV. They are manually transcribed from the Maxwellian
reaction-rate table on printed page 45 of the **2023 NRL Plasma Formulary**.

The included subject is a **table adapter**, not a fusion reactivity solver.
It reads the pinned published table, converts its cm^3/s values to m^3/s,
and calculates reaction-event densities. Its PASS establishes conversion
and normalisation against 60 separately frozen numeric targets. It does
not independently predict the 30 published reactivities or validate a
simulation against experiment. This distinction is also in the case and
the generated evidence/report.

## Run

From the source root, with PVS installed:

```powershell
python benchmark-packs/fusion-reactivity-v1/convert_reference_table.py --output benchmark-packs/fusion-reactivity-v1/derived_values.json
python -m pvs validate benchmark-packs/fusion-reactivity-v1/pvs.yaml --output fusion-evidence
python -m pvs verify fusion-evidence
```

These commands work with the external subject process on Windows as well
as POSIX systems. On supported POSIX platforms, PVS can execute the
declared subject itself with `python -m pvs run
benchmark-packs/fusion-reactivity-v1/pvs.yaml --output fusion-evidence`.

## What the numbers mean

`published_table.json` preserves all 30 printed decimal forms and their
display resolution. D-D is the **sum** of reactions (1a) and (1b) in the
NRL notation. It must not be treated as one branch or as a neutron rate.
The source table gives Maxwellian-averaged reactivities. The ten table
temperatures are the only admitted coordinates; the pack supplies no
interpolation, extrapolation, beam-target or non-Maxwellian model.

`inputs.json` contains arbitrary, clearly labelled demonstration densities:

| Channel | First ion | Second ion | Densities (m^-3) |
|---|---|---|---|
| DT | D | T | 5e19, 5e19 |
| DD total | D | D | 1e20, 1e20 |
| D-He3 | D | He3 | 5e19, 5e19 |

These are three separate idealised mixtures, not measurements from a device.
No electron density or fusion power balance is assumed.

The event density is `R = n1 * n2 * <sigma v> / (1 + delta12)` in m^-3 s^-1.
Here `delta12` is one for identical reactants and zero otherwise, as stated
in Xie et al., arXiv:2212.01840v2, p. 2, Eq. (1). The DD factor of one-half
counts each ion pair once. DD deuteron consumption would be twice this
event rate; neutron production requires separate branch information.

## Independence and tolerances

The adapter reads `published_table.json` and `inputs.json`. It never reads
`reference_values.json` and cannot update a frozen reference through its
output option. The expected SI values were separately transcribed, and
the event densities calculated using 60-digit Decimal arithmetic and
simplified pair-density factors. Independent review checks the source
table and numeric results without importing the adapter.

The 21 PVS checks cover output existence and schema, source-manifest
presence, and finite, positive and reference comparisons for six arrays.
The schema fixes the temperatures, array lengths, channels and density
inputs. SHA-256 pins bind six inputs/reference/subject artifacts.

The **2e-14 relative numerical tolerance** covers floating-point conversion
and multiplication. It is not a physical uncertainty. The NRL table gives
no per-point uncertainty estimates; printed precision is not an uncertainty
interval or a guarantee of correct rounding. No approximate analytic fit
is forced to pass these rounded table targets.

## Use with your solver

Make a new case with your own subject name/version, command, input and output
artifacts. Export your Maxwellian reactivity values at these temperatures
with the supplied units and channel definitions. The strict schema documents
the sample output contract. Keep the published references fixed and record
a scientifically justified comparison tolerance for your model; the sample's
conversion tolerance is not suitable for comparing different physical fits.
If your solver only returns reactivity, retain the three reactivity comparisons
and define a matching output schema; event-density checks are optional in
that new case. Do not label a replay of this table as a physical prediction.

## Sources and distribution

- [2023 NRL Plasma Formulary](https://www.nrl.navy.mil/Portals/38/PDF%20Files/NRL_Plasma_Formulary_2023.pdf),
  A. Beresnyak, pp. 44-45: reaction labels and the three selected table columns.
- [Xie et al., arXiv:2212.01840v2](https://arxiv.org/abs/2212.01840v2),
  *Fusion Reactivities with Drift bi-Maxwellian Ion Velocity Distributions*,
  p. 2, Eq. (1): elementary event-counting relation only.

`source_manifest.json` records the exact inspected document hashes, sizes,
locations, scope and limitations. The pack distributes cited numerical facts,
scientific formulas and original adapter code. Full publications, copied
prose, figures and external code are not included. It makes no blanket
licence claim about the publications. No Bosch-Hale fit or erratum-dependent
coefficient is added by this pack.
