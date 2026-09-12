# NePA 设计文档一致性校对会话记录

> 历史记录：对应 9.0 之前的设计，不约束本次已批准的端到端重构。
> 当前架构与实施状态见 system_design.md 和 refactor_plan.md。

## 记录信息

- 来源会话：`codex://threads/01a06fa3-045a-7d81-a9d2-1b31470a68f5`
- 来源会话标题：校对 NePA 设计文档一致性
- 记录日期：2026-09-05
- 会话工作目录：`/home/ljf`
- 校对对象：
  - `NePA/project_docs/system_design.md`
  - `NePA/project_docs/pipeline_design_s4_s9.md`
- 校对时确认的文档版本：主文档 `7.2.0`，子文档 `1.3.0`
- 本文件性质：会话内容整理记录，不属于设计规范，不覆盖或修改上述权威设计文档。

## 用户原始请求

请校对 `NePA/project_docs/pipeline_design_s4_s9.md` 和 `system_design.md` 两份文档是否还有冲突和不一致的地方，包括设计、表述和规定等各方面。

## 会话结论

两份文档之间仍存在多处会直接影响实现、恢复和验收的冲突或定义缺口。校对结果将问题分为：

1. 明确冲突：两份文档已有相互排斥的规定。
2. 定义缺口：现有规定不足以推出唯一实现。
3. 表述、引用和排版问题：不会立即形成状态机冲突，但需要同步清理。

本次会话只进行了文档校对，没有修改文档，也没有把代码现状作为设计依据。

## 主要冲突和定义缺口

### 1. F2 不重物化与工件绑定要求冲突

F2 可以调整文件 owner、拆分或合并任务、重新分配 `T-###`。主文档要求 manifest 保存 `owner_task_id`，contract map 保存 `provider_task_id`，并绑定计划版本与 Blueprint 哈希；子文档却规定 F2 不重物化，激活步骤也不更新这两份工件。

一次合法 F2 后，下游可能立即发现 owner、provider 或哈希漂移。需要明确 F2 的确定性元数据更新规则和 receipt 更新规则。

位置：

- 子 §4.1：`project_docs/pipeline_design_s4_s9.md:235`
- 子 §6.4：`project_docs/pipeline_design_s4_s9.md:720`
- 主 §5.6.5.4：`project_docs/system_design.md:1182`

### 2. 不可变任务证据不支持计划修订和 F1

证据路径固定为 `<task_id>/attempt_NNN.json`，内容绑定 `task_id`、`attempt`、`plan_sha256`，同一路径禁止发布不同字节。但修订允许任务重编号、`REGENERATE` 重置 attempts；`REVALIDATE` 和 F1 又要求 attempts 不变却换绑新证据。

由此既可能发生路径碰撞，也可能出现继承的旧证据无法通过新计划对账的问题。需要统一证据身份、历史证据继承证明，以及一次 F1 修改双方任务时的提交绑定方式。

位置：

- 主 §5.4：`project_docs/system_design.md:978`
- 主迁移事件：`project_docs/system_design.md:829`
- 子 §3.2：`project_docs/pipeline_design_s4_s9.md:155`

### 3. “F2 保全率恒为 1.0”不能由分类规则推出

`move_file_owner` 明确要求 `REVALIDATE`，但接收任务的 `deliverable_files` 增加，又满足 `REGENERATE` 判据；`move_responsibility` 会新增接收任务的 REQ 责任，按分类表应为 `AMEND`；split/merge 的新任务则被统一判为 `REGENERATE`。

这些规则与“F2 最坏只到 REVALIDATE”不一致。必须先明确任务分类与文件分类如何分别计算、重叠条件的优先级，再保留或调整恒等于 1 的结论。

位置：

- 子分类规则：`project_docs/pipeline_design_s4_s9.md:151`
- 子保全率证明：`project_docs/pipeline_design_s4_s9.md:214`
- 子算子表：`project_docs/pipeline_design_s4_s9.md:672`

### 4. 义务摘要漏掉任务“提供的接口”签名

摘要包含 `provides_contracts` 的标识，却只计算 `consumes_contracts` 的签名摘要。因此，对某个 task-ready contract 执行 `extend_contract` 时，如果 provider 的文件、责任和 contract id 不变，provider 仍可能被判为 `INHERIT`，新增接口没有任务负责补实现。

需要补齐提供方接口变化的失效规则。

位置：

- 子摘要公式：`project_docs/pipeline_design_s4_s9.md:135`
- 子 `extend_contract`：`project_docs/pipeline_design_s4_s9.md:681`

### 5. AMEND 的 attempts 保留规则与一次 Fixer 额度不一致

一个任务可以在第 4 次尝试成功；以后被判 AMEND 时，子文档要求保留 attempts 并允许一次 Fixer，但主文档规定重开的 pending 最多只能是 `total_limit-1`，因此 `attempts=4` 无法合法重开。

反过来，原 `attempts=1` 时，普通循环还会允许三次尝试，也不符合“一次 Fixer”。需要单独说明 AMEND 的额度与普通 attempts 上限如何组合。

位置：

- 子 §6.5：`project_docs/pipeline_design_s4_s9.md:737`
- 主 pending 例外：`project_docs/system_design.md:810`
- 主单任务循环：`project_docs/system_design.md:1562`

### 6. `blocked_by_dependency` 缺少修订后的重开路径

主文档把该状态列为终态，但 `reopened_by_revision` 只允许 `done/blocked → pending`。上游 provider 经修订恢复后，下游无法从 `blocked_by_dependency` 恢复执行，使 TR-2 的解阻塞目标无法闭合。

位置：

- 主状态迁移：`project_docs/system_design.md:818`
- 主 `reopened_by_revision`：`project_docs/system_design.md:830`
- 子 TR-2：`project_docs/pipeline_design_s4_s9.md:639`

### 7. 后续纪元的 S5 是否允许构建失败，规定相反

子文档允许 E1+ 带已登记的接口不兼容通过检查点，交给 S6 AMEND；主文档的 S5 验收仍统一要求默认构建零警告零错误、smoke 通过，没有纪元例外。

即使采用子文档方案，也需要明确多个不兼容任务同时存在时，逐任务构建门如何允许修复逐步提交。

位置：

- 子 E1+ 验收：`project_docs/pipeline_design_s4_s9.md:539`
- 主 S5 验收：`project_docs/system_design.md:1529`

### 8. 多纪元 S5 的阶段状态、恢复基线和历史锚点不同步

主文档规定已完成阶段重跑是空操作、done 为终态；S5 又要求修订时重入。恢复规则仍多处使用“首提交”，而子文档要求改用“当前纪元检查点”。

此外，主文档要求 manifest/map 按纪元留存、历史锚点保存在修订账本；目录中没有对应纪元布局，子文档又禁止把激活之后才生成的 checkpoint 回写到账本条目。需要明确纪元级状态及历史 receipt 的保存位置。

位置：

- 主 §4.8：`project_docs/system_design.md:445`
- 主历史工件：`project_docs/system_design.md:1188`
- 主 S5 恢复：`project_docs/system_design.md:1523`
- 子激活提交语义：`project_docs/pipeline_design_s4_s9.md:297`

### 9. S6 出口 smoke 失败后没有合法的 F0/F1 修复入口

smoke 规定在全部任务终态后执行，却又规定失败走单任务 F0/F1，最后表现为任务 blocked。此时没有明确的当前任务，done 也不能因 smoke 失败直接重开。

需要定义失败归属、修复额度和状态迁移；S5 的 smoke 失败还要与“无 LLM、失败不进入修复循环”区分。

位置：

- 子 smoke 执行位置：`project_docs/pipeline_design_s4_s9.md:564`
- 子 smoke 失败路由：`project_docs/pipeline_design_s4_s9.md:650`
- 主 S5 失败处理：`project_docs/system_design.md:1531`

### 10. F4/F5、熔断和修订预算耗尽的结局不统一

子文档一处要求以 `PLAN_INVALID_AT_EXECUTION` 受控结束，另一处要求继续独立分支并最终 `outcome=degraded`；主文档又把该错误码明确归为 `failed`。

主预算表要求修订次数耗尽后锁计划、继续执行，但 outcome 定义还写着“修订预算耗尽后进入受控失败”。需要分别规定“停止修订”和“停止整个阶段”的条件及错误码。

位置：

- 子 §5.6：`project_docs/pipeline_design_s4_s9.md:603`
- 子 §7.3：`project_docs/pipeline_design_s4_s9.md:787`
- 主预算表：`project_docs/system_design.md:407`
- 主 outcome：`project_docs/system_design.md:2065`

### 11. TR-9 在现有阶段顺序中无法合法触发 F3

TR-9 要求 S8 至少一轮修复已经失败；但计划修订发生于 S6，S8 明确禁止启动修订，也没有 S8 返回 S6 的路径。因此它目前只能作为记录型诊断，不能按表中语义自动升级 F3。

位置：

- 子 TR-9：`project_docs/pipeline_design_s4_s9.md:646`
- 子 S8 禁止修订：`project_docs/pipeline_design_s4_s9.md:617`
- 主 S8 出口：`project_docs/system_design.md:1659`

### 12. 触发条件并非全部摆脱模型判断，TR-3 的升级条件也自相矛盾

TR-1 的护栏允许依赖 Diagnoser 的“直接推出”理由，TR-9 明确依赖其结构缺陷归因，因此“TR-5 是唯一带模型判断的触发源”不成立。

主文档“命中 TR-1～TR-9 任一即修订”还忽略了 TR-5 只记录、TR-8 是提交门等例外。另有 TR-3：谓词要求同工作包，护栏却以“跨工作包”解释租约不适用，后一个分支无法满足前一个条件。

位置：

- 子触发表：`project_docs/pipeline_design_s4_s9.md:638`
- 子 TR-5 说明：`project_docs/pipeline_design_s4_s9.md:651`
- 主触发路由：`project_docs/system_design.md:1588`

### 13. “逐级额度耗尽才升级”会阻止必要的 F3

TR-1/TR-7 的最低级是 F3，但单调升级纪律要求任一级额度未耗尽不得升级；F2 额度耗尽时又直接锁定计划。

按字面执行，存在“F2 无法解决，却必须先耗尽；耗尽后 F3 又被禁止”的路径。需要明确不适用级别是否跳过，以及锁定的是本级还是全部修订。

位置：

- 子 TR-1/TR-7：`project_docs/pipeline_design_s4_s9.md:638`
- 子升级纪律：`project_docs/pipeline_design_s4_s9.md:759`
- 子额度熔断：`project_docs/pipeline_design_s4_s9.md:803`

### 14. 拒绝次数、触发次数和事后有效性指标缺少合规账本来源

主文档 D1.13 要求门失败时“账本无条目”，两份文档却又要求从该账本计算 gate rejection、记录型触发命中等指标。

账本条目还禁止回写，但 `ineffective`、实际返工成本和租约成功率是在事后才知道的。需要定义这些事件如何追加记录，并区分事件计数与版本激活计数。

位置：

- 主 D1.13：`project_docs/system_design.md:2272`
- 主修订指标：`project_docs/system_design.md:2099`
- 子账本不可回写：`project_docs/pipeline_design_s4_s9.md:297`
- 子指标表：`project_docs/pipeline_design_s4_s9.md:849`

### 15. `active_plan 必须等于账本末条`没有覆盖空账本和 F1

初始 seal 明确生成空账本，但 S7/S9/success 检查仍无条件引用末条。F1 又必须写入同一个账本，却不改变计划版本；其 `revision_seq` 是否推进、是否更新活动指针、如何填写版本迁移字段，没有定义。

需要给出初始态以及非版本变更条目的匹配规则。

位置：

- 主初始发布：`project_docs/system_design.md:1440`
- 主 S7 检查：`project_docs/system_design.md:1626`
- 子 F1 落账：`project_docs/pipeline_design_s4_s9.md:780`

### 16. `re_adopt` 和改名处置不能由现有封闭算子集完成

孤儿文件因失去 Blueprint 槽位而进入 `_orphan/`，重新采纳需要恢复槽位并移动文件；但 `re_adopt` 被列为不改结构、不动工作区的 F2。

文档还说改名走 `add_file_slot + 迁移分类`，但没有移除旧槽的算子，分类本身也不是计划修改算子。需要统一这些操作的层级和完整效果。

位置：

- 子孤儿处置：`project_docs/pipeline_design_s4_s9.md:218`
- 子 `re_adopt`：`project_docs/pipeline_design_s4_s9.md:679`
- 子禁止算子说明：`project_docs/pipeline_design_s4_s9.md:686`

### 17. REVALIDATE 的实际验收与提交没有进入激活流程

迁移事件要求重跑构建并换绑新 commit/证据，但激活步骤直接写迁移后的 State，未说明重验在哪里发生、失败后怎么处理。

F2 又声称完全不触碰工作区；F3 的新接口则要到后续 S5 才物化。需要明确分类预计算、实际重验和最终状态发布的先后关系，不能在重验发生前形成新的 done 事实。

位置：

- 主 `revalidation_passed`：`project_docs/system_design.md:829`
- 子激活步骤：`project_docs/pipeline_design_s4_s9.md:720`
- 子 F2 边界：`project_docs/pipeline_design_s4_s9.md:239`

## 数据定义和指标问题

### 18. 文件台账没有完整定义 S5 所有文件的记录方式

S5 要把 `s5_frozen` 标成 realized；realized 又统一要求非空 `owner_history`，示例中的 owner 是 task uid/id。但 `s5_frozen` 没有 task owner，`ready_gate=s5` 的 contract 也没有 provider task。

子文档“每个 internal contract 均与 provider 工作包对应”的预检同样漏掉这一合法类别。

位置：

- 子台账字段：`project_docs/pipeline_design_s4_s9.md:197`
- 子预检：`project_docs/pipeline_design_s4_s9.md:360`
- 子 S5 台账更新：`project_docs/pipeline_design_s4_s9.md:524`
- 主 s5 contract：`project_docs/system_design.md:683`

### 19. “所有导出符号均由六模式派生”无法覆盖已要求的内部 ABI

六模式只覆盖报文结构、编解码函数、Spec 类型别名、错误枚举类型和 packet-type 枚举类型；主文档另要求连接 id、输出 batch、connect/bytes/disconnect/tick 等内部接口，以及“未实现”等枚举常量，却没有完整的通用命名规则。

仅有 `error_enum` 模式也不能推出其枚举成员名称。需要区分机械符号的范围与架构规划符号的命名规则。

位置：

- 子导出符号规定：`project_docs/pipeline_design_s4_s9.md:426`
- 主六模式：`project_docs/system_design.md:1126`
- 主内部 ABI：`project_docs/system_design.md:1770`

### 20. slot id 唯一性作用域与 Blueprint 转写规则不一致

子文档只要求 `slot_id` 模块内唯一；主文档要求直接用它转写 Blueprint rule id，而 Blueprint 逻辑 id 在自身命名空间内唯一。

两个模块都声明 `slot_id="header"` 时，按子文档合法，转写后却碰撞；构建图中的裸 slot 引用也无法消歧。

位置：

- 子 layout 字段：`project_docs/pipeline_design_s4_s9.md:386`
- 主 id 与转写规则：`project_docs/system_design.md:1160`

### 21. 跨版本比较所需的两个关键规则不完整

一是 `local_task_id` 用于 uid 和稳定排序，但正式 task 字段表没有明确保留它或其权威映射；S4R 要重新 Link，又禁止从内部草稿取得正式语义事实。

二是 INV-3 要逐任务比较 acceptance 只能增加，但 split/merge 后任务身份改变，没有规定按什么血缘关系比较。这两点不能交由实现自行猜测。

位置：

- 主 task 字段：`project_docs/system_design.md:704`
- 主 Linker 排序：`project_docs/system_design.md:1388`
- 子 INV-3：`project_docs/pipeline_design_s4_s9.md:91`
- 子 split/merge：`project_docs/pipeline_design_s4_s9.md:672`

### 22. 返工预算公式可能漏算新增文件，并混用文件数与调用次数

§3.4 先对旧版本 realized 文件分类，再据此计算返工费用；新增文件不在这个集合内，却可能需要完整 Coder 调用。

另一方面，一个任务最多四文件，按文件数乘一次 Coder/Fixer 成本也不等于任务调用成本。需要分别定义保全率的文件统计域、返工费用的任务统计域，以及各单价来源。

位置：

- 子成本公式：`project_docs/pipeline_design_s4_s9.md:203`
- 子统计域：`project_docs/pipeline_design_s4_s9.md:212`
- 子 RG-3：`project_docs/pipeline_design_s4_s9.md:710`

### 23. 任务完成率、阻塞率和首过率口径不统一

子文档要求里程碑使用 `@r0`，主文档 D1.2 实际验收使用 `@final=100%`。主文档规定 `@r0` 中 REGENERATE 记未完成，子文档则按 split 子节点全部 done 等血缘结果折算。

阻塞率的分母，主文档用初版/终版任务集，子文档用历史 uid 谱系并集。首过率也分别采用“排除所有重开任务”和“在创建版本首次成功”的表述。同一 run 可能因此得到不同结果，必须统一成精确公式。

位置：

- 子指标重锚定：`project_docs/pipeline_design_s4_s9.md:835`
- 主指标定义：`project_docs/system_design.md:2097`
- 主 D1.2：`project_docs/system_design.md:2261`

### 24. 修订指标的键名、计数范围和成本含义不同

主文档使用 `revision.count`、`gate_rejections`、`trigger_hits`、`lease.granted`；子文档使用 `count_by_level`、`rejected_by_gate`、`trigger_histogram`、`lease.count`。

前者的修订次数只计 F2/F3，后者包含 F1；主文档的返工成本指标是估算值，子文档则要求实际值。应统一字段契约，并分别命名实际成本与估算成本。

位置：

- 主修订/租约指标：`project_docs/system_design.md:2099`
- 子新增指标：`project_docs/pipeline_design_s4_s9.md:849`

### 25. M1 使用 `build_ok` 做硬门，但该指标又被规定不可计算

主文档把 `build_ok` 的唯一来源定义为 S7 终态轮，并明确 planned-stop 运行该值为 null；M1 正常结束恰好是 `--until s6`，不进入 S7。

与此同时，主文档说 M1 将其与 smoke 并列验收，子文档的 M1 消融也使用它。需要明确 M1 构建验收记录的来源，或者使用与终态 `build_ok` 不同的指标。

位置：

- 主 planned-stop 指标：`project_docs/system_design.md:2061`
- 主 build_ok/smoke：`project_docs/system_design.md:2095`
- 子 M1 消融指标：`project_docs/pipeline_design_s4_s9.md:881`

## 表述、引用和排版问题

| 问题 | 位置与说明 |
|---|---|
| 旧的“发布后不修订”表述仍在正文 | 主 §3.4：`project_docs/system_design.md:194` 仍写“发布后的 Plan 不变，S6 只更新 Plan State；结构错误受控失败”，没有纳入版本链例外。 |
| 引用已删除的校准流程 | 子 §5.2：`project_docs/pipeline_design_s4_s9.md:358` 的“M1-4a3 冻结值”和子 §4.1：`project_docs/pipeline_design_s4_s9.md:241` 的 `6.4.8.2.1`、恢复期 R0/R1 均已过时；主文档 7.0.0 删除了这些流程。 |
| 开放问题编号错误 | 子 S8 规则：`project_docs/pipeline_design_s4_s9.md:617` 的 `Q-5` 应对应现表 `PQ-4`；TR-5 说明：`project_docs/pipeline_design_s4_s9.md:651` 的 `Q-3` 应对应 `PQ-2`。 |
| 主文档对开放问题的摘要失真 | 主 O-18：`project_docs/system_design.md:2452` 仍把“承诺层是否允许收缩”等列为待定问题，而子文档已经明确禁止，PQ 表也没有此项。 |
| 纪元定义自相矛盾 | 主术语表：`project_docs/system_design.md:2479` 将纪元称为“活动计划版本不变的区间”，但 F2 可以在同一纪元改变 P 位；应描述为结构层代不变。 |
| S4-G1 的旧判据残留 | 主 §6.4.1：`project_docs/system_design.md:1326` 前半仍要求 G1 校验布局安全和“每个 app 槽”，后半又说布局由 G2 裁决、G1 只忠实转写；门表需一起同步。 |
| 六模式名称列举错误 | 子自由度表：`project_docs/pipeline_design_s4_s9.md:370` 把 `symbol_prefix`、`type_id` 当成模式，漏掉主文档中的 `type_alias`、`packet_type_enum`。 |
| “真值级别”缺少统一定义且引用不准确 | 子 §1.2：`project_docs/pipeline_design_s4_s9.md:64` 声称引用主 §3.3 的真值阶梯，但主节没有这套 1～6 级定义；RG 表：`project_docs/pipeline_design_s4_s9.md:708` 的级别顺序也与“按成本升序”不一致。 |
| 是否允许评审型硬门的表述不一致 | 子核心约束：`project_docs/pipeline_design_s4_s9.md:61` 写“不新增评审型硬门”，但 RG-4：`project_docs/pipeline_design_s4_s9.md:711` 明确用 Critic blocker/major 阻止激活；应限定前句适用范围。 |
| 提示词禁令被扩大解释 | 子 §5.2.3：`project_docs/pipeline_design_s4_s9.md:430` 把主文档“禁止 MQTT 专有文件名/接口名”转述为禁止任何文件名/接口名和通用工程经验；主原文：`project_docs/system_design.md:1479` 允许通用说明与协议无关抽象示例。 |
| 冻结 Test Bundle 的路径描述冲突 | 主 §5.6.2：`project_docs/system_design.md:1059` 规定 Run 中路径固定为 `inputs/test_bundle.json`，主 §5.6.5.5：`project_docs/system_design.md:1192` 又说保存源文件路径。 |
| 可机械修正的文字和排版错误 | 主状态事件：`project_docs/system_design.md:820` 写“五种”，实际列八种；子状态扩展：`project_docs/pipeline_design_s4_s9.md:593` 写“两个”，实际列三个；子修订历史表：`project_docs/pipeline_design_s4_s9.md:993` 表头三列，后续多行四列。 |

## 会话给出的处理优先级建议

应优先裁决以下事项：

1. F2 的工件与证据绑定。
2. 迁移状态机。
3. 多纪元 S5。
4. 账本事件语义。
5. `outcome` 定义。
6. 指标口径。

这些事项决定后续实现的合法路径；只做措辞同步不足以消除上述矛盾。

## 后续使用说明

本记录用于在 `NePA` 项目中新会话中恢复原校对上下文。若后续要修改设计文档，应先针对上述冲突逐项作出设计裁决，再获得明确授权后修改 `system_design.md` 或 `pipeline_design_s4_s9.md`。
