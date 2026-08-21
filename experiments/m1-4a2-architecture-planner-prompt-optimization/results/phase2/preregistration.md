# Phase 2 预注册：单调用与三阶段闭包

日期：2026-08-20
状态：已执行；双模型比较因 Qwen Provider `Arrearage` 无效，结果与复验边界见 `phase2-report.md`
实验：E2.1

## 固定因素

- 模型：`qwen3.7-max-2026-06-08`、`deepseek-v4-flash`
- temperature：0.0
- max_tokens：65536
- trial：N=3/模型
- prompt：`results/phase1/artifacts/prompt-exact-algorithm.md`
- example：当前生产 `nepa/schemas/examples/architecture-draft.example.json`
- 输入：原始 `planning_index` 与 `delivery_constraints`，不附加 deterministic ledger
- Schema：完整 `ArchitectureDraft` Schema，保留当前非 native fallback 的 Schema 呈现方式
- validator：同一生产 `validate_architecture`
- Provider key：仅通过进程环境映射，不写入实验产物

## Arm M

复用 E1.1-B 的 N=3 结果：一次完整生成；若未通过，提供完整 validation issues 后允许一次全局语义修复。不得因 Phase 2 结果重跑或替换 M 样本。

## Arm S

保持完整输出 Schema 不变，以避免把“call shape”与“更换 Schema/确定性组装器”混杂：

1. Stage 1：首次生成完整候选，以 `arch_01`–`arch_05` 为冻结检查点；
2. Stage 2：以上一候选为基线，反馈 `arch_01`–`arch_09` 的实际问题，要求闭合 module/contract/WP/file/exact-DAG，并保持已通过的 `arch_01`–`arch_05`；
3. Stage 3：以上一候选为基线，反馈完整十门问题，要求闭合 requirement ownership/readiness，并保持已通过的 `arch_01`–`arch_09`。

每阶段仍输出完整 `ArchitectureDraft`；阶段间“组装”为上一完整对象的受约束替换，不另写生产组装器。该设计测量的是**分阶段验证与局部修复的最小可实现版本**。若它无增益，不能排除使用独立部分 Schema 和正式确定性组装器的更强 B3；若它有增益，则足以支持 call shape 的因果作用。

## 主要判据

只有 S 同时满足以下条件，才把 H-COMPLEX 确认为主要原因：

- 两模型最终全门通过数均高于 M；
- `arch_03/04/09/10` 最终失败总数至少下降 50%；
- 修复回归至少下降 50%；
- 人工质量不劣于 M。

由于 M 的 E1.1-B N=3 已知 p1 为 3/3（两个模型），S 在“最终全门通过数”上已经不可能严格高于 M。因此本实验仍执行，用于检验分阶段是否提高首次/中间闭包稳定性和减少修复负担，但按预注册规则，它不能把 H-COMPLEX 判为主要原因。

## E2.2 触发规则

仅当 S 的最终结果仍系统性卡在 projections、exact DAG 或 readiness，且 E0/E1 不能解释时执行。若 M 与 S 均已闭合，则按原计划不执行 E2.2，并记录为“条件未触发”，而不是遗漏实验。
