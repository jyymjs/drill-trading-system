"""duckdb 仓库配置常量（T-017 P3）

路径约定：
- DB：交易部门/数据基础/data/t017_p2.duckdb（git 不入库，见交易部门/.gitignore）
- 运行态文件（state.json/日志/校验输出）：数据基础/data/duckdb_runtime/（git 不入库）
"""
from pathlib import Path

# ── 路径 ──
PACKAGE_DIR = Path(__file__).resolve().parent           # .../交易部门/数据基础/duckdb/
DATA_BASE_DIR = PACKAGE_DIR.parent                      # .../交易部门/数据基础/
DB_PATH = DATA_BASE_DIR / "data" / "t017_p2.duckdb"
RUNTIME_DIR = DATA_BASE_DIR / "data" / "duckdb_runtime" # state/日志/校验输出

# ── 通达信服务器（P1/P2 实测仅此 2 台可用，必须传 (ip, port) 元组）──
SERVERS = [("180.153.18.170", 7709), ("60.191.117.167", 7709)]

# ── 拉取参数（沿用 P2 模式）──
WORKERS = 8          # 并发线程数（每线程独立连接）
MAX_RETRY = 3        # 单只最多重试次数
BACKOFF = [2, 5, 10] # 退避秒数（第 1/2/3 次尝试前）
PAGE_SLEEP = 0.05    # 全量翻页间隔
INTER_STOCK_SLEEP = 0.1  # 单只完成后的限速间隔（线程内）

# ── 增量窗口 ──
# 每次拉最近 N 条日线（升序过滤后只 upsert 库中已有日期之后的数据）。
# 15 条 ≈ 3 周交易日，可覆盖常规长假（国庆/春节）后的首次增量。
INCR_OFFSET = 15

# ── 除权完整性校验 ──
# 启发式：相邻交易日 open/close 跳变幅度超过阈值、且当日无 category=1（除权除息）
# 记录的日期 → 疑似漏记除权（300093 金刚光伏 2025-11-20 个案：跳变 13.5%，仅有
# category 9/15 记录、无 category=1）。P2 报告第七节建议阈值 5%。
XDXR_JUMP_THRESHOLD = 0.05

# ── 日终对账 ──
RECON_SAMPLE = 50          # 抽样只数
RECON_MUST = ["000651", "600519"]  # 强制纳入的疑难案例（P1/P2 遗留）
RECON_ALERT_PCT = 0.5      # 单只晚期中位误差 > 0.5% 报警
RECON_SEED = 42            # 固定随机种子，抽样可复现
RECON_ERA_CUT = "2006-01-01"  # 早期/晚期分界（回测默认区间 ≥2006）

# ── 次新股新浪补齐（P2 失败 7 只）──
SINA_FALLBACK_CODES = ["301655", "301707", "301717", "603468", "688826", "688828", "688836"]
