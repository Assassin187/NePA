# P1 private oracle source migration

Baseline: `351ff20949ba9e82b6ffa667040a5d134ffc7114` (Design11). Authority:
`project_docs/system_design.md`, section 4, and the research plan's top-level
Accepted implementation decisions. This is a source/unit-test handoff, not a new
real-generation, two-container admission, or paid-repair acceptance result.

The change is limited to three existing oracle scripts, two oracle test modules,
and this authorized document. No new runtime component or oracle asset is needed:
MQTT smoke imports the already-listed sibling `mqtt_behavior.py`; HTTP keeps its
small local trace/generator implementation. Both Acceptance1.0 manifests remain
byte-for-byte unchanged, including assets, IDs, `required`, argv, timeouts and every
`req_ids` array. Spec inputs and historical experiment assets are unchanged.

## Host runner contract (Noether handoff)

- `NEPA_ORACLE_SEED`: exactly 64 hexadecimal digits, a case/variant-specific 256-bit
  seed supplied by the host; never derived by an oracle from host/port/run ID.
- `NEPA_ORACLE_TRACE_FILE`: writable private JSONL file owned by the checker/host.
  CLI execution requires both env values. Unit tests may explicitly supply
  `Oracle(seed, trace_file=None)` without a file. There is no implicit/random seed.
- Host/port argv stay unchanged. The oracle connects only to the supplied endpoint.
- One stdout JSON line: `{"passed": bool, "category": str, "observation": dict}`.
  Success category is `protocol`, observation `{"outcome":"accepted"}`, exit 0.
  Failures exit 1. Check ID and variant are joined by the host, not echoed from argv.
- `WireMismatch` contains only explicitly constructed semantic fields. Categories
  are fixed source constants, not exception text or server strings. Unexpected
  exceptions yield `error` (or `timeout`) and an allowlisted `error_class`; no
  exception message, traceback, raw payload, seed, identifier, topic or path is
  printed. No raw diagnostic fallback is needed.
- Safe numeric fields are `expected_length`, `actual_length`, `expected_status`,
  `actual_status`, `expected_type`, `actual_type`, `actual_order`, `offset`,
  `expected_duration_ms`, `actual_duration_ms`. Length and first mismatch offset
  describe compared fields, not raw field values. Packet type is the MQTT fixed
  header byte; SUBACK code/QoS diagnostics use numeric types. `actual_order` is the
  one-based response ordinal on the connection, including its handshake.
- Safe string fields are `outcome`, `expected_outcome`, `actual_outcome`, and
  `error_class`. Strings are source constants such as `open_quiet`, `eof`,
  `mismatched_identifier`, `different_bytes`, `missing_or_invalid` or `timeout`.
  HTTP header timeouts have an unknown expected length (`null`), which the host
  omits; expected outcome and actual buffered length remain useful.
- Read-only inspection of the concurrently edited `nepa/tools/verification_worker.py`
  confirmed these env names and its exact single-line JSON parser. Inspection of
  `nepa/tools/verification.py::_observation` confirmed the numeric/string field
  whitelist accepts the fields above (and drops null/overlarge values). No core
  file was edited by this task. Direct Noether task messaging was unavailable:
  the session's callable tools contained no task/agent messaging tool. This file
  is the concrete handoff; no direct acknowledgement is claimed.

## Replay and private byte evidence

`random.Random(int(seed,16))` is case-local. No global PRNG, UUID, `os.urandom`,
port-based seed, or generated-code import exists. The start record includes the
seed, `randomization_version: random-inputs/1`, and Python version. The frozen
oracle source, Python image, seed and deterministic draw/call order define input
replay; actual send/receive records provide the byte evidence. OS timing and actual
TCP segmentation are not claimed to replay identically.

Every JSONL event has a sequence number, wall-clock nanoseconds and monotonic
nanoseconds. Connected sockets have monotonically assigned connection numbers;
connection failures are journaled. A `write` event marks each logical write and
its requested size. Every underlying `sock.send()` return is then recorded with
that write number, actual count, cumulative connection offset, and only the bytes
reported sent. A partial send followed by an exception records its accepted prefix
and a separate timeout/error event with no invented suffix. Each `recv()` records
requested size, actual bytes/count and cumulative receive offset, including EOF
and timeout. `settimeout`, `quiet` (including unexpected data/EOF/buffered data),
`half_close`, `close` and explicit keepalive `wait` events preserve observations and
schedule intent. Logical writes are not described as TCP packet boundaries.

Random client identifiers use only lowercase ASCII letters and digits: behavior
IDs are 9–21 bytes, reset IDs 21, smoke IDs 15–16. These lie within MQTT's mandatory
1–23-byte alphanumeric support; no MAY-only length/character acceptance is imposed.
Topics use a fixed legal prefix and 8–32 alphanumeric suffix bytes. Packet IDs are
1–65535, with the same identifier reused where session reset requires it. Random
bodies are 1–96 bytes. HTTP requests vary legal Host labels; additional header-case
exchanges only reuse the original leading HTAB/trailing SP+HTAB OWS pattern.

Original vectors stay first. Extra random payload exchanges are bounded at 2–4
per applicable case, not full-suite repetitions: MQTT pubsub, multi-client,
unsubscribe, session isolation/reset, QoS and coalesced traffic; smoke adds 2–4
PING exchanges on its original final connection. HTTP echo, header-case, pipeline,
unknown routes and healthy recovery after the fixed rejection cases add 2–4.
Fragmented cases union 2–4 sampled cuts with every mandatory cut. Coalesced extras
choose write groups of 2–4 (a trailing remainder may be one). Timing-sensitive
keepalive cases retain exactly their original observations.

## Shared assertion translation

All original predicate semantics remain. Byte equality now uses `equal_bytes`
(lengths and first differing offset). MQTT `receive` distinguishes early EOF from
partial-read timeout. `Client.packet` retains the four-byte Remaining Length bound
and 1 MiB response cap. `Client.expect` checks the fixed header and full body;
CONNACK mismatches also disclose the numeric return code. `subscribe` still checks
SUBACK header, exact packet-ID correlation, result count, legal codes 0/1/2/128,
and every granted QoS no greater than requested. `unsubscribe` still checks exact
UNSUBACK and correlated ID. `quiet` still requires an open stream with no data;
`eof` still rejects trailing data (behavior MQTT alone retains its original reset
allowance; smoke refusal still requires actual EOF).

HTTP `response` retains all original assertions: header EOF/131072-byte cap;
three-part HTTP/1.1 status line with three decimal status digits; colon/non-folded
header syntax; no duplicate Content-Length; no Transfer-Encoding; decimal
Content-Length; 1 MiB body cap; exact complete body; preservation of buffered bytes
for subsequent responses. `expect` retains exact status and optional full payload.
`quiet` checks both buffered and newly received data. `eof` checks the buffer and
actual EOF. None of these failures use a generic assertion string; they have
stable semantic categories (`status_line`, `content_length`, `body_EOF`,
`response_status`, `response_body`, `quiet`, `connection_close`, etc.).

## Every-case assertion map

The table's Req column means the **entire original manifest array is identical**,
not merely that a count is equal. The exact byte/hash evidence follows the table.

| MQTT case | Original assertions/boundaries → current logic | Req/timeouts |
|---|---|---|
| minimum-interactions | CONNECT cut at byte 3; exact `20 02 00 00`; `d0 00`; unsupported level 0 → exact `20 02 00 01` then actual EOF; new connection exact CONNACK/PINGRESP → smoke `expect_reply`, `receive`, strict refusal-close. Original three connections remain. | identical / 30s |
| pubsub | Two subscriptions; empty and `00 ff c0 00` binary payload delivery; unmatched publish quiet and PING → unchanged calls plus 2–4 extra bodies. | identical / 20s |
| multi_client | Two matching subscribers receive; absent subscriber stays quiet and answers PING → same checks plus bounded extra deliveries/absence checks. | identical / 20s |
| unsubscribe | Remove present/missing subscriptions, publish removed and remaining topics, only remaining delivery, quiet, repeated unsubscribe ACK → same checks plus bounded state-preserving traffic. | identical / 20s |
| session_isolation | One subscriber unsubscribes; other still receives; first quiet/PING → same state and checks plus extra deliveries/absence checks. | identical / 20s |
| qos | Subscribe requested QoS 0,1,2; legal granted codes ≤ requested; original retained binary publishes all delivered as fixed header 0x30 → same checks plus extra retained bodies on those topics. | identical / 20s |
| session_reset | Same client ID, CleanSession=1, original delivery, DISCONNECT+EOF, reconnect, no inherited subscription, PING, resubscribe and delivery → same sequence, plus extra post-resubscribe bodies. | identical / 20s |
| duplicate_connect | Second CONNECT on established socket closes; independent client PING succeeds → unchanged. | identical / 20s |
| disconnect | DISCONNECT closes; subsequent valid client PING succeeds → unchanged. | identical / 20s |
| invalid_qos | Every original 3,4,128 SUBSCRIBE code closes; later valid client PING → unchanged values/checks. | identical / 20s |
| fragmented_connect | Mandatory cuts 1,2,3,7,last−1,last, open/quiet .06s after each incomplete prefix; complete CONNACK and PING → mandatory cuts union random cuts; all original assertions retained. | identical / 20s |
| fragmented_publish | Original 160-byte `z` payload; cuts 1,2,3,4,5,8,last−1,last across RL/string/body; subscriber .06s and publisher .03s quiet per prefix; exact final delivery and PING → same plus cuts. | identical / 20s |
| coalesced | Exact original `one`, `two`, incomplete PING header together; two ordered deliveries; publisher .1s quiet; send final byte and expect PINGRESP → untouched, then 2–4 random ordered exchanges with varied groups. | identical / 20s |
| length_boundaries | Remaining Length exactly 127,128,16383,16384; full `z` payload exact delivery at each → same values, payload length adjusted to randomized topic as before. | identical / 20s |
| truncated | Publish missing last byte; .1s quiet; write-half-close and EOF; existing and new healthy clients PING → unchanged. | identical empty Req array / 20s |
| invalid_flags | Original C1, SUBSCRIBE header 80, E1 vectors close; healthy-client PING after each and new-client PING → unchanged. | identical / 20s |
| invalid_utf8 | Original C0 AF, ED A0 80, NUL topics close; healthy PING after each, then new client PING → unchanged. | identical / 20s |
| keep_alive | KeepAlive=2, EOF within 4s but no earlier than 2.5s, then healthy PING → unchanged; early-close diagnostic adds actual duration. | identical / 20s |
| keep_alive_zero | KeepAlive=0, open quiet for full 4s, then PING → unchanged. | identical / 20s |
| keep_alive_activity | KeepAlive=2, five 1s waits followed by PING, then EOF within 4s → unchanged; explicit waits journaled. | identical / 20s |

| HTTP case | Original assertions/boundaries → current logic | Req/timeouts |
|---|---|---|
| get_head | GET 200 `nepa\n`; coalesced HEAD+GET; no HEAD body; matching exact `Content-Length: 5`; subsequent GET exact payload → same sequence and `head_length` predicate. | identical / 20s |
| echo | Empty, original binary/request-like body, 16384 `z` bytes exactly echoed → same first three vectors, then 2–4 extra bodies. | identical / 20s |
| routes | Original GET/POST/HEAD `/missing` →404; BREW and lowercase get `/` →501; exact empty body and metadata 0 → same five vectors, then bounded random unknown paths. | identical / 20s |
| header_case | Original `hOsT`, `cOnTeNt-LeNgTh`, HTAB/SP OWS and `abc` →200 exact echo → unchanged original vector, then bounded random casing/bodies with the same OWS pattern. | identical / 20s |
| host_errors | Missing Host, duplicate Host a/b, `bad host` each →400+EOF → all fixed vectors unchanged; extra independent healthy echo traffic. | identical / 20s |
| length_errors | Original −1, abc, 2x, conflicting duplicate 2/3, list 2/3, identical duplicate 2/2, list 2/2, 36-digit huge length each →400+EOF → unchanged vectors/checks, then healthy echo traffic. | identical / 20s |
| fragmented | Original `body 00 ff`; cuts 1,5,22,header-end−1,last−1,last; .08s quiet at every incomplete prefix; exact echo only after completion → all original cuts/body/checks plus sampled cuts. | identical / 20s |
| pipeline | GET, echo `one`, incomplete echo `two` in one write; ordered first two exact responses; .08s quiet; final byte then exact `two` → unchanged, then bounded random grouped echoes. | identical / 20s |
| connection_close | GET with Connection close; 200 exact body, response Connection value case-insensitively close and EOF → identical predicates. | identical / 20s |
| truncated | POST missing last byte; .08s quiet; write-half-close and EOF; independent GET 200 exact body → unchanged. | identical / 20s |
| malformed | All four original malformed line/line-ending/Host-space/header-colon vectors each →400+EOF → unchanged, then healthy echo traffic. | identical / 20s |
| transfer_encoding | Original chunked vectors without/with Content-Length:5 each →400+EOF → unchanged subset policy, then healthy echo traffic. | identical / 20s |

## Invariance and validation evidence

SHA256, checked against `git show 351ff20:<path>` and pinned in an offline test:

| File | Unchanged SHA256 |
|---|---|
| gold_file/mqtt/acceptance.json | `eb74d6283eb80ca116f52d69565f930eb05efb2854af5a3172a1ff57ac23014d` |
| gold_file/http/acceptance.json | `f2d8323fb82739c6deda347b7d156f71cfac3434928bb47a4b6e342efc24e91a` |
| gold_file/mqtt/specIR.json | `a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09` |
| gold_file/http/specIR.json | `31e090e0dd6cd7e82a7d981b08f3fd0005a08c2143d50c556a75eba031d6c4c3` |

Offline tests exercise all 20 MQTT/12 HTTP cases with accumulating stream peers,
short reads and a simulated clock. Each case has identical input/I/O schedules for
the same seed and distinct transmitted input for another seed. Tests also verify
mandatory cuts/quiet counts, client-ID character/length limits, RL/binary/malformed
boundaries, manifest/Spec bytes, actual short sends, partial-send timeout evidence,
EOF/timeout/half-close records, CLI env consumption and safe structured failures.
Existing wrong ACK/body/length/status, leaked-session and early-partial-response
negative tests remain, with their exact fixed IDs now explicitly supplied.

Run only the authorized offline checks:

```text
.venv/bin/python -m pytest tests/test_protocol_oracles.py tests/test_private_oracle_vectors.py -q -p no:cacheprovider
.venv/bin/python -m ruff check gold_file/mqtt/acceptance gold_file/http/acceptance tests/test_protocol_oracles.py tests/test_private_oracle_vectors.py
```

Recorded result: 55 oracle tests passed; ruff and scoped `git diff --check`
passed. The affected caller `tests/test_acceptance_runner.py` was also read and
run (not edited by this task): all three test modules together passed 58 tests.

These tests do not establish actual container isolation, real TCP scheduling,
san/ASan behavior, paid repair, or production generation success. Those are the
core/main owner's later admission gates. Keep existing run/assets immutable.
