# PVS 1.0.0a1 release bundle


Template for an optional full PVS 1.0.0a1 release bundle. The public alpha is a
source repository; it does not include or require this bundle. A maintainer
using the full assembler must separately supply all listed artifacts and
`RELEASE_NOTES_1.0.0a1.md` before invoking the acceptance harnesses.

The complete repository manual, examples, tests and normative documentation
are in the [source archive](physics-validation-suite-1.0.0a1-source.zip).
Extract it and open `physics_validation_suite-1.0.0a1/README.md`; its `docs/`
links resolve inside that source tree. This shorter README is the bundle guide.
It is assembled from the frozen source's `BUNDLE_README.md` before acceptance.

The source archive also includes the four-pack benchmark catalogue: published
fusion reactivities, analytical plasma scales, classical physics examples and
the original reviewed plasma case. With the wheel installed, run
`python tools/run-benchmark-packs.py --output benchmark-evidence` from the
extracted source root. `docs/benchmark-library.md` explains each source and how
to compare your own outputs with the frozen targets. The wheel alone does not
install these scientific examples.

## Contents

| File | Purpose |
| --- | --- |
| `physics_validation_suite-1.0.0a1-py3-none-any.whl` | Installable Python package |
| `physics_validation_suite-1.0.0a1.tar.gz` | Python source distribution |
| `physics-validation-suite-1.0.0a1-source.zip` | Reviewable source, tests and full documentation |
| `test-pvs.ps1` / `test-pvs.sh` | Full Windows / Linux and macOS acceptance harnesses |
| `pvs-1.0.0a1-sample-plasma-evidence.zip` | Evidence example with retained inputs and reports |
| `sample-plasma-verification.json` | Recorded verification of that example |
| `RELEASE_METADATA.json` | Release filenames and sample Finding/Evidence/Package pins |
| `SHA256SUMS` | SHA-256 checksums of all 13 listed release files |
| `*.cdx.json` / `*.provenance.intoto.json` | SBOMs and unsigned build provenance |

If a full bundle is assembled, retain its actual acceptance results separately.

## Verify and run acceptance

Use a fresh extraction directory. Verify the outer checksum before extraction.
On Windows, from the directory containing the ZIP and sidecar:

```powershell
$expected = (Get-Content .\pvs-1.0.0a1-release-bundle.zip.sha256).Split()[0]
$actual = (Get-FileHash .\pvs-1.0.0a1-release-bundle.zip -Algorithm SHA256).Hash
if ($actual -ine $expected) { throw "Release bundle checksum mismatch" }
Expand-Archive .\pvs-1.0.0a1-release-bundle.zip -DestinationPath .\pvs-1.0.0a1-release-bundle
Set-Location .\pvs-1.0.0a1-release-bundle
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\test-pvs.ps1
```

On Linux:

```bash
sha256sum --check pvs-1.0.0a1-release-bundle.zip.sha256
unzip pvs-1.0.0a1-release-bundle.zip -d pvs-1.0.0a1-release-bundle
cd pvs-1.0.0a1-release-bundle
bash ./test-pvs.sh
```

On macOS, use `shasum -a 256 --check` for the checksum step, then the same
extraction and shell harness. The harness selects a supported interpreter,
installs the shipped wheel, verifies pinned identities, exercises examples and
the API, then runs engineering and frozen-dependency gates. It requires suitable
dependencies from an index or a complete cache. Python targets are CPython
3.10 through 3.14; the source manual defines dependency and platform limits.

Preserve the exact run directory printed by the invocation: bundle-relative
`acceptance-runs/<run>` on POSIX, or the short temporary/work-root child on Windows.
It contains the summary, ordinary/frozen JUnit XML, dependency snapshots,
stage journal, logs and diagnostic results including `plasma-finding-diff.json`.
Default successful runs remove disposable virtual environments. Exclude those
environments if the keep-environment option was used. Archive retained results
outside the release bundle and check their identities against this candidate.

For installation without acceptance, activate a supported Python virtual
environment, then run:

```text
python -m pip install "./physics_validation_suite-1.0.0a1-py3-none-any.whl[all]"
python -m pvs --version
```

The expected output is `PVS 1.0.0a1`. The `all` extra includes the NetCDF adapter.
Use the extracted source manual for complete CLI/API instructions.

## Qualification and claim boundary

Use only retained test results for the exact assembled artifacts. Earlier release results remain historical and do not qualify this
changed release. A configured CI job or successful Mypy platform target is
not a native OS test run. Ordinary Windows skips do not qualify the separate
filesystem-capability lane that requires no skips. Subject execution remains
POSIX-only; Windows supports inspect/validate/verify and fails closed for run.

Same-host repeat-build hashes describe newly built outputs. Shipped artifact
hashes are recorded under `artifact_identities` in `acceptance-summary.json`.
Same-host repeatability does not establish cross-host archive equality. A changed
Finding ID can reflect exact observation differences while both results pass
the declared tolerance; those values are not rounded to force matching IDs.

PVS records outcomes against declared scientific criteria, not universal physical
correctness. Internal consistency is separate from scientific PASS/FAIL and from
externally pinned identity. Verification does not rerun the original scientific
checks. The sample is a reference-derivation example, not a production-model run.
Its guide states the reviewed sources and their remaining scientific limits.
The public release contains no separate legacy test archive or private audit record.
Unsigned hashes do not establish issuer authenticity. Later qualification
results belong in separate, identified attachments; do not edit this tested bundle.
