# Gold Spec 字段说明

`mqtt-3.1.1-wire-format.json` 与 `mqtt-3.1.1-min-requirements.json` 分别记录从同一不可变 MQTT 3.1.1 规范源文档中抽取并人工确认的线缆格式和规范条目。前者声明协议数据单元如何定界、编码、排列和区分消息类型；后者只声明协议参与方必须、应当或可以执行的行为，并用 `wire_format_reference` 关联前者。两份协议事实文件都保留来源追溯信息，但不承载产品范围、MVP 决策、实现架构或验收测试要求。

`mqtt-3.1.1-min-profile.json` 是独立的生成范围契约。它引用上述两份协议事实文件，选择首个实验所需的最小 Broker 功能及明确排除项，但不改变或覆盖任何协议事实。该文件遵循 `generation-profile.schema.json`。

## 通用抽象

通用模型使用“协议数据单元”（Protocol Data Unit, PDU）作为上层概念，不假设所有协议都采用二进制报文或单一数据单元：

| 协议类别 | 使用该模型时的典型映射 |
| --- | --- |
| MQTT | 一个二进制 `MQTT Control Packet` PDU；类型位由类型选择器读取，Remaining Length 是定界字段。 |
| CoAP | 可分别声明数据报承载与可靠传输承载的 PDU；Type、Code 等字段可以是独立类型选择器。 |
| HTTP | HTTP/1 请求和响应消息、HTTP/2 帧、HTTP/3 帧可以是不同 PDU；起始行语法或 Frame Type 用于选择消息类型。 |
| SMTP | 命令行、回复行和邮件数据可以分别建模；CRLF 或数据终止规则用作定界方式。 |
| FTP | 控制连接上的命令与回复、数据连接中的传输内容可以分别建模，不强制合并成一种报文。 |

因此，模型同时支持：

- 一个协议包含多个 `protocol_data_units`；
- 协议内复用的整数、字符串、token 或 option 等 `data_types`；
- 可选的 `transport_bindings`，仅在需要显式描述 PDU 与承载协议的关系时使用；
- `binary`、`text` 和 `mixed` 编码；
- 长度前缀、分隔符、固定长度、传输层边界、连接关闭、语法边界及组合定界；
- 固定次数、可选和重复出现的有序组成部分；
- 由字段值、语法、上下文或传输方式选择的消息类型；
- 消息类型对公共字段的固定值或不同解释；
- 类型专属布局。

## 职责边界

以下信息属于独立的 wire-format 规格文件：

- PDU、帧、消息、命令或回复的名称；
- 字段位置、表示方式、顺序和出现次数；
- 消息边界和定界规则；
- 类型编号、类型名称、方向及类型专属结构；
- 固定标志值和字段在特定消息类型中的解释。

以下信息属于 `requirements`：

- 收到某类消息后必须执行的动作；
- 消息的合法发送顺序和状态转换；
- 非法字段或非法消息的错误处理；
- 超时、重试、确认、路由和会话语义；
- 规范中的 MUST、SHOULD、MAY 等行为约束。

例如，MQTT 每种 Control Packet 的类型值和固定头标志位定义在 wire-format 规格文件；“保留标志必须使用结构表中的值”和“收到非法标志必须关闭连接”仍是 requirements 文件中的条目。

最小功能选择属于独立 profile：

- 生成的协议角色和承载绑定；
- 必须收发的 Control Packet；
- QoS、Session、Topic matching 等必需能力；
- Retain、Will、认证、持久会话等明确排除项；
- 评估会提供的范围内输入和不会要求处理的合法但范围外输入。

profile 不是协议规范，也不声称所选子集是完整的 MQTT 3.1.1 一致性实现。

## 文件级字段

| 字段 | 含义 |
| --- | --- |
| `$schema` | 声明该 JSON 应遵循的 Schema 地址。 |
| `gold_set_id` | 规范条目集的稳定唯一标识。 |
| `description` | 人类可读的内容说明和职责边界。 |
| `source_sha256` | 原始规范源文件的 SHA-256，用于确认不可变来源。 |
| `wire_format_reference` | 独立线缆格式规格文件的相对路径及其稳定 ID。 |
| `requirements` | 原子规范条目数组。 |

`wire_format_reference.file` 必须是同目录中的 `*-wire-format.json` 文件；`wire_format_reference.wire_format_id` 必须与该文件的 `wire_format_id` 相同。需求条目的 `structure_references` 均相对于该被引用的 wire-format 文件解析。

## wire-format 规格文件的顶层字段

| 字段 | 含义 |
| --- | --- |
| `$schema` | 声明文件遵循 `wire-format.schema.json`。 |
| `wire_format_id` | 线缆格式规格的稳定 ID，供需求文件引用。 |
| `protocol` / `protocol_version` | 此结构规格所属的协议及版本。 |
| `source_sha256` | 该结构抽取所依据的不可变规范源文件哈希。 |
| `model_version` | 线缆格式抽象自身的版本，当前为 `1.1`。 |
| `description` | 当前规格如何实例化通用模型。 |
| `data_types` | 协议复用的线缆数据类型；可以为空数组。 |
| `transport_bindings` | 可选的承载绑定数组；不声明时完全省略。 |
| `protocol_data_units` | 本协议定义的 PDU 数组；不限制一个协议只能有一种 PDU。 |

## `data_types[]` 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 数据类型的稳定 ID，也可以作为需求的结构引用。 |
| `name` | 规范中的数据类型名称。 |
| `kind` | 人类可读的类别，例如 unsigned integer、string 或 token。 |
| `representation` | 数据类型在线缆上的表示方式。 |
| `definition` | 数据类型的规范含义。 |
| `constraints` | 长度、取值范围、字符集或其他结构边界。 |
| `source` | 数据类型定义的原文追溯信息。 |

## `protocol_data_units[]` 字段

| 字段 | 含义 |
| --- | --- |
| `id` | PDU 内部稳定 ID，供结构引用使用。 |
| `name` | 规范对 PDU 的正式称呼。 |
| `kind` | 人类可读的类别，例如 packet、message、frame、command 或 response。 |
| `definition` | PDU 的规范性或定义性说明。 |
| `encoding` | 编码类别、基本单位和说明。 |
| `source` | PDU 整体定义的原文定位和摘录。 |
| `framing` | 一项或多项消息边界规则。 |
| `layout` | PDU 的公共有序布局。 |
| `type_selectors` | 根据字段值、语法、上下文或传输方式选择消息类型。 |

## `transport_bindings[]` 字段

`transport_bindings` 是可选字段。PDU 的内部结构不依赖显式承载关系时，应省略整个字段，而不是写空的假设对象。

| 字段 | 含义 |
| --- | --- |
| `id` | 承载绑定的稳定 ID。 |
| `transport` | 底层传输或承载方式的规范名称。 |
| `definition` | PDU 与该承载方式之间的关系。 |
| `pdu_refs` | 使用该承载方式的一个或多个 PDU 引用。 |
| `framing_refs` | 该绑定采用或覆盖的定界规则引用；没有关联规则时为空数组。 |
| `source` | 承载绑定的原文追溯信息。 |

## `framing[]` 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 定界规则的局部稳定 ID。 |
| `kind` | `fixed_length`、`length_prefixed`、`delimiter_terminated`、`transport_delimited`、`connection_delimited`、`syntax_delimited` 或 `composite`。 |
| `applies_when` | 该规则适用的规范条件。 |
| `definition` | 如何确定一个完整 PDU 的边界。 |
| `field_refs` | 定界所使用的结构字段引用；不使用字段时可以为空数组。 |
| `delimiter` | 分隔符定界时使用的可选字面值。 |
| `source` | 定界规则的原文追溯信息。 |

## `layout[]` 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 组成部分的局部稳定 ID。 |
| `name` | 组成部分名称。 |
| `order` | 在线缆格式中的顺序，从 `1` 开始。 |
| `occurrence` | `one`、`zero_or_one`、`zero_or_more` 或 `one_or_more`。 |
| `definition` | 规范对该组成部分的位置和作用的定义。 |
| `fields` | 可选的公共字段；字段完全由消息类型定义时省略。 |

`fields[]` 使用 `id`、`name`、`occurrence`、`location`、`representation`、可选 `data_type_ref`、`definition`、`constraints` 和可选 `source` 记录字段的局部 ID、名称、出现次数、位/字节或语法位置、线缆表示、可复用数据类型、规范含义、取值边界及局部追溯信息。可选 `members` 用于表达重复复合组，例如 SUBSCRIBE 中重复出现的 Topic Filter/Requested QoS pair。`location` 和 `representation` 是字符串，因此既能描述二进制位域，也能描述文本语法位置。

公共 `protocol_data_units[].layout` 描述所有类型共享的外层组成；`type_selectors[].types[].layout` 使用相同 component ID 精化某种消息类型的字段和出现次数。

## `type_selectors[]` 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 类型选择器的局部稳定 ID。 |
| `name` | 规范中的类型字段或选择规则名称。 |
| `selection_method` | `field_value`、`syntax`、`context`、`transport` 或 `other`。 |
| `field_ref` | `field_value` 选择方式读取的结构字段。 |
| `definition` | 如何选择消息类型。 |
| `source` | 类型目录或语法分支的原文追溯信息。 |
| `types` | 选择器定义的消息类型。 |

每个 `types[]` 条目记录稳定 `id`、选择值 `value`、名称、状态、定义、允许方向和 `field_overrides`。`field_overrides` 用于表达固定标志值或同一公共字段在不同消息类型中的不同解释，并可用可选 `source` 追溯该覆盖值；可选 `layout` 用于表达类型专属结构。

## 结构引用

结构引用使用 `/` 分隔的稳定 ID 路径：

```text
<pdu-id>[/<component-or-type-selector-id>[/<field-or-type-id>[/<component-id>[/<field-or-member-id>]]]]]
```

例如：

- `mqtt-control-packet/fixed-header/remaining-length`
- `mqtt-control-packet/control-packet-type/connect`
- `mqtt-control-packet/control-packet-type/publish/variable-header/topic-name`
- `mqtt-control-packet/control-packet-type/subscribe/payload/topic-filter-qos-pair/requested-qos`

`framing[].field_refs`、消息类型的 `field_overrides[].field_ref` 和需求的 `structure_references` 都使用这一格式。

## `requirements[]` 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 需求的稳定唯一 ID。Schema 不绑定 MQTT 前缀，因此可用于其他协议。 |
| `source` | 原文定位信息。 |
| `modality` | 原文约束强度，例如 MUST、SHOULD 或 INFORMATIVE。 |
| `statement` | 对原文规范语义的原子化表述；不得加入实现方式、产品选择或测试场景。 |
| `preconditions` | 原文中触发规则的前提条件。 |
| `constraints` | 原文支持的行为边界或细节，不用于重复结构表。 |
| `exceptions` | 原文明示的例外。 |
| `references` | 相关规范要求 ID。 |
| `structure_references` | 可选的结构路径，用于连接行为规则与相关 PDU、字段、类型选择器或消息类型。 |
| `confidence` | 抽取和结构化的可信度，范围为 `0` 到 `1`。 |

## `source` 字段

| 字段 | 含义 |
| --- | --- |
| `section` | 规范章节或条款。 |
| `page` | 可选的 PDF 页码；没有稳定页码的 HTML 或文本规范可以省略。 |
| `locator` | 行号、段落号、锚点或其他可复现位置。 |
| `excerpt` | 指定位置的相关原文摘录。 |

空数组表示规范没有记录相应项目，不使用 `null`。`references` 中的值必须是合法规范要求 ID，并且不能重复。
