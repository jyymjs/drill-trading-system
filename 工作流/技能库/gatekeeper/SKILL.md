---
name: gatekeeper
description: 提审前预检——提交审查前自跑 lint+测试，失败不浪费审查时间。
---

# 门禁预检技能

## 流程

1. 运行项目门禁预检（lint + 测试，Node→eslint、Python→ruff，测试→npm test/pytest）
2. 失败→AI 诊断摘要→修复→重跑
3. 全部通过→提交代码审查
