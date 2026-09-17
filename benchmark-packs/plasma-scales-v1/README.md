# Plasma characteristic scales, version 1.0.0

Thirty frozen scalar values cover ten common plasma quantities at three explicitly
chosen, homogeneous deuterium states. These are independently evaluated public
equations, **not experimental data**. The runnable subject demonstrates an adapter
that can be replaced by a user's implementation. PASS qualifies these reference
calculations; it does not qualify a fusion simulation or prove confinement.

| State | n_e = n_D (m^-3) | T_e (eV) | T_D (eV) | B_z (T) | E_x (V/m) |
|---|---:|---:|---:|---:|---:|
| low_density | 1e18 | 10 | 5 | 0.1 | 100 |
| intermediate | 1e19 | 100 | 100 | 1 | 2000 |
| high_density | 1e20 | 1000 | 500 | 5 | 15000 |

These are pack-designed inputs, not measurements, published device operating
points or claims about a particular machine. The species are electrons and bare
deuterons with Z=1. All other field components vanish.

## Quantities and conventions

The source record gives the exact SI equation, unit and source location for every
quantity. The temperature energy conversion is k_B T = e T_eV joules.

| Quantity | Convention |
|---|---|
| Electron Debye length | Electron contribution only, not combined species screening |
| Electron and deuteron plasma frequencies | Angular frequency in rad/s |
| Deuteron cyclotron frequency | Positive angular-frequency magnitude in rad/s |
| Deuteron thermal speed | sqrt(k_B T_D / m_D), the one-dimensional Maxwellian standard deviation |
| Deuteron thermal gyroradius | That thermal speed divided by the cyclotron angular frequency |
| Alfvén speed | Ideal-MHD value with ion mass density n_D m_D |
| Total plasma beta | Electron plus ion scalar thermal pressure divided by magnetic pressure |
| Electron inertial length | c / omega_pe, also called the collisionless skin-depth scale |
| E-cross-B drift | Signed y component; positive E_x and B_z give negative y drift |

Classical, nonrelativistic scales are assumed. Wave interpretation of Alfvén speed
requires the MHD wavelength/frequency ordering; collisionless guiding-center drift
requires magnetized particles and steady uniform fields. Kinetic, relativistic,
anisotropic, collisional-drift and electron-inertia corrections are not calculated.
The gyroradius convention is not the perpendicular-RMS convention, which differs
by sqrt(2). Read the full per-quantity limits in `source_manifest.json` before
comparing an external model.

## Sources and independence

- [NRL Plasma Formulary, 2023](https://www.nrl.navy.mil/Portals/38/PDF%20Files/NRL_Plasma_Formulary_2023.pdf):
  SI conversion and Lorentz force on printed pp. 18-19, characteristic parameters
  on pp. 28-29. Equations are converted analytically to SI; rounded practical-unit
  coefficients are not treated as high-precision constants.
- [CODATA 2022, official NIST-hosted publication](https://physics.nist.gov/cuu/pdf/JPCRD2022CODATA.pdf):
  Table XXXII, p. 033105-44, and Table XXXIII, p. 033105-48. Selected central values,
  standard uncertainties and exact source-byte identities are retained in the
  manifest. Published epsilon_0 and mu_0 are separately rounded, so their product
  with c squared is unity only to the accuracy of those rounded numbers.

`derive_values.py` uses ordinary Python floating-point arithmetic and neither reads
nor overwrites the bundled frozen answers. `reference_values.json` was calculated separately
with 60-digit Decimal arithmetic and separately transcribed constants and states.
`independent_reference.py` checks those frozen values without importing or running
the subject; it has no reference-writing mode. Two calculations can share a wrong
interpretation, so source review and the declared scope remain essential.

The relative tolerance of 2e-14 checks arithmetic reproduction. It is **not** a
measurement uncertainty, physical error bound or claimed fourteen-digit knowledge
of nature. Constant uncertainties remain in the manifest; their propagation and
model-error estimation are outside this example.

No source publication, database dump, source image or third-party prose is bundled.
The pack contains original code, chosen inputs and derived numerical results with
attribution. This does not grant rights to redistribute the underlying publications.

## Run

From this directory, with PVS installed:

```powershell
python independent_reference.py
python derive_values.py --output derived_values.json
python -m pvs validate pvs.yaml --output evidence
python -m pvs verify evidence
```

This two-step producer/validation route also works on native Windows. In a fresh
copy on a platform supporting PVS's supervised execution, `python -m pvs run
pvs.yaml --output evidence` runs the declared subject automatically. It must start
without an existing `derived_values.json` output.

To connect another implementation, copy this case, keep the reference files and
their SHA-256 pins, declare the new subject and map its output quantities and units.
Review applicability and choose justified comparison tolerances for that model.
Do not regenerate the frozen answers from the model being tested.
