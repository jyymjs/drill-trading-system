"""全局配置"""
from pathlib import Path

# 项目路径
ROOT_DIR = Path(__file__).resolve().parent.parent

# 数据缓存目录
DATA_DIR = ROOT_DIR / "data" / "cache"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 输出目录
OUTPUT_DIR = ROOT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- 股票池配置 ---
STOCK_LIST_CACHE_DAYS = 1   # 股票列表缓存天数

# --- K线配置 ---
KLINE_ADJUST = "qfq"        # akshare复权方式: ""=不复权, "qfq"=前复权, "hfq"=后复权
KLINE_CACHE_DAYS = 1        # K线缓存有效期（天）
KLINE_YEARS = 3             # 拉取年数（pytdx上限约800条≈3年，超过无效）

# --- 技术指标默认参数 ---
MA_SHORT = 5                # 短期均线
MA_MEDIUM = 20              # 中期均线
MA_LONG = 60                # 长期均线
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_PERIOD = 14
KDJ_N = 9
KDJ_M1 = 3
KDJ_M2 = 3
BOLL_PERIOD = 20
BOLL_STD = 2
VOLUME_MA_PERIOD = 5        # 均量线周期

# --- 扫描配置 ---
SCAN_MAX_WORKERS = 5        # 并发数（akshare限制）
SCAN_PROGRESS = True        # 显示进度条
SCAN_RETRY = 2              # 失败重试次数

# --- 日志 ---
LOG_LEVEL = "INFO"
LOG_FILE = ROOT_DIR / "output" / "trading.log"
