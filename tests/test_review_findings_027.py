"""Focused regression tests from review of PVS 0.2.7.

Run in the project's supported development environment:
    python -m pytest -q test_review_findings_027.py

Expected against unmodified 0.2.7: 2 failures and 2 passes.
The failures cover an absent CSV column accepted on an empty table, and a
source file descriptor leaked when snapshot destination-directory creation fails.
The passing cases preserve legitimate empty-column behavior and verify the
existing rejection of an absent column on a nonempty table.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import pvs.hashing as hashing
from pvs.artifacts import ResolvedArtifact
from pvs.checks.engine import evaluate_checks
from pvs.models import CheckResult, Status


def _csv_size_check(root: Path, *, rows: str, column: str) -> CheckResult:
    (root / 'result.csv').write_text('temperature\n' + rows, encoding='utf-8')
    artifact = ResolvedArtifact(
        id='result',
        root=root,
        declaration={'path': 'result.csv', 'role': 'output', 'format': 'csv'},
    )
    declaration: dict[str, Any] = {
        'id': 'column-size',
        'type': 'compare',
        'unit': '1',
        'metric': 'absolute',
        'tolerance': 0.0,
        'actual': {
            'artifact': 'result', 'column': column, 'reduce': 'size', 'unit': '1',
        },
        'expected': {'value': 0, 'unit': '1'},
    }
    return evaluate_checks([declaration], {'result': artifact})[0]


@pytest.mark.parametrize('rows', ['', '12\n'], ids=['header-only', 'nonempty'])
def test_absent_csv_column_is_always_an_error(tmp_path: Path, rows: str) -> None:
    result = _csv_size_check(tmp_path, rows=rows, column='pressure')
    assert result.status is Status.ERROR, (
        'The pressure column is absent, independently of the number of data rows; '
        f'got {result.status.value}: {result.summary}'
    )


def test_existing_column_in_empty_csv_still_has_size_zero(tmp_path: Path) -> None:
    result = _csv_size_check(tmp_path, rows='', column='temperature')
    assert result.status is Status.PASS
    assert result.observed['actual'] == 0.0


def test_snapshot_directory_failure_closes_source_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / 'source.bin'
    source.write_bytes(b'payload')
    blocker = tmp_path / 'regular-file-not-a-directory'
    blocker.write_bytes(b'blocker')

    real_open = os.open
    real_close = os.close
    opened: list[int] = []
    closed: list[int] = []

    def tracked_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        descriptor = real_open(path, flags, *args, **kwargs)
        if Path(path) == source:
            opened.append(descriptor)
        return descriptor

    def tracked_close(descriptor: int) -> None:
        real_close(descriptor)
        closed.append(descriptor)

    monkeypatch.setattr(hashing.os, 'open', tracked_open)
    monkeypatch.setattr(hashing.os, 'close', tracked_close)
    try:
        with pytest.raises(OSError):
            hashing.snapshot_file(source, blocker / 'snapshot.bin')
        assert not set(opened).difference(closed), (
            'Every opened source descriptor must close even when mkdir fails; '
            f'opened={opened}, closed={closed}'
        )
    finally:
        # Do not make the regression test itself leave leaked descriptors behind.
        for descriptor in set(opened).difference(closed):
            real_close(descriptor)
