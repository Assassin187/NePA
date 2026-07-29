"""M1-4a ArchitecturePlanner N=20 production-shaped bring-up."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nepa.agents.base import AgentRunner
from nepa.agents.contracts import architecture_draft_schema
from nepa.agents.roles import RoleRegistry
from nepa.architecture import ArchValidationReport, arch_validate
from nepa.bringup import architecture_candidate_fingerprint
from nepa.canonical import atomic_write_canonical_json, canonical_sha256
from nepa.config import PricingEntry, load_config
from nepa.delivery import build_planning_index, compile_delivery_constraints
from nepa.llm.client import LLMRequest, LLMResponse, StructuredOutputError
from nepa.llm.factory import LLMFactory
from nepa.profile_build import build_default_assets
from nepa.tools.fs_ops import atomic_write_text

_SYSTEM = (
    "You are a stateless NePA agent. Use only the explicitly delimited input. "
    "Do not rely on protocol knowledge that is absent from the input."
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path}: root must be object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ref(batch_dir: Path, path: Path) -> dict[str, str]:
    return {"path": path.relative_to(batch_dir).as_posix(), "sha256": _sha(path)}


def _issues(report: ArchValidationReport) -> list[dict[str, str]]:
    return [
        {
            "gate": item.gate,
            "code": item.code,
            "path": item.path,
            "message": item.message,
        }
        for item in report.issues
    ]


def aggregate_trials(trials: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(trials)
    if n == 0:
        raise ValueError("at least one trial is required")

    def count(key: str) -> int:
        return sum(bool(item[key]) for item in trials)

    gate_names = [f"arch_{index:02d}" for index in range(1, 13)]
    gate_pass_counts = {
        gate: sum(bool(item["first_gate_results"].get(gate, False)) for item in trials)
        for gate in gate_names
    }
    cooccurrence: Counter[str] = Counter()
    for trial in trials:
        failed = sorted(
            gate for gate in gate_names if not trial["first_gate_results"].get(gate, False)
        )
        for left_index, left in enumerate(failed):
            for right in failed[left_index + 1 :]:
                cooccurrence[f"{left}+{right}"] += 1
    calls = [call for trial in trials for call in trial["calls"]]
    return {
        "schema_version": "1.0",
        "trial_count": n,
        "schema_first_pass_count": count("schema_first_pass"),
        "schema_first_pass_rate": count("schema_first_pass") / n,
        "schema_after_format_repair_count": count("schema_candidate_available"),
        "schema_after_format_repair_rate": count("schema_candidate_available") / n,
        "arch_raw_first_pass_count": count("arch_raw_first_pass"),
        "arch_raw_first_pass_rate": count("arch_raw_first_pass") / n,
        "arch_semantic_first_pass_count": count("arch_semantic_first_pass"),
        "arch_semantic_first_pass_rate": count("arch_semantic_first_pass") / n,
        "arch_pass_with_one_repair_count": count("arch_pass_with_one_repair"),
        "arch_pass_with_one_repair_rate": count("arch_pass_with_one_repair") / n,
        "gate_pass_counts": gate_pass_counts,
        "gate_pass_rates": {gate: value / n for gate, value in gate_pass_counts.items()},
        "failure_cooccurrence": dict(sorted(cooccurrence.items())),
        "usage": {
            "logical_call_count": len(calls),
            "tokens_in": sum(int(call["tokens_in"]) for call in calls),
            "tokens_out": sum(int(call["tokens_out"]) for call in calls),
            "cost_usd": round(sum(float(call["cost_usd"]) for call in calls), 8),
            "latency_ms": sum(int(call["latency_ms"]) for call in calls),
            "finish_reasons": dict(
                sorted(Counter(str(call.get("finish_reason")) for call in calls).items())
            ),
        },
    }


def _call_record(response: LLMResponse, *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "validation": response.validation,
        "model": response.model,
        "parameter_support": response.parameter_support,
        "finish_reason": response.provider_metadata.get("finish_reason"),
        "tokens_in": response.tokens_in,
        "tokens_out": response.tokens_out,
        "cost_usd": response.cost_usd,
        "latency_ms": response.latency_ms,
        "raw_call_count": len(response.provider_calls),
    }


def reconcile_spike_evidence(
    batch_dir: str | Path,
    pricing: Mapping[str, PricingEntry],
) -> dict[str, Any]:
    """按实际响应模型重算历史 spike 成本并封存请求/响应模型对账。

    所有 trial 必须有已登记价格；未知实际模型不能再静默记为零成本。
    """
    directory = Path(batch_dir)
    batch = _load(directory / "batch.json")
    fingerprint = dict(batch["fingerprint"])
    requested_model = fingerprint.pop(
        "model",
        fingerprint.get("requested_model"),
    )
    if not isinstance(requested_model, str):
        raise TypeError("spike fingerprint missing requested model")

    trials: list[dict[str, Any]] = []
    for validation_path in sorted((directory / "trials").glob("trial_*/validation.json")):
        trial = _load(validation_path)
        calls = trial.get("calls")
        if not isinstance(calls, list):
            raise TypeError(f"{validation_path}: calls must be an array")
        for call in calls:
            if not isinstance(call, dict):
                raise TypeError(f"{validation_path}: call must be an object")
            actual_model = call.get("model")
            if not isinstance(actual_model, str) or actual_model not in pricing:
                raise RuntimeError(
                    f"{validation_path}: pricing missing for response model {actual_model!r}"
                )
            price = pricing[actual_model]
            call["cost_usd"] = round(
                int(call["tokens_in"]) / 1_000_000 * price.input
                + int(call["tokens_out"]) / 1_000_000 * price.output,
                8,
            )
        atomic_write_canonical_json(validation_path, trial)
        trials.append(trial)
    if not trials:
        raise RuntimeError("spike batch has no validation trials")

    response_models = sorted(
        {
            str(call["model"])
            for trial in trials
            for call in trial["calls"]
        }
    )
    fingerprint["requested_model"] = requested_model
    fingerprint["response_models"] = response_models
    fingerprint["response_model_pricing_usd_per_mtok"] = {
        model: {
            "input": pricing[model].input,
            "output": pricing[model].output,
        }
        for model in response_models
    }
    fingerprint["model_reconciliation"] = (
        "exact" if response_models == [requested_model] else "mismatch"
    )
    atomic_write_canonical_json(
        directory / "artifacts" / "candidate_fingerprint.json",
        fingerprint,
    )

    report = aggregate_trials(trials)
    report["batch_id"] = batch["batch_id"]
    report["fingerprint"] = fingerprint
    atomic_write_canonical_json(directory / "spike_report.json", report)
    batch["fingerprint"] = fingerprint
    batch["completed_trials"] = len(trials)
    batch["spike_report_ref"] = _ref(directory, directory / "spike_report.json")
    atomic_write_canonical_json(directory / "batch.json", batch)
    return report


def run_architecture_spike(
    workspace_root: str | Path,
    *,
    trial_count: int = 20,
) -> Path:
    root = Path(workspace_root).resolve()
    config = load_config(root / "configs" / "default.yaml")
    target_path, language_path, bundle_path = build_default_assets(root)
    spec_path = root / "golds" / "mqtt-3.1.1-min" / "spec" / "spec.json"
    manifest_path = root / "golds" / "mqtt-3.1.1-min" / "tests_manifest.json"
    spec, target, language, bundle, manifest = (
        _load(spec_path),
        _load(target_path),
        _load(language_path),
        _load(bundle_path),
        _load(manifest_path),
    )
    constraints = compile_delivery_constraints(spec, target, language, bundle, manifest)
    index = build_planning_index(
        spec,
        constraints,
        manifest,
        estimated_input_tokens=0,
        output_tokens_reserved=config.tiers["T1"].max_tokens,
        context_limit=64000,
        safety_margin_tokens=9600,
    )
    preliminary_payload = {
        "planning_index": index,
        "delivery_constraints": constraints,
    }
    estimated = max(1, len(json.dumps(preliminary_payload, ensure_ascii=False)) // 4)
    index = build_planning_index(
        spec,
        constraints,
        manifest,
        estimated_input_tokens=estimated,
        output_tokens_reserved=config.tiers["T1"].max_tokens,
        context_limit=64000,
        safety_margin_tokens=9600,
    )
    if not index["preflight"]["fits"]:
        raise RuntimeError("PLAN_CONTEXT_TOO_LARGE")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parent = root / "runs" / "_bringup" / "s4-architecture"
    batch_dir = parent / stamp
    suffix = 1
    while batch_dir.exists():
        batch_dir = parent / f"{stamp}-{suffix}"
        suffix += 1
    (batch_dir / "trials").mkdir(parents=True)

    registry = RoleRegistry(config)
    role = registry.resolve("architecture_planner")
    factory = LLMFactory(config, batch_dir, batch_dir.name)
    runner = AgentRunner(registry, factory)
    schema = architecture_draft_schema()
    payload = {"planning_index": index, "delivery_constraints": constraints}
    fingerprint = architecture_candidate_fingerprint(
        planning_index=index,
        delivery_constraints=constraints,
        provider=role.provider,
        requested_model=role.model,
        config_snapshot_sha256=canonical_sha256(config.config_snapshot()),
    )
    artifacts = batch_dir / "artifacts"
    artifacts.mkdir()
    for name, value in (
        ("planning_index.json", index),
        ("delivery_constraints.json", constraints),
        ("candidate_fingerprint.json", fingerprint),
    ):
        atomic_write_canonical_json(artifacts / name, value)
    batch = {
        "schema_version": "1.0",
        "batch_id": batch_dir.name,
        "status": "running",
        "trial_count": trial_count,
        "assets": {
            "spec": {"path": spec_path.relative_to(root).as_posix(), "sha256": _sha(spec_path)},
            "target": {"path": target_path.relative_to(root).as_posix(), "sha256": _sha(target_path)},
            "language": {
                "path": language_path.relative_to(root).as_posix(),
                "sha256": _sha(language_path),
            },
            "test_bundle": {
                "path": bundle_path.relative_to(root).as_posix(),
                "sha256": _sha(bundle_path),
            },
        },
        "fingerprint": fingerprint,
    }
    atomic_write_canonical_json(batch_dir / "batch.json", batch)

    trials: list[dict[str, Any]] = []
    try:
        client = factory.client_for(role)
        for number in range(1, trial_count + 1):
            trial_dir = batch_dir / "trials" / f"trial_{number:03d}"
            trial_dir.mkdir()
            request_path = trial_dir / "request.json"
            atomic_write_canonical_json(request_path, payload)
            prompt = runner.render_prompt("architecture_planner", payload, schema)
            request = LLMRequest(
                role=role.name,
                tier=role.tier,
                system=_SYSTEM,
                user=prompt,
                json_schema=schema,
                temperature=role.temperature,
                max_tokens=role.max_tokens,
            )
            calls: list[dict[str, Any]] = []
            response: LLMResponse | None = None
            candidate: dict[str, Any] | None = None
            try:
                response = client.complete(
                    request,
                    stage="S4",
                    attempt=number,
                    use_cache=False,
                )
                calls.append(_call_record(response, kind="architecture"))
                candidate = response.parsed
            except StructuredOutputError as exc:
                if exc.response is not None:
                    calls.append(_call_record(exc.response, kind="architecture"))
                    atomic_write_text(trial_dir / "response.txt", exc.response.text)
                first_report = None
            else:
                assert candidate is not None
                response_path = trial_dir / "response.json"
                atomic_write_canonical_json(response_path, candidate)
                first_report = arch_validate(
                    candidate,
                    spec=spec,
                    target=target,
                    constraints=constraints,
                    planning_index=index,
                )

            repair_report: ArchValidationReport | None = None
            if (
                candidate is not None
                and first_report is not None
                and not first_report.ok
                and config.budgets.plan_architecture_repairs > 0
            ):
                repair_payload = {
                    **payload,
                    "previous_candidate": candidate,
                    "semantic_validation_errors": _issues(first_report),
                    "instruction": "Repair only the listed semantic validation failures.",
                }
                repair_request_path = trial_dir / "repair_request.json"
                atomic_write_canonical_json(repair_request_path, repair_payload)
                repair_prompt = runner.render_prompt(
                    "architecture_planner",
                    repair_payload,
                    schema,
                )
                repair_request = request.model_copy(update={"user": repair_prompt})
                try:
                    repaired = client.complete(
                        repair_request,
                        stage="S4",
                        attempt=number,
                        use_cache=False,
                    )
                    calls.append(_call_record(repaired, kind="semantic_repair"))
                    if repaired.parsed is not None:
                        atomic_write_canonical_json(
                            trial_dir / "repaired_response.json",
                            repaired.parsed,
                        )
                        repair_report = arch_validate(
                            repaired.parsed,
                            spec=spec,
                            target=target,
                            constraints=constraints,
                            planning_index=index,
                        )
                except StructuredOutputError as exc:
                    if exc.response is not None:
                        calls.append(_call_record(exc.response, kind="semantic_repair"))
                        atomic_write_text(
                            trial_dir / "repaired_response.txt",
                            exc.response.text,
                        )

            validation = {
                "schema_version": "1.0",
                "trial": number,
                "request_ref": _ref(batch_dir, request_path),
                "schema_first_pass": bool(response and response.validation == "pass"),
                "schema_candidate_available": candidate is not None,
                "arch_raw_first_pass": bool(
                    response and response.validation == "pass" and first_report and first_report.ok
                ),
                "arch_semantic_first_pass": bool(first_report and first_report.ok),
                "arch_pass_with_one_repair": bool(
                    (first_report and first_report.ok) or (repair_report and repair_report.ok)
                ),
                "first_gate_results": (
                    first_report.gate_results
                    if first_report is not None
                    else {f"arch_{index:02d}": False for index in range(1, 13)}
                ),
                "first_issues": _issues(first_report) if first_report is not None else [],
                "repair_issues": _issues(repair_report) if repair_report is not None else [],
                "calls": calls,
            }
            atomic_write_canonical_json(trial_dir / "validation.json", validation)
            trials.append(validation)
            print(
                f"trial {number:02d}/{trial_count}: "
                f"schema={validation['schema_candidate_available']} "
                f"first={validation['arch_semantic_first_pass']} "
                f"repair={validation['arch_pass_with_one_repair']}",
                flush=True,
            )
            if sum(float(call["cost_usd"]) for item in trials for call in item["calls"]) >= (
                config.budgets.max_cost_usd
            ):
                raise RuntimeError("spike max_cost_usd exhausted")
    except BaseException:
        batch["status"] = "infrastructure_invalid"
        batch["completed_trials"] = len(trials)
        atomic_write_canonical_json(batch_dir / "batch.json", batch)
        raise
    finally:
        factory.close()

    reconcile_spike_evidence(batch_dir, config.pricing)
    batch = _load(batch_dir / "batch.json")
    batch["status"] = "complete"
    atomic_write_canonical_json(batch_dir / "batch.json", batch)
    return batch_dir


def main() -> None:
    path = run_architecture_spike(Path.cwd())
    print(path)


if __name__ == "__main__":
    main()
