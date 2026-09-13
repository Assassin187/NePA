# P1 私有 Oracle 源码迁移

基线：`351ff20949ba9e82b6ffa667040a5d134ffc7114`（Design 11）。权威依据为 `system_design.md` 第 4 节
和 `nepa-p0-p2-plan.md` 顶层已接受决策。本文只记录源码／单元测试移交，不代表新的真实生成、双容器
准入或付费修复验收已经通过。

改动仅涉及三个既有 oracle 脚本、两个 oracle 测试模块和本文。没有新增运行时组件或 oracle 资产：
MQTT smoke 导入已列出的同级 `mqtt_behavior.py`，HTTP 保持其本地小型 trace／生成器。两个
Acceptance 1.0 manifest 的字节均未变化，包括资产、ID、`required`、argv、超时和全部 `req_ids`。
Spec 输入和历史实验资产不变。

## 宿主运行合同

- `NEPA_ORACLE_SEED`：恰好 64 个十六进制字符，由宿主为场景／变体提供的 256 位 seed；oracle 不得
  从 host、port 或 run ID 派生。
- `NEPA_ORACLE_TRACE_FILE`：检查器／宿主拥有的私有可写 JSONL。CLI 执行必须同时提供两个环境变量；
  单元测试可显式使用 `Oracle(seed, trace_file=None)`。不存在隐式随机 seed。
- host／port argv 保持不变，oracle 只连接所给端点。
- stdout 只输出一行 JSON：`{"passed": bool, "category": str, "observation": dict}`。成功类别为
  `protocol`，观察为 `{"outcome":"accepted"}`，退出 0；失败退出 1。check ID 和 variant 由宿主关联，
  不从 argv 回显。
- `WireMismatch` 只包含显式构造的语义字段。类别是源码常量，不使用异常文本或服务端字符串。意外异常
  输出 `error`（或 `timeout`）及 allowlist 中的 `error_class`，不打印异常消息、traceback、原始载荷、
  seed、标识符、topic 或路径，也不提供原始诊断回退。
- 安全数值字段：`expected_length`、`actual_length`、`expected_status`、`actual_status`、
  `expected_type`、`actual_type`、`actual_order`、`offset`、`expected_duration_ms`、
  `actual_duration_ms`。长度和首个差异 offset 只描述比较字段，不暴露字段原值。MQTT packet type
  使用固定头字节；SUBACK code／QoS 使用数值。`actual_order` 从握手开始按连接内响应顺序从 1 计数。
- 安全字符串字段：`outcome`、`expected_outcome`、`actual_outcome`、`error_class`，值均为源码常量，
  如 `open_quiet`、`eof`、`mismatched_identifier`、`different_bytes`、`missing_or_invalid`、`timeout`。
  HTTP 头超时时预期长度未知（null），宿主会省略；预期结果和已缓存实际长度仍可用于诊断。

## 重放与私有字节证据

每个场景独立使用 `random.Random(int(seed,16))`，不使用全局 PRNG、UUID、`os.urandom`、端口 seed 或
生成代码导入。起始记录包含 seed、`randomization_version: random-inputs/1` 和 Python 版本。冻结 oracle
源码、Python 镜像、seed 和确定性调用顺序共同定义输入重放；实际收发记录提供字节证据。不会声称 OS
时序和 TCP 分段逐次完全相同。

每条 JSONL 事件包含 sequence、墙钟纳秒和单调时钟纳秒。连接成功后分配单调连接号，连接失败也记账。
`write` 记录逻辑写入及请求大小；每次底层 `sock.send()` 返回记录 write number、实际数量、连接累计
offset 和实际已发送字节。部分发送后异常时，只记录已接受前缀，并单独记录超时／错误，不虚构后缀。
每次 `recv()` 记录请求大小、实际字节／数量和累计 offset，包括 EOF 与 timeout。`settimeout`、`quiet`
（含意外数据／EOF／缓存数据）、`half_close`、`close` 和 Keep Alive `wait` 都保留观察与调度意图。
逻辑 write 不被描述成 TCP 包边界。

随机 client ID 只使用小写 ASCII 字母和数字：普通行为 9–21 字节、reset 21、smoke 15–16，均落在
MQTT 必须支持的 1–23 字节字母数字范围。topic 使用固定合法前缀加 8–32 字节字母数字后缀。Packet ID
范围为 1–65535，会话重置需要时复用同一 ID。随机 body 为 1–96 字节。HTTP 会改变合法 Host 标签；
附加 header-case 交互仅复用原先的前置 HTAB、后置 SP+HTAB OWS 模式。

原始向量始终最先执行。适用场景附加 2–4 次随机 payload 交互，而非重复整套：MQTT 的 pubsub、
multi-client、unsubscribe、session isolation／reset、QoS 和 coalesced；smoke 在原最终连接增加 2–4 次
PING。HTTP 的 echo、header-case、pipeline、未知路径及固定拒绝后的健康恢复增加 2–4 次。fragmented
场景把 2–4 个随机 cut 与全部必选 cut 合并；coalesced 附加交互选择 2–4 个 write 组成一组，尾部可剩
单个。Keep Alive 等时序敏感场景完全保留原观察，不增加随机次数。

## 公共断言迁移

所有原始谓词语义保持不变。字节相等改由 `equal_bytes` 判定并记录长度与首个差异 offset。MQTT
`receive` 区分提前 EOF 和部分读取 timeout；`Client.packet` 保留四字节 Remaining Length 上限和
1 MiB 响应上限；`Client.expect` 检查固定头和完整 body，CONNACK 不匹配还输出数值返回码。
`subscribe` 仍检查 SUBACK 头、Packet ID 一致、返回码数量、合法值 0／1／2／128，且授予 QoS 不高于
请求。`unsubscribe` 检查精确 UNSUBACK 和相关 ID。`quiet` 要求连接保持打开且无数据；`eof` 拒绝尾随
数据（只有 MQTT behavior 保留原 reset 容许，smoke 拒绝仍要求真实 EOF）。

HTTP `response` 保留全部断言：头部 EOF／131072 字节上限；三段式 HTTP/1.1 状态行及三位十进制状态；
冒号和非折叠头语法；禁止重复 Content-Length 和 Transfer-Encoding；Content-Length 为十进制；
body 上限 1 MiB；完整 body 精确匹配；为后续响应保留已缓存字节。`expect` 保留精确 status 和可选完整
payload；`quiet` 同时检查缓存与新数据；`eof` 检查缓存及真实 EOF。失败使用稳定语义类别，如
`status_line`、`content_length`、`body_EOF`、`response_status`、`response_body`、`quiet`、
`connection_close`，不使用通用 assertion 字符串。

## MQTT 场景断言映射

下表“需求／超时相同”表示原 manifest 的完整 `req_ids` 数组逐字节一致，不只是数量相同。

| 场景 | 保留的原始断言与边界 | 需求／超时 |
|---|---|---|
| minimum-interactions | CONNECT 在第 3 字节分片；精确 `20 02 00 00`、`d0 00`；不支持 level 0 返回 `20 02 00 01` 后真实 EOF；新连接再次验证 CONNACK／PINGRESP；保留三条连接 | 相同／30s |
| pubsub | 两个订阅；空载荷和 `00 ff c0 00` 二进制载荷；不匹配发布保持安静并可 PING；增加 2–4 个 body | 相同／20s |
| multi_client | 两个匹配订阅者收到消息；未订阅者保持安静并响应 PING；增加有界投递／缺席检查 | 相同／20s |
| unsubscribe | 取消存在／不存在订阅；只剩余 topic 转发；quiet；重复取消仍 ACK；增加保持状态的流量 | 相同／20s |
| session_isolation | 一个订阅者取消后另一个仍收到，前者 quiet／PING；增加投递／缺席检查 | 相同／20s |
| qos | 请求 QoS 0／1／2，授予码合法且不高于请求；原 retained 二进制发布均以 0x30 投递；增加 body | 相同／20s |
| session_reset | 同 client ID、CleanSession=1、原投递、DISCONNECT+EOF、重连无旧订阅、PING、重新订阅后投递；增加新投递 | 相同／20s |
| duplicate_connect | 已建立连接上第二个 CONNECT 导致关闭；独立客户端 PING 成功 | 相同／20s |
| disconnect | DISCONNECT 关闭；后续新客户端 PING 成功 | 相同／20s |
| invalid_qos | 原始 3、4、128 SUBSCRIBE code 均关闭；之后合法客户端 PING | 相同／20s |
| fragmented_connect | 必选 cut 1、2、3、7、last−1、last；每个不完整前缀后 open／quiet 0.06s；完整后 CONNACK／PING；并入随机 cut | 相同／20s |
| fragmented_publish | 原 160 字节 `z`；跨 RL／字符串／body 的 cut 1、2、3、4、5、8、last−1、last；前缀期间订阅者 0.06s、发布者 0.03s quiet；最终精确投递和 PING；并入随机 cut | 相同／20s |
| coalesced | 原 `one`、`two` 和不完整 PING 头一次写入；两次有序投递、发布者 quiet 0.1s；补最后字节后 PINGRESP；再增加随机有序分组 | 相同／20s |
| length_boundaries | Remaining Length 127、128、16383、16384，分别精确投递完整 `z` 载荷 | 相同／20s |
| truncated | publish 缺最后一字节；quiet 0.1s；写半关闭后 EOF；旧／新健康客户端 PING | 原空需求数组／20s |
| invalid_flags | 原 C1、SUBSCRIBE header 80、E1 向量均关闭；每次之后健康 PING，最后新连接 PING | 相同／20s |
| invalid_utf8 | 原 C0 AF、ED A0 80、NUL topic 均关闭；每次健康 PING，最后新连接 PING | 相同／20s |
| keep_alive | KeepAlive=2；4s 内且不早于 2.5s 收到 EOF；随后健康 PING；诊断增加实际时长 | 相同／20s |
| keep_alive_zero | KeepAlive=0，完整 4s open quiet，然后 PING | 相同／20s |
| keep_alive_activity | KeepAlive=2，五次 1s 等待并 PING，之后 4s 内 EOF；显式记录 wait | 相同／20s |

## HTTP 场景断言映射

| 场景 | 保留的原始断言与边界 | 需求／超时 |
|---|---|---|
| get_head | GET 返回 200 `nepa\n`；粘连 HEAD+GET；HEAD 无 body 且精确 `Content-Length: 5`；后续 GET 精确 payload | 相同／20s |
| echo | 空 body、原二进制／类请求 body、16384 字节 `z` 精确回显；再增加 2–4 个 body | 相同／20s |
| routes | GET／POST／HEAD `/missing`→404，BREW 和小写 get `/`→501，body 为空且长度 0；增加随机未知路径 | 相同／20s |
| header_case | 原 `hOsT`、`cOnTeNt-LeNgTh`、HTAB／SP OWS 和 `abc` 精确回显；再增加随机大小写／body | 相同／20s |
| host_errors | 缺 Host、重复 Host a/b、`bad host` 均 400+EOF；增加独立健康 echo | 相同／20s |
| length_errors | −1、abc、2x、冲突重复 2/3、列表 2/3、相同重复 2/2、列表 2/2、36 位超大长度均 400+EOF；随后健康 echo | 相同／20s |
| fragmented | 原 body `00 ff`；cut 1、5、22、header-end−1、last−1、last；每个不完整前缀 quiet 0.08s；完整后才回显；并入随机 cut | 相同／20s |
| pipeline | GET、echo `one`、不完整 echo `two` 一次写入；前两响应有序且精确，quiet 0.08s；最后一字节后精确 `two`；增加随机分组 echo | 相同／20s |
| connection_close | GET 带 Connection close；200 精确 body；响应 Connection 值大小写不敏感地为 close，随后 EOF | 相同／20s |
| truncated | POST 缺最后一字节；quiet 0.08s；写半关闭后 EOF；独立 GET 返回精确内容 | 相同／20s |
| malformed | 原四个畸形请求行／换行／Host 空格／header 冒号向量均 400+EOF；随后健康 echo | 相同／20s |
| transfer_encoding | 原 chunked 向量（无／有 Content-Length:5）均 400+EOF；保持子集策略，随后健康 echo | 相同／20s |

## 不变量与验证证据

下列摘要与 `git show 351ff20:<path>` 比较，并固定在离线测试中：

| 文件 | 未变化 SHA256 |
|---|---|
| `gold_file/mqtt/acceptance.json` | `eb74d6283eb80ca116f52d69565f930eb05efb2854af5a3172a1ff57ac23014d` |
| `gold_file/http/acceptance.json` | `f2d8323fb82739c6deda347b7d156f71cfac3434928bb47a4b6e342efc24e91a` |
| `gold_file/mqtt/specIR.json` | `a0ec9616eb06c206416a93220e1ea630d04166eb17e102bc9d9476fe2694aa09` |
| `gold_file/http/specIR.json` | `31e090e0dd6cd7e82a7d981b08f3fd0005a08c2143d50c556a75eba031d6c4c3` |

离线测试使用累积流 peer、短读取和模拟时钟覆盖全部 MQTT 20／HTTP 12 场景；同 seed 的输入／I/O
调度一致，不同 seed 的发送输入不同。测试还验证必选 cut／quiet 次数、client ID 字符／长度、RL／二进制／
畸形边界、manifest／Spec 字节、真实短发送、部分发送 timeout 证据、EOF／timeout／half-close、CLI 环境
变量及安全结构化失败。错误 ACK／body／length／status、会话泄漏和提前部分响应等负例继续保留。

授权的离线命令为：

```text
.venv/bin/python -m pytest tests/test_protocol_oracles.py tests/test_private_oracle_vectors.py -q -p no:cacheprovider
.venv/bin/python -m ruff check gold_file/mqtt/acceptance gold_file/http/acceptance tests/test_protocol_oracles.py tests/test_private_oracle_vectors.py
```

记录结果：55 个 oracle 测试通过，Ruff 和范围内 `git diff --check` 通过；加上只读核查的
`tests/test_acceptance_runner.py`，三个模块共 58 项通过。

这些测试不证明真实容器隔离、真实 TCP 调度、san／ASan、付费修复或生产生成成功；这些属于后续准入门。
既有运行和资产必须保持不变。
