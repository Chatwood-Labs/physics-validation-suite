#!/usr/bin/env python3
"""Derive release names from pyproject.toml and reject active version drift.

Historical release notes, qualification records, scientific case versions and
frozen evidence contracts are deliberately outside the active-release scan.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path, PurePath
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "physics-validation-suite"
ACTIVE_FILES = (
    "README.md", "BUNDLE_README.md", "test-pvs.sh", "test-pvs.ps1", "docs/release-integrity.md",
    "tests/test_release_assembly.py", "tests/test_reproducible_build.py",
    "tests/test_build_backend.py", "tests/test_supply_chain.py", "tests/test_cli.py",
)
ACTIVE_REFERENCE = re.compile(
    r"(?:physics[_-]validation[_-]suite[-_]v?|pvs[-_]v?|PVS v?|"
    r"RELEASE_NOTES_|--version\s+)(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)"
)


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def release_metadata(root: Path = ROOT) -> dict[str, str]:
    project = _toml(root / "pyproject.toml")["project"]
    version = project["version"]
    if project["name"] != PROJECT or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", version
    ):
        raise ValueError("release requires physics-validation-suite and an X.Y.Z[preN] version")
    tag = re.sub(
        r"(a|b|rc)(\d+)$",
        lambda match: "-" + {"a": "alpha", "b": "beta", "rc": "rc"}[match[1]] + "." + match[2],
        version,
    )
    return {
        "version": version,
        "tag": f"v{tag}",
        "wheel": f"physics_validation_suite-{version}-py3-none-any.whl",
        "sdist": f"physics_validation_suite-{version}.tar.gz",
        "source_archive": f"physics-validation-suite-{version}-source.zip",
        "source_root": f"physics_validation_suite-{version}",
        "release_notes": f"RELEASE_NOTES_{version}.md",
        "bundle": f"pvs-{version}-release-bundle.zip",
        "provenance": f"pvs-{version}.provenance.intoto.json",
        "candidate": f"pvs-{version}-release-candidate",
        "sample_directory": f"sample-plasma-evidence-v{version}",
    }


def _version_drift_errors(
    root: PurePath, path: PurePath, contents: str, version: str
) -> list[str]:
    """Report stale references as repository-relative POSIX path:line diagnostics."""
    relative = path.relative_to(root).as_posix()
    return [
        f"{relative}:{number}: {found} != {version}"
        for number, line in enumerate(contents.splitlines(), 1)
        for found in ACTIVE_REFERENCE.findall(line)
        if found != version
    ]


def check_release_tree(root: Path = ROOT) -> dict[str, str]:
    metadata = release_metadata(root)
    version = metadata["version"]
    errors = []

    urls = _toml(root / "pyproject.toml")["project"].get("urls", {})
    source_url = urls.get("Source", "").rstrip("/")
    if not source_url or urls.get("Documentation") != f"{source_url}/tree/{metadata['tag']}/docs":
        errors.append("Documentation URL must target the current source tag's docs directory")
    if not (root / "docs/README.md").is_file():
        errors.append("versioned documentation requires docs/README.md")

    runtime = ast.parse((root / "src/pvs/version.py").read_text(encoding="utf-8"))
    versions = [
        ast.literal_eval(node.value)
        for node in runtime.body if isinstance(node, ast.Assign)
        if any(isinstance(target, ast.Name) and target.id == "__version__"
               for target in node.targets)
    ]
    if versions != [version]:
        errors.append(f"runtime __version__ {versions!r} != {version}")
    locked = [item["version"] for item in _toml(root / "uv.lock")["package"]
              if item["name"] == PROJECT]
    if locked != [version]:
        errors.append(f"uv.lock project versions {locked!r} != {version}")
    citation = (root / "CITATION.cff").read_text(encoding="utf-8")
    if re.findall(r"(?m)^version: (\S+)$", citation) != [version]:
        errors.append(f"CITATION.cff version != {version}")
    notes = root / metadata["release_notes"]
    if notes.exists() and (not notes.is_file() or not notes.read_text(encoding="utf-8").startswith(
        f"# PVS {version} release notes\n"
    )):
        errors.append(f"mismatched current release notes: {notes.name}")
    readme = (root / "README.md").read_text(encoding="utf-8")
    for required in (f"`{version}`", f"`{metadata['tag']}`"):
        if required not in readme:
            errors.append(f"README.md lacks current release declaration: {required}")
    bundle_readme = (root / "BUNDLE_README.md").read_text(encoding="utf-8")
    if not bundle_readme.startswith(f"# PVS {version} release bundle\n"):
        errors.append("BUNDLE_README.md lacks the current release heading")
    for name in (metadata["source_archive"], metadata["wheel"]):
        if name not in bundle_readme:
            errors.append(f"BUNDLE_README.md lacks current artifact: {name}")
    # Each workflow is found recursively, including the hidden .github tree.
    workflows = sorted((root / ".github/workflows").rglob("*.y*ml"))
    if not workflows:
        errors.append("source distribution lacks .github/workflows")
    for path in [*(root / name for name in ACTIVE_FILES), *workflows]:
        errors.extend(_version_drift_errors(
            root, path, path.read_text(encoding="utf-8"), version
        ))
    for name, pattern in (
        ("test-pvs.sh", r'if version != "([^"\n]+)":'),
        ("test-pvs.ps1", r'\$Metadata.version -cne "([^"\n]+)"'),
    ):
        if re.findall(pattern, (root / name).read_text(encoding="utf-8")) != [version]:
            errors.append(f"{name} must require exactly release {version}")
    if errors:
        raise ValueError("release version drift:\n" + "\n".join(errors))
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        metadata = check_release_tree(args.root) if args.check else release_metadata(args.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"{exc}\n")
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8", newline="\n") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in metadata.items())
    print(json.dumps(metadata, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
