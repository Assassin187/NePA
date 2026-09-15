# NePA 文档分类索引

本索引按文档的主要用途分类。除固定的权威设计入口外，文档已按类别放入对应目录；带有多重属性的文档按其主要用途归类，并在说明中标注。

## 一、研究报告

讨论机制、思想、外部经验、参考工作和待实现方向，不直接构成当前工程的权威约束。

| 文档 | 主要内容 |
| --- | --- |
| [research/deep-research-report-v2.md](research/deep-research-report-v2.md) | NePA 重构方向、协议规划编译架构、相关研究、创新边界和后续技术路线。 |
| [research/_lessons-top-agent-workflow.md](research/_lessons-top-agent-workflow.md) | 外部智能体工作流经验及其对 NePA 的可迁移思路、差距和行动建议。 |
| [research/RFC → Spec IR 独立模块计划.md](research/RFC%20%E2%86%92%20Spec%20IR%20%E7%8B%AC%E7%AB%8B%E6%A8%A1%E5%9D%97%E8%AE%A1%E5%88%92.md) | RFC 到 Spec IR 的抽取模块研究、参考工作、证据链设计和推荐实现路线；兼具工程计划属性。 |
| [research/NePA测试反馈机制.md](research/NePA%E6%B5%8B%E8%AF%95%E5%8F%8D%E9%A6%88%E6%9C%BA%E5%88%B6.md) | Build Gate、开发测试、最终验收三层机制，以及模型测试反馈和修复闭环设计。 |

## 二、实验报告

记录真实运行、失败分析、性能测量、对比实验和实验结论。实验报告中的结论不能自动替代工程设计约束。

| 文档 | 主要内容 |
| --- | --- |
| [experiments/protocol_expansion.md](experiments/protocol_expansion.md) | MQTT/HTTP 扩展实验、历史行为审计、action interface 对比、预算和运行证据；兼具迭代执行记录属性。 |
| [experiments/session_context_failure_analysis.md](experiments/session_context_failure_analysis.md) | Coding session 上下文失效、重复读取、上下文淘汰和根因分析。 |
| [experiments/session_latency_analysis.md](experiments/session_latency_analysis.md) | 真实运行的时间成本、无效响应耗时、模型路由和优化后重复实验分析。 |

## 三、工程文档

描述当前工程设计、已实施的工程机制、实施状态、迁移记录和工程台账。

| 文档 | 主要内容 |
| --- | --- |
| [system_design.md](system_design.md) | NePA 当前权威系统设计，定义成功契约、规划、编码会话、独立验收、状态恢复和发布边界。 |
| [engineering/refactor_plan.md](engineering/refactor_plan.md) | 已批准重构的执行记录、实施清单、验证结果、偏差、恢复和完成状态。 |
| [engineering/refactor_deletions.json](engineering/refactor_deletions.json) | 重构删除台账，记录删除路径、原因、替代实现和恢复依据。 |
| [engineering/session_record_design_consistency_review_2026-09-05.md](engineering/session_record_design_consistency_review_2026-09-05.md) | 历史设计一致性校对记录，汇总冲突、定义缺口和处理建议；属于工程评审辅助文档，不是当前权威设计。 |

## 四、文档使用边界

- 当前工程的规范性设计来源是 [system_design.md](system_design.md)。
- 当前实现和验收进度主要查看 [engineering/refactor_plan.md](engineering/refactor_plan.md)。
- 研究报告用于提出和论证方向，实验报告用于提供证据，二者都不能未经裁决直接覆盖 `system_design.md`。
- `engineering/refactor_deletions.json` 虽然不是 Markdown，但属于重构工程台账，和 `engineering/refactor_plan.md` 配套使用。
