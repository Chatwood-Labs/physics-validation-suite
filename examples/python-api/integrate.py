"""Minimal example of embedding PVS in another Python application."""

from pathlib import Path

from pvs import validate_case, verify_target

case = Path(__file__).parents[1] / "json-existing" / "pvs.yaml"
outcome = validate_case(case, output_dir="pvs-api-evidence")
if outcome.status.value != "PASS":
    raise SystemExit(f"scientific validation status was {outcome.status.value}, not PASS")
verification = verify_target(outcome.output_directory)
if not verification.valid:
    raise SystemExit("evidence package did not verify")
print(outcome.evidence_id)
