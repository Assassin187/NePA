## Why

当前 M1 架构校准代码与实验仍按固定文件槽、`arch_01`～`arch_10` 和 Qwen/DeepSeek 双模型基线工作；权威设计 5.3.0 已改为 S4b 自由布局（含 `{message_id}`/`{type_id}` 与 `messages`/`types` 两种受限展开）、`arch_11`～`arch_15`、Qwen/Claude/DeepSeek 三模型，以及开发/恢复阶段 `p1 ≥ 0.80` 的单调收紧筛选漏斗。旧 ArchitectureDraft、validator、prompt、lineage 和实验报告因此不能继续作为 M1-4a2/M1-4a2r 的有效证据，必须在同一套新生产窄切片上重建代码并重采实验。

## What Changes

- **里程碑与工作项：**覆盖 M1-4a1、M1-4a2 和条件式 M1-4a2r，并只对已归档的 M1-1/M1-2/M1-3 做受影响回归；依据 `system_design.md` §4.2、§4.6、§5.2、§5.6.5、§6.4.1～§6.4.4、§6.4.8.1～§6.4.8.2.1、§8.3～§8.4、§8.8、§9.2、§10.2 D1.0/D1.11，以及 `pipeline_design_s4_s9.md` §5.1～§5.2。
- **BREAKING：**把 M1-4a1 的固定 `file_rules/file_slots/internal_interface_slots` 架构输入替换为带 hash 的布局约定资产和 ArchitecturePlanner 输出的完整 `architecture.layout`；布局声明只允许 `{message_id}`/`{type_id}` 分别按 `messages`/`types` 展开，后续 Blueprint 机械映射为 `per_message`/`per_type`；ArchitectureDraft、示例、canonical serializer、验证 envelope 与生产 `ARCH_VALIDATE` 同步扩展到 `arch_01`～`arch_15`。
- 重构 Delivery Constraints 的窄切片，使其提供机械命名、资源上限、构建变体、`layout_convention_id`、约定 hash、`advisory/hard` 内容和机械契约边界，不再预先决定项目文件树；增加协议中立布局资产与 MQTT/非 MQTT fixture 校验。
- 把校准配置、Schema、artifact 目录、执行器和统计聚合统一迁移为稳定的 `qwen/claude/deepseek` 三个逻辑模型槽位，固定校准请求 `max_tokens=65536`；provider 返回的模型标识只逐 trial 记录并汇总，不再作为 lineage/批次有效性硬门。
- 在上述全部受控组件变化后生成全新的 lineage；旧固定布局、旧十门 validator、旧双模型 development/recovery root 和旧报告保持不可变历史，但明确标为不可进入新分母、fallback、候选或 handoff。
- 重写共享 ArchitecturePlanner prompt 的通用构造/自检算法，使其从布局约定与冻结输入产生自由布局并闭合十五个子门；按 V0/V1/可选 V2 重新执行三模型开发实验，版本内只允许 prompt 字节变化。
- 按设计 5.1.0 重做开发筛选：三个模型分别满足 `p1 ≥ 0.80` 且无截断、无基础设施无效即通过；Schema 修复后通过率、首次语义通过率、`p0`、逐门首次失败和重复首次失败保留为诊断证据，不再成为额外 rate 硬门；fallback 排名保持设计规定不变。
- 仅当新 M1-4a2 完整 V0/V1/V2 后仍产生 `PROMPT_SELECTION_TIE`，且负责人对本次恢复明确批准时，才从该新 predecessor 建立新的 M1-4a2r lineage，按 R0/R1/可选 R2、三模型各 N=5、每 trial 至多一次局部修复执行恢复；恢复筛选同样为每模型 `p1 ≥ 0.80`，并继续要求无截断/基础设施无效、修复差异/局部性证据完整、交接候选通过完整十五门 validator。若正常开发已选中 prompt，则记录 `not_triggered`，禁止人为制造平局以运行恢复。
- 新增版本受控的实现简报、实验预注册、机器汇总与简短结果报告；报告必须逐模型列出固定分母指标、模型标识全集/调用占比、逐门证据、截断/基础设施状态、选择或恢复停止原因，并引用新 lineage 工件，禁止抄用旧报告数字。
- **里程碑归属已按 5.3.0 更正：**布局约定资产、`layout_convention_id` 派生、`architecture.layout` Schema 与 `arch_11`～`arch_15` 四项原列于 M1-4b2，现改归 M1-4a1 并由本 change 交付——依据是 §6.4.8.1 第 2 项要求 M1-4a1 实现全部 `S4-G2` 子门，且 D1.0 要求全部批次在含 `layout` 与十五门的 lineage 上采集，四项缺失则本 change 的目标不可能达成。M1-4b2 保留 `layout.files[] → file_rules[]`/`build_graph` 转写器与 `kind`/`producer` 派生表，仍属非范围。
- **非范围：**M1-4a3 的三模型各 N=10 正式资格批次、`model_comparison.json`、B1～B4、生产模型/修复预算/调用形态冻结与负责人签字；M1-4b 的完整 Blueprint/Plan/Linker、M1-4b2 的转写器与 `kind`/`producer` 派生表、M1-4c 及后续 S4～S9、S5/S6、正式 Run/receipt/report；除负责人已授权并完成的 5.2.0 占位符同步与 5.3.0 布局子门判据同步（含子文档 1.1.0）外，不借本 change 改变其他权威设计。

## Capabilities

### New Capabilities

- `architecture-prompt-development`: 在新自由布局 lineage 上执行三模型共享 ArchitecturePlanner prompt 的 V0/V1/可选 V2 有界开发、诊断、筛选、fallback 与技术交接。

### Modified Capabilities

- `planning-architecture-infrastructure`: 将生产同形的 M1-4a1 窄切片从固定文件槽和十门校验迁移为布局约定输入、自由 `architecture.layout`、十五门 `ARCH_VALIDATE`、三模型逻辑槽位及只记录模型标识的 lineage/evidence 规则。
- `architecture-calibration-recovery`: 将旧双模型、旧十门、`p1=1.00` 的固定 predecessor 恢复协议替换为只消费本 change 新平局证据的三模型、十五门、每模型 `p1 ≥ 0.80` 条件恢复协议。

## Impact

- **当前已确认代码事实：**`nepa/speclib/delivery.py` 仍生成十个固定 rule/slot，尚未实现 `{type_id}`/`types` 自由布局展开；`architecture-draft.schema.json` 无 `layout`；`nepa/speclib/architecture.py` 与 validation Schema 只有十门；校准协调器、多个 calibration Schema、`configs/m1-4a2-live.yaml`、context limits 和测试仍硬编码双模型；开发/恢复筛选仍要求 `p1=1.00`、额外 Schema/首次通过硬门及模型标识稳定。旧恢复 selection 也只绑定 Qwen/DeepSeek 和旧 design hash。相关旧定向测试当前为 15/15 通过，只证明旧合同自洽，不证明符合 5.3.0。另有一处**先于本 change 存在**的红灯必须如实记录：`configs/m1-4a2r-authorization.json` 钉住的 `project_docs/system_design.md` SHA-256 为 `db75844d…`，既不等于 HEAD 字节也不等于任何当前修订，因此 `tests/test_prompt_recovery.py` 有 1 failed + 2 errors（`approved design path or SHA-256 does not match the owner authorization` 与授权边界 token 缺失）。这是旧恢复代码硬编码历史 predecessor/design 身份的直接后果，正由任务 6.1 移除；本 change 不得把它当作新引入的回归，也不得靠改钉住值绕过。
- **预计代码路径：**`nepa/speclib/{delivery,planning,architecture}.py`，`nepa/schemas/architecture-*.json` 与 calibration Schemas/examples，新增 `nepa/assets/layout_conventions/`，`nepa/calibration/{s4_architecture,s4_prompt_development}.py`，`nepa/agents/prompts/architecture_planner.md`，`nepa/config.py`、`configs/default.yaml`、显式 live/context 配置，相关 tests/fixtures，以及新的 `experiments/m1-architecture-calibration-redo-through-4a2r/` 报告目录。
- **前置依赖状态：**M1-1/M1-2/M1-3 和旧 M1-4a1/M1-4a2r changes 在仓库中有归档记录；本轮只重跑了旧路径的定向测试，尚未重新验证 M0 全部 DoD、负责人冻结签字或完整测试套件。apply 开始前必须重新核验 M0 入口、归档依赖和当前配置；OpenSpec 工件不能替代这些记录。
- **跨工作项不可拆原因：**自由布局同时改变 ArchitectureDraft、validator、prompt 输入/输出、repair locality、lineage hash 与所有实验分母。若把 M1-4a1 迁移、M1-4a2 重采和条件恢复拆成可独立完成的 changes，旧双模型/十门证据可能被错误接到新 prompt 或新恢复 handoff；本 change 以“一个新生产窄切片、一个不可混样 lineage 链、一个条件终点”闭合该风险，同时在 tasks 中逐项保留三个工作项各自的入口和验收门。
- **机器 DoD：**Schema/example 互校；`arch_01`～`arch_15` 正反例与确定性重算；Delivery Constraints/布局约定协议中立与双 fixture；三模型隔离、model-slot/identity 漂移、artifact/hash/resume、筛选单调性和固定 fallback 测试；完整新 M1-4a2 证据可从 leaves 重算并与新报告一致；若恢复被触发，再要求其新 lineage 完整重算，否则要求可重算的 `not_triggered` 记录。最终运行 focused suite、全量 `pytest`、gold lints、`openspec validate --all --strict` 和 `git diff --check`。
- **负责人门：**两组主/子文档冲突均已由负责人裁决并同步。其一（5.2.0）占位符：只允许 `{message_id}`/`{type_id}`，分别绑定 `messages`/`types`，Blueprint 对应 `per_message`/`per_type`。其二（5.3.0 与子文档 1.1.0）布局子门判据一律采用子文档 §5.2.4 口径：`arch_13` 为三段闭合＋每个 `link_source` 槽恰进入一个 artifact＋输出路径唯一＋`entry_point` 数量精确匹配＋无环（不再用 `app` 槽措辞，该词属 Blueprint 层 `kind` 词汇且在 `build_role` 枚举中无对应值）；`arch_15` 为白名单口径，判定域是 `path`/`path_pattern` 分段与 `purpose` token，白名单是与 D1.11 共用的校验器侧 lineage 绑定实现，不进 `advisory`/`hard`。这两处必须在任务 3.6 冻结 lineage 之前定案，因为它们直接决定 `p0`/`p1` 口径。实现只需记录并核验 5.3.0/1.1.0 的 path/hash，不再因原冲突阻塞。M1-4a2r 若实际触发，仍需负责人对该新 predecessor/new design hash 另行明确批准；M1-4a3 的生产冻结签字不属于本 change，也不得由自动检查代替。
- **已有 change 与下游：**活动 change `m1-4a2-architecture-planner-prompt-optimization` 的旧 34/36 状态和归档 `m1-4a2r` 证据不重写；本 change 的新结果取代其下游资格，但不伪造旧 change 完成。该旧 change 必须在开工前按任务 1.5 以 `--skip-specs` 归档为被取代项：它与本 change 都 ADD 同一 capability `architecture-prompt-development` 且需求集互不相交（旧为双模型、新为三模型），若两者都按常规归档，capability 基线会同时含双模型与三模型两套矛盾需求；`--skip-specs` 使其 delta 永不进入基线，同时保留其字节与 34/36 状态作为可追溯历史。只有新 M1-4a2 selection 或条件式新 M1-4a2r handoff 才可作为后续 M1-4a3 的输入。
