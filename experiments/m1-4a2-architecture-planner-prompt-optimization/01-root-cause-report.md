# M1-4a2 无法完成的根因报告

报告日期：2026-08-20
证据截止：基线 lineage `daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772` 全量产物，以及本临时目录的 Phase 0–2 根因区分实验
状态标签：`已确认事实`、`强推断`、`尚待验证`。第 13 节为实验后的最终修订结论；若与实验前强推断冲突，以第 13 节为准。

## 1. 结论摘要

change 当前不能完成的直接原因不是 provider、Schema、截断或代码崩溃，而是：V0、V1、V2 均未通过筛选，固定 fallback 又得到完全相同的 `[0, 0, 1, 0]`，规范要求抛出 `PROMPT_SELECTION_TIE`，因此没有 selected prompt、selection 或 handoff，tasks 7.6 和 8.2 无法闭合。

上游质量根因按实验后的证据强度排序如下：

1. **已确认：首要根因是 V0→V1→V2 没有把失败证据表达成模型可执行的精确构造与最小修复算法。** 冻结单调用、原始输入、当前 example 和 Schema 呈现，只替换规则文本后，N=5 的 Qwen 从 p0/p1=0/5、0/5 提升为 2/5、5/5，DeepSeek 提升为 5/5、5/5；`arch_03/04/09` 与最终 readiness 问题被消除，且没有修复回归。
2. **已确认：`ARCH_VALIDATE` 的 hard-gate 失败大多是真实关系冲突，但其评价目标不足以代表工程架构质量。** gate-local 修复能闭合真实错误，也能用“无消费者 contract”或“把全部 primary 集中到 app”等语义较差手段全门通过；两名独立盲评者都把至少一个 validator-fail 样本排在唯一 pass 样本之前。
3. **已确认：fallback 在当前低成功率与零价格区域失去分辨率。** `[0,0,1,0]` 只表示 worst-model 的 p1/p0 都为 0、Schema 为 1、成本维度为 0，不表示 V0/V1/V2 质量相同。替代逐门/issue/回归指标能显示差异，但没有一致唯一 winner。
4. **已确认：单调用全量闭包增加了 Qwen 首次稳定性难度，但“天然过难、不适合单调用”不是主要根因。** 精确 prompt 在相同单调用下让 DeepSeek 5/5 p0、双模型 10/10 p1；Qwen p0 仍为 2/5，说明复杂度是次要残余因素而非无法完成的主解释。
5. **已确认：example、deterministic ledger 和 Schema 重复不是主要语义根因。** 单独替换 example 或加入 ledger 没有跨模型稳定增益；Schema 去重使 provider prompt 减少 3,949 bytes、首次输入 token 约降 5%，但 p0/p1 与关键 gate 结果完全相同。
6. **尚待验证：真正的部分 Schema + 确定性阶段组装是否能进一步提高 Qwen p0 与工程质量。** 当前 E2.1 是保持完整 Schema 的最小分阶段验证/修复实验，不能替代正式 B3 设计实验；这不影响“缺少精确算法是首要根因”的结论。

## 2. change 的终止状态

OpenSpec 当前显示 34/36 tasks 完成，未完成的是：

- 7.6：要求 selected immutable prompt 与源模板匹配，并发布 handoff；
- 8.2：要求完整重算并存在满足终态的 selection/handoff 证据。

提示词开发 spec 明确规定：V2 后没有版本过筛时按四维 tuple 排名；总成本之后仍精确相等时，必须产生 machine-readable tie，并阻塞 M1-4a3，等待权威决定。实现中的 `_fallback_tuple()` 与 `_compare_fallback()` 与这条规则一致。因此当前 tie 是**按现有规范执行成功后的受控失败**，不是 coordinator 偶然没有选出版本。

系统设计写了 fallback 应产生唯一候选，但没有定义精确 tie 的处置；change spec 选择了显式阻塞。这暴露的是设计层未保证唯一性的缺口，不是运行期证据损坏。

## 3. 双模型逐版本总览

每个单元均为 N=5。`p0` 即首次 Schema-valid 候选的全十门语义通过率；`p1` 是一次语义修复后的累计全十门通过率。

| 版本 | 模型 | Schema 首次/最终 | p0 | p1 | 截断 | 基础设施无效 |
|---|---|---:|---:|---:|---:|---:|
| V0 | Qwen | 5/5 → 5/5 | 0/5 | 0/5 | 0 | 否 |
| V0 | DeepSeek | 5/5 → 5/5 | 0/5 | 1/5 | 0 | 否 |
| V1 | Qwen | 5/5 → 5/5 | 0/5 | 0/5 | 0 | 否 |
| V1 | DeepSeek | 5/5 → 5/5 | 0/5 | 0/5 | 0 | 否 |
| V2 | Qwen | 5/5 → 5/5 | 0/5 | 0/5 | 0 | 否 |
| V2 | DeepSeek | 5/5 → 5/5 | 0/5 | 0/5 | 0 | 否 |

六组身份均稳定，没有格式修复，没有声明 p2。每组实际发生 5 次初始调用和 5 次语义修复调用。由此可以排除“Schema 输出失败”“上下文被截断”“某模型批次基础设施失效”“模型身份漂移”作为当前主因。

## 4. 逐门通过率与修复增益

下表为 `p0 通过数 → p1 通过数`，分母均为 5。

| 版本/模型 | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| V0/Qwen | 5→5 | 0→5 | 0→3 | 0→4 | 5→5 | 5→4 | 0→5 | 0→5 | 0→3 | 0→0 |
| V0/DeepSeek | 5→5 | 0→5 | 0→3 | 2→3 | 5→5 | 4→5 | 2→3 | 2→2 | 0→5 | 0→4 |
| V1/Qwen | 5→5 | 0→5 | 0→4 | 0→4 | 5→4 | 5→5 | 0→5 | 0→5 | 0→3 | 0→0 |
| V1/DeepSeek | 5→5 | 0→5 | 0→2 | 0→5 | 5→5 | 4→5 | 0→5 | 1→4 | 0→5 | 0→3 |
| V2/Qwen | 5→5 | 0→5 | 0→4 | 0→4 | 5→5 | 5→5 | 0→5 | 0→5 | 0→3 | 0→1 |
| V2/DeepSeek | 5→5 | 0→5 | 0→3 | 1→4 | 5→5 | 4→5 | 1→4 | 1→4 | 0→4 | 0→1 |

关键事实：

- p0 的 `arch_02/03/09/10` 是 30/30 失败；`arch_04/07` 是 27/30 失败，`arch_08` 是 26/30 失败。
- 语义修复把 `arch_02` 从 0/30 修到 30/30，把 `arch_03` 修到 19/30，`arch_04` 修到 24/30，`arch_09` 修到 23/30，但 `arch_10` 只修到 9/30。
- 修复不是局部修复：共出现 12 次 gate-trial 回归，分布为 `arch_04=3`、`arch_07=3`、`arch_08=4`、`arch_05=1`、`arch_06=1`。
- p1 的 30 个候选中，1 个全门通过，15 个只失败一门，7 个失败两门，其余 7 个失败三至五门。说明模型常能接近闭包，但一次全局重写式 repair 难以稳定保住已满足关系。

最终 p1 失败门出现次数为：

| Gate | 失败候选数 / 30 |
|---|---:|
| `arch_03` | 11 |
| `arch_04` | 6 |
| `arch_05` | 1 |
| `arch_06` | 1 |
| `arch_07` | 3 |
| `arch_08` | 5 |
| `arch_09` | 7 |
| `arch_10` | 21 |

最终失败形成两个主要共现族：

1. contract/provider/gate/projection/file-slot 族：`arch_03/04/07/08/09`；
2. requirement ownership/test readiness 族：`arch_10`，有时与 `arch_09` 同时出现。

这不是一个单一拼写错误，而是两个跨对象关系闭包问题。

## 5. 问题码证据

p0 的主要问题总数：

| 问题码 | 数量 |
|---|---:|
| `ARCH_REQUIREMENT_PRIMARY_INVALID` | 1628 |
| `ARCH_DELIVERY_CONSTRAINT_VIOLATION` | 210 |
| `ARCH_MODULE_FILE_INVALID` | 180 |
| `ARCH_CONTRACT_GATE_INVALID` | 113 |
| `ARCH_DEPENDENCY_MISMATCH` | 88 |
| `ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH` | 58 |
| `ARCH_CONTRACT_PROVIDER_INVALID` | 58 |
| `ARCH_WORK_PACKAGE_FILE_PARTITION` | 12 |

其中 `arch_10` 的 p0 消息可进一步分成 1147 条“DEFINITION requirements cannot have primary ownership”和 481 条“each non-DEFINITION requirement needs exactly one primary”。这说明模型没有建立“所有且仅有 non-DEFINITION requirement 恰好一个 primary”的精确 ledger。

p1 的主要问题总数：

| 问题码 | 数量 |
|---|---:|
| `ARCH_TEST_READINESS_UNCLOSED` | 167 |
| `ARCH_CONTRACT_GATE_INVALID` | 37 |
| `ARCH_CONTRACT_PROVIDER_INVALID` | 21 |
| `ARCH_REQUIREMENT_PRIMARY_INVALID` | 14 |
| `ARCH_INTERFACE_SLOT_UNCLOSED` | 14 |
| `ARCH_DEPENDENCY_MISMATCH` | 10 |
| `ARCH_WORK_PACKAGE_CONTRACT_SET_MISMATCH` | 9 |
| `ARCH_WORK_PACKAGE_FILE_PARTITION` | 6 |
| `ARCH_MODULE_CONTRACT_SET_MISMATCH` | 3 |

修复把 primary 的数量问题显著压下去后，主瓶颈转成 `ARCH_TEST_READINESS_UNCLOSED`：一个 task-gated test 引用的全部 requirement primary/supporting work packages，必须在依赖图上存在共同可达的 convergence work package。只分配 requirement 而不构造对应 DAG 闭包无法通过。

## 6. V0 → V1 → V2 是否针对证据

### V1

V1 只把原来的普通 final self-check 扩成“deterministic final reconciliation”，一次要求检查 ownership、dependency、contract endpoint、module/WP projection、file namespace 和 DAG，并再重复一次。

已确认的问题：

- revision 预期同时改善 `arch_02/03/04/07/08/09/10` 和首次语义通过率，范围太宽，不能形成单一可证伪的缺陷假设；
- 没有给出每一门对应的精确集合等式、白名单或构造顺序；
- p0 仍为双模型 0/5，说明这段自检没有进入首次生成的可靠行为；
- V0 双模型最终失败门出现总数为 21，V1 降到 16，说明局部修复质量有改善，但唯一全门通过样本反而消失。

结论：V1 方向相关，但表达过于抽象，不能证明模型真的执行了 validator 等价算法。

### V2

V2 新增 requirement-responsibility ledger，预期只针对 `arch_10`。

已确认的问题：

- “each applicable requirement”没有明确为“所有且仅有 non-DEFINITION requirement”；
- 没有明确 DEFINITION requirement 的处理方式；
- 没有写出每个 task-gated test 的 convergence 集合是所有 primary/supporting WPs 后继集合的交集；
- 没有说明交集为空时应建立集成 WP、补依赖，还是收缩不必要 supporting roles；
- 没有要求 repair 保持已经通过的 contract/file/dependency 门，只修失败关系。

观察结果：V1 的 readiness issue 数为 Qwen 44 + DeepSeek 12 = 56；V2 为 Qwen 31 + DeepSeek 39 = 70。`arch_10` 最终通过候选数从 V1 的 3/10 降到 V2 的 2/10。Qwen 略有改善，DeepSeek 明显恶化。

结论：V2 确实瞄准失败证据，但只表达了 assignment，不足以表达 readiness closure；跨模型净效果为负。强推断是它还可能诱导更多 supporting assignment，从而扩大需要共同收敛的 WP 集合。

## 7. 实际实验产物质量

本次判断不仅依据聚合指标，也检查了全部 60 个 p0/p1 候选的结构摘要，并详细检查代表性候选与 validation。

### 唯一全门通过样本

`v0/deepseek/candidates/trial_002_p1.json` 构造了 codec、session、net、server 四个模块，并让 server 成为关键工作的共同后继，因此通过机械闭包。

它证明 validator 约束可满足，但并非显然的“黄金架构”：部分 behavior requirement 的 primary 分配仍有工程语义争议，而且 task-ready contracts 使用了较大的 `.c` 实现文件边界集合。由此确认：全门通过是必要的机械一致性证明，不是充分的架构质量证明。

### 只差一门的代表样本

- `v2/qwen/candidates/trial_001_p1.json`：只失败 `arch_09`，漏掉两个必需的 s5 interface contract，validation 给出两条 `ARCH_INTERFACE_SLOT_UNCLOSED`。失败真实、局部且可定位。
- `v2/deepseek/candidates/trial_004_p1.json`：只失败 `arch_03`，task-ready contracts 使用 s5 headers，产生三条 contract gate 问题。失败真实。
- `v0/deepseek/candidates/trial_004_p1.json`：只失败 `arch_08`，声明的 `depends_on` 与 contract 导出的精确依赖不一致。失败真实。
- `v2/qwen/candidates/trial_002_p1.json`：只失败 `arch_10`，有 9 条 readiness 未闭合；work packages 基本都 `depends_on=[]`，确实不存在共同后继。
- `v2/deepseek/candidates/trial_001_p1.json`：只失败 `arch_10`，有 13 条 readiness 未闭合；依赖图同样缺少实际 convergence。

这些样本支持两个判断：一是 validator 不是凭空拒绝看起来良好的对象；二是很多 p1 已有较高局部质量，fallback 用 0/1 的全候选通过率无法利用这些质量差异。

## 8. 评价标准、screening 与 fallback

### `ARCH_VALIDATE`

已确认：实现与系统设计列出的 arch_01 至 arch_10 机械关系基本一致；抽样失败能回溯到实际字段冲突。没有发现足以解释当前结果的 validator bug。

强推断：标准整体没有“过严到不可满足”，但它只测机械闭包，对职责分配的语义优劣可能偏弱。应通过盲评和下游 TaskPlanner/Linker/PlanCritic 结果验证，而不是直接放松 gate。

### screening

当前每个模型都必须同时满足：Schema=1.00、p1=1.00、p0≥0.80、零截断、基础设施有效、身份稳定、没有同一硬门在至少两次 p0 中失败。

该标准用于选择需要进入 M1-4a3 的稳定提示词，因此“严格”本身不是错误。不过在 N=5 时，p0≥0.80 已意味着最多只有一次 p0 失败，因此“同一 gate 至少两次 p0 失败”大体是冗余约束。更重要的是，本次所有组合 p0=0，微调阈值不会改变结果；必须先提高生成能力或改变任务分解。

### fallback `[0,0,1,0]`

四个维度的真实含义是：

1. `min_model_p1 = 0`：每个版本至少有一个模型在 5 次语义修复后仍是 0/5 全十门通过；
2. `min_model_p0 = 0`：每个版本至少有一个模型首次全十门通过为 0/5，实际上六组全是 0/5；
3. `min_model_schema = 1`：两个模型的 Schema 最终率都是 1.00；
4. `-total_cost = 0`：配置中两个模型的输入/输出单价都为 0，所以记录成本均为 0。

V0 DeepSeek 的 p1=0.2 被 robust minimum 丢弃，因为同版本 Qwen p1=0。V0/V1/V2 最终失败门出现总数分别是 21、16、18，也被 tuple 丢弃。

因此 tie 的真实含义不是“三个提示词实际质量相同”，而是“授权的四维排序在当前低全通过率、零成本数据上没有分辨率”。`PROMPT_SELECTION_TIE` 是规范要求的诚实结果。

## 9. 单调用任务复杂度

冻结输入要求模型在一个 ArchitectureDraft 中同时闭合：

- 110 个 requirements，其中 56 个 DEFINITION，54 个 non-DEFINITION 必须恰好各有一个 primary；
- 23 个 tests，全部是 task-gated，每个 test 最多关联 21 个 requirement；
- 19 个文件槽位，其中 6 个 s5_frozen、13 个 s6_owned；
- 2 个必需 internal interface slots；
- modules/contracts、module↔WP projections、file partition、contract-derived exact dependency DAG 和 test readiness convergence。

没有截断，`max_tokens=65536`，所以当前证据指向关系组合复杂度而不是单纯上下文容量不足。

系统设计已经把 S4 标记为最大不确定性和 R-12 高风险，并把 B3 分阶段方案安排在 M1-4a3：先 module/contract，再 WP/file/DAG，再 responsibility/readiness。本次失败恰好集中在这些跨阶段交叉关系上。

强推断：当前稳定筛选目标对于单次全量生成过难。但不能声称任务绝对不可能，因为已有 1/30 p1 全门通过、16/30 p1 至少达到“全过或只差一门”。正确说法是：**当前 call shape 与 repair 不能稳定满足双模型门槛。**

## 10. 其他实现和输入工件问题

### 已确认事实

1. 当前 ArchitectureDraft example 把 `include/core/interface.h` 作为 task-ready contract interface，同时又让 module/WP 拥有该 header；真实 delivery constraints 中 s5 headers 是 frozen，不应按 mutable task-ready implementation 边界示范。
2. 当前两个 provider 都不支持 native structured output；模板已经嵌入一次完整 Schema，`LLMClient._fallback_request()` 又附加一次完整 Schema，最终 provider prompt 中 Schema 重复。
3. V0/V1/V2 的实际输入/输出价格配置均为 0，使 fallback 成本维度恒定。
4. `outcome.json` 只记录 `passed screening` 或 `failed screening`，没有记录 revision 声明的 expected gates/metrics 是否实际改善，弱化了两次修改的因果审计。
5. `_leave_one_out_sensitive()` 只重算 Schema、p1、p0 和 truncation，未包含正式 screening 的 repeated-gate、identity、infrastructure 条件。当前样本离阈值很远，所以没有改变本次 ambiguity=`none`，但实现语义不完全同构。
6. 当前 prompt 的输入 delimiters 没有说明每种输入是什么、如何使用；repair_context 虽含上次完整候选和具体问题，但没有要求“对上次候选做最小修改并保持已通过 gates”。

### 强推断

1. example 语义偏差与 p0 中普遍的 s5/file/contract 错误存在因果关系；
2. Schema 重复和 repair prompt 达约 89–98 KB 会降低重要约束的信噪比，但目前没有截断证据；
3. outcome 不记录预期改善使流程可以“合法地”继续采用效果不佳的假设；
4. 零价格若只是占位值，会意外消除最后一个 fallback 区分维度。

## 11. 证据排序后的根因树（实验前判断，已由第 13 节修订）

以下是制定实验计划时的判断轨迹，保留用于审计；最终排序以第 13 节为准。

```text
change 未完成
└── 直接终止条件（已确认）
    └── V0/V1/V2 未过筛 + fallback 精确 tie
        ├── 全版本 p0=0；最差模型 p1=0（已确认）
        │   ├── 提示词只给抽象自检，未给精确构造算法（已确认）
        │   ├── example/input/repair 组织与目标关系不匹配（事实 + 强推断因果）
        │   └── 单调用要求多套全局关系同时闭包（强推断主因）
        └── fallback 在低成功率区域退化（已确认）
            ├── 忽略逐门质量、问题数和修复回归
            └── 价格配置为零，成本维度失效
```

按“对生成失败的贡献”排序：

1. 单次全量关系闭包与非最小 repair 的结构性难度——强推断，高贡献；
2. 精确约束表达、输入 ledger、示例和 repair 指令不足——事实明确，因果贡献为强推断；
3. fallback 语义与实际质量错位——已确认，是无法选择的直接原因，但不是候选失败的原因；
4. screening 严格——已确认，但当前 p0=0 时不是边际原因；
5. validator 核心 gate 错误——当前证据不支持，优先级最低；
6. Schema 重复、零价格、outcome/leave-one-out 语义等次要实现问题——已确认存在，具体因果贡献尚未验证。

## 12. 实验前结论边界

以下事项尚未验证，不能写成正式结论：

- 价格 0 是真实免费价格还是占位配置；
- 唯一 validator-pass 候选在 TaskPlanner/Linker/PlanCritic 下是否优于只差一门候选；
- 修正 example、添加 deterministic ledgers、精确 checklist 各自贡献多大；
- 把任务拆成 B3 三阶段后是否在同样模型和样本量上显著提升；
- 23 个 tests 全部标记 task-gated 是否符合 test 作者真实意图；
- 任何替代 fallback 分数是否与最终 S4 下游质量相关。

这些问题由 [02-experiment-plan.md](02-experiment-plan.md) 中的最小实验区分。

## 13. 实验执行后的根因汇总结论（最终修订）

本节汇总 Phase 0–2；它覆盖第 1–12 节中实验前的因果强推断。详细数据分别见：

- `results/phase0/phase0-report.md`；
- `results/phase1/phase1-report.md`；
- `results/phase2/phase2-report.md`。

### 13.1 实验矩阵与判定

| 假设 | 实验 | 结果 | 判定 |
|---|---|---|---|
| H-EVAL | 双模型匿名盲评 + gate-local 反事实 | hard-gate 冲突真实；pass 可由空 consumer contract、primary 集中化满足；盲评对 pass/fail 排序不一致 | 确认 evaluator 只测机械闭包，不能充分表示工程质量；不支持直接放松 hard gates |
| H-RANK | fallback 离线重放 | 三版本 tuple 同为 `[0,0,1,0]`，逐门/issue/回归指标存在差异但 winner 不一致 | 确认 tuple 退化且信息不足；不能追认某旧版本获胜 |
| H-PROMPT | 只换精确算法 prompt，N=3 + N=5 | Qwen N=5 p0/p1=2/5→5/5；DeepSeek=5/5→5/5；最终 readiness=0、回归=0 | 确认首要根因 |
| H-EXAMPLE | 只换对齐 example，N=3 | Qwen p0 由 1/3 降为 0/3，出现 arch_05；DeepSeek不变 | 反对作为主要根因 |
| H-INPUT | 只加 deterministic ledgers，N=3 | Qwen/DeepSeek 总 p0/p1 与控制相同；局部 10 改善伴随 05 新错 | 反对作为主要根因 |
| H-IMPL/Schema | 只去除 fallback 重复 Schema，N=3 | prompt bytes -5.76%，tokens_in 约 -5%，p0/p1 与关键门完全相同 | 确认效率缺陷；反对作为语义主因 |
| H-COMPLEX | M vs 三阶段 S，N=3 | M 已双模型 p1=3/3；S 首次运行被 Qwen HTTP 400 中断，整组重跑 Qwen 因 `Arrearage` 全无效 | 现有证据反对“天然过难”为主因；严格双模型增益尚待 Provider 恢复后验证 |

### 13.2 已确认事实

1. **change 的直接阻塞逻辑没有误执行。** 原 V0/V1/V2 全部未过筛，fallback tuple 精确相同，spec 要求 `PROMPT_SELECTION_TIE`；没有 selected prompt/handoff 是合规终止。
2. **原三版修改没有命中可执行语义。** V1 的全局自检与 V2 的 responsibility ledger 没有写出 validator 所需的集合等式、构造顺序、共同后继算法和最小 repair；六组基线 p0 全为 0/5。
3. **精确算法提示是决定性因果因素。** 保持单调用、原始 input、当前 example、重复 Schema 和模型参数不变，只换规则文本，就让 DeepSeek N=5 首次 5/5 全门通过，并让 Qwen/DeepSeek repair 后都 5/5；基线反复出现的 `arch_03/04/09` 与最终 `ARCH_TEST_READINESS_UNCLOSED` 消失。
4. **现有筛选门对“稳定可直接进入下游”的目标并非简单错误或过严。** 新 prompt 的 Qwen p0 仍为 2/5，低于正式 0.80，并有重复首次 gate 失败，所以它仍不能在不改 change 协议的情况下追认为正式 selected prompt。小幅下调阈值无法解释原来的 0/5。
5. **validator hard gates 基本有实现依据，但全门 pass 不是充分质量条件。** 反事实修复与盲评共同证明：它能捕获真实 file/contract/DAG/readiness 冲突，同时允许形式闭包而语义内聚较差的方案。
6. **fallback tuple 的真实语义是评价信息坍缩。** 第一个 0 是 worst-model p1 全候选通过率为 0；第二个 0 是 worst-model p0 为 0；1 是 Schema rate；最后 0 来自配置价格全为零。它没有表达 near-pass 数、逐门率、issue 数、回归或盲评质量，所以 tie 不等于三个版本同质。
7. **example、ledger、Schema duplication 不是主因。** 单因素实验未产生跨模型语义增益；Schema duplication 只产生可测的约 5% 输入开销。
8. **Provider 不是基线失败原因，但影响了 Phase 2 完整验证。** 基线与 Phase 1 均无身份/截断/基础设施异常；Phase 2 后段 Qwen 账户返回明确 `Arrearage`，这是新发生的外部状态，不能倒推为 V0/V1/V2 失败原因。

### 13.3 强推断

1. **根因贡献排序应改为：精确约束/repair 表达不足 > evaluator 与工程质量目标不完全对齐 > fallback 退化 > 单调用复杂度 > example/input/Schema 次要问题。** Phase 1 已推翻“单调用复杂度是首要结构性原因”的原强推断。
2. **Qwen 剩余 p0 不稳定来自长列表手工闭包与职责语义选择的模型敏感性。** 它在精确 prompt 下通常只剩一条 primary 或一次 module projection 漏项；这比基线的多门系统失败小一个数量级，且一次最小 repair 能闭合。
3. **分阶段可能降低局部 repair 的认知负担，但不会自动提高架构质量。** 有效轨迹能分步闭合 arch_05/10；同时 Qwen 在无 issue 的额外阶段仍新增 supporting roles，说明不受结构约束的阶段调用也会产生无必要改写。
4. **正式后续若只追求 validator pass，可能优化出职责集中或粗糙 `.c` contract。** 候选质量统计中，Qwen pass 样本出现最大 WP primary 占比 81.5%、零职责 integration WP 和无消费者 task contract；因此未来评价需要与真实下游可消费性或人工 rubric 建立关联，而不是继续增加机械 gate 或只看全门通过率。

### 13.4 尚待验证

1. Qwen 账户恢复后，E2.1 三阶段整组 N=3 是否在相同样本协议下改善首次/阶段稳定性；当前双模型比较因 `Arrearage` 无效。
2. 使用独立部分 Schema、字段冻结和确定性组装的真正 B3 是否优于完整 Schema 的三次受约束重写。
3. TaskPlanner/Linker/PlanCritic 的真实下游消费质量；当前仓库没有可运行的完整下游检查管线，不能用人工印象替代。
4. 两模型外部真实价格及其是否应进入 selection；workspace 内配置为 0，且成本胜者不等价于质量胜者。
5. `ARCH_VALIDATE` 是否应增加职责内聚、consumer usefulness 或 contract abstraction 的评价；这需要权威设计决定，不能在本实验目录修改 validator。

### 13.5 对 change “无法完成”的最终解释

原 change 的失败不是“没有找到任何能工作的方法”，而是**在协议允许的 V0→V1→V2 两次修改内，没有提出足够精确的提示词；随后一个只看 worst-model 全候选全门通过率、Schema 和零成本的 fallback 无法区分三个均处于低通过区的版本，按 spec 必须 tie**。

实验性精确 prompt 已证明当前单调用可以达到双模型 p1=100%，所以任务并非天然不可完成；但它发生在原 change 修改额度之外，而且 Qwen p0=40% 仍未满足正式筛选。因此正确后续不是篡改旧 lineage 或追认新 prompt，而是由权威流程决定：是否为精确算法 prompt 开新 change/新 lineage，是否调整评价目标与 fallback，以及是否在 M1-4a3 验证真正 B3。

### 13.6 最小剩余验证

为了区分最后残余的“Qwen 单调用稳定性”与“call shape 复杂度”，只需一个最小实验：

1. 恢复 Qwen Provider 账户状态；
2. 不改 `prompt-exact-algorithm.md`、example、input、Schema、temperature、max_tokens、validator 或 N=3；
3. 完整重跑 `results/phase2/preregistration.md` 的 M/S 双模型 arm；
4. 比较 Qwen 的 Stage 1 全门率、最终全门率、`arch_05/10`、回归和每个最终候选的人工质量；
5. 若 S 只用更多调用达到与 M 相同的最终 3/3，则复杂度不是剩余主因；只有 S 在相同最终质量下稳定提高 Qwen 首次/阶段闭包且不产生额外职责漂移，才支持进一步正式设计 B3。

该剩余实验不影响当前已经确认的首要根因结论。

## 14. Qwen Phase 2 第一次重新执行结果（2026-08-20，已由第 15 节更新）

应操作者要求，本轮再次使用指定的进程内环境映射：

- `ALI_API → NEPA_QWEN_API_KEY`；
- 同时保持已声明的 Claude/DeepSeek 环境映射，但本轮不调用它们；
- 不输出、不落盘任何 key 值。

### 14.1 执行前 Provider 健康门

在重发 ArchitecturePlanner staged N=3 前，先对相同 Qwen provider 与模型 `qwen3.7-max-2026-06-08` 发起最小真实请求：temperature=0、`max_tokens=4`、仅要求返回 `OK`。结果为：

| 字段 | 结果 |
|---|---|
| HTTP status | 400 |
| error type | `Arrearage` |
| error code | `Arrearage` |
| Provider 解释 | account is not in good standing / overdue payment |
| 是否进入 ArchitecturePlanner prompt | 否 |
| 是否产生候选/validation | 否 |

该结果与上一轮 `results/phase2/qwen-provider-health.json` 完全同类，说明 Qwen 账户状态在本轮仍未恢复。错误与 prompt bytes、Schema、上下文长度、staged repair 或 candidate 内容无关，因为最小四 token 请求在任何架构输入发送前就被拒绝。

### 14.2 为什么没有继续制造三个失败 trial

预注册规则把基础设施无效与模型语义失败分开，任何 infrastructure-invalid trial 都不能进入 p0/p1、逐门率或 call-shape 比较。最小健康门已证明当前账户拒绝所有请求；继续提交 staged N=3 只会复制三次 HTTP 400，既不能增加关于 H-COMPLEX/H-PROMPT 的信息，也不能形成有效双模型实验。因此本轮在 Provider 健康门停止，没有把账户错误伪装成 Qwen 0/3。

### 14.3 本轮结论分级

**已确认事实：**

1. Qwen Provider 当前仍处于 `Arrearage`，此前 Phase 2 的 HTTP 400 阻塞尚未解除。
2. 本轮没有新的 Qwen ArchitectureDraft、validation、p0/p1 或 staged repair 数据。
3. 已有 Phase 1 的 Qwen 有效数据仍保持有效：精确算法 prompt N=5 为 p0=2/5、p1=5/5；Provider 后续账户状态不能倒推污染已经完成的有效调用。
4. 第 13 节的首要根因排序不变；本次复验只更新 Phase 2 的外部阻塞状态。

**强推断：**

恢复账户后，原样重跑的主要价值是量化 staged call shape 对 Qwen p0 的影响，而不是重新检验精确算法 prompt 是否有效；后者已经由 Phase 1 N=5 确认。

**尚待验证：**

Qwen 账户恢复后的 staged N=3 仍是唯一未获得有效数据的实验。有效复验必须保持 `results/phase2/preregistration.md` 冻结的 prompt、example、input、Schema、temperature、max_tokens 和 validator，不应通过更换模型、缩短架构输入或降低 gate 绕过账户问题。

### 14.4 当次状态

本轮已完成能够执行的 Provider 诊断与根因分析，但 Qwen staged N=3 仍受外部账户状态阻塞。恢复或更换为操作者明确授权且状态正常的同身份 Qwen 凭据后，才能继续生成有效实验数据；在此之前，不应声称 Phase 2 双模型比较已经完成。

## 15. Qwen Phase 2 有效复验与最终分析（2026-08-20）

本节是 Qwen Phase 2 的最新结论，覆盖第 14 节的临时阻塞状态。没有新建独立分析报告；原始 candidate、validation、trace 和机器 summary 位于 `results/phase2/runs/e2-1-s-staged-qwen-rerun2/`。

### 15.1 环境重新导入与健康门

直接继承 Codex 进程环境、以及在非交互 shell 中 source `.bashrc` 后，最小 Qwen 请求仍返回 `Arrearage`。随后使用新的交互式 shell 强制重新加载当前用户初始化环境，并在同一 shell 中执行：

- `CLAUDE_API → NEPA_CLAUDE_API_KEY`；
- `ALI_API → NEPA_QWEN_API_KEY`；
- `DS_API → NEPA_DS_API_KEY`。

相同的最小 Qwen 健康请求恢复为 HTTP 200，返回模型身份 `qwen3.7-max-2026-06-08`。这确认前两次失败使用的是旧进程环境快照或未执行交互初始化后的值；账户/凭据本身在交互式重新导入后已经可用。整个过程没有输出或落盘 key 值。

该发现只解释 Phase 2 重试为什么先后出现 `Arrearage` 与恢复，不改变基线 V0/V1/V2 或 Phase 1 的根因判断。

### 15.2 冻结配置

本轮只执行 Qwen staged S，N=3，配置保持：

| 项目 | 冻结值 |
|---|---|
| 模型 | `qwen3.7-max-2026-06-08` |
| temperature / max_tokens | 0 / 65536 |
| prompt SHA-256 | `d5c24f1939f3a767f2cd1d7a116124d4b5ea32552391664052a93f24f1914b85` |
| example SHA-256 | `81ba08770658ec54603a301fe32a86d7aacb1189a6513a7e9c51e200044c0a5f` |
| input | 原始 planning index，无 deterministic ledger |
| Schema | 完整 ArchitectureDraft，保留当前非 native fallback 的重复呈现 |
| call shape | Stage 1 `arch_01–05`；Stage 2 `arch_01–09`；Stage 3 `arch_01–10` |
| validator | 同一生产 `validate_architecture` |

9 次调用的模型身份、prompt template hash 和 finish reason 均稳定；每次 transport attempt=1，内部格式 repair=0，无截断或基础设施异常。

### 15.3 逐 trial 与逐阶段结果

| Trial | Stage 1 | Stage 2 | Stage 3 | 实际修复 |
|---|---|---|---|---|
| 001 | 全门 pass | 全门 pass | 全门 pass | 无；三个候选字节完全一致 |
| 002 | fail `arch_05` | 全门 pass | 全门 pass | 仅给 session module/WP 的 `consumes_contracts` 补入 `contract-codec-header`；Stage 2/3 字节一致 |
| 003 | fail `arch_05` | 全门 pass | 全门 pass | 仅从 `contract-session.consumers` 删除不匹配的 `mod-net`；Stage 2/3 字节一致 |

聚合结果：

| 指标 | 结果 |
|---|---:|
| Stage 1 全十门通过 | 1/3 |
| Stage 2 累计全十门通过 | 3/3 |
| Stage 3 最终全十门通过 | 3/3 |
| Stage 1 `arch_05` | 1/3 |
| Stage 1 其他 9 门 | 全部 3/3 |
| 最终 `ARCH_TEST_READINESS_UNCLOSED` | 0 |
| 最终 issue | 0 |
| gate regression | 0 |
| infrastructure-invalid | 0 |

三个 Stage 3 候选重新用当前生产 validator 独立重算，均为 pass、0 failed gates、0 issues。

### 15.4 与单调用 M 的直接比较

M 使用同一精确 prompt、example、input、Schema 与模型参数，N=3；未通过时最多一次全局语义 repair。

| 指标 | 单调用 M | 三阶段 S | 变化 |
|---|---:|---:|---:|
| 首次/Stage 1 全门通过 | 1/3 | 1/3 | 无改善 |
| 最终全门通过 | 3/3 | 3/3 | 无改善 |
| repair regression | 0 | 0 | 无改善 |
| 最终 readiness issue | 0 | 0 | 无改善 |
| Provider 调用数 | 5 | 9 | +80% |
| tokens_in | 105,217 | 192,995 | +83.4% |
| tokens_out | 66,323 | 108,027 | +62.9% |
| 累计 latency | 1,090,137 ms | 1,724,858 ms | +58.2% |

S 的 Stage 2 确实表现出良好的局部修复：两个失败各只改动一组直接相关的 contract projection，且 Stage 3 不再重写。但这没有转化为更高的首次率、最终率或更少回归；Stage 3 对三个 trial 都是冗余调用。

M 的两次首次失败是 `arch_10` primary 漏项，S 的两次首次失败是 `arch_05` projection。由于两组不是同一 provider 响应样本，不能把失败族变化直接归因于 staged call shape；可以确定的只有聚合首次率和最终率没有改善。

### 15.5 实际候选质量检查

三个 S 最终候选都满足机械 validator，但工程质量仍不均匀：

| Trial | primary 分布 | 最大 WP 占比 | 零职责 WP | 无消费者 task contract |
|---|---|---:|---|---|
| 001 | codec 25 / session 29 / net 0 / app 0 | 53.7% | net、app | 无 |
| 002 | codec 19 / session 34 / net 1 / app 0 | 63.0% | app | 无 |
| 003 | codec 20 / session 34 / net 0 / app 0 | 63.0% | net、app | `contract-app-impl` |

三个候选都是 54 primary、0 supporting；所有 task-ready contract 仍用 `.c` 文件作为 `interface_files`。与 M 相比：

- S 避免了 M 最差样本的 81.5% primary 单 WP 集中，但样本量小且不是逐 candidate 配对，不能确认职责质量稳定改善；
- S 的零职责 WP 数为 2、1、2，M 为 1、2、1，没有一致优势；
- S 的无消费者 task contract 为 0、0、1，M 为 1、1、1，形式上较少，但第三个仍存在；
- `.c` contract abstraction 问题完全没有改变。

因此 S 的人工结构质量至多是“部分指标较好、部分指标相同或更差”，不满足预注册的“人工质量不劣且有明确收益”证据要求。

### 15.6 根因判定更新

**已确认事实：**

1. Qwen 在交互式重新导入环境后 Provider 恢复，staged N=3 完整有效执行。
2. 对本次完整 Schema staged 方案，Qwen 的首次率、最终率、readiness 和 regression 均没有优于单调用 M。
3. staged 把调用数提高 80%，输入 token 提高 83.4%，输出 token 提高 62.9%，累计 latency 提高 58.2%。
4. staged 的局部 repair 质量良好，但第三阶段在本次三个 trial 中全部冗余。
5. 候选仍存在零职责 WP、无消费者 task contract 和 `.c` contract 边界，机械 pass 没有消除 evaluator 的工程质量缺口。

**强推断：**

1. H-COMPLEX 不能作为本 change 失败的主要原因：同一精确 prompt 的单调用已经达到相同最终 3/3，分阶段没有提高稳定性，只增加资源消耗。
2. 精确约束与最小 repair 表达不足仍是首要根因；staged 能局部修复恰好说明规则表达有效，而不是证明任务必须拆分。
3. 如果正式使用 staged，应按 validation issue 提前停止；固定执行三阶段会在已经闭合后浪费一次或两次完整生成。

**尚待验证：**

1. 独立部分 Schema、字段级冻结和确定性组装的“真正 B3”可能与本次完整 Schema 重写不同，当前实验不能排除它在更大样本上的收益。
2. 当前 Qwen-only 复验是操作者明确要求的单模型补充，不追溯修改原预注册中“基础设施无效时双模型整组重跑”的历史判定；若需要一份形式上完全配对的 E2.1 证据，仍应在同一批次原样执行 Qwen+DeepSeek。
3. N=3 是根因区分样本，不是正式 change screening 或 M1-4a3 资格证据。

### 15.7 最终根因排序

本轮之后，证据排序进一步收敛为：

1. **提示词没有把 validator 关系表达为精确构造算法和最小 repair——已确认首要根因；**
2. **validator/fallback 只测和排序机械闭包，不能充分代表工程质量——已确认评价缺口；**
3. **fallback 在低全通过率、零成本区退化并触发 tie——已确认直接选择阻塞；**
4. **单调用复杂度是模型敏感的次要负担，但本次完整 Schema staged 没有改善 Qwen 结果——H-COMPLEX 作为主因不受支持；**
5. **example、deterministic ledger 和 Schema duplication 是次要或效率问题——已有单因素实验反对其为主因。**

因此无需再把“等待 Qwen staged 数据”列为当前根因报告的开放阻塞；尚未验证的只剩更强的部分 Schema/确定性组装 B3，以及真实下游质量关联。
