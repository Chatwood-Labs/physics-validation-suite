from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from pvs.jsonutil import load_strict
from pvs.reporting import render_html, render_html_v1, render_pdf, render_pdf_v1
from pvs.reporting.details import project_report_details


def _rich_audit_envelope(package_factory) -> dict:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    finding_digest = "c" * 64
    envelope["integrity"]["finding"] = {
        "projection": "pvs-finding-projection/1",
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "status": "ISSUED",
        "digest": finding_digest,
        "finding_id": f"pvs-finding:v1:sha256:{finding_digest}",
    }
    checks = [
        {
            "id": "published-scalar",
            "type": "reference",
            "description": "Compare the recorded pressure against the published value.",
            "required": True,
            "reference_id": "paper-1",
            "actual": {
                "artifact": "result",
                "pointer": "/pressure",
                "unit": "Pa",
            },
            "expected": {
                "artifact": "reference-data",
                "pointer": "/pressure",
                "unit": "Pa",
            },
            "metric": "relative",
            "tolerance": 0.02,
            "scale_floor": 1e-12,
            "unit": "Pa",
        },
        {
            "id": "profile-close",
            "type": "compare",
            "actual": list(range(100)),
            "expected": list(range(100)),
            "metric": "close",
            "absolute_tolerance": 0.1,
            "relative_tolerance": 0.01,
            "unit": "K",
        },
        {
            "id": "mass-balance",
            "type": "conservation",
            "terms": [
                {"label": f"term-{index}", "coefficient": 1.0, "value": index}
                for index in range(25)
            ],
            "expected": 300.0,
            "absolute_tolerance": 0.5,
            "relative_tolerance": 0.01,
            "unit": "kg",
        },
        {
            "id": "errored-check",
            "type": "finite",
            "source": {"artifact": "result", "pointer": "/missing", "unit": "m/s"},
        },
        {
            "id": "skipped-check",
            "type": "exists",
            "artifact": "optional-output",
            "required": False,
        },
    ]
    envelope["record"]["case"]["resolved_definition"]["checks"] = checks
    envelope["record"]["checks"] = [
        {
            "id": "published-scalar",
            "type": "reference",
            "status": "PASS",
            "required": True,
            "summary": "comparison is within tolerance",
            "reference_id": "paper-1",
            "criterion": {
                "metric": "relative",
                "tolerance": 0.02,
                "scale_floor": 1e-12,
                "unit": "Pa",
            },
            "observed": {
                "shape": [1],
                "actual": 101.0,
                "expected": 100.0,
                "absolute_error": 1.0,
                "relative_error": 0.01,
                # Deliberately inconsistent sentinels prove render-only behavior.
                "gating_error": 999.25,
                "permitted_error": 0.25,
                "l1_error": 1.0,
                "l2_error": 1.0,
                "linf_error": 1.0,
                "unit": "Pa",
            },
        },
        {
            "id": "profile-close",
            "type": "compare",
            "status": "FAIL",
            "required": True,
            "summary": "comparison exceeds tolerance",
            "criterion": {
                "metric": "close",
                "absolute_tolerance": 0.1,
                "relative_tolerance": 0.01,
                "unit": "K",
            },
            "observed": {
                "shape": [100],
                "l1_error": 4.0,
                "l2_error": 2.0,
                "linf_error": 1.5,
                "gating_error": 1.5,
                "permitted_error": 0.2,
                "failing_count": 25,
                "failing_indices": [[index] for index in range(25)],
                "unit": "K",
            },
        },
        {
            "id": "mass-balance",
            "type": "conservation",
            "status": "PASS",
            "required": True,
            "summary": "conservation residual is within tolerance",
            "criterion": {
                "absolute_tolerance": 0.5,
                "relative_tolerance": 0.01,
                "rule": "abs(balance-expected) <= absolute + relative*normalization",
                "unit": "kg",
            },
            "observed": {
                "balance": 300.0,
                "expected": 300.0,
                "absolute_error": 0.0,
                "normalization": 300.0,
                "permitted_error": 3.5,
                "terms": [
                    {
                        "label": f"term-{index}",
                        "coefficient": 1.0,
                        "raw_total": float(index),
                        "contribution": float(index),
                    }
                    for index in range(25)
                ],
                "unit": "kg",
            },
        },
        {
            "id": "errored-check",
            "type": "finite",
            "status": "ERROR",
            "required": True,
            "summary": "selector failed <script>alert('error')</script>",
            "criterion": {},
            "observed": {},
        },
        {
            "id": "skipped-check",
            "type": "exists",
            "status": "SKIP",
            "required": False,
            "summary": "optional artefact was unavailable",
            "criterion": {},
            "observed": {},
        },
    ]
    long_derivation = "Derived directly from Table 7. " + ("long-unbroken-value-" * 170)
    envelope["record"]["references"] = [
        {
            "id": "paper-1",
            "type": "published",
            "citation": "Research <script>alert('citation')</script> source.",
            "locator": "https://example.invalid/paper?x=<unsafe>",
            "artifact": "reference-data",
            "source_location": "Table 7, pressure column",
            "accessed_utc": "2026-08-28",
            "derivation": long_derivation,
            "notes": "No conversion was performed; values are recorded in Pa.",
        }
    ]
    envelope["record"]["artifacts"].append(
        {
            "id": "reference-data",
            "role": "reference",
            "path": "references/paper-1.json",
            "expected_sha256": "a" * 64,
            "package_path": "artifacts/reference/reference-data/paper-1.json",
            "validation_input": {
                "exists": True,
                "sha256": "b" * 64,
                "size_bytes": 1234,
            },
        }
    )
    return envelope


def test_html_rendering_is_byte_deterministic_for_frozen_evidence(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    before = copy.deepcopy(envelope)
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"

    render_html(envelope, first)
    render_html(envelope, second)

    assert first.read_bytes() == second.read_bytes()
    assert envelope == before
    text = first.read_text(encoding="utf-8")
    assert envelope["integrity"]["evidence_id"] in text
    assert envelope["record"]["summary"]["status"] in text
    assert text.startswith("<!doctype html>\n")
    assert "\r\n" not in text


def test_pdf_rendering_is_byte_deterministic_for_frozen_evidence(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    before = copy.deepcopy(envelope)
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"

    render_pdf(envelope, first)
    render_pdf(envelope, second)

    assert first.read_bytes() == second.read_bytes()
    assert envelope == before
    assert first.read_bytes().startswith(b"%PDF-")
    assert envelope["integrity"]["evidence_id"].encode("ascii") in first.read_bytes()


def test_v2_pdf_timestamp_and_bytes_derive_from_evidence_not_environment(
    package_factory, tmp_path: Path, monkeypatch
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    first = tmp_path / "first-environment.pdf"
    second = tmp_path / "second-environment.pdf"

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1000000000")
    render_pdf(envelope, first)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "2000000000")
    render_pdf(envelope, second)

    assert first.read_bytes() == second.read_bytes()
    finished = envelope["record"]["execution"]["finished_at"]
    expected = datetime.fromisoformat(finished.replace("Z", "+00:00")).replace(
        microsecond=0
    )
    assert PdfReader(first).metadata.creation_date == expected


def test_explicit_v1_renderers_remain_byte_stable_for_frozen_envelope(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    before = copy.deepcopy(envelope)
    first_html = tmp_path / "legacy-first.html"
    second_html = tmp_path / "legacy-second.html"
    first_pdf = tmp_path / "legacy-first.pdf"
    second_pdf = tmp_path / "legacy-second.pdf"

    render_html_v1(envelope, first_html)
    render_html_v1(envelope, second_html)
    render_pdf_v1(envelope, first_pdf)
    render_pdf_v1(envelope, second_pdf)

    assert envelope == before
    assert first_html.read_bytes() == second_html.read_bytes()
    assert first_pdf.read_bytes() == second_pdf.read_bytes()
    assert "Check audit appendix" not in first_html.read_text(encoding="utf-8")
    assert "Scientific finding identity" not in "\n".join(
        page.extract_text() for page in PdfReader(first_pdf).pages
    )


def test_pdf_carries_full_identity_metadata_and_short_identity_on_every_page(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    pdf = tmp_path / "report.pdf"
    render_pdf(envelope, pdf)
    reader = PdfReader(pdf)
    evidence_id = envelope["integrity"]["evidence_id"]
    short_id = envelope["integrity"]["digest"][:16]

    assert reader.metadata.subject == evidence_id
    assert reader.metadata.author == "Chatwood Labs Ltd"
    expected_creator = f"Physics Validation Suite {envelope['record']['pvs']['version']}"
    assert reader.metadata.creator == expected_creator
    assert len(reader.pages) >= 2
    page_text = [page.extract_text() for page in reader.pages]
    assert evidence_id in "\n".join(page_text)
    assert all(short_id in text for text in page_text)
    assert all(f"Page {number}" in text for number, text in enumerate(page_text, start=1))


def test_reports_render_declared_evidence_without_recalculating_results(
    package_factory, tmp_path: Path
) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    envelope["record"]["checks"][0]["status"] = "FAIL"
    envelope["record"]["checks"][0]["summary"] = "renderer sentinel - no evaluation"
    envelope["record"]["summary"]["status"] = "FAIL"
    html = tmp_path / "sentinel.html"
    pdf = tmp_path / "sentinel.pdf"

    render_html(envelope, html)
    render_pdf(envelope, pdf)

    assert "renderer sentinel - no evaluation" in html.read_text(encoding="utf-8")
    assert "renderer sentinel - no evaluation" in "\n".join(
        page.extract_text() for page in PdfReader(pdf).pages
    )


def test_pdf_renders_reference_ids_without_exposing_markup(package_factory, tmp_path: Path) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    envelope["record"]["references"] = [
        {
            "id": "published-source-1",
            "citation": "Example citation.",
            "locator": "Example locator.",
            "source_location": "Table 1",
        }
    ]
    pdf = tmp_path / "reference.pdf"

    render_pdf(envelope, pdf)

    text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "published-source-1" in text
    assert "<b>" not in text
    assert "</b>" not in text


def test_html_escapes_untrusted_case_and_check_text(package_factory, tmp_path: Path) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    envelope["record"]["case"]["title"] = '<script>alert("case")</script>'
    envelope["record"]["checks"][0]["summary"] = '<img src=x onerror="alert(1)">'
    output = tmp_path / "escaped.html"

    render_html(envelope, output)

    text = output.read_text(encoding="utf-8")
    assert '<script>alert("case")</script>' not in text
    assert '<img src=x onerror="alert(1)">' not in text
    assert "&lt;script&gt;alert(&quot;case&quot;)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in text
    assert "default-src 'none'" in text


def test_reports_do_not_leak_absolute_case_or_output_paths(package_factory, tmp_path: Path) -> None:
    _, package = package_factory(html=False, pdf=False, embed=True)
    envelope = load_strict(package / "evidence.json")
    html = tmp_path / "paths.html"
    pdf = tmp_path / "paths.pdf"

    render_html(envelope, html)
    render_pdf(envelope, pdf)

    absolute_fragment = str(package.parent)
    assert absolute_fragment not in html.read_text(encoding="utf-8")
    assert absolute_fragment not in "\n".join(page.extract_text() for page in PdfReader(pdf).pages)


def test_shared_audit_projection_is_complete_bounded_and_non_mutating(package_factory) -> None:
    envelope = _rich_audit_envelope(package_factory)
    before = copy.deepcopy(envelope)

    details = project_report_details(envelope)

    assert envelope == before
    expected_finding_id = "pvs-finding:v1:sha256:" + "c" * 64
    assert details.finding.status == "ISSUED"
    assert details.finding.finding_id == expected_finding_id
    assert details.finding.display == expected_finding_id
    assert any(row.label == "Finding ID" for row in details.finding.rows)
    assert [check.id for check in details.checks] == [
        "published-scalar",
        "profile-close",
        "mass-balance",
        "errored-check",
        "skipped-check",
    ]
    scalar = details.checks[0]
    assert scalar.reference_id == "paper-1"
    assert any(row.label == "Unit" and row.value == "Pa" for row in scalar.declaration)
    assert any(row.label == "Gating error" and row.value == "999.25" for row in scalar.observation)
    assert any(row.label == "Permitted error" and row.value == "0.25" for row in scalar.observation)

    array = details.checks[1]
    assert any(row.value == "[0,1,2,3,4,5,6,7,8,9]" for row in array.declaration)
    assert any("90 additional items omitted" in row.value for row in array.declaration)
    assert any("15 additional items omitted" in row.value for row in array.observation)

    conservation = details.checks[2]
    first_term = next(row for row in conservation.observation if row.label == "Terms[1]")
    assert '"contribution":0.0' in first_term.value
    assert any("5 additional items omitted" in row.value for row in conservation.observation)
    for check in details.checks[3:]:
        assert check.criterion[0].value == "No recorded details."
        assert check.observation[0].value == "No recorded details."

    reference = details.references[0]
    reference_values = {row.label: row.value for row in reference.rows}
    assert reference_values["Type"] == "published"
    assert reference_values["Access date (UTC)"] == "2026-08-28"
    assert reference_values["Artefact"] == "reference-data"
    assert reference_values["Artifact identity / SHA-256"] == "b" * 64
    assert reference_values["Artifact identity / Size (bytes)"] == "1234"


def test_html_audit_appendix_contains_recorded_scientific_details_and_escapes_markup(
    package_factory, tmp_path: Path
) -> None:
    envelope = _rich_audit_envelope(package_factory)
    output = tmp_path / "audit.html"

    render_html(envelope, output)

    text = output.read_text(encoding="utf-8")
    finding_id = envelope["integrity"]["finding"]["finding_id"]
    assert "Check audit appendix" in text
    assert text.count(finding_id) >= 3
    for expected in (
        "Declaration",
        "Effective criterion",
        "Recorded observation",
        "Reference ID",
        "paper-1",
        "Actual",
        "Expected",
        "Gating error",
        "999.25",
        "Permitted error",
        "0.25",
        "L1 error",
        "L2 error",
        "Linf error",
        "Failing samples[1]",
        "Terms[1]",
        "No recorded details.",
        "Access date (UTC)",
        "Derivation",
        "Notes",
        "Artifact identity / SHA-256",
    ):
        assert expected in text
    assert "90 additional items omitted" in text
    assert "15 additional items omitted" in text
    assert "5 additional items omitted" in text
    assert "Research <script>" not in text
    assert "selector failed <script>" not in text
    assert "Research &lt;script&gt;alert(&#x27;citation&#x27;)&lt;/script&gt; source." in text
    assert "selector failed &lt;script&gt;alert(&#x27;error&#x27;)&lt;/script&gt;" in text


def test_pdf_audit_appendix_is_extractable_handles_long_fields_and_empty_results(
    package_factory, tmp_path: Path
) -> None:
    envelope = _rich_audit_envelope(package_factory)
    output = tmp_path / "audit.pdf"

    render_pdf(envelope, output)

    reader = PdfReader(output)
    page_text = [page.extract_text() for page in reader.pages]
    text = "\n".join(page_text)
    finding_id = envelope["integrity"]["finding"]["finding_id"]
    assert len(reader.pages) >= 5
    assert all(finding_id in page for page in page_text)
    for expected in (
        "Check audit appendix",
        "published-scalar",
        "Declaration",
        "Effective criterion",
        "Recorded observation",
        "Reference ID",
        "paper-1",
        "Gating error",
        "999.25",
        "Permitted error",
        "0.25",
        "L1 error",
        "L2 error",
        "Linf error",
        "Failing samples[1]",
        "Terms[1]",
        "No recorded details.",
        "Access date (UTC)",
        "Table 7, pressure column",
        "No conversion was performed; values are recorded in Pa.",
        "Artifact identity / SHA-256",
    ):
        assert expected in text
    assert "90 additional items omitted" in text
    assert "15 additional items omitted" in text
    assert "5 additional items omitted" in text
    assert "long-unbroken-value" in text


def test_detailed_reports_are_byte_deterministic_and_do_not_mutate_evidence(
    package_factory, tmp_path: Path
) -> None:
    envelope = _rich_audit_envelope(package_factory)
    before = copy.deepcopy(envelope)
    first_html = tmp_path / "first.html"
    second_html = tmp_path / "second.html"
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"

    render_html(envelope, first_html)
    render_html(envelope, second_html)
    render_pdf(envelope, first_pdf)
    render_pdf(envelope, second_pdf)

    assert envelope == before
    assert first_html.read_bytes() == second_html.read_bytes()
    assert first_pdf.read_bytes() == second_pdf.read_bytes()


def test_reports_state_why_scientific_finding_identity_was_not_issued(
    package_factory, tmp_path: Path
) -> None:
    envelope = _rich_audit_envelope(package_factory)
    envelope["integrity"]["finding"] = {
        "projection": "pvs-finding-projection/1",
        "canonicalization": "RFC8785",
        "algorithm": "sha256",
        "status": "NOT_ISSUED",
        "reason": "ERROR_PRESENT",
    }
    html = tmp_path / "not-issued.html"
    pdf = tmp_path / "not-issued.pdf"

    render_html(envelope, html)
    render_pdf(envelope, pdf)

    expected = "Not issued: ERROR_PRESENT"
    assert html.read_text(encoding="utf-8").count(expected) >= 2
    page_text = [page.extract_text() for page in PdfReader(pdf).pages]
    assert all(expected in page for page in page_text)
    assert "Reason\nERROR_PRESENT" in "\n".join(page_text)
