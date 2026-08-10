"""Command-line entry point for the M0 lint commands."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from .speclib.lint import lint_spec, lint_target, lint_test_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nepa")
    commands = parser.add_subparsers(dest="command", required=True)
    lint = commands.add_parser("lint")
    lint_commands = lint.add_subparsers(dest="lint_command", required=True)

    spec = lint_commands.add_parser("spec")
    spec.add_argument("path")
    spec.add_argument("--gold", action="store_true")
    spec.add_argument("--manifest", dest="manifest_path")

    target = lint_commands.add_parser("target")
    target.add_argument("path")
    target.add_argument("--spec", dest="spec_path")

    test_bundle = lint_commands.add_parser("test-bundle")
    test_bundle.add_argument("path")
    test_bundle.add_argument("--spec", dest="spec_path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.lint_command == "spec":
        report = lint_spec(args.path, args.gold, args.manifest_path)
    elif args.lint_command == "target":
        report = lint_target(args.path, args.spec_path)
    else:
        report = lint_test_bundle(args.path, args.spec_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["valid"] else 1
