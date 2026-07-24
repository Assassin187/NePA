# Gold Spec 字段说明

`mqtt-3.1.1-min-requirements.json` 记录从不可变 MQTT 3.1.1 规范源文档中抽取并人工确认的报文结构与原子规范条目。`packet_structure` 单独描述协议数据单元的组成，`requirements` 保存规范要求；两者都保留来源追溯信息，但不承载产品范围、MVP 决策、实现架构或验收测试要求。

## 文件级字段

| 字段 | 含义 |
| --- | --- |
| `$schema` | 声明该 JSON 应遵循的 Schema 地址，用于校验文件结构和字段类型。 |
| `gold_set_id` | 这组需求集的唯一标识，方便引用或版本化。 |
| `description` | 人类可读的内容说明以及该账本的职责边界。 |
| `source_sha256` | 原始 MQTT 规范文件的 SHA-256 哈希，用于确认需求来自哪一份不可变源文档。 |
| `packet_structure` | MQTT Control Packet 的结构定义，包括协议数据单元名称、来源以及有序组成部分。 |
| `requirements` | 原子规范条目对象数组；数组中的每个对象表示一条可独立追踪的规范语义。 |

## `packet_structure` 字段

| 字段 | 含义 |
| --- | --- |
| `protocol_data_unit` | 规范对协议数据单元的正式称呼；MQTT 中为 `MQTT Control Packet`。 |
| `definition` | 对整个报文结构的概括，仅描述规范定义，不描述实现数据结构。 |
| `source` | 报文结构定义的原文定位与摘录，结构与需求条目的 `source` 相同。 |
| `components` | 按线缆顺序排列的报文组成部分。 |

## `packet_structure.components[]` 字段

| 字段 | 含义 |
| --- | --- |
| `name` | 组成部分名称，例如 `Fixed header`。 |
| `order` | 组成部分在线缆格式中的顺序，从 `1` 开始。 |
| `presence` | `always` 表示所有报文都有，`conditional` 表示仅部分报文类型存在。 |
| `definition` | 规范对该组成部分的位置和作用的定义。 |
| `fields` | 该组成部分中由通用报文格式明确定义的字段；包类型专属内容可为空数组。 |

`fields[]` 使用 `name`、`location`、`representation` 和 `definition` 分别记录字段名称、位/字节位置、线缆表示和规范含义。

## `requirements[]` 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 需求的稳定唯一 ID，用于让代码、测试、证据和依赖关系准确关联到这条需求。 |
| `source` | 原文定位信息对象，包含 `section`、`page`、`locator` 和 `excerpt` 四个字段。 |
| `modality` | 原文的约束强度。例如 `MUST` 表示强制要求，`REQUIRED` 表示不可省略的定义性约束。具体允许值由 Schema 定义。 |
| `statement` | 对原文规范语义的原子化表述；不得加入实现方式、产品选择或测试场景。 |
| `preconditions` | 原文中触发这条规则的前提条件；为空数组表示原文没有给出特定前提。 |
| `constraints` | 原文支持的边界或细节，例如 Remaining Length 最多使用四字节。 |
| `exceptions` | 原文明确给出的例外；为空数组表示原文没有记录例外。 |
| `references` | 原文涉及或与本条直接相关的 MQTT 规范要求 ID；被引用条目可以不在当前选取的最小集合中。 |
| `confidence` | 这条需求从原文抽取并结构化后的可信度评分，范围为 `0` 到 `1`；当前 `1.0` 表示人工确认度很高。 |

## `source` 字段

在当前 JSON 结构中，题目中所说的原文定位字段位于每条需求的 `source` 对象内：

| 字段 | 含义 |
| --- | --- |
| `section` | 规范章节。 |
| `page` | PDF 页码，从 `1` 开始。 |
| `locator` | 文本行数或其他可复现的位置描述，例如 `lines 242-246`。 |
| `excerpt` | 从指定位置抽取的相关原文段落内容，用于支持该条需求的来源和解释。 |

空数组字段（`preconditions`、`constraints`、`exceptions`）应使用 `[]` 表示原文没有记录相应内容，不要使用 `null`。`references` 中的 ID 必须是 MQTT 规范要求 ID，并且不能重复。
