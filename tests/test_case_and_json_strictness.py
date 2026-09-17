from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from pvs.artifacts import resolve_artifacts
from pvs.case import load_case
from pvs.errors import ArtifactError, CaseError
from pvs.jsonutil import loads_strict


def _write_case(root: Path, data: dict, filename: str = "pvs.yaml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_loads_minimal_case_from_file_and_directory(case_factory) -> None:
    path, original = case_factory()

    from_file = load_case(path)
    from_directory = load_case(path.parent)

    assert from_file.path == path.resolve()
    assert from_directory == from_file
    assert from_file.data == original
    assert from_file.id == original["id"]
    assert from_file.version == original["version"]
    assert len(from_file.raw_identity["sha256"]) == 64
    assert len(from_file.semantic_sha256) == 64


def test_v1_case_is_verification_only_but_loads_with_explicit_legacy_dispatch(
    tmp_path: Path,
    minimal_case,
) -> None:
    minimal_case["schema"] = "pvs-case/1"
    path = _write_case(tmp_path, minimal_case)

    with pytest.raises(CaseError, match="verification-only"):
        load_case(path)

    legacy = load_case(path, allow_legacy=True)
    assert legacy.data["schema"] == "pvs-case/1"


def test_frozen_v1_schema_retains_its_historical_known_field_footgun(
    tmp_path: Path,
    minimal_case,
) -> None:
    minimal_case["schema"] = "pvs-case/1"
    minimal_case["checks"][0].update(
        {
            "metric": "close",
            "absolute_tolerance": 1.0,
            "relative_tolerance": 1.0,
            "terms": [
                {"value": 1.0, "coefficient": 1.0},
                {"value": 1.0, "coefficient": -1.0},
            ],
        }
    )

    legacy = load_case(_write_case(tmp_path, minimal_case), allow_legacy=True)

    assert legacy.data["checks"][0]["metric"] == "close"


def test_yaml_semantic_identity_ignores_mapping_order(tmp_path: Path, minimal_case) -> None:
    left = _write_case(tmp_path / "left", minimal_case)
    reordered = dict(reversed(list(minimal_case.items())))
    right = _write_case(tmp_path / "right", reordered)

    left_case = load_case(left)
    right_case = load_case(right)

    assert left_case.semantic_sha256 == right_case.semantic_sha256
    assert left_case.raw_identity["sha256"] != right_case.raw_identity["sha256"]


def test_duplicate_yaml_mapping_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pvs.yaml"
    path.write_text(
        """schema: pvs-case/2
id: duplicate-key
id: silently-overwritten-without-strict-loading
version: 1.0.0
title: Duplicate
classifications: [verification]
subject: {name: subject, version: 1.0.0}
artifacts: {result: {path: result.json, role: output, format: json}}
checks: [{id: exists, type: exists, artifact: result}]
""",
        encoding="utf-8",
    )

    with pytest.raises(CaseError, match="duplicate YAML mapping key: 'id'"):
        load_case(path)


def test_duplicate_nested_yaml_mapping_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pvs.yaml"
    path.write_text(
        """schema: pvs-case/2
id: duplicate-key
version: 1.0.0
title: Duplicate
classifications: [verification]
subject:
  name: first
  name: second
  version: 1.0.0
artifacts: {result: {path: result.json, role: output, format: json}}
checks: [{id: exists, type: exists, artifact: result}]
""",
        encoding="utf-8",
    )

    with pytest.raises(CaseError, match="duplicate YAML mapping key: 'name'"):
        load_case(path)


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        (
            "subject: &subject {name: subject, version: 1.0.0}\nmetadata: {copy: *subject}\n",
            "YAML aliases are not allowed",
        ),
        ("1: non-string-key\n", "YAML mapping keys must be strings"),
        ("metadata: {not_finite: .nan}\n", "non-finite YAML numbers are not allowed"),
        ("metadata: {not_finite: .inf}\n", "non-finite YAML numbers are not allowed"),
        (
            "metadata: {too_large: 9007199254740992}\n",
            "integer is outside the RFC 8785 interoperable range",
        ),
        ("metadata: {implicit_date: 2026-08-28}\n", "unsupported YAML value type date"),
    ],
)
def test_yaml_is_restricted_to_unaliased_i_json_values(
    tmp_path: Path,
    minimal_case,
    fragment: str,
    message: str,
) -> None:
    base = yaml.safe_dump(minimal_case, sort_keys=False)
    if fragment.startswith("subject:"):
        start = base.index("subject:")
        end = base.index("artifacts:")
        base = base[:start] + fragment + base[end:]
    else:
        base += fragment
    path = tmp_path / "pvs.yaml"
    path.write_text(base, encoding="utf-8")

    with pytest.raises(CaseError, match=message):
        load_case(path)


def test_yaml_mapping_key_with_unpaired_unicode_surrogate_is_rejected(
    tmp_path: Path,
    minimal_case,
) -> None:
    path = tmp_path / "pvs.yaml"
    path.write_text(
        yaml.safe_dump(minimal_case, sort_keys=False) + 'metadata:\n  "\\uD800": hostile-key\n',
        encoding="utf-8",
    )

    with pytest.raises(CaseError, match="unpaired Unicode surrogate"):
        load_case(path)


@pytest.mark.parametrize(
    "document",
    [
        "- not\n- a\n- mapping\n",
        "null\n",
        "!!python/object/apply:os.system ['echo unsafe']\n",
        "schema: !unrecognised pvs-case/2\n",
    ],
)
def test_non_mapping_and_unsafe_yaml_constructs_are_rejected(tmp_path: Path, document: str) -> None:
    path = tmp_path / "pvs.yaml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(CaseError):
        load_case(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda case: case.update({"unexpected": True}), "Additional properties"),
        (lambda case: case["subject"].update({"unexpected": True}), "Additional properties"),
        (
            lambda case: case["checks"][0].update({"unexpected": True}),
            "not valid under any of the given schemas",
        ),
        (lambda case: case.pop("title"), "'title' is a required property"),
        (lambda case: case.update({"schema": "pvs-case/999"}), "was expected"),
        (lambda case: case.update({"id": "UPPER CASE"}), "does not match"),
        (lambda case: case.update({"classifications": []}), "should be non-empty"),
        (lambda case: case["execution"].update({"command": "echo unsafe"}), "not of type 'array'"),
    ],
)
def test_case_schema_is_strict(
    tmp_path: Path,
    minimal_case,
    mutation,
    message: str,
) -> None:
    case = copy.deepcopy(minimal_case)
    if "execution" not in case:
        case["execution"] = {"command": ["true"]}
    mutation(case)
    path = _write_case(tmp_path, case)

    with pytest.raises(CaseError, match=message):
        load_case(path)


def _unit_source() -> dict[str, object]:
    return {"artifact": "result", "pointer": "/value", "unit": "1"}


def _unit_literal(value: object = 1.0) -> dict[str, object]:
    return {"value": value, "unit": "1"}


@pytest.mark.parametrize(
    "check",
    [
        {
            "id": "exists",
            "type": "exists",
            "artifact": "result",
            "metric": "close",
            "absolute_tolerance": 1.0,
            "relative_tolerance": 1.0,
            "terms": [
                {"value": {"value": 1.0, "unit": "1"}, "coefficient": 1.0},
                {"value": {"value": 1.0, "unit": "1"}, "coefficient": -1.0},
            ],
        },
        {
            "id": "schema",
            "type": "schema",
            "artifact": "result",
            "schema_artifact": "result",
            "tolerance": 0.0,
        },
        {
            "id": "finite",
            "type": "finite",
            "source": _unit_source(),
            "unit": "1",
            "minimum": 0.0,
        },
        {
            "id": "range",
            "type": "range",
            "source": _unit_source(),
            "unit": "1",
            "minimum": 0.0,
            "metric": "absolute",
        },
        {
            "id": "monotonic",
            "type": "monotonic",
            "source": _unit_source(),
            "unit": "1",
            "direction": "increasing",
            "absolute_tolerance": 0.0,
            "maximum": 1.0,
        },
        {
            "id": "compare",
            "type": "compare",
            "actual": _unit_literal(),
            "expected": _unit_literal(),
            "unit": "1",
            "metric": "absolute",
            "tolerance": 0.0,
            "reference_id": "irrelevant",
        },
        {
            "id": "reference",
            "type": "reference",
            "reference_id": "ref",
            "actual": _unit_literal(),
            "expected": _unit_literal(),
            "unit": "1",
            "metric": "absolute",
            "tolerance": 0.0,
            "source": _unit_source(),
        },
        {
            "id": "conservation",
            "type": "conservation",
            "terms": [
                {"value": _unit_literal(), "coefficient": 1.0},
                {"value": _unit_literal(), "coefficient": -1.0},
            ],
            "expected": _unit_literal(0.0),
            "unit": "1",
            "absolute_tolerance": 0.0,
            "relative_tolerance": 0.0,
            "metric": "close",
        },
    ],
)
def test_each_check_type_rejects_known_but_irrelevant_fields(
    tmp_path: Path,
    minimal_case,
    check: dict[str, object],
) -> None:
    minimal_case["checks"] = [check]
    minimal_case["references"] = [
        {"id": "ref", "type": "analytic", "citation": "citation", "locator": "locator"}
    ]

    with pytest.raises(CaseError, match="case does not satisfy pvs-case/2"):
        load_case(_write_case(tmp_path, minimal_case))


@pytest.mark.parametrize(
    ("metric", "parameters", "irrelevant"),
    [
        ("absolute", {"tolerance": 0.0}, {"scale_floor": 1.0}),
        ("relative", {"tolerance": 0.0, "scale_floor": 1.0}, {"absolute_tolerance": 0.0}),
        ("l1", {"tolerance": 0.0}, {"relative_tolerance": 0.0}),
        ("l2", {"tolerance": 0.0}, {"scale_floor": 1.0}),
        ("linf", {"tolerance": 0.0}, {"absolute_tolerance": 0.0}),
        (
            "close",
            {"absolute_tolerance": 0.0, "relative_tolerance": 0.0},
            {"tolerance": 0.0},
        ),
    ],
)
def test_each_metric_variant_rejects_fields_from_other_metrics(
    tmp_path: Path,
    minimal_case,
    metric: str,
    parameters: dict[str, float],
    irrelevant: dict[str, float],
) -> None:
    minimal_case["checks"] = [
        {
            "id": "metric",
            "type": "compare",
            "actual": _unit_literal(),
            "expected": _unit_literal(),
            "unit": "1",
            "metric": metric,
            **parameters,
            **irrelevant,
        }
    ]

    with pytest.raises(CaseError):
        load_case(_write_case(tmp_path, minimal_case))


@pytest.mark.parametrize(
    ("metric", "parameters"),
    [
        ("absolute", {"tolerance": 0.0}),
        ("relative", {"tolerance": 0.0, "scale_floor": 1.0}),
        ("l1", {"tolerance": 0.0}),
        ("l2", {"tolerance": 0.0}),
        ("linf", {"tolerance": 0.0}),
        ("close", {"absolute_tolerance": 0.0, "relative_tolerance": 0.0}),
    ],
)
def test_each_closed_metric_variant_accepts_its_exact_shape(
    tmp_path: Path,
    minimal_case,
    metric: str,
    parameters: dict[str, float],
) -> None:
    minimal_case["checks"] = [
        {
            "id": "metric",
            "type": "compare",
            "actual": _unit_literal([1.0, 2.0]),
            "expected": _unit_literal([1.0, 2.0]),
            "unit": "1",
            "metric": metric,
            **parameters,
        }
    ]

    assert load_case(_write_case(tmp_path, minimal_case)).data["checks"][0]["metric"] == metric


@pytest.mark.parametrize(
    "mutation",
    [
        lambda check: check.pop("unit"),
        lambda check: check["source"].pop("unit"),
        lambda check: check.update({"unit": ""}),
        lambda check: check.update({"unit": " m"}),
        lambda check: check.update({"unit": "m "}),
        lambda check: check.update({"unit": "m\tkg"}),
        lambda check: check.update({"unit": "m\nkg"}),
    ],
)
def test_numeric_source_checks_require_explicit_well_formed_units(
    tmp_path: Path,
    minimal_case,
    mutation,
) -> None:
    check = {"id": "finite", "type": "finite", "source": _unit_source(), "unit": "1"}
    mutation(check)
    minimal_case["checks"] = [check]

    with pytest.raises(CaseError):
        load_case(_write_case(tmp_path, minimal_case))


@pytest.mark.parametrize("missing", ["unit", "actual.unit", "expected.unit"])
def test_comparison_requires_units_on_check_and_both_operands(
    tmp_path: Path,
    minimal_case,
    missing: str,
) -> None:
    check = {
        "id": "compare",
        "type": "compare",
        "actual": _unit_literal(),
        "expected": _unit_literal(),
        "unit": "1",
        "metric": "absolute",
        "tolerance": 0.0,
    }
    if "." in missing:
        operand, key = missing.split(".")
        check[operand].pop(key)
    else:
        check.pop(missing)
    minimal_case["checks"] = [check]

    with pytest.raises(CaseError):
        load_case(_write_case(tmp_path, minimal_case))


def test_raw_numeric_literals_are_rejected_in_v2(tmp_path: Path, minimal_case) -> None:
    minimal_case["checks"] = [
        {
            "id": "compare",
            "type": "compare",
            "actual": _unit_literal(),
            "expected": 1.0,
            "unit": "1",
            "metric": "absolute",
            "tolerance": 0.0,
        }
    ]

    with pytest.raises(CaseError):
        load_case(_write_case(tmp_path, minimal_case))


@pytest.mark.parametrize(
    "source",
    [
        {"artifact": "result", "variable": "u", "unit": "1"},
        {
            "artifact": "result",
            "variable": "u",
            "unit": "1",
            "netcdf_decoding": "library-defaults",
        },
    ],
)
def test_netcdf_selector_requires_the_named_decoding_profile(
    tmp_path: Path,
    minimal_case,
    source: dict[str, object],
) -> None:
    minimal_case["artifacts"]["result"]["format"] = "netcdf"
    minimal_case["artifacts"]["result"]["path"] = "result.nc"
    minimal_case["checks"] = [{"id": "finite", "type": "finite", "source": source, "unit": "1"}]

    with pytest.raises(CaseError):
        load_case(_write_case(tmp_path, minimal_case))


def test_size_reduction_requires_dimensionless_unit(tmp_path: Path, minimal_case) -> None:
    minimal_case["checks"] = [
        {
            "id": "size",
            "type": "finite",
            "source": {
                "artifact": "result",
                "pointer": "/value",
                "reduce": "size",
                "unit": "m",
            },
            "unit": "m",
        }
    ]

    with pytest.raises(CaseError):
        load_case(_write_case(tmp_path, minimal_case))


def test_component_selector_rejects_integral_float_in_yaml(
    tmp_path: Path,
    minimal_case,
) -> None:
    minimal_case["checks"] = [
        {
            "id": "component",
            "type": "finite",
            "source": {
                "artifact": "result",
                "pointer": "/value",
                "component": 1.0,
                "unit": "1",
            },
            "unit": "1",
        }
    ]

    with pytest.raises(CaseError, match="component selector must be an integer"):
        load_case(_write_case(tmp_path, minimal_case))


def test_yaml_11_boolean_is_not_silently_accepted_as_a_string(tmp_path: Path, minimal_case) -> None:
    text = yaml.safe_dump(minimal_case, sort_keys=False).replace(
        "version: 1.0.0", "version: yes", 1
    )
    path = tmp_path / "pvs.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(CaseError, match="not of type 'string'"):
        load_case(path)


def test_reference_artifact_requires_frozen_sha256(tmp_path: Path, minimal_case) -> None:
    minimal_case["artifacts"]["reference"] = {
        "path": "reference.json",
        "role": "reference",
        "format": "json",
    }
    path = _write_case(tmp_path, minimal_case)

    with pytest.raises(CaseError, match="must declare expected_sha256"):
        load_case(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda case: case["checks"].append(copy.deepcopy(case["checks"][0])),
            "check IDs must be unique",
        ),
        (
            lambda case: case.update(
                {
                    "references": [
                        {
                            "id": "wrong-role",
                            "type": "published",
                            "citation": "citation",
                            "locator": "locator",
                            "artifact": "result",
                        }
                    ]
                }
            ),
            "whose role is not 'reference'",
        ),
        (
            lambda case: case.update(
                {
                    "references": [
                        {
                            "id": "same",
                            "type": "analytic",
                            "citation": "one",
                            "locator": "one",
                        },
                        {
                            "id": "same",
                            "type": "analytic",
                            "citation": "two",
                            "locator": "two",
                        },
                    ]
                }
            ),
            "reference IDs must be unique",
        ),
        (
            lambda case: case["checks"][0].update({"artifact": "unknown"}),
            "names unknown artifact 'unknown'",
        ),
        (
            lambda case: case["checks"].__setitem__(
                0,
                {
                    "id": "finite",
                    "type": "finite",
                    "source": {
                        "artifact": "unknown",
                        "pointer": "/value",
                        "unit": "1",
                    },
                    "unit": "1",
                },
            ),
            "names unknown artifact 'unknown'",
        ),
        (
            lambda case: case["checks"].__setitem__(
                0,
                {
                    "id": "reference",
                    "type": "reference",
                    "reference_id": "unknown-reference",
                    "actual": {"value": 1.0, "unit": "1"},
                    "expected": {"value": 1.0, "unit": "1"},
                    "unit": "1",
                    "metric": "absolute",
                    "tolerance": 0.0,
                },
            ),
            "names unknown reference 'unknown-reference'",
        ),
        (
            lambda case: case.update(
                {
                    "references": [
                        {
                            "id": "bad-artifact",
                            "type": "published",
                            "citation": "citation",
                            "locator": "locator",
                            "artifact": "unknown",
                        }
                    ]
                }
            ),
            "names unknown artifact 'unknown'",
        ),
    ],
)
def test_semantic_cross_references_are_enforced(
    tmp_path: Path,
    minimal_case,
    mutation,
    message: str,
) -> None:
    mutation(minimal_case)
    path = _write_case(tmp_path, minimal_case)

    with pytest.raises(CaseError, match=message):
        load_case(path)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../escape.json",
        "/tmp/escape.json",
        r"directory\result.json",
        "C:drive-relative.json",
        "result.json:alternate-stream",
        "NUL.json",
        "trailing-dot.",
    ],
)
def test_artifact_paths_cannot_escape_case_root(
    tmp_path: Path, minimal_case, unsafe_path: str
) -> None:
    minimal_case["artifacts"]["result"]["path"] = unsafe_path
    path = _write_case(tmp_path / "case", minimal_case)

    with pytest.raises(
        CaseError,
        match=r"artifact 'result'.*(unsafe|escapes|absolute|invalid|reserved)",
    ):
        load_case(path)


def test_artifact_symlink_cannot_escape_case_root(
    tmp_path: Path, minimal_case, symlink_factory
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    root = tmp_path / "case"
    root.mkdir()
    symlink_factory(root / "result.json", outside)
    path = _write_case(root, minimal_case)

    with pytest.raises(CaseError, match=r"artifact 'result'.*escapes"):
        load_case(path)


def test_contained_artifact_symlink_loads_but_runtime_resolution_rejects_it(
    tmp_path: Path, minimal_case, symlink_factory
) -> None:
    root = tmp_path / "case"
    root.mkdir()
    (root / "actual.json").write_text("{}\n", encoding="utf-8")
    symlink_factory(root / "result.json", "actual.json")
    path = _write_case(root, minimal_case)

    loaded = load_case(path)

    assert loaded.id == minimal_case["id"]
    with pytest.raises(ArtifactError, match="symbolic links and reparse points"):
        _ = resolve_artifacts(loaded)["result"].path


@pytest.mark.parametrize("unsafe_cwd", ["../outside", "/tmp"])
def test_execution_working_directory_cannot_escape_case_root(
    tmp_path: Path, minimal_case, unsafe_cwd: str
) -> None:
    minimal_case["execution"] = {
        "command": ["true"],
        "working_directory": unsafe_cwd,
    }
    path = _write_case(tmp_path / "case", minimal_case)

    with pytest.raises(
        CaseError,
        match=r"execution working_directory.*(unsafe|escapes|absolute)",
    ):
        load_case(path)


def test_execution_working_directory_symlink_cannot_escape_case_root(
    tmp_path: Path, minimal_case, symlink_factory
) -> None:
    root = tmp_path / "case"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    symlink_factory(root / "linked-cwd", outside, target_is_directory=True)
    minimal_case["execution"] = {
        "command": ["true"],
        "working_directory": "linked-cwd",
    }
    path = _write_case(root, minimal_case)

    with pytest.raises(CaseError, match=r"execution working_directory.*escapes"):
        load_case(path)


def test_case_directory_requires_exactly_one_supported_filename(
    tmp_path: Path, minimal_case
) -> None:
    with pytest.raises(CaseError, match=r"no pvs\.yaml or pvs\.yml"):
        load_case(tmp_path)

    _write_case(tmp_path, minimal_case, "pvs.yaml")
    _write_case(tmp_path, minimal_case, "pvs.yml")
    with pytest.raises(CaseError, match=r"contains both pvs\.yaml and pvs\.yml"):
        load_case(tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        '{"x": 1, "x": 2}',
        '{"x": NaN}',
        '{"x": Infinity}',
        '{"x": -Infinity}',
        '{"x": 1e400}',
        '{"x": 9007199254740992}',
        '{"x": -9007199254740992}',
    ],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(text: str) -> None:
    with pytest.raises(ValueError):
        loads_strict(text)


def test_strict_json_accepts_only_unambiguous_finite_document() -> None:
    assert loads_strict('{"array":[0,1.25,-2],"nested":{"ok":true}}') == {
        "array": [0, 1.25, -2],
        "nested": {"ok": True},
    }
