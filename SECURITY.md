# Security policy

## Supported versions

PVS is currently alpha software. Security fixes are applied to the latest
published release.

## Execution boundary

`pvs run` executes the exact argument list declared by a case. Cases are code
execution instructions and must be reviewed before use. PVS does not invoke a
shell, interpolate shell expressions or evaluate Python from YAML. Case paths
and package-manifest paths are confined to their declared roots.

Before launch, PVS copies the resolved command executable through one open
handle into a private snapshot, executes those acquired bytes and verifies the
snapshot afterward. Evidence retains the declared command, not the transient
snapshot path. Declared artefact hashes identify bytes observed at PVS
transaction boundaries; PVS cannot prove which files arbitrary subject code
chose to open internally. Use external sandboxing or system-call auditing when
that stronger execution claim is required.

PVS records only declared environment overrides and selected non-secret runtime
metadata. It does not copy the process environment into evidence records.

## Verification and PDF parsing

PVS requires pypdf 6.18.1 or later within major version 6. The frozen dependency
profile uses 6.18.1. This minimum excludes the affected versions identified in
three moderate-severity upstream advisories published on 11 September 2026:

- [GHSA-jw7q-gvrg-4vj3](https://github.com/py-pdf/pypdf/security/advisories/GHSA-jw7q-gvrg-4vj3):
  excessive runtime while decompressing certain malformed streams.
- [GHSA-g9cg-prrw-2r8q](https://github.com/py-pdf/pypdf/security/advisories/GHSA-g9cg-prrw-2r8q):
  excessive memory use when interpreting font widths.
- [GHSA-fp3h-c4fm-7vvf](https://github.com/py-pdf/pypdf/security/advisories/GHSA-fp3h-c4fm-7vvf):
  excessive memory use when interpreting font Unicode mappings.

All three identify versions below 6.18.1 as affected. Version 6.18.0 addressed
the earlier indirect-object parsing advisory
[GHSA-5jq2-8x83-x246](https://github.com/py-pdf/pypdf/security/advisories/GHSA-5jq2-8x83-x246),
but remains affected by the September 11 advisories. The dependency correction
does not establish that every vulnerable path is reachable through PVS, and no
end-to-end PVS exploit was demonstrated in this review. Regression checks enforce
the corrected source requirement, frozen lock and installed parser version;
they are not a complete dependency vulnerability audit.

A package with invalid evidence/file bindings or a mismatching supplied identity
is rejected before its PDF is parsed or deterministically replayed. This reduces
unnecessary exposure to content which cannot pass verification. Unpinned hashes
can be recomputed by the sender and do not authenticate or sanitize PDF content.

PDF parsing remains in process. PVS does not impose a parser CPU/memory budget
or provide a verification sandbox. Services that accept hostile packages should
run verification in a worker with operating-system-enforced resource limits,
timeouts and restricted permissions. A final invalid verdict alone is not a
resource-containment guarantee.

## Reporting a vulnerability

Report an unpatched vulnerability privately to
[hello@chatwoodlabs.com](mailto:hello@chatwoodlabs.com), with the subject
`PVS security report`. This is Chatwood Labs' published general contact address;
the [contact page](https://www.chatwoodlabs.com/contact/) provides another private
contact route. Do not use a public GitHub issue for an unpatched vulnerability.

Include the PVS version, affected behaviour, a minimal reproduction and the
relevant artifact identities. Omit credentials and confidential scientific data
from the initial message; arrange any sensitive material transfer directly.
No dedicated encrypted reporting channel or response-time guarantee is claimed.
