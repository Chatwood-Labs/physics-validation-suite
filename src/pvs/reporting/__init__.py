"""Deterministic renderers for the authoritative evidence envelope."""

from pathlib import Path
from typing import Any

from ..errors import ReportError
from .html import render_html
from .legacy_v1_html import render_html as render_html_v1
from .legacy_v2_html import render_html as render_html_v2


def _missing_pdf_dependency(exc: ModuleNotFoundError) -> ReportError:
    return ReportError(
        "PDF reporting dependencies are missing from this installation; "
        "reinstall physics-validation-suite and run 'python -m pip check'"
    )


def render_pdf(envelope: dict[str, Any], output_path: Path) -> None:
    """Load the PDF stack only when v2 PDF output is requested."""

    try:
        from .pdf import render_pdf as renderer
    except ModuleNotFoundError as exc:
        if exc.name == "reportlab" or (exc.name or "").startswith("reportlab."):
            raise _missing_pdf_dependency(exc) from exc
        raise
    renderer(envelope, output_path)


def render_pdf_v1(envelope: dict[str, Any], output_path: Path) -> None:
    """Load the frozen v1 PDF renderer only when legacy replay is requested."""

    try:
        from .legacy_v1_pdf import render_pdf as renderer
    except ModuleNotFoundError as exc:
        if exc.name == "reportlab" or (exc.name or "").startswith("reportlab."):
            raise _missing_pdf_dependency(exc) from exc
        raise
    renderer(envelope, output_path)


def render_pdf_v2(envelope: dict[str, Any], output_path: Path) -> None:
    """Replay frozen v2 reports with their original projection."""
    try:
        from .legacy_v2_pdf import render_pdf as renderer
    except ModuleNotFoundError as exc:
        if exc.name == "reportlab" or (exc.name or "").startswith("reportlab."):
            raise _missing_pdf_dependency(exc) from exc
        raise
    renderer(envelope, output_path)


__all__ = [
    "render_html",
    "render_html_v1",
    "render_html_v2",
    "render_pdf",
    "render_pdf_v1",
    "render_pdf_v2",
]
