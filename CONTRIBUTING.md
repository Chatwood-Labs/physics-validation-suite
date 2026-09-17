# Contributing

PVS core is intentionally domain-agnostic. Contributions that add a hidden
domain tolerance, software-name branch or physics-specific check to core will
not be accepted. Put scientific meaning in a case or benchmark pack, and put
software translation in an adapter or example.

For code changes, use the local development checks:

```bash
python -m pip install -e ".[dev]"
python tools/run-pytest.py
ruff check .
python -m build
python -m twine check dist/*
```

New check behaviour must include PASS, WARN, FAIL and ERROR tests. Changes to
canonical evidence or manifest semantics require tamper-detection tests and a
schema-version decision. New scientific anchors require primary-source
citation, derivation notes and reviewable provenance; a green regression test
against an unexplained literal is not sufficient.

Scientific content must also meet the
[public-content policy](docs/public-content-policy.md), including per-file
rights and redistribution status, independent source checks and inspection of
nested fixtures and generated evidence. Keep private implementation material and
diagnostics outside public contributions.

`uv.lock` is the frozen qualification environment and must remain consistent
with `pyproject.toml`. Alpha CI runs only the core smoke workflow on Linux/Python 3.12.
Run broader checks locally when relevant to your change. See
[`docs/release-integrity.md`](docs/release-integrity.md) before building release
artefacts or generating SBOM/provenance records.
