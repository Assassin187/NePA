# Qwen 3.7 接入核实记录

记录时间：2026-09-13（Asia/Shanghai）。状态：官方资料与账户模型列表已核实；真实生成探测尚未执行。

使用精确快照 `qwen3.7-plus-2026-05-26` 和 `qwen3.7-flash-2026-07-15`。
北京时间 02:04 对已配置北京 endpoint 的只读 `GET /models` 返回 200，并同时列出两个 ID。
证据：`runs/qwen-p0-p2-discovery/models.json`，包含响应摘要，不包含凭据。

采用北京地域 `https://dashscope.aliyuncs.com/compatible-mode/v1`；官方说明旧域名仍可使用。
无需为此次实验获取或猜测业务空间 ID。[接口与地域说明](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)

| 精确模型 | 输入区间（token） | 未命中输入／命中输入／输出（CNY 每百万 token） |
|---|---|---|
| Plus 2026-05-26 | ≤256K | 2／0.4／8 |
| Plus 2026-05-26 | >256K，≤1M | 6／1.2／24 |
| Flash 2026-07-15 | ≤32K | 0.2／0.04／0.8 |
| Flash 2026-07-15 | >32K，≤256K | 0.6／0.12／2.4 |
| Flash 2026-07-15 | >256K，≤1M | 1.2／0.24／4.8 |

价格来自对应快照的北京价目，按每次实际输入总量选择档位，不能只给命中后的输入量选档。
不套用 DeepSeek 的闲忙折扣；这些 Qwen 公开价目没有此折扣。价格是估算依据，不是账户发票，
免费额度和临时促销不降低预算预留。[Plus 价格](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)、[Flash 价格](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)

两者快照页均声明 Function Calling、结构化输出和上下文缓存；上下文 1,000,000 token，
最大输入 991,808，思考模式输入 983,616，最大输出 131,072。此次配置仍使用既有
180,000 字节上下文和 16,000 token 输出决策预算，不因更大的供应商上限扩大任务预算。
[Plus 能力](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)、[Flash 能力](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)

接口实现约束：

- 使用顶层 `enable_thinking`，需要回传历史推理时明确 `preserve_thinking`；不得发送 DeepSeek 的思考控制参数。
- 使用 `max_completion_tokens` 限制思考与正文之和。官方指出实际值可相差最多 10 token，预留包含此余量。
- 工具策略 `auto`，`parallel_tool_calls=false`；本地仍拒绝多动作。思考模式不强制指定工具。
- JSON-object 需要提示中出现 JSON；不能因供应商称支持而免除完整本地 Schema 与需求声明检查。
- 流式请求启用 `stream_options.include_usage`。计费读取 `prompt_tokens`、`completion_tokens`、
  `prompt_tokens_details.cached_tokens` 和可选 `completion_tokens_details.reasoning_tokens`。
- 思考 token 是输出细分项，不重复相加计费。缺失 usage、实际模型不符或未知调用保留预留，并单独分类。
- temperature 范围为 `[0,2)`，当前温度 0 合法；不设置 stop。探测需证明实际字段与文档一致。

[请求、输出与 usage 合同](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)

P2 采用独立累计 CNY300 活动，单次 CNY20／4 小时，探测、失败、重试及未知预留全部计入。
这些是用户自主执行授权下采用的需求默认值；不占用或修改之前的 DeepSeek 账本。
完整实验在适配器、预算闭环和私有验收离线检查通过后开始。
