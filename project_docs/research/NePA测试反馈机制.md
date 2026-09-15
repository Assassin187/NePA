# NePA 测试反馈机制

## 1. 目标

NePA 的测试反馈应划分为三个职责明确的层级：

- **L1 Build Gate**：保证代码能够编译、链接并基本运行。
- **L2 Development Tests**：为生成 Agent 提供低成本、可迭代的开发反馈。
- **L3 Acceptance**：使用人工预先固定的隐藏测试，对最终产物进行独立评价。

最关键的边界是：当前 `acceptance` 应明确定位为 **L3 最终验收**，不再同时承担开发测试和最终评价。

## 2. 三层测试的职责

### 2.1 L1：Build Gate

L1 由系统固定执行，包括：

- 编译和链接；
- release 与 Sanitizer 构建；
- 基本启动检查；
- 必要的产物存在性检查。

L1 的执行过程和结果对模型可见。失败后，模型可以根据编译器、链接器、Sanitizer 或启动诊断继续修复。

L1 只证明工程具备基本可运行条件，不证明协议行为正确。

### 2.2 L2：Development Tests

L2 是模型根据当前任务负责的 requirements 自行生成并运行的局部开发测试：

- 测试源码对模型可见；
- 测试结果对模型可见；
- 失败后允许模型分析、修改代码并重新测试；
- 主要用于发现明显的局部实现错误，而不是证明完整协议符合性。

例如，当前任务负责：

```text
REQ-FRAME-001
REQ-FRAME-002
REQ-FRAME-003
REQ-FRAME-004
```

模型可以生成以下局部测试：

```text
正常完整 frame
分片 frame
多个 frame 粘包
非法 Remaining Length
```

L2 可以不完整。它的目的，是给 Agent 提供快速、低成本的反馈信号，而不是独立证明所有 requirements 均已满足。

### 2.3 L3：Acceptance

L3 是人工预先准备并冻结的完整验收测试：

- 模型不可读取测试源码、测试向量、具体 argv 或 oracle；
- 模型不可修改验收资产；
- 测试包含正常、异常、边界和组合行为；
- 测试明确映射到 Spec IR requirements；
- 只在所有任务完成后用于最终评价；
- 默认不进入修复循环。

现有资产可以继续作为 L3 使用，例如：

```text
gold_file/mqtt/acceptance.json
gold_file/mqtt/acceptance/mqtt_smoke.py
gold_file/mqtt/acceptance/mqtt_behavior.py
```

这些资产的角色应统一为 **Frozen Hidden Acceptance Suite**。

L3 不必立即覆盖 110/110 条需求，但必须准确报告实际覆盖范围，例如：

```text
Acceptance covers 51 / 110 requirements.
```

后续可以逐步扩充覆盖率，不能把未覆盖需求描述为已经验证。

## 3. 推荐执行流程

```text
Task requirements
        │
        ▼
      写代码
        │
        ▼
 L1 Build Gate
        │
  失败 ─┴─→ 分析并修复 ─┐
        │               │
      通过              │
        ▼               │
 生成并运行 L2 tests    │
        │               │
  失败 ─┴─→ 分析并修复 ─┘
        │
      通过
        ▼
   当前 Task 完成
        │
        ▼
   所有 Task 完成
        │
        ▼
 L3 Hidden Acceptance
        │
   ┌────┴────┐
   ▼         ▼
 PASS       FAIL
 最终成功   记录最终失败
```

一句话概括：

> L1 保证代码能跑，L2 帮助模型把代码写对，L3 独立判断代码到底对不对。

## 4. L2 应采用结构化测试能力

对于 GPT-5.6 等强模型，直接根据 requirements 编写测试通常可行。但 NePA 还计划支持 GLM、DeepSeek、Kimi 等不同能力的模型，不应要求模型每次都从头完成以下全部工作：

```text
读取 requirement
→ 设计测试策略
→ 搭建测试框架
→ 编写 socket harness
→ 实现结果判定 oracle
```

更合适的方式是由系统提供固定、通用的测试工具，模型只负责生成具体测试案例。例如：

```text
start_server()
connect_client()
send_bytes()
recv_packet()
expect_bytes()
expect_close()
expect_timeout()
sleep()
```

模型根据当前 requirement 描述测试步骤：

```text
REQ-PING-001

1. 建立连接
2. 完成 CONNECT
3. 发送 PINGREQ
4. 断言收到 PINGRESP
```

这样可以降低测试基础设施对模型能力的要求，使较弱模型也能得到有效的开发反馈。

这些工具必须保持协议无关；具体报文和预期行为应来自当前 Spec IR 和任务 requirements，而不是写死在 NePA 运行时中。

## 5. L2 与 L3 的隔离边界

L2 测试自然覆盖到与 L3 相同的 requirement 并不构成泄露。例如，Spec IR 明确规定：

```text
PINGREQ 必须返回 PINGRESP
```

模型据此生成：

```text
PINGREQ → expect PINGRESP
```

这是从规范独立推导测试，不是复制隐藏验收。

真正需要禁止的是：

- 读取 L3 的测试脚本；
- 读取 L3 manifest 中的具体 argv、测试向量或 oracle；
- 读取隐藏测试的详细失败信息；
- 通过路径、日志、历史记录或打包产物间接恢复 L3 内容；
- 修改 L3 资产以改变最终评价结果。

因此，L2 可以测试同一个 requirement，但不能以 L3 的实现细节作为开发输入。

## 6. L3 失败反馈策略

### 6.1 科研模式

科研主实验中，L3 最好只返回：

```text
final_acceptance: PASS
```

或：

```text
final_acceptance: FAIL
```

随后结束运行。不要向生成 Agent 返回以下细粒度信息：

```text
pubsub timeout
invalid_utf8 failed
expected D0 00, got ...
```

否则模型可能针对最终隐藏测试继续修改，导致 L3 逐渐退化为开发测试，污染科研评价。

### 6.2 工程模式

如果工程使用场景更重视最终交付成功率，可以另设显式模式：

```text
research_mode:
    L3 hidden acceptance 不参与修复

engineering_mode:
    L3 失败可提供受限反馈并进入 final repair
```

两种模式的结果必须分开报告，不能把使用过 L3 反馈修复的运行作为纯隐藏验收结果。

## 7. 最终建议

NePA 应固定采用以下机制：

1. 系统执行 L1，向模型提供完整构建和基本运行诊断。
2. 模型围绕当前任务 requirements 生成并运行 L2 局部测试。
3. 系统提供协议无关的结构化测试工具，降低测试实现门槛。
4. L1 和 L2 共同构成正常开发修复循环。
5. 所有任务完成后，系统只运行一次冻结、隐藏的 L3 Acceptance。
6. 科研模式下，L3 只返回 PASS/FAIL，不进入修复循环。
7. 工程模式可以允许受限的最终修复，但必须单独标识和报告。
8. Acceptance 报告必须明确给出已覆盖和未覆盖的 requirements 数量。

对于支持 GLM、DeepSeek、Kimi 等非顶级模型的目标，重点不是向模型暴露更多隐藏测试信息，而是把 L2 建设成低门槛、结构化、可自主迭代的开发反馈环境。这样既能提高较弱模型的完成率，也能保持 L3 最终评价的独立性和科研可信度。
