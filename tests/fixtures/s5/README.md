# Frozen S5 fixtures

These fixtures are generated, never hand-authored, by:

```text
uv run python tests/tools/generate_s5_fixtures.py --output tests/fixtures/s5
```

The generator runs both the MQTT and non-MQTT inputs through the existing S4
completion and initial-publication helpers, and emits deterministic artificial
F2/F3 epoch input bundles. CI consumes the checked-in bytes; it does not
regenerate them.
