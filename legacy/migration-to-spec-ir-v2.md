# Legacy three-file model → Spec IR v2.0 migration map

Status: M0-2 migration record. The source files are immutable historical inputs.
The destination contract is `nepa/schemas/specs-requirements.schema.json`.

## File mapping

| Legacy source | Spec IR v2.0 destination | Migration rule |
| --- | --- | --- |
| `gold_specs/*-min-profile.json` | `scope`, `transport`, `meta` | Merge selected role, packet/capability scope, exclusions, assumptions, and TCP binding. The v2.0 gold scope follows the frozen 7.1 baseline, not every older profile choice. |
| `gold_specs/*-wire-format.json` | `types`, `messages`, `constants` | Flatten the single MQTT Control Packet PDU and its selector types into one message per included control packet. Preserve field order and fixed-header type/flag constants. |
| `gold_specs/*-min-requirements.json` | `requirements`, `state_machines`, `behaviors`, `timers`, `errors` | Convert in-scope normative entries to atomic `REQ-*` records and attach them to externally observable protocol elements. Out-of-scope requirements remain only in the archived source. |

## Top-level field mapping

| Legacy path | v2.0 path | Notes |
| --- | --- | --- |
| profile `protocol`, `protocol_version` | `meta.protocol_name`, `meta.protocol_version` | `meta.source.kind = manual`. |
| profile `role` | `scope.roles` | Expanded from broker-only wording to the 7.1 `client` + `broker` target roles. |
| profile `packet_scope`, `capabilities` | `scope.features_included` | Only features retained by 7.1 are migrated. |
| profile excluded packet/capability entries | `scope.features_excluded[]` | Each entry gains an explicit reason. |
| profile `transport.binding` | `transport.layer` | TCP → `tcp`; port 1883 and big-endian wire order are explicit v2.0 values. |
| wire `data_types[]` | `types[]` | MQTT variable byte integer → `varint`; UTF-8 string → `length_prefixed_string`; binary data → `length_prefixed_bytes` where applicable. |
| wire selector `types[]` | `messages[]` | Selector value → `packet_type_code`; allowed direction → `direction`; type layout → ordered `fields`. |
| wire component IDs | message `wire_layout` and field `loc` | `fixed-header`, `variable-header`, and `payload` become snake-case segment names. |
| wire field representation/constraints | field `type`, `bits`, `constraint`, `derived` | Remaining Length becomes a derived `mqtt_varint`; fixed flags become bit constraints. |
| requirement `id` | `requirements[].id` | Renamed to stable topic-based `REQ-<TOPIC>-<NNN>` IDs; original section remains in `source_ref`. |
| requirement `modality` | `requirements[].level` | `MUST_NOT` → `MUST NOT`; `REQUIRED` is normalized according to the quoted normative clauses. |
| requirement `statement` | `requirements[].text` | Chinese normalized statement retaining MQTT field/packet names. |
| requirement `source.section/excerpt` | `requirements[].source_ref.section/quote` | Locator/page remain available in the archived source but are not v2.0 fields. |
| requirement `structure_references` | `covered_by.elements` and element `req_ids` | Legacy paths are rewritten to v2.0 element paths. |
| requirement behavioral semantics | `state_machines`, `behaviors`, `timers`, `errors` | State-transition-shaped rules become transitions; otherwise use observable behaviors/timers/errors. |
| no legacy equivalent | `requirements[].covered_by.tests` | Added from the M0-6 gold test manifest. |

## Intentional non-migrations

- Wildcard topic matching, retained messages, QoS 1/2, Will, authentication,
  persistent sessions, TLS, WebSocket, and IPv6 remain archived and excluded.
- The legacy generic multi-PDU/framing model is not copied into v2.0. It remains
  an input to the future O-5/v3.0 decision.
- Legacy product assumptions that conflict with the approved 7.1 baseline do
  not override the active scope.

The design document 12.3 summary still requires maintainer approval before this
standalone record can be linked from or merged into that document.
