# P0–P2 执行记录

2026-09-13，用户授权自主执行 P0–P2、记录节点并本地提交；不使用 OpenSpec，不触及 P3–P5 实现。

- 前阶段在 `de5fb5e` 收口：MQTT20／HTTP12 场景双变体通过，172 非付费测试、Ruff、mypy、打包通过。
  原始案例与所有费用、未知预留和交付保持不变。
- GPT-6 Astra Ultra 形成完整计划与需求矩阵，并复核简化：保留 Acceptance1；公开工具四会话；
  私有服务与 oracle 分容器文件系统；安全反馈采用字段投影；不新增 public-check CLI。
  第一次规划回执因上下文截断不可追踪，恢复的 Ultra 审查任务 `01a096cc-0dac-7613-a6d4-c50a5ca40954`
  已完成，未重复修改实现。
- P0 三份只读指标已记录于 `runs/p0-baseline-inventory/`，概览见 `p0-serial-baseline.md`。
  未测量的历史开销明确缺失；旧 USD 金额不改标 CNY。
- 两个精确 Qwen 快照及北京价格已通过官方页面与当前账户模型列表核实。
  证据见 `qwen-capability-audit.md`、`runs/qwen-p0-p2-discovery/models.json`。
  尚未付费探测，不把模型列表可见等同于动作会话通过。
- Design11 已根据授权先行更新，采用独立 Qwen 累计 ¥300、单次 ¥20／4 小时，
  探测和公共会话阶段各暂设 ¥5 子限额；全部复用活动锁和预留。

当前门槛：G1 设计提交；随后 Astra high 分工实现私有存储／执行、供应商能力／计费、
会话闭环和验收资产。付费完整生成须等待离线闭环与小额真实合同验证。

G1 已提交 `351ff20`。Astra high 实施分工（不共享写入文件）：

| 实施任务 | agent ID | 负责边界 |
|---|---|---|
| 私有执行／存储／报告 | 01a096cf-f762-70a1-93f1-7819be30318e | tools、RunStore、Report5、对应 schema／tests |
| 供应商能力／价格 | 01a096cf-f80b-7e70-bc5f-307f445cbec8 | Config3、provider/client、usage、tier价格及对应 tests |
| 会话与调用方 | 01a096d0-757f-70a0-943c-d38479a254d8 | context/session/prompt、orchestrator/application/CLI |
| 人工验收随机化 | 01a096d0-75fa-7ef2-9162-7ad8ddba9737 | MQTT／HTTP oracle、字节轨迹、语义诊断和迁移映射 |
| 实验与公共夹具 | 01a096d1-0056-7b13-be26-4ce7d09bd256 | P0 汇总驱动、四个公开会话、P2 分级实验驱动 |

跨边界约定：`store.private_checks` 只给主机验证器；`publish_agent_evidence` 只发布已投影的安全视图；
`safe_feedback` 统一处理验证结果；Context 使用选定 provider 的实际 wire payload 计量。
实验实施任务均未获准在离线门槛前自行启动付费调用，由主任务审核后执行。

实施审查进展（尚未合并为整体通过）：

- 人工 oracle 已交付：58 项相关测试通过；迁移表见 `private-oracle-migration.md`。
  Acceptance1、Req 数组及 Spec 字节未变，实际双容器全套验证仍由核心测试承担。
- Provider／计费已交付：146 项相关测试通过，后续删除重复且未生效的
  `campaign.runs_root` 配置，保留 `--runs-root` 作为账本根入口，补充测试 83 项通过。
  Qwen 阶梯 K 使用十进制 1,000；当前 profile 以 native tool_calls 作为待探测候选，
  DeepSeek 默认 JSON 不变。不能据离线测试宣布真实动作会话通过。
- Session／调用方已交付：50 项测试通过，含真实编译失败升级、CLI 导出、幂等恢复。
  命令失败保守升级至强模型，同一会话和决策预算；明确单模型 fixture 不会换成 Plus。
- 主审补充了服务输出伪装源码位置、checker 通过但服务失败的报告状态、日志上限、
  提前 exit0、已完成研究状态及公开归档完整性检查。核心／实验任务正在收口。
- 三份原始运行的全部文件摘要已冻结至 `runs/p0-baseline-inventory/original-integrity-before.json`，
  最终收口再次比较。历史输入、Report4 和两份交付压缩包摘要也已重新核对。

完成门槛仍为：最终候选 MQTT 三次全新成功、同候选 HTTP 一次全新成功，均私有独立导出复验。
任何失败、预算耗尽或证据缺口都不改称完成。

整体离线验证：382 项非付费测试全部通过，无跳过，340.859 秒；包含真实 Docker 控制。证据 runs/p0-p2-offline-results.xml。Ruff、mypy（30模块）、uv sdist/wheel打包通过，wheel中Run7/Report5/Acceptance1示例有效且无私有资产。中断后确认测试进程已结束，以落盘JUnit结果为准。Qwen真实阶段尚未启动。
