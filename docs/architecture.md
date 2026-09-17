# Architecture and scope

Physics Validation Suite (PVS) is a local evidence engine. Its stable centre is
the `EvidenceRecord`; command execution, readers, checks and reports all feed or
render that one model.

The command runner is POSIX-only because its provenance boundary requires
process-group containment through the end of execution. Other commands and the
validation API are cross-platform. Windows run mode fails closed until Job
Object containment is implemented.

```text
case + declared artefacts
          |
          v
optional command execution
          |
          v
generic readers and checks
          |
          v
authoritative EvidenceRecord
          |
          +-- evidence.json
          +-- report.html
          +-- report.pdf
          +-- manifest.json
```

## Boundaries

Core may understand files, arrays, scalar values, dimensions, commands,
tolerances, metrics, hashes, provenance and evidence. It must not contain
branches for a particular solver, research programme or physical model.

Scientific meaning belongs in case files and separately versioned benchmark
packs. An adapter may translate a software-specific output into a declared
artefact or discover software metadata. It must not choose a scientific
tolerance or silently decide what constitutes a pass.

The practical test is simple: a BOUT++ case, a CFD code, a laboratory data
pipeline and a small Python program must all reach the same engine through the
generic command and file interfaces.

## Modules

| Area | Responsibility |
| --- | --- |
| `case`, `hashing` and schemas | One stable case-byte acquisition for parsing, raw identity and retention; duplicate-key rejection and schema validation |
| `runner` | Local argument-vector execution without a shell, bounded by an optional timeout |
| `readers` | JSON Pointer, CSV-column and NetCDF-variable extraction |
| `checks` | Generic existence, schema, finite, range, monotonic, comparison, reference and conservation checks |
| `artifacts` | Confined path resolution, lifecycle snapshots, SHA-256 identity and optional retention |
| `evidence` and `finding` | One authoritative record, result aggregation, exact Evidence ID and transaction-independent Scientific Finding ID |
| `reporting` | Deterministic projections of the evidence record; never independent calculations |
| `package` and `verify` | Exact file manifest, Package ID and offline integrity verification |

The verifier's internal invariant families and dependency boundaries are mapped
in [verifier architecture](verifier-architecture.md). The public verification API
stays in `pvs.verify`; runtime validation remains mandatory for incoming evidence.

## Transaction lifecycle

PVS acquires the case once from an opened regular file, checking file identity,
size and metadata around the read. Parsing, raw SHA-256/size and retained
`case/pvs.yaml` all derive from those immutable bytes. A detected acquisition
race is a case error. Retention is checked before subject execution and never
reopens the live case. A later change to the live case still fails the transaction's
provenance check; a coherent acquired declaration does not make such a change benign.

PVS resolves all declared paths inside the case directory and takes a
pre-execution identity snapshot. In `run` mode, every declared output must be absent;
PVS refuses to bless an unchanged stale output or delete it on the user's
behalf. It then executes the declared argument vector and copies every present
validation input through one open file handle into a private immutable snapshot.
Checks and optional embedding use only those acquired bytes; a final live-path
snapshot detects changes during evaluation. Changes to immutable subject,
input or reference artefacts during execution, changes during checking, missing
required artefacts, and expected-hash mismatches are provenance errors.

The command executable is also acquired through one open handle into a private
transient snapshot before process launch. PVS executes that acquired file while
retaining the case's declared argument vector in evidence, then hashes the
snapshot again after the contained process group has finished. No transient
snapshot path is part of the evidence contract. Evidence instead binds the
versioned `pvs-private-executable-snapshot/1` launch profile. Software that
derives resource paths from its executable location can observe the private
location; adapters should use an explicit interpreter plus a declared script
artefact, or a location-independent binary, when that distinction matters.

Before subject execution, PVS proves the platform's atomic no-replace primitive
on the output parent filesystem using private collision and success probes. A
missing primitive or unsupported kernel/filesystem fails closed. The output is
then assembled in a temporary sibling directory and atomically renamed only
after evidence, requested reports and the manifest have been written. PVS
refuses to overwrite an existing output directory and does not fall back to a
rename operation that can replace it.

The command runner is not a security sandbox. A case author can ask it to run
any local executable available to the current user. Untrusted cases must be
reviewed and executed in an external sandbox or isolated worker. PVS binds the
executable it launches and records declared artefacts at transaction
boundaries; it cannot prove which files or versions arbitrary subject code
actually opened between those boundaries.

## Deliberately excluded

- arbitrary expressions or `eval`;
- hidden unit conversion or tolerance inference;
- remote data retrieval;
- scheduler, MPI or container orchestration;
- experiment covariance and uncertainty propagation;
- digital signatures and issuer trust;
- solver-specific physics checks in core.

These omissions keep the first evidence contract small enough to inspect and
reproduce. They are not claims that the problems are unimportant.
