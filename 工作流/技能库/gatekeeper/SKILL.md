---
name: gatekeeper
description: 提审前预检——工程部在提交审查前自跑 lint+测试，失败不浪费质检部时间。工程部角色专属。
---

# 门禁预检技能

## 流程

1. 运行 `工程部门/支撑/脚本/门禁/门禁预检.sh`（脚本按项目类型自适应：Node→eslint、Python→ruff，测试→npm test/pytest）
2. 失败→AI 诊断摘要→修复→重跑
3. 全部通过→提交质检部
