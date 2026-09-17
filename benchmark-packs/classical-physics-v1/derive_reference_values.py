#!/usr/bin/env python3
"""Analytical demonstration subject; it never reads the frozen answers.

These are chosen ideal problems, not experimental measurements. See README.md
and source_manifest.json for equations, assumptions and the claim boundary.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def build_values() -> dict[str, Any]:
    mass, stiffness, amplitude = 2.0, 8.0, 0.25
    times = [0.0, 0.5, 1.0, 2.0, 3.0]
    omega = math.sqrt(stiffness / mass)
    positions = [amplitude * math.cos(omega * t) for t in times]
    velocities = [-amplitude * omega * math.sin(omega * t) for t in times]
    length, diffusivity, excess = 1.0, 0.1, 10.0
    heat_times, heat_positions = [0.0, 0.5, 2.0], [0.0, 0.25, 0.5, 0.75, 1.0]
    mu, radii = 1.0, [1.0, 4.0, 9.0]
    return {
        "schema": "pvs-classical-physics-values/1",
        "oscillator": {
            "mass_kg": mass,
            "spring_constant_n_m": stiffness,
            "amplitude_m": amplitude,
            "time_s": times,
            "position_m": positions,
            "velocity_m_s": velocities,
            "total_energy_j": [
                0.5 * stiffness * x * x + 0.5 * mass * v * v
                for x, v in zip(positions, velocities, strict=True)
            ],
        },
        "heat": {
            "length_m": length,
            "diffusivity_m2_s": diffusivity,
            "initial_amplitude_k": excess,
            "time_s": heat_times,
            "position_m": heat_positions,
            "temperature_excess_k": [
                [
                    excess * math.sin(math.pi * x / length)
                    * math.exp(-diffusivity * (math.pi / length) ** 2 * t)
                    for x in heat_positions
                ]
                for t in heat_times
            ],
        },
        "kepler": {
            "gravitational_parameter_m3_s2": mu,
            "radius_m": radii,
            "circular_speed_m_s": [math.sqrt(mu / r) for r in radii],
            "orbital_period_s": [2.0 * math.pi * math.sqrt(r**3 / mu) for r in radii],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    # Do not allow the demonstration command to overwrite pack inputs or code.
    pack = Path(__file__).resolve().parent
    for protected in pack.iterdir():
        if protected.name == "derived_values.json" or not protected.is_file():
            continue
        if output.resolve() == protected.resolve() or (
            output.exists() and output.samefile(protected)
        ):
            parser.error("--output must not overwrite a benchmark-pack source or reference")
    output.write_text(
        json.dumps(build_values(), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
