"""Command-line interface for Physics Validation Suite."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from .api import RunOutcome, run_case, validate_case
from .case import load_case
from .errors import CaseError, IntegrityError, PVSError
from .jsonutil import load_strict
from .models import Status
from .verify import VerificationResult, _verify_target, verify_target
from .version import __version__


class _CLIUsageError(Exception):
    """Argument parsing failed without argparse writing unstructured output."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _CLIUsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="pvs",
        description="Portable scientific validation and verification evidence.",
    )
    parser.add_argument("--version", action="version", version=f"PVS {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="inspect a case or evidence record")
    inspect_parser.add_argument("target")
    inspect_parser.add_argument("--json", action="store_true", dest="json_output")

    for name, help_text in (
        ("validate", "validate existing artefacts without executing the subject"),
        ("run", "execute the declared command and validate its artefacts"),
    ):
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.add_argument("case")
        command_parser.add_argument("-o", "--output", dest="output_dir")
        command_parser.add_argument("--json", action="store_true", dest="json_output")
        command_parser.add_argument("--fail-on-warn", action="store_true")
        embed_group = command_parser.add_mutually_exclusive_group()
        embed_group.add_argument("--embed-artifacts", action="store_true", dest="embed")
        embed_group.add_argument("--no-embed-artifacts", action="store_false", dest="embed")
        command_parser.set_defaults(embed=None)
        command_parser.add_argument("--no-html", action="store_true")
        command_parser.add_argument("--no-pdf", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="verify record or package integrity")
    verify_parser.add_argument("target")
    verify_parser.add_argument("--expect-evidence-id")
    verify_parser.add_argument("--expect-finding-id")
    verify_parser.add_argument("--expect-package-id")
    verify_parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _outcome_exit(outcome: RunOutcome, fail_on_warn: bool) -> int:
    if outcome.status is Status.ERROR:
        return 3
    if outcome.status is Status.FAIL or (outcome.status is Status.WARN and fail_on_warn):
        return 1
    return 0


def _render_outcome(outcome: RunOutcome, json_output: bool) -> None:
    if json_output:
        _print_json(outcome.as_dict())
        return
    print(f"PVS {outcome.status.value}")
    print(f"Evidence ID: {outcome.evidence_id}")
    if outcome.finding_id:
        print(f"Finding ID:  {outcome.finding_id}")
    print(f"Package ID:  {outcome.package_id}")
    print(f"Output:      {outcome.output_directory}")


def _render_verification(result: VerificationResult, json_output: bool) -> None:
    if json_output:
        _print_json(result.as_dict())
        return
    if not result.valid:
        banner = "PVS INTEGRITY FAILED"
    elif result.trust == "package-pinned":
        banner = "PVS PACKAGE IDENTITY PINNED"
    elif result.trust == "record-pinned":
        banner = "PVS EVIDENCE IDENTITY PINNED"
    elif result.trust == "finding-pinned":
        banner = "PVS SCIENTIFIC FINDING PINNED"
    else:
        banner = f"PVS {result.level.upper()} INTERNALLY CONSISTENT"
    print(banner)
    if result.evidence_id:
        print(f"Evidence ID: {result.evidence_id}")
    if result.finding_id:
        print(f"Finding ID:  {result.finding_id}")
    if result.package_id:
        print(f"Package ID:  {result.package_id}")
    if result.evidence_status:
        print(f"Evidence:    {result.evidence_status}")
    print(f"Trust:       {result.trust}")
    for check in result.checks:
        marker = "+" if check["status"] == "PASS" else "x"
        print(f"[{marker}] {check['name']}: {check['status']} - {check['detail']}")


def _inspect(target: str) -> dict[str, Any]:
    path = Path(target)
    evidence_path = (
        path / "evidence.json" if path.is_dir() and (path / "evidence.json").is_file() else path
    )
    if evidence_path.is_file() and evidence_path.suffix.lower() == ".json":
        try:
            value = load_strict(evidence_path)
        except (OSError, UnicodeError, ValueError):
            value = None
        if isinstance(value, dict) and value.get("schema") in {
            "pvs-evidence/1",
            "pvs-evidence/2",
            "pvs-evidence/3",
        }:
            verification_target = path if path.is_dir() else evidence_path
            # The first read only identifies the input format. Display the exact
            # record checked inside verification's snapshot, never that first read.
            acquired = _verify_target(verification_target)
            verification = acquired.verification
            record = acquired.record
            if not verification.valid or record is None:
                raise IntegrityError("cannot inspect an evidence record with failed integrity")
            return {
                "kind": "evidence",
                "case_id": record["case"]["id"],
                "case_version": record["case"]["version"],
                "subject": record["subject"],
                "status": record["summary"]["status"],
                "evidence_id": verification.evidence_id,
                "finding_id": verification.finding_id,
                "run_id": record["run"]["run_id"],
                "checks": record["summary"]["counts"],
                "integrity_valid": True,
                "verification_level": verification.level,
                "trust": verification.trust,
            }
    case = load_case(path)
    return {
        "kind": "case",
        "case_id": case.id,
        "case_version": case.version,
        "title": case.data["title"],
        "classifications": case.data["classifications"],
        "subject": case.data["subject"],
        "artifacts": sorted(case.data["artifacts"]),
        "checks": [item["id"] for item in case.data["checks"]],
        "raw_sha256": case.raw_identity["sha256"],
        "semantic_sha256": case.semantic_sha256,
    }


def _render_inspect(value: dict[str, Any], json_output: bool) -> None:
    if json_output:
        _print_json(value)
        return
    print(f"PVS {value['kind'].upper()}")
    for key, item in value.items():
        if key == "kind":
            continue
        print(f"{key.replace('_', ' ').title()}: {item}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in raw_args
    try:
        args = parser.parse_args(raw_args)
    except _CLIUsageError as exc:
        if json_requested:
            _print_json({"status": "ERROR", "kind": "usage", "message": str(exc)})
        else:
            parser.print_usage(file=sys.stderr)
            print(f"pvs: error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.command == "inspect":
            _render_inspect(_inspect(args.target), args.json_output)
            return 0
        if args.command == "verify":
            result = verify_target(
                args.target,
                expect_evidence_id=args.expect_evidence_id,
                expect_finding_id=args.expect_finding_id,
                expect_package_id=args.expect_package_id,
            )
            _render_verification(result, args.json_output)
            return 0 if result.valid else 4
        operation = run_case if args.command == "run" else validate_case
        outcome = operation(
            args.case,
            output_dir=args.output_dir,
            embed_artifacts=args.embed,
            render_html_report=False if args.no_html else None,
            render_pdf_report=False if args.no_pdf else None,
        )
        _render_outcome(outcome, args.json_output)
        return _outcome_exit(outcome, args.fail_on_warn)
    except CaseError as exc:
        if getattr(args, "json_output", False):
            _print_json({"status": "ERROR", "kind": "case", "message": str(exc)})
        else:
            print(f"PVS case error: {exc}", file=sys.stderr)
        return 2
    except IntegrityError as exc:
        if getattr(args, "json_output", False):
            _print_json({"status": "ERROR", "kind": "integrity", "message": str(exc)})
        else:
            print(f"PVS integrity error: {exc}", file=sys.stderr)
        return 4
    except PVSError as exc:
        if getattr(args, "json_output", False):
            _print_json({"status": "ERROR", "kind": "runtime", "message": str(exc)})
        else:
            print(f"PVS error: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        if json_requested:
            _print_json({"status": "ERROR", "kind": "interrupted", "message": "PVS interrupted"})
        else:
            print("PVS interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        if getattr(args, "json_output", False):
            _print_json(
                {
                    "status": "ERROR",
                    "kind": "internal",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            print(f"PVS internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
