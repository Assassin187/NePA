# M1-13 DeepSeek Pro preregistration amendment

Status: frozen after two Claude groups finalized `internal_error` and before any DeepSeek Pro Provider I/O.
Date: 2026-09-11
Reason: the responsible owner directed the study to stop the Claude batch and restart the complete experiment with `deepseek-v4-pro` if Claude produced another internal interruption. The two superseded Claude runs remain excluded audit evidence and never enter the natural-failure denominator.

DeepSeek Pro pricing uses the provided DeepSeek high-period rate only as a conservative reference estimate; it is not asserted to be a provider bill and is not a scientific or acceptance gate.

<!-- protocol-json
{
  "schema_version": "1.0",
  "study_id": "m1-13-natural-failure-root-cause-study",
  "configuration_group": "m1-13-g03-deepseek-pro",
  "group_size": 5,
  "replacement_group": "m1-13-g04-deepseek-pro",
  "max_replacement_groups": 1,
  "logical_run_ids": ["m1-13-g03-r01", "m1-13-g03-r02", "m1-13-g03-r03", "m1-13-g03-r04", "m1-13-g03-r05"],
  "replacement_logical_run_ids": ["m1-13-g04-r01", "m1-13-g04-r02", "m1-13-g04-r03", "m1-13-g04-r04", "m1-13-g04-r05"],
  "superseded_runs": [
    {"logical_run_id": "m1-13-g01-r01", "group_id": "m1-13-g01", "run_id": "20260911T074906Z_MQTT_spec-run", "config_snapshot_sha256": "a7bc22493a62d5365f6bacffcdacfe00acfe7dfec450d56e2a456d585c010980", "reason": "finalized internal_error after Claude stream ended before DONE"},
    {"logical_run_id": "m1-13-g02-r01", "group_id": "m1-13-g02", "run_id": "20260911T080027Z_MQTT_spec-run", "config_snapshot_sha256": "a7bc22493a62d5365f6bacffcdacfe00acfe7dfec450d56e2a456d585c010980", "reason": "finalized internal_error after Claude returned varying identities within one stream"}
  ],
  "inputs": [
    {"path": "gold_file/specIR.json", "sha256": "a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09"},
    {"path": "gold_file/target.json", "sha256": "efa8dc8fc0914d5b563a1da1aeaad1a7a277b4b161b89893b8efa75d6818b49b"},
    {"path": "gold_file/test_bundle.json", "sha256": "8f77eb4c5a15ef0ee02979240fbcd4eebdf585d5ad023d130de9468e791d343c"}
  ],
  "config_path": "experiments/m1-13-natural-failure-root-cause-study/study-config-deepseek-pro.yaml",
  "config_sha256": "5b5f17fb38fdba30cca43987c19dde69f954d001c211165bc9fb5ac9af9ac394",
  "config_snapshot_sha256": "559955108892283a1cd81494a6cdbaf23a1ffc735bbbb88b96e8846bd5b24109",
  "sandbox_digest": "sha256:1c44bd40b8d6995fc09088769e86b204539664d4bebd7b9bc77800f2e16ebb92",
  "revision_limits": {"f2": 0, "f3": 0},
  "environment_variable_names": ["NEPA_DS_API_KEY", "NEPA_QWEN_API_KEY"],
  "requested_routes": {
    "T1": {"provider": "deepseek", "model": "deepseek-v4-pro", "temperature": 0.0, "max_tokens": 16000},
    "T2": {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.1, "max_tokens": 16000},
    "T3": {"provider": "qwen", "model": "qwen3.7-max-2026-06-08", "temperature": 0.0, "max_tokens": 4000}
  },
  "pricing_reference": {
    "accounting_fx_cny_per_usd": 7.2,
    "claude_usd_per_million": {"input": 5.0, "output": 25.0, "provider_cached_input": 0.5},
    "deepseek_pro_estimate_cny_per_million": {"input": 3.0, "output": 9.0, "basis": "provided DeepSeek high-period reference; not a billed Pro quote"},
    "deepseek_flash_cny_per_million": {"off_peak_input": 1.5, "off_peak_output": 4.5, "peak_input": 3.0, "peak_output": 9.0},
    "qwen_cny_per_million": {"input": 12.0, "output": 36.0},
    "provider_cached_tokens_available": false,
    "cost_is_scientific_gate": false
  },
  "sample_unit": "one terminally unsuccessful F0/F1 task identified by (run_id, task_uid)",
  "inclusion_rule": "all terminally unsuccessful F0/F1 tasks from every admitted finalized DeepSeek Pro real run",
  "exclusion_rule": "superseded Claude groups, calibration, synthetic, injected, undeclared, unfinalized, and finalized internal_error runs never enter the natural denominator",
  "configuration_equivalence": "requested inputs, DeepSeek Pro route, provider endpoint, parameters, prompt bundle, sandbox digest, and config are frozen; returned model identity is disclosure-only",
  "cache_policy": "each Run owns its run-local cache; no cross-Run cache is shared; resume reuses only the same Run cache",
  "interruption_policy": "recoverable provider interruption retains call and usage evidence and resumes the same logical Run within existing hard budgets",
  "group_invalidation_policy": "a finalized internal_error invalidates all five members; at most one fresh complete DeepSeek Pro replacement group is allowed",
  "stopping_rule": "stop after one complete valid DeepSeek Pro N=5 group, or after the single replacement group is invalid/persistently blocked; never extend after observing outcomes",
  "command": "uv run nepa run --spec gold_file/specIR.json --target gold_file/target.json --test-bundle gold_file/test_bundle.json --config experiments/m1-13-natural-failure-root-cause-study/study-config-deepseek-pro.yaml --until s6 --runs-root runs"
}
-->
