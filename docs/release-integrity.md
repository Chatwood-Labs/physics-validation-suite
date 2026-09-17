# Release integrity and dependency closure

Alpha 1 is published as a source repository with basic CI. The distribution,
SBOM, reproducibility and full-bundle procedures below are optional maintainer
tools, not prerequisites or executed qualification claims for this alpha.
`BUNDLE_README.md` is a template; the full assembler also requires separately
authored release notes and all of its declared artifacts.

PVS records which implementation generated an evidence record without
trying to place a wheel's final digest inside that same wheel. Final wheel and
sdist hashes remain external release facts, where they can later be signed
without a recursive-hash problem.

## Installed implementation identity

`record.pvs.content_identity` uses the
`pvs-installed-package-tree-sha256-v1` profile. An independent implementation
recomputes it as follows:

1. enumerate the installed importable `pvs/` tree without following links;
2. reject linked/reparse and non-regular content;
3. exclude `__pycache__/`, `*.pyc` and `*.pyo` derived runtime files;
4. record every remaining file as its package-relative POSIX path, byte length
   and lowercase SHA-256, sorted by path;
5. RFC 8785-canonicalize `{"profile": PROFILE, "files": ENTRIES}` and hash
   those canonical bytes with SHA-256.

The evidence stores the profile, canonicalization, algorithm, digest and file
count. The wheel archive, installer-generated `*.dist-info/RECORD`, console
script and bytecode caches are deliberately outside this identity. That makes
the value stable across a wheel installation and an equivalent unpacked
package tree while still binding all executable PVS Python and packaged schema
bytes.

`record.pvs.build.source_identity` binds the exact source archive used for the
release build with the `pvs-source-archive-sha256-v1` profile. Build it with
`PVS_BUILD_SOURCE_ARCHIVE=/path/to/physics-validation-suite-1.0.0a1-source.zip`; an independently
computed digest can instead be injected through `PVS_BUILD_SOURCE_DIGEST`.
The source repository, full 40- or 64-character VCS revision, dirty state and
`SOURCE_DATE_EPOCH` are separate fields. Editable or ordinary local source use
is represented honestly with null build fields.

PVS does not infer a public repository from a local Git checkout. The generic
build backend can accept explicit repository/revision/dirty inputs, but the
official release assembler requires all three to be null and binds the
published source ZIP instead. This prevents a private or synthetic local commit
from being presented as a revision resolvable in the public project repository.

## Dependency profiles

The base installation contains NumPy and the PDF generation/verification stack.
PDF stays core because effective package policy defaults PDF output on. NetCDF
is intentionally separate:

```bash
python -m pip install .
python -m pip install ".[netcdf]"
python -m pip install ".[all]"
```

The base profile can validate JSON/CSV/text/binary cases and create and verify
PDF packages without installing the NetCDF C stack. A case that actually uses
NetCDF fails closed and names `physics-validation-suite[netcdf]`; no requested
reader, report or verification step is silently omitted.

`uv.lock` is the exact, hash-pinned development/release qualification closure.
Keep it synchronized with project metadata and exercise it separately from the
compatible-range basic CI job:

```bash
uv lock --check
uv sync --frozen --extra dev --extra all
uv run --frozen python tools/run-pytest.py
```

The basic CI job resolves compatible dependencies for the core installation.
It is not the frozen release environment or a cross-platform matrix.

## Reproducible distributions

The custom PEP 517 backend normalizes sdist member order, timestamps, ownership,
modes and gzip headers. Setuptools' wheel builder consumes the same
`SOURCE_DATE_EPOCH`. Use the release assembler's source phase to prepare and
audit the exact source ZIP that will be published, then inject its identity into
both Python distributions:

```bash
SOURCE_DATE_EPOCH=1700000000 \
PVS_BUILD_SOURCE_ARCHIVE="$(pwd)/release/physics-validation-suite-1.0.0a1-source.zip" \
python -m build --no-isolation --outdir dist
```

The optional reproducibility tool builds both formats twice and compares the final
bytes:

```bash
SOURCE_DATE_EPOCH=1700000000 \
PVS_BUILD_SOURCE_ARCHIVE="$(pwd)/release/physics-validation-suite-1.0.0a1-source.zip" \
python tools/check-reproducible-build.py --expect-directory dist
```

Choose one fixed release epoch and record it in release metadata and build
provenance. The literal above is only a stable local/CI test fixture. A passing
comparison establishes repeatability in that tested build environment;
cross-platform and independently provisioned builder comparison remains a
separate release qualification.

`--expect-directory` belongs to the canonical release-building gate: it
requires both pristine rebuilds to equal the distribution bytes that will be
published. The extracted Windows and POSIX consumer harnesses omit that option.
They still verify the published files through `SHA256SUMS`, install and exercise
the published wheel, validate both published distributions with Twine, and
require two pristine builds on the consumer host to be byte-identical to each
other. They do not claim that ordinary setuptools archives produced on
different operating systems are byte-identical; generated line endings and ZIP
host metadata can legitimately differ.

## CycloneDX and build provenance

Build and generate official metadata only from the canonical Linux x86_64
CPython 3.12 environment containing the released wheel and `[all]`. Provision
that environment from the frozen lock, add only the exact lock-pinned build
bootstrap, build the distributions with its interpreter, and then replace the
temporary synchronized project installation with the released wheel. The tool
excludes pip, setuptools, wheel, build and pyproject-hooks as bootstrap tools,
records the remaining installed distribution closure, binds `uv.lock`, and
makes each wheel/sdist an external SHA-256 subject. Bundle assembly independently
regenerates each SBOM in that clean frozen environment and requires exact bytes:

```bash
SOURCE_TREE="$(pwd)"
RELEASE_ROOT=/absolute/path/outside-source/pvs-v0.3-release
RELEASE_ENV="$RELEASE_ROOT/release-environment"
RELEASE_PYTHON=/absolute/path/to/cpython3.12
UV_PROJECT_ENVIRONMENT="$RELEASE_ENV" \
  uv sync --frozen --extra all --python "$RELEASE_PYTHON"
RELEASE_PY="$RELEASE_ENV/bin/python"
"$RELEASE_PY" -c \
  'import platform, sys; assert platform.python_implementation() == "CPython"; assert sys.version_info[:2] == (3, 12)'
export PATH="$RELEASE_ENV/bin:$PATH"
test "$(command -v python)" = "$RELEASE_PY"
uv pip install --python "$RELEASE_PY" --no-deps \
  'build==1.6.0' \
  'pyproject-hooks==1.2.0' \
  'setuptools==84.0.0' \
  'wheel==0.48.0'
SOURCE_DATE_EPOCH=1700000000 \
PVS_BUILD_SOURCE_ARCHIVE="$RELEASE_ROOT/physics-validation-suite-1.0.0a1-source.zip" \
  python -m build --no-isolation --outdir dist
uv pip install --python "$RELEASE_PY" --no-deps --force-reinstall \
  ./dist/physics_validation_suite-1.0.0a1-py3-none-any.whl

SOURCE_DATE_EPOCH=1700000000 \
PVS_BUILDER_ID=https://github.com/chatwood-labs/physics-validation-suite/actions \
PVS_BUILD_COMMAND='python -m build --no-isolation --outdir dist' \
"$RELEASE_PY" -m pvs.supply_chain \
  dist/*.whl dist/*.tar.gz \
  --source "$RELEASE_ROOT/physics-validation-suite-1.0.0a1-source.zip" \
  --lock uv.lock --output "$RELEASE_ROOT/release-metadata"
```

This writes a deterministic CycloneDX 1.6 SBOM for each release subject and one
in-toto Statement v1 using the SLSA Provenance v1 predicate. Provenance
generation rejects a missing embedded source identity or a supplied source
whose digest does not match the installed release build. A pristine extracted
release tree is also accepted as `--source` and uses the independently specified
`pvs-source-tree-sha256-v1` profile.

That tree profile rejects VCS metadata, links/reparse points and non-regular
files, then records every regular file as its source-relative POSIX path, byte
length and lowercase SHA-256. It sorts those entries, RFC 8785-canonicalizes
`{"profile": PROFILE, "files": ENTRIES}`, and SHA-256-hashes the canonical
bytes. There are no filename or cache exclusions: use a pristine extraction of
the published source archive.

No VCS material is inferred from embedded build metadata. The generic generator
can accept `--source-repository` and `--source-revision` together for separate
workflows, but official release bundles use neither. Their statement binds the
actual source ZIP and `uv.lock`, which is the precise claim the assembler
supports.

The release statements are deliberately unsigned. Their digests are useful release
facts, but they do not authenticate Chatwood Labs as issuer. Publish their final
hashes in the external release checksum manifest; signatures remain roadmap
work rather than implied trust.

## Release-version consistency

`pyproject.toml` is the authoritative release version. Before assembling or
building a release, run `python tools/release_contract.py --check` from the
source root. The command emits derived artifact names as JSON and fails if
active runtime, lock, citation, README, harness, release-test or workflow
references disagree. Release notes are optional in the source tree; the full
bundle assembler requires them as an explicit input. This metadata tool is
available locally and is not a job in alpha CI.
All workflows are scanned recursively, including the hidden `.github` tree.
Both the source ZIP and sdist include this workflow and the consistency tests.
The gate also requires a documentation index and a project Documentation URL
under the current source tag; it does not claim that the tag is already public.
Stale-reference diagnostics use repository-relative POSIX paths on every host:
`path:line: rejected-version != required-version`. This formatting does not
change filesystem access or the rejection criteria. A failed gate exits with
code 1 before emitting metadata or appending GitHub job outputs.

Historical release notes and qualification records, frozen evidence fixtures,
and scientific case/schema versions are separate contracts and retain their
original versions. A new release updates active references and adds new dated
qualification evidence; it never rewrites older runs as qualification of new bytes.
