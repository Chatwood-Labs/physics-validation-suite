# Public content policy

The public PVS source, distributions, benchmark packs and sample evidence contain
only releasable framework code, identified public reference material and explicitly
labelled teaching fixtures. Company copyright, public project links and licence
notices remain attached to the software.

## Admitting a reference

For every scientific data or reference file, retain:

- A primary public source, edition/version, stable identifier and exact table,
  figure, equation or dataset location.
- The acquisition date and source-byte identity where a reviewed copy is available.
- The file's provenance, transformations, units, applicability, assumptions and
  scientific uncertainty or declared numerical tolerance.
- Rights and redistribution status for that file, including required attribution.
  Public access and a DOI alone do not grant redistribution permission.
- A clear classification: published measurement, published numerical benchmark,
  equation-derived verification case, cross-code comparison or synthetic example.
- An independent check against the direct public reference. Generating both the
  subject and its expected answer with one implementation is only a regression
  test, and must be labelled accordingly.

When redistribution is not permitted or its status is unresolved, keep link-only
source/acquisition metadata and any permitted derived material separately
identified. Do not bundle the restricted dataset or paper. Do not describe a
small transcription or derived result as a redistributed original dataset.

## Excluded material

Public exports exclude private implementation code, unpublished data, internal
parameter sets, application configurations, credentials, implementation hashes,
private source paths, production audit records and private diagnostic reports.
Public scientific-source hashes and public software-artifact hashes are allowed.
Do not include a private archive merely because it was useful during review.

Inspect the complete exported file set, including nested ZIPs and source
archives, frozen test fixtures, generated JSON, manifests, HTML/PDF reports,
logs, caches and build metadata. A source-text search alone does not inspect a
nested evidence package. Generate public sample evidence afresh from public
inputs; avoid retaining private material through an earlier sample.

Private historical evidence must be omitted from a public export as a whole.
Do not edit or redact an integrity-bound historical record and present it as the
original. Any replacement compatibility fixture must be clearly identified as
synthetic and preserve only the contract it is intended to test.

## Release evidence

Qualification applies to exact identified artifacts and the checks actually
executed. Historical runs do not qualify changed reference content or replacement
archives. Keep complete original qualification privately when it includes host
diagnostics. Any public summary must be explicitly identified as a summary,
state its actual scope and omissions, and bind the release hashes without
including private diagnostics.
