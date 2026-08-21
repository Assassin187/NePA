# Phase 1 结果：提示词、示例、输入 ledger 与 Schema 呈现

日期：2026-08-20
状态：完成
真实调用环境：Qwen/DeepSeek 均通过操作者给出的进程内 key 映射；所有有效 arm 无 401、截断、身份漂移或基础设施无效。

## 1. 结论

E1.1 确认 H-PROMPT：把抽象自检改成 validator 等价的显式构造/复核算法，在不改变单调用形态、原始输入、当前 example 和 Schema 呈现的情况下，把 N=3 的双模型 p1 从基线 V2 的 0 提升到 3/3；N=5 复验中 Qwen 为 p0=2/5、p1=5/5，DeepSeek 为 p0=5/5、p1=5/5，且无 gate 回归、readiness issue 或基础设施异常。

E1.2、E1.3 均未命中预注册判据：对齐 example 没有改善 Qwen，反而引入 `arch_05` projection mismatch；deterministic ledger 只把 Qwen 的 `arch_10` 首次通过由 1/3 提到 2/3，同时引入 `arch_05`，整体 p0 不变。E1.4 确认 Schema 重复是效率缺陷但不是当前语义失败主因：provider prompt 减少 3,949 bytes，Qwen 首次 tokens_in 固定减少 1,005，双模型 p0/p1 和关键 gate 结果与重复 Schema 控制组完全相同。

## 2. N=3 单因素结果

| Arm | 唯一变化 | Qwen p0→p1 | DeepSeek p0→p1 | Qwen 首次失败 | 最终 readiness | 回归 |
|---|---|---:|---:|---|---:|---:|
| E1.1-B | 精确算法 prompt | 1/3→3/3 | 3/3→3/3 | 两次 `arch_10`，各 1 条 primary 漏项 | 0 | 0 |
| E1.2 | 只换对齐 example | 0/3→3/3 | 3/3→3/3 | `arch_10`×2；`arch_05`×1（3 条 projection mismatch） | 0 | 0 |
| E1.3 | 只加 deterministic ledgers | 1/3→3/3 | 3/3→3/3 | `arch_05`×2，其中一次并发 `arch_10` | 0 | 0 |
| E1.4 | provider prompt 只保留一份 Schema | 1/3→3/3 | 3/3→3/3 | 两次 `arch_10`，分别漏 4 条和 1 条 primary | 0 | 0 |

所有 arm 的 `arch_02/03/04/09` 在两个模型 p0/p1 都是 3/3。与基线 V2 的 p0 `arch_02/03/09=0/5`、Qwen `arch_04=0/5`、DeepSeek `arch_04=1/5` 相比，失败族在首次生成阶段已经被消除；这不是仅靠 repair 得到的表面提升。

## 3. E1.1 N=5 复验

N=3 原样本加同一冻结配置的 trial_004/005，未改 prompt、example、输入或 Schema。

| 模型 | p0 | p1 | p0 逐门异常 | p1 逐门 | repair 回归 |
|---|---:|---:|---|---|---:|
| Qwen | 2/5 | 5/5 | `arch_05=4/5`、`arch_10=2/5`；其余 8 门 5/5 | 10 门 5/5 | 0 |
| DeepSeek | 5/5 | 5/5 | 10 门 5/5 | 10 门 5/5 | 0 |

这组结果足以确认“现有 V0→V1→V2 的规则表达/repair 指令没有把真实失败证据转成可执行算法”是主要根因；也反对“任务在当前单调用下天然无法完成”作为主要解释。它不等于正式 change 过筛：Qwen p0=0.40，低于正式 0.80，且 `arch_10` 在 p0 重复失败；新 prompt 也是诊断性 V3，不在原 change 允许的最多两次修改内。

## 4. 各单因素判定

### E1.1 精确算法：支持 H-PROMPT

相对 V2，真正新增的不是更多“仔细检查”语句，而是：non-DEFINITION/DEFINITION 精确集合、s5/s6 白名单、contract/module/WP 投影集合等式、contract-derived exact dependency、required slot 逐槽位闭包、task test 的共同后继交集，以及基于 previous candidate 的最小修复与全门复核。新结果直接消除了基线反复出现的 `arch_03/04/09`，把 `ARCH_TEST_READINESS_UNCLOSED` 最终数降为 0。

### E1.2 对齐 example：反对 H-EXAMPLE 为主因

两个模型没有获得预注册的 50% contract/file/slot 错误下降，因为控制组这些错误已经为 0；Qwen 反而新增 `ARCH_MODULE_CONTRACT_SET_MISMATCH`。example 原有语义偏差仍是事实，但在精确算法 prompt 存在时不是剩余稳定性瓶颈。

### E1.3 deterministic ledger：反对 H-INPUT 为主因

ledger 对 Qwen `arch_10` 有一个 trial 的局部正向信号，但整体 p0 仍为 1/3，且新增 `arch_05`。DeepSeek 维持 3/3。没有跨模型、跨 gate 的稳定增益，不应把 ledger 引入正式输入路径作为本次根因修复。

### E1.4 Schema 去重：确认效率问题，反对其为语义主因

| 指标 | 重复 Schema | 单份 Schema | 变化 |
|---|---:|---:|---:|
| 首次 provider prompt bytes | 68,545 | 64,596 | -3,949（-5.76%） |
| Qwen 首次 tokens_in | 19,860 | 18,855 | -1,005（-5.06%） |
| DeepSeek 首次 tokens_in（稳定样本） | 21,546 | 20,404 | -1,142（-5.30%） |
| Qwen p0→p1 | 1/3→3/3 | 1/3→3/3 | 相同 |
| DeepSeek p0→p1 | 3/3→3/3 | 3/3→3/3 | 相同 |

单份 Schema 的总观测 latency 也更低，但 Provider 延迟噪声较大，不作为主要判据。DeepSeek 重复 Schema 组有一次相同 prompt bytes 却报告 47,579 tokens_in，说明 provider usage 计数存在样本波动；因此只把确定的字节差和稳定首次 token 差作为效率证据。

## 5. 实际候选质量复核

`quality-inspection.json` 对唯一基线 pass 和精确 prompt 的 10 个 N=5 最终 pass 候选重新运行同一 validator，并统计职责分布。11 个候选均再次通过，但工程质量并不均匀：

- 基线唯一 pass：54 primary、4 supporting，最大单 WP primary 占比 48.1%；独立 DeepSeek 与本地复核均排第一，结构最清楚。
- 精确 prompt 的 Qwen：5 个候选中 3 个存在完全没有 requirement responsibility 的 WP；2 个最大单 WP primary 占比达到 81.5%；3 个产生无消费者的 app task contract。它们机械闭合，但存在职责集中或“空集成 WP”问题。
- 精确 prompt 的 DeepSeek：5 个候选均把 primary 分散到所有 WP，最大占比 42.6%–51.9%，通常比 Qwen 内聚；但 supporting 数在 3–78 之间大幅波动，说明 prompt 仍没有稳定控制 supporting 的必要性。
- 所有这些 pass 架构仍使用 mutable `.c` 文件作为 task contract 的 `interface_files`；这是现有 validator/Delivery Constraints 的机械要求，却被两名独立盲评者视为粗糙或不稳定的工程接口。

因此，精确算法 prompt 已解决“过 validator”的主要问题，但没有解决“职责内聚与 contract 抽象质量”的评价缺口。不能把 p1=100%解释为架构质量已经达到可直接交付的充分条件。

## 6. Phase 1 决定

1. H-PROMPT：已确认，是生成失败的首要根因。
2. H-EXAMPLE：现有 example 有偏差，但作为主要因果解释被本实验反对。
3. H-INPUT：deterministic ledger 不是主要因果解释。
4. H-IMPL/Schema duplication：确认效率缺陷，不是语义主因。
5. H-COMPLEX：作为“任务天然不适合单调用”的主因已被明显削弱；仍需 E2.1 检查分阶段是否提高 Qwen 首次稳定性，但 M 组 p1 已达到天花板。
