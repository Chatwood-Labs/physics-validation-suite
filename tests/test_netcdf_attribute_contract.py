"""Numeric admission of NetCDF packing metadata, before binary64 arithmetic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pytest
from conftest import base_case, dump_case

from pvs.api import validate_case
from pvs.artifacts import ResolvedArtifact
from pvs.errors import ArtifactError
from pvs.jsonutil import MAX_SAFE_INTEGER, load_strict
from pvs.models import Status
from pvs.readers import ArtifactReader
from pvs.verify import verify_target


def _write_variable(
    root: Path, raw: Any, *, dtype: str = "i8", **attributes: Any,
) -> ResolvedArtifact:
    path = root / "result.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("point", len(raw))
        variable = dataset.createVariable("x", dtype, ("point",))
        variable.set_auto_maskandscale(False)
        variable[:] = raw
        variable.units = "1"
        for key, value in attributes.items():
            variable.setncattr(key, value)
    return ResolvedArtifact(
        id="result", root=root,
        declaration={"path": "result.nc", "role": "output", "format": "netcdf"},
    )


def _source() -> dict[str, str]:
    return {
        "artifact": "result", "variable": "x", "unit": "1",
        "netcdf_decoding": "pvs-netcdf/1",
    }


@pytest.mark.parametrize("attribute", ["scale_factor", "add_offset"])
@pytest.mark.parametrize(
    "value",
    [
        np.int64(MAX_SAFE_INTEGER + 1),
        np.int64(MAX_SAFE_INTEGER + 2),
        np.int64(-MAX_SAFE_INTEGER - 1),
        np.int64(-MAX_SAFE_INTEGER - 2),
        np.int64(np.iinfo(np.int64).min),
        np.int64(np.iinfo(np.int64).max),
        np.uint64(MAX_SAFE_INTEGER + 1),
        np.uint64(MAX_SAFE_INTEGER + 2),
        np.uint64(np.iinfo(np.uint64).max),
    ],
)
def test_unsafe_integer_packing_attributes_are_rejected_before_conversion(
    tmp_path: Path, attribute: str, value: Any,
) -> None:
    artifact = _write_variable(tmp_path, [1], **{attribute: value})

    # Verify that the fixture retained integer storage, rather than rounding
    # an already-floating value before the reader receives it.
    with netCDF4.Dataset(artifact.path) as dataset:
        stored = dataset.variables["x"].getncattr(attribute)
        assert np.asarray(stored).dtype == value.dtype
        assert int(stored) == int(value)
    with pytest.raises(
        ArtifactError,
        match=f"NetCDF {attribute}: integer value is outside the exact binary64 range",
    ):
        ArtifactReader({artifact.id: artifact}).select(_source())


@pytest.mark.parametrize("attribute", ["scale_factor", "add_offset"])
@pytest.mark.parametrize(
    "value",
    [
        np.int64(MAX_SAFE_INTEGER), np.int64(-MAX_SAFE_INTEGER), np.int64(0),
        np.uint64(MAX_SAFE_INTEGER), np.uint64(0),
    ],
)
def test_safe_integer_packing_attribute_boundaries_are_preserved(
    tmp_path: Path, attribute: str, value: Any,
) -> None:
    raw = [1] if attribute == "scale_factor" else [0]
    artifact = _write_variable(tmp_path, raw, **{attribute: value})

    result = ArtifactReader({artifact.id: artifact}).select(_source())

    assert result.dtype == np.float64
    assert result.shape == (1,)
    assert result[0] == int(value)


@pytest.mark.parametrize("attribute", ["scale_factor", "add_offset"])
@pytest.mark.parametrize(
    "value", [np.float32(0.1), np.float64(0.1), np.float64(2**53), np.float64(1e308)],
)
def test_floating_packing_attributes_keep_binary64_conversion(
    tmp_path: Path, attribute: str, value: Any,
) -> None:
    raw = [1] if attribute == "scale_factor" else [0]
    artifact = _write_variable(tmp_path, raw, **{attribute: value})

    result = ArtifactReader({artifact.id: artifact}).select(_source())

    assert float(result[0]).hex() == float(value).hex()


@pytest.mark.parametrize(
    ("raw", "attribute", "value", "expected", "status"),
    [
        ([MAX_SAFE_INTEGER], "add_offset", np.int64(-MAX_SAFE_INTEGER - 2), -1, Status.ERROR),
        ([1], "scale_factor", np.uint64(MAX_SAFE_INTEGER + 2), float(2**53), Status.ERROR),
        ([MAX_SAFE_INTEGER], "add_offset", np.int64(-MAX_SAFE_INTEGER), 0, Status.PASS),
        ([1e308], "scale_factor", np.float64(2), 0, Status.ERROR),
    ],
)
def test_packing_attribute_outcomes_through_public_api(
    tmp_path: Path, raw: Any, attribute: str, value: Any, expected: float, status: Status,
) -> None:
    artifact = _write_variable(
        tmp_path, raw, dtype="f8" if isinstance(raw[0], float) else "i8", **{attribute: value},
    )
    case = base_case()
    case["artifacts"]["result"] = artifact.declaration
    case["package"]["embed_artifacts"] = True
    case["checks"] = [{
        "id": "packing", "type": "compare", "unit": "1", "metric": "linf", "tolerance": 0,
        "actual": _source(), "expected": {"value": [expected], "unit": "1"},
    }]

    outcome = validate_case(dump_case(tmp_path / "pvs.yaml", case), output_dir=tmp_path / "out")

    assert outcome.status is status
    result = load_strict(outcome.evidence_path)["record"]["checks"][0]
    if status is Status.ERROR:
        assert outcome.finding_id is None
        assert result["observed"] == result["criterion"] == {}
        assert ("outside the exact binary64 range" if value.dtype.kind in "iu"
                else "unpacking overflowed") in result["summary"]
    else:
        assert outcome.finding_id is not None
        assert result["observed"]["actual"] == expected
    assert verify_target(outcome.output_directory).valid


def test_large_integer_missing_metadata_stays_in_the_packed_domain(tmp_path: Path) -> None:
    missing = np.uint64(2**64 - 2)
    artifact = _write_variable(
        tmp_path, [missing, np.uint64(7)], dtype="u8", missing_value=missing,
    )

    result = ArtifactReader({artifact.id: artifact}).select(_source())

    # Missing-value metadata is compared before numeric ingestion, not used
    # in binary64 arithmetic. Rejecting its full-width sentinel would prevent
    # legitimate masked uint64 input from being read.
    assert np.isnan(result[0])
    assert result[1] == 7.0
