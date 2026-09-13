# NePA 研究资料

本目录只保存为 NePA 工程设计提供理论或实证依据的材料。这里的结论可以支持设计决策，但不直接定义
生产行为，也不能单独证明某项功能、协议符合性或真实生成已经完成。若研究结论被采用，最终约束应写入
父目录的 `system_design.md`、需求、实施计划或验收文档。

## 架构与方法研究

- `deep-research-report-v2.md`：协议规划编译架构、义务图、类型化填空和计划修订边界。
- `_lessons-top-agent-workflow.md`：外部智能体工作流经验及其对 NePA 的适用性分析。
- `session_record_design_consistency_review_2026-09-05.md`：旧设计文档的一致性问题和历史评审结论。

## 实验与性能分析

- `p0-serial-baseline.md`：DeepSeek 历史运行的任务、时间、费用和失败类型基线。
- `session_context_failure_analysis.md`：源码观察逐出、事务配对错误和上下文机制根因。
- `session_latency_analysis.md`：真实生成中的 API 等待、无效动作、优化前后时间和费用。
- `qwen-capability-audit.md`：Qwen 3.7 精确模型、地域、能力、usage 字段和人民币价格依据。

当前工程状态请返回上级目录查看 `../README.md`、`../p0-p2-progress.md` 和
`../p0-p2-completion-audit.md`。
