#!/usr/bin/env python3
"""Independent, standard-library derivation of equation-based plasma anchors.

This script uses only the Python standard library and the cited equations.
It transcribes the cited equations and emits deterministic JSON for comparison
with the separately frozen reference_values.json artefact.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ME_KG = 9.1093837139e-31
EV_J = 1.602176634e-19
MP_KG = 1.67262192595e-27
MU_D = 1.9990075012699
MU_T = 2.99371703403
MU_ALPHA = 3.972599690252
EPSILON_0_F_M = 8.8541878188e-12
ALPHA_MASS_KG = 6.6446573450e-27

BH_BG = 34.3827
BH_MRC2_KEV = 1124656.0
BH_C1 = 1.17302e-9
BH_C2 = 1.51361e-2
BH_C3 = 7.51886e-2
BH_C4 = 4.60643e-3
BH_C5 = 1.35000e-2
BH_C6 = -1.06750e-4
BH_C7 = 1.36600e-5

BH_TEMPERATURES_KEV = [
    0.2,
    0.5,
    1.0,
    1.5,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
    6.0,
    7.0,
    8.0,
    9.0,
    10.0,
    12.0,
    15.0,
    20.0,
    30.0,
    40.0,
    50.0,
    70.0,
    80.0,
    100.0,
]


def nrl_ln_lambda_ei(
    ne_cm3: float,
    te_ev: float,
    ti_ev: float | None = None,
    z: float = 1.0,
    mu: float = 2.5,
) -> float:
    """NRL 2023 electron-ion Coulomb logarithm, p. 34."""

    if ti_ev is None:
        ti_ev = te_ev
    ion_speed_threshold = ti_ev * (ME_KG / (mu * MP_KG))
    if te_ev > 10.0 * z * z and te_ev > ion_speed_threshold:
        return 24.0 - math.log(math.sqrt(ne_cm3) / te_ev)
    if te_ev > ion_speed_threshold:
        return 23.0 - math.log(math.sqrt(ne_cm3) * z * te_ev ** (-1.5))
    ni_cm3 = ne_cm3 / z
    return 16.0 - math.log(math.sqrt(ni_cm3) * ti_ev ** (-1.5) * z**2 * mu)


def nrl_electron_collision(ne_m3: float, te_ev: float) -> tuple[float, float, float]:
    ne_cm3 = ne_m3 * 1.0e-6
    log_lambda = nrl_ln_lambda_ei(ne_cm3, te_ev)
    frequency = 2.91e-6 * ne_cm3 * log_lambda * te_ev ** (-1.5)
    return log_lambda, frequency, 1.0 / frequency


def nrl_ei_equilibration_dt(ne_m3: float, temperature_ev: float) -> dict[str, float]:
    """NRL additive species coefficient for an equal-number D-T mixture."""

    nd_m3 = ne_m3 / 2.0
    nt_m3 = ne_m3 / 2.0
    log_lambda = nrl_ln_lambda_ei(
        ne_m3 * 1.0e-6,
        temperature_ev,
        temperature_ev,
        1.0,
        MU_D,
    )
    prefactor = 3.2e-9 * log_lambda / temperature_ev**1.5
    frequency = prefactor * (nd_m3 * 1.0e-6 / MU_D + nt_m3 * 1.0e-6 / MU_T)
    effective_mass_ratio = ne_m3 / (nd_m3 / MU_D + nt_m3 / MU_T)
    return {
        "effective_mass_ratio": effective_mass_ratio,
        "ln_lambda": log_lambda,
        "frequency_s-1": frequency,
        "tau_e_equation_s": 1.0 / frequency,
        "tau_delta_temperature_s": 1.0 / (2.0 * frequency),
    }


def nrl_bremsstrahlung(
    ne_m3: float,
    ions: list[tuple[float, float]],
    te_ev: float,
) -> float:
    """NRL 2023 p. 58 Eq. (30), returning W m^-3."""

    ne_cm3 = ne_m3 * 1.0e-6
    sum_z2n_cm3 = math.fsum(z * z * density_m3 * 1.0e-6 for z, density_m3 in ions)
    return 1.69e-32 * ne_cm3 * math.sqrt(te_ev) * sum_z2n_cm3 * 1.0e6


def bosch_hale_dt(temperature_kev: float) -> float:
    """Bosch-Hale Eq. (12), D-T coefficients, converted to m^3 s^-1."""

    numerator = temperature_kev * (
        BH_C2 + temperature_kev * (BH_C4 + temperature_kev * BH_C6)
    )
    denominator = 1.0 + temperature_kev * (
        BH_C3 + temperature_kev * (BH_C5 + temperature_kev * BH_C7)
    )
    theta = temperature_kev / (1.0 - numerator / denominator)
    xi = (BH_BG * BH_BG / (4.0 * theta)) ** (1.0 / 3.0)
    value_cm3_s = (
        BH_C1
        * theta
        * math.sqrt(xi / (BH_MRC2_KEV * temperature_kev**3))
        * math.exp(-3.0 * xi)
    )
    return value_cm3_s * 1.0e-6


def evans_ion_fraction(energy_mev: float, equal_stopping_energy_mev: float) -> float:
    """Ion fraction from Evans Eq. (27a), printed p. 4 (PDF page 7)."""

    return 1.0 / (1.0 + (energy_mev / equal_stopping_energy_mev) ** 1.5)


def build_values() -> dict[str, Any]:
    collision_temperatures = [100.0, 1000.0, 15000.0]
    collision = [nrl_electron_collision(1.0e20, value) for value in collision_temperatures]
    equilibration_temperatures = [1000.0, 5000.0, 15000.0]
    equilibration = [
        nrl_ei_equilibration_dt(1.0e20, value) for value in equilibration_temperatures
    ]
    return {
        "schema": "pvs-plasma-reference-values/3",
        "constants": {
            "electron_mass_kg": ME_KG,
            "elementary_charge_c": EV_J,
            "proton_mass_kg": MP_KG,
            "deuteron_proton_mass_ratio": MU_D,
            "triton_proton_mass_ratio": MU_T,
            "alpha_proton_mass_ratio": MU_ALPHA,
            "vacuum_permittivity_f_m": EPSILON_0_F_M,
            "alpha_mass_kg": ALPHA_MASS_KG,
        },
        "nrl_electron_collision": {
            "electron_density_m-3": 1.0e20,
            "temperature_ev": collision_temperatures,
            "ln_lambda": [item[0] for item in collision],
            "frequency_s-1": [item[1] for item in collision],
            "collision_time_s": [item[2] for item in collision],
        },
        "nrl_dt_equilibration": {
            "electron_density_m-3": 1.0e20,
            "composition": {"deuterium_number_fraction": 0.5, "tritium_number_fraction": 0.5},
            "temperature_ev": equilibration_temperatures,
            "effective_mass_ratio": [item["effective_mass_ratio"] for item in equilibration],
            "ln_lambda": [item["ln_lambda"] for item in equilibration],
            "frequency_s-1": [item["frequency_s-1"] for item in equilibration],
            "tau_e_equation_s": [item["tau_e_equation_s"] for item in equilibration],
            "tau_delta_temperature_s": [
                item["tau_delta_temperature_s"] for item in equilibration
            ],
        },
        "nrl_bremsstrahlung": {
            "case_ids": ["pure-dt", "dt-plus-he-ash", "pure-h-like"],
            "power_w_m-3": [
                nrl_bremsstrahlung(1.0e20, [(1.0, 5.0e19), (1.0, 5.0e19)], 15000.0),
                nrl_bremsstrahlung(
                    1.2e20,
                    [(1.0, 4.0e19), (1.0, 4.0e19), (2.0, 2.0e19)],
                    15000.0,
                ),
                nrl_bremsstrahlung(2.0e20, [(1.0, 2.0e20)], 1000.0),
            ],
        },
        "bosch_hale_dt": {
            "temperature_kev": BH_TEMPERATURES_KEV,
            "reactivity_m3_s": [bosch_hale_dt(value) for value in BH_TEMPERATURES_KEV],
        },
        "evans_stopping_partition": {
            "temperature_kev": [50.0, 100.0],
            "alpha_energy_mev": [2.5, 1.5],
            "equal_stopping_energy_mev": [2.2, 4.3],
            "equation_ion_fraction": [
                evans_ion_fraction(2.5, 2.2),
                evans_ion_fraction(1.5, 4.3),
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("derived_values.json"))
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(build_values(), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
