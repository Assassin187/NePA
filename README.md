# NePA

NePA 当前包含两条相连但边界明确的流水线：RFC→Spec 从冻结文本快照生成可审阅、批准并严格投影的
Spec；Spec→Code 根据批准后的 Spec v3、目标格式和独立 Acceptance 资产生成协议工程。代码生成当前
范围为 Linux x86_64、C99 服务端。MQTT 是首个评估输入，不是生成器中的协议特例。

文档总览见 [project_docs/README.md](project_docs/README.md)，权威合同见
[系统设计](project_docs/system_design.md)，实际实施与验收状态见
[P0–P2 执行记录](project_docs/p0-p2-progress.md) 和
[完成审计](project_docs/p0-p2-completion-audit.md)。

两条流水线当前都尚未通过各自的真实验收门槛。Spec→Code 最新 P0–P2 candidate-12 在首个 MQTT
样本中断；RFC→Spec Phase 1 的字段及需求精确率／召回率门禁失败。离线测试、静态检查和打包通过
不能替代这些验收结论。RFC→Spec 的最小失败摘要见
[`experiments/rfc-spec-ir/phase1-failed/`](experiments/rfc-spec-ir/phase1-failed/README.md)。

2026-09-12 的历史验证包括一个真实开发基线，以及同一优化候选的两个新运行；它们通过规定构建和最小
交互。两个新运行耗时 76.8／75.4 分钟，基线排除充值暂停后为 128.8 分钟。这不是完整 MQTT 符合性，
也不是三次相同候选。详见 [重构执行记录](project_docs/refactor_plan.md) 和
[会话延迟分析](project_docs/research/session_latency_analysis.md)。

2026-09-13，MQTT 与 HTTP 固定长度子集各完成一次全新真实生成，在 release 和 san 两个变体中通过
扩展必过场景及独立导出复验。MQTT 保留 110 条需求，其中 51 条有场景映射、59 条明确为缺口。动作接口
研究没有支持把默认值从 JSON 切换为原生工具调用。费用、归档和限制见
[协议扩展结果](project_docs/protocol_expansion.md)。

## 开发与验证

```bash
uv sync --extra dev
docker build -t nepa-sandbox:refactor -f docker/sandbox.Dockerfile .
uv run pytest -q -m "not live_e2e"
uv run ruff check nepa tests
uv run mypy nepa
```

## 生成命令

通过供应商配置指定 API key 环境变量，不要把凭据写入已提交配置或生成工程。

```bash
uv run nepa run --spec gold_file/mqtt/specIR.json --target gold_file/mqtt/target.json \
  --acceptance gold_file/mqtt/acceptance.json --config configs/default.yaml --runs-root runs/mqtt-e2e
uv run nepa status RUN_ID --runs-root runs/mqtt-e2e
uv run nepa resume RUN_ID --runs-root runs/mqtt-e2e
```

默认配置对普通 bootstrap／message／requirements 首会话使用 V4.1 Flash（`deepseek-flash`），对 wire、
integration 和 retry／repair 使用 V4 Pro。费用按国内人民币和上海时区忙／闲时估算；供应商提供缓存
usage 时照实记录，缺失时按未命中估算，未知调用保留忙时预留。这些是估算，不是供应商账单。

显式批准的开发继续运行可以修改活动配置：

```bash
uv run nepa resume RUN_ID --runs-root runs/mqtt-e2e --config configs/default.yaml \
  --accept-runtime-change --change-reason "说明已授权的实验变化"
```

该流程保留旧状态／报告证据、费用、尝试次数和原始截止时间。普通恢复拒绝运行时漂移；混合版本开发运行
不能描述为相同候选稳定性样本。

成功导出包含源码、Makefile、README 和 release／san 可执行文件。无需 NePA 即可执行
`make clean && make release san`。只有全部任务、必过检查和发布产物成功时 CLI 才返回 0；模型声明
不等于行为已验证。

人工输入并列存放在 `gold_file/mqtt/` 与 `gold_file/http/`，每套包含 `specIR.json`、`target.json`、
`acceptance.json` 和独立 oracle。MQTT 有 110 条需求和 20 个核心行为检查；HTTP 固定长度子集有 27 条
需求和 12 个检查；两者使用同一 C99/server Target。这些检查不证明完整协议符合性。

当前合同版本为 Spec 3.0、Target 1.0、Acceptance 1.0、Plan 6.0、AgentAction 1.0、Config 3.0、Run 7.0
和 Report 5.0。Acceptance 及其脚本存放在宿主私有目录，编码智能体不可见；Report 5 将模型声明、公共
开发结果、私有最终场景和未覆盖缺口分开。动作格式可选 `json_object` 或 `tool_calls`，两者都使用严格
本地动作校验。旧运行必须使用其原运行时。

HTTP 示例：

```bash
uv run nepa run --spec gold_file/http/specIR.json --target gold_file/http/target.json \
  --acceptance gold_file/http/acceptance.json --config configs/default.yaml --runs-root runs/http-e2e
```

付费实验必须使用明确的实验驱动、冻结候选和独立活动根，并把失败、重试和未知预留计入预算。普通开发
或测试命令不会自动启动付费实验。
