"""JSON, CSV and NetCDF loading with common selectors and reductions."""

from __future__ import annotations

import csv
import math
import re
import stat
from typing import Any

import numpy as np

from ..artifacts import ResolvedArtifact
from ..constants import NETCDF_DECODING_PROFILE
from ..errors import ArtifactError, MissingArtifactError
from ..jsonutil import MAX_SAFE_INTEGER, load_strict
from ..numeric import numeric_array, source_array

_MISSING = object()


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ArtifactError(f"JSON pointer must be empty or start with '/': {pointer!r}")
    current = document
    for raw_token in pointer[1:].split("/"):
        if re.search(r"~(?:[^01]|$)", raw_token):
            raise ArtifactError(f"invalid JSON pointer escape in token: {raw_token!r}")
        token = raw_token.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(current, list):
                if re.fullmatch(r"0|[1-9][0-9]*", token) is None:
                    raise ValueError
                index = int(token)
                current = current[index]
            elif isinstance(current, dict):
                current = current[token]
            else:
                raise TypeError
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ArtifactError(f"JSON pointer token not found: {token!r} in {pointer!r}") from exc
    return current


def _csv_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return text
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return value


def _reduce(value: Any, operation: str | None) -> Any:
    if operation is None:
        return value
    if operation == "size":
        return int(source_array(value).size)
    numeric = numeric_array(value)
    if operation == "first":
        return numeric.reshape(-1)[0]
    if operation == "last":
        return numeric.reshape(-1)[-1]
    if not np.all(np.isfinite(numeric)):
        raise ArtifactError("numeric reduction contains NaN or infinity")
    if operation == "sum":
        try:
            return math.fsum(float(item) for item in numeric.reshape(-1))
        except OverflowError as exc:
            raise ArtifactError("numeric sum overflowed the finite range") from exc
    if operation == "mean":
        try:
            total = math.fsum(float(item) for item in numeric.reshape(-1))
        except OverflowError as exc:
            raise ArtifactError("numeric mean overflowed the finite range") from exc
        return total / numeric.size
    if operation == "min":
        return numeric.min()
    if operation == "max":
        return numeric.max()
    raise ArtifactError(f"unknown reduction: {operation}")


def _component(value: Any, component: Any) -> Any:
    if component is None:
        return value
    if isinstance(component, bool) or not isinstance(component, int) or component < 0:
        raise ArtifactError("component selector must be a non-negative integer")
    array = source_array(value)
    if array.ndim == 0:
        raise ArtifactError("component selector requires at least one array dimension")
    if component >= array.shape[-1]:
        raise ArtifactError(
            f"component selector {component} is out of bounds for final axis "
            f"of length {array.shape[-1]}"
        )
    return array[..., component]


def _attribute(variable: Any, name: str) -> Any:
    if name not in variable.ncattrs():
        return _MISSING
    return variable.getncattr(name)


def _unsigned_dtype(dtype: np.dtype[Any]) -> np.dtype[Any]:
    return np.dtype(dtype.str.replace("i", "u", 1))


def _packed_attribute(
    value: Any,
    *,
    name: str,
    storage_dtype: np.dtype[Any],
    packed_dtype: np.dtype[Any],
    unsigned: bool,
) -> np.ndarray[Any, Any]:
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"NetCDF {name} must be numeric: {exc}") from exc
    if array.dtype.kind not in "iuf":
        raise ArtifactError(f"NetCDF {name} must be numeric")
    if unsigned and array.dtype.kind == "i":
        info = np.iinfo(storage_dtype)
        if np.any(array < info.min) or np.any(array > info.max):
            raise ArtifactError(f"NetCDF {name} is outside the packed storage range")
        array = array.astype(storage_dtype).view(packed_dtype)
    if packed_dtype.kind in "iu":
        if array.dtype.kind == "f" and (
            np.any(~np.isfinite(array)) or np.any(array != np.trunc(array))
        ):
            raise ArtifactError(f"NetCDF {name} must contain integers for an integer variable")
        info = np.iinfo(packed_dtype)
        if np.any(array < info.min) or np.any(array > info.max):
            raise ArtifactError(f"NetCDF {name} is outside the packed storage range")
    return array


def _mask_equal(mask: np.ndarray[Any, Any], raw: np.ndarray[Any, Any], value: Any) -> None:
    try:
        scalar = value.item() if isinstance(value, np.generic) else value
        if isinstance(scalar, (float, np.floating)) and math.isnan(float(scalar)):
            mask |= np.isnan(raw)
        else:
            mask |= raw == scalar
    except (TypeError, ValueError, OverflowError) as exc:
        raise ArtifactError(f"NetCDF masking attribute cannot be compared to data: {exc}") from exc


def _numeric_scalar_attribute(variable: Any, name: str, default: float) -> float:
    value = _attribute(variable, name)
    if value is _MISSING:
        return default
    array = np.asarray(value)
    if array.size != 1 or array.dtype.kind not in "iuf":
        raise ArtifactError(f"NetCDF {name} must be one numeric scalar")
    try:
        # Packing metadata is a numeric input too. Preserve integer admission
        # checks before binary64 conversion, just as for the variable values.
        result = float(numeric_array(array).reshape(-1)[0])
    except ArtifactError as exc:
        raise ArtifactError(f"NetCDF {name}: {exc}") from exc
    if not math.isfinite(result):
        raise ArtifactError(f"NetCDF {name} must be finite")
    return result


def _decode_netcdf_variable(variable: Any, netcdf4: Any) -> np.ndarray[Any, Any]:
    """Decode one variable using the normative ``pvs-netcdf/1`` profile."""

    variable.set_auto_maskandscale(False)
    raw = np.asarray(variable[:])
    storage_dtype = raw.dtype
    if storage_dtype.kind not in "iuf":
        raise ArtifactError(f"NetCDF variable must be real numeric, got dtype {storage_dtype}")

    unsigned_attribute = _attribute(variable, "_Unsigned")
    unsigned = False
    if unsigned_attribute is not _MISSING:
        if not isinstance(unsigned_attribute, str) or unsigned_attribute.lower() not in {
            "true",
            "false",
        }:
            raise ArtifactError("NetCDF _Unsigned must be the string 'true' or 'false'")
        unsigned = unsigned_attribute.lower() == "true"
        if unsigned and storage_dtype.kind != "i":
            raise ArtifactError("NetCDF _Unsigned='true' requires a signed integer variable")

    packed_dtype = _unsigned_dtype(storage_dtype) if unsigned else storage_dtype
    packed = raw.view(packed_dtype) if unsigned else raw
    mask = np.zeros(packed.shape, dtype=np.bool_)

    fill_value = _attribute(variable, "_FillValue")
    if fill_value is _MISSING:
        fill_key = f"{storage_dtype.kind}{storage_dtype.itemsize}"
        fill_value = netcdf4.default_fillvals.get(fill_key, _MISSING)
    if fill_value is not _MISSING:
        fill_values = _packed_attribute(
            fill_value,
            name="_FillValue",
            storage_dtype=storage_dtype,
            packed_dtype=packed_dtype,
            unsigned=unsigned,
        )
        if fill_values.size != 1:
            raise ArtifactError("NetCDF _FillValue must be one numeric scalar")
        _mask_equal(mask, packed, fill_values.reshape(-1)[0])

    missing_value = _attribute(variable, "missing_value")
    if missing_value is not _MISSING:
        missing_values = _packed_attribute(
            missing_value,
            name="missing_value",
            storage_dtype=storage_dtype,
            packed_dtype=packed_dtype,
            unsigned=unsigned,
        )
        for value in missing_values.reshape(-1):
            _mask_equal(mask, packed, value)

    valid_range = _attribute(variable, "valid_range")
    valid_min = _attribute(variable, "valid_min")
    valid_max = _attribute(variable, "valid_max")
    if valid_range is not _MISSING and (valid_min is not _MISSING or valid_max is not _MISSING):
        raise ArtifactError("NetCDF valid_range cannot be combined with valid_min or valid_max")
    lower: Any = _MISSING
    upper: Any = _MISSING
    if valid_range is not _MISSING:
        bounds = _packed_attribute(
            valid_range,
            name="valid_range",
            storage_dtype=storage_dtype,
            packed_dtype=packed_dtype,
            unsigned=unsigned,
        ).reshape(-1)
        if bounds.size != 2:
            raise ArtifactError("NetCDF valid_range must contain exactly two values")
        if np.any(~np.isfinite(bounds)):
            raise ArtifactError("NetCDF valid_range must contain finite values")
        lower, upper = bounds
    else:
        if valid_min is not _MISSING:
            values = _packed_attribute(
                valid_min,
                name="valid_min",
                storage_dtype=storage_dtype,
                packed_dtype=packed_dtype,
                unsigned=unsigned,
            ).reshape(-1)
            if values.size != 1:
                raise ArtifactError("NetCDF valid_min must be one numeric scalar")
            if np.any(~np.isfinite(values)):
                raise ArtifactError("NetCDF valid_min must be finite")
            lower = values[0]
        if valid_max is not _MISSING:
            values = _packed_attribute(
                valid_max,
                name="valid_max",
                storage_dtype=storage_dtype,
                packed_dtype=packed_dtype,
                unsigned=unsigned,
            ).reshape(-1)
            if values.size != 1:
                raise ArtifactError("NetCDF valid_max must be one numeric scalar")
            if np.any(~np.isfinite(values)):
                raise ArtifactError("NetCDF valid_max must be finite")
            upper = values[0]
    if lower is not _MISSING and upper is not _MISSING and lower > upper:
        raise ArtifactError("NetCDF valid range lower bound exceeds upper bound")
    if lower is not _MISSING:
        mask |= packed < lower
    if upper is not _MISSING:
        mask |= packed > upper

    if packed_dtype.kind in "iu" and np.any(~mask):
        unmasked = packed[~mask]
        if np.any(unmasked > MAX_SAFE_INTEGER) or (
            packed_dtype.kind == "i" and np.any(unmasked < -MAX_SAFE_INTEGER)
        ):
            raise ArtifactError("NetCDF integer value is outside the exact binary64 range")

    scale_factor = _numeric_scalar_attribute(variable, "scale_factor", 1.0)
    add_offset = _numeric_scalar_attribute(variable, "add_offset", 0.0)
    safe_packed = np.where(mask, 0, packed)
    try:
        # Preserve separate binary64 multiply/add rounding. Underflow to a
        # subnormal or zero is admissible; overflow is diagnosed below.
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            decoded = np.asarray(safe_packed, dtype=np.float64) * scale_factor + add_offset
    except (TypeError, ValueError, OverflowError) as exc:
        raise ArtifactError(f"NetCDF unpacking failed: {exc}") from exc
    packed_finite = np.isfinite(packed)
    if np.any(~np.isfinite(decoded[~mask & packed_finite])):
        raise ArtifactError("NetCDF scale_factor/add_offset unpacking overflowed")
    return np.asarray(np.where(mask, np.nan, decoded), dtype=np.float64)


def _require_artifact_file(artifact: ResolvedArtifact) -> None:
    if artifact.resolution_error is not None:
        raise ArtifactError(artifact.resolution_error)
    path = artifact.path
    try:
        metadata = path.stat()
    except FileNotFoundError as exc:
        raise MissingArtifactError(artifact.id, str(path)) from exc
    except OSError as exc:
        raise ArtifactError(f"could not access artifact {artifact.id}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ArtifactError(f"artifact is not a regular file: {artifact.id} ({path})")


class ArtifactReader:
    """Lazy per-run reader cache for declared artefacts."""

    def __init__(self, artifacts: dict[str, ResolvedArtifact]) -> None:
        self._artifacts = artifacts
        self._cache: dict[str, Any] = {}
        self._csv_headers: dict[str, frozenset[str]] = {}

    def artifact(self, artifact_id: str) -> ResolvedArtifact:
        try:
            return self._artifacts[artifact_id]
        except KeyError as exc:
            raise ArtifactError(f"unknown artifact: {artifact_id}") from exc

    def read(self, artifact_id: str) -> Any:
        if artifact_id in self._cache:
            return self._cache[artifact_id]
        artifact = self.artifact(artifact_id)
        _require_artifact_file(artifact)
        try:
            if artifact.format == "json":
                value = load_strict(artifact.path)
            elif artifact.format == "csv":
                with artifact.path.open("r", encoding="utf-8", newline="") as stream:
                    reader = csv.DictReader(
                        stream, delimiter=",", quotechar='"', doublequote=True,
                        escapechar=None, skipinitialspace=False, quoting=csv.QUOTE_MINIMAL,
                        strict=True,
                    )
                    headers = reader.fieldnames
                    if (
                        not headers
                        or any(header is None or header == "" for header in headers)
                        or len(headers) != len(set(headers))
                    ):
                        raise ArtifactError("CSV headers must be present, non-empty and unique")
                    value = list(reader)
                    if any(
                        None in row or any(cell is None for cell in row.values()) for row in value
                    ):
                        raise ArtifactError("CSV rows must contain exactly one field per header")
                    self._csv_headers[artifact_id] = frozenset(headers)
            elif artifact.format == "netcdf":
                value = None
            elif artifact.format == "text":
                value = artifact.path.read_text(encoding="utf-8")
            elif artifact.format == "binary":
                value = artifact.path.read_bytes()
            else:
                raise ArtifactError(f"unsupported artifact format: {artifact.format}")
        except ArtifactError:
            raise
        except (OSError, UnicodeError, ValueError, csv.Error) as exc:
            raise ArtifactError(f"could not read artifact {artifact_id}: {exc}") from exc
        self._cache[artifact_id] = value
        return value

    def select(self, source: dict[str, Any]) -> Any:
        artifact = self.artifact(str(source["artifact"]))
        if artifact.format == "netcdf":
            _require_artifact_file(artifact)
            profile = source.get("netcdf_decoding")
            if profile != NETCDF_DECODING_PROFILE:
                raise ArtifactError(
                    f"NetCDF source requires netcdf_decoding: {NETCDF_DECODING_PROFILE}"
                )
            value = self._netcdf_variable(
                artifact,
                source.get("variable"),
                source.get("unit"),
                verify_unit=source.get("reduce") != "size",
            )
        else:
            value = self.read(artifact.id)
            if "pointer" in source:
                if artifact.format != "json":
                    raise ArtifactError("pointer selector is only valid for JSON artifacts")
                value = _json_pointer(value, str(source["pointer"]))
            if "column" in source:
                if artifact.format != "csv":
                    raise ArtifactError("column selector is only valid for CSV artifacts")
                column = str(source["column"])
                if column not in self._csv_headers[artifact.id]:
                    raise ArtifactError(f"CSV column not found: {column}")
                try:
                    value = [_csv_scalar(row[column]) for row in value]
                except KeyError as exc:
                    raise ArtifactError(f"CSV column not found: {column}") from exc
            if "variable" in source:
                raise ArtifactError("variable selector is only valid for NetCDF artifacts")
            if "netcdf_decoding" in source:
                raise ArtifactError("netcdf_decoding selector is only valid for NetCDF artifacts")
        value = _component(value, source.get("component"))
        return _reduce(value, source.get("reduce"))

    @staticmethod
    def _netcdf_variable(
        artifact: ResolvedArtifact,
        variable: Any,
        declared_unit: Any,
        *,
        verify_unit: bool,
    ) -> Any:
        if not variable:
            raise ArtifactError("NetCDF source requires a variable selector")
        if not isinstance(declared_unit, str) or not declared_unit:
            raise ArtifactError("NetCDF source requires an explicit unit")
        try:
            import netCDF4
        except ImportError as exc:
            raise ArtifactError(
                "NetCDF support requires the 'netcdf' extra; install "
                "physics-validation-suite[netcdf]"
            ) from exc
        try:
            with netCDF4.Dataset(artifact.path, "r") as dataset:
                if variable not in dataset.variables:
                    raise ArtifactError(f"NetCDF variable not found: {variable}")
                selected = dataset.variables[str(variable)]
                units = _attribute(selected, "units")
                if verify_unit and units is not _MISSING:
                    if not isinstance(units, str):
                        raise ArtifactError("NetCDF variable units attribute must be a string")
                    if units != declared_unit:
                        raise ArtifactError(
                            f"NetCDF variable unit {units!r} does not exactly match "
                            f"declared unit {declared_unit!r}"
                        )
                return _decode_netcdf_variable(selected, netCDF4)
        except ArtifactError:
            raise
        except Exception as exc:
            raise ArtifactError(f"could not read NetCDF artifact {artifact.id}: {exc}") from exc
