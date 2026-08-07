"""资金升级回测单元测试（2026-08-08 老板拍板方案）

覆盖：可买池量化（108→168 元风险额对应的每股风险上限 1.08→1.68）/
payload 精简（DataFrame/mc 大对象不落 JSON）/
三组参数矩阵（5600 vs 8401.26 vs 8401.26+3000 注入，风险额 2% 比例）。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "项目"))

from 回测系统 import 资金升级回测_8401 as m


def test_buyable_pool_limits():
    """可买池量化：单笔风险额/100 = 每股风险上限（A: 5600×2%=112→1.12，B/C: 8401×2%=168→1.68）"""
    tr = pd.DataFrame({
        "trigger": [10.0] * 6,
        "stop": [9.5, 8.9, 8.5, 8.0, 7.0, 9.0],  # 每股风险 0.5/1.1/1.5/2.0/3.0/1.0
    })
    pool = m.buyable_pool(tr)
    a, bc = pool["A(5600×2%)"], pool["B/C(8401×2%)"]
    assert a["limit"] == 1.12 and bc["limit"] == 1.68
    # A 上限 1.12：风险 ≤1.12 的 = 0.5/1.0/1.1 → 3 笔
    assert a["n_signal"] == 3
    # B/C 上限 1.68：风险 ≤1.68 的 = 0.5/1.0/1.1/1.5 → 4 笔
    assert bc["n_signal"] == 4


def test_groups_matrix():
    """三组参数矩阵：A=5600×2%（112 元），B=8401.26×2%（168.03 元），C=8401.26×2%+月3000"""
    assert m.GROUPS == [("A", 5600.0, 0.0), ("B", 8401.26, 0.0), ("C", 8401.26, 3000.0)]
    assert abs(5600 * m.RISK_RATIO - 112.0) < 1e-9
    assert abs(8401.26 * m.RISK_RATIO - 168.0252) < 1e-3


def test_payload_slim():
    """payload 精简：equity/mc 不落 JSON，dd 去除 _curve（图已单独生成）"""
    g = {
        "overview": {"n_exec": 10},
        "equity": pd.DataFrame({"date": ["2024-01-01"], "balance": [5600.0]}),
        "mc": {"final_equities": [1.0]},
        "dd": {"max_dd": 100.0, "_curve": pd.DataFrame()},
        "rs": [1.0, -0.5],
    }
    slim = m._slim(g)
    assert slim["equity"] is None and slim["mc"] is None
    assert "_curve" not in slim["dd"]
    assert slim["dd"]["max_dd"] == 100.0
    assert slim["rs"] == [1.0, -0.5]


def test_draw_r_hist_smoke(tmp_path):
    """R 分布直方图冒烟：空组不崩、有数据组出图"""
    import matplotlib
    matplotlib.use("Agg")
    groups = {"A": {"rs": [1.0, -0.5, 0.3]}, "B": {"rs": []}}
    p = tmp_path / "r_hist.png"
    out = m.draw_r_hist(groups, p)
    assert out == str(p) and p.exists() and p.stat().st_size > 0
