"""Complete evaluations own their floating-point error policy and restore it."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import base_case, dump_case
from netCDF4 import Dataset

from pvs.api import validate_case
from pvs.jsonutil import load_strict
from pvs.models import Status
from pvs.verify import verify_target


@pytest.mark.parametrize("mode", ["ignore", "raise", "warn", "call", "log", "print"])
def test_close_status_observations_and_settings_are_caller_independent(tmp_path, mode) -> None:
    case = base_case()
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    checks = []
    for name, actual, expected, relative in (
        ("zero", 1e-310, 1e-310, 1e-20),
        ("subnormal", 1e-300, 1e-300, 1e-10),
        ("fail", 2e-310, 1e-310, 1e-20),
        ("overflow", 1e308, 1e308, 2.0),
    ):
        checks.append({
            "id": name, "type": "compare", "metric": "close", "unit": "1",
            "actual": {"value": actual, "unit": "1"},
            "expected": {"value": expected, "unit": "1"},
            "absolute_tolerance": 0, "relative_tolerance": relative,
        })
    case["checks"] = checks
    path = dump_case(tmp_path / "pvs.yaml", case)
    original = np.geterr()
    original_callback = np.geterrcall()

    class Trap:
        def __call__(self, *_):
            pytest.fail("PVS invoked the caller's floating-point callback")

        def write(self, *_):
            pytest.fail("PVS wrote to the caller's floating-point log")

    trap = Trap()
    try:
        np.seterrcall(trap)
        with np.errstate(all="raise", under=mode):
            settings = np.geterr()
            outcome = validate_case(path, output_dir=tmp_path / "strict")
            assert verify_target(outcome.output_directory).valid
            assert np.geterr() == settings
            assert np.geterrcall() is trap
        with np.errstate(all="ignore"):
            control = validate_case(path, output_dir=tmp_path / "control")
    finally:
        np.seterrcall(original_callback)
    assert np.geterr() == original
    strict_checks = load_strict(outcome.evidence_path)["record"]["checks"]
    control_checks = load_strict(control.evidence_path)["record"]["checks"]
    assert strict_checks == control_checks
    assert [item["status"] for item in strict_checks] == ["PASS", "PASS", "FAIL", "ERROR"]
    assert strict_checks[0]["observed"]["permitted_error"] == 0
    assert strict_checks[1]["observed"]["permitted_error"] == 1e-310
    assert "closeness calculation overflowed" in strict_checks[3]["summary"]


@pytest.mark.parametrize("packed,scale,offset,expected,status", [
    (1e-310, 1e-20, 0.0, 0.0, Status.PASS),
    (1e-300, 1e-10, 0.0, 1e-310, Status.PASS),
    (1e-310, 1e-20, 2.0, 2.0, Status.PASS),
    (1e308, 2.0, 0.0, 0.0, Status.ERROR),
])
def test_netcdf_unpacking_has_local_policy(tmp_path: Path, packed, scale, offset, expected, status):
    # Disable netCDF's automatic packing: these are the exact stored bytes.
    with Dataset(tmp_path / "result.nc", "w") as dataset:
        dataset.createDimension("n", 1)
        variable = dataset.createVariable("value", "f8", ("n",))
        variable.set_auto_maskandscale(False)
        variable[:] = [packed]
        variable.scale_factor = scale
        variable.add_offset = offset
    case = base_case()
    case["artifacts"]["result"].update(path="result.nc", format="netcdf")
    case["checks"] = [{
        "id": "unpacked", "type": "compare", "metric": "linf", "tolerance": 0, "unit": "1",
        "actual": {
            "artifact": "result", "variable": "value", "unit": "1",
            "netcdf_decoding": "pvs-netcdf/1",
        },
        "expected": {"value": [expected], "unit": "1"},
    }]
    path = dump_case(tmp_path / "pvs.yaml", case)
    original = np.geterr()
    with np.errstate(all="raise"):
        settings = np.geterr()
        outcome = validate_case(path, output_dir=tmp_path / "strict")
        assert outcome.status is status
        assert verify_target(outcome.output_directory).valid
        assert np.geterr() == settings
    assert np.geterr() == original
    with np.errstate(all="ignore"):
        control = validate_case(path, output_dir=tmp_path / "control")
    assert load_strict(outcome.evidence_path)["record"]["checks"] == (
        load_strict(control.evidence_path)["record"]["checks"]
    )


def test_monotonic_overflow_is_explicit_and_restores_settings(tmp_path: Path) -> None:
    case = base_case()
    (tmp_path / "result.json").write_text("[-1e308, 1e308]", encoding="utf-8")
    case["checks"] = [{
        "id": "steps", "type": "monotonic", "direction": "increasing", "unit": "1",
        "absolute_tolerance": 0, "source": {"artifact": "result", "unit": "1"},
    }]
    path = dump_case(tmp_path / "pvs.yaml", case)
    with np.errstate(all="raise"):
        settings = np.geterr()
        outcome = validate_case(path, output_dir=tmp_path / "strict")
        assert outcome.status is Status.ERROR
        assert verify_target(outcome.output_directory).valid
        assert np.geterr() == settings
    check = load_strict(outcome.evidence_path)["record"]["checks"][0]
    assert check["summary"] == "monotonic differences overflowed the finite numeric range"
