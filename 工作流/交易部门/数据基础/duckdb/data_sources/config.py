"""data_sources 配置常量（T-017 P4 · a-stock-data 公告/快讯接入）

数据源（2026-08-05 实测全部可用）：
- 巨潮 cninfo：公告全文检索（hisAnnouncement/query）+ 定期报告预约披露
  （getPrbookInfo，官方"预约披露"页面接口，全市场 5540 只/报告期）
- 财联社 cls.cn：全市场实时电报（v1/roll/get_roll_list，本地签名零 key）
"""

# ── 通用 ──
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
REQUEST_INTERVAL = 0.3  # 全局请求最小间隔秒数（对源站友好，防封）

# ── 巨潮 cninfo ──
CNINFO_ANN_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_ORGID_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_PRBOOK_URL = "http://www.cninfo.com.cn/new/information/getPrbookInfo"
CNINFO_TIMEOUT = 15
CNINFO_ANN_PAGE = 30   # 公告单页条数
PRBOOK_MARKET = "szsh"  # 预约披露市场（沪深两市）
PRBOOK_PAGE = 50        # 预约披露单页条数（接口上限）

# ── 财联社 cls.cn ──
CLS_URL = "https://www.cls.cn/v1/roll/get_roll_list"
CLS_TIMEOUT = 10
CLS_PAGE = 50           # 快讯单页条数（接口上限约 50）

# ── 公告拉取抽样样本（--announcements 不带 --symbols 时的默认范围）──
# 20 只沪深大盘蓝筹（覆盖深市 0/3 开头 + 沪市 6 开头），验证与日常抽样用
SAMPLE_SYMBOLS = [
    "000651", "600519", "000001", "600036", "601318", "000002", "600900",
    "601398", "000858", "600030", "601166", "000333", "600887", "601288",
    "000725", "600276", "601988", "000063", "600050", "601601",
]
