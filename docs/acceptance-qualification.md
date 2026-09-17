# Alpha validation and optional acceptance tools

Alpha CI runs only the basic Linux/Python 3.12 job described in the
[README](../README.md). No full acceptance bundle is supplied or required.
The sections below document optional tools retained for separately assembled
full bundles; their presence is not a claim that they ran for this alpha.

The full `test-pvs.ps1` and `test-pvs.sh` harnesses test the released wheel before
installing the source development environment. They keep the full logs in the
unique run directory printed by the script. On POSIX this is beneath the bundle's
`acceptance-runs/`; Windows uses a bounded temporary directory or the parent
selected with `-WorkRoot`. The run directory contains:

| File | Recorded facts |
| --- | --- |
| `acceptance-summary.json` | Overall outcome, full/quick mode, stages, selected interpreter, environments, test results, release hashes and plasma Finding comparison |
| `acceptance-stages.tsv` | UTC start/end and outcome of each stage, including the failed stage |
| `selected-interpreter.json` | Concrete base executable, executing executable, CPython version, platform and architecture |
| `runtime-all.json` | Released-wheel runtime dependency versions and interpreter |
| `ordinary-dev-all.json` | Ordinary development dependency versions and interpreter |
| `frozen-dev-all.json` | Frozen development dependency versions and interpreter |
| `ordinary-pytest.xml`, `frozen-pytest.xml` | Individual outcomes, capability properties and actual skip reasons |
| `plasma-finding-diff.json` | Exact scientific projection differences between bundled and fresh plasma evidence |
| `benchmark-evidence/` | Complete catalogue demonstration results, external-producer records and verified per-pack evidence |

The summary format is `pvs-acceptance-summary/1`. Once a run directory exists, early failures before a trusted
source tool is available produce a minimal FAILED summary referring to the log
and stage journal. Quick runs mark absent engineering tests as NOT_RUN. Full
success requires both JUnit reports, all three environment snapshots, release
identities and a completed Finding comparison. A reported passing stage does
not override a failed test or interpreter mismatch found by the summary tool.

Each `pvs-dependency-versions:sha256:` identity hashes the UTF-8 JSON array of
unique installed normalized package names and versions, sorted by name/version, with
sorted object keys and compact separators. The lock file has a separate SHA-256.
This identifies installed version sets; it is not a content hash of every
installed file, a signature or proof that the ordinary profile equals the lock.
The frozen stage separately enforces `uv.lock` with `--frozen`.

All `uv lock`, `sync` and `run` commands receive the concrete base executable
selected at startup. Snapshots check the implementation, complete version and
base executable against that selection. This prevents `uv` silently choosing a
different patch release. Ordinary and frozen versions may differ for packages
within declared ranges; each actual closure is recorded independently.

## Optional native Windows filesystem checks

Tests marked `windows_filesystem` exercise real symlinks and junctions where
host permissions permit. `python tools/run-pytest.py -m windows_filesystem
--require-no-skips` requires those capabilities without skips. Run this command
on one line, on Windows, when native filesystem qualification is needed.
There is no Windows capability job in alpha CI. Windows `run` remains unsupported.

## Investigating a different Finding ID

A Finding ID hashes the declared scientific projection, including structured
observations. Transaction timestamps, execution logs, output artifact byte
identities and report bytes are excluded. A small numerical difference may change
that ID while both results still satisfy the declared tolerance.

Both harnesses independently verify the bundled and fresh records, then write
all canonical scientific field differences, with JSON pointers and hexadecimal
float values. The comparison is exact, without approximate equality. A difference
between valid Findings is recorded rather than treated as transport corruption;
package verification still has to pass independently. Do not change observations
or tolerances simply to force matching IDs.

The tool can also inspect any two retained records:

```bash
python tools/acceptance-summary.py finding-diff \
  /path/to/bundled/evidence.json /path/to/fresh/evidence.json \
  --output /path/to/plasma-finding-diff.json
```

A difference report identifies the changed scientific fields. Establishing its
origin in a particular math operation also requires the relevant output arrays
and a numerical trace; the report alone does not establish that cause.

## Retaining results

If the optional full harnesses are used, retain their original outputs privately
when they include host paths or diagnostics. Bind any published summary to the
actual tested artifacts and state which platforms and checks ran. Do not treat
old qualification results or a configured workflow as results for a new release.
Follow the [public-content policy](public-content-policy.md).
