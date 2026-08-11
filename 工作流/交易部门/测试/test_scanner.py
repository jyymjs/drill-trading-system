"""扫描流程单元测试

2026-08-06 首次实战扫描发现 2 个缺陷，老板拍板"不执行，先完善优化事宜"：
- A1: ST/*ST 一票否决未接入扫描流程——候选混入 ST 股（600079 当日 S 级）
- A2: 已突破（现价≥触发价）也输出为 prebreak 候选——挂单立即成交=追高
"""
import os
import sys
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

# 确保能导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from 分析决策.分析 import scanner
from 数据基础.配置.stock_pool import is_st_name
from 策略.核心策略.base import BaseStrategy


class FakeStrategy(BaseStrategy):
    """测试桩策略：可控 match / trigger_price / stop_loss（避免依赖真实策略与K线形态）"""
    name = "测试桩策略"
    description = "扫描流程测试专用"
    required_indicators: ClassVar[list[str]] = []

    def __init__(self, trigger_price: float = 10.0, match: bool = True,
                 stop_loss: float = 9.0):
        self._trigger = trigger_price
        self._match = match
        self._stop = stop_loss

    def quick_prefilter(self, df: pd.DataFrame) -> bool:
        return True

    def prebreak_grade(self, df: pd.DataFrame) -> dict:
        return {
            "match": self._match, "grade": "S",
            "trigger_price": self._trigger,
            "stop_loss": self._stop,
            "risk_per_share": round(self._trigger - self._stop, 2),
            "ty_high": 10.0, "ty_low": self._stop,
        }

    def grade(self, df: pd.DataFrame) -> dict:
        return {"match": self._match, "grade": "S"}

    def filter(self, df: pd.DataFrame) -> bool:
        return True


def make_kline(n: int = 100, last_close: float = 10.0) -> pd.DataFrame:
    """生成模拟 K 线（桩策略覆盖 quick_prefilter，无需真实形态）

    开盘 = 收盘×0.985：保证实体占比（px = 0.575 ≥ G8 预筛阈值 0.35），
    避免像素感预筛（G8 2026-08-06）误伤测试桩（开=收 的零实体数据
    会被判"像素感严重"→ px 0.2 < 0.35 排除）。
    """
    closes = np.linspace(8.0, last_close, n)
    return pd.DataFrame({
        "日期": pd.bdate_range("2025-01-01", periods=n),
        "开盘": closes * 0.985,
        "收盘": closes,
        "最高": closes * 1.01,
        "最低": closes * 0.99,
        "成交量": np.full(n, 1e6),
        "涨跌幅": np.zeros(n),
        "换手率": np.zeros(n),
    })


# ============ A1: ST/*ST 一票否决 ============

@pytest.mark.parametrize("name", [
    "ST 中昌", "ST中昌", "*ST 美尚", "*ST美尚",
    "SST 某某", "S*ST 某某", "NST 某某", "st 小写",
])
def test_is_st_name_positive(name):
    """A1: 各种 ST 名称格式（带/不带空格、前缀变体）应判为 ST"""
    assert is_st_name(name)


@pytest.mark.parametrize("name", [
    "贵州茅台", "平安银行", "纳指ETF", "东方财富", "中远海控", "", None,
])
def test_is_st_name_negative(name):
    """A1: 正常名称（含 ETF）不应误伤"""
    assert not is_st_name(name)


def test_scan_excludes_st_stocks(monkeypatch):
    """A1: scan() 池级过滤——ST 股票不进入扫描候选（也不发起拉取）"""
    called = []
    stocks = [
        {"code": "600001", "name": "ST 中昌"},
        {"code": "600002", "name": "贵州茅台"},
        {"code": "600003", "name": "*ST 美尚"},
    ]
    monkeypatch.setattr(scanner, "get_all_stocks", lambda: stocks)

    def fake_single(stock, strategy, years, mode):
        called.append(stock["code"])
        return {"code": stock["code"], "name": stock["name"], "price": 10.0}

    monkeypatch.setattr(scanner, "scan_single_stock", fake_single)
    results = scanner.scan(FakeStrategy(), max_workers=4, show_progress=False)

    codes = [r["code"] for r in results]
    assert "600001" not in codes
    assert "600003" not in codes
    assert "600002" in codes
    # 池级过滤应在提交线程前完成——ST 股票不应被扫描
    assert "600001" not in called
    assert "600003" not in called


# ============ A2: 已突破（现价≥触发价）过滤与标注 ============

def test_scan_single_stock_marks_broken(monkeypatch):
    """A2: 现价≥触发价 → 标注"已突破"（挂单会立即成交=追高）"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=10.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0), mode="prebreak")
    assert entry is not None
    assert entry["触发价"] == 10.0
    assert entry["突破状态"] == "已突破"


def test_scan_single_stock_marks_not_broken(monkeypatch):
    """A2: 现价<触发价 → 标注"未突破"（保留为挂单候选）"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0), mode="prebreak")
    assert entry is not None
    assert entry["突破状态"] == "未突破"


def test_scan_single_stock_trigger_zero_not_broken(monkeypatch):
    """A2: 触发价无效(0)时不应误判为已突破"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=10.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=0.0), mode="prebreak")
    assert entry is not None
    assert entry["突破状态"] == "未突破"


# ============ T-020: P2 放量阈值标注 ============

def test_scan_single_stock_prebreak_volume_threshold(monkeypatch):
    """T-020（R-070 拍板 1.2）: prebreak 候选输出放量阈值 = 前20日均量×1.2
    （不含最新日；dn_confirm 触发日 1.5 保持——双阈值区分）"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0), mode="prebreak")
    assert entry is not None
    # make_kline 成交量恒 1e6 → 前20日均量 1e6 → 放量阈值 = 1.2e6（VOL_MIN）
    assert entry["放量阈值"] == 1200000.0


def test_scan_single_stock_volume_threshold_min_bars(monkeypatch):
    """T-020（R-070 1.2）: 恰好 60 根（扫描最低门槛）时放量阈值仍正常（前20根均量×1.2）"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(n=60, last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0), mode="prebreak")
    assert entry is not None
    # 60 根 → 不含最新日 20 根均量 1e6 → 阈值 1.2e6
    assert entry["放量阈值"] == 1200000.0


def test_scan_single_stock_normal_no_volume_threshold(monkeypatch):
    """T-020: 放量阈值仅 prebreak 模式输出（normal 模式不含该字段）"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0), mode="normal")
    assert entry is not None
    assert "放量阈值" not in entry


# ============ T-022: 当日扫描去重 ============

def test_cmd_scan_skips_when_today_report_exists(monkeypatch, capsys):
    """T-022: 当日已产出扫描报告 → cmd_scan 直接跳过（不执行扫描）"""
    import main as main_mod

    class Args:
        strategy = "fake"
        mode = "prebreak"
        max_price = None

    scanned = []
    monkeypatch.setattr(main_mod, "_scan_report_already_today", lambda: "scan_result_20260806_031609.csv")
    monkeypatch.setattr(main_mod, "_load_strategy", lambda name: FakeStrategy())
    monkeypatch.setattr(main_mod, "scan", lambda strategy, mode="normal": scanned.append(1) or [])

    main_mod.cmd_scan(Args())

    assert scanned == []  # 未触发扫描
    out = capsys.readouterr().out
    assert "跳过重复扫描" in out


def test_cmd_scan_runs_when_no_today_report(monkeypatch, capsys):
    """T-022: 当日无报告 → 正常执行扫描（幂等判断放行）"""
    import main as main_mod

    class Args:
        strategy = "fake"
        mode = "normal"
        max_price = None

    scanned = []
    monkeypatch.setattr(main_mod, "_scan_report_already_today", lambda: None)
    monkeypatch.setattr(main_mod, "_load_strategy", lambda name: FakeStrategy())
    monkeypatch.setattr(main_mod, "scan", lambda strategy, mode="normal": scanned.append(1) or [])
    monkeypatch.setattr(main_mod, "save_results", lambda r, suffix="": None)
    monkeypatch.setattr(main_mod, "print_results", lambda r, mode="normal": None)
    monkeypatch.setattr(
        "分析决策.风控.trade_guardian.discipline_report", lambda: "")

    main_mod.cmd_scan(Args())

    assert scanned == [1]  # 扫描执行了一次


def test_scan_prebreak_marks_and_split(monkeypatch):
    """A2 端到端: scan prebreak——已突破被拆分，不进候选主表"""
    stocks = [{"code": f"60000{i}", "name": f"测试股{i}"} for i in range(1, 4)]

    def fake_single(stock, strategy, years, mode):
        # 600001 已突破（现价≥触发价），其余未突破
        last_close = 10.5 if stock["code"] == "600001" else 9.5
        return {
            "code": stock["code"], "name": stock["name"],
            "price": last_close, "触发价": 10.0, "评级": "S",
            "止损价": 9.0, "每股风险": 1.0, "TY高": 10.0, "TY低": 9.0,
            "突破状态": "已突破" if last_close >= 10.0 else "未突破",
        }

    monkeypatch.setattr(scanner, "get_all_stocks", lambda: stocks)
    monkeypatch.setattr(scanner, "scan_single_stock", fake_single)
    results = scanner.scan(
        FakeStrategy(trigger_price=10.0), max_workers=4,
        mode="prebreak", show_progress=False)

    candidates, broken = scanner.split_prebreak_results(results)
    # 线程池 as_completed 完成顺序不定，用排序后比较
    assert sorted(r["code"] for r in candidates) == ["600002", "600003"]
    assert sorted(r["code"] for r in broken) == ["600001"]


def test_split_prebreak_results_empty():
    """A2: 空结果拆分安全"""
    assert scanner.split_prebreak_results([]) == ([], [])


def test_cmd_scan_prebreak_splits(monkeypatch):
    """A2: cmd_scan prebreak——已突破行单独打印+保存（不进候选主表）"""
    import main as main_mod

    class Args:
        strategy = "fake"
        mode = "prebreak"
        max_price = None

    results_all = [
        {"code": "600001", "name": "已突破股", "price": 10.5, "触发价": 10.0,
         "评级": "S", "突破状态": "已突破", "C23": "达标", "C23原因": ""},
        {"code": "600002", "name": "未突破股", "price": 9.5, "触发价": 10.0,
         "评级": "S", "突破状态": "未突破", "C23": "达标", "C23原因": ""},
    ]
    monkeypatch.setattr(main_mod, "_load_strategy", lambda name: FakeStrategy())
    monkeypatch.setattr(main_mod, "scan", lambda strategy, mode="normal": results_all)
    # T-022 当日去重隔离（2026-08-07 修复）：真实环境下当日 scan_result 文件
    # 存在会触发去重提前 return，测试必须 mock 掉——否则与真实环境耦合
    monkeypatch.setattr(main_mod, "_scan_report_already_today", lambda: None)

    saved = []
    printed = []
    monkeypatch.setattr(
        main_mod, "save_results",
        lambda r, suffix="": saved.append(([x["code"] for x in r], suffix)))
    monkeypatch.setattr(
        main_mod, "print_results",
        lambda r, mode="normal": printed.append([x["code"] for x in r]))
    monkeypatch.setattr(
        "分析决策.风控.trade_guardian.discipline_report", lambda: "")

    main_mod.cmd_scan(Args())

    # 候选主表：只有未突破
    assert printed[0] == ["600002"]
    assert saved[0] == (["600002"], "")
    # 已突破：单独打印 + _broken 后缀保存（供研究）
    assert printed[1] == ["600001"]
    assert saved[1] == (["600001"], "_broken")


# ============ C23: 动量≤10% + 止损距离 0.5~3 元（2026-08-06 老板拍板替换进策略） ============

def _expected_mom20(n: int, last_close: float, trigger: float) -> float:
    """与 scanner 同法计算期望动量：trigger / iloc[-21] 收盘 - 1"""
    closes = np.linspace(8.0, last_close, n)
    return trigger / closes[-21] - 1.0


def test_scan_single_stock_c23_momentum_field(monkeypatch):
    """C23: prebreak 候选输出 动量20日% = 触发价 vs 20交易日前收盘涨幅（对齐 tighten_compare）"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0), mode="prebreak")
    assert entry is not None
    expected = _expected_mom20(100, 9.5, 10.0)
    assert entry["动量20日%"] == pytest.approx(round(expected * 100, 1))


def test_scan_single_stock_c23_pass(monkeypatch):
    """C23: 动量≤10% 且 止损距离 0.5~3 元 → 达标（挂单候选主表）"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0, stop_loss=9.0),  # 止损距离 1.0 元
        mode="prebreak")
    assert entry is not None
    assert entry["C23"] == "达标"
    assert entry["C23原因"] == ""


def test_scan_single_stock_c23_reject_momentum(monkeypatch):
    """C23: 动量>10% → 不达标（原因含动量）——触发价远离 20 日前收盘=追高"""
    # last_close=8.2（接近起点 8.0）→ iloc[-21] ≈ 8.16 → 动量 ≈ 22.6% > 10%，
    # 恒走拒绝分支（质检建议级修复：旧版 if/else 弱断言未锁定前提）
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=8.2))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0), mode="prebreak")
    assert entry is not None
    expected = _expected_mom20(100, 8.2, 10.0)
    assert expected > scanner.C23_MOM_MAX  # 前提锁定：22.6% > 10%，必走拒绝分支
    assert entry["C23"] == "不达标"
    assert "动量" in entry["C23原因"]


def test_scan_single_stock_c23_reject_stop_near(monkeypatch):
    """C23: 止损距离<0.5 元 → 不达标（原因含止损）——太近易被扫"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0, stop_loss=9.6),  # 止损距离 0.4 元
        mode="prebreak")
    assert entry is not None
    assert entry["C23"] == "不达标"
    assert "止损0.40元<0.5" in entry["C23原因"]


def test_scan_single_stock_c23_reject_stop_far(monkeypatch):
    """C23: 止损距离>3 元 → 不达标（原因含止损）——太远盈亏比差"""
    monkeypatch.setattr(
        scanner, "get_daily_kline",
        lambda code, use_cache=True: make_kline(last_close=9.5))
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "贵州茅台"},
        FakeStrategy(trigger_price=10.0, stop_loss=6.5),  # 止损距离 3.5 元
        mode="prebreak")
    assert entry is not None
    assert entry["C23"] == "不达标"
    assert "止损3.50元>3" in entry["C23原因"]


def test_apply_c23_filter_splits():
    """C23: apply_c23_filter 拆分（达标→主表，不达标→研究列表）"""
    rows = [
        {"code": "600001", "C23": "达标", "C23原因": ""},
        {"code": "600002", "C23": "不达标", "C23原因": "动量12.3%>10%"},
        {"code": "600003", "C23": "不达标", "C23原因": "止损0.40元<0.5"},
    ]
    passing, filtered = scanner.apply_c23_filter(rows)
    assert [r["code"] for r in passing] == ["600001"]
    assert [r["code"] for r in filtered] == ["600002", "600003"]


def test_apply_c23_filter_empty():
    """C23: 空结果拆分安全"""
    assert scanner.apply_c23_filter([]) == ([], [])


def test_cmd_scan_prebreak_c23_filter(monkeypatch):
    """C23 端到端: cmd_scan prebreak——C23 不达标单独打印+保存（_c23 后缀），主表只留达标"""
    import main as main_mod

    class Args:
        strategy = "fake"
        mode = "prebreak"
        max_price = None

    results_all = [
        {"code": "600001", "name": "达标股", "price": 9.5, "触发价": 10.0,
         "评级": "S", "突破状态": "未突破", "C23": "达标", "C23原因": ""},
        {"code": "600002", "name": "追高股", "price": 9.5, "触发价": 10.0,
         "评级": "S", "突破状态": "未突破", "C23": "不达标", "C23原因": "动量15.0%>10%"},
        {"code": "600003", "name": "已突破股", "price": 10.5, "触发价": 10.0,
         "评级": "S", "突破状态": "已突破", "C23": "达标", "C23原因": ""},
    ]
    monkeypatch.setattr(main_mod, "_load_strategy", lambda name: FakeStrategy())
    monkeypatch.setattr(main_mod, "scan", lambda strategy, mode="normal": results_all)
    # T-022 当日去重隔离（2026-08-07 修复）：与真实环境解耦（同
    # test_cmd_scan_prebreak_splits——当日 scan_result 文件存在会提前 return）
    monkeypatch.setattr(main_mod, "_scan_report_already_today", lambda: None)

    saved = []
    printed = []
    monkeypatch.setattr(
        main_mod, "save_results",
        lambda r, suffix="": saved.append(([x["code"] for x in r], suffix)))
    monkeypatch.setattr(
        main_mod, "print_results",
        lambda r, mode="normal": printed.append([x["code"] for x in r]))
    monkeypatch.setattr(
        "分析决策.风控.trade_guardian.discipline_report", lambda: "")

    main_mod.cmd_scan(Args())

    # 打印顺序：C23 过滤名单 → 候选主表 → 已突破
    assert printed[0] == ["600002"]      # C23 不达标
    assert printed[1] == ["600001"]      # 主表：C23 达标且未突破
    assert printed[2] == ["600003"]      # 已突破
    assert saved[0] == (["600002"], "_c23")
    assert saved[1] == (["600001"], "")
    assert saved[2] == (["600003"], "_broken")


# ============ G8: 像素感池级预筛（2026-08-06） ============

def _px_kline(n: int = 80, body_ratio: float = 0.5) -> pd.DataFrame:
    """构造指定实体占比的 K 线（控制 pixelation_score）"""
    closes = np.linspace(8.0, 9.0, n)
    spread = 0.02  # 最高/最低波幅 ±1%
    return pd.DataFrame({
        "日期": pd.bdate_range("2025-01-01", periods=n),
        # 实体 = 收盘×(body_ratio)；影线 = (1-body_ratio)
        "开盘": closes * (1 - body_ratio * spread),
        "收盘": closes,
        "最高": closes * (1 + spread),
        "最低": closes * (1 - spread),
        "成交量": np.full(n, 1e6),
        "涨跌幅": np.zeros(n),
        "换手率": np.zeros(n),
    })


def test_px_prefilter_excludes_pixelated(monkeypatch):
    """G8: 像素感严重（px < 0.35）→ 池级预筛直接排除（不进入评级）"""
    from 分析决策.分析.indicators import pixelation_score
    df = _px_kline(body_ratio=0.1)  # 实体占比 10% → 长影线像素感
    assert pixelation_score(df) < scanner.G8_PX_THRESHOLD
    monkeypatch.setattr(scanner, "get_daily_kline", lambda code, use_cache=True: df)
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "像素股"}, FakeStrategy(), mode="prebreak")
    assert entry is None


def test_px_prefilter_passes_normal(monkeypatch):
    """G8: 像素感正常（px ≥ 0.35）→ 预筛放行，正常走评级"""
    from 分析决策.分析.indicators import pixelation_score
    df = _px_kline(body_ratio=0.9)  # 实体占比 90% → 正常
    assert pixelation_score(df) >= scanner.G8_PX_THRESHOLD
    monkeypatch.setattr(scanner, "get_daily_kline", lambda code, use_cache=True: df)
    entry = scanner.scan_single_stock(
        {"code": "600001", "name": "正常股"}, FakeStrategy(trigger_price=10.0),
        mode="prebreak")
    assert entry is not None
