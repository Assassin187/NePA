# Phase 0 结果：零调用评价审计

状态：完成。E0.2、E0.3 完成；E0.1 获得 Qwen 的 Schema-valid 盲评、DeepSeek 的完整实质盲评（原始修复响应误重复 candidate-e/f，确定性去重后重新通过 reviewer Schema）以及 Codex 的代码级复核。Claude 代理因流中模型身份变化无效，不计入质量证据。

## E0.1 分层质量审阅

六个候选已经匿名化、复制并固定 SHA-256。最初按错误环境变量映射发起的 Qwen/DeepSeek 请求返回 HTTP 401；按操作者给出的 `ALI_API→NEPA_QWEN_API_KEY`、`DS_API→NEPA_DS_API_KEY` 进程内映射重试后获得有效内容。未记录任何 secret 值，401 与无效 Claude 调用均没有被计入质量判断。

三份审阅排序为：

| 评审 | 排序（优→劣） | 关键观察 |
|---|---|---|
| Qwen（独立、Schema-valid） | f, e, c, b, d, a | 把 validator-fail 的 f 排第一，把唯一 pass 的 c 排第三；认为 `.c` task contract 不是稳定接口 |
| DeepSeek（独立、原始响应去重后 Schema-valid） | c, b, f, d, e, a | 把唯一 pass 的 c 排第一，但 validator-fail 的 b 排第二；同样批评 `.c` interface contract |
| Codex（本地复核，非独立） | c, b, e, f, a, d | 更重视机械闭包，同时也指出 task contract 边界粗糙 |

DeepSeek 原始修复响应包含 8 条 review，其中 e/f 各重复一次，超过 Schema 的 `maxItems=6`。`reviewer-deepseek-deduplicated.json` 只保留每个 `candidate_id` 的首次出现，未改评分、文字、ranking 或 rationale；去重后的 review 主体经同一 reviewer Schema 复验有效。该修复的性质是响应封装修复，不是事后改变评审意见。

Codex 代码级审阅的临时排序为：

1. candidate-c：唯一 validator-pass 样本；机械闭包最好，但 task-ready contract 暴露整组 `.c` 文件，边界偏粗；
2. candidate-b：职责与 integration WP 合理，但依赖不是 task contract 推导，机械失败与工程直觉存在张力；
3. candidate-e：task contracts 和 DAG 较好，但缺两项 s5 interface slot；
4. candidate-f：整体关系可读，但把 s5 header 当 task-ready boundary；
5. candidate-a：所有 contract 均 s5-ready、DAG 为空，跨模块测试不能收敛；
6. candidate-d：supporting responsibility 大量扩散且 DAG 为空，readiness 最差。

三份排序的共同点是 candidate-a 靠后、candidate-c 在前三，说明 validator 与工程质量并非完全反向；但 Qwen 把失败样本 f/e 排在唯一 pass 样本 c 之前，DeepSeek 把失败样本 b 排第二，确认“全门通过”与人工工程质量并不等价。仓库中没有可运行的 TaskPlanner/Linker/PlanCritic 下游管线，所以未伪造下游结果。

## E0.2 gate-local 反事实修复

| 样本 | 原失败 | 局部修改 | 完整复验结果 | 含义 |
|---|---|---|---|---|
| V2/DeepSeek/trial_004 | arch_03 | task contract 改用 owner 的 mutable `.c` | arch_03 通过，但新失败 arch_09 | 原错误真实；同一错误 contract 同时承担 required s5 slot，03/09 强耦合 |
| V2/Qwen/trial_001 | arch_09 | 增加两个 owner/provider=s5、consumer 为空的 slot contract | 全门通过 | arch_09 能被“无人消费”的形式 contract 满足，validator 对实际可用性偏弱 |
| V0/DeepSeek/trial_004 | arch_08 | 删除不能由 task contracts 推导的 app dependencies | arch_08 通过，但新产生 10 条 arch_10 readiness | arch_08 精确等式与 arch_10 convergence 强耦合；原额外依赖虽非法却具工程用途 |
| V2/Qwen/trial_002 | arch_10 | 全部 54 个 non-DEFINITION primary 移到 wp-app | 全门通过 | mechanical closure 可用语义很差的 ownership 集中化满足 |
| V2/DeepSeek/trial_001 | arch_10 | 同上并删除所有 supporting roles | 全门通过 | supporting spread 是失败原因之一，但 arch_10 不评价职责内聚性 |

结论：

- 不支持“validator 失败全是误报”；每个原失败都对应真实关系冲突。
- 确认 gate 间存在强耦合，局部修一门会暴露另一门，支持 H-COMPLEX。
- 确认 validator 只证明机械闭包，不足以证明 contract 有消费者或 ownership 语义合理；这支持“评价能力不足”，但尚不支持放松现有 hard gates。

## E0.3 fallback 离线重放

| 版本 | 授权 tuple | worst-model 最终逐门通过率 | worst-model 失败门中位数 | 最终 issue 总数 | repair 回归 |
|---|---|---:|---:|---:|---:|
| V0 | `[0,0,1,0]` | 0.78 | 2 | 91 | 7 |
| V1 | `[0,0,1,0]` | 0.80 | 2 | 88 | 2 |
| V2 | `[0,0,1,0]` | 0.80 | 1 | 102 | 3 |

替代指标不产生一致胜者：最终逐门通过率、issue 总数和 repair 回归偏向 V1；每候选失败门中位数偏向 V2。因此确认 H-RANK 的第一部分——授权 tuple 丢失实际质量差异——但没有证据授权一个唯一替代 winner。

workspace 内价格配置全为 0。真实外部价格没有核实，因为 workspace boundary 不允许在未获明确授权时查询外部位置；即使真实价格能打破 tie，也不能证明最低成本版本有最佳架构质量。

## Phase 0 决定

评价标准存在“只测机械闭包、不测语义内聚”的确认缺口，现有 hard-gate 失败大多真实，而且 gate-local 修复揭示了显著关系耦合。按预注册停止规则进入 Phase 1，通过提示词/输入单因素实验判断生成失败能否在单调用下改善；不先放松 validator。

Phase 1 的凭据阻塞已通过操作者提供的进程内环境映射解除；错误映射产生的 401 只作为基础设施诊断保留。
