#!/usr/bin/env python3
"""Convert public NRL data and calculate event-rate density, not reactivity.

The published table is an input to this demonstration adapter. The frozen
expected outputs are separately calculated and never read by this program.
No cross-section model, velocity integral, or experimental solver is executed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_values() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    table = json.loads((root / "published_table.json").read_text(encoding="utf-8"))
    inputs = json.loads((root / "inputs.json").read_text(encoding="utf-8"))
    reactivities = {}
    rates = {}
    for channel in ("dt", "dd_total", "d_he3"):
        # NRL p.45 gives cm^3/s; one cm^3 is 10^-6 m^3.
        values = [float(value) * 1.0e-6 for value in table["reactivity_cm3_s"][channel]]
        reactivities[channel] = values
        mixture = inputs["mixtures"][channel]
        n1 = mixture["first_density_m3"]
        n2 = mixture["second_density_m3"]
        # Xie et al., arXiv:2212.01840v2, p.2 Eq.(1). DD counts events,
        # not deuterons consumed: identical pairs must only be counted once.
        pair_divisor = 2.0 if mixture["first_species"] == mixture["second_species"] else 1.0
        rates[channel] = [n1 * n2 * value / pair_divisor for value in values]
    return {
        "schema": "pvs-fusion-reactivity-values/1",
        "pack_version": "1.0.0",
        "temperature_keV": [float(value) for value in table["temperature_keV"]],
        "mixtures": inputs["mixtures"],
        "reactivity_m3_s": reactivities,
        "event_rate_per_m3_s": rates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("derived_values.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    protected = {path.resolve() for path in root.iterdir() if path.name != "derived_values.json"}
    aliases_protected = args.output.exists() and any(
        args.output.samefile(path) for path in protected
    )
    if args.output.resolve() in protected or aliases_protected:
        parser.error("output must not overwrite a pack input, reference, or source file")
    args.output.write_text(
        json.dumps(build_values(), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
