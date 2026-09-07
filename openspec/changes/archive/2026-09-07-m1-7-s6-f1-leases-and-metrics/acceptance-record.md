# M1-7 acceptance record

Refreshed after the correctness repair and final acceptance run on 2026-09-07. This is a derived change artifact.

## Regression and recovery evidence

- Full test suite: `575 passed` (`.venv/bin/pytest -q`).
- Focused S6/lease/metric collection: `48/575 tests`；final lease gate `18 passed` and metric gate `8 passed` after the last recovery checks.
- Schema, contract and repaired execution selection: `75 tests` in the final collected set.
- Both MQTT/non-MQTT Docker E2E cases, all joint recovery windows, allocation sub-write recovery, current-only/leased-only F1 subsets and production-shaped run metrics passed.
- The focused contract suite covers Plan State, revision/RunStore allocation, Agent context/candidate scope, Git trailers, joint publication, and recovery replay.

## Static and contract gates

- `uv run ruff check .` — passed.
- `uv run mypy nepa` — passed.
- Gold/spec, target/spec, and test-bundle lint — passed.
- `openspec validate m1-7-s6-f1-leases-and-metrics --strict` — passed.
- `openspec validate --all --strict` — 15 passed.
- `git diff --check` — passed.

## Scope audit

- `project_docs/` contains only the user-authorized sequential State-history and output-path call-reference clarification; frozen gold/non-MQTT inputs and ArchitecturePlanner lineage are unchanged.
- The only lease entry point is the internal authorization provider; absent or invalid authorization follows the existing F0 path.
- Lease publication/recovery has one WAL/reconciler path, one joint commit path, and idempotent typed start/finish events. Recovery never replays Agent, build, smoke, or commit after a durable commit.
- No public lease CLI option, `nepa eval runs`, TR/F2/F3/E1+/AMEND/REVALIDATE/group/M2 producer, or later owner-signature gate was added. Metric fixtures consume future typed records only.

## Artifact coverage

- Lease authorization and Joint Evidence schemas/examples, normal/lease WAL union, S6 attempt/evidence/revision contracts.
- Authorization/allocation/context/candidate validation, member evidence, Joint Evidence, State/file-ledger/event projection, joint Git commit, and pre/post-commit recovery.
- Pure M1 metrics, read-only run-directory adapter, fixed metric cases, MQTT/non-MQTT lease fixtures, and deterministic fixture regeneration tests.
