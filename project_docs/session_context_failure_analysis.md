# Coding-session failure analysis — 2026-09-12

Status: mechanism implemented and offline regressions passed; the user explicitly
authorized further evidence-driven refactor changes without per-change approval.
New real acceptance remains necessary; no generation success is inferred here.

## Evidence

Frozen production candidate fc170b1 used a 60000-byte actual-wire cap. The earlier
48c09e7 candidate used 180000 bytes. The model, source inputs and acceptance gates
were unchanged, but prompts/diagnostics also changed: these are not controlled A/B
samples and do not alone prove a causal model-success difference.

Replay script: runs/_refactor/audit_context.py. It reads only immutable call evidence,
uses the actual wire messages, and counts identical read requests since the last
write/replace/command. Any command invalidates that comparison conservatively,
because a command could modify a file. A prior read is counted as visible only if
its successful content is actually present in the next request's transcript.

| Run/task | Calls | Read actions | Unchanged rereads whose content was evicted | Such rereads with content still visible |
| --- | ---: | ---: | ---: | ---: |
| 8bc177b4 / message:subscribe | 120 | 87 | 78 | 0 |
| 812db4b0 / message:connect | 120 | 69 | 17 | 0 |
| c1b46c97 / message:subscribe | 80 | 53 | 38 | 0 |
| c1b46c97 / requirements:002 | 119 | 59 | 21 | 0 |

The first failed task performed 12 listings, 87 reads, 18 invalid actions and three
searches, with no editing/building action in its entire 120-decision allowance.
Its actual requests held at most 23032 characters of file contents; fixed task,
schema and transcript overhead occupied the rest. The later c1b46c97 project has
72277 source/header characters before JSON and task overhead.

There is also a concrete transcript invariant bug. Session.run appends a separate
user message at each session boundary, but eviction always deletes messages[1:3]
as if every entry after the base were an assistant/action plus user/result pair.
In 8bc177b4 call 000119, the final two messages are both user messages. By call
000135, the request begins system,user,user,assistant,user: slicing has ceased to
operate on complete transactions. This is independently reproducible without an API.

The previous 180000-byte candidate had no counted unchanged-read eviction loop in
message:subscribe, completing it in 38 calls. It later failed at the cost boundary
after malformed finish responses; its larger window was not proof of completion.

## Root causes and scope

1. Context management protects only a byte ceiling, not the source working set
   needed to make a code change. Removing oldest dialogue pairs silently discards
   still-needed source observations. Repeated reads refill and then evict one
   another. The replay proves eviction-before-reread; its contribution to model
   indecision is a strong inference, not a measured counterfactual success rate.
2. Session boundaries violate the eviction algorithm's pair assumption. Retrying
   appends another instruction into the same transcript instead of constructing a
   coherent new request with current code observations and actual diagnostics.
3. Existing tests proved byte compliance and a scripted compile-repair path, not
   retention of the information required by an autonomous coding task. The new
   60000-byte setting passed those tests while making the live workflow worse.

More exhortations to avoid rereading cannot restore bytes that were removed.
Increasing the limit alone postpones the same failure for a larger working set.
No evidence here requires changing the 23-task planner, weakening claims, importing
an answer, or relaxing the independent protocol checks.

## Authorized correction

Keep the existing single-writer/tool loop, but assemble model context explicitly
from immutable task facts, deduplicated versioned source observations with evidence
references, and complete action/result transactions. Invalidate source observations
when edits/commands can change their contents. Do not let redundant transcript text
crowd out the current observations. Never silently drop the latest tool diagnostic.
Reconstruct session transitions from those facts instead of introducing unpaired
messages. Restore an appropriately sized configured window; if the required current
working set cannot fit, diagnose capacity explicitly instead of spending repeated
model calls on an eviction loop. Do not add a second authoritative run/code state.

Required tests: repeated source reads and eviction replay; write/command invalidation;
latest build diagnostic retention; exact tool transaction pairing across all three
sessions; accurate wire budgeting; explicit capacity exhaustion; existing true
compiler repair and full CLI/oracle tests. Then freeze a new candidate and launch
one new independent run first, then two repetitions only after its full end-to-end
success (latest user instruction). No old generated project is reused.

## Terminal records and budget

Parallel batch d59fda21f23a4c0a90667fc138933fc8: failed. The original serial batch
157c82ce1afb4e74bd850ce8f6870f63 was adopted by a separately hashed parallel scheduler
at the user's explicit request; production code/config/input/image stayed frozen.
Run indexes from the final combined batch (not directory ordering):

- 1: 20260912T083509Z-8bc177b4, failed, exit 2, USD4.04680188.
- 2: 20260912T085149Z-c1b46c97, user-authorized SIGINT, exit 130,
  USD19.25777964, 16/23 accepted tasks, 48 claims, no delivery.
- 3: 20260912T085149Z-812db4b0, failed, exit 2, USD3.15208608.

Historical campaign total USD49.02257712; authorized limits USD100/run,
USD300/campaign, four hours/run. Every failed/interrupted call remains counted.
