# NePA

Generate a protocol project from manually curated Spec (including requirements),
target format and independent acceptance assets. Initial scope: Linux x86_64/C99
servers, with MQTT as the first evaluation input, not a generator special case.

See project_docs/system_design.md for the approved contract and
project_docs/refactor_plan.md for actual implementation/acceptance status.

## Development

```bash
uv sync --extra dev
docker build -t nepa-sandbox:refactor -f docker/sandbox.Dockerfile .
uv run pytest -q -m "not live_e2e"
uv run ruff check nepa tests
uv run mypy nepa
```

## Generation: new CLI contract

Set the API-key environment variable named by your provider configuration; never
write credentials into committed configuration or a generated project.

```bash
uv run nepa run --spec gold_file/specIR.json --target gold_file/target.json \
  --acceptance gold_file/acceptance.json --config configs/default.yaml --runs-root runs/e2e
uv run nepa status RUN_ID --runs-root runs/e2e
uv run nepa resume RUN_ID --runs-root runs/e2e
```

Successful exports contain sources, Makefile, README and release/san executables.
Build without NePA using make clean then make release san. Exit zero requires all
tasks, mandatory checks and published artifacts. Claims are not verified behavior.

The minimum MQTT oracle covers CONNECT, PING and a refusal path; it does not prove
all requirements, full MQTT conformance, other protocols or other languages. Paid
live tests are opt-in. Old run versions require their original code for reproduction.
