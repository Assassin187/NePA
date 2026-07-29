"""Collect gold pytest nodeids and REQ markers into tests_manifest.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from nepa.canonical import atomic_write_canonical_json

ROOT = Path(__file__).resolve().parent


class Collector:
    def __init__(self) -> None:
        self.tests: list[dict[str, Any]] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        for item in session.items:
            path = item.nodeid.replace("\\", "/")
            layer = next(
                (
                    name.split("_", 1)[0]
                    for name in ("l0_static", "l1_codec", "l2_behavior", "l3_interop")
                    if f"/{name}/" in f"/{path}"
                ),
                "unknown",
            )
            req_ids = sorted({str(mark.args[0]) for mark in item.iter_markers("req") if mark.args})
            gates = {str(mark.args[0]) for mark in item.iter_markers("gate") if mark.args}
            contracts = sorted(
                {str(mark.args[0]) for mark in item.iter_markers("contract") if mark.args}
            )
            build_variants = sorted(
                {str(mark.args[0]) for mark in item.iter_markers("build_variant") if mark.args}
            )
            if len(gates) != 1:
                raise ValueError(f"{item.nodeid}: 必须显式声明且只能声明一个 gate")
            if not contracts:
                raise ValueError(f"{item.nodeid}: 必须显式声明 required contract")
            function = getattr(item, "function", None)
            doc = getattr(function, "__doc__", "") or ""
            description = doc.strip().splitlines()[0] if doc.strip() else item.name
            self.tests.append(
                {
                    "nodeid": item.nodeid,
                    "layer": layer,
                    "req_ids": req_ids,
                    "description": description,
                    "gate": gates.pop(),
                    "required_contracts": contracts,
                    **(
                        {"build_variant_ids": build_variants}
                        if build_variants
                        else {}
                    ),
                }
            )


def collect() -> list[dict[str, Any]]:
    collector = Collector()
    code = pytest.main(
        [str(ROOT / "tests"), "--collect-only", "-q", "-p", "no:cacheprovider"],
        plugins=[collector],
    )
    if code != pytest.ExitCode.OK:
        raise SystemExit(int(code))
    return sorted(collector.tests, key=lambda item: item["nodeid"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tests_manifest.json",
        help="Destination JSON path.",
    )
    args = parser.parse_args()
    payload = {"schema_version": "2.0", "tests": collect()}
    schema = json.loads(
        (ROOT.parent.parent / "nepa" / "schemas" / "tests-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(payload)
    atomic_write_canonical_json(args.output, payload)
    print(f"wrote {len(payload['tests'])} tests to {args.output}")


if __name__ == "__main__":
    main()
