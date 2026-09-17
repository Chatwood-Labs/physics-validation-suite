# Alpha publication scope

The public source version is `1.0.0a1`, titled **PVS 1.0.0 Alpha 1**, with tag
`v1.0.0-alpha.1`. Publish from a fresh repository with no internal Git history.
The package's documentation URL targets that tag's [documentation index](README.md).

The alpha CI workflow performs one basic Linux/Python 3.12 validation job.
Its green status covers only the checks listed in the [README](../README.md).
Full cross-platform qualification and a separate acceptance/handoff bundle are
not part of this alpha publication. Retained earlier development evidence is
historical; it does not qualify changed artifacts.

The complete regression suite and optional distribution/acceptance tooling
remain available for future releases. The full bundle assembler has additional
inputs, including release notes, which are deliberately absent from this source
release. See [release integrity](release-integrity.md) before using that workflow.

Apply the [public-content policy](public-content-policy.md) to published source
and nested fixtures. Keep private diagnostics and credentials outside the public
repository. [SECURITY.md](../SECURITY.md) provides the private reporting route.
