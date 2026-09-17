"""Evaluate declared SI plasma scales; never read or rewrite frozen answers.

This reference-equation subject is a runnable integration example, not a
simulation or a claim of experimental validation. See README.md for conventions.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def derive_values() -> dict[str, object]:
    # CODATA 2022 central values; uncertainties are retained in source_manifest.json.
    charge = 1.602176634e-19
    electron_mass = 9.1093837139e-31
    deuteron_mass = 3.3435837768e-27
    epsilon_0 = 8.8541878188e-12
    mu_0 = 1.25663706127e-6
    speed_of_light = 299792458.0
    # Explicit pack-designed states: (n_e=n_D /m3, T_e/eV, T_D/eV, B_z/T, E_x/V/m).
    states = (
        ("low_density", 1e18, 10.0, 5.0, 0.1, 100.0),
        ("intermediate", 1e19, 100.0, 100.0, 1.0, 2000.0),
        ("high_density", 1e20, 1000.0, 500.0, 5.0, 15000.0),
    )
    points = {}
    for name, density, te, ti, field, electric in states:
        te_joule = te * charge
        ti_joule = ti * charge
        omega_e = math.sqrt(density * charge**2 / (epsilon_0 * electron_mass))
        omega_i = math.sqrt(density * charge**2 / (epsilon_0 * deuteron_mass))
        cyclotron_i = charge * field / deuteron_mass
        thermal_i = math.sqrt(ti_joule / deuteron_mass)
        points[name] = {
            "electron_debye_length_m": math.sqrt(epsilon_0 * te_joule / (density * charge**2)),
            "electron_plasma_angular_frequency_rad_s": omega_e,
            "deuteron_plasma_angular_frequency_rad_s": omega_i,
            "deuteron_cyclotron_angular_frequency_rad_s": cyclotron_i,
            "deuteron_thermal_speed_m_s": thermal_i,
            "deuteron_thermal_gyroradius_m": thermal_i / cyclotron_i,
            "alfven_speed_m_s": field / math.sqrt(mu_0 * density * deuteron_mass),
            "total_plasma_beta": 2.0 * mu_0 * density * (te_joule + ti_joule) / field**2,
            "electron_inertial_length_m": speed_of_light / omega_e,
            # E=(E_x,0,0), B=(0,0,B_z): E cross B points along negative y.
            "exb_drift_y_m_s": -electric / field,
        }
    return {"schema": "pvs-plasma-scales-values/1", "points": points}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protected_names = (
        "derive_values.py",
        "independent_reference.py",
        "reference_values.json",
        "reference_values.schema.json",
        "source_manifest.json",
        "pvs.yaml",
        "README.md",
    )
    for name in protected_names:
        protected = Path(__file__).parent / name
        same_path = args.output.resolve() == protected.resolve()
        same_file = args.output.exists() and protected.exists() and args.output.samefile(protected)
        if same_path or same_file:
            parser.error("pack source and reference files cannot be used as outputs")
    args.output.write_text(
        json.dumps(derive_values(), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
