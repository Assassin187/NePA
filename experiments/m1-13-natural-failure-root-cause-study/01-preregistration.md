# M1-13 preregistration

Status: frozen before formal Provider I/O.
Date: 2026-09-11

The JSON block below is the normative machine-readable protocol. Returned model identity is observational only: exact aliases, variation, or absence do not alter configuration equivalence. Provider cached-input pricing is a reference only because current telemetry does not expose trustworthy cached-token counts; NePA's local response-cache hits remain zero incremental cost.

<!-- protocol-json
{
  "schema_version": "1.0",
  "study_id": "m1-13-natural-failure-root-cause-study",
  "configuration_group": "m1-13-g01",
  "group_size": 5,
  "replacement_group": "m1-13-g02",
  "max_replacement_groups": 1,
  "logical_run_ids": ["m1-13-g01-r01", "m1-13-g01-r02", "m1-13-g01-r03", "m1-13-g01-r04", "m1-13-g01-r05"],
  "replacement_logical_run_ids": ["m1-13-g02-r01", "m1-13-g02-r02", "m1-13-g02-r03", "m1-13-g02-r04", "m1-13-g02-r05"],
  "inputs": [
    {"path": "gold_file/specIR.json", "sha256": "a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09"},
    {"path": "gold_file/target.json", "sha256": "efa8dc8fc0914d5b563a1da1aeaad1a7a277b4b161b89893b8efa75d6818b49b"},
    {"path": "gold_file/test_bundle.json", "sha256": "8f77eb4c5a15ef0ee02979240fbcd4eebdf585d5ad023d130de9468e791d343c"}
  ],
  "config_path": "experiments/m1-13-natural-failure-root-cause-study/study-config.yaml",
  "config_sha256": "ca6112142bd2909c331be0f33d26f0629001a91f55a1a2abc3582df4eee75957",
  "config_snapshot_sha256": "a7bc22493a62d5365f6bacffcdacfe00acfe7dfec450d56e2a456d585c010980",
  "sandbox_digest": "sha256:1c44bd40b8d6995fc09088769e86b204539664d4bebd7b9bc77800f2e16ebb92",
  "revision_limits": {"f2": 0, "f3": 0},
  "environment_variable_names": ["NEPA_CLAUDE_API_KEY", "NEPA_DS_API_KEY", "NEPA_QWEN_API_KEY"],
  "requested_routes": {
    "T1": {"provider": "anthropic", "model": "claude-opus-5", "temperature": 0.0, "max_tokens": 16000},
    "T2": {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.1, "max_tokens": 16000},
    "T3": {"provider": "qwen", "model": "qwen3.7-max-2026-06-08", "temperature": 0.0, "max_tokens": 4000}
  },
  "pricing_reference": {
    "accounting_fx_cny_per_usd": 7.2,
    "claude_usd_per_million": {"input": 5.0, "output": 25.0, "provider_cached_input": 0.5},
    "qwen_cny_per_million": {"input": 12.0, "output": 36.0},
    "deepseek_cny_per_million": {"off_peak_input": 1.5, "off_peak_output": 4.5, "peak_input": 3.0, "peak_output": 9.0},
    "provider_cached_tokens_available": false,
    "cost_is_scientific_gate": false
  },
  "sample_unit": "one terminally unsuccessful F0/F1 task identified by (run_id, task_uid)",
  "inclusion_rule": "all terminally unsuccessful F0/F1 tasks from every admitted finalized real run",
  "exclusion_rule": "calibration, synthetic, injected, undeclared, unfinalized, and finalized internal_error runs never enter the natural denominator",
  "configuration_equivalence": "requested inputs, route, provider endpoint, parameters, prompt bundle, sandbox digest, and config are frozen; returned model identity is disclosure-only",
  "cache_policy": "each Run owns its run-local cache; no cross-Run cache is shared; resume reuses only the same Run cache",
  "interruption_policy": "recoverable provider interruption retains call and usage evidence and resumes the same logical Run within existing hard budgets",
  "group_invalidation_policy": "a finalized internal_error invalidates all five members; at most one fresh complete replacement group is allowed",
  "stopping_rule": "stop after one complete valid N=5 group, or after the single replacement group is invalid/persistently blocked; never extend after observing outcomes",
  "command": "uv run nepa run --spec gold_file/specIR.json --target gold_file/target.json --test-bundle gold_file/test_bundle.json --config experiments/m1-13-natural-failure-root-cause-study/study-config.yaml --until s6 --runs-root runs"
}
-->
