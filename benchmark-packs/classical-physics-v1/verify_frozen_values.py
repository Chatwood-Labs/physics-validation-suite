#!/usr/bin/env python3
"""Read-only, independent 80-digit calculation of the 36 frozen scalar targets.

No import of the demonstration producer or PVS. The oscillator is evaluated by
the ODE power-series recurrence, pi by Machin's identity, heat-mode spatial
values by exact radicals, and decay by Decimal.exp(). Nothing is regenerated
or accepted automatically. Review changes to both this script and the frozen
record when updating a benchmark version.
"""

from __future__ import annotations

import json
from decimal import Decimal, localcontext
from pathlib import Path

D = Decimal


def arctan(value: Decimal) -> Decimal:
    power, total, denominator = value, value, 1
    while True:
        power *= -value * value
        denominator += 2
        updated = total + power / denominator
        if updated == total:
            return total
        total = updated


def expected_targets() -> dict[str, list[Decimal]]:
    with localcontext() as context:
        context.prec = 80
        pi = 16 * arctan(D(1) / 5) - 4 * arctan(D(1) / 239)
        positions, velocities = [], []
        for t in map(D, ["0", "0.5", "1", "2", "3"]):
            # x'' = -(k/m) x, x(0)=1/4 m, x'(0)=0 m/s; k/m=4 s^-2.
            term, position, velocity, n = D("0.25"), D("0.25"), D(0), 0
            while True:
                n += 1
                term *= -4 * t * t / ((2 * n - 1) * (2 * n))
                updated = position + term
                if t:
                    velocity += 2 * n * term / t
                if updated == position:
                    break
                position = updated
            positions.append(position)
            velocities.append(velocity)
        spatial = [D(0), D(2).sqrt() / 2, D(1), D(2).sqrt() / 2, D(0)]
        return {
            "oscillator/position_m": positions,
            "oscillator/velocity_m_s": velocities,
            # The conserved total follows from initial energy, not computed x,v.
            "oscillator/total_energy_j": [D("0.25")] * 5,
            "heat/temperature_excess_k": [
                10 * value * (-pi * pi * t / 10).exp()
                for t in map(D, ["0", "0.5", "2"])
                for value in spatial
            ],
            "kepler/circular_speed_m_s": [D(1), D("0.5"), D(1) / 3],
            # T=2*pi*r/v; radii and velocities give these integer multiples.
            "kepler/orbital_period_s": [2 * pi, 16 * pi, 54 * pi],
        }


def verify() -> int:
    frozen = json.loads(
        (Path(__file__).parent / "reference_values.json").read_text(encoding="utf-8"),
        parse_float=D,
        parse_int=D,
    )
    count = 0
    for pointer, expected in expected_targets().items():
        family, key = pointer.split("/")
        actual = frozen[family][key]
        if family == "heat":
            actual = [item for row in actual for item in row]
        if len(actual) != len(expected):
            raise ValueError(f"Wrong target count at {pointer}")
        for index, (observed, target) in enumerate(zip(actual, expected, strict=True)):
            if abs(observed - target) > D("2e-16") * abs(target):
                raise ValueError(f"Frozen target differs at {pointer}/{index}")
            count += 1
    return count


if __name__ == "__main__":
    print(json.dumps({"status": "PASS", "scalar_targets": verify(), "decimal_precision": 80}))
