"""Fail-closed construction of JSON Schema format validation."""

from __future__ import annotations

from jsonschema import FormatChecker

REQUIRED_JSON_SCHEMA_FORMATS = frozenset({"date", "date-time", "uri"})


class FormatSupportError(RuntimeError):
    """Required optional jsonschema format validators are unavailable."""


def required_format_checker() -> FormatChecker:
    """Return a checker only when every PVS contract format is registered."""

    checker = FormatChecker()
    registered = set(checker.checkers)
    missing = sorted(REQUIRED_JSON_SCHEMA_FORMATS - registered)
    if missing:
        names = ", ".join(missing)
        raise FormatSupportError(
            "required JSON Schema format validators are unavailable "
            f"({names}); reinstall PVS with its declared "
            "'jsonschema[format-nongpl]' dependency and run 'python -m pip check'"
        )
    return checker
