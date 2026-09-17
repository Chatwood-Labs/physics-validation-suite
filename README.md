# Physics Validation Suite

**Physics Validation Suite (PVS)** turns declared scientific checks into portable,
auditable evidence packages. It works with existing outputs or a declared local
command, records exactly what was checked, and produces JSON evidence with HTML
and PDF reports.

Developed by [Chatwood Labs Ltd](https://chatwoodlabs.com).

> **1.0.0 Alpha 1** · Python package version `1.0.0a1` · Git tag `v1.0.0-alpha.1`
>
> This is the first public alpha, intended for evaluation and feedback. APIs and
> package interfaces may change before 1.0. A scientific PASS applies to the
> declared checks and references; it is not a certificate of physical correctness.

## Quick start

From the repository root, create a Python 3.12 virtual environment.

**Windows PowerShell:**

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install .
& .\.venv\Scripts\python.exe -m pvs validate examples/quickstart --output quickstart-evidence
& .\.venv\Scripts\python.exe -m pvs verify quickstart-evidence
```

**Linux / macOS:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m pvs validate examples/quickstart --output quickstart-evidence
python -m pvs verify quickstart-evidence
```

Use a new output directory. The [quick-start case](examples/quickstart/pvs.yaml)
checks a supplied JSON value and produces `evidence.json`, `manifest.json`,
`report.html` and `report.pdf`. Verification checks package integrity;
`unpinned` means that no externally trusted identity was supplied.

Check the installed version with `python -m pvs --version`: it should report
`PVS 1.0.0a1`. These instructions install from source and do not assume a PyPI
publication or a separate release bundle.

## What PVS provides

- Declarative YAML cases, explicit tolerances and named scientific references.
- JSON and CSV readers, with an optional NetCDF reader.
- Range, finite, monotonic, comparison, reference and conservation checks.
- Distinct PASS, WARN, FAIL, ERROR and SKIP outcomes.
- SHA-256 identities and RFC 8785 canonical JSON evidence.
- Offline integrity verification, with optional caller-pinned identities.
- HTML and PDF reports bound to the evidence package.
- A command-line interface and typed Python API.
- Public examples and four independently versioned benchmark packs.

PVS needs no server, account or database. Scientific assumptions, reference
suitability and tolerances belong in the case; PVS does not choose them for you.
Unsigned hashes establish byte identity, not publisher authentication.

## Common commands

```bash
python -m pvs inspect examples/json-existing/pvs.yaml
python -m pvs validate examples/json-existing/pvs.yaml --output json-evidence
python -m pvs verify json-evidence
```

On Linux, a case can also run its declared command before validation:

```bash
python -m pvs run examples/command-python/pvs.yaml --output command-evidence
```

Review a case before using `run`: it executes the declared program with your
permissions. See the [security policy](SECURITY.md) for execution and verification
boundaries.

For NetCDF, install `python -m pip install ".[netcdf]"`. The `[all]` extra
currently installs the same runtime adapters. JSON, CSV, HTML and PDF are included
in the base installation.

## Alpha scope and platforms

The package declares CPython 3.10–3.14. Python 3.12 on Linux is the current basic
CI target. That job installs the core package, checks dependencies and syntax,
validates JSON/CSV examples, verifies HTML/PDF evidence packages, and checks that
a modified report is rejected with exit code 4.

| Platform | Inspect / validate / verify | Run a subject command |
| --- | --- | --- |
| Linux | Implemented; basic CI on Python 3.12 | Implemented |
| Windows | Implemented; not covered by alpha CI | Unsupported; fails closed |
| macOS | Implemented; not covered by alpha CI | Implemented; not qualified for this alpha |

The basic job does not run the full test suite, other Python versions, native
Windows/macOS checks, NetCDF or release-bundle acceptance. The broader tests and
tools remain available locally. Earlier development results are not presented as
qualification of these new alpha bytes.

See [platform and verification details](docs/user-guide.md) and the
[numerical contract](docs/numerical-contract.md) for the scientific and execution
limits. Public benchmark examples demonstrate their declared cases; they do not
validate an arbitrary production solver.

## Documentation and examples

- [Documentation index](docs/README.md)
- [User guide: cases, CLI, Python API and exit codes](docs/user-guide.md)
- [Case format](docs/case-format.md)
- [Evidence and verification](docs/evidence-and-verification.md)
- [Benchmark library](docs/benchmark-library.md)
- [Architecture](docs/architecture.md)

The [examples](examples/README.md) cover existing JSON/CSV data, local commands,
NetCDF, a Python API integration and a BOUT++ adapter.

## Development

```bash
python -m pip install -e ".[dev]"
python tools/run-pytest.py
```

The complete regression suite stays in the repository. Pushes, pull requests
and manual CI runs execute only the basic validation job, with no dormant jobs
or full-qualification toggle. See [CONTRIBUTING.md](CONTRIBUTING.md) for local
development checks and [release integrity](docs/release-integrity.md) for optional
distribution tooling.

Report reproducible problems through
[GitHub issues](https://github.com/chatwood-labs/physics-validation-suite/issues).
Follow [SECURITY.md](SECURITY.md) for private vulnerability reports.

## Citation and licence

Cite **Chatwood Labs Ltd, Physics Validation Suite, version 1.0.0a1 (2026)** and
the scientific sources declared by your cases. Machine-readable citation metadata
is in [CITATION.cff](CITATION.cff).

PVS is distributed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE).
