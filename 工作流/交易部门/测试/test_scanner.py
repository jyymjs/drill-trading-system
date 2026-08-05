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
    """测试桩策略：可控 match / trigger_price（避免依赖真实策略与K线形态）"""
    name = "测试桩策略"
    description = "扫描流程测试专用"
    required_indicators: ClassVar[list[str]] = []

    def __init__(self, trigger_price: float = 10.0, match: bool = True):
        self._trigger = trigger_price
        self._match = match

    def quick_prefilter(self, df: pd.DataFrame) -> bool:
        return True

    def prebreak_grade(self, df: pd.DataFrame) -> dict:
        return {
            "match": self._match, "grade": "S",
            "trigger_price": self._trigger,
            "stop_loss": 9.0, "risk_per_share": 1.0,
            "ty_high": 10.0, "ty_low": 9.0,
        }

    def grade(self, df: pd.DataFrame) -> dict:
        return {"match": self._match, "grade": "S"}

    def filter(self, df: pd.DataFrame) -> bool:
        return True


def make_kline(n: int = 100, last_close: float = 10.0) -> pd.DataFrame:
    """生成模拟 K 线（桩策略覆盖 quick_prefilter，无需真实形态）"""
    closes = np.linspace(8.0, last_close, n)
    return pd.DataFrame({
        "日期": pd.bdate_range("2025-01-01", periods=n),
        "开盘": closes,
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
         "评级": "S", "突破状态": "已突破"},
        {"code": "600002", "name": "未突破股", "price": 9.5, "触发价": 10.0,
         "评级": "S", "突破状态": "未突破"},
    ]
    monkeypatch.setattr(main_mod, "_load_strategy", lambda name: FakeStrategy())
    monkeypatch.setattr(main_mod, "scan", lambda strategy, mode="normal": results_all)

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
