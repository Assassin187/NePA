# NePA 开发状态

> 本文件是项目开发进展的**唯一显式状态记录**，与 [system_design.md](system_design.md) 同级维护。
> 设计文档定义"做什么、怎么算完"（10.0 规则 3），本文件记录"做到哪了、发生过什么"。
> 更新纪律：每完成/阻塞一个工作项即更新对应行；每次工作会话在"会话日志"追加一条；决策与偏离记入"决策记录"。

## 环境状态

| 项 | 状态 | 说明 |
| --- | --- | --- |
| Python | ✅ 3.12.9（uv 0.10.9） | 满足 ≥ 3.11 |
| gcc / make | ✅ gcc 12.3 / make 4.3 | 宿主机可用（正式构建走沙箱） |
| Docker | ✅ 29.3.0 | `ljf` 已加入 docker 组；`nepa-sandbox@sha256:3795cf4e272e1353b0437779511b3433a950e93a4bee3e7a1a2c094992d1de37` 已构建、自验并登记 |
| mosquitto broker | ✅ 2.0.11 | 可执行文件及客户端工具可用；D0.2 在隔离沙箱中逐轮启动临时 broker，不依赖 systemd 常驻服务 |
| DeepSeek API | ✅ 当前会话已设置 `DS_API` | 已执行三次正式 gold spec-run S4 联调；实际能力状态仍按 DEC-12 的显式证据标准逐 raw call 记账 |

## 里程碑总览

| 里程碑 | 状态 | 说明 |
| --- | --- | --- |
| 文档 v0.5.14 | ✅ 完成 | DEC-26 以前置 coverage-readiness ARCH_VALIDATE 门重开 M1-4a N=20，并替代 DEC-23 活动冻结 |
| M0 gold 资产与校验工具 | ✅ 完成 | D0.1～D0.6 全部通过；2026-07-28 修补 Gold 证据后重新完成 D0.2 |
| M1 spec-run 到可构建 | ⏳ 决策阻塞 | S4～S6 controller、run/resume CLI 与确定性交付链已实现；DEC-26 重开后的 N=20 已完成但一次语义修复仅 15/20 通过，等待负责人依据报告重新冻结预算或授权继续调优 |
| M2 一致性验证与修复 | ⬜ 未开始 | |
| M3 文档到规格 | ⬜ 未开始 | |
| M4 端到端闭环 | ⬜ 未开始 | |

## 工作项明细（当前里程碑）

状态：⬜ 未开始 / 🔨 进行中 / ✅ 完成 / ⏳ 外部阻塞 / ❌ 失败待处理

| 工作项 | 内容（详见设计文档 10.1） | 状态 | 备注 |
| --- | --- | --- | --- |
| M0-1 | 第 5 章表格 → JSON Schema + 示例 | ✅ | 10 份 schema 与 10 份示例通过 draft 2020-12 校验；补充 run_store→run.schema 跨模块契约测试 |
| M0-2 | 旧三文件草案迁移映射与归档 | ✅ | 已移入 legacy/，独立字段迁移映射完成并经负责人授权更新设计文档 12.3 |
| M0-3 | 7.1 功能子集冻结确认 | ✅ | 负责人于 2026-07-27 确认当前基线并授权写入 7.1 |
| M0-4 | gold 规格 spec.json（v3.0） | ✅ | 10 种报文；20 条需求的 `source_ref` 引文片段已逐段与本地 OASIS PDF 原文核对 |
| M0-5 | spec_lint / plan_lint + 单测 | ✅ | v0.4 基线 `nepa lint spec/plan` 可用；ARCH_VALIDATE 迁移归 M1-4a，Plan v3 full lint 与 Plan State snapshot/execution lint 已在 M1-4b 完成，不倒改 M0 历史完成状态 |
| M0-6 | gold 测试集（harness + L0/L1/L2） | ✅ | v1 manifest 有 22 个用例；常量从 spec 读取、输入按 seed 随机化；v2 gate/contract/variant 迁移归 M1-4a |
| M0-7 | 参考实现验证（100% + 20 轮） | ✅ | 2026-07-28 以当前 Spec/Test Bundle 在固定沙箱重跑：20/20 轮均为 19 passed / 1 skipped |
| M0-8 | 沙箱镜像 Dockerfile | ✅ | 镜像构建及无网络/非 root 自验通过；digest 与 Dockerfile 哈希登记于 `docker/sandbox-image.json` |
| 基础设施 | pyproject / 包骨架 / LLM 层 / config / run_store | 🔨 | config 已迁移 v0.5 的 S4 角色、规划策略、资产选择和候选预算字段；其余随 M1 闭环 |

### M1 工作项进度

| 工作项 | 状态 | 当前进展 |
| --- | --- | --- |
| M1-1 运行框架 | ✅ | Run v2/run_store、可恢复全局预算、termination request、per-run controller lock、spec-run 创建与四项冻结输入、S4→S6 orchestrator 已实现；resume 先恢复 orphaned running，再按 request 直达免预算 S9 或关闭 planned-stop 窗口；阶段受控错误统一路由 S9，未知实现/工具错误落 internal error |
| M1-2 LLM 层 | ✅ | 两个 provider、结构化输出一次修复、重试、缓存、逐 raw-call trace、成本/预算聚合与 capability probe 已闭环；probe 强制禁用缓存，请求接受仅记 `request_accepted_only`，当前 adapter 无显式证明时保持 `unknown`；非 JSON/缺关键字段等畸形 HTTP 200 统一转为 `ProviderResponseError`，probe 保守记 unknown |
| M1-3 Agent 框架 | ✅ | v0.5 四个 S4 角色与 Coder/Diagnoser/Fixer 已进入默认静态路由；各角色有独立 Schema/prompt，Coder/Fixer 输出包含必填 micro-plan 与完整白名单文件集；S4 模板均有 payload 渲染门，编码角色通过源码扫描与非 MQTT fixture 渲染门，公共 prompt 只消费注入工件 |
| M1-4a 规划输入与 Architecture bring-up | ⏳ | 四份 proposal 已迁活动 Schema；Test Manifest v2 collector、Test Bundle 双摘要、默认三资产、Delivery Constraints/planning index、ArchitecturePlanner prompt、生产 ARCH_VALIDATE 与候选指纹已实现。DEC-26 后正式 N=20 `20260729T141956Z` 为 Schema 首轮 8/20、语义首轮 9/20、一次修复后 15/20；成本 $1.46027314，尚未获得活动冻结签字 |
| M1-4b 确定性编译资产 | ✅ | Plan State/Task Evidence/reconciliation、Test Summary v2、round index/pending WAL Schema 与 canonical 原子发布/crash 对账已实现；未接受 WAL/事实不符会隔离后继续，已接受工件损坏保持 fail-stop；Delivery Blueprint、唯一 task owner 门及按单一最小 ready queue 的稳定 task-id 分配已实现；Plan v3 Schema/示例、layered 与 flat 共用的 PlanDraftIR、6.4.5 九步确定性 Linker、两级 `plan_lint`（basic + stage full）与 `execution_state_lint` 已全部落地并各自单测覆盖 |
| M1-4c 完整 S4 控制器 | ⏳ | 6.4.2～6.4.7 controller、S4-G3、layered/flat、Critic、预算修复、检查点/resume、atomic seal/receipt 与完整 trace 已实现；TaskPlanner 跨包 work-package id 误写入 shard-local `depends_on` 已按 contract 图机械剥离。DEC-26 将 5.2.3 readiness 闭包前置到 ARCH_VALIDATE 后，新的 N=20 已完成但一次修复仍有 5 个不收敛候选；正式 D1.3 需等待活动预算重新冻结 |
| M1-5 S5 | ✅ | Target Profile v2 三段构建图与独立机械契约已迁移；S5 重算 full Blueprint，确定性物化 Target/Language/mechanical/stub 文件，发布 artifact manifest/contract map，建立首提交，执行全部构建变体与 gate=s5 测试、clean、RoundStore summary 和 receipt；partial workspace/首提交后的 resume 与 done no-op 均有正反门 |
| M1-6 S6 | ✅ | Plan State admission/迁移、micro-plan 单任务 Coder/Fixer、文件白名单、T2→T1 尝试、构建/任务测试、Diagnoser、失败恢复、Task Evidence、tree→evidence→commit→state 顺序、commit-state reconciliation、blocked 传播、execution lint 与 S6 receipt 已实现并覆盖成功/耗尽路径 |
| M1-7 CLI | ✅ | `run --spec [--until s6]`、`resume`、`status`、`lint` 均接入真实 controller；Spec 来源原始字节锚点与 run 内 canonical `spec/spec.json` 双锚点分离，run 锁防止并发 controller，terminal resume 不构造外部服务 |
| M1-8 单测与 CI | 🔨 | 当前全量 559 passed；ruff、mypy 绿。Schema/examples、gold lint、prompt 中立、fault injection 专项与成功 gold 三连跑仍须在 Architecture 决策后完成最终验收 |

## M0 DoD 验收记录

| DoD | 状态 | 证据 |
| --- | --- | --- |
| D0.1 | ✅ | gold 模式 `nepa lint spec`：0 error、0 warning |
| D0.2 | ✅ | 2026-07-28 固定 digest、无网络、非 root 沙箱内 20/20 轮全绿；摘要 `passed: true`，当前 Spec SHA-256 为 `92cf26af04f125050e28c0f265c1ba6af950801401dd8579298df7368acd839a` |
| D0.3 | ✅ | 每条 MUST/MUST NOT 均由 Test Bundle manifest 的 `req_ids` 覆盖，gold lint 与 manifest 漂移测试验证 |
| D0.4 | ✅ | 10 份活动 schema 与最小示例互校通过；M0 验收时全量测试 173 passed |
| D0.5 | ✅ | 项目负责人于 2026-07-27 签字确认 7.1 冻结范围 |
| D0.6 | ✅ | 项目负责人授权旧草案归档及 12.3 迁移记录，归档与映射均已完成 |

## 决策记录

| id | 日期 | 决策 | 依据/影响 |
| --- | --- | --- | --- |
| DEC-1 | 2026-07-26 | 测试期所有档位（T1/T2/T3）绑定 DeepSeek：T1=deepseek-reasoner，T2/T3=deepseek-chat，密钥走环境变量 `DS_API` | 用户指示。偏离设计文档 4.6 规则 3“评审角色应当不同 provider”——单 provider 测试配置下 SpecCritic 暂与 SpecExtractor 同厂不同型号，正式实验前需补第二 provider。`deepseek-reasoner` 是否实际应用 temperature 按 DEC-12 probe 标准记账；无 provider 显式证据时 probe 前后均为 `unknown`。temperature 0 不作为确定性保证，bring-up/D1.3 依靠关闭跨 run 缓存后的 N 次独立重复估计稳定性 |
| DEC-2 | 2026-07-26 | Docker 权限开通前，只实现不依赖沙箱执行的模块；沙箱严格按 8.5 实现 docker 后端，不做宿主机执行后备 | 用户选择"开通 docker 权限"方案，无偏离 |
| DEC-3 | 2026-07-26 | D0.2 参考实现验证推迟到 mosquitto 安装后补跑，测试先行编写 | 用户确认；风险 V-3 在此期间未闭合，记录在案 |
| DEC-4 | 2026-07-26 | NePA 仓库提交纪律：阶段性成果由用户确认后提交（未经用户要求不自动 commit/push） | 遵循协作约定 |
| DEC-5 | 2026-07-27 | 负责人确认四项既有约定调整：M1 引入 Target/Language Profile 与 Test Bundle 边界；M6a 前置于 M5；A7 改用独立合成 oracle；Spec IR 收窄为协议事实唯一来源 | 对应设计 v0.4.0；保持 R4/R6、Spec/Plan Schema、gold 数据与 7.4 契约不变 |
| DEC-6 | 2026-07-27 | R4 采用“可直接提取的最小事实层”：Spec IR v3.0 不保存状态机、行为拆解、测试步骤或反向覆盖；复合线格式只增加 `sequence/repeat` | 已同步设计、Schema、gold、lint、切片与 plan requirement 引用；R6 仍待负责人确认 |
| DEC-7 | 2026-07-28 | R6 采用最小输入引用闭包：Plan v2.0 仅以 `{path, sha256}` 绑定 Spec、Target Profile、Language Profile、Test Bundle | 由 S4 控制器确定性注入并由 `plan_lint` 比对；不增加 capability、推理摘要或输入内容副本 |
| DEC-8 | 2026-07-28 | 负责人批准设计 v0.5.0：S4 改为 layered Plan Compiler；Plan v3 静态合同与 Plan State 执行账本分离；S5 独占 scaffold；Test Manifest 升 v2 | 取代 DEC-5“Plan Schema 不变”和 DEC-7“Plan v2 为活动版本”的部分，但保留 DEC-7 四项最小 `input_refs`；本次仅修改 `system_design.md` 与 `dev_status.md`，实际 Schema/prompt/lint/gold 资产暂不修改并转入 M1 |
| DEC-9 | 2026-07-28 | 负责人批准 v0.5.0 终审闭环：M1 用 `--until s6` planned stop；round 发布引入 pending WAL；S8 单轮单簇且快验失败不提交；M5 拆出 M5-prep/M5-0 scale gate；Report 明确四态执行计数 | 同步消除 M1 正常终止、S5 故障注入、round 恢复、S8 预算、M5 gate 循环依赖与 report 聚合歧义；仍只修改两份文档，实际资产继续按里程碑迁移 |
| DEC-10 | 2026-07-28 | 负责人要求通用 Coder prompt 不含任何 `mqtt_*`，协议专属命名只能由冻结资产/工件注入；确认旧 7.3 session 签名无法表达 broker 扇出，按 11.3 立项 O-18 | 设计升 v0.5.1；O-18 在 M1-5 模板冻结前必须裁决连接寻址、共享 broker 状态、out batch 与 K×消息容量/满载行为；实际 prompt 当前扫描无 `mqtt_*`，本次不改资产 |
| DEC-11 | 2026-07-28 | 完整 Plan Compiler 前先做 ArchitecturePlanner prompt + 生产 ARCH_VALIDATE 的 N=20 bring-up；原 M1-4 拆成 M1-4a/b/c，依据逐门/联合首次通过率和一次修复收益冻结 prompt/Schema/validator 与架构修复默认值 | S4 是全链最高经验不确定点；spike 产物隔离在 `runs/_bringup`，不冒充正式 Run/S4 结果。全局重规划因依赖 Critic，只在 M1-4a 记录进入 M1-4c 的暂定上限，正式默认值由 D1.3 复核；M1-4b 可并行，M1-4c 冻结/联调受决策门约束；本次仍不修改实际资产 |
| DEC-12 | 2026-07-28 | Capability probe 采用严格 provider-report 证据标准：请求被 API 接受仅证明语法可接受，输出统计推断也不能证明参数已应用；只有 provider 响应/专属 capability 端点显式报告才能写 `reported_applied/reported_ignored`，否则保持 `unknown` | 负责人批准；设计升 v0.5.3。probe 关闭缓存并分别记录请求接受状态和证据种类；DeepSeek 无显式证明时 probe 后仍为 `unknown` |
| DEC-13 | 2026-07-28 | Run v2 `inputs` 采用与 Plan 同名的嵌套引用并按 entry 强制形态；三项资产锚定 run 内冻结解析描述，源文件引用哈希原始字节；项目 canonical JSON 冻结为 CPython 精确参数算法，不采用 RFC 8785/JCS | 负责人批准，设计升 v0.5.4；共享 `nepa.canonical` 成为唯一 serializer，非字符串键和 NaN/Inf 拒绝。文件引用哈希原始字节，内存对象才用 canonical hash；跨语言一致性若需要必须另开问题并走主版本 |
| DEC-14 | 2026-07-28 | 全局 `wall_clock_s` 采用跨 resume 累计的活跃 controller 运行时间，进程离线、人工审阅暂停及两次 resume 间隔不计入预算 | 负责人批准，设计升 v0.5.5；每会话以 monotonic clock 计量并在阶段/外部调用边界原子累加。缓存重放只计本地活跃时间，不重复累计 provider 成本/token |
| DEC-15 | 2026-07-28 | Report v2 条件字段统一采用 availability envelope；available 必须非 null 且无 reason，invalid/unavailable/not_run 必须 null 并带开放机器码与说明；artifact availability 不重复 value | 负责人批准，设计升 v0.5.6；完整条件化 Schema 在 M1 一次冻结，partial/full producer 共用。报告顶层增加机器可读 `termination_reason`，条件指标不以空值或 0 冒充测量 |
| DEC-16 | 2026-07-29 | 受控出口先原子写 `termination_request`；internal error 可保留 request；request stage 仅 s1～s8 且状态限 failed/pending；resume 统一回收 orphaned running，S9 免预算并只复制 request reason；`--until` 持久化到 config snapshot | 负责人批准，设计升 v0.5.7。补充裁决：`s9=done` 但 receipt/Schema/hash/request 绑定失败属于终态工件损坏，直接 finalize internal error，禁止开放 done→running |
| DEC-17 | 2026-07-29 | Plan State 五态采用完整字段条件表：pending/in-progress/done/blocked/blocked-by-dependency 分别锁定 attempts、notes、commit/evidence 与 last_error | 负责人批准，设计升 v0.5.8；blocked 仅在 total limit 耗尽后成立，in-progress 新 attempt 清除旧错误，done 独占 commit/evidence |
| DEC-18 | 2026-07-29 | Plan State 迁移采用五类判别事件，原子 API 从磁盘完整 State 与 Plan 自行推导新状态；dependency blocked 不信任调用方自报，reconciled commit 使用类型化 proof | 负责人要求继续执行，设计升 v0.5.9；终态不可改写，attempt 严格递增且旧错误在新 attempt 状态中清除 |
| DEC-19 | 2026-07-29 | Task Evidence 为 canonical 不可变闭合工件；task commit 先封存 staged tree、再发布 evidence、最后写固定 trailers，reconciliation 联合验证后才生成 proof | 按既有 5.4/6.6 证据顺序落成设计 v0.5.10；防止 commit/tree/evidence 任一侧可独立替换或普通调用方伪造恢复证明 |
| DEC-20 | 2026-07-29 | Test Summary v2 精确锁定 trigger context、构建/用例四态及 frozen hashes；req_matrix 由 cases 按 error>fail>skipped>pass 确定性重算 | 设计升 v0.5.11；build-only task 可 cases 为空但 build 不得空，Summary 的存在不代替 round index 接受事实 |
| DEC-21 | 2026-07-29 | 负责人批准 M1-4a 四份 inactive Schema proposal 与 README 十项裁决点迁为活动实现；同时批准 O-18 拆分 client session/shared broker core、稳定 conn_id、有界目标 batch 且禁止静默截断的方向 | 按负责人要求，本批只迁 Schema/实现并更新开发状态，不修改主设计文档；O-18 数值容量和精确满载返回语义仍须在 MQTT 专属模板落盘前冻结 |
| DEC-22 | 2026-07-29 | 负责人确认 O-18 精确 ABI 容量：非零 `uint32_t conn_id`、最大连接数/K=16、单项 4096 bytes、batch 65536 bytes；超限原子失败且不返回部分成功，连接容量报 CAPACITY，报文/输出过大报 RESOURCE_LIMIT 并关闭来源连接 | MQTT Target Profile resource limits、session/net 冻结头模板与 C99 编译/静态边界测试已同步；broker core 与 client session 使用不同 opaque 类型和 caller-provided storage，net 只按 conn_id 路由 batch |
| DEC-23 | 2026-07-29 | 负责人批准冻结 M1-4a N=20 ArchitecturePlanner 基线：prompt/schema/validator/输入指纹以 `runs/_bringup/s4-architecture/20260729T035703Z/spike_report.json` 为准；`plan_architecture_repairs=1`，`plan_global_replans=1` 为进入 M1-4c 的暂定上限 | N=20 中 Schema 首轮 18/20、ARCH_VALIDATE 首轮 10/20、一次修复后 19/20；全局重规划尚未经 Critic 测量，D1.3 必须复核所有正式 S4 预算 |
| DEC-24 | 2026-07-29 | 负责人审查后授权收口实现/文档偏差：S6 允许白名单子集交付且只 stage 实际变更；任一全局预算入口跳转按 9.1.2 degraded；畸形 HTTP 200 归一为 LLMError；Linker 使用逐个最小 ready queue；未接受的坏 WAL 隔离后继续；Test Summary 只经 RoundStore 发布；O-18 正式关闭并回填 7.3 | 历史 spike 同步按实际响应模型补价并对账请求/响应模型，成本为 $1.04648848；不改变 DEC-23 的通过率与暂定预算。设计升 v0.5.12，12.3 仓库现状同步 |
| DEC-25 | 2026-07-29 | 负责人批准 Target Profile major 迁移：采用 deliverable→build artifact→link source set 三段显式构建图，并选择独立机械生成契约；`mechanical_spec` file rule 必须绑定模板 | Target Profile 升 v2 且新增三段/机械段必填字段；S4-G1/full lint 与 S5 必须做引用和集合闭合，Makefile 禁止从文件名猜目标。默认布局删除无 file rule 的 `src/util/`，MQTT broker 源 file rule 更名为 `broker-entry-source`，避免与 deliverable `broker-app` 混淆。设计升 v0.5.13 |
| DEC-26 | 2026-07-29 | 负责人批准以 5.2.3 coverage readiness 工作包级可行性门重开 M1-4a，并用新 N=20 替代 DEC-23 活动冻结 | ARCH_VALIDATE `arch_10` 新增 `ARCH_TEST_READINESS_UNCLOSED`：每个 task test 的 contract provider 与全部 REQ primary/supporting 工作包必须存在共同下游祖先闭包；同步强化 ArchitecturePlanner，重跑无跨 trial 缓存 N=20 后再冻结 prompt/validator hash 与正式预算。设计升 v0.5.14 |

## 待办（需用户/负责人动作）

- [x] 已批准 `project_docs/schema_proposals/m1-4a/` 四份 Schema proposal 与 README 十项裁决点；活动 Schema/示例已迁入 `nepa/schemas/`
- [x] O-18 精确容量、错误语义与 MQTT session/net 模板已冻结并通过 C99 `-Werror` 编译门
- [ ] DEC-26 N=20 已完成：审阅 `runs/_bringup/s4-architecture/20260729T141956Z/spike_report.json`（Schema 首轮 8/20、语义首轮 9/20、一次修复 15/20），并决定是否继续调优或以何种 `plan_architecture_repairs`/`plan_global_replans` 预算重新冻结活动基线
- [ ] 按 DEC-4 审阅并确认当前 M1 基础实现与本批修补后再提交
- [ ] 复核 M1-4c 新增的三份活动资产：`nepa/schemas/s4-state.schema.json`（`plan/_s4` 检查点状态，5.6.6 只列出文件清单、未规定字段）、`nepa/schemas/flat-plan-draft.schema.json` 与 `nepa/agents/prompts/flat_plan_baseline.md`（A9 消融，6.4 只规定"一次调用产出完整语义草稿"）。三者的字段形状均按既有 `architecture-draft` + `task-shard` 逐字段同形推导，未引入新语义契约；主设计文档未修改
- [ ] 复核两处 M1-4c 判断：(a) 纯机械问题重链不消耗任何语义修复配额（6.4.6 只说"由控制器修正后重新链接"，未给配额；确定性重链幂等，复现由不收敛门兜住）；(b) `s4-state.schema.json` 新增可选 `seal` 块记录崩溃重跑时是否复用了逐字节一致的盘上 Plan（6.4.7 规定了该复用条件，但未规定记录位置）
- [x] M1-5 构建/机械接口按 DEC-25 裁决：Target Profile v2 使用 deliverable→artifact→link source set 三段声明，并采用独立机械生成契约

## 会话日志

### 2026-07-29（会话 15）
- 负责人批准 DEC-25；先按 0.1/11.3 更新主设计到 v0.5.13，再迁实现。三段构建图使用独立命名空间和显式引用，机械契约声明模板、输入域与输出 rule；7.2 删除没有 file rule/责任的 `src/util/`，MQTT broker 源 rule 将与 deliverable 分名。
- 完成 Target Profile v2 实现迁移：新增必填 `build_artifacts[]`、`link_source_sets[]`、`mechanical_generation_contracts[]` 与相应闭合门；MQTT 默认资产采用 deliverable→artifact→link source set 显式三段图，机械输出绑定独立模板/输入域，app 源 rule 改为独立命名空间。Makefile 按 artifact 独立对象目录生成 executable/static/shared 目标，S5 机械 ABI 生成不含协议 identity 分支。
- 完成 S5/S6 与 M1 runtime：S5 发布 artifact manifest/contract map、首提交、双构建、S5 tests、RoundStore summary/receipt；S6 实现 micro-plan 编码/修复、白名单、构建/任务门、诊断升级、失败恢复、Task Evidence/commit trailers/Plan State 对账与 receipt。新增 `run/resume` CLI、四项冻结输入、Spec 来源/冻结副本双锚点、controller lock、阶段错误→S9 与 planned-stop 路由；当前全量回归 559 passed，ruff/mypy 绿。
- 执行三次无跨 run 缓存的正式 gold S4 联调：`20260729T1312Z_mqtt_spec-run` 因 TaskPlanner 把跨工作包 id 写入 shard-local `depends_on` 耗尽局部预算，已用“仅剥离精确命中 work-package id 的冗余依赖、其他未知 id 仍硬失败”机械规范化修正；该 run 正确产出 S9 partial report、`controlled_exit/failed/20` 与 14 条完整 trace。`20260729T1318Z_mqtt_spec-run` 通过 ARCH_VALIDATE/shards 后在 5.2.3 coverage readiness 失败，一次 global replan 后同类 `PLAN_LINK_FAILED` 复现并按不收敛门退出；Linker 反馈现列出 required provider/implementation tasks 与候选闭包。`20260729T1327Z_mqtt_spec-run` 的 Architecture 初次及一次修复均把三个 task-ready external contract 错改为 s5，按 `PLAN_ARCH_VALIDATE_FAILED` 受控退出。
- 上述证据使 D1.3 决策阻塞：DEC-23 冻结的 Architecture prompt/validator/输入指纹没有前置表达设计 5.2.3 的 contract provider + 全部 primary/supporting implementation readiness 闭包；若在 ARCH_VALIDATE 新增工作包级可行性门并强化 Architecture prompt/规划输入，必须重做 N=20 bring-up 并由负责人重新冻结，不能由实现层静默修改。
- 本会话先修 S4/Plan 硬门：S4 PREPARE 现逐项核验传入的 Spec/Target/Language/Test Bundle/Manifest 与 run 内冻结文件的 canonical 内容绑定；Test Manifest 的每个 MUST/MUST NOT 必须至少有 `task` 或 `s7_only` 测试，且 `s5` gate 只可依赖 `s5` ready contract；Linker 的不可定位失败统一进入既有全局重规划预算，不再绕开修复路由。新增回归门后相关 S4/Linker/full-lint 集合为 121 passed。
- 开始 M1-5 前发现一项阻塞性资产接口缺口：现有 Target Profile 仅有 deliverables/file rules/external contracts，不能确定性得出 executable 名称和链接源集；`mechanical_spec` 文件槽也没有可生成完整公开 ABI 的字段。已记录待负责人裁决，未修改主设计文档，也未用协议文件名作隐式约定。
- M1-4c 主体落地：`nepa/stages/s4_plan.py` 实现 6.4.2 全状态机 `PREPARE → DELIVERY_CONSTRAINTS → SELECT_STRATEGY → {layered: ARCHITECT → ARCH_VALIDATE → EXPAND_WORK_PACKAGES → SHARD_VALIDATE | flat: FLAT_DRAFT → FLAT_VALIDATE} → LINK_AND_RESOLVE_BLUEPRINT → PLAN_LINT_AND_SIMULATE → PLAN_CRITIC → {REEXPAND_WORK_PACKAGE | REPLAN_ARCHITECTURE | REPLAN_FLAT_DRAFT | SEAL_AND_PUBLISH}`；工作包按稳定 id 串行展开（4.9 v1 禁并发），每次 L3 调用仍只做一个认知任务。
- 新增 `nepa/task_shard.py`：TaskPlanner 输入构造只投喂本包切片、相关架构决策、spec 切片、邻接契约摘要、`allowed_files`/`s5_frozen_files` 与 Test Manifest v2 六个白名单元数据字段（P1 反作弊边界，`layer` 也不外泄）；S4-G3 生产门覆盖 schema/包 id/局部 id 重复/单任务≤4 文件/文件越界与冻结/文件分区等式/契约集合等式/责任重复与越界/primary 唯一/未细化/依赖未知与自环与环共 16 个稳定错误码。
- 预算与修复按 6.4.6 记账：架构一次定点修复，每个工作包一次局部重做（shard 门与 Critic 局部问题共用同一配额），全局问题最多回架构一次并作废受影响 shard，flat 的唯一修复路径是整份重绘且同时消耗 critic 与全局重规划各一次；相同 issue signature 复现即 `PLAN_NOT_CONVERGING`；结构化输出二次失败、预算耗尽一律受控失败且不发布部分 Plan。控制器自行复核 verdict：Critic 报 pass 但存在 blocker/major 时按 revise 处理，未解决 minor 由控制器重新编号后写入 `plan.review.unresolved_minor_issues`。
- `plan/_s4` 检查点按 5.6.6 落盘并绑定直接父哈希，只有 Schema 校验通过才标 valid；resume 只复用"valid + 父哈希匹配 + 盘上内容哈希一致"的工件，`input_refs`/`planning_fingerprint`/`strategy` 任一漂移即整份作废。6.4.7 封口为复跑 full lint → 原子发布 → 重读校验 SHA-256/四项 `input_refs`/顶层 blueprint 哈希 → 原子写 `run.json` receipt；S4 已 done 时只核对 receipt 并作为只读 no-op。
- 修掉一个真实缺陷：`task_planner.md` 与 `plan_critic.md` 模板从未渲染 `{{ payload_json }}`，这两个角色实际会收到空输入；已补上输入块，并新增"四个 S4 模板必须渲染各自 payload"的门防回归。同时修正 `tests/plan_v3.py` 的 Spec 夹具（`transport.req_ids` 缺失、`source_ref` 误用数组），此前只有不跑 spec lint 的 Linker 测试用到它，S4 PREPARE 会跑全量 spec lint 才暴露。
- 新增 30 个无 LLM 的控制器测试（`tests/test_s4_plan.py` + `tests/s4_stubs.py` 角色响应队列桩）：封口 receipt 与 phase、检查点父哈希链、review 轮次记录、Critic/TaskPlanner payload 的测试实现不可见性、verdict 覆盖、minor 重新编号、局部重做只动本包、shard 门与预算耗尽、全局重规划与其预算耗尽、critic 预算耗尽、不收敛、架构一次修复与二次失败、结构化输出失败、spec lint 与冻结输入漂移在任何 LLM 调用前拦截、未知策略、flat 单次调用/整份重绘/绝不 fallback、resume 复用与指纹漂移/内容篡改作废、done 只读 no-op、封口后被改动的 Plan 报 `PLAN_RECEIPT_INVALID`。
- 复读设计 §5.5/§6.4.2–6.4.7/§10.2 后补齐四处缺口：(1) S4 调用的追踪证据 `compiler_phase`、`work_package_id`、`parent_artifact_sha256`、`repair_budget_used` 由 `_invoke` 经 `AgentRunner.invoke(trace_extra=)` → `LLMClient.complete(trace_extra=)` → `TraceWriter.record(extra=)` 逐层下传，`extra` 只允许新增键，撞上 5.5 公共字段即报错以免证据被覆盖；(2) provider 报 `finish_reason ∈ {length, max_tokens, model_length}` 时即使 Schema 通过也抛 `TruncatedOutputError`，S4 转 `PLAN_OUTPUT_TRUNCATED` 受控失败；(3) 纯机械问题（route=mechanical）由控制器修正后回 `LINK_AND_RESOLVE_BLUEPRINT` 重链，不消耗任何语义配额——确定性重链是幂等的，同一机械 signature 复现由已登记的不收敛门终止；(4) PREPARE 追加复核 Run v2 config snapshot 哈希与 `input_refs` 逐类与 `run.json` inputs 声明的一致性。
- 6.4.7 收尾细节：`atomic_write_canonical_json` 在 `os.replace` 后补 fsync 目录（平台不支持时忽略），满足"fsync 文件和目录"；`s4-state.schema.json` 新增可选 `seal: {reused_existing_plan, plan_sha256}`，记录"发布后写 receipt 前崩溃"重跑时是否复用了逐字节一致的盘上 Plan，并新增两个门覆盖复用与不一致残留必须重发布两条路径。
- 完整门禁：535 passed，`ruff check nepa tests`、`mypy nepa`、gold `lint spec --gold`（0 error / 0 warning）与 `git diff --check` 全绿。主设计文档未修改，本会话不提交。
- DEC-26 的无跨 trial 缓存正式 N=20 已完成：`runs/_bringup/s4-architecture/20260729T141956Z/spike_report.json` 可由逐 trial 重算；Schema 首轮 8/20、语义首轮 9/20、一次语义修复后 15/20，31 个逻辑调用、$1.46027314。首轮主要失败集中于 `arch_09`/`arch_10`，共现最高为 `arch_09+arch_10`（7）；剩余 5 个修复失败分别为外部 contract 漂移、三个内部槽缺失、依赖推导加主责残差、依赖推导残差、及两个 `ARCH_TEST_READINESS_UNCLOSED`。因此该批尚未替代 DEC-23 的活动签字，后续必须由负责人决定继续调优或冻结更高修复预算。
- 在不变更 ArchitectureDraft Schema、ARCH_VALIDATE 或修复次数的前提下，强化 ArchitecturePlanner 的定点修复步骤（逐条错误路径、精确外部字段回填、内部槽、依赖重算）；相关回归 68 passed、ruff/mypy/diff 检查通过。独立 3-trial 预跑 `20260729T152625Z` 前两 trial 均在一次语义修复后通过；第 3 个 provider 调用超过有限等待窗口且未写响应，已终止，该不完整批次仅作诊断、不得计入正式基线。
- 提示词收敛后完整本地回归为 561 passed；`ruff check nepa tests`、`mypy nepa` 与 `git diff --check` 通过。新 prompt hash 的正式 N=20 `20260729T153634Z` 正在按无跨 trial 缓存协议执行，结果未完成前不更新活动冻结。
- `20260729T153634Z` 在第 9 个 trial 的语义修复 HTTP 调用因网络重启保持连接约五分钟；停止请求后核验发现第 9 条已自行完成并封存，且前 1–9 条的输入/配置/prompt/Schema/validator/资产哈希均未漂移。负责人指示保留这些完整独立样本并仅继续未开始的条目；因此从第 10 条恢复同一 batch，绝不覆盖或重抽第 1–9 条。该恢复尚未完成，不能用于活动冻结。

### 2026-07-29（会话 14）
- M1-4b 收口：Plan v3 Schema/示例落地（无 `status/attempts/notes`、无 scaffold task、无顶层 `modules`，`ready_gate ∈ {s5, task}` 与 `gate ∈ {s5, task, s7_only}` 分离），layered 与 flat 策略统一规范化为同一 `PlanDraftIR`。
- 实现 6.4.5 九步确定性 Linker：集合等式与责任细化校验 → 唯一 provider task 解析与派生跨包依赖边 → 稳定 Kahn 序分配 `T-###` → 注入 requirement `context_refs` 与精确构建变体 → 生成 coverage（gate=task 绑定拓扑序中最早合法闭包）→ 反向注入 enabled nodeid 到 `acceptance.tests` → 编译并哈希 Delivery Blueprint → 注入四项 `input_refs` 与顶层 `delivery_blueprint_sha256`，同时输出可审计 `link_report`。哈希顺序保证无环：coverage 先于 acceptance 注入，acceptance 先于 Blueprint 编译，封口哈希只存在于 Plan 顶层。
- `plan_lint` 重写为 Plan v3 两级门：basic lint 覆盖结构/冻结输入/双层 DAG/文件全分区/契约三层集合等式/责任与覆盖闭包/coverage 重算等式；新增 stage `plan_full_lint` 追加 Blueprint 封口与 s5_frozen 归属、构建变体引用、测试就绪性与规划索引预算。缺清单或 config snapshot 时 coverage 重算降级为 `PLAN-COVERAGE-UNCHECKED` 警告而非误报。
- 新增 `execution_state_lint`（5.2.5 第三项，供 S6 准入）：核对 S4/S5 receipt 与 Blueprint 封口、S5 首提交祖先关系、done task 的 commit trailers/证据内容/依赖 commit 祖先、孤儿证据、Test Summary 双摘要与 workspace clean；`GitOps` 相应新增 `is_ancestor`。
- `nepa lint plan` 新增 `--run-dir/--repo-root`：从 run 目录的 `run.json` config snapshot 与 `inputs/*.json` 重建四项冻结输入与 Delivery 资产后跑 full lint。
- 测试夹具 `tests/plan_v3.py` 用真实 Linker 从示例资产生成 Plan，使 Schema、Linker、basic lint 与 full lint 校验同一工件；新增 Linker（38）、`execution_state_lint`（23）与 CLI full-lint 用例，`test_plan_lint.py`/`test_schemas.py`/`test_cli.py` 迁至 v3。
- 完整门禁：463 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。开发期间未修改主设计文档，本会话不提交。

### 2026-07-29（会话 13）
- 落实负责人代码审查：Git task commit 从整白名单 `add --all` 改为只暂存已验证实际变更，覆盖从未创建的白名单文件；S4/S6 admission 前全局预算耗尽均确定性分类为 degraded/exit 10。
- OpenAI-compatible/Anthropic adapter 对非 JSON、error-shaped 或缺关键字段的 HTTP 200 统一抛 `ProviderResponseError`；真实 probe 路径验证失败保守记 `unknown/no_response`。
- PlanDraft task id 改为单一最小 ready heap 逐项 Kahn，消除波次排序漂移；Test Summary 删除绕过 WAL 的最终路径，RoundStore 对未接受坏 WAL/目录隔离后继续并复用编号，已接受证据损坏仍 fail-stop。
- 为 `deepseek-v4-flash` 补历史同档价格，重算冻结 spike 的 629074 input/319862 output token 为 $1.04648848；指纹分记 requested/response model 并标记 mismatch，DEC-23 通过率与预算不变。
- 主设计升 v0.5.12：O-18 按 DEC-22 关闭并回填 7.2/7.3/11.2，12.3 更新为当前 Schema/prompt/profile/manifest 现状；新增 DEC-24。
- 完整门禁：369 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。本会话不提交。

### 2026-07-29（会话 12）
- 负责人确认 DEC-16 受控出口恢复协议及 `s9=done` 损坏处理；主设计升 v0.5.7，Run v2 Schema/model 同步锁定 termination request 条件表与 controlled-exit outcome。
- 新增 Run/Report 共用 `Reason`；partial Report 直接复制持久化 request reason，S9 不再接收或重推 reason 参数。
- `run_store` 新增受控退出原子请求、request-stage lint 与 orphaned running 原子恢复；internal error 保留既有 request，completed/planned stop 禁止 request。
- 新增 M1 resume coordinator：request 窗口跳过非 S9 阶段，S9 前后预算同步固定 `enforce=false`；done 窗口校验 receipt、文件哈希、Report Schema、outcome 与 reason 绑定；producer/工件错误落 internal error。
- 默认配置新增 `run.until`，CLI 合并值随完整 config snapshot 封存；resume 可在 s6 done、planned-stop finalize 前崩溃的窗口确定性停止，保持 S7 pending。
- 完整门禁：249 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。本会话不提交。
- 负责人进一步冻结 Plan State 五态字段条件表，设计升 v0.5.8 并新增 DEC-17；新增 Plan State v1 Schema/示例、canonical 初始化发布与 snapshot lint，覆盖 S4/config 锚点、task 集合、attempt 上限、blocked 耗尽及 done evidence attempt 路径。
- 完整门禁更新为 264 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。
- Plan State 迁移协议升 v0.5.9：实现 attempt started/succeeded/exhausted、dependency blocked、reconciled commit 五类事件与完整 State 原子更新；终态不可改写，dependency proof 从 Plan/State 重算，调用方不能注入任意 new state。
- 完整门禁更新为 269 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。
- Task Evidence v1 与 task commit 证据链升 v0.5.10：新增 Schema/canonical immutable producer/source-ref 校验；Git 提交拆为 staged tree 封存与 evidence trailers commit，联合验证成功后才生成不可直接构造的 reconciliation proof。
- 完整门禁更新为 286 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。
- S6 reconciliation 核心新增 done task 反向审计与 in-progress commit-before-state 前向发现/原子补记；baseline HEAD 返回无 proof，部分 trailers、脏工作树、attempt 错位或来源 evidence 漂移均拒绝恢复。
- 完整门禁更新为 291 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。本会话不提交。
- Test Summary 活动 Schema 升至 v2，并新增确定性 producer：锁定五类 trigger context、四项 frozen hash、构建/用例结果，build-only 空 cases 合法；req matrix 由 cases 按 error>fail>skipped>pass 重算，canonical immutable 发布。
- 完整门禁更新为 299 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。
- round index v1 与 pending WAL v1 落地：accepted id 从 1 连续递增并校验 parent/ref，发布严格采用 temp→WAL→rename→index→clear；resume 覆盖 WAL 位于 temp/final/index 后的三个崩溃窗口，无 WAL 的未登记目录只隔离、不前向接受，连续编号可复用。
- 故障注入覆盖 pre-WAL temp、WAL 后、rename 后、index 后、无 WAL final、hash/context/id/parent 损坏；完整门禁更新为 311 passed，ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。本会话不提交。
- M1-7 新增只读 `nepa status`：支持显式 run path 或 `--runs-root` 下的 run id，联合 run.json、可选 S4 checkpoint 与 Plan State 输出稳定 JSON/简洁人读进度；不提供无法推进普通阶段的伪 `resume`。
- 完整门禁更新为 314 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。开发期间不修改主设计文档，本会话不提交。
- 负责人批准 M1-4a 四份 proposal/十项裁决点与 O-18 推荐方向，并要求开发期间不修改主设计文档。四份 Schema/示例已迁活动目录，proposal README 改为审批追溯记录。
- Test Manifest 升 v2：22 个 gold 测试从显式 pytest gate/contract/build-variant 元数据收集并 canonical 发布，S4 visibility 不含 layer；新增 Test Bundle manifest/tree 双摘要、组件 ref 与 source profile 原始字节校验。
- 新增确定性 Delivery Constraints/file-rule 展开、planning index/上下文预算预检、十二类生产 `ARCH_VALIDATE`、独立 ArchitecturePlanner prompt 与 prompt/Schema/validator 候选指纹；O-18 专属模板因数值容量尚未给定停在落盘前。
- 完整门禁更新为 338 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。主设计文档未修改，本会话不提交。
- 负责人确认 O-18 精确数值和原子失败语义。新增默认 MQTT Target、C99/POSIX Language 与 Test Bundle source/解析构建器；解析描述 canonical 发布并绑定源文件、模板、Manifest、runner/oracle/adapter 及 bundle tree 的真实字节摘要。
- 新增 MQTT session/net C99 冻结模板：client session 与 shared broker core 分型、caller-provided opaque storage、稳定非零 conn id、四事件 API、有界 targeted batch；Target Profile 四项 resource limit 与头文件宏逐值对账，并通过 `gcc -std=c99 -Wall -Wextra -Werror -fsyntax-only`。
- 新增 S5 Target/Language template materializer：只允许写 Delivery Constraints 中对应 producer 的 `s5_frozen` file slot，模板树存在未声明文件、缺槽位或 hash 漂移均拒绝。
- M1-4a N=20 ArchitecturePlanner bring-up 完成并隔离发布于 `runs/_bringup/s4-architecture/20260729T035703Z/`：20/20 有候选，Schema 首轮 18/20，ARCH_VALIDATE 首轮 10/20、一次修复后 19/20；逐门失败共现、调用用量与 candidate fingerprint 均在 `spike_report.json`。完整门禁更新为 348 passed，ruff、mypy、gold manifest 再生成比对与 `git diff --check` 全绿；主设计文档未修改，本会话不提交。
- 负责人批准 DEC-23：以该 N=20 报告冻结 ArchitecturePlanner 基线，`plan_architecture_repairs=1` 与 `plan_global_replans=1` 进入 M1-4c（D1.3 仍须复核）。新增 TaskShard 与 PlanCritic 活动 Schema/prompt，TaskPlanner 只能输出局部 id 语义草稿、Critic 只能输出结构化 issue list；Delivery Compiler 新增无副作用的 Blueprint 投影并锁定 s6 文件唯一 owner。完整门禁更新为 355 passed，ruff、mypy 与 `git diff --check` 全绿；主设计文档未修改，本会话不提交。
- 新增 PlanDraftIR 的首个确定性 Linker 切片：局部 task 依赖按稳定 Kahn 顺序分配最终 `T-###`，拒绝重复/未知依赖与环；完整 PlanDraftIR、跨工作包 contract 链接和 Plan v3 发布语义仍归 M1-4b 后续。
- 本次状态核对完整门禁更新为 358 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。主设计文档未修改，本会话不提交。

### 2026-07-28（会话 11）
- 负责人批准 capability probe 证据标准；主设计升 v0.5.3，记录“请求接受不等于参数已应用、统计推断不得升级状态、只认 provider 显式报告”，新增 DEC-12。
- 新增 `nepa.llm.capabilities`：probe 使用最小非结构化请求并强制 `use_cache=False`，结果以严格模型记录 provider/model、请求参数、接受状态、逐参数能力/证据、token/成本/延迟与错误；失败或缺项均保守为 `unknown`。
- `LLMFactory.probe_for` 提供生产执行入口；`LLMClient.complete` 增加显式 cache bypass。正向覆盖请求接受但未知、显式 applied/ignored、部分报告，反向覆盖请求失败与意外缓存响应。
- 新增通用代码角色 prompt lint：模板源码按设计正则扫描，非 MQTT fixture 对协议名、路径和接口前缀做更严格渲染审计；Coder/Diagnoser/Fixer 均通过。`AgentRunner.render_prompt` 让生产调用与审计复用同一渲染路径。
- 清除公共 Coder prompt 中 C99、POSIX、malloc 与固定行数等 Language Profile 职责硬编码，三个角色的最小示例改用中性扩展名；具体语言与资源策略只从任务输入注入。
- 负责人冻结 Run v2 inputs 与哈希契约，设计升 v0.5.4 并新增 DEC-13。新增共享 `nepa.canonical`，精确实现键排序/紧凑分隔符/UTF-8/无尾随换行/非字符串键与非有限浮点拒绝；LLM cache key 迁移为复用同一 serializer，并提供 canonical JSON 原子发布函数。
- Run Schema/示例与 `run_store` 迁移至 v2：spec/doc entry 绑定（doc-run scope 必填）、三项固定冻结描述引用、config snapshot canonical hash 重算、阶段 output refs、四类条件终态与新版目录树均已落地。默认 Test Bundle id 同步修正为符合全局 id 规则的 `mqtt-3-1-1-min-gold`。
- 负责人确认墙钟预算按跨 resume 累计活跃 controller 时间计量，设计升 v0.5.5 并新增 DEC-14。新增 `RunBudget`：monotonic 分会话记账、调用边界原子落盘、缓存零 provider 消耗、达到 wall/cost 边界后先持久化再抛 `BudgetExhausted`。
- spec-run 创建时确定性把 S1～S3 标为 skipped；`first_incomplete_stage` 与 `begin_stage` 支持固定顺序恢复，已完成阶段幂等空操作，崩溃遗留 running 阶段可直接恢复。
- 全局 wall/cost 配置值增加严格正数校验，避免无效预算进入运行期。
- 负责人确认 Report v2 availability envelope，设计升 v0.5.6 并新增 DEC-15。活动 Report Schema/示例迁至 v2，条件覆盖、测试终态、过程统计、指标、假设缺陷与复现锚点均禁止用 0/空值伪装缺失测量。
- 新增 M1 partial producer：按 stage receipt/hash 检查工件，支持 S4 无 Plan、S5 已封存 Plan 的静态 coverage、S6 活动 Plan State 三类受控早退；从 raw-call trace 聚合 stage/role/model/tier 成本，报告以 canonical 字节发布并在写前做 Schema 自检。LLM trace 同步新增实际解析 `tier` 字段。
- 完整门禁：228 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。M1-2 按当前设计工作项收口完成；真实 DeepSeek probe 待 `DS_API` 可用时运行，但不改变无显式证据即 `unknown` 的实现结论。本会话不提交。

### 2026-07-28（会话 10）
- 按负责人要求把 Target Profile、Language Profile、Test Bundle 描述与 ArchitectureDraft 四份候选 Schema/示例隔离放入 `project_docs/schema_proposals/m1-4a/`，保持 proposal/inactive，不修改主设计或活动 Schema。
- 落实负责人首轮审阅：Target 示例新增 `Makefile` 的 `language_template/s5_frozen` file rule 与 `src/net.c` 的 `stub/s6_owned` rule；跨示例展开验证确认 module `owns_files` 恰好完整互斥分区全部 S6 slots，contract 文件均有来源；从 S4 planning visibility 删除设计 6.4.7 白名单之外的 `layer`。
- 继续无裁决依赖的 M1-2：引入仅运行时、不进缓存的 provider raw-call 记录；结构化初次调用与格式修复调用分别落 trace，保留各自实际 prompt/output/token/成本/延迟/能力状态，并把成本聚合回逻辑响应和失败预算回调；缓存命中显式记为零成本 `cache_replay`。
- 完整门禁：176 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。本会话不提交。

### 2026-07-28（会话 9）
- 继续 M1 的无裁决依赖切片：把 `parameter_support` 与 `provider_metadata` 加入统一 LLM 响应，OpenAI-compatible/Anthropic adapter 在 API 不提供实际应用证明时均按设计记 `unknown`，并保留响应 id/`finish_reason`。
- trace 字段迁移为 `params_requested`、`parameter_support`、`provider_metadata`、`finish_reason`；结构化修复聚合响应保留两次 provider 元数据。D1.6 要求的逐 raw call 独立 trace 尚未完成，未宣称 M1-2 完成。
- 默认配置迁移 v0.5 S4 四角色、layered/flat planning 策略、三项资产选择与四类候选规划预算；未创建尚缺精确字段定义的 Profile/ArchitectureDraft 资产。
- 增加能力状态枚举拒绝、修复聚合和规划策略拒绝测试。完整门禁：175 passed；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。
- 本会话不修改主设计文档、不提交；M1-4c 与 M1-5 的既有负责人决策门保持不变。

### 2026-07-28（会话 8）
- 负责人要求在完整 Plan Compiler 前增加廉价 ArchitecturePlanner/ARCH_VALIDATE bring-up；设计升至 v0.5.2，规定 gold spec 上 N=20 独立调用、关闭跨 trial 缓存，逐项报告 Schema/ARCH_VALIDATE 首次通过率、一次修复提升、失败共现、成本/延迟/截断。
- 原超重 M1-4 拆为 M1-4a 规划输入与架构 spike、M1-4b 确定性编译资产、M1-4c 完整 S4 控制器；M1-4b 可并行，M1-4c 必须等待 spike 报告及负责人 prompt/预算冻结决策。
- 记录 DEC-1 的 provider 能力边界：T1 当前绑定 deepseek-reasoner，但 temperature 是否生效在 capability probe 前视为 `unknown`；trace 分开记录请求值与 `reported_applied/reported_ignored/unknown`，复现依靠独立重复统计。
- 负责人批准把结合 Codex/Claude Code 公开工程经验形成的 S4 方案写入设计，并明确本次只修改两份文档、实际资产暂不迁移。
- `system_design.md` 升至 v0.5.0：S4 从一次性 Planner 改为 architecture → work-package expansion → deterministic link/full lint → independent critic → atomic seal；生产默认 layered，flat 只作显式消融。
- Plan 设计升至 v3：不可变 `plan.json` 与 S6 admission 初始化的 `plan_state.json` 分离；S5 独占 scaffold，并以共享 Delivery Compiler/blueprint hash 约束工件、契约和文件所有权。
- Test Manifest 目标设计升至 v2，新增 `gate`、`required_contracts` 与可选 `build_variant_ids`；同步修订 S5～S9、恢复协议、指标、M1 DoD、风险/O-16 和修订历史。
- 终审补齐实现闭环：Blueprint hash 移出编译输入以消除自引用；需求责任进入正式工作包/任务字段；S4/S5 用 `run.json.output_refs` 独立封存；Test Bundle 使用 manifest/tree 双摘要；Plan State 拆为 snapshot/transition/execution 校验并明确 resume reconciliation；S9 支持工件缺失的条件化部分报告。
- 负责人批准终审发现的六项契约修订：M1 正常以 `--until s6` planned stop；S5 不承担 LLM 结构化失败注入；round 以 pending WAL 原子发布；S8 每轮只处理一个簇且快验失败回滚不提交；M5 先做 M5-prep/M5-0 scale qualification；Report 单列 pass/fail/error/skipped 与 disabled/not_run。
- v0.5.1 修复 7.3 边界：通用 Coder/Diagnoser/Fixer prompt 不得内嵌 `mqtt_*`，实例标识符只由冻结 Spec/Profile/Plan/contract/interface 上下文注入；当前实际通用 prompt 源码只读扫描为 0 命中。
- 撤销旧 session/net 固定签名并新增 active/blocking 的 O-18：broker 输入必须可寻址连接、输出必须支持有界多目标 batch，K×消息容量及满载行为必须在 M1-5 模板冻结前裁决。
- 现有 Plan v2 Schema、Planner prompt、plan_lint、Test Manifest v1/collector/gold 与 summary/report 等实现资产均未修改；12.3、M1/M2 进度与 M5\-0 明确记录迁移缺口。

### 2026-07-28（会话 7）
- 复核 Gold 需求证据，仅修补 `source_ref.section/quote`：从仓库内 OASIS MQTT 3.1.1 PDF 实际检索并摘录；跨条款引文用 `[…]` 明示省略，归一化逐段比对结果为 0 个缺失片段。
- 修复三项实现问题并增加回归测试：结构化调用最终失败仍执行预算回调；任务提交前拒绝任何 `deliverable_files` 白名单外变更且仅暂存白名单；`spec_lint` 拒绝 `repeat.min_items > max_items`。
- 完整门禁：`173 passed`；ruff、mypy、gold lint（0 error / 0 warning）与 `git diff --check` 全绿。
- 使用固定镜像 digest、`--network=none` 和宿主 UID 999 的非 root 容器，按种子 311000～311019 重跑 D0.2；20/20 轮退出码均为 0，每轮 19 passed / 1 skipped，摘要与 20 份日志已更新。
- 本会话未修改设计文档。

### 2026-07-28（会话 6）
- 负责人确认执行 R6，并要求改动最小化。
- Plan Schema 升至 v2.0：用四项 `input_refs` 替换单一 `spec_ref`；每项仅含冻结资产路径与 SHA-256。
- `plan_lint` 增加可选的冻结引用逐项比对，S4 可传完整闭包，CLI 对已提供的 Spec 文件执行实际哈希校验；S5/S6 在副作用前拒绝错位 Plan。
- 未新增 capability、推理摘要、Profile/Test Bundle 内容副本，也未扩展 Spec IR。
- 验证：全量测试 `167 passed`；ruff、mypy、JSON 与 `git diff --check` 全绿。

### 2026-07-27（会话 5）
- 负责人要求按“未来由智能体从文档提取、推理留给后续智能体”的原则重做 R4，并在继续 R6 前先详细说明。
- Spec IR 升至 v3.0：只保留协议/角色、可选传输、类型、报文和带 `source_ref` 的原子需求；scope/运行元数据留在各自工件，状态机/行为/定时器/错误对象、`observable_check`、`category` 和 `covered_by` 均移出。
- 用 `sequence` 与 `repeat` 直接表达 MQTT 的两个复合列表，删除自然语言 `encoding.item`；报文方向改为角色引用，类型码只保留在线上字段 `constraint.const`。
- 同步迁移 gold、Schema 示例、`spec_lint`、切片器、plan 的 requirement 上下文引用和相关单测；R6 未修改。

### 2026-07-27（会话 4）
- 负责人确认 R1/R2/R3/R5/R7/R8/R9 的四项冲突裁决；设计文档以最小改动升至 v0.4.0。
- 明确四类运行输入、S5 解析后交付契约、A7 独立变异判定及 M4 → M6a → M5 → M6b 顺序；未修改 R4/R6、现有 Schema/gold 资产或 7.4 冻结契约。

### 2026-07-27（会话 3）
- 负责人进一步授权更新设计文档 12.4；已追加 M0 冻结与迁移修订记录，不提升设计版本、不改动 M1+ 设计。
- Docker 权限与 Mosquitto broker 就绪后完成 M0-7/M0-8：
  - 构建并自验 `nepa-sandbox`，固定 digest `sha256:3795cf4e272e1353b0437779511b3433a950e93a4bee3e7a1a2c094992d1de37`，登记 Dockerfile 哈希。
  - 校准 gold 参考适配：以非法 SUBSCRIBE 固定头覆盖协议违规断连，并区分 Mosquitto 粗粒度 keep-alive 调度容差与生成目标容差；补齐 Paho 1.x/2.x 兼容。
  - 在固定 digest、`--network=none`、非 root UID 的沙箱中以种子 311000～311019 连续验证 20 轮，全部为 19 passed / 1 skipped，日志及环境摘要归档。
- M0 最终门禁：全量测试 `175 passed`；ruff、mypy、gold lint、`git diff --check` 全绿；D0.1～D0.6 全部满足，按要求停止于 M1 入口前。
- 负责人确认 7.1 当前 M0 子集原样冻结，并授权更新设计文档 7.1 冻结记录与 12.3 迁移归档记录；M0-2/M0-3/M0-4 据此完成。12.4 当时暂缓，后于本会话获得单独授权并完成登记。
- 按目标提交 `e78f523` 复核实际进度后继续 M0，明确不进入 M1、不修改未获授权的设计文档。
- 收口 M0-1/M0-5：
  - 修复 `run_store` 阶段键与 `run.schema` 不一致、可选字段错误序列化为 null、同分钟 run_id 冲突格式等跨模块问题，并增加真实工件校验测试。
  - 补齐 `nepa lint spec/plan` CLI；`spec_lint` 新增 MQTT `packet_type_code` 必填/唯一和字段 `loc`→`wire_layout` 引用检查。
- 完成 M0-2：旧三文件 schema/gold 草案完整移入 `legacy/`，新增独立字段迁移映射，并在负责人授权后同步设计文档 12.3。
- 推进 M0-4/M0-6：
  - 新建 MQTT 3.1.1 最小子集 Spec IR v2.0，覆盖 10 种报文、client/broker 状态机、行为、定时器、错误和 18 条 MUST 需求；gold 模式 `spec_lint` 0 error。
  - 新建 spec 驱动 harness、L0/L1/L2、Mosquitto 参考 client/broker 适配、随机化 fixture、manifest 收集器和 20 轮参考审计脚本；manifest 当前 22 个用例，L1 参考自验 7 passed。
- 质量门禁：NePA 自身 `171 passed, 4 skipped`；ruff、mypy 全绿。4 个 skip 为 DeepSeek/Docker 外部集成测试。
- 外部阻塞复核：`ljf` 仍无 Docker daemon 权限；仅有 `mosquitto_pub/sub`，broker 未安装；D0.2 脚本已实际运行并明确因 broker 缺失停止。

### 2026-07-27（会话 2）
- 两个评审工作流返回并全部落实：
  - **一致性校验**：56 条发现、54 条经对抗验证确认，全部修入设计文档（新增 5.6 工件结构小节与 tests_manifest 工件；统一 T1 升级时机为 3+1、受控出口语义、任务状态枚举；修复 D0.2/D1.1/D1.3/M3 入口条件等 DoD 可执行性问题；新增 SegmentClassifier 角色、scope 配置结构、7.3 varint 映射与当时的 session/net 固定接口；该接口后由 v0.5.1/O-18 撤销）。
  - **长期目标对照评审**：六视角 29 条差距、27 条确认。结论：架构方向与长期目标对齐（Spec IR 枢纽 + 协议无关流水线 + 语言约定隔离于第 7 章），确认缺口以 O-9～O-17 记入 11.2（多文档输入、图形摄取、ASCII/ABNF 保护、代理形态、TLS 路径、**无 gold 协议的测试合成路线（O-15，长期关键）**、分层规划、语言参数化）；Spec IR 的 4 处 MQTT 硬编码在 schema 冻结前完成协议无关化修正。
- 设计文档升版 **v0.3.1**。
- 工程实施启动：uv 依赖装好、包骨架建立、configs/default.yaml（DeepSeek 三档位，DS_API）与 scope-mqtt-min.yaml 写入。
- 启动 **M0 基础工作流**（9 个智能体，四阶段）：A1 schemas → A5 speclib → A6 gold 规格 → A7 gold 测试，A2 LLM 层 / A3 config+run_store / A4 sandbox 并行，最后 A8 对抗审查 + A9 修复。结果待回收。

### 2026-07-26（会话 1）
- 设计文档补全第 9～12 章，版本升至 v0.3.0；启动全文一致性校验与长期目标对照评审两个多智能体工作流（结果待合并）。
- 环境核查：发现 docker 权限与 mosquitto 缺失两个阻塞，已与用户确认处置方案（见 DEC-2/DEC-3）。
- 开始 M0 实施：建立工程骨架（pyproject、包结构、configs），启动 M0 基础工作流（schemas、LLM 层、config/run_store、sandbox、speclib、gold 规格、gold 测试、审查修复）。
