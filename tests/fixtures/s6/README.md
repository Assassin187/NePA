# Frozen S6 provider fixtures

`mqtt/provider-sequence.json` and `non_mqtt/provider-sequence.json` are generated from the archived `tests/fixtures/s5/{mqtt,non_mqtt}` inputs by `tests/tools/generate_s6_fixtures.py`.

The files are committed test inputs. Tests validate their canonical bytes and source-file hashes; CI consumes them and does not regenerate them.
