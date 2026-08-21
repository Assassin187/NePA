# M1-4a2 ArchitecturePlanner 提示词优化实验工作区

状态：临时实验目录；计划内实验已执行，Qwen Phase 2 单模型复验已在交互式环境重载后完成；正式配对与真正 B3 仍属结论边界
建立日期：2026-08-20
关联 change：`m1-4a2-architecture-planner-prompt-optimization`
基线 lineage：`daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772`

## 用途与边界

本目录集中保存该 change 无法完成后的诊断、实验假设、实验计划和后续实验记录，避免把尚未确认的判断写入权威设计或 OpenSpec 工件。

本目录是可抛弃的实验工作区：change 完成并且需要保留的结论已经转移到正式工件后，可以整体删除。

当前目录中的材料不具备以下效力：

- 不修改或替代 `project_docs/system_design.md`；
- 不修改该 change 的 proposal、design、spec 或 tasks；
- 不改变既有 lineage 的 V0/V1/V2 证据、筛选结果或 tie 结果；
- 不构成 M1-4a3 的 N=20、B1-B4、生产模型/调用形态冻结或责任人签署；
- 不授权在既有 lineage 上追加样本、篡改提示词快照或用新评价维度追认某版本获胜。

## 文件

- [01-root-cause-report.md](01-root-cause-report.md)：当前已确认事实、强推断、证据矩阵、实际产物质量和根因排序。
- [02-experiment-plan.md](02-experiment-plan.md)：用于区分评价标准、提示词/输入组织、任务复杂度和实现问题的纸面实验计划。
- [results/phase0/phase0-report.md](results/phase0/phase0-report.md)：盲评、gate-local 反事实和 fallback 重放。
- [results/phase1/phase1-report.md](results/phase1/phase1-report.md)：精确算法 prompt、example、ledger、Schema 去重和 N=5 复验。
- [results/phase2/preregistration.md](results/phase2/preregistration.md)：单调用与三阶段实验的冻结比较规则。
- [results/phase2/phase2-report.md](results/phase2/phase2-report.md)：两次 staged 执行、Provider 诊断和 H-COMPLEX 结论边界。
- [results/execution-summary.json](results/execution-summary.json)：跨 Phase 的机器可读指标与最终根因排序。
- `scripts/`：只服务于本临时目录的复验与候选质量统计脚本，不属于生产实现。

真实 Provider 调用按操作者给出的映射只在调用子进程内 export `CLAUDE_API/ALI_API/DS_API` 到 NePA 所需变量；实验产物不保存 key 值。每次实验的预注册、不可变输入引用、trace、原始结果和结论均继续放在本目录下，不覆盖基线 lineage。

## 主要证据入口

- [权威系统设计](../../project_docs/system_design.md)
- [change proposal](../../openspec/changes/m1-4a2-architecture-planner-prompt-optimization/proposal.md)
- [change design](../../openspec/changes/m1-4a2-architecture-planner-prompt-optimization/design.md)
- [change tasks](../../openspec/changes/m1-4a2-architecture-planner-prompt-optimization/tasks.md)
- [提示词开发 spec](../../openspec/changes/m1-4a2-architecture-planner-prompt-optimization/specs/architecture-prompt-development/spec.md)
- [ArchitectureDraft validator](../../nepa/speclib/architecture.py)
- [提示词开发协调器](../../nepa/calibration/s4_prompt_development.py)
- [当前 ArchitecturePlanner 提示词](../../nepa/agents/prompts/architecture_planner.md)
- [ArchitectureDraft 示例](../../nepa/schemas/examples/architecture-draft.example.json)
- [基线 lineage](../../runs/_calibration/s4-architecture/daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772)
