"""Build provenance embedded into distributions.

Release builds replace these source-tree fallbacks in the wheel and sdist build
trees. Keeping the fallback explicit makes editable/source checkouts honest:
an unbuilt checkout does not pretend to have a release revision.
"""

SOURCE_IDENTITY: dict[str, str] | None = None
SOURCE_REPOSITORY: str | None = None
SOURCE_REVISION: str | None = None
SOURCE_DIRTY: bool | None = None
SOURCE_DATE_EPOCH: int | None = None
