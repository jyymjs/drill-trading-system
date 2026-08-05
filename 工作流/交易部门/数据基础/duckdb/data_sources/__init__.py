"""data_sources 子包（T-017 P4 · a-stock-data 公告/快讯接入）

职责：
- 巨潮 cninfo：公告全文检索（标题/类型/日期/PDF 链接）+ 定期报告预约披露
  （预约披露日 = 财报日避让的核心数据，环境闸门 C1/C2 依赖）
- 财联社 cls.cn：全市场实时快讯（分钟级情绪数据，环境闸门 C4 依赖）

模块组织（与 P3 duckdb 模块风格一致）：
- config.py    常量（URL/UA/超时/分页/限速/抽样样本）
- cninfo.py    巨潮数据源：公告 + 预约披露（orgId 动态映射 + 硬编码 fallback）
- cls.py       财联社快讯（v1 API + 本地签名，零 key）
- store.py     落库：announcements / prbook / news_flash 三表 schema + upsert
- update.py    增量更新入口（CLI，可单跑/组合跑，--db 覆盖主库或临时库）

P4 依据：老板 2026-08-05 确认继续执行 T-017 P4（估工报告 ② a-stock-data 接入）。
"""
