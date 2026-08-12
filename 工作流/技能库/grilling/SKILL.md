---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview me relentlessly about every aspect of this until we reach a shared understanding. Walk down each branch of the decision tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time, waiting for feedback on each question before continuing. Asking multiple questions at once is bewildering.

If a *fact* can be found by exploring the environment (filesystem, tools, etc.), look it up rather than asking me. The *decisions*, though, are mine — put each one to me and wait for my answer.

Do not act on it until I confirm we have reached a shared understanding.

## with-docs 变体（原 grill-with-docs）

在拷问过程中**实时产出文档**，适合设计阶段：

1. 每个被确认的术语 → 写入 `CONTEXT.md` 词条
2. 每个被确认的架构决策 → 写入 `docs/adr/` 下的 ADR
3. 拷问结束时文档与结论同步就绪，无需事后补记
