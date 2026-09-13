# P0–P2 完成审计（进行中）

本表对照 `nepa-next-stage-requirements.md`，不把离线夹具、能力探测或历史成功当作全新协议生成。
最终候选与真实样本未全部通过前，整体状态为未完成。

| 要求 | 当前可核验证据 | 状态／剩余工作 |
|---|---|---|
| P0 三份历史基线、每任务／模型时间费用与失败分类 | `p0-serial-baseline.md`；`experiments/p0-p2/baseline-results/`；原始摘要 `runs/p0-baseline-inventory/` | 已固化；历史缺失计时与 USD→CNY 不可重建字段明确留缺口 |
| P0 不覆盖历史工件 | `experiments/p0-p2/original-integrity-after.json`，三个原始目录逐文件比对 | 当前一致，最终收口再比对 |
| ACC-001 私有输入和服务／检查器文件系统隔离 | `tests/test_private_isolation.py` 的快照与真实双容器测试；`tests/test_workspace_tools.py` | 离线通过 |
| ACC-002 所有文件／命令／符号链接／历史与导出访问边界 | 同上与 session/context/report 测试，源码路径仅接受存在的公开文件 | 离线通过 |
| ACC-003 有用的有限反馈 | 递归语义投影、编码回显排除测试；受控二进制 echo 修复夹具 | 已通过；candidate-6 的 67b7298d 以真实 Plus 调用修复，同种子／同断言双变体复验通过 |
| ACC-004 随机端口／ID／载荷／分片及可重放实际轨迹 | `tests/test_private_oracle_vectors.py` 32 场景；VerificationRunner seed 重放与实际 wire 测试 | 离线通过 |
| ACC-005 原始验收覆盖、私有摘要与公开报告分栏 | 原始 MQTT110／20 场景、HTTP27／12 场景合同摘要未变；Report5 与完整复制导出双变体测试 | 离线通过；最终真实报告待生成 |
| P1 正确与错误控制 | MQTT／HTTP 原交付复制品全套通过；错误响应、固定 ID、固定端口、休眠、提前 exit0、sanitizer 控制失败 | 382 项非付费测试 JUnit：0 失败、0 跳过 |
| MODEL-001/002 能力、wire、身份、usage、人民币预留 | `qwen-capability-audit.md`、Config3 与 provider 测试、真实探测请求／响应 | 精确模型与两种动作格式探测通过；不将历史失败样本补为通过 |
| MODEL-003 通用难度路由及失败升级 | session/orchestrator 测试与配置 | 离线通过；真实生成路由证据待收集 |
| MODEL-004 两模型各四个公开工具会话 | `runs/qwen-p0-p2-candidate-6/public-tool-results.json` | 八个真实会话全部通过，已有失败候选保持原记录 |
| MODEL-004 同最终候选三个空工程 MQTT 样本 | candidate-6／20260913T030005Z-ee4d7b51 | 第一轮运行中，未完成 |
| MODEL-004 同候选一个空工程 HTTP 样本 | 尚未执行 | 未完成 |
| 每个样本独立 clean release／san 与私有全套导出复验 | driver 的 `verify_generation`、公共归档审计 | 离线驱动测试通过，真实生成后逐次执行 |
| 非付费测试、Ruff、mypy、打包 | `runs/p0-p2-offline-results.xml`：382通过；mypy30模块；最终提示调整后38驱动测试通过；`runs/p0-p2-build-final.log` | 已通过，后续代码变化按影响复验 |

预算：前阶段 MQTT／HTTP 各新 ¥300，旧费用不占新上限；Qwen 后续活动使用独立 ¥300 账本，
单次生成 ¥20／4小时，能力探测与公开工具阶段各 ¥5 子限额，失败与未知预留均保留。
P3–P5 未实施，协议任务仍串行；不作完整 MQTT／HTTP 合规或范围外稳定性声明。
