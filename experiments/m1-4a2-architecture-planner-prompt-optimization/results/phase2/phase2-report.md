# Phase 2 结果：单调用与分阶段 call shape

日期：2026-08-20
状态：E2.1 已执行两次，但双模型比较因 Qwen Provider `Arrearage` 基础设施状态无效；E2.2 按预注册条件未触发。

## 1. 预注册比较

Arm M 复用 E1.1-B N=3：完整单次生成 + 最多一次全局语义修复。Arm S 保持相同精确算法 prompt、当前 example、原始输入、完整 Schema、模型参数和 validator，只把验证/修复检查点拆为 `arch_01–05`、`arch_01–09`、`arch_01–10` 三阶段。两组均要求 N=3/模型；任一基础设施无效时整组作废并以同配置重跑，不能只补失败模型。

## 2. Arm M 有效控制结果

| 模型 | p0 | 一次 repair 后 | 回归 | 最终 readiness |
|---|---:|---:|---:|---:|
| Qwen | 1/3 | 3/3 | 0 | 0 |
| DeepSeek | 3/3 | 3/3 | 0 | 0 |

由于两个模型的 M 最终已经 3/3，S 无法满足“两个模型最终全门通过数均高于 M”的预注册主判据。实验仍执行，用来观察阶段轨迹和冻结能力，但在执行前就不能据此把 H-COMPLEX 确认为主要原因。

## 3. S 首次运行：部分有效、整组无效

目录：`runs/e2-1-s-staged/`

| 模型/trial | Stage 1 | Stage 2 | Stage 3 |
|---|---|---|---|
| Qwen/001 | pass | pass | pass |
| Qwen/002 | fail `arch_05+10` | fail `arch_10` | pass |
| Qwen/003 | fail `arch_10` | HTTP 400 | 未执行 |
| DeepSeek/001 | pass | pass | pass |
| DeepSeek/002 | pass | pass | pass |
| DeepSeek/003 | fail `arch_10` | pass | pass |

有效轨迹没有 gate regression。Qwen/002 显示分阶段能够先闭合 module projection，再闭合 primary ownership；DeepSeek/003 在 Stage 2 即闭合一条 `arch_10` 漏项。可是 Qwen/003 Stage 2 的 HTTP 400 使整组按预注册无效，这些轨迹只能作为方向性观察。

阶段冻结也不完全可靠：Qwen/001 Stage 1 已全门通过，Stage 2 字节不变，但 Stage 3 在无 validation issue 时仍新增 3 条 supporting responsibility。DeepSeek/001–002 在无 issue 时保持字节不变。由此确认，单靠 prompt 声明“保持已通过门”不能实现强冻结；正式 B3 若需要强冻结，应使用部分 Schema/字段级组装或确定性边界，这属于另一个设计实验。

## 4. 完全同配置整组重跑

目录：`runs/e2-1-s-staged-rerun1/`

- Qwen：3 个 Stage 1 全部立即 HTTP 400，无可评价候选；
- DeepSeek：9/9 阶段调用有效，三个 trial 从 Stage 1 起均全门通过，最终 3/3，无回归；
- 因 Qwen 全组基础设施无效，重跑仍不能形成预注册所需的双模型比较。

随后用同一 key 做不含 ArchitecturePlanner 大输入的最小健康请求（`max_tokens=4`），同样返回 HTTP 400，错误类型和 code 均为 `Arrearage`。因此可确认本次 400 是 Qwen 账户欠费/状态异常，不是 prompt 大小、Schema 重复、模型输出或 staged 逻辑引起。证据见 `qwen-provider-health.json`，其中未保存 key。

## 5. 对 H-COMPLEX 的结论

### 已确认事实

1. 精确算法 prompt 的单调用 M 已在 N=3 双模型达到 repair 后 3/3，在 N=5 达到 Qwen/DeepSeek p1=5/5/5/5；任务不是“单调用天然不可完成”。
2. 有效 staged 轨迹能顺序闭合 `arch_05` 与 `arch_10`，但最终通过数不能高于已到天花板的 M。
3. staged 会把本来 1–2 次调用扩大到固定 3 次；对已经通过的候选，额外阶段可能无效重写 supporting responsibility。
4. 本次 E2.1 不能产生有效的双模型统计比较，因为 Qwen Provider 账户状态在运行中转为 `Arrearage`。

### 强推断

任务复杂度是 Qwen p0 稳定性的次要因素：精确 prompt N=5 中 Qwen p0 仍只有 2/5，而 DeepSeek 为 5/5；分阶段的局部反馈在观察到的 Qwen/002 上确实有序修复。但现有证据反对它是 change 失败的首要或不可绕过原因。

### 尚待验证

恢复 Qwen 账户后，以完全相同 `e2-1-s-staged-rerun1` 配置整组 N=3 重跑，才可量化 S 的阶段通过率、回归与调用成本。若要验证“真正 B3”而不是本次完整 Schema 分阶段修复，还需另行设计部分 Schema + 确定性组装实验；这超出当前 change 和本次最小根因区分所必需的范围。

## 6. E2.2 未执行的理由

E2.2 原计划仅在 E2.1 仍系统性卡在 projections、exact DAG 或 readiness 时触发。有效 M 已无最终卡点，DeepSeek 的有效 S 也无最终卡点；Qwen 的剩余问题是外部 `Arrearage`，不能用确定性闭包实验修复。因此条件未触发，未执行不是遗漏。
