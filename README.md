# NePA

根据人工整理的 Spec（包括需求）、Target 格式和独立验收资产生成协议项目。初始范围为
Linux x86_64/C99 服务器，首个评估输入为 MQTT；MQTT 不是生成器的特殊分支。

已批准的契约见 `project_docs/system_design.md`，实际实现和验收状态见
`project_docs/engineering/重构计划.md`。

截至 2026-09-12 已验证：先完成一次真实开发基线运行，再完成两次全新的优化候选运行，
均通过既定构建和最小交互检查。两次重复运行耗时分别为 76.8/75.4 分钟；基线耗时为
128.8 分钟，其中不计入充值暂停时间。这不代表完整 MQTT 一致性，也不代表三次未改变
候选版本的运行结果。详细证据和限制见 `project_docs/engineering/重构计划.md` 以及
`project_docs/experiments/会话延迟分析.md`。

## 开发

```bash
uv sync --extra dev
docker build -t nepa-sandbox:refactor -f docker/sandbox.Dockerfile .
uv run pytest -q -m "not live_e2e"
uv run ruff check nepa tests
uv run mypy nepa
```

## 生成：新版 CLI 契约

设置供应商配置指定名称的 API 密钥环境变量；不要将凭据写入已提交的配置或生成的项目。

```bash
uv run nepa run --spec gold_file/mqtt/specIR.json --target gold_file/mqtt/target.json \
  --acceptance gold_file/mqtt/acceptance.json --config configs/default.yaml --runs-root runs/mqtt-e2e
uv run nepa status RUN_ID --runs-root runs/mqtt-e2e
uv run nepa resume RUN_ID --runs-root runs/mqtt-e2e
```

随附配置为初始普通编码会话使用 V4.1 Flash（`deepseek-flash`），为线协议/集成以及
重试/修复会话使用 V4 Pro。成本采用国内 CNY 费率以及 Asia/Shanghai 高峰/低谷时段。
如果响应提供了缓存使用量，系统会记录该数据；缺失的缓存统计按未命中计算。
未知调用保留高峰价格预留值。这些都是估算值，不是供应商账单。
全部预算字段均可在配置的 `budgets` 中独立设置；代码中的 20/300/4/40/3/3/3
只是未提供相应字段时的缺省值，不是最大值。每次运行会冻结解析后的完整配置，
后续修改磁盘上的 YAML 不会追溯改变该运行。
详见 `configs/default.yaml` 和 `project_docs/experiments/协议扩展实验.md`。

经过明确批准的开发续作可以修改当前生效配置：

```bash
uv run nepa resume RUN_ID --runs-root runs/mqtt-e2e --config configs/default.yaml \
  --accept-runtime-change --change-reason "描述已授权的实验变更"
```

这会保留既有状态/报告证据、成本、尝试记录和原始截止时间。普通续作会拒绝运行时漂移。
混合版本的开发运行不得作为未改变候选版本的稳定性样本展示。

成功导出的项目包含源代码、Makefile、README 以及 release/san 可执行文件。
不使用 NePA 构建时，执行 `make clean`，然后执行 `make release san`。返回码为零要求
所有任务、必需检查和已发布产物均成功。文档中的声明不等于已验证的行为。

人工输入集彼此并行：`gold_file/mqtt/` 和 `gold_file/http/`，每个输入集都包含
`specIR.json`、`target.json`、`acceptance.json` 以及独立的预言脚本。
MQTT 保留 110 条需求，目前有 20 个核心行为检查。HTTP 包含 27 条人工整理的定长子集
需求和 12 个检查。两者使用相同的 C99/服务器 Target。这些检查不能证明完整协议一致性。
Report4.0 会将每项声明关联到最终导出项目的实际场景结果或明确缺口。
Config2.0 通过 `coder.action_format: json_object` 或 `tool_calls` 选择动作格式；两种
模式下本地动作校验都保持严格。旧运行必须使用其原始运行时。

```bash
uv run nepa run --spec gold_file/http/specIR.json --target gold_file/http/target.json \
  --acceptance gold_file/http/acceptance.json --config configs/default.yaml --runs-root runs/http-e2e
NEPA_LIVE_E2E=1 uv run pytest -s -q -m live_e2e tests/test_live_e2e.py
```

选择性启用的付费测试工具会在冻结两套输入集和同一候选版本后，并发执行全新的 MQTT
和 HTTP 生成，再分别检查每个导出结果。MQTT 使用新的 `runs/mqtt-e2e` CNY 计费活动；
HTTP 使用 `runs/http-e2e`。每次生成及其计费活动的成本、时长、会话、决策和修复预算
均来自启动时解析并冻结的配置；随附 `configs/default.yaml` 当前设置为每次 ¥100、
每个计费活动 ¥1500 和四小时。用户明确将旧 USD 运行排除在新 CNY 账本之外；
旧证据仍保留在原历史根目录。
Run6.0 和 Config2.0 会拒绝混用货币。证据和剩余工作见
`project_docs/experiments/协议扩展实验.md`。
