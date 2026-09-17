"""Independently check frozen plasma scales with 60-digit decimal arithmetic.

No subject imports, subject output, or shared numeric helpers are used. Constants
and states were transcribed separately from the declared source record. Algebra
is rearranged where possible. This script has no reference-writing option.
"""

from __future__ import annotations

import json
from decimal import Decimal, localcontext
from pathlib import Path


def calculate() -> dict[str, dict[str, Decimal]]:
    with localcontext() as context:
        context.prec = 60
        d = Decimal
        elementary_charge = d("1.602176634e-19")
        electron_kg = d("9.1093837139e-31")
        deuteron_kg = d("3.3435837768e-27")
        permittivity = d("8.8541878188e-12")
        permeability = d("1.25663706127e-6")
        c = d("299792458")
        states = {
            "low_density": ("1e18", "10", "5", "0.1", "100"),
            "intermediate": ("1e19", "100", "100", "1", "2000"),
            "high_density": ("1e20", "1000", "500", "5", "15000"),
        }
        result = {}
        for name, strings in states.items():
            n, electron_ev, ion_ev, magnetic_t, electric_v_m = map(d, strings)
            inverse_density = 1 / n
            momentum = (ion_ev * elementary_charge * deuteron_kg).sqrt()
            charge_density_factor = elementary_charge * (n / permittivity).sqrt()
            magnetic_pressure = magnetic_t**2 / (2 * permeability)
            thermal_pressure = elementary_charge * n * (electron_ev + ion_ev)
            result[name] = {
                "electron_debye_length_m": (
                    electron_ev * permittivity * inverse_density / elementary_charge
                ).sqrt(),
                "electron_plasma_angular_frequency_rad_s": charge_density_factor
                / electron_kg.sqrt(),
                "deuteron_plasma_angular_frequency_rad_s": charge_density_factor
                / deuteron_kg.sqrt(),
                "deuteron_cyclotron_angular_frequency_rad_s": magnetic_t
                / (deuteron_kg / elementary_charge),
                "deuteron_thermal_speed_m_s": momentum / deuteron_kg,
                "deuteron_thermal_gyroradius_m": momentum / (elementary_charge * magnetic_t),
                "alfven_speed_m_s": (
                    magnetic_t**2 * inverse_density / (permeability * deuteron_kg)
                ).sqrt(),
                "total_plasma_beta": thermal_pressure / magnetic_pressure,
                "electron_inertial_length_m": (
                    electron_kg * permittivity * inverse_density
                ).sqrt()
                * c
                / elementary_charge,
                "exb_drift_y_m_s": -(electric_v_m * magnetic_t) / magnetic_t**2,
            }
        return result


def main() -> None:
    frozen = json.loads(
        (Path(__file__).parent / "reference_values.json").read_text(encoding="utf-8"),
        parse_float=Decimal,
        parse_int=Decimal,
    )
    expected = calculate()
    if frozen["schema"] != "pvs-plasma-scales-values/1" or set(frozen["points"]) != set(expected):
        raise SystemExit("FAIL: unexpected schema or point set")
    count = 0
    for name, values in expected.items():
        if set(frozen["points"][name]) != set(values):
            raise SystemExit(f"FAIL: unexpected quantity set at {name}")
        for quantity, reference in values.items():
            actual = frozen["points"][name][quantity]
            if abs(actual / reference - 1) > Decimal("2e-15"):
                raise SystemExit(
                    f"FAIL: frozen {name}/{quantity} differs from independent calculation"
                )
            count += 1
    print(f"PASS: {count} frozen scalar values checked independently at 60 decimal digits")


if __name__ == "__main__":
    main()
