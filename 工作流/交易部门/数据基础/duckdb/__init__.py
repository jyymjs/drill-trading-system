"""duckdb 数据仓库模块（T-017 P3 增量固化）

职责：
- 每日盘后增量：mootdx 增量拉取 → upsert 进 duckdb（daily/xdxr）
- 除权完整性校验：价格跳变启发式，兜底通达信 xdxr 漏记（300093 类问题）
- 次新股新浪补齐：通达信未收录的 7 只新股用新浪源补齐
- 日终对账：与新浪抽样对照，质量闭环

模块组织：
- config.py        常量（DB 路径/服务器/参数）
- fetch.py         mootdx 取数（验活/增量窗口/全量 xdxr，重试+服务器切换）
- store.py         duckdb 建表/upsert/校验（去重、尾部缺口）
- update_daily.py  主入口：每日盘后增量 CLI
- xdxr_check.py    除权完整性校验（纯函数，可单测）
- sina_backfill.py 次新股新浪补齐
- recon.py         日终对账（抽样 50 只 vs 新浪）

P3 依据：老板 2026-08-05 确认执行（P2 全量报告第七节建议）。
"""
