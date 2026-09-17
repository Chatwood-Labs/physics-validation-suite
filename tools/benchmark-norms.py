"""Measure local norm costs; this is not production performance qualification."""

from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import time
import tracemalloc

import numpy as np

from pvs import __version__
from pvs.checks.metrics import error_norms


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[64, 200_000, 1_000_000])
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 1 for size in args.sizes):
        parser.error("sizes and repeats must be positive")
    results = []
    for size in args.sizes:
        # Linear profiles reproduce the review workload; strided fields also
        # exercise noncontiguous multidimensional inputs used by selectors.
        for layout in ("profile", "strided-field"):
            if layout == "profile":
                actual = np.linspace(-1.0, 1.0, size, dtype=np.float64)
                expected = np.zeros_like(actual)
            else:
                actual = np.linspace(-1.0, 1.0, size * 4, dtype=np.float64).reshape(size, 4)
                actual = actual[:, ::2]
                expected = np.full_like(actual, 0.125)
            error_norms(actual, expected)  # Warm-up; excluded from timing.
            timings = []
            for _ in range(args.repeats):
                start = time.perf_counter()
                norms = error_norms(actual, expected)
                timings.append(time.perf_counter() - start)
            gc.collect()
            tracemalloc.start()
            measured = error_norms(actual, expected)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert measured == norms
            results.append({
                "layout": layout, "size_parameter": size, "elements": int(actual.size),
                "shape": list(actual.shape), "input_c_contiguous": actual.flags.c_contiguous,
                "median_seconds": statistics.median(timings), "times_seconds": timings,
                "tracemalloc_peak_bytes": peak,
                "norms_hex": {key: value.hex() for key, value in norms.items()},
            })
    print(json.dumps({
        "pvs_version": __version__, "python": platform.python_version(),
        "numpy": np.__version__, "platform": platform.platform(),
        "timing_repeats": args.repeats,
        "timing_method": "Median of independent calls after one untimed warm-up per workload",
        "memory_method": "Separate tracemalloc call; inputs allocated before tracing; not RSS",
        "qualification": "Local microbenchmark, not production performance qualification",
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
