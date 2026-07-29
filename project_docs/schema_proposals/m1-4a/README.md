# M1-4a Schema 审批记录

状态：**APPROVED / MIGRATED**

2026-07-29，项目负责人批准四份提案及本文件十项裁决点，并明确本批开发
不得修改主设计文档。活动 Schema 与示例已迁入 `nepa/schemas/`；本目录仅
保留审批边界和裁决记录，不再被运行时消费。

本目录用于负责人审阅 M1-4a 尚未冻结的四类 Schema。这里的文件：

- 不属于主设计文档；
- 不属于活动 `nepa/schemas/`；
- 不得被运行时、测试收集器、S4 或任何正式 Run 消费；
- 不代表 prompt、validator 或预算已经冻结。

本次批准直接放行活动 Schema 与实现迁移；主设计文档保持不变。

## 文件

| Schema | 候选运行工件/输出 | 最小示例 |
| --- | --- | --- |
| `target-profile.schema.json` | `inputs/target.json` | `examples/target-profile.min.json` |
| `language-profile.schema.json` | `inputs/language.json` | `examples/language-profile.min.json` |
| `test-bundle.schema.json` | `inputs/test_bundle.json` | `examples/test-bundle.min.json` |
| `architecture-draft.schema.json` | ArchitecturePlanner 原始语义候选 | `examples/architecture-draft.min.json` |

示例刻意使用虚构的 `sample-wire`/`proto` 标识符，不冻结 MQTT 的 O-18 ABI，也不把协议专属名称写入通用 Schema。

## 共同原则

1. 所有对象使用 JSON Schema draft 2020-12，顶层及嵌套结构默认 `additionalProperties: false`。
2. 版本化源资产用 `asset.id/version` 标识；`source_ref` 绑定被解析的源 Profile。解析后工件自身的 canonical SHA-256 不写回自身，而由 Plan `input_refs`/Run receipt 外部锚定，避免自引用。
3. 所有路径都是运行资产根或仓库根下的安全相对路径；路径逃逸、反斜杠和绝对路径由 Schema 先拒绝，符号链接解析和规范化仍由确定性 lint 复核。
4. Schema 只验证结构、枚举和局部条件。id 唯一性、跨对象引用、集合等式、DAG、文件完整分区、REQ 唯一 primary、contract readiness 等必须由生产 lint/`ARCH_VALIDATE` 校验。
5. Profile 只承载交付与语言约束，不承载协议 wire facts、状态转移或测试步骤；协议事实仍只来自 Spec IR。

## 1. Target Profile 提案

职责边界：

- `deliverables`：选择要交付的 library/client/server/broker/proxy/CLI 等形态，并绑定公开 contract id；
- `naming`：冻结标识符、文件名和公共 include 前缀的机械命名规则；
- `templates`：每个专属模板以 id/version/path/SHA-256 冻结；
- `file_rules`：以数据驱动方式产生 `s5_frozen` 与 `s6_owned` 文件槽位；
- `external_contracts`：冻结 Test Manifest 可引用的公开 contract namespace、入口和最早可就绪阶段；
- `internal_interface_slots`：只声明结构性接口槽位和模板引用，不直接把槽位变成 Plan internal contract；
- `resource_limits`：冻结容量、单位和满载行为，供 O-18 一类有界资源决策使用。

`file_rules.expansion_source` 的候选集合为：

- `none`：单个固定路径；
- `spec_messages`：按 Spec message id 展开；
- `spec_roles`：按 Spec role id 展开；
- `deliverables`：按本 Profile 的 deliverable id 展开；
- `internal_interface_slots`：按接口槽位 id 展开。

路径模板中的 `{id}` 在 Delivery Compiler 中必须先按 `naming` 规则规范化再替换；Schema 不执行模板展开。

`file_rules` 是 Delivery Constraints 的唯一文件槽位来源。Language Profile
提供 `build_file_template` 的模板内容与哈希，但 Target Profile 仍必须用一条
`producer=language_template` 的 file rule 显式声明其目标路径和 mutability；
Delivery Compiler 禁止绕过 file rules 暗中增加 `Makefile` 等文件。

## 2. Language Profile 提案

职责边界：

- `language/platform`：语言标准、扩展和平台 API family；
- `toolchain`：构建系统、编译器及语言级构建文件模板；
- `build_variants`：每个变体的 argv、编译 flags、sanitizer 和是否为必跑变体；
- `file_types`：源文件/头文件扩展名及 include 目录；
- `type_mappings`：以 `spec_type_id`、`encoding_kind`、`shape` 的组合做协议中立类型降级；
- `runtime_constraints/style`：依赖、线程、动态分配、网络 API 与代码风格。

本提案禁止把 `mqtt_varint` 一类协议身份字符串固化在通用 Language Profile。默认 C 资产可用 `encoding_kind=varint` 表达机械映射；协议中自定义 type id 仍来自 Spec。

## 3. Test Bundle 描述提案

职责边界：

- `bundle_tree_sha256` 按设计文档的规范树哈希算法绑定完整测试资产；
- `manifest_ref` 独立绑定 Test Manifest v2 文件及 canonical SHA-256；
- `runner`、`oracle_refs`、`adapter_refs`、`reference_target_refs` 记录组件路径与内容哈希；
- `default_build_variant_ids` 绑定缺省构建变体；
- `planning_visibility` 把 S4 Agent 可见字段锁定为 Manifest 元数据白名单，并明确测试/runner/oracle/adapter 源码均不可见；
- `responsibilities` 固化黑盒、禁止 import 生成内部实现、随机种子和参考验证边界。

本批只提 Test Bundle **解析描述** Schema。它要求 `manifest_ref.schema_version=2.0`，但不在这里偷渡 Test Manifest v2 Schema；后者仍是单独的 M1-4a 活动资产迁移。

S4 可见的 Manifest 字段严格采用设计文档 6.4.7 白名单：
`nodeid/description/req_ids/gate/required_contracts/build_variant_ids`。
`layer` 不对任何 S4 LLM 角色可见；分层执行仍由确定性测试控制器消费完整
Manifest。

## 4. ArchitectureDraft 提案

顶层只允许：

```text
schema_version
architecture
work_packages
```

因此 Schema 直接拒绝最终 task id/instructions、`input_refs`、Blueprint hash、coverage、review、Plan State、S5 文件内容及运行状态。

`architecture` 包含：

- `decisions[]`：短设计决定及上下文引用；
- `assumptions[]`：结构化的保守假设、理由和上下文引用；
- `contracts[]`：外部/内部 contract 候选；
- `modules[]`：职责、non-goals、文件和 contract 集合。

`work_packages[]` 与 Plan v3 工作包语义同形，但不包含任务或测试 nodeid。

### task-ready contract 的中间表示

这是本提案最需要负责人确认的字段：

```json
{
  "ready_gate": "task",
  "provider_work_package_id": "wp-codec"
}
```

ArchitecturePlanner 运行时 TaskPlanner 尚未生成任务，因此它不可能诚实输出最终 `provider_task_id`。候选方案让 ArchitectureDraft 先唯一预留 provider work package；TaskPlanner 必须在该 shard 内选出恰一个 provider task，Linker 随后把它解析为正式 Plan v3 的 `provider_task_id`。`provider_work_package_id` 只存在于 S4 内部草稿，禁止进入正式 Plan。

## Schema 之外的生产校验

候选 `ARCH_VALIDATE` 至少还需要验证：

1. module/contract/work package id 分别唯一；
2. external contract id、ready gate、interface files 与 Target Profile 一致；
3. required internal interface slot 恰有一个匹配 internal contract；
4. `s5` contract 必须 owner=`s5` 且没有 provider work package；
5. `task` contract 的 owner module 与 provider work package module 一致；
6. module provides/consumes 等于其 work package 对应集合的并集；
7. work package `depends_on` 恰由跨包 task-ready contract provider/consumer 关系派生；
8. work package DAG 无环；
9. module/work package 文件集合是 Delivery Constraints 中 `s6_owned` 槽位的完整互斥分区；
10. 每条非 `DEFINITION` requirement 恰有一个 primary work package，supporting 分配无重复；
11. context ref、REQ、contract、module、文件槽位引用全部存在；
12. planning index、预计输出、模型上下文与截断状态满足预算门。

## 需要负责人裁决的提案点

1. 是否采用共同的 `asset: {id, version, description}` 包装，而不是顶层 `profile_id/profile_version`。
2. Target Profile 是否应同时冻结 external contract 的 `ready_gate`；当前提案要求 ArchitectureDraft 与其一致。
3. 是否接受数据驱动 `file_rules + expansion_source + path_template`，作为 Delivery Constraints 的唯一文件槽位来源。
4. 是否接受通用 `resource_limits.maximum/exhaustion_behavior` 表达 O-18 容量；具体 MQTT 值和 ABI 仍留给 O-18 单独裁决。
5. Language Profile 的构建命令是否保持 argv 数组；当前提案禁止 shell 字符串。
6. Test Bundle 是否必须至少有一个 oracle 和一个 generated-target adapter；当前提案均为 `minItems: 1`。
7. ArchitectureDraft assumption 是否采用结构化对象，而非 Plan 正文当前未定形状的纯字符串。
8. 是否批准 `provider_work_package_id → provider_task_id` 的两阶段 contract provider 解析。
9. work package `kind` 是否与 task 共用六值枚举；当前提案共用 `codec/state/logic/transport/app/integration`。
10. 是否允许无 requirement responsibility 的纯结构工作包；当前提案允许，`ARCH_VALIDATE` 仍要求所有非 DEFINITION REQ 有唯一 primary。

上述任一项未获批准前，不应开始 M1-4a 的正式 Schema、prompt 或生产 validator 冻结。
