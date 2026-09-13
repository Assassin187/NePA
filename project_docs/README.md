# NePA 文档导航

`project_docs/` 保存 NePA 的工程文档，包括现行设计、需求、实施计划、执行记录、验收结果和交付说明。
`project_docs/research/` 只保存为 NePA 设计提供依据的研究报告、实验分析和历史评审记录。

## 现行工程文档

- [系统设计](system_design.md)：NePA 当前唯一的权威架构说明。
- [后续阶段需求与优先级](nepa-next-stage-requirements.md)：P0–P5 的目标、依赖和完成条件。
- [P0–P2 实施计划](nepa-p0-p2-plan.md)：当前阶段的实施决策、执行门和证据矩阵。
- [P0–P2 执行记录](p0-p2-progress.md)：当前实施与真实实验进度。
- [P0–P2 完成审计](p0-p2-completion-audit.md)：逐项核对完成证据和剩余缺口。
- [端到端重构执行记录](refactor_plan.md)：上一轮重构的授权、实施与验收记录。
- [协议扩展与交付证据](protocol_expansion.md)：MQTT 与 HTTP 生成、验收、费用和交付结果。
- [私有验收迁移表](private-oracle-migration.md)：旧验收断言到私有验收资产的逐项迁移关系。
- [MQTT 需求证据](mqtt_requirement_evidence.md)：110 条需求的声明、场景证据和明确缺口。
- `refactor_deletions.json`：重构删除项的机器可读台账，不是自然语言说明文档。

## 研究与分析

- [规划编译架构研究](research/deep-research-report-v2.md)
- [顶级智能体工作流经验](research/_lessons-top-agent-workflow.md)
- [串行基线分析](research/p0-serial-baseline.md)
- [Qwen 3.7 接入核实](research/qwen-capability-audit.md)
- [会话延迟分析](research/session_latency_analysis.md)
- [会话上下文失败分析](research/session_context_failure_analysis.md)
- [历史设计一致性评审](research/session_record_design_consistency_review_2026-09-05.md)

研究文档用于解释设计依据，不覆盖 `system_design.md`，也不能单独作为功能完成或协议符合性证明。
