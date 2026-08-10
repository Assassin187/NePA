"""Command-line entry point for the M0 lint commands."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from .speclib.lint import lint_spec, lint_target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nepa")
    commands = parser.add_subparsers(dest="command", required=True)
    lint = commands.add_parser("lint")
    lint_commands = lint.add_subparsers(dest="lint_command", required=True)

    spec = lint_commands.add_parser("spec")
    spec.add_argument("path")

    target = lint_commands.add_parser("target")
    target.add_argument("path")
    target.add_argument("--spec", dest="spec_path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.lint_command == "spec":
        report = lint_spec(args.path)
    else:
        report = lint_target(args.path, args.spec_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["valid"] else 1
