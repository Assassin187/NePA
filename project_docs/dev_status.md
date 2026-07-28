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
| DeepSeek API | ⏳ 当前会话未设置 `DS_API` | M0 不依赖；进入需真实 LLM 调用的后续里程碑前恢复 |

## 里程碑总览

| 里程碑 | 状态 | 说明 |
| --- | --- | --- |
| 文档 v0.4.0 | ✅ 完成 | R1～R9 边界修订完成；R4 采用 Spec IR v3.0 最小事实层，R6 采用 Plan v2.0 最小输入引用闭包 |
| M0 gold 资产与校验工具 | ✅ 完成 | D0.1～D0.6 全部通过；2026-07-28 修补 Gold 证据后重新完成 D0.2 |
| M1 spec-run 到可构建 | 🔨 进行中 | 基础 Agent/LLM/工具契约已开始实现；S4～S6 端到端闭环尚未完成 |
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
| M0-5 | spec_lint / plan_lint + 单测 | ✅ | `nepa lint spec/plan` 可用；增加 `repeat.min_items <= max_items` 关系校验 |
| M0-6 | gold 测试集（harness + L0/L1/L2） | ✅ | 22 个 manifest 用例；常量从 spec 读取、输入按 seed 随机化；参考模式每轮 19 passed、1 个 workspace 专用用例按设计跳过 |
| M0-7 | 参考实现验证（100% + 20 轮） | ✅ | 2026-07-28 以当前 Spec/Test Bundle 在固定沙箱重跑：20/20 轮均为 19 passed / 1 skipped |
| M0-8 | 沙箱镜像 Dockerfile | ✅ | 镜像构建及无网络/非 root 自验通过；digest 与 Dockerfile 哈希登记于 `docker/sandbox-image.json` |
| 基础设施 | pyproject / 包骨架 / LLM 层 / config / run_store | 🔨 | 提前自 M1（不依赖沙箱，先行实现） |

### M1 工作项进度

| 工作项 | 状态 | 当前进展 |
| --- | --- | --- |
| M1-1 运行框架 | 🔨 | config/run_store 已有；orchestrator、阶段状态机、预算上限控制与 resume 尚待闭环 |
| M1-2 LLM 层 | 🔨 | provider、结构化输出修复、缓存、trace 与 client factory 已有；失败结构化调用现纳入预算回调 |
| M1-3 Agent 框架 | 🔨 | 角色注册、无状态调用器、输出契约和四类提示词骨架已实现并有回归测试 |
| M1-4 四类资产与 S4 | 🔨 | Plan v2.0 输入引用闭包和 plan_lint 已实现；Profile/Test Bundle 解析冻结与 Planner 尚待完成 |
| M1-5 S5 | 🔨 | build/git/fs/event 基础工具已起步；完整脚手架、工件清单/契约映射和首提交尚待完成 |
| M1-6 S6 | 🔨 | 文件写入与提交均校验任务白名单；单任务循环、上下文包和升级路径尚待完成 |
| M1-7 CLI | ⬜ | `run --spec`、`resume`、`status` 尚未实现 |
| M1-8 单测与 CI | 🔨 | 当前全量 173 passed，ruff/mypy/gold lint 全绿；CI 集成仍待完成 |

## M0 DoD 验收记录

| DoD | 状态 | 证据 |
| --- | --- | --- |
| D0.1 | ✅ | gold 模式 `nepa lint spec`：0 error、0 warning |
| D0.2 | ✅ | 2026-07-28 固定 digest、无网络、非 root 沙箱内 20/20 轮全绿；摘要 `passed: true`，当前 Spec SHA-256 为 `92cf26af04f125050e28c0f265c1ba6af950801401dd8579298df7368acd839a` |
| D0.3 | ✅ | 每条 MUST/MUST NOT 均由 Test Bundle manifest 的 `req_ids` 覆盖，gold lint 与 manifest 漂移测试验证 |
| D0.4 | ✅ | 10 份活动 schema 与最小示例互校通过；当前全量测试 173 passed |
| D0.5 | ✅ | 项目负责人于 2026-07-27 签字确认 7.1 冻结范围 |
| D0.6 | ✅ | 项目负责人授权旧草案归档及 12.3 迁移记录，归档与映射均已完成 |

## 决策记录

| id | 日期 | 决策 | 依据/影响 |
| --- | --- | --- | --- |
| DEC-1 | 2026-07-26 | 测试期所有档位（T1/T2/T3）绑定 DeepSeek：T1=deepseek-reasoner，T2/T3=deepseek-chat，密钥走环境变量 `DS_API` | 用户指示。偏离设计文档 4.6 规则 3"评审角色应当不同 provider"——单 provider 测试配置下 SpecCritic 暂与 SpecExtractor 同厂不同型号，正式实验前需补第二 provider |
| DEC-2 | 2026-07-26 | Docker 权限开通前，只实现不依赖沙箱执行的模块；沙箱严格按 8.5 实现 docker 后端，不做宿主机执行后备 | 用户选择"开通 docker 权限"方案，无偏离 |
| DEC-3 | 2026-07-26 | D0.2 参考实现验证推迟到 mosquitto 安装后补跑，测试先行编写 | 用户确认；风险 V-3 在此期间未闭合，记录在案 |
| DEC-4 | 2026-07-26 | NePA 仓库提交纪律：阶段性成果由用户确认后提交（未经用户要求不自动 commit/push） | 遵循协作约定 |
| DEC-5 | 2026-07-27 | 负责人确认四项既有约定调整：M1 引入 Target/Language Profile 与 Test Bundle 边界；M6a 前置于 M5；A7 改用独立合成 oracle；Spec IR 收窄为协议事实唯一来源 | 对应设计 v0.4.0；保持 R4/R6、Spec/Plan Schema、gold 数据与 7.4 契约不变 |
| DEC-6 | 2026-07-27 | R4 采用“可直接提取的最小事实层”：Spec IR v3.0 不保存状态机、行为拆解、测试步骤或反向覆盖；复合线格式只增加 `sequence/repeat` | 已同步设计、Schema、gold、lint、切片与 plan requirement 引用；R6 仍待负责人确认 |
| DEC-7 | 2026-07-28 | R6 采用最小输入引用闭包：Plan v2.0 仅以 `{path, sha256}` 绑定 Spec、Target Profile、Language Profile、Test Bundle | 由 S4 控制器确定性注入并由 `plan_lint` 比对；不增加 capability、推理摘要或输入内容副本 |

## 待办（需用户/负责人动作）

- [ ] 按 DEC-4 审阅并确认当前 M1 基础实现与本批修补后再提交

## 会话日志

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
  - **一致性校验**：56 条发现、54 条经对抗验证确认，全部修入设计文档（新增 5.6 工件结构小节与 tests_manifest 工件；统一 T1 升级时机为 3+1、受控出口语义、任务状态枚举；修复 D0.2/D1.1/D1.3/M3 入口条件等 DoD 可执行性问题；新增 SegmentClassifier 角色、scope 配置结构、7.3 varint 映射与 session/net 固定接口）。
  - **长期目标对照评审**：六视角 29 条差距、27 条确认。结论：架构方向与长期目标对齐（Spec IR 枢纽 + 协议无关流水线 + 语言约定隔离于第 7 章），确认缺口以 O-9～O-17 记入 11.2（多文档输入、图形摄取、ASCII/ABNF 保护、代理形态、TLS 路径、**无 gold 协议的测试合成路线（O-15，长期关键）**、分层规划、语言参数化）；Spec IR 的 4 处 MQTT 硬编码在 schema 冻结前完成协议无关化修正。
- 设计文档升版 **v0.3.1**。
- 工程实施启动：uv 依赖装好、包骨架建立、configs/default.yaml（DeepSeek 三档位，DS_API）与 scope-mqtt-min.yaml 写入。
- 启动 **M0 基础工作流**（9 个智能体，四阶段）：A1 schemas → A5 speclib → A6 gold 规格 → A7 gold 测试，A2 LLM 层 / A3 config+run_store / A4 sandbox 并行，最后 A8 对抗审查 + A9 修复。结果待回收。

### 2026-07-26（会话 1）
- 设计文档补全第 9～12 章，版本升至 v0.3.0；启动全文一致性校验与长期目标对照评审两个多智能体工作流（结果待合并）。
- 环境核查：发现 docker 权限与 mosquitto 缺失两个阻塞，已与用户确认处置方案（见 DEC-2/DEC-3）。
- 开始 M0 实施：建立工程骨架（pyproject、包结构、configs），启动 M0 基础工作流（schemas、LLM 层、config/run_store、sandbox、speclib、gold 规格、gold 测试、审查修复）。
