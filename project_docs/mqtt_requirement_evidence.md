# MQTT 110 条需求：最终场景证据与缺口

对应运行：`20260912T164202Z-e4b27709`。全部20个必过场景均在release/san下通过最终导出检查及独立复验。

“场景通过”只表示配置的断言通过，不代表整条复合需求已被证明。未配置场景的59条需求明确保留为缺口；客户端义务和底层传输等背景条件不能用客户端正常操作冒充服务端证明。

[完整原始需求、模型声明及证据 JSON](/home/ljf/NePA/runs/protocol-expansion/8a21d64ddcde4728a675322aaa74105f/mqtt-requirement-evidence.json) 保留声明解释、代码引用、构建变体、实际结果和证据哈希；模型声明与独立结果分列。

| 需求 ID | 原始需求 | 模型声明 | 最终场景结果 | 场景 ID（均含 release/san） |
|---|---|---|---|---|
| REQ-TRANSPORT-001 | MQTT 的底层传输提供从 Client 到 Server 以及从 Server 到 Client 的有序、无损字节流。 | implemented | 未验证：无独立场景 | — |
| REQ-TRANSPORT-002 | 标准的非规范性注释说明 TCP/IP 可以用于承载 MQTT 3.1.1。 | implemented | 未验证：无独立场景 | — |
| REQ-TRANSPORT-003 | 标准的非规范性注释说明非 TLS MQTT 的 IANA 注册 TCP 端口是 1883。 | implemented | 未验证：无独立场景 | — |
| REQ-TRANSPORT-004 | Server 接受来自 Clients 的网络连接。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-DATA-001 | 16 位整数以大端顺序在线上传输，高位字节在低位字节之前。 | implemented | 未验证：无独立场景 | — |
| REQ-STRING-001 | MQTT UTF-8 字符串具有两字节长度前缀，编码后的字符串不得超过 65535 字节。 | implemented | 未验证：无独立场景 | — |
| REQ-STRING-002 | UTF-8 字符串 MUST 是格式良好的 UTF-8；收到含非法 UTF-8 的 Control Packet 时接收方 MUST 关闭网络连接。 | implemented | 已配置场景通过 | invalid_utf8 |
| REQ-STRING-003 | UTF-8 字符串 MUST NOT 包含 U+0000；收到含 U+0000 的 Control Packet 时接收方 MUST 关闭网络连接。 | implemented | 已配置场景通过 | invalid_utf8 |
| REQ-FRAME-001 | Remaining Length 等于当前报文中 variable header 与 payload 的字节数，不包含 Remaining Length 自身的编码字节。 | implemented | 已配置场景通过 | fragmented_publish, length_boundaries |
| REQ-FRAME-002 | Remaining Length 使用每字节七个数据位和一个 continuation bit 的变长编码，最多使用四字节。 | implemented | 已配置场景通过 | fragmented_publish, length_boundaries |
| REQ-FRAME-003 | fixed header 第一个字节的 7-4 位是四位无符号 MQTT Control Packet 类型。 | implemented | 未验证：无独立场景 | — |
| REQ-FRAME-004 | 标为 Reserved 的 fixed-header flag MUST 使用 Table 2.2 规定的值；收到非法 flags 时接收方 MUST 关闭网络连接。 | implemented | 已配置场景通过 | invalid_flags |
| REQ-FRAME-005 | MQTT Control Packet type 值 0 是 Forbidden。 | implemented | 未验证：无独立场景 | — |
| REQ-FRAME-006 | MQTT Control Packet type 值 15 是 Forbidden。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-001 | 网络连接建立后，Client 发送给 Server 的第一个报文 MUST 是 CONNECT。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-002 | Client 在一个网络连接上只能发送一次 CONNECT；Server 收到第二个 CONNECT 时 MUST 断开该 Client。 | implemented | 已配置场景通过 | duplicate_connect |
| REQ-CONNECT-003 | CONNECT variable header 依次包含 Protocol Name、Protocol Level、Connect Flags 和 Keep Alive。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-004 | Protocol Name 是内容为 MQTT 的 UTF-8 字符串。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-005 | MQTT 3.1.1 的 Protocol Level 字段值是 4。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-006 | Server 不支持收到的 Protocol Level 时 MUST 返回 return code 0x01 的 CONNACK，然后断开 Client。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-CONNECT-007 | Connect Flags 指定 MQTT 连接行为，并指示 payload 字段是否存在。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-008 | Server MUST 验证 CONNECT 的 reserved flag 为 0；否则断开 Client。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-009 | CleanSession=1 时，Client 和 Server MUST 丢弃此前 Session 并启动新 Session。 | implemented | 已配置场景通过 | session_reset |
| REQ-CONNECT-010 | CleanSession=1 创建的 Session 与网络连接具有相同生命周期。 | implemented | 已配置场景通过 | session_reset |
| REQ-CONNECT-011 | CleanSession=1 创建的 Session 数据 MUST NOT 在后续 Session 中复用。 | implemented | 已配置场景通过 | session_reset |
| REQ-CONNECT-012 | Keep Alive 是以秒为单位并表示为 16 位字的时间间隔。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-013 | ClientId 标识 Client，并且 Client 与 Server MUST 用它标识该 MQTT Session 的状态。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-014 | ClientId MUST 存在，并且 MUST 是 CONNECT payload 的第一个字段。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-015 | ClientId MUST 是 MQTT UTF-8 字符串。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-016 | Server MUST 接受长度为 1 至 23 个 UTF-8 编码字节且仅含规定字母数字字符的 ClientId。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-017 | Server MUST 验证 CONNECT 符合第 3.1 节；不符合时关闭网络连接且不发送 CONNACK。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNECT-018 | CONNECT 验证成功时，Server MUST 发送 return code 0 的 CONNACK。 | implemented | 已配置场景通过 | fragmented_connect |
| REQ-CONNECT-019 | CONNECT 的 MQTT Control Packet type 值是 1。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNACK-001 | CONNACK 是 Server 响应 CONNECT 的报文，并且 MUST 是 Server 向 Client 发送的第一个报文。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-CONNACK-002 | CONNACK Remaining Length 的值是 2。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-CONNACK-003 | CONNACK Connect Acknowledge Flags 的 7-1 位是 reserved，MUST 为 0。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-CONNACK-004 | Server 接受 CleanSession=1 的连接时，MUST 在 CONNACK 中设置 Session Present=0 和 return code=0。 | implemented | 已配置场景通过 | minimum-interactions, session_reset |
| REQ-CONNACK-005 | Server 发送非零 return code 的 CONNACK 时 MUST 设置 Session Present=0。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-CONNACK-006 | CONNACK 的单字节 Connect Return code 使用 Table 3.1 定义的值 0 至 5。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNACK-007 | Server 发送非零 Connect return code 后 MUST 关闭网络连接。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-CONNACK-008 | CONNACK 没有 payload。 | implemented | 未验证：无独立场景 | — |
| REQ-CONNACK-009 | CONNACK 的 MQTT Control Packet type 值是 2。 | implemented | 未验证：无独立场景 | — |
| REQ-SUBSCRIBE-001 | SUBSCRIBE 创建一个或多个 Subscription；每个 Subscription 注册 Client 对一个或多个 Topic 的兴趣。 | implemented | 已配置场景通过 | pubsub |
| REQ-SUBSCRIBE-002 | SUBSCRIBE fixed-header flags MUST 是 0,0,1,0；其他值视为 malformed 并关闭网络连接。 | implemented | 已配置场景通过 | invalid_flags |
| REQ-SUBSCRIBE-003 | SUBSCRIBE MUST 包含非零 16 位 Packet Identifier。 | implemented | 未验证：无独立场景 | — |
| REQ-SUBSCRIBE-004 | SUBSCRIBE payload 中的 Topic Filters MUST 是 MQTT UTF-8 字符串。 | implemented | 未验证：无独立场景 | — |
| REQ-SUBSCRIBE-005 | SUBSCRIBE payload MUST 至少包含一个 Topic Filter/QoS 对；空 payload 是协议违规。 | implemented | 未验证：无独立场景 | — |
| REQ-SUBSCRIBE-006 | Requested QoS 字节的高六位为 reserved；reserved 位非零或 QoS 不为 0、1、2 时，Server MUST 将 SUBSCRIBE 视为 malformed 并关闭连接。 | implemented | 已配置场景通过 | invalid_qos |
| REQ-SUBSCRIBE-007 | Server 收到 SUBSCRIBE 时 MUST 回复 SUBACK。 | implemented | 已配置场景通过 | pubsub |
| REQ-SUBSCRIBE-008 | SUBACK Packet Identifier MUST 与其确认的 SUBSCRIBE Packet Identifier 相同。 | implemented | 已配置场景通过 | pubsub |
| REQ-SUBSCRIBE-009 | SUBACK MUST 为每个 Topic Filter/QoS 对包含一个表示 granted maximum QoS 或失败的 return code。 | implemented | 已配置场景通过 | pubsub |
| REQ-SUBSCRIBE-010 | SUBSCRIBE 的 MQTT Control Packet type 值是 8。 | implemented | 未验证：无独立场景 | — |
| REQ-SUBACK-001 | SUBACK 确认 SUBSCRIBE 的接收和处理。 | implemented | 未验证：无独立场景 | — |
| REQ-SUBACK-002 | SUBACK Remaining Length 等于两字节 variable header 加 payload 的长度。 | implemented | 已配置场景通过 | pubsub |
| REQ-SUBACK-003 | SUBACK return codes 的顺序 MUST 与 SUBSCRIBE Topic Filters 的顺序一致。 | implemented | 未验证：无独立场景 | — |
| REQ-SUBACK-004 | SUBACK 仅可使用 return code 0x00、0x01、0x02、0x80，其他值 MUST NOT 使用。 | implemented | 已配置场景通过 | pubsub, qos |
| REQ-SUBACK-005 | SUBACK 的 MQTT Control Packet type 值是 9。 | implemented | 未验证：无独立场景 | — |
| REQ-PUBLISH-001 | PUBLISH 从 Client 发往 Server 或从 Server 发往 Client，用于传输 Application Message。 | implemented | 未验证：无独立场景 | — |
| REQ-PUBLISH-002 | QoS 0 的发送方 MUST 使用 QoS=0、DUP=0 的 PUBLISH。 | implemented | 已配置场景通过 | qos |
| REQ-PUBLISH-003 | QoS=0 的 PUBLISH MUST NOT 包含 Packet Identifier。 | implemented | 已配置场景通过 | qos |
| REQ-PUBLISH-004 | PUBLISH Remaining Length 等于 variable header 与 payload 的长度之和。 | implemented | 已配置场景通过 | length_boundaries |
| REQ-PUBLISH-005 | Topic Name MUST 是 PUBLISH variable header 的第一个字段，并且 MUST 是 MQTT UTF-8 字符串。 | implemented | 已配置场景通过 | pubsub |
| REQ-PUBLISH-006 | PUBLISH Topic Name MUST NOT 包含通配符。 | implemented | 未验证：无独立场景 | — |
| REQ-PUBLISH-007 | PUBLISH payload 包含 Application Message，并且允许长度为零。 | implemented | 已配置场景通过 | pubsub |
| REQ-PUBLISH-008 | Client 使用 PUBLISH 向 Server 发送 Application Message，以分发给具有匹配订阅的 Clients。 | implemented | 未验证：无独立场景 | — |
| REQ-PUBLISH-009 | Server 使用 PUBLISH 向每个具有匹配订阅的 Client 发送 Application Message。 | implemented | 已配置场景通过 | pubsub, multi_client, fragmented_publish, coalesced |
| REQ-PUBLISH-010 | 因匹配既有订阅而向 Client 发送 PUBLISH 时，Server MUST 设置 RETAIN=0。 | implemented | 已配置场景通过 | qos |
| REQ-PUBLISH-011 | PUBLISH 的 MQTT Control Packet type 值是 3。 | implemented | 未验证：无独立场景 | — |
| REQ-TOPIC-001 | Server 进行订阅匹配时 MUST NOT 规范化 Topic Name 或 Topic Filter，也不得修改或替换无法识别的字符。 | implemented | 未验证：无独立场景 | — |
| REQ-TOPIC-002 | Topic Filter 中每个非通配层必须与 Topic Name 对应层逐字符相同，匹配才成功。 | implemented | 已配置场景通过 | pubsub |
| REQ-TOPIC-003 | Application Message 被发送到每个 Topic Filter 与 Topic Name 匹配的 Client Subscription。 | implemented | 已配置场景通过 | multi_client |
| REQ-TOPIC-004 | Topic Name 和 Topic Filter MUST 至少包含一个字符。 | implemented | 未验证：无独立场景 | — |
| REQ-TOPIC-005 | Topic Name 和 Topic Filter 的 UTF-8 编码长度 MUST NOT 超过 65535 字节。 | implemented | 未验证：无独立场景 | — |
| REQ-SESSION-001 | Client 和 Server MUST 在整个 Session 生命周期内保存 Session state。 | implemented | 已配置场景通过 | pubsub |
| REQ-SESSION-002 | Server 的 Session state 包含 Client 的 subscriptions。 | implemented | 已配置场景通过 | pubsub, session_isolation |
| REQ-KEEPALIVE-001 | Keep Alive 非零时，若 Server 在一点五倍 Keep Alive 时间内没有收到 Client 的任何报文，MUST 断开该 Client 的网络连接，视同网络故障。 | implemented | 已配置场景通过 | keep_alive, keep_alive_activity |
| REQ-KEEPALIVE-002 | Keep Alive 值为 0 时关闭保活机制，Server 不因 Client 不活动而断开连接。 | implemented | 已配置场景通过 | keep_alive_zero |
| REQ-KEEPALIVE-003 | Keep Alive 非零时，Client MUST 发送报文；在没有其他报文可发送的情况下 MUST 发送 PINGREQ。 | implemented | 未验证：无独立场景 | — |
| REQ-DISCONNECT-001 | DISCONNECT 是 Client 发给 Server 的最后一个 Control Packet，表示 Client 正常断开。 | implemented | 未验证：无独立场景 | — |
| REQ-DISCONNECT-002 | Server MUST 验证 DISCONNECT reserved bits 为 0；否则断开 Client。 | implemented | 已配置场景通过 | invalid_flags |
| REQ-DISCONNECT-003 | DISCONNECT 没有 variable header。 | implemented | 未验证：无独立场景 | — |
| REQ-DISCONNECT-004 | DISCONNECT 没有 payload。 | implemented | 未验证：无独立场景 | — |
| REQ-DISCONNECT-005 | Client 发送 DISCONNECT 后 MUST 关闭网络连接。 | implemented | 未验证：无独立场景 | — |
| REQ-DISCONNECT-006 | Client 发送 DISCONNECT 后 MUST NOT 在该网络连接上继续发送 Control Packet。 | implemented | 未验证：无独立场景 | — |
| REQ-DISCONNECT-007 | Server 收到 DISCONNECT 后，如果 Client 尚未关闭网络连接，Server SHOULD 关闭该连接。 | implemented | 已配置场景通过 | disconnect |
| REQ-DISCONNECT-008 | DISCONNECT Remaining Length 为 0。 | implemented | 未验证：无独立场景 | — |
| REQ-DISCONNECT-009 | DISCONNECT 的 MQTT Control Packet type 值是 14。 | implemented | 未验证：无独立场景 | — |
| REQ-ERROR-001 | 除非另有规定，Client 或 Server 遇到协议违规时 MUST 关闭收到引发违规 Control Packet 的网络连接。 | implemented | 已配置场景通过 | duplicate_connect, invalid_qos, invalid_flags, invalid_utf8 |
| REQ-ERROR-002 | 处理入站 Control Packet 时发生 Transient Error，Client 或 Server MUST 关闭收到该报文的网络连接。 | implemented | 未验证：无独立场景 | — |
| REQ-ERROR-003 | Server 检测到 Transient Error 时 SHOULD NOT 断开其他 Client，也不应影响与其他 Client 的交互。 | implemented | 未验证：无独立场景 | — |
| REQ-PING-001 | PINGREQ 由 Client 发往 Server，用于向 Server 表明 Client 存活、请求 Server 响应以确认存活，并维持网络连接活动。 | implemented | 未验证：无独立场景 | — |
| REQ-PING-002 | PINGREQ 没有 variable header 也没有 payload，Remaining Length 为 0。 | implemented | 未验证：无独立场景 | — |
| REQ-PING-003 | Server 收到 PINGREQ 后 MUST 返回 PINGRESP。 | implemented | 已配置场景通过 | minimum-interactions, coalesced, keep_alive_activity |
| REQ-PING-004 | PINGRESP 没有 variable header 也没有 payload，Remaining Length 为 0。 | implemented | 已配置场景通过 | minimum-interactions |
| REQ-PING-005 | PINGREQ 的 MQTT Control Packet type 值是 12。 | implemented | 未验证：无独立场景 | — |
| REQ-PING-006 | PINGRESP 的 MQTT Control Packet type 值是 13。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBSCRIBE-001 | UNSUBSCRIBE 由 Client 发往 Server，用于取消订阅 Topics。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBSCRIBE-002 | UNSUBSCRIBE 固定报头的 3-0 位是保留位，MUST 被设置为 0010；其他任何值都是 malformed，Server MUST 关闭网络连接。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBSCRIBE-003 | UNSUBSCRIBE variable header 包含 Packet Identifier。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBSCRIBE-004 | UNSUBSCRIBE payload 包含一组 Topic Filters，每个 Topic Filter 是 UTF-8 字符串，在 payload 中紧密打包。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBSCRIBE-005 | UNSUBSCRIBE payload MUST 至少包含一个 Topic Filter；没有 payload 的 UNSUBSCRIBE 是协议违规。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBSCRIBE-006 | Server MUST 将 UNSUBSCRIBE 的 Topic Filters 与其为该 Client 保存的 Subscriptions 的 Topic Filters 逐字符比较；匹配时 MUST 删除该 Subscription，否则 MUST NOT 有任何额外影响。 | implemented | 已配置场景通过 | unsubscribe, session_isolation |
| REQ-UNSUBSCRIBE-007 | Server MUST 通过发送 UNSUBACK 响应 UNSUBSCRIBE；即使没有删除任何 Subscription 也 MUST 响应。 | implemented | 已配置场景通过 | unsubscribe |
| REQ-UNSUBSCRIBE-008 | UNSUBSCRIBE 的 MQTT Control Packet type 值是 10。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBSCRIBE-009 | UNSUBSCRIBE MUST 包含非零 16 位 Packet Identifier。 | implemented | 未验证：无独立场景 | — |
| REQ-UNSUBACK-001 | UNSUBACK 由 Server 发往 Client，用于确认收到 UNSUBSCRIBE。 | implemented | 已配置场景通过 | unsubscribe |
| REQ-UNSUBACK-002 | UNSUBACK Remaining Length 的值是 2。 | implemented | 已配置场景通过 | unsubscribe |
| REQ-UNSUBACK-003 | UNSUBACK variable header 包含与被确认的 UNSUBSCRIBE 相同的 Packet Identifier。 | implemented | 已配置场景通过 | unsubscribe |
| REQ-UNSUBACK-004 | UNSUBACK 的 MQTT Control Packet type 值是 11。 | implemented | 已配置场景通过 | unsubscribe |
| REQ-SUBACK-006 | Server 可以授予比 Client 请求更低的 maximum QoS，因此对请求 QoS 1 或 QoS 2 的 Subscription 回复 Success - Maximum QoS 0 是允许的。 | implemented | 已配置场景通过 | qos |
