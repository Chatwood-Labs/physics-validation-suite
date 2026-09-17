#!/usr/bin/env python3
"""Tiny deterministic subject used to demonstrate the command adapter."""

from __future__ import annotations

import json
import math
from pathlib import Path


def main() -> None:
    config = json.loads(Path("input.json").read_text(encoding="utf-8"))
    initial = float(config["initial"])
    decay_rate = float(config["decay_rate"])
    times = [float(value) for value in config["times"]]
    population = [initial * math.exp(-decay_rate * time) for time in times]
    output = Path("output/result.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"times": times, "population": population}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

