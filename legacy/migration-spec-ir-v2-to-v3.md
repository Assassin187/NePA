# Spec IR v2.0 → v3.0 migration

Spec IR v3.0 narrows the artifact to facts that can be copied from protocol
documents or selected explicitly by the run scope. Implementation models, test
design, and reverse indexes are no longer extraction outputs.

| v2.0 | v3.0 |
| --- | --- |
| `meta.protocol_name`, `meta.protocol_version` | `protocol.name`, `protocol.version` |
| `meta.source`, `meta.created_at` | `run.json` |
| `scope` | independent scope/Target Profile input |
| `transport.layer` | `transport.name` |
| `packet_type_code` | the discriminating wire field's `constraint.const` |
| `direction` | `senders[]`, `receivers[]`, both referencing `protocol.roles` |
| free-form composite `encoding.item/repeat` | `sequence.members[]` plus `repeat.item_type` |
| `state_machines`, `behaviors`, `timers`, `errors` | source-backed atomic `requirements[]` only |
| `constants` | owning structure, or a source-backed requirement's optional `values` |
| `on_violation` | source-backed atomic requirement |
| requirement `category` | downstream classification when needed |
| `covered_by.elements` | deterministic reverse index of element `req_ids` |
| `covered_by.tests` | Test Bundle manifest `req_ids` |
| `observable_check` | Test Bundle/test-design output |

Additional rules:

- Every transport/type/message/field fact has at least one `req_id`.
- Every requirement has a `source_ref`.
- `DEFINITION` records non-normative wire or binding definitions and is excluded
  from MUST coverage metrics.
- v2.0 and v3.0 artifacts cannot be mixed in one run.
- IDs are retained where the source clause remains the same. New direct
  definition clauses receive new `REQ-*` IDs.
