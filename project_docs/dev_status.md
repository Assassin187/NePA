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
| 文档 v0.3.1 | ✅ 完成 | 第 0～12 章齐备；一致性校验与长期目标评审结论已合并 |
| M0 gold 资产与校验工具 | ✅ 完成 | D0.1～D0.6 全部通过；2026-07-27 停止于 M1 入口前 |
| M1 spec-run 到可构建 | ⬜ 未开始 | |
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
| M0-4 | gold 规格 spec.json（v2.0） | ✅ | 冻结范围已写入 spec；18 条 MUST、10 种报文；gold lint 0 error |
| M0-5 | spec_lint / plan_lint + 单测 | ✅ | `nepa lint spec/plan` 可用；新增 MQTT type code、wire_layout 引用检查；ruff/mypy/pytest 全绿 |
| M0-6 | gold 测试集（harness + L0/L1/L2） | ✅ | 22 个 manifest 用例；常量从 spec 读取、输入按 seed 随机化；参考模式每轮 19 passed、1 个 workspace 专用用例按设计跳过 |
| M0-7 | 参考实现验证（100% + 20 轮） | ✅ | 固定沙箱中 Mosquitto 2.0.11 + Paho 1.6.1 连续 20 轮全绿；种子 311000～311019，逐轮日志与摘要已归档 |
| M0-8 | 沙箱镜像 Dockerfile | ✅ | 镜像构建及无网络/非 root 自验通过；digest 与 Dockerfile 哈希登记于 `docker/sandbox-image.json` |
| 基础设施 | pyproject / 包骨架 / LLM 层 / config / run_store | 🔨 | 提前自 M1（不依赖沙箱，先行实现） |

## M0 DoD 验收记录

| DoD | 状态 | 证据 |
| --- | --- | --- |
| D0.1 | ✅ | gold 模式 `nepa lint spec`：0 error、0 warning |
| D0.2 | ✅ | 固定 digest 沙箱内 20/20 轮全绿；`golds/mqtt-3.1.1-min/validation/reference/summary.json` 为 `passed: true`，20 份日志齐全 |
| D0.3 | ✅ | 18 条 MUST 的 `covered_by.tests` 均非空、nodeid 存在，由 gold lint + manifest 漂移测试验证 |
| D0.4 | ✅ | 10 份活动 schema 与最小示例互校通过；全量测试 175 passed |
| D0.5 | ✅ | 项目负责人于 2026-07-27 签字确认 7.1 冻结范围 |
| D0.6 | ✅ | 项目负责人授权旧草案归档及 12.3 迁移记录，归档与映射均已完成 |

## 决策记录

| id | 日期 | 决策 | 依据/影响 |
| --- | --- | --- | --- |
| DEC-1 | 2026-07-26 | 测试期所有档位（T1/T2/T3）绑定 DeepSeek：T1=deepseek-reasoner，T2/T3=deepseek-chat，密钥走环境变量 `DS_API` | 用户指示。偏离设计文档 4.6 规则 3"评审角色应当不同 provider"——单 provider 测试配置下 SpecCritic 暂与 SpecExtractor 同厂不同型号，正式实验前需补第二 provider |
| DEC-2 | 2026-07-26 | Docker 权限开通前，只实现不依赖沙箱执行的模块；沙箱严格按 8.5 实现 docker 后端，不做宿主机执行后备 | 用户选择"开通 docker 权限"方案，无偏离 |
| DEC-3 | 2026-07-26 | D0.2 参考实现验证推迟到 mosquitto 安装后补跑，测试先行编写 | 用户确认；风险 V-3 在此期间未闭合，记录在案 |
| DEC-4 | 2026-07-26 | NePA 仓库提交纪律：阶段性成果由用户确认后提交（未经用户要求不自动 commit/push） | 遵循协作约定 |

## 待办（需用户/负责人动作）

- [ ] 按 DEC-4 审阅并确认本次 M0 变更后再提交；在此之前不进入 M1

## 会话日志

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
