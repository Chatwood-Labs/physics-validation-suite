"""Release-review regressions for PVS 0.3.8.

Run in a normal PVS development environment:
    python -m pytest -q test_same_source_consistency.py

Expected on the reviewed 0.3.8 wheel: three rejection tests FAIL and two
controls PASS. The failures demonstrate acceptance of contradictory evidence,
not ordinary producer miscalculation or bypass of an externally trusted pin.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from pvs.api import validate_case
from pvs.canonical import canonical_sha256
from pvs.finding import create_finding_metadata
from pvs.hashing import file_identity
from pvs.jsonutil import load_strict, write_pretty
from pvs.verify import verify_target


def _produce(
    root: Path, *, reduction: str | None, distinct_sources: bool = False,
    expected: float = 1.0,
) -> tuple[Any, dict[str, Any]]:
    case_root = root / 'case'
    case_root.mkdir(parents=True)
    write_pretty(case_root / 'result.json', {'a': [10, 20], 'b': [10]})
    source: dict[str, Any] = {'artifact': 'result', 'pointer': '/a', 'unit': '1'}
    if reduction is not None:
        source['reduce'] = reduction
    other = copy.deepcopy(source)
    if distinct_sources:
        other['pointer'] = '/b'
    case = {
        'schema': 'pvs-case/2', 'id': 'same-source-review', 'version': '1.0.0',
        'title': 'Identical source consistency', 'classifications': ['verification'],
        'subject': {'name': 'synthetic', 'version': '1.0.0'},
        'artifacts': {'result': {'path': 'result.json', 'role': 'output', 'format': 'json'}},
        'checks': [{
            'id': 'balance', 'type': 'conservation', 'required': True, 'unit': '1',
            'terms': [
                {'label': 'positive', 'coefficient': 1.0, 'value': source},
                {'label': 'negative', 'coefficient': -1.0, 'value': other},
            ],
            'expected': {'value': expected, 'unit': '1'},
            'absolute_tolerance': 0.0, 'relative_tolerance': 0.0,
        }],
        'package': {'embed_artifacts': True, 'html': False, 'pdf': False},
    }
    write_pretty(case_root / 'pvs.yaml', case)
    outcome = validate_case(case_root, output_dir=root / 'package')
    assert verify_target(outcome.output_directory).valid
    return outcome, load_strict(outcome.evidence_path)


def _rehash(outcome: Any, envelope: dict[str, Any]) -> None:
    record = envelope['record']
    record['summary']['status'] = 'PASS'
    record['summary']['counts'] = {
        name: int(name == 'PASS') for name in record['summary']['counts']
    }
    envelope['integrity']['finding'] = create_finding_metadata(record)
    digest = canonical_sha256(record)
    envelope['integrity'].update(digest=digest, evidence_id=f'pvs:sha256:{digest}')
    write_pretty(outcome.evidence_path, envelope)
    manifest = load_strict(outcome.manifest_path)
    manifest['package']['evidence_id'] = envelope['integrity']['evidence_id']
    for entry in manifest['package']['files']:
        entry.update(file_identity(outcome.output_directory / entry['path']))
    digest = canonical_sha256(manifest['package'])
    manifest['integrity'].update(digest=digest, package_id=f'pvs-package:sha256:{digest}')
    write_pretty(outcome.manifest_path, manifest)


@pytest.mark.parametrize('reduction', ['size', 'sum', None])
def test_reject_inconsistent_totals_for_identical_sources(
    tmp_path: Path, reduction: str | None,
) -> None:
    outcome, envelope = _produce(tmp_path, reduction=reduction)
    assert outcome.status.value == 'FAIL'  # x - x cannot equal 1 at zero tolerance.
    package = outcome.output_directory
    preserved = {
        path.relative_to(package): path.read_bytes()
        for directory in [package / 'case', package / 'artifacts']
        for path in directory.rglob('*') if path.is_file()
    }
    result = envelope['record']['checks'][0]
    result['status'] = 'PASS'
    result['summary'] = 'conservation residual is within tolerance'
    observed = result['observed']
    positive = observed['terms'][0]['raw_total']
    observed['terms'][1].update(raw_total=positive - 1.0, contribution=-(positive - 1.0))
    observed.update(balance=1.0, absolute_error=0.0)
    _rehash(outcome, envelope)

    assert all((package / name).read_bytes() == content for name, content in preserved.items())
    # Existing caller-pinned identity remains protective; this is not a pin bypass.
    assert not verify_target(package, expect_package_id=outcome.package_id).valid
    verification = verify_target(package)
    assert not verification.valid, (
        'Verifier accepted a PASS for x - x == 1 with identical source declarations; '
        f'reduction={reduction!r}, evidence_status={verification.evidence_status!r}'
    )


def test_identical_sources_with_consistent_totals_remain_valid(tmp_path: Path) -> None:
    outcome, _ = _produce(tmp_path, reduction='size', expected=0.0)
    assert outcome.status.value == 'PASS'
    assert verify_target(outcome.output_directory, expect_package_id=outcome.package_id).valid


def test_distinct_selectors_may_have_distinct_totals(tmp_path: Path) -> None:
    outcome, _ = _produce(tmp_path, reduction='size', distinct_sources=True)
    assert outcome.status.value == 'PASS'
    assert verify_target(outcome.output_directory, expect_package_id=outcome.package_id).valid
