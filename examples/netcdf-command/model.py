#!/usr/bin/env python3
"""Generate a tiny deterministic NetCDF field for adapter demonstration."""

from __future__ import annotations

from pathlib import Path

import netCDF4
import numpy as np


def main() -> None:
    output = Path("output/field.nc")
    output.parent.mkdir(parents=True, exist_ok=True)
    x = np.linspace(0.0, 1.0, 9, dtype=np.float64)
    with netCDF4.Dataset(output, "w", format="NETCDF4_CLASSIC") as dataset:
        dataset.createDimension("x", x.size)
        coordinate = dataset.createVariable("x", "f8", ("x",))
        coordinate.units = "m"
        coordinate[:] = x
        field = dataset.createVariable("u", "f8", ("x",))
        field.units = "1"
        field[:] = 1.0 - x


if __name__ == "__main__":
    main()

