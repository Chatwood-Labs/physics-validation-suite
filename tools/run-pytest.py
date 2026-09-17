"""Run pytest with a unique, short, owned base directory on every invocation.

Pytest deletes --basetemp at startup. Never accept a caller-supplied directory;
always give it a child of a freshly allocated private temporary root instead.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--keep-temp", action="store_true")
    options, pytest_args = parser.parse_known_args()
    if "--" in pytest_args or any(
        argument == "--basetemp" or argument.startswith("--basetemp=")
        for argument in pytest_args
    ):
        parser.error("pytest's base directory is managed by this launcher")
    temporary = None
    if options.keep_temp:
        root = Path(tempfile.mkdtemp(prefix="pvs-test-"))
    else:
        temporary = tempfile.TemporaryDirectory(prefix="pvs-test-")
        root = Path(temporary.name)
    print(f"pytest temporary root: {root}", flush=True)
    try:
        # CLI options follow PYTEST_ADDOPTS; our owned directory takes priority.
        return subprocess.call([
            sys.executable, "-m", "pytest", *pytest_args, "--basetemp", str(root / "tests"),
        ])
    finally:
        if temporary is None:
            print(f"Retained pytest temporary root: {root}", flush=True)
        else:
            try:
                temporary.cleanup()
            except OSError as exc:
                print(f"Could not clean pytest temporary root {root}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
