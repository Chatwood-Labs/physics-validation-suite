#!/usr/bin/env python3
"""Exercise a released-style wheel in a disposable binary-only environment."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def _run(*arguments: str, cwd: Path | None = None) -> None:
    subprocess.run(arguments, cwd=cwd, check=True)


def _python(environment: Path) -> Path:
    if sys.platform == "win32":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--case", required=True, type=Path)
    arguments = parser.parse_args()
    wheel = arguments.wheel.resolve(strict=True)
    case = arguments.case.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="pvs-clean-wheel-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        output = root / "evidence"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = _python(environment)
        _run(
            str(python),
            "-m",
            "pip",
            "install",
            "--only-binary=:all:",
            f"{wheel}[all]",
        )
        _run(str(python), "-m", "pip", "check")
        _run(str(python), "-m", "pvs", "validate", str(case), "--output", str(output))
        evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
        status = evidence.get("record", {}).get("summary", {}).get("status")
        if status != "PASS":
            raise SystemExit(f"clean wheel validation status was {status!r}, not PASS")
        _run(str(python), "-m", "pvs", "verify", str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
