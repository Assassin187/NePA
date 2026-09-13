# 协议扩展实施与证据

本轮保留 MQTT 原有 110 条需求，增加独立核心行为检查；在严格本地校验下比较 JSON-object 与原生
工具调用；并要求 MQTT 与 HTTP 固定长度子集各完成一次全新生成。全过程不使用 OpenSpec。
两套输入并列存放在 `gold_file/mqtt` 和 `gold_file/http`，各自包含 `specIR.json`、`target.json`、
`acceptance.json` 及只读 oracle 资产。

## 预算与实验顺序

1. 完成离线测试和历史导出副本审计。
2. 冻结并执行 `action_study.py`：每种接口使用相同的 24 个 Flash、8 个 Pro 样本，各运行四个真实
   工具夹具，并探测 Strict Beta。总上限 ¥10，计入新的 MQTT 活动。只有达到预注册门槛才切换原生接口。
3. 冻结最终候选，并行启动空工程 MQTT 和 HTTP 生成；分别对导出副本重新构建和验证。一个实验失败
   不阻断另一个。这些样本只证明本轮场景可行，不证明稳定性。

最终预算授权：`runs/mqtt-e2e` 和 `runs/http-e2e` 各自使用新的 ¥300 累计额度，每次生成最多 ¥20／
4 小时。动作研究 ¥10 计入新 MQTT 活动且失败后不补回。旧美元限额由本轮人民币限额替代；历史运行
仍在 `runs/_refactor/worktree/runs/e2e`，明确不计入新额度。所有新失败和未知调用预留均计入。

国内价格采用 2026-09-13 官方页面快照：Flash 忙时缓存命中输入／未命中输入／输出为每百万 token
¥0.04／¥2／¥8，Pro 为 ¥0.30／¥9／¥27，闲时半价。忙时为上海时区周一至周五 09:00–12:00、
14:00–18:00。Run 6.0 记录请求开始 UTC、所选时段与价格，以及可获得的缓存计数。缓存计数缺失时
按全部未命中估算；usage 未知时保留忙时价格预留。金额是估算，不是供应商账单。旧美元报告保持原样，
只能用其原运行时复现。

## 历史导出审计

原交付和报告均未修改。审计只复制交付，执行 clean release 与 ASan/UBSan 构建，再运行 20 个必过
MQTT 场景。原始结果和按需求关联记录在 `runs/behavior-audit-v4/summary.json`。

| 历史运行 | 构建变体 | 扩展检查 |
|---|---|---|
| `20260912T112242Z-56f67d18` | 两者通过 | 全部通过 |
| `20260912T135449Z-4c036768` | 两者通过 | 每个变体各失败 12 个场景 |
| `20260912T135449Z-22021a32` | 两者通过 | 每个变体各失败 6 个场景 |

`4c036768` 失败于 pubsub、multi_client、unsubscribe、session_isolation、qos、session_reset、
fragmented_publish、coalesced、length_boundaries、truncated、invalid_flags、invalid_utf8。
`22021a32` 失败于 session_reset、duplicate_connect、invalid_qos、fragmented_publish、coalesced、
invalid_flags。这不推翻历史最小检查的成功结论，只说明当时没有验证这些行为。

第一次审计保留了构建和检查输出，但汇总发布因相对路径处理失败。修复驱动后执行 v2；v3 冻结自身输入，
并在只要求连接结束的场景中将 TCP reset 视为关闭。v2、v3 的失败场景一致。最终 v4 收紧需求映射，
底层传输假设仍未验证；失败场景不变。三个原交付的输入和源码摘要均未变化。

## 当前实现

最终映射只将 51 条 MQTT 需求关联到实际服务端断言，余下 59 条明确列为缺口。字节流场景不证明
“底层传输无损”等背景假设。

Report 4.0 列出全部需求，并将模型声明与最终导出场景结果分开。可选检查、缺失证据和不完整执行都不能
建立验证。Config 2.0 以 `coder.action_format`（`json_object`／`tool_calls`）代替布尔配置；默认仍为
JSON，除非真实配对研究支持切换。原生调用保留流式参数、调用 ID 和 `reasoning_content`，并经过不变的
本地 AgentAction Schema。

HTTP 有 27 条人工需求和 12 个必过场景，Target 与 MQTT 的 C99/server Target 字节一致。输入明确区分
RFC 9110／9112 规则和项目应用约定。因明确排除 chunked，本实现不是完整 HTTP/1.1 符合性实现。
生成器没有增加 HTTP 专用路径。

本阶段离线验证通过 172 项非付费测试、Ruff、mypy、sdist 和 wheel 构建。

## 已完成的动作接口比较

冻结候选为 `73cdc4b`。预注册和完整结果位于 `runs/action-study-cny-v1`；单次调用和夹具位于
`runs/mqtt-e2e`，均标记为研究运行，不能算协议生成成功。64 个格式样本、8 个短会话和 Strict Beta
探测均已完成。

| 指标 | JSON-object | 原生工具调用 |
|---|---:|---:|
| Flash 无效动作 | 15/24（62.5%） | 22/24（91.7%） |
| Pro 无效动作 | 0/8 | 4/8 |
| 每个有效格式动作耗时 | 3.843 秒 | 13.457 秒 |
| 每个有效格式动作费用 | ¥0.01069 | ¥0.08291 |
| 无效格式生成耗时 | 21.693 秒 | 58.902 秒 |
| 真实短会话通过数 | 3/4 | 3/4 |

JSON 失败包括 11 次 XML/DSML 和 4 次语法／附加文字错误；26 次原生失败全是多调用，均未执行。
两种模式的读写、替换和完成会话通过。编译修复会话虽构建成功，却把小夹具扩展成常驻服务，因而在
预定义运行检查中超时。夹具本身保留了 server 任务上下文，所以该失败不能单独衡量纯语法修复能力。
观察结果后没有修改门槛或夹具检查。

Strict Beta 返回 HTTP 400：required 属性必须覆盖对象的全部属性。原 AgentAction Schema 含可选属性，
探测没有重写 Schema 或放宽本地校验。未知 usage 保留 ¥0.145724 忙时预留。研究活动共占用
¥1.16708254（已结算估算 ¥1.02135854，加未知预留），低于 ¥10。所有已结算调用发生在闲时，并使用
供应商缓存 usage。没有使用响应缓存，也没有生成协议代码。由于升级门槛失败，默认接口仍为 JSON-object。

## 最终冻结候选生成

两个真实实验都通过。候选提交：`10cb987178c329cefb909c8651a2daa9e9c548b5`；运行时包 SHA256：
`cf4b589a6dadd872890bcaba8428a50edfc4d11992b8d844c3a0a3bec70d5b35`。
两者从空工程并发启动，使用真实 API。运行期间源码、提示词、配置和输入未变；生成代码没有人工编辑；
没有响应缓存、历史实现或研究夹具。候选后的文档变更只记录结果和修正旧版本标注，不改变运行时、
提示词、配置或输入。

| 结果 | MQTT | HTTP 固定长度子集 |
|---|---:|---:|
| Run ID | `20260912T164202Z-e4b27709` | `20260912T164202Z-d0b839c4` |
| 生成时间 | 69.39 分钟 | 19.74 分钟 |
| 通过任务 | 23/23 | 10/10 |
| 必过场景（release、san 各一次） | 20/20 | 12/12 |
| 独立导出 clean 构建和检查 | 两者通过 | 两者通过 |
| 场景映射需求通过 | 51/110 | 26/27 |
| 无独立场景需求 | 59 | 1（范围定义） |
| 调用数（含 usage 未知） | 750 | 208 |
| 已结算人民币估算（均为闲时） | ¥9.91957752 | ¥2.05181392 |
| 未知调用预留 | 0 | ¥0.236486 |
| 本次生成占用 | ¥9.91957752 | ¥2.28829992 |
| 新活动累计占用 | ¥11.08666006（含动作研究） | ¥2.28829992 |
| 新活动剩余额度 | ¥288.91333994 | ¥297.71170008 |

HTTP 的 call 102 在 ConnectTimeout 后未返回 usage，因此保留预留；动作研究同样保留 Strict Beta 预留。
任何未知预留均未重置。两个生成均低于 ¥20／4 小时，研究低于 ¥10，各活动低于 ¥300。历史美元记录
按用户后续授权排除在新活动之外并保持不变。金额均为估算。

默认 JSON 接口在 MQTT 中仍产生 192 次格式无效响应（152 XML/DSML、22 语法／附加文字、17 空响应、
1 Schema 错误），消耗 11.27 分钟 API 时间；HTTP 为 52 次（41 XML/DSML、8 语法／附加文字、3 空响应），
消耗 3.89 分钟。严格校验保证它们没有执行。因范围和上下文不同，不能用此次 MQTT 证明相较历史
185／180 次已有改善。长时间源码检查是另一个已观察到的费用来源。接口评估已经完成，动作格式问题仍开放。

需求状态只使用当前最终导出的检查。独立复验对复制导出执行 clean release／san 构建，并使用只读 oracle。
每条需求在交付证据 JSON 中保留原文、模型声明和证据引用。通过只表示已映射场景通过，不证明全部复合
条款、客户端义务或背景定义。

本轮不作稳定性或完整协议符合性声明。MQTT 只覆盖所选核心行为，不扩展 QoS 1/2 确认／重传和持久会话。
HTTP 明确排除 chunked、TLS、代理、升级、缓存和 HTTP/2；拒绝 Transfer-Encoding 不等于满足完整
HTTP/1.1 接收要求。

## 交付物

- [MQTT 源码／二进制／输入／报告包](/home/ljf/NePA/runs/protocol-expansion/8a21d64ddcde4728a675322aaa74105f/deliverables/mqtt-c99-server.tar.gz)；
  [Report 4.0](/home/ljf/NePA/runs/mqtt-e2e/20260912T164202Z-e4b27709/report.json)；
  [完整需求证据](/home/ljf/NePA/runs/protocol-expansion/8a21d64ddcde4728a675322aaa74105f/mqtt-requirement-evidence.json)。
- [HTTP 源码／二进制／输入／报告包](/home/ljf/NePA/runs/protocol-expansion/8a21d64ddcde4728a675322aaa74105f/deliverables/http-c99-server.tar.gz)；
  [Report 4.0](/home/ljf/NePA/runs/http-e2e/20260912T164202Z-d0b839c4/report.json)；
  [完整需求证据](/home/ljf/NePA/runs/protocol-expansion/8a21d64ddcde4728a675322aaa74105f/http-requirement-evidence.json)。

共享批次和独立验证见 [batch.json](/home/ljf/NePA/runs/protocol-expansion/8a21d64ddcde4728a675322aaa74105f/batch.json)，
摘要和实测费用见 `experiments/protocol-expansion/generation-results.json`，MQTT 110 条索引见
`mqtt_requirement_evidence.md`。

每个压缩包都包含未修改的生成工程源码、两个二进制、人工输入、冻结运行输入、Report 4.0、需求证据和
独立验证结果。解压后可在 `project/` 中执行 `make clean && make release san`，需要 C99 编译器和
ASan/UBSan。两个 Makefile 均不依赖 NePA。
