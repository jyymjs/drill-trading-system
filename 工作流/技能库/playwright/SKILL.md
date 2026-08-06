---
name: playwright
description: 环境验证技能——质检部六轴第 6 轴。用 MCP 浏览器工具操作页面、Bash 运行命令行、真实数据验证功能。覆盖页面/CLI/数据三种验证分支。有 UI 页面→优先使用。
---

# 环境验证（六轴第 6 轴）

标准见 `工程部门/流程/测试/质检标准.md`（唯一事实源）。不是看代码，而是像真实用户一样运行程序、操作页面、检查交互结果。

## 何时使用 / 何时不用

- ✅ 有 UI 页面 → 浏览器验证（本技能页面分支）
- ✅ 有命令行程序 → Bash 运行验证（CLI 分支）
- ✅ 有数据/算法 → 真实数据验证（数据分支）
- ❌ 无运行入口（纯静态审查）→ 跳过此轴，不硬造运行场景
- ❌ 复杂自动化回归（多服务启动+脚本断言）→ 用 webapp-testing 技能（with_server.py）

## 三分支验证

### 分支 A：页面（浏览器交互）

1. **启动应用**：先 Bash 启动 dev server / 本地服务，确认端口可访问；多服务场景（前后端）用 webapp-testing 的 `with_server.py` 管理生命周期
2. **打开页面**：`browser_navigate` 到本地 URL
3. **等待渲染**：动态应用先等 `browser_wait_for` 目标元素出现（等价 networkidle 等待），**不要在渲染完成前分析 DOM**
4. **识别选择器**：`browser_snapshot` 获取页面结构 → 从渲染结果确定操作目标（Reconnaissance-Then-Action：先侦察后行动）
5. **执行操作**：`browser_click` / `browser_type` / `browser_fill_form` / `browser_select_option` / `browser_press_key`
6. **检查结果**：`browser_snapshot` 验证状态变化，必要时 `browser_console_messages` 查报错、`browser_network_requests` 查请求
7. **截图留证**：`browser_take_screenshot` 保存（命名：`工程部门/支撑/文档/审计记录/{任务名}-{验证点}.png`）

**登录/鉴权**：有登录页时先确认测试账号（问用户或用 .env 测试凭据）；验证码/OTP 场景说明无法自动化，记录为"人工验证项"。

### 分支 B：CLI（命令行程序）

1. Bash 运行程序，记录完整命令
2. 检查**退出码**与**输出内容**（两者都要，只查一个会漏）
3. 边界输入：空参数 / 错误参数 / 超大输入 → 应有合理错误
4. 结果写入报告，附实际命令与输出片段

### 分支 C：数据（真实数据验证）

1. 用真实样本数据运行功能
2. 对比输出与人工预期（或与验收标准数值核对）
3. 异常数据（空值/异常值/边界值）→ 应被正确处理

## 验收标准 → 验证步骤转化示例

| 规划确认书验收标准（业务语言） | 验证步骤 |
|------------------------------|---------|
| "登录后能看到历史记录" | 输入账号→登录→断言历史记录区域出现 |
| "空表单提交会提示错误" | 不填→提交→断言错误提示文本 |
| "计算器输入负数返回 0" | 传 -5 → 断言返回 0 |

## 异常衔接

发现交互/运行异常 → 接 `diagnosing-bugs` 诊断回路（复现→根因→修复验证），不要在环境验证中直接修代码。

## 工具表（MCP）

| 工具 | 用途 |
|------|------|
| `browser_navigate` / `browser_navigate_back` | 打开页面 / 返回 |
| `browser_snapshot` | 页面无障碍快照（分析首选，优于截图） |
| `browser_find` | 文本/正则搜索页面元素 |
| `browser_click` / `browser_hover` / `browser_drag` / `browser_drop` | 交互操作 |
| `browser_type` / `browser_fill_form` / `browser_select_option` | 输入与选择 |
| `browser_press_key` | 键盘操作 |
| `browser_file_upload` | 文件上传 |
| `browser_handle_dialog` | 弹窗处理（alert/confirm） |
| `browser_wait_for` | 等待文本出现/消失 |
| `browser_take_screenshot` | 截图留证（仅用于报告，分析用 snapshot） |
| `browser_evaluate` | 执行 JS 表达式 |
| `browser_console_messages` / `browser_network_requests` / `browser_network_request` | 控制台/网络诊断 |
| `browser_tabs` / `browser_resize` | 标签页管理 / 视口调整 |

## 报告格式

```
## 环境验证
- ✅ 登录流程：输入账号密码→点击登录→成功跳转首页
- ❌ 空表单提交：未显示错误提示，直接提交了空数据
- ⚠️ 加载状态：数据加载时页面空白，无 loading 指示器
```
