# P0/P2 experiment drivers

Design11 (`351ff20`) and the Ultraplan's top Accepted decisions govern this harness.
No production code, gold inputs, historical study, existing tests or paid run is
changed by installing these files. Run commands from `/home/ljf/NePA`, with the
repository environment installed. The implementation assignment requested Astra high;
the harness does not select the implementation agent's model or reasoning effort.

The new files span the collector, harness, public fixtures and one test module
because historical profiling and real bounded session/generation gates have distinct
inputs. They reuse production state, accounting, context, executor and orchestration.

## Read-only P0 collection

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python experiments/p0-p2/baseline.py --output experiments/p0-p2/baseline-new
```

Output must be a new directory outside historical runs and the existing inventory.
The three inventory JSON files are source locators, not metrics to blindly copy.
`original-integrity-before.json` is never overwritten or mistaken for a run index.
The collector uses request/response/error/action evidence and outer execution spans,
splits host builds/checks from agent commands and final export, retains task/model/
tool breakdowns and original USD/CNY settlement, and reports missing timing. Format
failure time/cost is a subset of model time/cost. Non-LLM intervals include tools;
they are not assumed to be suspension. No filesystem mtime is used for timing.
First-run USD cannot be precisely repriced without original starts/cache detail.
Host-check duration for all three historical runs remains legacy-readable evidence.
The current AgentAction1 schema digest accompanies the retrospective format analysis.

## Offline checks

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_p0_p2_experiments.py --basetemp experiments/p0-p2/.test-tmp
```

Tests use scripted providers and a **local compiler only for authored test fixtures**,
plus a loopback C service and actual private assertion, while exercising CodingSession, CodingContext, WorkspaceTools,
BuildRunner, decoder and RunStore. They prove harness behavior, never paid model
capability, Docker isolation or MQTT generation. Live commands use SandboxExecutor.

## Explicit paid stages (main reviewer executes later)

Use the provider owner's reviewed Qwen Config3 file. `prepare` requires the configured
Docker image to exist, reads its immutable ID, prepares eight short wires without I/O,
and freezes samples, both exact IDs, local schema, suite, runtime, inputs, prices and
resolved phase settings before payment. Original YAML bytes and the dated
`qwen-capability-audit.md` are copied and hashed alongside their original paths;
comments and price sources are preserved. Its result is `status: false`, `prepared:
true`; collecting/preparing is not acceptance. All phase calls share
`runs/qwen-e2e`, with RunStore campaign locking and cumulative CNY5 capability /
CNY5 public-tools caps inside CNY300. Generation retains at most CNY20/four hours.
No second budget ledger, model fallback, direct HTTP client or implicit retry command
exists. Production LLMClient's bounded transport attempts remain charged. The CLI
campaign root is explicit in `RunStore.initialize(CAMPAIGN, ...)`; no duplicate
`campaign.runs_root` config field is used. For the production CLI, pass
`--runs-root runs/qwen-e2e` explicitly.

```bash
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py prepare --batch experiments/p0-p2/reviewed-batch --config configs/qwen-p0-p2.yaml
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py capability --batch experiments/p0-p2/reviewed-batch --paid
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py public-tools --batch experiments/p0-p2/reviewed-batch --paid
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py private-repair --batch experiments/p0-p2/reviewed-batch --paid
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py freeze --batch experiments/p0-p2/reviewed-batch
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py mqtt-first --batch experiments/p0-p2/reviewed-batch --paid
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py mqtt-repeat-1 --batch experiments/p0-p2/reviewed-batch --paid
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py mqtt-repeat-2 --batch experiments/p0-p2/reviewed-batch --paid
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py http --batch experiments/p0-p2/reviewed-batch --paid
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py summary --batch experiments/p0-p2/reviewed-batch
PYTHONPATH=. .venv/bin/python experiments/p0-p2/driver.py archive --batch experiments/p0-p2/reviewed-batch --stage http --output experiments/p0-p2/http-public.tar.gz
```

These are separate manual commands, not a script that launches all phases. Both
repeat commands run serially; concurrency is optional and unnecessary here. A failed
first MQTT blocks repeats. Any attempted phase is retained and cannot be overwritten.
Diagnose failures and prepare a new batch after reviewed changes. Every changed
runtime, prompt, fixture, scoped input, config, price or image requires a new three
MQTT cohort. User changes under `protocol_document/` do not affect the scoped manifest.
The two protocols have distinct frozen inputs under the same shared candidate.

Each public model gets four sessions in the reviewed **native tool_calls** mode, max 12 decisions
and one session each, with `fast_model=None`. JSON and native capability probes get
two logical short responses per model/mode, each with 1024 completion tokens
(plus the profile's billing reserve), without changing thinking settings. A
`finish_reason=length` is recorded as `truncated`, not unsupported capability.
Probes decode actual actions locally;
they do not ask the model to grade its own support. The fixed sample wire, actual
request/response/error evidence and usage remain host-only. Provider errors stop that
gate; no large paid error matrix is launched. A native failure requires a main-reviewed
new candidate; no automatic JSON fallback exists. DeepSeek defaults are untouched.
Study runs end in Run7 `study_complete`, with each task retaining its actual state;
stage results are in experiment records. Only the production orchestrator can finish
an entire generation with `status=success`. Completed studies cannot resume as generation.

The four public contracts are read/create-header, exact replacement, a real compile
failure followed by two-file interface repair, and an already-present requirement
claim with host builds. They replace the entire system prompt at the test boundary.
Target1's server role/artifact name remains compatibility metadata; each public
program must print exact output and terminate. The tiny public Acceptance1 snapshot
exists only for RunStore input validation, and cannot establish private acceptance.
Ordered observation of `RunStore.finish_action` records host seeding separately from
model actions, including actual result refs and model call counters. It never chooses
or executes a model action. Claims, trace, both host builds and executed stdout/exit
must all pass; an accepted `finish` alone is insufficient.

Generation begins with a checked empty checkpoint, no calls/responses/cache or
imported source. Production orchestration runs all serial tasks. After success the
harness audits exact identities, original claims/check mapping, and clean-builds a
copy of the delivery in release/san and reruns the complete private suite. Only that
copy's success counts. Original check metadata is compared with the frozen historical
contract; preserving each oracle assertion still needs the acceptance owner's code/
migration-map review. Counts cannot prove assertion equivalence.

Archives contain the complete audited delivery tree (including `.inc`, `.def` and
included build files), public Spec/Target/index snapshots, the production
`nepa.report.public_report(host_report)` Report5 projection, independent-audit
`safe_feedback` summaries, and an opaque candidate/run reference. Safe in-tree
symlinks remain portable; links outside delivery and private source trees are refused.
They exclude host manifests, private assets, seeds, raw
verification, calls and conversations. Full batch and run directories are host-only
and must not be archived as deliverables. Archives do not imply all P2 gates passed.

## Owner interfaces and controlled private repair

Cross-task messaging is unavailable in this task's callable tools. Main should relay
these exact integration checks before paid admission:

- **Goodall `01a096cf-f80b-7e70-bc5f-307f445cbec8`:** confirm
  `LLMClient.prepare(request)` returns `.body/.wire`, `complete(...,store,task_id)`
  passes `config.campaign.phase`, exact identity/usage failures retain reservations
  and raw fault evidence, and Config3 contains reviewed Qwen profiles/prices.
- **Noether `01a096cf-f762-70a1-93f1-7819be30318e`:** confirm
  `RunStore.initialize(...)`, `.private_checks` Path property, Run7/Report5,
  `reserve_call(...,phase=...)` enforces configured per-phase caps under the same
  campaign lock, `study_complete` is restricted to capability/public_tools and cannot
  resume as generation, and public evidence publication is available to CodingSession.

These APIs were read from the owners' in-progress files, not independently confirmed
by their owners. The harness never patches their implementations.

`private-repair` is implemented using `tests/fixtures/private_repair`. The C99
loopback service initially echoes `n-1` bytes per received block. Its public Spec
requires every binary byte, including zero/non-ASCII bytes, followed by clean EOF.
The host-private seeded oracle records actual send/receive bytes and emits only
structured lengths/outcomes. Both variants must first fail specifically with a short
echo, not infrastructure failure. Only `session.publish_feedback(..., raw_result)`
enters the Plus-only native repair session (12 decisions, one session). No manual
source repair occurs. The same immutable private assertion and seed must then pass
in both variants, with actual model edit/finish and exact provider/CNY evidence.
This extra study shares the public_tools CNY5 phase cap and does not change the
four public sessions per model. It is required before `freeze`; it is controlled
development evidence, never a fresh MQTT sample. `summary.status` remains false
until this and every real generation gate has passed. No manual status override
command exists.
