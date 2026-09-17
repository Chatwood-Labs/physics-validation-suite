"""Deterministic PDF rendering with visible Evidence ID binding."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from typing import Any

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    CondPageBreak,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .details import DetailRow, project_report_details


class _InvariantCanvas(canvas.Canvas):
    def __init__(self, *args: Any, pvs_epoch: int, **kwargs: Any) -> None:
        kwargs["invariant"] = 1
        kwargs["pageCompression"] = 0
        super().__init__(*args, **kwargs)
        # ReportLab's invariant mode still consults the process-wide
        # SOURCE_DATE_EPOCH.  Bind v2 report metadata and the PDF document ID
        # to the evidence completion time instead, so a verifier can replay a
        # report without reconstructing the producer's environment.
        timestamp = _FixedTimestamp(pvs_epoch)
        document: Any = vars(self).get("_doc")
        if document is None or not all(
            hasattr(document, attribute) for attribute in ("_timeStamp", "signature", "_ID")
        ):
            raise RuntimeError("unsupported ReportLab PDF document internals")
        document._timeStamp = timestamp
        signature = md5(usedforsecurity=False)
        signature.update(b"a reportlab document")
        signature.update(ascii(timestamp.t).encode("utf-8"))
        document.signature = signature
        document._ID = None


class _FixedTimestamp:
    def __init__(self, epoch: int) -> None:
        self.t = epoch
        self.lt = time.gmtime(epoch)
        self.YMDhms = tuple(self.lt)[:6]
        self.dhh = 0
        self.dmm = 0
        self.tzname = "UTC"


def _evidence_completion_epoch(record: dict[str, Any]) -> int:
    raw = str(record["run"]["finished_at"])
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    value = datetime.fromisoformat(normalized)
    if value.tzinfo is None:
        raise ValueError("evidence execution finished_at must include a UTC offset")
    return int(value.astimezone(timezone.utc).timestamp())


def _safe(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 32
        else "\N{REPLACEMENT CHARACTER}"
        for character in str(value)
    )
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    )


def render_pdf(envelope: dict[str, Any], output_path: Path) -> None:
    record = envelope["record"]
    integrity = envelope["integrity"]
    evidence_id = str(integrity["evidence_id"])
    short_id = str(integrity["digest"])[:16]
    details = project_report_details(envelope)
    completion_epoch = _evidence_completion_epoch(record)

    def evidence_canvas(*args: Any, **kwargs: Any) -> _InvariantCanvas:
        return _InvariantCanvas(*args, pvs_epoch=completion_epoch, **kwargs)

    font_directory = Path(reportlab.__file__).resolve().parent / "fonts"
    if "PVS-Vera" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("PVS-Vera", font_directory / "Vera.ttf"))
        pdfmetrics.registerFont(TTFont("PVS-Vera-Bold", font_directory / "VeraBd.ttf"))

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="PVSBrand",
            parent=styles["Normal"],
            fontName="PVS-Vera-Bold",
            fontSize=9,
            leading=11,
            textColor=colors.HexColor("#22a6b3"),
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSTitle",
            parent=styles["Title"],
            fontName="PVS-Vera-Bold",
            fontSize=25,
            leading=30,
            textColor=colors.HexColor("#172033"),
            alignment=TA_LEFT,
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSHeading",
            parent=styles["Heading2"],
            fontName="PVS-Vera-Bold",
            fontSize=15,
            leading=18,
            textColor=colors.HexColor("#1f5f91"),
            spaceBefore=8,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSBody",
            parent=styles["BodyText"],
            fontName="PVS-Vera",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#172033"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSCode",
            parent=styles["BodyText"],
            fontName="Courier",
            fontSize=7,
            leading=10,
            textColor=colors.HexColor("#25364a"),
            splitLongWords=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSSubheading",
            parent=styles["PVSBody"],
            fontName="PVS-Vera-Bold",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#687386"),
            spaceBefore=8,
            spaceAfter=3,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSFieldLabel",
            parent=styles["PVSBody"],
            fontName="PVS-Vera-Bold",
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#687386"),
            spaceBefore=4,
            spaceAfter=1,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSDetailBody",
            parent=styles["PVSBody"],
            fontSize=8,
            leading=11,
            splitLongWords=True,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSDetailCode",
            parent=styles["PVSCode"],
            fontSize=7,
            leading=10,
            splitLongWords=True,
            spaceAfter=2,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSHeader",
            parent=styles["PVSBody"],
            fontName="PVS-Vera-Bold",
            textColor=colors.white,
        )
    )
    styles.add(
        ParagraphStyle(
            name="PVSBodyBold",
            parent=styles["PVSBody"],
            fontName="PVS-Vera-Bold",
        )
    )

    def paragraph(value: Any, style: str = "PVSBody") -> Paragraph:
        return Paragraph(_safe(value), styles[style])

    def detail_flowables(rows: tuple[DetailRow, ...]) -> list[Any]:
        flowables: list[Any] = []
        short_rows: list[list[Paragraph]] = []

        def flush_short_rows() -> None:
            if not short_rows:
                return
            detail_table = Table(short_rows[:], colWidths=[47 * mm, 108 * mm], splitByRow=1)
            detail_table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d9e0e8")),
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7fa")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            flowables.append(detail_table)
            short_rows.clear()

        for row in rows:
            value_style = "PVSDetailCode" if row.code else "PVSDetailBody"
            if len(row.value) > 600:
                flush_short_rows()
                flowables.append(paragraph(row.label, "PVSFieldLabel"))
                flowables.append(paragraph(row.value, value_style))
            else:
                short_rows.append(
                    [
                        paragraph(row.label, "PVSFieldLabel"),
                        paragraph(row.value, value_style),
                    ]
                )
        flush_short_rows()
        return flowables

    def footer(pdf: canvas.Canvas, document: SimpleDocTemplate) -> None:
        pdf.saveState()
        pdf.setTitle(f"PVS evidence - {record['case']['id']}")
        pdf.setAuthor("Chatwood Labs Ltd")
        pdf.setCreator(f"Physics Validation Suite {record['pvs']['version']}")
        pdf.setSubject(evidence_id)
        pdf.setKeywords(f"PVS,{evidence_id}")
        width, height = A4
        pdf.setFillColor(colors.HexColor("#edf2f6"))
        pdf.setFont("PVS-Vera-Bold", 32)
        pdf.translate(width / 2, height / 2)
        pdf.rotate(35)
        pdf.drawCentredString(0, 0, f"PVS EVIDENCE {short_id}")
        pdf.restoreState()
        pdf.saveState()
        pdf.setStrokeColor(colors.HexColor("#d9e0e8"))
        pdf.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
        pdf.setFillColor(colors.HexColor("#687386"))
        pdf.setFont("PVS-Vera", 6.5)
        pdf.drawString(18 * mm, 11 * mm, f"PVS - {evidence_id}")
        pdf.drawRightString(width - 18 * mm, 11 * mm, f"Page {document.page}")
        pdf.setFont("PVS-Vera", 6)
        pdf.drawString(18 * mm, 7.8 * mm, f"Scientific finding - {details.finding.display}")
        pdf.restoreState()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"PVS evidence - {record['case']['id']}",
        author="Chatwood Labs Ltd",
        subject=evidence_id,
    )
    story: list[Any] = [
        paragraph("CHATWOOD LABS - PHYSICS VALIDATION SUITE", "PVSBrand"),
        paragraph(record["case"]["title"], "PVSTitle"),
        Spacer(1, 3 * mm),
    ]

    status_color = {
        "PASS": "#176b3a",
        "WARN": "#8b5a00",
        "FAIL": "#a12424",
        "ERROR": "#6f1d7a",
        "SKIP": "#566273",
    }.get(record["summary"]["status"], "#172033")
    status_table = Table(
        [
            [
                paragraph("OVERALL STATUS"),
                Paragraph(
                    _safe(record["summary"]["status"]),
                    ParagraphStyle(
                        "PVSStatus",
                        parent=styles["PVSBody"],
                        fontName="PVS-Vera-Bold",
                        textColor=colors.HexColor(status_color),
                    ),
                ),
            ]
        ],
        colWidths=[45 * mm, 110 * mm],
    )
    status_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fa")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0e8")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend(
        [
            status_table,
            Spacer(1, 6 * mm),
            paragraph("Evidence identity", "PVSHeading"),
            paragraph(evidence_id, "PVSCode"),
            paragraph("Scientific finding identity", "PVSSubheading"),
            paragraph(
                details.finding.display,
                "PVSCode" if details.finding.finding_id is not None else "PVSBody",
            ),
        ]
    )

    summary_rows = [
        [paragraph("Case ID"), paragraph(record["case"]["id"], "PVSCode")],
        [paragraph("Case version"), paragraph(record["case"]["version"])],
        [paragraph("Classification"), paragraph(", ".join(record["case"]["classifications"]))],
        [paragraph("Subject"), paragraph(record["subject"]["name"])],
        [paragraph("Subject version"), paragraph(record["subject"]["version"])],
        [paragraph("Run ID"), paragraph(record["run"]["run_id"], "PVSCode")],
        [paragraph("Mode"), paragraph(record["run"]["mode"])],
        [paragraph("Provenance"), paragraph(record["summary"]["provenance_status"])],
        [
            paragraph("Scientific finding"),
            paragraph(
                details.finding.display,
                "PVSCode" if details.finding.finding_id is not None else "PVSBody",
            ),
        ],
    ]
    summary_table = Table(summary_rows, colWidths=[45 * mm, 110 * mm], repeatRows=0)
    summary_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e0e8")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7fa")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [Spacer(1, 5 * mm), paragraph("Case and subject", "PVSHeading"), summary_table, PageBreak()]
    )

    check_rows = [
        [
            paragraph("ID", "PVSHeader"),
            paragraph("Type", "PVSHeader"),
            paragraph("Status", "PVSHeader"),
            paragraph("Finding", "PVSHeader"),
        ]
    ]
    for check in record["checks"]:
        check_status_color = {
            "PASS": "#176b3a",
            "WARN": "#8b5a00",
            "FAIL": "#a12424",
            "ERROR": "#6f1d7a",
            "SKIP": "#566273",
        }.get(check["status"], "#172033")
        check_status = Paragraph(
            _safe(check["status"]),
            ParagraphStyle(
                f"PVSCheckStatus{check['status']}",
                parent=styles["PVSBody"],
                fontName="PVS-Vera-Bold",
                textColor=colors.HexColor(check_status_color),
            ),
        )
        check_rows.append(
            [
                paragraph(check["id"], "PVSCode"),
                paragraph(check["type"]),
                check_status,
                paragraph(check["summary"]),
            ]
        )
    check_table = Table(check_rows, colWidths=[38 * mm, 28 * mm, 22 * mm, 67 * mm], repeatRows=1)
    check_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102238")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e0e8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([paragraph("Checks", "PVSHeading"), check_table, PageBreak()])

    story.append(paragraph("Check audit appendix", "PVSHeading"))
    for check in details.checks:
        check_status_color = {
            "PASS": "#176b3a",
            "WARN": "#8b5a00",
            "FAIL": "#a12424",
            "ERROR": "#6f1d7a",
            "SKIP": "#566273",
        }.get(check.status, "#172033")
        check_heading = Table(
            [
                [
                    paragraph(check.id, "PVSCode"),
                    Paragraph(
                        _safe(check.status),
                        ParagraphStyle(
                            f"PVSAuditStatus{check.status}",
                            parent=styles["PVSBody"],
                            fontName="PVS-Vera-Bold",
                            textColor=colors.HexColor(check_status_color),
                            alignment=2,
                        ),
                    ),
                ]
            ],
            colWidths=[120 * mm, 35 * mm],
        )
        check_heading.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f7fa")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e0e8")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("PADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.extend(
            [
                CondPageBreak(35 * mm),
                check_heading,
                *detail_flowables(
                    (
                        DetailRow("Type", check.type),
                        DetailRow("Required", "yes" if check.required else "no"),
                        DetailRow("Finding", check.summary),
                        DetailRow("Reference ID", check.reference_id or "-", code=True),
                    )
                ),
                paragraph("Declaration", "PVSSubheading"),
                *detail_flowables(check.declaration),
                paragraph("Effective criterion", "PVSSubheading"),
                *detail_flowables(check.criterion),
                paragraph("Recorded observation", "PVSSubheading"),
                *detail_flowables(check.observation),
                Spacer(1, 5 * mm),
            ]
        )

    story.append(PageBreak())

    artifact_rows = [
        [
            paragraph("ID", "PVSHeader"),
            paragraph("Role", "PVSHeader"),
            paragraph("Path", "PVSHeader"),
            paragraph("SHA-256", "PVSHeader"),
        ]
    ]
    for artifact in record["artifacts"]:
        final = artifact.get("validation_input") or {}
        artifact_rows.append(
            [
                paragraph(artifact["id"], "PVSCode"),
                paragraph(artifact["role"]),
                paragraph(artifact["path"], "PVSCode"),
                paragraph(final.get("sha256", "missing"), "PVSCode"),
            ]
        )
    artifact_table = Table(
        artifact_rows, colWidths=[32 * mm, 23 * mm, 42 * mm, 58 * mm], repeatRows=1
    )
    artifact_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102238")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e0e8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([paragraph("Artefacts", "PVSHeading"), artifact_table, PageBreak()])

    story.append(paragraph("References", "PVSHeading"))
    if details.references:
        for reference in details.references:
            story.extend(
                [
                    CondPageBreak(25 * mm),
                    paragraph(reference.id, "PVSBodyBold"),
                    *detail_flowables(reference.rows),
                    Spacer(1, 4 * mm),
                ]
            )
    else:
        story.append(paragraph("No external reference metadata was declared."))

    story.extend([paragraph("Execution", "PVSHeading")])
    execution_rows = [
        [paragraph("Started (UTC)"), paragraph(record["execution"]["started_at"])],
        [paragraph("Finished (UTC)"), paragraph(record["execution"]["finished_at"])],
        [paragraph("Duration (s)"), paragraph(record["execution"]["duration_seconds"])],
        [paragraph("Command"), paragraph(record["execution"]["command"], "PVSCode")],
        [paragraph("Return code"), paragraph(record["execution"]["return_code"])],
        [paragraph("Succeeded"), paragraph(record["execution"]["succeeded"])],
    ]
    execution_table = Table(execution_rows, colWidths=[45 * mm, 110 * mm])
    execution_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e0e8")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7fa")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([execution_table, Spacer(1, 5 * mm), paragraph("Integrity", "PVSHeading")])
    integrity_rows = [
        [paragraph("Evidence ID"), paragraph(evidence_id, "PVSCode")],
        [paragraph("Digest"), paragraph(integrity["digest"], "PVSCode")],
        [paragraph("Canonicalization"), paragraph(integrity["canonicalization"])],
        [paragraph("Algorithm"), paragraph(integrity["algorithm"])],
        [paragraph("PVS version"), paragraph(record["pvs"]["version"])],
    ]
    integrity_table = Table(integrity_rows, colWidths=[45 * mm, 110 * mm])
    integrity_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e0e8")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f7fa")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [
            integrity_table,
            Spacer(1, 4 * mm),
            paragraph("Scientific finding identity", "PVSSubheading"),
            *detail_flowables(details.finding.rows),
            Spacer(1, 4 * mm),
            paragraph(
                "This PDF is a deterministic human-readable rendering. "
                "evidence.json is authoritative. The final PDF byte hash is "
                "recorded in manifest.json; verify the complete package with "
                "pvs verify."
            ),
        ]
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer, canvasmaker=evidence_canvas)
