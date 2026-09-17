"""Dependency-free deterministic HTML evidence rendering."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _row(label: str, value: Any, *, code: bool = False) -> str:
    rendered = escape(_text(value))
    if code:
        rendered = f"<code>{rendered}</code>"
    return f"<tr><th>{escape(label)}</th><td>{rendered}</td></tr>"


def render_html(envelope: dict[str, Any], output_path: Path) -> None:
    record = envelope["record"]
    integrity = envelope["integrity"]
    evidence_id = str(integrity["evidence_id"])
    status = str(record["summary"]["status"])
    checks = record["checks"]
    artifacts = record["artifacts"]
    references = record["references"]

    check_rows = []
    for check in checks:
        check_rows.append(
            "<tr>"
            f"<td><code>{escape(str(check['id']))}</code></td>"
            f"<td>{escape(str(check['type']))}</td>"
            f'<td><span class="state state-{escape(str(check["status"]).lower())}">'
            f"{escape(str(check['status']))}</span></td>"
            f"<td>{escape(str(check['summary']))}</td>"
            "</tr>"
        )

    artifact_rows = []
    for artifact in artifacts:
        final = artifact.get("validation_input") or {}
        artifact_rows.append(
            "<tr>"
            f"<td><code>{escape(str(artifact['id']))}</code></td>"
            f"<td>{escape(str(artifact['role']))}</td>"
            f"<td><code>{escape(str(artifact['path']))}</code></td>"
            f"<td>{'yes' if final.get('exists') else 'no'}</td>"
            f"<td><code>{escape(str(final.get('sha256', '-')))}</code></td>"
            "</tr>"
        )

    reference_blocks = []
    for reference in references:
        reference_blocks.append(
            '<article class="reference">'
            f"<h3>{escape(str(reference['id']))}</h3>"
            f"<p>{escape(str(reference['citation']))}</p>"
            f"<p><strong>Locator:</strong> <code>{escape(str(reference['locator']))}</code></p>"
            f"<p><strong>Source location:</strong> {escape(_text(reference.get('source_location')))}</p>"
            "</article>"
        )

    summary_counts = record["summary"]["counts"]
    count_text = " | ".join(
        f"{name}: {summary_counts.get(name, 0)}"
        for name in ("PASS", "WARN", "FAIL", "ERROR", "SKIP")
    )
    provenance_issues = record["summary"].get("provenance_issues", [])
    issues_html = "".join(f"<li>{escape(str(issue))}</li>" for issue in provenance_issues)
    if not issues_html:
        issues_html = "<li>None. Declared material provenance was complete.</li>"

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>PVS evidence - {escape(str(record["case"]["id"]))}</title>
<style>
:root{{--ink:#172033;--muted:#687386;--line:#d9e0e8;--paper:#fff;--wash:#f4f7fa;--blue:#1f5f91;--cyan:#22a6b3;--pass:#176b3a;--warn:#8b5a00;--fail:#a12424;--error:#6f1d7a;--skip:#566273}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--wash);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1100px;margin:32px auto;background:var(--paper);box-shadow:0 8px 32px #1d2a3a18}}
header{{padding:44px 52px;background:linear-gradient(135deg,#102238,#1f5f91);color:white;border-bottom:5px solid var(--cyan)}}
.brand{{letter-spacing:.12em;text-transform:uppercase;font-size:12px;font-weight:700;color:#bdebf0}} h1{{font-size:34px;line-height:1.15;margin:.4rem 0}}
.identity{{font:12px ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;color:#d8e9f5}}
.status{{display:inline-block;margin-top:18px;padding:6px 13px;border:1px solid #ffffff80;border-radius:999px;font-weight:800;letter-spacing:.06em}}
section{{padding:28px 52px;border-bottom:1px solid var(--line)}} h2{{font-size:21px;margin:0 0 16px;color:var(--blue)}} h3{{font-size:16px;margin:0 0 8px}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
table.kv th{{width:220px}} code{{font:12px ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}}
.state{{font-weight:800}} .state-pass{{color:var(--pass)}} .state-warn{{color:var(--warn)}} .state-fail{{color:var(--fail)}} .state-error{{color:var(--error)}} .state-skip{{color:var(--skip)}}
.reference{{padding:14px 0;border-bottom:1px solid var(--line)}} ul{{margin:0;padding-left:20px}}
footer{{padding:22px 52px;color:var(--muted);font-size:12px}} @media(max-width:700px){{main{{margin:0}}header,section,footer{{padding-left:22px;padding-right:22px}}}}
</style>
</head>
<body><main>
<header>
  <div class="brand">Chatwood Labs - Physics Validation Suite</div>
  <h1>{escape(str(record["case"]["title"]))}</h1>
  <div class="identity">{escape(evidence_id)}</div>
  <div class="status">{escape(status)}</div>
</header>
<section><h2>Evidence summary</h2><table class="kv">
{_row("Case ID", record["case"]["id"], code=True)}
{_row("Case version", record["case"]["version"])}
{_row("Classification", ", ".join(record["case"]["classifications"]))}
{_row("Subject", record["subject"]["name"])}
{_row("Subject version", record["subject"]["version"])}
{_row("Run ID", record["run"]["run_id"], code=True)}
{_row("Mode", record["run"]["mode"])}
{_row("Checks", count_text)}
{_row("Provenance", record["summary"]["provenance_status"])}
</table></section>
<section><h2>Checks</h2><table><thead><tr><th>ID</th><th>Type</th><th>Status</th><th>Finding</th></tr></thead><tbody>{"".join(check_rows)}</tbody></table></section>
<section><h2>Artefacts</h2><table><thead><tr><th>ID</th><th>Role</th><th>Path</th><th>Present</th><th>SHA-256</th></tr></thead><tbody>{"".join(artifact_rows)}</tbody></table></section>
<section><h2>References</h2>{"".join(reference_blocks) if reference_blocks else "<p>No external reference metadata was declared.</p>"}</section>
<section><h2>Execution</h2><table class="kv">
{_row("Started (UTC)", record["execution"]["started_at"])}
{_row("Finished (UTC)", record["execution"]["finished_at"])}
{_row("Duration (s)", record["execution"]["duration_seconds"])}
{_row("Command", record["execution"]["command"], code=True)}
{_row("Return code", record["execution"]["return_code"])}
{_row("Succeeded", record["execution"]["succeeded"])}
</table></section>
<section><h2>Provenance issues</h2><ul>{issues_html}</ul></section>
<section><h2>Integrity</h2><table class="kv">
{_row("Evidence ID", evidence_id, code=True)}
{_row("Digest", integrity["digest"], code=True)}
{_row("Canonicalization", integrity["canonicalization"])}
{_row("Algorithm", integrity["algorithm"])}
{_row("PVS version", record["pvs"]["version"])}
</table><p>This rendering is human-readable. <code>evidence.json</code> is authoritative. Verify the complete package with <code>pvs verify</code>.</p></section>
<footer>PVS evidence {escape(integrity["digest"][:16])} - generated from the authoritative evidence record.</footer>
</main></body></html>"""
    output_path.write_text(html, encoding="utf-8", newline="\n")
