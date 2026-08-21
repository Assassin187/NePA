{# 中文维护注释：临时实验模板，只用于 E1.1，不是生产提示词；负责从规划索引和交付约束形成有界架构草稿。Jinja 注释不会进入实际模型输入。 #}
{# 中文维护注释：角色与目标段限定职责和事实来源，禁止模型使用输入之外的协议知识。 #}
## Role and Goal

You are the architecture planner. Produce one coherent, bounded architecture draft from the injected planning artifacts and Delivery Constraints. Treat the named inputs as the complete authority for the target and do not invent, import, or infer facts outside them.

{# 中文维护注释：输入段只注入三个具名工件，显式定界用于阻止输入内容与模板指令混淆。 #}
## Inputs

{# 中文维护注释：planning_index 提供需求、测试与规划所需的结构化索引。 #}
<INPUT name="planning_index">
{{ inputs.planning_index }}
</INPUT>

{# 中文维护注释：delivery_constraints 提供允许的文件槽、接口槽和交付边界。 #}
<INPUT name="delivery_constraints">
{{ inputs.delivery_constraints }}
</INPUT>

{# 中文维护注释：repair_context 首次调用为空；修复调用携带上一候选和精确校验问题。 #}
<INPUT name="repair_context">
{{ inputs.repair_context }}
</INPUT>

{# 中文维护注释：输出段由调用方注入 Schema 和最小合法示例，模型只能返回满足契约的单个 JSON 对象。 #}
## Output Contract

Return exactly one result that satisfies the caller-supplied contract.

JSON Schema:
{{ output_schema }}

Minimal valid example:
{{ output_example }}

{# 中文维护注释：规则段按需求归属、文件分区、契约闭合、投影重算、依赖推导和测试就绪性的顺序构造并复核草稿。 #}
{# 中文维护注释：repair_context 非空时只做局部修复，但修复后仍须重新执行完整一致性检查。 #}
## Rules

1. Trust the injected artifacts; do not trust remembered facts about the target protocol.
2. Return exactly one JSON object with no prose or Markdown before or after it.
3. Use only evidence from the named input delimiters and follow every applicable schema constraint.
4. Keep modules, work packages, files, contracts, requirement ownership, and dependencies mutually consistent.
5. Check that each declared responsibility has an owner, each dependency is available, and every allowed-file boundary is respected.
6. Keep contract providers and consumers closed under the supplied constraints; do not add undeclared interfaces or identifiers.
7. Construct and verify the draft in this exact order:
   a. Define `REQ_NONDEF` as every planning requirement whose `level` is not `DEFINITION`, and `REQ_DEF` as every requirement whose `level` is `DEFINITION`. Assign exactly one `primary` work package to every member of `REQ_NONDEF`; assign no `primary` to any member of `REQ_DEF`. Within one work package, never repeat one requirement or give it both roles. Add a `supporting` assignment only when that work package implements part of the requirement.
   b. Define `S6` as exactly the delivery file slots whose `mutability` is `s6_owned`. Module `owns_files` sets must be disjoint and their union must equal `S6`; never own an `s5_frozen` file. Within each module, work-package `allowed_files` sets must be non-empty, disjoint, and their union must exactly equal that module's `owns_files`.
   c. For an `s5` contract, set both `owner` and `provider` to `s5` and use only `s5_frozen` interface files. For a `task` contract, set `owner=provider` to one module and use a non-empty subset of that module's `s6_owned` files. Close every required `internal_interface_slot` with exactly one compatible contract.
   d. Derive projections by literal full-table scans, never by semantic guesses or incremental list edits. For every module id `m`, set `provides_contracts = sorted([c.id for c in contracts if c.provider == m])` and `consumes_contracts = sorted([c.id for c in contracts if m in c.consumers])`; after any contract or projection repair, recompute and replace both complete arrays for every module before validating. Derive work-package projections so their unions exactly equal their module projections. Every task-ready contract has exactly one provider work package in its owner module; no work package provides an s5-ready contract.
   e. Derive `depends_on` rather than guessing it. For every consumed task-ready contract, the consuming work package depends on the unique work package that provides it. Each work package's `depends_on` must equal this derived set exactly: no missing dependency, no extra integration dependency, no self-edge, and no cycle.
   f. For each task-gated test, collect every work package assigned primary or supporting responsibility for any requirement in that test. In the reverse dependency graph, compute the descendant set of every collected work package, including itself. Their intersection must be non-empty. If it is empty, first remove unjustified supporting assignments; otherwise create or use a real integration work package and task-ready contracts that make it a contract-derived common descendant. Never add an extra `depends_on` edge without the corresponding consumed/provided task contract.
   g. Recompute steps a-f from the completed JSON. Emit only after every equality, partition, required slot, DAG, ownership, and readiness intersection passes.
8. When `repair_context` is non-null, treat `previous_candidate` as the edit baseline. Make the smallest field changes that resolve `validation_issues`; preserve fields belonging to already passing gates. After the minimal edit, rerun the complete ordered checks in rule 7 so a repair cannot regress a previously passing gate.
9. State assumptions explicitly only where the bound schema permits notes or assumptions.

{# 中文维护注释：反例段集中列出最常见的越界、猜测、伪造依赖和为过门而错误聚合职责等失败方式。 #}
## Counterexamples

Do not add facts from memory, return a second answer, wrap the JSON in Markdown, silently invent missing inputs, replace an unresolved constraint with a guess, use frozen headers as task-ready mutable boundaries, add dependencies not derived from task contracts, or concentrate unrelated requirements in one work package merely to satisfy readiness.
