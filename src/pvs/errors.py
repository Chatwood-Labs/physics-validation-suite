"""PVS exception hierarchy."""


class PVSError(Exception):
    """Base class for controlled PVS failures."""


class CaseError(PVSError):
    """The case declaration is missing, invalid or unsafe."""


class ArtifactError(PVSError):
    """An artefact could not be resolved or read."""


class NumericOverflowError(ArtifactError):
    """A defined numeric operation exceeded the finite binary64 range."""


class MissingArtifactError(ArtifactError):
    """A declared artefact is absent, rather than malformed or unreadable."""

    def __init__(self, artifact_id: str, path: str) -> None:
        self.artifact_id = artifact_id
        self.path = path
        super().__init__(f"artifact does not exist: {artifact_id} ({path})")


class ExecutionError(PVSError):
    """The declared subject could not be executed."""


class IntegrityError(PVSError):
    """An evidence or package integrity check failed."""


class ReportError(PVSError):
    """A requested report could not be rendered."""
