# Phase 1 预注册

状态：已按冻结工件执行完成；结果见 `phase1-report.md`。

## 固定因素

- spec、target、test bundle、ArchitectureDraft Schema、validator：与基线 lineage 相同；
- Qwen/DeepSeek model identity、temperature=0、max_tokens=65536、context limits：与基线相同；
- N=3/模型、semantic repair depth=1；
- infrastructure-invalid 时整组双模型 arm 无效，不只补单模型；
- 所有 arm 使用新的独立实验根，不写入基线 lineage。

## Arms

1. E1.1-A：复用基线 V2 N=5 作为 control，不重复付费调用；
2. E1.1-B：只把 `prompt-control-v2.md` 换成 `prompt-exact-algorithm.md`；
3. E1.2：在 E1.1 胜出 prompt 上，只把现有 example 换成 `example-aligned.json`；
4. E1.3：E1.2 没有胜出，因此回到 E1.1 胜出配置，只向 planning input 添加 `deterministic-ledgers.json`；
5. E1.4：E1.3 没有胜出，因此回到 E1.1 胜出配置，通过仅限进程内的 diagnostic override 禁止 `LLMClient._fallback_request` 再附加 Schema，使最终 provider prompt 只含模板内的一份 Schema；不修改仓库实现。

主要判据完全沿用 `02-experiment-plan.md`，结果出来前不调整。

## 基础设施记录

2026-08-20 的最初 E0 blind-review Qwen、DeepSeek 调用因错误环境变量映射返回 HTTP 401。操作者随后明确 `ALI_API→NEPA_QWEN_API_KEY`、`DS_API→NEPA_DS_API_KEY`、`CLAUDE_API→NEPA_CLAUDE_API_KEY`；每次真实调用只在同一子进程中 export 映射，不输出值。Phase 1 arms 均没有 401、截断或身份异常。
