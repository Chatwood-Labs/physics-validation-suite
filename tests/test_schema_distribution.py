from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest

from pvs.schemas import SCHEMA_FILES


def test_historical_schemas_match_the_verified_028_release_bytes() -> None:
    root = Path(__file__).resolve().parents[1]
    hashes = json.loads((root / "tests/fixtures/frozen-schema-sha256.json").read_text())
    for name, digest in hashes.items():
        assert hashlib.sha256((root / "schemas" / name).read_bytes()).hexdigest() == digest
        assert (
            hashlib.sha256(files("pvs.schemas").joinpath(name).read_bytes()).hexdigest() == digest
        )


@pytest.mark.parametrize("filename", sorted(SCHEMA_FILES.values()))
def test_public_schema_is_byte_identical_to_packaged_runtime_schema(filename: str) -> None:
    project_root = Path(__file__).resolve().parents[1]
    public_bytes = (project_root / "schemas" / filename).read_bytes()
    packaged_bytes = files("pvs.schemas").joinpath(filename).read_bytes()

    assert public_bytes == packaged_bytes


def test_current_and_frozen_manifest_schema_aliases_are_unambiguous() -> None:
    packaged = files("pvs.schemas")
    current = json.loads(packaged.joinpath("pvs-manifest.schema.json").read_text(encoding="utf-8"))
    named_v3 = json.loads(
        packaged.joinpath("pvs-manifest-v3.schema.json").read_text(encoding="utf-8")
    )
    frozen_v1 = json.loads(
        packaged.joinpath("pvs-manifest-v1.schema.json").read_text(encoding="utf-8")
    )

    assert current == named_v3
    assert current["properties"]["schema"]["const"] == "pvs-manifest/3"
    assert frozen_v1["properties"]["schema"]["const"] == "pvs-manifest/1"


@pytest.mark.parametrize(
    ("stem", "discriminator"),
    [
        ("case", "pvs-case/2"),
        ("evidence", "pvs-evidence/3"),
        ("manifest", "pvs-manifest/3"),
    ],
)
def test_current_schema_aliases_are_byte_identical(stem: str, discriminator: str) -> None:
    project_root = Path(__file__).resolve().parents[1]
    for root in (project_root / "schemas", files("pvs.schemas")):
        current = root.joinpath(f"pvs-{stem}.schema.json")
        version = discriminator.rsplit("/", 1)[1]
        frozen = root.joinpath(f"pvs-{stem}-v{version}.schema.json")
        assert current.read_bytes() == frozen.read_bytes()
        parsed = json.loads(current.read_text(encoding="utf-8"))
        assert parsed["properties"]["schema"]["const"] == discriminator
