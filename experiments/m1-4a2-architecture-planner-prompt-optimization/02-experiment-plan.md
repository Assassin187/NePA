# M1-4a2 后续根因区分实验计划（纸面版）

计划日期：2026-08-20
状态：已执行；Phase 0/1 完成，Phase 2 双模型比较因 Qwen Provider `Arrearage` 基础设施状态无效，保留完全同配置重跑项
关联基线：lineage `daa917e4c0362d5bce575df3e1ef7436f35942aa0075ba21e3f432ca4ce48772`
目标：以最少的新模型调用区分评价标准问题、提示词/输入组织问题、任务复杂度问题和实现问题。

## 1. 实验原则

1. **既有 lineage 不可变。** 新实验不得追加到基线 V0/V1/V2，不得覆盖 candidate、validation、assessment、revision 或 outcome。
2. **先离线、后调用。** 先用现有 60 个候选完成 evaluator 与 fallback 诊断；只有离线证据不足时才进行新模型调用。
3. **一次只改变一个因果因素。** 不把 prompt、example、输入摘要、validator 和 call shape 同时修改。
4. **双模型对称。** 所有线上 arm 同时运行同一 Qwen 和 DeepSeek 身份、相同 temperature、max_tokens、输入工件和 trial 数；不能只补失败模型。
5. **预注册判据。** 每个 arm 在运行前写下 prompt/example/input/hash、主要指标、成功阈值和停止条件。
6. **完整保留失败。** 保留 raw request/response、p0/p1、validation、usage、身份与基础设施状态；不得跨 trial 或跨 arm 拼接。
7. **不追认选择。** 新指标只能验证未来设计，不能据此把基线 V0/V1/V2 中某个版本追认为已选中。
8. **结论分级。** 实验输出继续区分“已确认事实、强推断、尚待验证”。

## 2. 待区分假设

| ID | 假设 | 如果为真，应观察到 |
|---|---|---|
| H-EVAL | 核心 evaluator/validator 与真实架构质量错位 | validator-pass 样本在盲评/下游明显差；只差局部门的样本无需修复也同样可用；gate-local 修复不能改善下游 |
| H-PROMPT | 失败主要来自精确约束、输入组织、自检或 repair 指令不足 | 保持单调用不变，仅增加等价算法/ledger/白名单/最小修复后，p0 的 02/03/09/10 和 p1 明显改善 |
| H-EXAMPLE | 示例和 Schema 呈现诱导了错误 contract/file 语义 | 只替换对齐示例或去重 Schema，就显著减少 p0 的 02/03/09 问题 |
| H-INPUT | 原始全量输入缺少模型可执行的确定性中间表 | 只增加 non-DEFINITION、s5/s6、required-interface、test→requirements ledger，就显著改善 09/10 |
| H-COMPLEX | 单次全量闭包是主要瓶颈 | 使用相同规则与模型，三阶段 call shape 的最终全门通过、修复回归和跨模型稳定性显著优于单调用 |
| H-IMPL | prompt 渲染、价格或开发协调器语义污染结果 | 去重 Schema 后行为变化大；真实价格打破成本退化；重算逻辑与正式 screening 出现边界不一致 |
| H-RANK | fallback tuple 与实际质量失配 | tuple 相同的版本在逐门质量、盲评或下游得分上有稳定可复现差异 |

## 3. 共用指标

### 主要指标

- 每模型 p0：首次 Schema-valid 候选的全十门通过率；
- 每模型 p1：一次语义修复后的全十门通过率；
- `arch_02/03/04/09/10` 的 p0→p1 逐门通过数；
- `ARCH_TEST_READINESS_UNCLOSED` 每候选中位数和总数；
- p0→p1 修复回归数；
- 每候选最终失败门数分布：0、1、2、3+；
- 双模型 worst-case 值，不用平均值掩盖弱模型。

### 次要指标

- Schema 首次/修复后通过率；
- token、prompt bytes、latency、truncation、finish reason、实际成本；
- 人工盲评的职责合理性、contract 清晰度、可实现性；
- 下游 TaskPlanner/Linker/PlanCritic 是否可消费，以及产生的新增错误数。

### 最低记录格式

每个实验 arm 记录：

```text
experiment_id:
date:
operator:
model identities:
input artifact hashes:
prompt hash:
example hash:
schema hash:
validator hash:
call shape:
trial count:
actual price declaration:
infrastructure validity:
primary metrics:
decision:
unexpected observations:
```

## 4. Phase 0：零模型调用的评价标准审计

### E0.1 分层盲评现有候选

目的：先判断 validator 通过与真实工程质量是否一致。

固定抽取 6 个现有 p1 候选：

1. 唯一全门通过：V0/DeepSeek/trial_002；
2. 只失败 `arch_03`：V2/DeepSeek/trial_004；
3. 只失败 `arch_09`：V2/Qwen/trial_001；
4. 只失败 `arch_08`：V0/DeepSeek/trial_004；
5. 只失败 `arch_10`：V2/Qwen/trial_002；
6. 只失败 `arch_10`：V2/DeepSeek/trial_001。

步骤：

- 去除版本、模型、trial 和 validator verdict 信息，随机编号；
- 至少两名评审者按同一 rubric 独立评审：requirement coverage、职责内聚、contract boundary、文件可实施性、DAG 合理性、test readiness；
- 有条件时把 6 个候选送入完全相同的只读下游消费检查；
- 评审结束后再揭盲，对照 failed gates。

判定：

- 若 validator-pass 样本不是前列，或多个失败样本无需修改即可同等下游可用，则支持 H-EVAL；
- 若失败样本的具体 gate 缺口也导致人工/下游问题，则反对“validator 是主因”。

### E0.2 gate-local 反事实修复

目的：区分 gate 真问题与只是形式不一致。

对上面 5 个 near-pass 样本分别制作临时副本，只做 validation 指向的最小字段修复：

- `arch_03`：只调整 contract ready_gate/interface_files；
- `arch_09`：只补齐 required s5 interface slots；
- `arch_08`：只令 `depends_on` 等于 contract 导出的精确依赖；
- `arch_10`：只补最少 convergence dependency，或收缩非必要 supporting responsibility。

每次修复后运行完整 `ARCH_VALIDATE`，并进行同样下游检查。临时副本不得写回基线 lineage。

判定：

- 局部修复能全门通过且下游改善：支持 validator 正确，问题在生成/repair；
- 局部修复导致架构明显更差：支持 H-EVAL；
- 必须大范围重构才可能闭合：支持 H-COMPLEX。

### E0.3 fallback 离线重放

目的：验证 `[0,0,1,0]` 是否丢失可用区分。

对 V0/V1/V2 只读计算以下候选排序，不改变授权选择：

- 授权四维 tuple；
- 双模型最差的最终逐门通过率；
- 双模型最差的每候选失败门数中位数；
- issue 严重度/数量；
- 修复回归数；
- E0.1 的盲评/下游分数。

同时核实两个模型的 0 单价是实际价格还是占位值，并用真实非零价格仅做反事实重放。

判定：

- 若授权 tuple tie，但其他指标对一个版本给出稳定一致优势，支持 H-RANK；
- 若替换真实价格即可唯一排名，只能确认成本配置退化，不能自动确认该排名代表架构质量。

### Phase 0 停止点

若 E0 已证明 validator 与下游显著错位，应先取得设计授权修正评价目标，不进入提示词优化。若 validator 与下游方向一致，再进入 Phase 1。

## 5. Phase 1：提示词与输入组织的最小调用实验

每个 arm 先用 N=3/模型。N=3 只用于根因区分，不用于 M1-4a2/M1-4a3 资格认定。

### E1.1 精确算法提示词 A/B

控制组 A：冻结当前 V2 prompt、example、Schema、输入和单调用形态。
实验组 B：只替换规则文本，其他全部相同。

B 必须显式包含以下可执行算法，而不是“检查一致性”口号：

1. `non_definition_req_ids` 的所有且仅有成员各恰好一个 primary；DEFINITION 不得 primary；同一 WP/requirement 不混合 primary/supporting；
2. s5_frozen 与 s6_owned 路径白名单；module owns_files 和 WP allowed_files 的分区规则；
3. contract provider/consumer、module projection、WP projection 的集合等式；
4. `depends_on` 从 consumed/provided contracts 精确推导，并检查 DAG；
5. required internal interface slots 的逐槽位闭包；
6. 每个 task test 收集全部 primary/supporting WPs，计算共同后继交集；为空时创建最小 integration convergence 或删除不必要 supporting；
7. repair 时以 previous candidate 为基线，仅修 validation 指出的字段，保持已通过 gates，再跑全部十门 checklist。

主要判据：相对 A，B 在两个模型上都应至少满足以下之一：

- p0 的 `arch_02/03/09/10` 合计通过数提高至少 30%；
- p1 全门通过候选增加且 repair 回归不增加；
- readiness issue 总数至少下降 50%。

若只一个模型改善，保留为模型敏感证据，不宣称 H-PROMPT 已确认。

### E1.2 对齐示例单因素实验

控制组：E1.1 中表现较好的规则文本 + 当前 example。
实验组：规则文本不变，只替换 example。

新 example 必须：

- 不使用真实 gold identifiers；
- 明确展示 s5 frozen interface 与 s6 mutable implementation 的区别；
- task-ready contract 不把 frozen header 当成待实现 ownership；
- module/WP contract projections、file partitions 和 exact dependency 自洽；
- 至少包含一个 task readiness convergence 例子。

主要判据：实验组两个模型的 p0 `ARCH_CONTRACT_GATE_INVALID`、`ARCH_MODULE_FILE_INVALID`、`ARCH_INTERFACE_SLOT_UNCLOSED` 合计至少下降 50%，且不增加 arch_10。

### E1.3 输入 ledger 单因素实验

控制组：E1.2 胜出配置 + 原始输入呈现。
实验组：prompt、example、Schema、call shape 不变，只在输入中增加从冻结工件确定性派生的只读索引：

- `non_definition_req_ids`；
- `definition_req_ids`；
- `s5_frozen_paths`、`s6_owned_paths`；
- `required_internal_interface_slots`；
- `task_test_requirement_sets`。

索引必须可从原工件重算，不能加入人工答案或架构决策。

主要判据：两个模型 p0 `arch_09/10` 合计通过数提高，且 primary invalid/readiness issue 至少下降 50%。

### E1.4 Schema 去重单因素实验

控制组：模板内 Schema + fallback 再附加 Schema。
实验组：provider 最终请求只出现一次完全相同的 Schema；其他字节保持一致。

主要判据：记录 prompt bytes、tokens_in、p0/p1、逐门问题。若仅 token 下降而语义指标无稳定变化，则 H-IMPL 的该分支不是主因；若双模型 02/03/09 显著改善，则确认 Schema 重复有实质影响。

### Phase 1 停止点

- 若单调用配置已在 N=3 双模型显著提高 p0/p1，则先以同一冻结配置复验 N=5；
- 若多个单因素都无稳定改善，或仍主要卡在 arch_10/readiness，进入 Phase 2；
- 不允许根据中间结果继续即兴改第三、第四版 prompt，避免重复当前因果不可识别问题。

## 6. Phase 2：任务复杂度与 call shape 实验

### E2.1 单调用 vs B3 三阶段

使用 Phase 1 的同一胜出规则、example、输入索引、Schema 和模型参数，只改变 call shape。

| Arm | Call shape |
|---|---|
| M | 单次生成完整 ArchitectureDraft + 一次全局语义 repair |
| S | 阶段 1 module/contract（arch_01–05）；阶段 2 WP/file/exact DAG（arch_06–09）；阶段 3 requirement/readiness（arch_10）；最后确定性组装并跑完整 validator |

两组均 N=3/模型，输入事实相同。S 组每阶段只能扩充下阶段允许字段，不能偷偷改变上一阶段已冻结决定；最终仍使用完全相同的完整 `ARCH_VALIDATE`，不降低门槛。

主要判据：若 S 同时满足以下条件，则确认 H-COMPLEX 是主要原因：

- 两模型最终全门通过数均高于 M；
- 03/04/09/10 失败总数至少下降 50%；
- repair 回归至少下降 50%；
- 人工/下游质量不劣于 M。

若 S 只增加调用数但无质量改善，反对“拆分本身足够”，转查阶段间冻结/组装或 evaluator。

### E2.2 确定性闭包边界（仅在 E2.1 仍卡住时）

比较：

- S1：模型自己生成全部 projections、exact DAG、readiness closure；
- S2：模型只做语义架构决策，能由 contracts/responsibilities 唯一推导的 projections、DAG 和 closure 由确定性过程生成或校验后反馈。

目的不是放松标准，而是判断哪些字段不应让模型重复手算。若 S2 显著优于 S1，则后续设计应把确定性关系从生成职责移到 compiler/validator 边界；这属于设计决策，必须另行授权，不能在本实验目录中直接实施为正式行为。

## 7. 最小样本和升级规则

1. E0 不产生模型调用。
2. E1 每个首次 arm 为 N=3/模型；只有预注册主要判据命中时才对该唯一配置复验 N=5/模型。
3. E2 的 M/S 均为 N=3/模型；只有结果方向一致但置信不足时，成对扩到 N=5，不直接到 N=10。
4. 任一 arm 出现基础设施无效，整组双模型 trial 作废并按同配置重跑；不得只补失败模型。
5. 若 N=3 的三个 trial 结论相互矛盾，不继续改 prompt；先检查输入/身份/请求字节和 evaluator 重算。
6. 所有新实验是诊断证据，不满足正式 change 的 N=5/N=10/N=20 或选择协议，除非 OpenSpec/设计获得明确更新授权。

## 8. 决策矩阵

| 观察结果 | 根因判断 | 下一步 |
|---|---|---|
| E0 显示 validator-pass 与盲评/下游负相关 | 评价目标错位 | 停止 prompt 优化，提交设计问题供授权决定 |
| E0 gate-local 修复同时改善 validator 和下游 | validator 基本正确 | 进入 E1 |
| E1.1 显著改善，E1.2/E1.3 增益小 | 主要是约束表达/repair 提示问题 | 冻结精确算法提示词，复验 N=5 |
| E1.2 显著改善 02/03/09 | example 诱导是主因 | 使用对齐示例复验，不同时改 call shape |
| E1.3 显著改善 09/10 | 输入组织是主因 | 把 deterministic ledger 作为设计候选 |
| E1 全部效果小，E2 的 S 显著优于 M | 单调用任务复杂度是主因 | 设计授权 B3/分阶段，不再继续堆 prompt 条款 |
| E2 的 M/S 都失败且 E0 支持 validator | 任务表示或确定性边界仍有问题 | 执行 E2.2，审查哪些字段应推导而非生成 |
| fallback tie 但盲评/下游稳定区分版本 | fallback 评价错位 | 为未来 selection 提交新设计，不追认旧 lineage |
| 真实价格非零且仅成本打破 tie | 零价格配置退化 | 修正未来配置；仍需独立证明成本胜者质量可接受 |

## 9. 执行前检查单

- [x] 获得执行新模型调用和新临时 lineage 的授权；
- [x] 记录当前 git status，确认不覆盖用户现有改动；
- [x] 固定 spec/target/test bundle/Schema/validator hashes；
- [x] 固定 Qwen/DeepSeek provider、model、temperature、max_tokens；
- [x] 核实 workspace 价格声明，不打印或记录 API key 值；外部真实价格仍未核实；
- [x] 为各有效 arm 保存 provider render/trace，确认唯一变量；
- [x] 预先写明主要指标和成功阈值；
- [x] 预先写明基础设施无效与重跑规则，并在 E2.1 实际执行；
- [x] 建立盲评编号映射并在评审结束后揭盲；
- [x] 确认所有输出只写入本临时实验目录或新的独立 calibration lineage；
- [x] 确认未修改基线 `daa917...`；
- [x] 确认未把诊断性 N=3/N=5 宣称为正式资格证据。

## 10. 单实验记录页模板

```text
实验 ID：____________________    日期：____________________
操作者：____________________    状态：计划 / 运行 / 完成 / 无效

要检验的单一假设：
________________________________________________________________

控制组唯一标识与 hashes：
________________________________________________________________

实验组唯一变化：
________________________________________________________________

保持不变的因素：
________________________________________________________________

预注册主要判据：
________________________________________________________________

Qwen 结果：p0 ____  p1 ____  03 ____  04 ____  09 ____  10 ____
DeepSeek 结果：p0 ____  p1 ____  03 ____  04 ____  09 ____  10 ____
Readiness issues：控制 ____ / 实验 ____
Repair regressions：控制 ____ / 实验 ____
基础设施/截断异常：____________________________________________

是否命中预注册判据：是 / 否 / 无效
支持或反对的假设：____________________________________________
不应从本实验推出的结论：______________________________________
下一步：________________________________________________________
```

## 11. 完成标准

本轮根因实验完成不是“找到一个能过的样本”，而是至少满足以下之一：

1. 有重复证据把主因定位为 evaluator、prompt/example/input、单调用复杂度或实现中的一个主要类别；
2. 能排除至少两个类别，并明确剩余最小不确定性；
3. 形成一项需要权威设计决定的具体问题及其影响，而不是继续无边界修改提示词。

实验结论转入正式 change/design/spec 前应单独评审。本临时目录在正式结论完成迁移、基线和新实验 lineage 均已归档或明确舍弃后，可以整体删除。

## 12. 实际执行状态（2026-08-20）

| 实验 | 状态 | 结论入口 |
|---|---|---|
| E0.1 双模型盲评 | 完成；DeepSeek 原始重复项已透明去重并复验 Schema | `results/phase0/phase0-report.md` |
| E0.2 gate-local 反事实 | 完成 | `results/phase0/counterfactuals/summary.json` |
| E0.3 fallback 重放 | 完成；外部真实价格未查询 | `results/phase0/fallback/fallback-replay.json` |
| E1.1 精确算法 A/B | 完成 N=3，并把唯一胜出配置扩到 N=5 | `results/phase1/phase1-report.md` |
| E1.2 对齐 example | 完成 N=3；未命中判据 | 同上 |
| E1.3 deterministic ledger | 完成 N=3；未命中判据 | 同上 |
| E1.4 Schema 去重 | 完成 N=3；仅效率改善 | 同上 |
| E2.1 M vs S | 已执行首次组和完全同配置重跑；两组均因 Qwen 400/`Arrearage` 不满足双模型有效性 | `results/phase2/phase2-report.md` |
| E2.2 确定性边界 | 未触发：有效 M 与 DeepSeek S 均无最终闭包卡点；Qwen 是外部账户状态 | 同上 |

当前唯一需要外部状态变化后重做的项目是 E2.1 的 Qwen/DeepSeek 整组原样重跑。它只影响 H-COMPLEX 的严格双模型增益量化，不影响 E1.1 已确认的 H-PROMPT 首要根因。
