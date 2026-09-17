"""Unit tests for the domain-agnostic readers and check engine.

These tests intentionally exercise the engine directly as well as through artefact
selectors.  Case-schema validation has its own test surface; the check engine must
still fail closed when it receives malformed or non-finite values.
"""

from __future__ import annotations

import copy
import json
import warnings
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np
import pytest

from pvs.artifacts import ResolvedArtifact
from pvs.checks import evaluate_checks
from pvs.checks.metrics import error_norms, numeric_array
from pvs.errors import ArtifactError
from pvs.models import CheckResult, Status
from pvs.readers import ArtifactReader


def _artifact(
    path: Path,
    artifact_id: str,
    artifact_format: str,
    *,
    required: bool = True,
) -> ResolvedArtifact:
    return ResolvedArtifact(
        id=artifact_id,
        root=path.parent,
        declaration={
            "path": path.name,
            "role": "output",
            "format": artifact_format,
            "required": required,
        },
    )


def _json_artifact(
    tmp_path: Path,
    payload: Any,
    artifact_id: str = "data",
) -> ResolvedArtifact:
    path = tmp_path / f"{artifact_id}.json"
    path.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    return _artifact(path, artifact_id, "json")


def _raw_json_artifact(
    tmp_path: Path,
    raw: str,
    artifact_id: str = "data",
) -> ResolvedArtifact:
    path = tmp_path / f"{artifact_id}.json"
    path.write_text(raw, encoding="utf-8")
    return _artifact(path, artifact_id, "json")


def _netcdf_artifact(
    tmp_path: Path,
    values: Any,
    artifact_id: str = "field",
    variable: str = "u",
) -> ResolvedArtifact:
    array = np.asarray(values, dtype=np.float64)
    path = tmp_path / f"{artifact_id}.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        dimensions: list[str] = []
        for index, length in enumerate(array.shape):
            name = f"dim_{index}"
            dataset.createDimension(name, length)
            dimensions.append(name)
        output = dataset.createVariable(variable, "f8", tuple(dimensions))
        if array.ndim == 0:
            output.assignValue(float(array))
        else:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="Setting the shape on a NumPy array has been deprecated.*",
                    category=DeprecationWarning,
                )
                output[:] = array
    return _artifact(path, artifact_id, "netcdf")


def _masked_netcdf_artifact(tmp_path: Path) -> ResolvedArtifact:
    path = tmp_path / "masked.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("x", 2)
        dataset.createDimension("y", 3)
        output = dataset.createVariable("u", "f8", ("x", "y"), fill_value=-999.0)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Setting the shape on a NumPy array has been deprecated.*",
                category=DeprecationWarning,
            )
            output[:] = np.asarray(
                [[1.0, -999.0, 3.0], [4.0, 5.0, 6.0]],
                dtype=np.float64,
            )
    return _artifact(path, "masked", "netcdf")


def _packed_netcdf_artifact(
    tmp_path: Path,
    values: Any,
    *,
    dtype: str,
    attributes: dict[str, Any] | None = None,
    fill_value: Any | None = None,
    artifact_id: str = "packed",
) -> ResolvedArtifact:
    array = np.asarray(values, dtype=dtype)
    path = tmp_path / f"{artifact_id}.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("x", array.size)
        create_options = {} if fill_value is None else {"fill_value": fill_value}
        output = dataset.createVariable("u", dtype, ("x",), **create_options)
        output.set_auto_maskandscale(False)
        output[:] = array
        for name, value in (attributes or {}).items():
            output.setncattr(name, value)
    return _artifact(path, artifact_id, "netcdf")


def _evaluate(
    declaration: dict[str, Any],
    *artifacts: ResolvedArtifact,
) -> CheckResult:
    artifact_map = {artifact.id: artifact for artifact in artifacts}
    contracted = copy.deepcopy(declaration)
    if contracted.get("type") in {
        "finite",
        "range",
        "monotonic",
        "compare",
        "reference",
        "conservation",
    }:
        unit = contracted.setdefault("unit", "1")

        def annotate_operand(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                value.setdefault("unit", unit)
                artifact = artifact_map.get(str(value.get("artifact")))
                if artifact is not None and artifact.format == "netcdf":
                    value.setdefault("netcdf_decoding", "pvs-netcdf/1")
                return value
            return {"value": value, "unit": unit}

        if "source" in contracted:
            contracted["source"] = annotate_operand(contracted["source"])
        for key in ("actual", "expected", "normalization"):
            if key in contracted:
                contracted[key] = annotate_operand(contracted[key])
        for term in contracted.get("terms", []):
            term["value"] = annotate_operand(term["value"])
    return evaluate_checks([contracted], artifact_map)[0]


def _source(
    artifact: ResolvedArtifact,
    *,
    unit: str = "1",
    **selectors: Any,
) -> dict[str, Any]:
    source = {"artifact": artifact.id, "unit": unit, **selectors}
    if artifact.format == "netcdf":
        source["netcdf_decoding"] = "pvs-netcdf/1"
    return source


# Readers --------------------------------------------------------------------


def test_json_reader_supports_root_pointer_escaping_array_indices_and_cache(
    tmp_path: Path,
) -> None:
    artifact = _json_artifact(
        tmp_path,
        {"a/b": {"~key": [10, 20, 30]}, "": "empty-key", "values": [1, 2, 3]},
    )
    reader = ArtifactReader({artifact.id: artifact})

    original = reader.read("data")
    assert reader.select({"artifact": "data", "pointer": ""}) is original
    assert reader.select({"artifact": "data", "pointer": "/a~1b/~0key/1"}) == 20
    assert reader.select({"artifact": "data", "pointer": "/"}) == "empty-key"

    artifact.path.write_text('{"replacement": true}', encoding="utf-8")
    assert reader.read("data") is original


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (None, [1, 2, 3]),
        ("size", 3),
        ("sum", 6),
        ("mean", 2),
        ("min", 1),
        ("max", 3),
        ("first", 1),
        ("last", 3),
    ],
)
def test_json_reader_reductions(
    tmp_path: Path,
    operation: str | None,
    expected: Any,
) -> None:
    artifact = _json_artifact(tmp_path, {"values": [1, 2, 3]})
    source: dict[str, Any] = {"artifact": "data", "pointer": "/values"}
    if operation is not None:
        source["reduce"] = operation

    assert ArtifactReader({"data": artifact}).select(source) == expected


@pytest.mark.parametrize(
    "raw",
    [
        '{"duplicate": 1, "duplicate": 2}',
        '{"value": NaN}',
        '{"value": Infinity}',
        '{"value": -Infinity}',
        # Valid JSON can still overflow Python's binary64 parser.  It must not silently
        # introduce a non-finite value into a parser advertised as strict.
        '{"value": 1e400}',
    ],
)
def test_json_reader_rejects_duplicate_keys_and_every_nonfinite_number(
    tmp_path: Path,
    raw: str,
) -> None:
    artifact = _raw_json_artifact(tmp_path, raw)

    with pytest.raises(ArtifactError):
        ArtifactReader({"data": artifact}).read("data")


@pytest.mark.parametrize(
    ("pointer", "message"),
    [
        ("not-a-pointer", "must be empty or start with '/'"),
        ("/missing", "token not found"),
        ("/values/-1", "token not found"),
        ("/values/not-an-index", "token not found"),
        ("/values/99", "token not found"),
        ("/values/0/child", "token not found"),
    ],
)
def test_json_reader_rejects_malformed_or_unresolvable_pointers(
    tmp_path: Path,
    pointer: str,
    message: str,
) -> None:
    artifact = _json_artifact(tmp_path, {"values": [1]})

    with pytest.raises(ArtifactError, match=message):
        ArtifactReader({"data": artifact}).select({"artifact": "data", "pointer": pointer})


@pytest.mark.parametrize(
    "pointer",
    [
        "/trailing~",
        "/unknown~2escape",
        "/unknown~xescape",
        "/double~~0escape",
        "/nested/bad~9token",
    ],
)
def test_json_reader_rejects_invalid_rfc6901_escape_sequences(
    tmp_path: Path,
    pointer: str,
) -> None:
    artifact = _json_artifact(tmp_path, {"nested": {"value": 1}})

    with pytest.raises(ArtifactError, match="invalid JSON pointer escape"):
        ArtifactReader({"data": artifact}).select({"artifact": "data", "pointer": pointer})


@pytest.mark.parametrize(
    "index_token",
    [
        "",
        "00",
        "01",
        "+1",
        "-0",
        "-1",
        "1.0",
        " 1",
        "1 ",
        "%31",
        "\N{ARABIC-INDIC DIGIT ONE}",
    ],
)
def test_json_reader_rejects_noncanonical_array_index_tokens(
    tmp_path: Path,
    index_token: str,
) -> None:
    artifact = _json_artifact(tmp_path, {"values": ["zero", "one"]})

    with pytest.raises(ArtifactError, match="JSON pointer token not found"):
        ArtifactReader({"data": artifact}).select(
            {"artifact": "data", "pointer": f"/values/{index_token}"}
        )


def test_csv_reader_parses_numeric_columns_and_preserves_other_cells(tmp_path: Path) -> None:
    path = tmp_path / "profile.csv"
    path.write_text(
        "step,value,label,blank\n0, 1 ,alpha,\n1,2.5,beta,\n2,-3,gamma,\n",
        encoding="utf-8",
    )
    artifact = _artifact(path, "profile", "csv")
    reader = ArtifactReader({"profile": artifact})

    rows = reader.read("profile")
    assert rows[0] == {"step": "0", "value": " 1 ", "label": "alpha", "blank": ""}
    assert reader.select({"artifact": "profile", "column": "step"}) == [0, 1, 2]
    assert reader.select({"artifact": "profile", "column": "value"}) == [1, 2.5, -3]
    assert reader.select({"artifact": "profile", "column": "label"}) == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert reader.select({"artifact": "profile", "column": "blank"}) == ["", "", ""]
    assert reader.select(
        {"artifact": "profile", "column": "value", "reduce": "sum"}
    ) == pytest.approx(0.5)


def test_csv_reader_reports_missing_column_and_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "profile.csv"
    path.write_text("x,y\n1,2\n", encoding="utf-8")
    artifact = _artifact(path, "profile", "csv")

    with pytest.raises(ArtifactError, match="CSV column not found"):
        ArtifactReader({"profile": artifact}).select({"artifact": "profile", "column": "z"})

    invalid_path = tmp_path / "invalid.csv"
    invalid_path.write_bytes(b"x\n\xff\n")
    invalid = _artifact(invalid_path, "invalid", "csv")
    with pytest.raises(ArtifactError, match="could not read artifact invalid"):
        ArtifactReader({"invalid": invalid}).read("invalid")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "\n",
        ",\n1,2\n",
        "x,,z\n1,2,3\n",
        "x,y,\n1,2,3\n",
        "x,x\n1,2\n",
        "x,y,x\n1,2,3\n",
    ],
)
def test_csv_reader_rejects_missing_empty_and_duplicate_headers(
    tmp_path: Path,
    raw: str,
) -> None:
    path = tmp_path / "headers.csv"
    path.write_text(raw, encoding="utf-8")
    artifact = _artifact(path, "headers", "csv")

    with pytest.raises(
        ArtifactError,
        match="CSV headers must be present, non-empty and unique",
    ):
        ArtifactReader({"headers": artifact}).read("headers")


@pytest.mark.parametrize(
    "raw",
    [
        "x,y\n1\n",
        "x,y\n1,2,3\n",
        "x,y,z\n1,2,3\n4,5\n",
        "x,y\n1,2\n3,4,5\n",
    ],
)
def test_csv_reader_rejects_ragged_rows(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "ragged.csv"
    path.write_text(raw, encoding="utf-8")
    artifact = _artifact(path, "ragged", "csv")

    with pytest.raises(
        ArtifactError,
        match="CSV rows must contain exactly one field per header",
    ):
        ArtifactReader({"ragged": artifact}).read("ragged")


def test_netcdf_reader_preserves_shape_and_converts_masked_values_to_nan(
    tmp_path: Path,
) -> None:
    artifact = _masked_netcdf_artifact(tmp_path)
    reader = ArtifactReader({"masked": artifact})

    selected = reader.select(
        {
            "artifact": "masked",
            "variable": "u",
            "unit": "1",
            "netcdf_decoding": "pvs-netcdf/1",
        }
    )
    assert isinstance(selected, np.ndarray)
    assert selected.shape == (2, 3)
    assert selected[0, 0] == 1.0
    assert np.isnan(selected[0, 1])
    assert (
        reader.select(
            {
                "artifact": "masked",
                "variable": "u",
                "unit": "1",
                "netcdf_decoding": "pvs-netcdf/1",
                "reduce": "size",
            }
        )
        == 6
    )
    assert (
        reader.select(
            {
                "artifact": "masked",
                "variable": "u",
                "unit": "1",
                "netcdf_decoding": "pvs-netcdf/1",
                "reduce": "last",
            }
        )
        == 6.0
    )


def test_netcdf_profile_decodes_masked_integer_scale_offset_and_valid_range(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [-9999, -8888, -2, 0, 2, 4, 6],
        dtype="i2",
        fill_value=-9999,
        attributes={
            "missing_value": np.int16(-8888),
            "valid_range": np.asarray([-2, 4], dtype=np.int16),
            "scale_factor": 0.5,
            "add_offset": 10.0,
            "units": "K",
        },
    )

    selected = ArtifactReader({artifact.id: artifact}).select(
        _source(artifact, variable="u", unit="K")
    )

    assert selected.dtype == np.float64
    assert selected.shape == (7,)
    np.testing.assert_allclose(
        selected,
        [np.nan, np.nan, 9.0, 10.0, 11.0, 12.0, np.nan],
        equal_nan=True,
    )


def test_netcdf_profile_masks_the_implicit_default_fill_for_integer_data(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [-32767, 7],
        dtype="i2",
        attributes={"units": "1"},
        artifact_id="default-fill",
    )

    selected = ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))

    assert selected.dtype == np.float64
    assert np.isnan(selected[0])
    assert selected[1] == 7.0


def test_netcdf_profile_applies_unsigned_reinterpretation_before_decoding(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [-2, 0, 1],
        dtype="i2",
        attributes={"_Unsigned": "TrUe", "units": "1"},
        artifact_id="unsigned",
    )

    selected = ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))

    np.testing.assert_array_equal(selected, [65534.0, 0.0, 1.0])


def test_netcdf_profile_applies_individual_valid_bounds_in_the_packed_domain(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [-1, 0, 1, 2],
        dtype="i2",
        attributes={"valid_min": np.int16(0), "valid_max": np.int16(1)},
        artifact_id="individual-valid-bounds",
    )

    selected = ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))

    np.testing.assert_allclose(selected, [np.nan, 0.0, 1.0, np.nan], equal_nan=True)


def test_netcdf_profile_requires_exact_declared_variable_unit_without_conversion(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [100.0],
        dtype="f8",
        attributes={"units": "cm"},
        artifact_id="centimetres",
    )

    with pytest.raises(ArtifactError, match="does not exactly match declared unit 'm'"):
        ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u", unit="m"))


def test_netcdf_case_unit_is_authoritative_when_variable_units_attribute_is_absent(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [273.15],
        dtype="f8",
        artifact_id="unitless-metadata",
    )

    selected = ArtifactReader({artifact.id: artifact}).select(
        _source(artifact, variable="u", unit="K")
    )

    np.testing.assert_array_equal(selected, [273.15])


@pytest.mark.parametrize(
    ("attributes", "message"),
    [
        ({"_Unsigned": "yes"}, "_Unsigned must be the string"),
        (
            {"valid_range": np.asarray([0, 2], dtype=np.int16), "valid_min": np.int16(0)},
            "valid_range cannot be combined",
        ),
        ({"valid_range": np.asarray([2, 0], dtype=np.int16)}, "lower bound exceeds"),
        ({"scale_factor": float("inf")}, "scale_factor must be finite"),
        ({"add_offset": [0.0, 1.0]}, "add_offset must be one numeric scalar"),
        ({"missing_value": 1.5}, "must contain integers for an integer variable"),
    ],
)
def test_netcdf_profile_rejects_ambiguous_or_invalid_decoding_attributes(
    tmp_path: Path,
    attributes: dict[str, Any],
    message: str,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [0, 1],
        dtype="i2",
        attributes=attributes,
        artifact_id="invalid-attributes",
    )

    with pytest.raises(ArtifactError, match=message):
        ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))


def test_netcdf_profile_rejects_unsigned_marker_on_noninteger_variable(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [1.0],
        dtype="f8",
        attributes={"_Unsigned": "true"},
        artifact_id="unsigned-float",
    )

    with pytest.raises(ArtifactError, match="requires a signed integer variable"):
        ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))


def test_netcdf_profile_rejects_scale_offset_overflow(tmp_path: Path) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [np.finfo(np.float64).max],
        dtype="f8",
        attributes={"scale_factor": 2.0},
        artifact_id="unpack-overflow",
    )

    with pytest.raises(ArtifactError, match="unpacking overflowed"):
        ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))


def test_netcdf_profile_preserves_scalar_shape(tmp_path: Path) -> None:
    path = tmp_path / "scalar.nc"
    with netCDF4.Dataset(path, "w") as dataset:
        output = dataset.createVariable("u", "i4")
        output.assignValue(7)
    artifact = _artifact(path, "scalar", "netcdf")

    selected = ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))

    assert isinstance(selected, np.ndarray)
    assert selected.shape == ()
    assert selected.item() == 7.0


def test_netcdf_profile_rejects_inexact_uint64_values(tmp_path: Path) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [2**53],
        dtype="u8",
        artifact_id="unsafe-uint64",
    )

    with pytest.raises(ArtifactError, match="outside the exact binary64 range"):
        ArtifactReader({artifact.id: artifact}).select(_source(artifact, variable="u"))


def test_netcdf_size_is_dimensionless_and_does_not_claim_the_variable_unit(
    tmp_path: Path,
) -> None:
    artifact = _packed_netcdf_artifact(
        tmp_path,
        [1.0, 2.0],
        dtype="f8",
        attributes={"units": "m"},
        artifact_id="sized",
    )

    assert (
        ArtifactReader({artifact.id: artifact}).select(
            {**_source(artifact, variable="u"), "reduce": "size"}
        )
        == 2
    )


def test_netcdf_reader_reports_missing_selector_variable_and_corrupt_file(
    tmp_path: Path,
) -> None:
    artifact = _netcdf_artifact(tmp_path, [1.0, 2.0])
    reader = ArtifactReader({"field": artifact})

    with pytest.raises(ArtifactError, match="requires a variable selector"):
        reader.select(
            {
                "artifact": "field",
                "unit": "1",
                "netcdf_decoding": "pvs-netcdf/1",
            }
        )
    with pytest.raises(ArtifactError, match="NetCDF variable not found"):
        reader.select(
            {
                "artifact": "field",
                "variable": "missing",
                "unit": "1",
                "netcdf_decoding": "pvs-netcdf/1",
            }
        )

    corrupt_path = tmp_path / "corrupt.nc"
    corrupt_path.write_bytes(b"not a netcdf file")
    corrupt = _artifact(corrupt_path, "corrupt", "netcdf")
    with pytest.raises(ArtifactError, match="could not read NetCDF artifact corrupt"):
        ArtifactReader({"corrupt": corrupt}).select(
            {
                "artifact": "corrupt",
                "variable": "u",
                "unit": "1",
                "netcdf_decoding": "pvs-netcdf/1",
            }
        )


@pytest.mark.parametrize("operation", ["sum", "mean", "min", "max", "first", "last"])
def test_reader_rejects_non_size_reductions_of_empty_values(
    tmp_path: Path,
    operation: str,
) -> None:
    artifact = _json_artifact(tmp_path, {"values": []})

    with pytest.raises(ArtifactError, match="empty value"):
        ArtifactReader({"data": artifact}).select(
            {"artifact": "data", "pointer": "/values", "reduce": operation}
        )


def test_reader_rejects_unknown_artifacts_formats_reductions_and_selector_mismatches(
    tmp_path: Path,
) -> None:
    json_artifact = _json_artifact(tmp_path, {"value": 1}, "json")
    csv_path = tmp_path / "table.csv"
    csv_path.write_text("value\n1\n", encoding="utf-8")
    csv_artifact = _artifact(csv_path, "csv", "csv")
    unsupported = _artifact(tmp_path / "data.unknown", "unsupported", "unknown")
    unsupported.path.write_text("content", encoding="utf-8")
    reader = ArtifactReader(
        {
            "json": json_artifact,
            "csv": csv_artifact,
            "unsupported": unsupported,
        }
    )

    with pytest.raises(ArtifactError, match="unknown artifact"):
        reader.read("missing")
    with pytest.raises(ArtifactError, match="unsupported artifact format"):
        reader.read("unsupported")
    with pytest.raises(ArtifactError, match="unknown reduction"):
        reader.select({"artifact": "json", "pointer": "/value", "reduce": "median"})
    with pytest.raises(ArtifactError, match="pointer selector is only valid for JSON"):
        reader.select({"artifact": "csv", "pointer": "/value"})
    with pytest.raises(ArtifactError, match="column selector is only valid for CSV"):
        reader.select({"artifact": "json", "column": "value"})
    with pytest.raises(ArtifactError, match="variable selector is only valid for NetCDF"):
        reader.select({"artifact": "json", "variable": "value"})


def test_component_selector_selects_the_final_axis_before_reduction(tmp_path: Path) -> None:
    artifact = _json_artifact(tmp_path, {"rows": [[0.05, 0.92], [0.25, 0.50]]})
    reader = ArtifactReader({artifact.id: artifact})

    energies = reader.select(
        {
            "artifact": artifact.id,
            "pointer": "/rows",
            "component": 0,
            "unit": "MeV",
        }
    )
    last_fraction = reader.select(
        {
            "artifact": artifact.id,
            "pointer": "/rows",
            "component": 1,
            "reduce": "last",
            "unit": "1",
        }
    )

    np.testing.assert_array_equal(energies, [0.05, 0.25])
    assert last_fraction == pytest.approx(0.50)


@pytest.mark.parametrize(
    ("payload", "component", "message"),
    [
        ({"value": 1.0}, 0, "at least one array dimension"),
        ({"value": [[1.0, 2.0]]}, 2, "out of bounds"),
        ({"value": [[1.0, 2.0]]}, -1, "non-negative integer"),
        ({"value": [[1.0, 2.0]]}, 1.0, "non-negative integer"),
        ({"value": [[1.0], [2.0, 3.0]]}, 0, "rectangular array"),
    ],
)
def test_component_selector_rejects_invalid_type_shape_and_bounds(
    tmp_path: Path,
    payload: dict[str, Any],
    component: Any,
    message: str,
) -> None:
    artifact = _json_artifact(tmp_path, payload)

    with pytest.raises(ArtifactError, match=message):
        ArtifactReader({artifact.id: artifact}).select(
            {
                "artifact": artifact.id,
                "pointer": "/value",
                "component": component,
                "unit": "1",
            }
        )


def test_component_bounds_failure_is_a_check_error(tmp_path: Path) -> None:
    artifact = _json_artifact(tmp_path, {"rows": [[1.0, 2.0]]})

    result = _evaluate(
        {
            "id": "component",
            "type": "finite",
            "source": _source(artifact, pointer="/rows", component=2),
            "unit": "1",
        },
        artifact,
    )

    assert result.status is Status.ERROR
    assert "out of bounds" in result.summary


# Checks ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("present", "required", "expected_status"),
    [
        (True, True, Status.PASS),
        (False, True, Status.FAIL),
        (False, False, Status.WARN),
    ],
)
def test_exists_check_pass_fail_and_optional_warn(
    tmp_path: Path,
    present: bool,
    required: bool,
    expected_status: Status,
) -> None:
    path = tmp_path / "result.json"
    if present:
        path.write_text("{}", encoding="utf-8")
    artifact = _artifact(path, "result", "json", required=required)

    result = _evaluate(
        {"id": "exists", "type": "exists", "artifact": "result", "required": required},
        artifact,
    )

    assert result.status is expected_status
    assert result.observed == {"exists": present}
    assert result.criterion["required_type"] == "regular_file"


def test_exists_check_with_unknown_artifact_is_error() -> None:
    result = _evaluate({"id": "exists", "type": "exists", "artifact": "undeclared"})

    assert result.status is Status.ERROR
    assert "KeyError" in result.summary


def test_schema_check_passes_valid_document_and_reports_all_violations(tmp_path: Path) -> None:
    schema = _json_artifact(
        tmp_path,
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["count", "date"],
            "properties": {
                "count": {"type": "integer", "minimum": 0},
                "date": {"type": "string", "format": "date"},
            },
            "additionalProperties": False,
        },
        "schema",
    )
    valid = _json_artifact(tmp_path, {"count": 0, "date": "2026-08-28"}, "valid")
    invalid = _json_artifact(
        tmp_path,
        {"count": -1, "date": "not-a-date", "extra": True},
        "invalid",
    )
    declaration = {
        "id": "schema",
        "type": "schema",
        "artifact": "valid",
        "schema_artifact": "schema",
    }

    passing = _evaluate(declaration, valid, schema)
    failing = _evaluate({**declaration, "artifact": "invalid"}, invalid, schema)

    assert passing.status is Status.PASS
    assert passing.observed == {"error_count": 0, "errors": []}
    assert failing.status is Status.FAIL
    assert failing.observed["error_count"] == 3
    assert len(failing.observed["errors"]) == 3


@pytest.mark.parametrize(
    ("schema_payload", "summary_fragment"),
    [
        (["not", "an", "object"], "JSON Schema artifact must contain an object"),
        (
            {"$ref": "https://example.test/schema.json"},
            "remote or external JSON Schema $ref is forbidden",
        ),
        (
            {"allOf": [{"$ref": "other.json"}]},
            "remote or external JSON Schema $ref is forbidden",
        ),
        (
            {"$dynamicRef": "https://example.test/dynamic.json"},
            "remote or external JSON Schema $dynamicRef is forbidden",
        ),
        (
            {"allOf": [{"$recursiveRef": "file:///etc/passwd"}]},
            "remote or external JSON Schema $recursiveRef is forbidden",
        ),
    ],
)
def test_schema_check_rejects_non_object_and_external_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schema_payload: Any,
    summary_fragment: str,
) -> None:
    def fail_if_registry_is_constructed(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("external schema retrieval path was reached")

    monkeypatch.setattr("pvs.checks.engine.Registry", fail_if_registry_is_constructed)
    actual = _json_artifact(tmp_path, {}, "actual")
    schema = _json_artifact(tmp_path, schema_payload, "schema")

    result = _evaluate(
        {
            "id": "schema",
            "type": "schema",
            "artifact": "actual",
            "schema_artifact": "schema",
        },
        actual,
        schema,
    )

    assert result.status is Status.ERROR
    assert result.observed == {}
    assert result.criterion == {}
    assert summary_fragment in result.summary


def test_schema_check_turns_an_invalid_schema_into_error(tmp_path: Path) -> None:
    actual = _json_artifact(tmp_path, {}, "actual")
    schema = _json_artifact(tmp_path, {"type": "definitely-not-a-type"}, "schema")

    result = _evaluate(
        {
            "id": "schema",
            "type": "schema",
            "artifact": "actual",
            "schema_artifact": "schema",
        },
        actual,
        schema,
    )

    assert result.status is Status.ERROR
    assert "SchemaError" in result.summary


def test_finite_check_passes_finite_values_and_fails_every_nonfinite_class(
    tmp_path: Path,
) -> None:
    finite = _json_artifact(tmp_path, {"values": [-1.0, 0.0, 1.0]}, "finite")
    nonfinite = _netcdf_artifact(
        tmp_path,
        [float("nan"), float("inf"), -float("inf"), 1.0],
        "nonfinite",
    )

    passing = _evaluate(
        {"id": "finite", "type": "finite", "source": _source(finite, pointer="/values")},
        finite,
    )
    failing = _evaluate(
        {
            "id": "nonfinite",
            "type": "finite",
            "source": _source(nonfinite, variable="u"),
        },
        nonfinite,
    )

    assert passing.status is Status.PASS
    assert passing.observed["finite_count"] == 3
    assert passing.observed["nonfinite_count"] == 0
    assert failing.status is Status.FAIL
    assert failing.observed["finite_count"] == 1
    assert failing.observed["nonfinite_count"] == 3
    assert failing.observed["minimum"] == 1.0
    assert failing.observed["maximum"] == 1.0


@pytest.mark.parametrize("payload", [[], [True, False], ["1", "2"]])
def test_finite_check_errors_when_no_numeric_values_are_examined(
    tmp_path: Path,
    payload: list[Any],
) -> None:
    artifact = _json_artifact(tmp_path, {"values": payload})

    result = _evaluate(
        {"id": "finite", "type": "finite", "source": _source(artifact, pointer="/values")},
        artifact,
    )

    assert result.status is Status.ERROR


def test_range_check_inclusive_boundaries_pass_and_exclusive_boundaries_fail(
    tmp_path: Path,
) -> None:
    artifact = _json_artifact(tmp_path, {"values": [0.0, 0.5, 1.0]})
    base = {"id": "range", "type": "range", "source": _source(artifact, pointer="/values")}

    inclusive = _evaluate({**base, "minimum": 0.0, "maximum": 1.0}, artifact)
    exclusive = _evaluate(
        {
            **base,
            "minimum": 0.0,
            "maximum": 1.0,
            "inclusive_minimum": False,
            "inclusive_maximum": False,
        },
        artifact,
    )

    assert inclusive.status is Status.PASS
    assert inclusive.observed["failing_count"] == 0
    assert exclusive.status is Status.FAIL
    assert exclusive.observed["failing_count"] == 2
    assert exclusive.observed["failing_indices"] == [[0], [2]]


def test_range_check_reports_out_of_range_and_nonfinite_indices(tmp_path: Path) -> None:
    artifact = _netcdf_artifact(
        tmp_path,
        [[0.0, -0.1], [1.1, float("nan")]],
    )

    result = _evaluate(
        {
            "id": "range",
            "type": "range",
            "source": _source(artifact, variable="u"),
            "minimum": 0.0,
            "maximum": 1.0,
        },
        artifact,
    )

    assert result.status is Status.FAIL
    assert result.observed["nonfinite_count"] == 1
    assert result.observed["failing_count"] == 3
    assert result.observed["failing_indices"] == [[0, 1], [1, 0], [1, 1]]


def test_range_check_errors_on_an_empty_operand(tmp_path: Path) -> None:
    artifact = _json_artifact(tmp_path, {"values": []})

    result = _evaluate(
        {
            "id": "range",
            "type": "range",
            "source": _source(artifact, pointer="/values"),
            "minimum": 0.0,
        },
        artifact,
    )

    assert result.status is Status.ERROR


@pytest.mark.parametrize(
    ("direction", "values"),
    [
        ("increasing", [0.0, 1.0, 2.0]),
        ("decreasing", [2.0, 1.0, 0.0]),
        ("nondecreasing", [0.0, 0.0, 1.0]),
        ("nonincreasing", [1.0, 1.0, 0.0]),
    ],
)
def test_monotonic_check_passes_each_direction(
    tmp_path: Path,
    direction: str,
    values: list[float],
) -> None:
    artifact = _json_artifact(tmp_path, {"values": values})

    result = _evaluate(
        {
            "id": "monotonic",
            "type": "monotonic",
            "source": _source(artifact, pointer="/values"),
            "direction": direction,
            "absolute_tolerance": 0.0,
        },
        artifact,
    )

    assert result.status is Status.PASS
    assert result.observed["failing_count"] == 0


@pytest.mark.parametrize(
    ("direction", "values", "expected_status"),
    [
        # Strict directions require a step greater than the threshold.
        ("increasing", [0.0, 1.0], Status.FAIL),
        ("decreasing", [1.0, 0.0], Status.FAIL),
        # Non-strict directions permit an opposing step exactly at the threshold.
        ("nondecreasing", [0.0, -1.0], Status.PASS),
        ("nonincreasing", [0.0, 1.0], Status.PASS),
    ],
)
def test_monotonic_check_exact_tolerance_boundaries(
    tmp_path: Path,
    direction: str,
    values: list[float],
    expected_status: Status,
) -> None:
    artifact = _json_artifact(tmp_path, {"values": values})

    result = _evaluate(
        {
            "id": "monotonic",
            "type": "monotonic",
            "source": _source(artifact, pointer="/values"),
            "direction": direction,
            "absolute_tolerance": 1.0,
        },
        artifact,
    )

    assert result.status is expected_status


@pytest.mark.parametrize(
    ("values", "summary"),
    [
        ([[1.0, 2.0]], "requires a 1-D array"),
        ([1.0], "requires at least two values"),
    ],
)
def test_monotonic_check_errors_on_wrong_shape_or_too_few_values(
    tmp_path: Path,
    values: list[Any],
    summary: str,
) -> None:
    artifact = _json_artifact(tmp_path, {"values": values})

    result = _evaluate(
        {
            "id": "monotonic",
            "type": "monotonic",
            "source": _source(artifact, pointer="/values"),
            "direction": "increasing",
            "absolute_tolerance": 0.0,
        },
        artifact,
    )

    assert result.status is Status.ERROR
    assert summary in result.summary


def test_monotonic_check_errors_on_nonfinite_values(tmp_path: Path) -> None:
    artifact = _netcdf_artifact(tmp_path, [0.0, float("nan")])

    result = _evaluate(
        {
            "id": "monotonic",
            "type": "monotonic",
            "source": _source(artifact, variable="u"),
            "direction": "nondecreasing",
            "absolute_tolerance": 0.0,
        },
        artifact,
    )

    assert result.status is Status.ERROR
    assert "contains NaN or infinity" in result.summary


def test_monotonic_check_rejects_unknown_direction(tmp_path: Path) -> None:
    artifact = _json_artifact(tmp_path, {"values": [0.0, 1.0]})
    result = _evaluate(
        {
            "id": "monotonic",
            "type": "monotonic",
            "source": _source(artifact, pointer="/values"),
            "direction": "sideways",
            "absolute_tolerance": 0.0,
        },
        artifact,
    )

    assert result.status is Status.ERROR
    assert "unknown monotonic direction" in result.summary


@pytest.mark.parametrize(
    ("metric", "actual", "expected", "parameters", "failing_actual"),
    [
        ("absolute", 1.0, 0.0, {"tolerance": 1.0}, 1.25),
        (
            "relative",
            2.0,
            1.0,
            {"tolerance": 1.0, "scale_floor": 0.1},
            2.25,
        ),
        ("l1", [1.0, 1.0], [0.0, 0.0], {"tolerance": 2.0}, [1.0, 1.25]),
        ("l2", [3.0, 4.0], [0.0, 0.0], {"tolerance": 5.0}, [3.0, 4.1]),
        ("linf", [1.0, -2.0], [0.0, 0.0], {"tolerance": 2.0}, [1.0, -2.1]),
        (
            "close",
            1.5,
            1.0,
            {"absolute_tolerance": 0.25, "relative_tolerance": 0.25},
            1.5001,
        ),
    ],
)
def test_compare_metrics_pass_exact_boundaries_and_fail_above_them(
    metric: str,
    actual: Any,
    expected: Any,
    parameters: dict[str, float],
    failing_actual: Any,
) -> None:
    declaration = {
        "id": metric,
        "type": "compare",
        "actual": actual,
        "expected": expected,
        "metric": metric,
        **parameters,
    }

    passing = _evaluate(declaration)
    failing = _evaluate({**declaration, "actual": failing_actual})

    assert passing.status is Status.PASS
    assert passing.observed["gating_error"] == pytest.approx(passing.observed["permitted_error"])
    assert failing.status is Status.FAIL
    assert failing.observed["gating_error"] > failing.observed["permitted_error"]


def test_compare_close_preserves_multidimensional_shape_and_failure_indices(
    tmp_path: Path,
) -> None:
    actual = _json_artifact(tmp_path, {"value": [[1.0, 2.0], [3.0, 4.1]]}, "actual")
    expected = _json_artifact(tmp_path, {"value": [[1.0, 2.0], [3.0, 4.0]]}, "expected")

    result = _evaluate(
        {
            "id": "close",
            "type": "compare",
            "actual": _source(actual, pointer="/value"),
            "expected": _source(expected, pointer="/value"),
            "metric": "close",
            "absolute_tolerance": 0.05,
            "relative_tolerance": 0.0,
        },
        actual,
        expected,
    )

    assert result.status is Status.FAIL
    assert result.observed["shape"] == [2, 2]
    assert result.observed["failing_count"] == 1
    assert result.observed["failing_indices"] == [[1, 1]]


def test_compare_rejects_numpy_broadcasting_and_requires_identical_shapes(
    tmp_path: Path,
) -> None:
    actual = _json_artifact(tmp_path, {"value": [[1.0], [2.0]]}, "actual")
    expected = _json_artifact(tmp_path, {"value": [1.0, 2.0]}, "expected")

    result = _evaluate(
        {
            "id": "shape",
            "type": "compare",
            "actual": _source(actual, pointer="/value"),
            "expected": _source(expected, pointer="/value"),
            "metric": "close",
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
        },
        actual,
        expected,
    )

    assert result.status is Status.ERROR
    assert "requires identical shapes, got (2, 1) and (2,)" in result.summary


@pytest.mark.parametrize(
    ("actual", "expected", "metric", "message"),
    [
        ([1.0, 2.0], [1.0, 2.0], "absolute", "scalar-only"),
        ([], [], "l2", "numeric value must contain at least one element"),
        ([True], [True], "linf", "numeric value required"),
        (["1"], ["1"], "linf", "numeric value required"),
        ([float("nan")], [0.0], "linf", "non-finite numeric declaration"),
        ([float("inf")], [0.0], "close", "non-finite numeric declaration"),
    ],
)
def test_compare_rejects_malformed_non_numeric_empty_and_nonfinite_operands(
    actual: Any,
    expected: Any,
    metric: str,
    message: str,
) -> None:
    tolerance = (
        {"absolute_tolerance": 0.0, "relative_tolerance": 0.0}
        if metric == "close"
        else {"tolerance": 0.0}
    )

    result = _evaluate(
        {
            "id": "compare",
            "type": "compare",
            "actual": actual,
            "expected": expected,
            "metric": metric,
            **tolerance,
        }
    )

    assert result.status is Status.ERROR
    assert message in result.summary


def test_compare_rejects_nonfinite_values_loaded_from_artifacts(tmp_path: Path) -> None:
    actual = _netcdf_artifact(tmp_path, [float("nan")], "actual")
    expected = _netcdf_artifact(tmp_path, [0.0], "expected")

    result = _evaluate(
        {
            "id": "compare",
            "type": "compare",
            "actual": _source(actual, variable="u"),
            "expected": _source(expected, variable="u"),
            "metric": "linf",
            "tolerance": 0.0,
        },
        actual,
        expected,
    )

    assert result.status is Status.ERROR
    assert "comparison operands contain NaN or infinity" in result.summary


def test_compare_scalar_against_zero_reports_no_relative_error() -> None:
    result = _evaluate(
        {
            "id": "zero",
            "type": "compare",
            "actual": 0.0,
            "expected": 0.0,
            "metric": "absolute",
            "tolerance": 0.0,
        }
    )

    assert result.status is Status.PASS
    assert result.observed["relative_error"] == {
        "status": "UNAVAILABLE", "reason": "ZERO_REFERENCE",
    }


def test_numeric_checks_record_the_exact_declared_unit(tmp_path: Path) -> None:
    artifact = _json_artifact(tmp_path, {"temperature": [300.0]})

    result = _evaluate(
        {
            "id": "temperature",
            "type": "range",
            "source": _source(artifact, pointer="/temperature", unit="K"),
            "unit": "K",
            "minimum": 0.0,
        },
        artifact,
    )

    assert result.status is Status.PASS
    assert result.observed["unit"] == "K"
    assert result.criterion["unit"] == "K"


@pytest.mark.parametrize(
    "declaration",
    [
        {
            "id": "source-unit",
            "type": "finite",
            "source": {"artifact": "data", "pointer": "/values", "unit": "cm"},
            "unit": "m",
        },
        {
            "id": "actual-unit",
            "type": "compare",
            "actual": {"value": 100.0, "unit": "cm"},
            "expected": {"value": 1.0, "unit": "m"},
            "unit": "m",
            "metric": "absolute",
            "tolerance": 0.0,
        },
        {
            "id": "case-sensitive-unit",
            "type": "compare",
            "actual": {"value": 1.0, "unit": "kev"},
            "expected": {"value": 1.0, "unit": "keV"},
            "unit": "keV",
            "metric": "absolute",
            "tolerance": 0.0,
        },
        {
            "id": "term-unit",
            "type": "conservation",
            "terms": [
                {"value": {"value": 1.0, "unit": "kg"}, "coefficient": 1.0},
                {"value": {"value": 1.0, "unit": "g"}, "coefficient": -1.0},
            ],
            "expected": {"value": 0.0, "unit": "kg"},
            "unit": "kg",
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
        },
    ],
)
def test_unit_mismatches_are_errors_and_never_trigger_conversion(
    tmp_path: Path,
    declaration: dict[str, Any],
) -> None:
    artifact = _json_artifact(tmp_path, {"values": [1.0]})

    result = _evaluate(declaration, artifact)

    assert result.status is Status.ERROR
    assert "does not exactly match check unit" in result.summary
    assert result.observed == {}
    assert result.criterion == {}


@pytest.mark.parametrize(
    "declaration",
    [
        {
            "id": "missing-check-unit",
            "type": "finite",
            "source": {"artifact": "data", "pointer": "/values", "unit": "1"},
        },
        {
            "id": "missing-operand-unit",
            "type": "compare",
            "actual": {"value": 1.0},
            "expected": {"value": 1.0, "unit": "1"},
            "unit": "1",
            "metric": "absolute",
            "tolerance": 0.0,
        },
    ],
)
def test_engine_fails_closed_when_called_without_required_unit_metadata(
    tmp_path: Path,
    declaration: dict[str, Any],
) -> None:
    artifact = _json_artifact(tmp_path, {"values": [1.0]})

    result = evaluate_checks([declaration], {artifact.id: artifact})[0]

    assert result.status is Status.ERROR
    assert "unit" in result.summary


def test_relative_comparison_ratio_overflow_is_a_serializable_controlled_error() -> None:
    result = _evaluate(
        {
            "id": "ratio-overflow",
            "type": "compare",
            "actual": 10.0,
            "expected": 1e-308,
            "metric": "relative",
            "tolerance": 1.0,
            "scale_floor": 1e-308,
        }
    )

    assert result.status is Status.ERROR
    assert result.summary == "comparison ratio overflowed the finite numeric range"
    assert result.observed == {}
    assert result.criterion == {}
    assert json.loads(json.dumps(result.as_dict(), allow_nan=False))["status"] == "ERROR"


@pytest.mark.parametrize(
    "declaration",
    [
        {
            "id": "range-nan",
            "type": "range",
            "source": {"artifact": "data", "pointer": "/values"},
            "minimum": float("nan"),
        },
        {
            "id": "monotonic-inf",
            "type": "monotonic",
            "source": {"artifact": "data", "pointer": "/values"},
            "direction": "nondecreasing",
            "absolute_tolerance": float("inf"),
        },
        {
            "id": "compare-nan",
            "type": "compare",
            "actual": 1.0,
            "expected": 1.0,
            "metric": "absolute",
            "tolerance": float("nan"),
        },
        {
            "id": "relative-floor-inf",
            "type": "compare",
            "actual": 2.0,
            "expected": 1.0,
            "metric": "relative",
            "tolerance": 0.0,
            "scale_floor": float("inf"),
        },
        {
            "id": "close-inf",
            "type": "compare",
            "actual": 100.0,
            "expected": 1.0,
            "metric": "close",
            "absolute_tolerance": float("inf"),
            "relative_tolerance": 0.0,
        },
    ],
)
def test_numeric_checks_reject_nonfinite_configuration_values(
    tmp_path: Path,
    declaration: dict[str, Any],
) -> None:
    artifact = _json_artifact(tmp_path, {"values": [0.0, 1.0]})

    result = _evaluate(declaration, artifact)

    assert result.status is Status.ERROR
    assert "finite" in result.summary.lower()


def test_reference_check_uses_compare_semantics_and_retains_reference_id() -> None:
    result = _evaluate(
        {
            "id": "published-anchor",
            "type": "reference",
            "reference_id": "source-001",
            "actual": [1.0, 2.0],
            "expected": [1.0, 2.0],
            "metric": "linf",
            "tolerance": 0.0,
        }
    )

    assert result.status is Status.PASS
    assert result.reference_id == "source-001"
    assert result.as_dict()["reference_id"] == "source-001"


def test_reference_check_has_compare_fail_and_error_semantics() -> None:
    declaration = {
        "id": "published-anchor",
        "type": "reference",
        "reference_id": "source-001",
        "actual": [1.0, 2.1],
        "expected": [1.0, 2.0],
        "metric": "linf",
        "tolerance": 0.1,
    }

    failing = _evaluate(declaration)
    malformed = _evaluate({**declaration, "expected": [1.0]})

    assert failing.status is Status.FAIL
    assert failing.observed["gating_error"] > failing.observed["permitted_error"]
    assert malformed.status is Status.ERROR
    assert "requires identical shapes" in malformed.summary
    assert malformed.reference_id == "source-001"


def test_optional_failed_comparison_is_warn_not_fail() -> None:
    result = _evaluate(
        {
            "id": "advisory",
            "type": "compare",
            "required": False,
            "actual": 2.0,
            "expected": 1.0,
            "metric": "absolute",
            "tolerance": 0.0,
        }
    )

    assert result.status is Status.WARN


def test_conservation_passes_at_combined_tolerance_boundary_and_records_terms() -> None:
    result = _evaluate(
        {
            "id": "balance",
            "type": "conservation",
            "terms": [
                {"label": "in", "value": [4.0, 6.0], "coefficient": 1.0},
                {"label": "out", "value": [0.0], "coefficient": -1.0},
            ],
            "expected": 8.0,
            "normalization": 10.0,
            "absolute_tolerance": 1.0,
            "relative_tolerance": 0.1,
        }
    )

    assert result.status is Status.PASS
    assert result.observed["balance"] == 10.0
    assert result.observed["absolute_error"] == 2.0
    assert result.observed["permitted_error"] == 2.0
    assert result.observed["terms"] == [
        {
            "label": "in",
            "coefficient": 1.0,
            "raw_total": 10.0,
            "contribution": 10.0,
            "unit": "1",
        },
        {
            "label": "out",
            "coefficient": -1.0,
            "raw_total": 0.0,
            "contribution": -0.0,
            "unit": "1",
        },
    ]


def test_conservation_uses_fsum_across_terms_for_catastrophic_cancellation() -> None:
    result = _evaluate(
        {
            "id": "stable-balance",
            "type": "conservation",
            "terms": [
                {"label": "large-positive", "value": 1e16, "coefficient": 1.0},
                {"label": "small-residual", "value": 1.0, "coefficient": 1.0},
                {"label": "large-negative", "value": -1e16, "coefficient": 1.0},
            ],
            "expected": 1.0,
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
        }
    )

    assert result.status is Status.PASS
    assert result.observed["balance"] == 1.0
    assert result.observed["absolute_error"] == 0.0
    assert [term["contribution"] for term in result.observed["terms"]] == [
        1e16,
        1.0,
        -1e16,
    ]


def test_conservation_fails_above_boundary_and_defaults_normalization_to_expected() -> None:
    result = _evaluate(
        {
            "id": "balance",
            "type": "conservation",
            "terms": [
                {"value": 10.1, "coefficient": 1.0},
                {"value": 0.0, "coefficient": -1.0},
            ],
            "expected": 8.0,
            "absolute_tolerance": 1.0,
            "relative_tolerance": 0.1,
        }
    )

    assert result.status is Status.FAIL
    assert result.observed["normalization"] == 8.0
    assert result.observed["absolute_error"] == pytest.approx(2.1)
    assert result.observed["permitted_error"] == pytest.approx(1.8)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("term", [float("nan")], "non-finite numeric declaration"),
        ("term", [float("inf")], "non-finite numeric declaration"),
        ("expected", [0.0, 0.0], "expected value must be one finite scalar"),
        ("expected", float("nan"), "non-finite numeric declaration"),
        ("normalization", [1.0, 2.0], "normalization must be one finite scalar"),
        ("normalization", float("inf"), "non-finite numeric declaration"),
    ],
)
def test_conservation_rejects_nonfinite_or_nonscalar_operands(
    field: str,
    replacement: Any,
    message: str,
) -> None:
    declaration: dict[str, Any] = {
        "id": "balance",
        "type": "conservation",
        "terms": [
            {"value": 1.0, "coefficient": 1.0},
            {"value": 1.0, "coefficient": -1.0},
        ],
        "expected": 0.0,
        "normalization": 1.0,
        "absolute_tolerance": 0.0,
        "relative_tolerance": 0.0,
    }
    if field == "term":
        declaration["terms"][0]["value"] = replacement
    else:
        declaration[field] = replacement

    result = _evaluate(declaration)

    assert result.status is Status.ERROR
    assert message in result.summary


def test_conservation_rejects_nonfinite_terms_loaded_from_artifacts(tmp_path: Path) -> None:
    term = _netcdf_artifact(tmp_path, [1.0, float("inf")], "term")

    result = _evaluate(
        {
            "id": "balance",
            "type": "conservation",
            "terms": [
                {"value": _source(term, variable="u"), "coefficient": 1.0},
                {"value": 1.0, "coefficient": -1.0},
            ],
            "expected": 0.0,
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
        },
        term,
    )

    assert result.status is Status.ERROR
    assert "conservation term contains NaN or infinity" in result.summary


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("coefficient", float("nan")),
        ("absolute_tolerance", float("nan")),
        ("relative_tolerance", float("inf")),
    ],
)
def test_conservation_rejects_nonfinite_configuration_values(
    field: str,
    replacement: float,
) -> None:
    declaration: dict[str, Any] = {
        "id": "balance",
        "type": "conservation",
        "terms": [
            {"value": 1.0, "coefficient": 1.0},
            {"value": 1.0, "coefficient": -1.0},
        ],
        "expected": 0.0,
        "absolute_tolerance": 0.0,
        "relative_tolerance": 0.0,
    }
    if field == "coefficient":
        declaration["terms"][0]["coefficient"] = replacement
    else:
        declaration[field] = replacement

    result = _evaluate(declaration)

    assert result.status is Status.ERROR
    assert "finite" in result.summary.lower()


def test_missing_optional_source_is_skip_but_malformed_optional_source_is_error(
    tmp_path: Path,
) -> None:
    missing = _artifact(tmp_path / "missing.json", "missing", "json", required=False)
    malformed = _json_artifact(tmp_path, {"values": ["not-numeric"]}, "malformed")

    skipped = _evaluate(
        {
            "id": "missing",
            "type": "finite",
            "required": False,
            "source": _source(missing, pointer="/values"),
        },
        missing,
    )
    errored = _evaluate(
        {
            "id": "malformed",
            "type": "finite",
            "required": False,
            "source": _source(malformed, pointer="/values"),
        },
        malformed,
    )

    assert skipped.status is Status.SKIP
    assert errored.status is Status.ERROR


def test_one_check_error_does_not_abort_later_checks() -> None:
    results = evaluate_checks(
        [
            {"id": "bad", "type": "not-implemented"},
            {
                "id": "good",
                "type": "compare",
                "actual": {"value": 1.0, "unit": "1"},
                "expected": {"value": 1.0, "unit": "1"},
                "unit": "1",
                "metric": "absolute",
                "tolerance": 0.0,
            },
        ],
        {},
    )

    assert [result.status for result in results] == [Status.ERROR, Status.PASS]
    assert "KeyError" in results[0].summary


# Numeric helpers are part of the check engine's fail-closed boundary. --------


@pytest.mark.parametrize("value", [True, [True], "1", ["1"], [object()]])
def test_numeric_array_rejects_boolean_and_non_numeric_dtypes(value: Any) -> None:
    with pytest.raises(ArtifactError, match="numeric value required"):
        numeric_array(value)


def test_error_norms_are_exact_and_never_broadcast() -> None:
    actual = numeric_array([[1.0, -2.0], [3.0, -4.0]])
    expected = numeric_array([[0.0, 0.0], [0.0, 0.0]])

    assert error_norms(actual, expected) == {
        "l1": 10.0,
        "l2": pytest.approx(np.sqrt(30.0)),
        "linf": 4.0,
    }
    with pytest.raises(ArtifactError, match="requires identical shapes"):
        error_norms(numeric_array([[1.0], [2.0]]), numeric_array([1.0, 2.0]))
    with pytest.raises(ArtifactError, match="contain NaN or infinity"):
        error_norms(numeric_array([float("nan")]), numeric_array([0.0]))
