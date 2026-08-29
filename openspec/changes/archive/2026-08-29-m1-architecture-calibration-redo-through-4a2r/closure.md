# Closure record

## Final disposition

This change is terminated and sealed as implementation-complete with an unmet quality target. The bounded implementation, deterministic tests, live evidence collection, recomputation, report generation and handoff publication are complete. The original prompt-calibration quality objective is not achieved and must not be described as achieved.

The new lineage is `966954e7865b97339a123d30f558fe882a09bac8d00dcf30ead1ea2edae434bf`. The terminal development result is V1, selected as the unique fixed fallback winner. It did not pass the `p1 >= 0.80` screen: V1 p1 is approximately Qwen `0.2`, Claude `0.6`, and DeepSeek `0.2`. This is a relative fallback choice among failed candidates, not a quality-gate win. The selected repository prompt and V1 snapshot both have SHA-256 `cb34eacad305d20962a413dd9a83fe149f0a375fa3fc5526a74935b6093e5e7f`.

## Required blockers and deviations

1. `arch_15` has a tokenization/whitelist inconsistency for underscore identifiers derived from the Spec. A legal path can therefore be rejected. This remains unresolved and is not repaired by this closure.
2. V2 Claude `trial_010` was completed through the explicitly authorized single-slot retry exception. The original V2 N=10 attempt retained its invalid evidence, but the supplemental retry did not recreate the design-required coherent three-model slot lineage. Consequently, the V1 final choice is not strictly compliant with the original three-model complete-lineage experiment protocol. The exception is preserved in `v2/extensions/n010/slot-retry-001/exception.json`; the original invalid Claude report remains retained as audit evidence.

The two items above are quality/protocol risks, not reasons to start another lineage in this change. They are intentionally not fixed here. Any repair or new calibration must be a separate change with its own lineage and authorization.

## Recovery and scope

Conditional recovery was not triggered. `results/recovery-status.json` records `status=not_triggered`, `provider_calls=0`, and `recovery_root_created=false`; no recovery root was created. Tasks 6.7–6.9 therefore remain unchecked and are explicitly marked not applicable in `tasks.md`, rather than being represented as executed work.

The handoff is technical evidence only. It does not claim M1-4a3 qualification: N=10 qualification, B1–B4, production model/call-shape/budget freeze, formal Run, S4/S5/S6, and production freeze/signature remain out of scope and unsatisfied. No new prompt optimization or experiment lineage is authorized by this closure.

## Evidence and validation

The tracked development summary/report, selection, handoff, preregistration, configuration provenance and recovery status are retained. Raw request/response/trial leaves remain under the lineage-relative `runs/_calibration/` evidence store according to the project’s existing gitignore policy and are referenced by hash from the tracked artifacts. The source prompt, V1 snapshot, selection record and report were rechecked for consistency.

The final validation record is:

- focused change tests: passed;
- full test suite: `348 passed`;
- `openspec validate --all --strict`: passed (`6 passed, 0 failed`);
- `git diff --check`: passed;
- live credentials: used only through `run_m1_calibration.sh`; no credential values are recorded in tracked evidence.

This closure records the end of this round of work, not satisfaction of the original calibration target.
