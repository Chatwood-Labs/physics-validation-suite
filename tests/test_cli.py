from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from pvs.cli import main


def test_readme_quickstart_produces_and_verifies_a_complete_package(tmp_path, capsys):
    case = Path(__file__).resolve().parents[1] / "examples/quickstart"
    output = tmp_path / "quickstart-evidence"
    assert main(["validate", str(case), "--output", str(output)]) == 0
    assert capsys.readouterr().out.startswith("PVS PASS\n")
    assert all((output / name).is_file() for name in ("evidence.json", "manifest.json",
                                                      "report.html", "report.pdf"))
    assert main(["verify", str(output), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is True
    assert result["evidence_status"] == "PASS"
    assert result["trust"] == "unpinned"


def _data_for_status(status: str) -> dict:
    from conftest import base_case

    data = base_case()
    if status == "PASS":
        data["checks"] = [
            {
                "id": "in-range",
                "type": "range",
                "source": {"artifact": "result", "pointer": "/value", "unit": "1"},
                "unit": "1",
                "minimum": 0.0,
                "maximum": 1.0,
            }
        ]
    elif status in {"FAIL", "WARN"}:
        data["checks"] = [
            {
                "id": "out-of-range",
                "type": "range",
                "source": {"artifact": "result", "pointer": "/value", "unit": "1"},
                "unit": "1",
                "maximum": 0.5,
                "required": status == "FAIL",
            }
        ]
    elif status == "ERROR":
        data["checks"] = [
            {
                "id": "bad-pointer",
                "type": "finite",
                "source": {
                    "artifact": "result",
                    "pointer": "/not-present",
                    "unit": "1",
                },
                "unit": "1",
            }
        ]
    else:  # pragma: no cover - test helper guard
        raise AssertionError(status)
    return data


@pytest.mark.parametrize(
    ("status", "fail_on_warn", "expected_code"),
    [
        ("PASS", False, 0),
        ("FAIL", False, 1),
        ("WARN", False, 0),
        ("WARN", True, 1),
        ("ERROR", False, 3),
    ],
)
def test_validate_exit_code_contract(
    case_factory,
    tmp_path: Path,
    capsys,
    status: str,
    fail_on_warn: bool,
    expected_code: int,
) -> None:
    case_path, _ = case_factory(_data_for_status(status), result_text='{ "value": 1.0 }\n')
    output = tmp_path / f"output-{status}-{fail_on_warn}"
    argv = ["validate", str(case_path), "-o", str(output), "--no-html", "--no-pdf"]
    if fail_on_warn:
        argv.append("--fail-on-warn")

    code = main(argv)
    captured = capsys.readouterr()

    assert code == expected_code
    assert captured.err == ""
    assert captured.out.startswith(f"PVS {status}\n")
    assert (output / "evidence.json").is_file()
    assert (output / "manifest.json").is_file()
    assert not (output / "report.html").exists()
    assert not (output / "report.pdf").exists()


@pytest.mark.skipif(
    os.name != "posix", reason="PVS run mode requires POSIX process containment"
)
def test_run_nonzero_exit_maps_to_runtime_error_code(case_factory, tmp_path: Path, capsys) -> None:
    from conftest import base_case

    data = base_case()
    data["execution"] = {
        "command": [sys.executable, "-c", "import sys; sys.exit(9)"],
        "expected_exit_codes": [0],
    }
    case_path, _ = case_factory(data)

    code = main(
        [
            "run",
            str(case_path),
            "-o",
            str(tmp_path / "nonzero-output"),
            "--no-html",
            "--no-pdf",
        ]
    )
    captured = capsys.readouterr()

    assert code == 3
    assert captured.err == ""
    assert captured.out.startswith("PVS ERROR\n")


def test_run_without_execution_is_case_error_code_2(case_factory, tmp_path: Path, capsys) -> None:
    case_path, _ = case_factory()

    code = main(["run", str(case_path), "-o", str(tmp_path / "output")])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "pvs run requires an execution declaration" in captured.err


def test_case_schema_error_is_exit_2_and_json_mode_is_one_stdout_object(
    tmp_path: Path, capsys
) -> None:
    case = tmp_path / "pvs.yaml"
    case.write_text("schema: pvs-case/1\nid: incomplete\n", encoding="utf-8")

    code = main(["validate", str(case), "--json"])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert value["status"] == "ERROR"
    assert value["kind"] == "case"


def test_success_json_mode_is_one_machine_readable_stdout_object(
    case_factory, tmp_path: Path, capsys
) -> None:
    case_path, _ = case_factory(_data_for_status("PASS"))

    code = main(
        [
            "validate",
            str(case_path),
            "-o",
            str(tmp_path / "json-output"),
            "--no-html",
            "--no-pdf",
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    value = json.loads(lines[0])
    assert value["status"] == "PASS"
    assert value["evidence_id"].startswith("pvs:sha256:")
    assert value["finding_id"].startswith("pvs-finding:v1:sha256:")
    assert value["package_id"].startswith("pvs-package:sha256:")


def test_inspect_case_human_and_json_modes(case_factory, capsys) -> None:
    case_path, data = case_factory()

    assert main(["inspect", str(case_path)]) == 0
    human = capsys.readouterr()
    assert human.err == ""
    assert human.out.startswith("PVS CASE\n")
    assert data["id"] in human.out

    assert main(["inspect", str(case_path), "--json"]) == 0
    machine = capsys.readouterr()
    value = json.loads(machine.out)
    assert value["kind"] == "case"
    assert value["case_id"] == data["id"]
    assert value["artifacts"] == ["result"]
    assert value["checks"] == ["result-exists"]


def test_inspect_evidence_directory(package_factory, capsys) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)

    code = main(["inspect", str(package), "--json"])
    captured = capsys.readouterr()
    value = json.loads(captured.out)

    assert code == 0
    assert captured.err == ""
    assert value["kind"] == "evidence"
    assert value["evidence_id"] == outcome.evidence_id
    assert value["status"] == "PASS"


def test_verify_exit_codes_and_json_output(package_factory, capsys) -> None:
    outcome, package = package_factory(html=False, pdf=False, embed=False)

    valid_code = main(
        [
            "verify",
            str(package),
            "--expect-evidence-id",
            outcome.evidence_id,
            "--expect-package-id",
            outcome.package_id,
            "--json",
        ]
    )
    valid_capture = capsys.readouterr()
    valid_value = json.loads(valid_capture.out)
    assert valid_code == 0
    assert valid_capture.err == ""
    assert valid_value["valid"] is True

    (package / "evidence.json").write_bytes((package / "evidence.json").read_bytes() + b"X")
    invalid_code = main(["verify", str(package), "--json"])
    invalid_capture = capsys.readouterr()
    invalid_value = json.loads(invalid_capture.out)
    assert invalid_code == 4
    assert invalid_capture.err == ""
    assert invalid_value["valid"] is False


def test_existing_output_directory_is_case_error_exit_2(
    case_factory, tmp_path: Path, capsys
) -> None:
    case_path, _ = case_factory()
    output = tmp_path / "already-exists"
    output.mkdir()

    code = main(["validate", str(case_path), "-o", str(output)])
    captured = capsys.readouterr()

    assert code == 2
    assert "output directory already exists" in captured.err


def test_argparse_usage_error_returns_2(capsys) -> None:
    code = main([])

    captured = capsys.readouterr()
    assert code == 2
    assert "usage: pvs" in captured.err


def test_argparse_usage_error_is_machine_readable_with_json(capsys) -> None:
    code = main(["validate", "--json"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.err == ""
    value = json.loads(captured.out)
    assert value["status"] == "ERROR"
    assert value["kind"] == "usage"
    assert "case" in value["message"]


def test_version_option_exits_zero(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])

    captured = capsys.readouterr()
    assert caught.value.code == 0
    assert captured.out.startswith("PVS 1.0.0a1")
