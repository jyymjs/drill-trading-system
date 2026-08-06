"""0.5R 确认规则回放验证测试（2026-08-06 老板确认四连包 ③④）

覆盖：回放判定（确认/reject/止损/等待）/ 误杀率口径（不确认平仓后 20 天内
最高涨幅 ≥1R）/ 漏补率口径 / 机会成本（持有 20 天近似 R）/ R 档位分布。
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 回测系统.confirm_replay import (
    _post_close_nth,
    _post_exit_high,
    r_bucket_dist,
    replay_confirm,
)


def mk_kline(rows: list[tuple], start="2024-01-01") -> pd.DataFrame:
    """rows = [(开盘, 最高, 最低, 收盘, 成交量), ...]"""
    return pd.DataFrame({
        "日期": pd.bdate_range(start, periods=len(rows)),
        "开盘": [r[0] for r in rows],
        "最高": [r[1] for r in rows],
        "最低": [r[2] for r in rows],
        "收盘": [r[3] for r in rows],
        "成交量": [r[4] if len(r) > 4 else 1_000_000 for r in rows],
    })


def mk_signals(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("code", "grade", "entry_20d", "stop_loss", "stop",
                "risk", "r_20d"):
        if col not in df.columns:
            df[col] = ""
    if "mode" not in df.columns:
        df["mode"] = "prebreak"
    if "triggered_20d" not in df.columns:
        df["triggered_20d"] = 1
    return df


def kline_after_confirm_close(confirm_close: float):
    """构造：信号日 2024-01-02（触发价 10.5），触发日 01-03（最高≥触发价），确认日 01-04"""
    return mk_kline([
        (10.0, 10.3, 9.9, 10.1, 1_000_000),    # 2024-01-01（信号日前）
        (10.1, 10.4, 10.0, 10.2, 1_000_000),   # 2024-01-02 信号日（未触发）
        (10.2, 10.9, 10.1, 10.3, 1_000_000),   # 2024-01-03 触发日（最高 10.9 ≥ 10.5）
        (confirm_close, confirm_close + 0.3, confirm_close - 0.2, confirm_close, 1_000_000),  # 01-04 确认日
    ], start="2024-01-01")


class TestReplayVerdicts:
    def test_confirm_verdict(self):
        """确认日收盘 ≥ 进场价且 ≥ 开仓日收盘 → 确认"""
        k = kline_after_confirm_close(10.6)
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": 1.0}])
        r = replay_confirm(s, {"A": k})
        assert r["n_judged"] == 1
        assert r["n_confirm"] == 1 and r["confirm_rate"] == 1.0

    def test_reject_verdict_and_miss_rate(self):
        """确认日收盘跌破进场价 → reject；若后续 20 天最高 ≥ 1R → 误杀"""
        k = kline_after_confirm_close(10.3)  # 收盘 10.3 < 进场 10.5
        # 确认日之后 20 根：最高 12.5 → 涨幅 2.2 ≥ 风险 0.5 → 误杀
        tail = mk_kline([(12.0, 12.5, 11.5, 12.0, 1_000_000)] * 25,
                        start="2024-01-05")
        k = pd.concat([k, tail], ignore_index=True)
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": -0.5}])
        r = replay_confirm(s, {"A": k})
        assert r["n_reject"] == 1
        assert r["n_missed_kill"] == 1 and r["miss_rate"] == 1.0

    def test_reject_no_miss_when_flat(self):
        """确认日 reject 后 20 天最高涨幅 < 1R → 不算误杀"""
        k = kline_after_confirm_close(10.3)
        tail = mk_kline([(10.4, 10.7, 10.2, 10.5, 1_000_000)] * 25,
                        start="2024-01-05")   # 最高 10.7，涨幅 0.4 < 风险 0.5
        k = pd.concat([k, tail], ignore_index=True)
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": -0.5}])
        r = replay_confirm(s, {"A": k})
        assert r["n_missed_kill"] == 0 and r["miss_rate"] == 0.0

    def test_stop_verdict(self):
        """确认日最低 ≤ 止损价 → 层面1 止损（不计入 reject/确认）"""
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1_000_000),
            (10.1, 10.4, 10.0, 10.2, 1_000_000),
            (10.2, 10.9, 10.1, 10.3, 1_000_000),   # 触发日
            (10.0, 10.2, 9.4, 9.9, 1_000_000),      # 确认日最低 9.4 ≤ 止损 9.5
        ])
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 1.0,
                         "entry_20d": 10.5, "stop": 9.5, "r_20d": -1.0}])
        r = replay_confirm(s, {"A": k})
        assert r["n_stop"] == 1
        assert r["n_confirm"] == 0 and r["n_reject"] == 0

    def test_wait_when_no_trigger(self):
        """信号日后无触发（最高 < 触发价）→ 等待，不计入判定"""
        k = mk_kline([
            (10.0, 10.3, 9.9, 10.1, 1_000_000),
            (10.1, 10.4, 10.0, 10.2, 1_000_000),
            (10.2, 10.4, 10.1, 10.3, 1_000_000),   # 最高 10.4 < 触发 10.5
        ])
        s = mk_signals([{"code": "A", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": 0.0}])
        r = replay_confirm(s, {"A": k})
        assert r["n_judged"] == 0
        assert r["detail"]["verdict"].iloc[0] == "wait"

    def test_code_zfill_lookup(self):
        """4 位 code（历史去零格式）→ zfill(6) 后查 K 线缓存（key=原始 code）"""
        k = kline_after_confirm_close(10.6)
        s = mk_signals([{"code": "685", "date": "2024-01-02", "risk": 0.5,
                         "entry_20d": 10.5, "r_20d": 1.0}])
        r = replay_confirm(s, {"685": k})   # 缓存 key 为原始 code
        assert r["n_confirm"] == 1


class TestPostHelpers:
    def test_post_exit_high_excludes_from_date(self):
        k = mk_kline([(10, 11, 9, 10, 1)] * 5, start="2024-01-01")
        # 01-01 之后：01-02 起 4 根最高 = 11
        assert _post_exit_high(k, "2024-01-01") == 11.0

    def test_post_close_nth(self):
        k = mk_kline([(10, 11, 9, 10.5, 1)] * 25, start="2024-01-01")
        # 01-01 之后第 20 根收盘 = 10.5
        assert _post_close_nth(k, "2024-01-01") == 10.5


class TestRBucketDist:
    def test_buckets_and_10r_share(self):
        s = mk_signals([
            {"code": "A", "date": "2024-01-02", "r_20d": 12.0},
            {"code": "B", "date": "2024-01-02", "r_20d": 0.5},
            {"code": "C", "date": "2024-01-02", "r_20d": -1.5},
        ])
        d = r_bucket_dist(s)
        assert d["n"] == 3
        assert d["n_10r"] == 1 and d["pct_10r"] == pytest.approx(33.33, abs=0.01)
        assert d["buckets"]["10R+"] == 1
        assert d["buckets"]["-3~-1R"] == 1
        # 最大单笔 12 / 累计 11 → 100%+ 上限口径：12/11
        assert d["max_r"] == 12.0
        assert d["max_share_pct"] == pytest.approx(12 / 11 * 100, abs=0.1)
