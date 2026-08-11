#!/usr/bin/env python3
"""R-048 比例网格 + R-049 时间窗稳健性统一执行器（2026-08-11 · 交易部审核 v2 通过稿）

双子命令 + 公共组件：
  grid        比例网格 manifest（主网格 7档×4资金 + 精化 + 拐点窗 + 注入补测，分阶段生成）
  timewindow  时间窗 manifest（起点 6 + 滚动窗 + 制度 2 + 合并参考）
  run-one     单格执行器（batch 子进程入口；空窗/空成交安全路径）
  batch       批处理（每格独立 .json+.err，防并发写丢行；enrich 输出重定向抑制）
  collect     合并 jsonl + 枢轴表 + selfcheck 门禁（任一格失败标红停查）
  anchor      7 点锚点对账（r44 三点 + R-045 三单点 + 16k；偏差 >1pp 停跑）
  mc          蒙卡（候选档 × 10000 场景，复用 monte_carlo.simulate 纯函数）
  regime      宏观周期标注统计（B4：牛/熊/震荡占比 + 信号层 avgR/胜率）

口径（与 r44.run_one 同源，单一执行器）：
  - 回撤一律总资产口径（build_total_asset_curve）
  - avgR = mean(pnl/risk_actual)（逐笔实际风险额）
  - 排序 time（与 R-043/044/045 历史同源；risk_mid 为实盘执行卡口径，差异观察项）
  - min_date/max_date = 信号日期过滤（非资金起点）；max_date 透传解决 B2 跨窗出场截断
  - reasons 提取："资金不足"固定键；"每股风险*超限"动态键按前缀聚合

用法:
  python 项目/回测系统/r48_grid.py anchor
  python 项目/回测系统/r48_grid.py grid --phase main --out 实验/r48_ratio_grid
  python 项目/回测系统/r48_grid.py grid --phase refine --best <pivot.csv> --out ...
  python 项目/回测系统/r48_grid.py timewindow --out 实验/r48_timewindow
  python 项目/回测系统/r48_grid.py batch --manifest m.csv --workers 6 --out dir
  python 项目/回测系统/r48_grid.py run-one --cell G01 --manifest m.csv --out dir
  python 项目/回测系统/r48_grid.py collect --dir dir --pivot pivot.csv
  python 项目/回测系统/r48_grid.py mc --capital 8401 --risk-ratio 0.012855 --nsim 10000
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent          # 项目/回测系统
_ROOT = _HERE.parent.parent                      # 交易部门根
for p in (_HERE.parent, _ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pandas as pd  # noqa: E402
import numpy as np  # noqa: E402

from 回测系统.r44_position_grid import run_one as r44_run_one  # noqa: E402

DEFAULT_SIGNALS = _ROOT / "产出" / "输出" / "backtest_r43_t2" / "signals.csv"
DATA_END = "2026-08-10"   # 信号集数据末（B2 跨窗出场统一透传的"真实数据末"）

RATIOS = [0.008, 0.00857, 0.010, 0.012855, 0.016, 0.020, 0.025]
CAPITALS = [8401.0, 30000.0, 50000.0, 100000.0]
MAX_POS = 999   # 无限制（V3 现行）

# 7 点锚点对账（r44 三点 @8401 + R-045 三单点 + 16k 档；全部 time 排序）
ANCHORS_7 = [
    {"label": "5仓@8401", "capital": 8401.0, "risk_ratio": 0.012855, "max_positions": 5,
     "expect_ret": 391.0, "expect_dd": -10.3},
    {"label": "8仓@8401", "capital": 8401.0, "risk_ratio": 0.012855, "max_positions": 8,
     "expect_ret": 515.2, "expect_dd": -9.8},
    {"label": "无限制@8401", "capital": 8401.0, "risk_ratio": 0.012855, "max_positions": 999,
     "expect_ret": 629.6, "expect_dd": -11.0},
    {"label": "无限制@16k", "capital": 16000.0, "risk_ratio": 0.012855, "max_positions": 999,
     "expect_ret": 830.5, "expect_dd": -10.1},
    {"label": "无限制@30k", "capital": 30000.0, "risk_ratio": 0.012855, "max_positions": 999,
     "expect_ret": 942.2, "expect_dd": -12.2},
    {"label": "无限制@50k", "capital": 50000.0, "risk_ratio": 0.012855, "max_positions": 999,
     "expect_ret": 1026.0, "expect_dd": -11.1},
    {"label": "无限制@100k", "capital": 100000.0, "risk_ratio": 0.012855, "max_positions": 999,
     "expect_ret": 1107.9, "expect_dd": -11.7},
]

# 时间窗（B2）：(label, min, max, 信号数, 是否跑引擎)
WINDOWS = [
    ("w2001", "2001-01-01", "2004-01-01", 2, False),    # 近空窗 → 不跑，仅年度分布
    ("w2004", "2004-01-01", "2007-01-01", 0, False),    # 空窗（v2 更正：原稿 176 为 2014 错位）
    ("w2007", "2007-01-01", "2010-01-01", 3, False),    # 近空窗 → 不跑
    ("w2010", "2010-01-01", "2013-01-01", 142, True),
    ("w2013", "2013-01-01", "2016-01-01", 304, True),
    ("w2016", "2016-01-01", "2019-01-01", 937, True),
    ("w2019", "2019-01-01", "2022-01-01", 562, True),
    ("w2022", "2022-01-01", "2026-08-10", 2399, True),
]
MERGE_WINDOWS = [
    ("m2004", "2004-01-01", "2010-01-01", 3, True),     # 低置信参考行
    ("m2010", "2010-01-01", "2016-01-01", 446, True),
    ("m2016", "2016-01-01", "2022-01-01", 1499, True),
    ("m2022", "2022-01-01", "2026-08-10", 2399, True),
]
STARTS = ["2000-01-01", "2005-01-01", "2010-01-01", "2015-01-01", "2019-01-01", "2023-01-01"]


# ─────────────────────────── 公共组件 ───────────────────────────

def read_signals(path: str | Path) -> pd.DataFrame:
    """读信号集（utf-8-sig BOM；code 保 str）"""
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"code": str})


def prefilter_signals(src: str | Path, wmin: str, wmax: str, out_path: str | Path) -> Path:
    """按信号日期区间预过滤 signals → 独立 CSV（幂等；保持列序 + BOM 头）

    enrich 逐行查库不依赖行间上下文，预过滤安全（r44 注释已核）。
    右开区间 [wmin, wmax)；wmax 为空 → 只取下限。
    """
    df = read_signals(src)
    df = df[df["date"].astype(str) >= str(wmin)]
    if wmax:
        df = df[df["date"].astype(str) < str(wmax)]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def _enriched_cache_path(signals_path: str | Path) -> Path:
    """已 enrich 信号集缓存路径（与信号集同目录，{stem}_enriched.csv）"""
    p = Path(signals_path)
    return p.with_name(p.stem + "_enriched.csv")


def _build_enriched_cache(signals_path: str | Path) -> Path:
    """构建/校验 enrich 缓存（原子写：临时文件 + os.replace；指纹 = 行数+日期范围）

    2026-08-11 提速：主网格/拐点窗/起始点格共用同一信号集，enrich（4,351 行逐行
    duckdb 复算 vol_ratio/mom20）每格重复耗时 ~30-50s——构建一次全格复用。
    指纹校验防信号集更新后读旧缓存；结果与每次复算零差（enrich 为逐行纯函数）。
    """
    from 回测系统.tighten_compare import enrich as _enrich
    cache = _enriched_cache_path(signals_path)
    src = read_signals(signals_path)
    fp = f"{len(src)}_{str(src['date'].astype(str).min())[:10]}_{str(src['date'].astype(str).max())[:10]}"
    fp_file = Path(str(cache) + ".fp")
    if cache.exists() and fp_file.exists() and fp_file.read_text(encoding="utf-8").strip() == fp:
        return cache
    import contextlib
    import os as _os
    df = src.copy()
    with contextlib.redirect_stdout(open(_os.devnull, "w")):
        df = _enrich(df)
    tmp = Path(str(cache) + ".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    _os.replace(tmp, cache)            # 原子替换，防并发读半文件
    fp_file.write_text(fp, encoding="utf-8")
    return cache


def reasons_breakdown(res: dict) -> dict:
    """从 simulate_capital res 提取可买性拒绝计数（r44 死字段修复）

    "资金不足"为固定键；"每股风险{值}超限(>{上限})"为动态键名 → 按前缀聚合。
    """
    reasons = res.get("reasons") or {}
    insufficient = int(reasons.get("资金不足", 0) or 0)
    risk_over = sum(int(v) for k, v in reasons.items()
                    if str(k).startswith("每股风险") and v)
    return {
        "reject_insufficient": insufficient,
        "reject_risk_over": risk_over,
        "n_eligible": int(res.get("n_exec", 0) or 0) + insufficient + risk_over,
    }


def run_cell(cell: dict, out_dir: str | Path) -> dict:
    """一格 = 一次 r44.run_one + reasons 增强（单格执行器核心）

    cell: {id, signals, capital, risk_ratio, max_positions, monthly_inject,
           min_date, window_min, window_max, risk_growth, note}
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    signals_path = cell["signals"]
    enriched_path = None
    if cell.get("window_min") or cell.get("window_max"):
        label = cell["id"]
        signals_path = prefilter_signals(signals_path, cell.get("window_min") or "0000-01-01",
                                         cell.get("window_max") or "",
                                         out_dir / f"signals_{label}.csv")
    else:
        # 无窗口预过滤 → 复用 enrich 缓存（2026-08-11 提速：全量信号集复算一次全格共享）
        enriched_path = str(_build_enriched_cache(signals_path))
    metrics, res = r44_run_one(
        str(signals_path), float(cell["capital"]), float(cell["risk_ratio"]),
        int(cell.get("max_positions", MAX_POS)),
        monthly_inject=float(cell.get("monthly_inject", 0.0) or 0.0),
        min_date=cell.get("min_date") or None,
        risk_growth=bool(cell.get("risk_growth")),
        return_raw=True,
        # max_date：显式传值（制度对比 REGIME_OLD 用）；滚动窗（window_max）自动用真实数据末
        max_date=str(cell.get("max_date") or "").strip() or (DATA_END if cell.get("window_max") else None),
        enriched_path=enriched_path,
        confirm_shortfall_skip=bool(cell.get("confirm_shortfall_skip")),
    )
    rb = reasons_breakdown(res)
    exec_rate = (metrics["n_exec"] / rb["n_eligible"] * 100
                 if rb["n_eligible"] > 0 else 0.0)
    metrics["reject_insufficient"] = rb["reject_insufficient"]
    metrics["reject_risk_over"] = rb["reject_risk_over"]
    metrics["n_eligible"] = rb["n_eligible"]
    metrics["exec_rate"] = round(exec_rate, 1)
    half = res.get("half_stats") or {}
    metrics["half_n"] = half.get("n_half", 0)
    metrics["half_confirm"] = half.get("n_confirm", 0)
    metrics["half_reject"] = half.get("n_reject", 0)
    metrics["id"] = cell["id"]
    metrics["note"] = cell.get("note", "")
    metrics["n_confirm_shortfall"] = int(res.get("n_confirm_shortfall", 0) or 0)
    # selfcheck_pass 门禁字段（r44 CLI 层才加；run_one 返回 metrics 不含，collect 门禁依赖）
    metrics["selfcheck_pass"] = bool(metrics.get("selfcheck") and all(metrics["selfcheck"].values()))
    return metrics


def write_manifest(cells: list[dict], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(cells).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[manifest] {len(cells)} 格 → {path}")


def cell_row(cid: str, capital: float, risk_ratio: float,
             max_positions: int = MAX_POS, monthly_inject: float = 0.0,
             min_date: str | None = None, window_min: str | None = None,
             window_max: str | None = None, risk_growth: bool = False,
             note: str = "", skip: bool = False,
             max_date: str | None = None,
             confirm_shortfall_skip: bool = False) -> dict:
    return {
        "id": cid, "signals": str(DEFAULT_SIGNALS), "capital": capital,
        "risk_ratio": risk_ratio, "max_positions": max_positions,
        "monthly_inject": monthly_inject, "min_date": min_date or "",
        "window_min": window_min or "", "window_max": window_max or "",
        "risk_growth": risk_growth, "note": note, "skip": skip,
        "max_date": max_date or "",
        "confirm_shortfall_skip": confirm_shortfall_skip,
    }


# ─────────────────────────── 子命令：anchor ───────────────────────────

def anchor_main(signals: str) -> int:
    print("=== V1 锚点对账（7 点：r44 三点 @8401 + R-045 三单点 + 16k · 偏差>1pp 停跑）===")
    ok = True
    for a in ANCHORS_7:
        r = r44_run_one(signals, a["capital"], a["risk_ratio"], a["max_positions"])
        d_ret = r["total_ret_pct"] - a["expect_ret"]
        d_dd = r["dd_peak_pct"] - a["expect_dd"]
        mark = "✅" if abs(d_ret) < 1.0 else "❌"
        if abs(d_ret) >= 1.0:
            ok = False
        print(f"  {a['label']:<14} 收益 {r['total_ret_pct']:>8.1f}% (期望 {a['expect_ret']}, "
              f"差 {d_ret:+.1f}pp) | 回撤 {r['dd_peak_pct']:>6.1f}% (期望 {a['expect_dd']}) {mark}")
    print("  V1 结论:", "全部通过 → 开跑网格" if ok else "有偏差 → 停跑查因（不得继续）")
    return 0 if ok else 1


# ─────────────────────────── 子命令：grid ───────────────────────────

def grid_manifest(phase: str, best_pivot: str | None, out_dir: str | Path,
                  ratios: list[float] | None = None,
                  capitals: list[float] | None = None) -> int:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if phase == "ext":
        # R-050 扩展档位：ratios × capitals × 26 年（不改 RATIOS/CAPITALS 常量防破坏 corner 索引）
        rs = ratios or [0.030, 0.035, 0.040]
        cs = capitals or CAPITALS
        cells = []
        for ci, cap in enumerate(cs):
            for ri, ratio in enumerate(rs):
                cells.append(cell_row(f"X{ci * len(rs) + ri + 1:02d}", cap, ratio,
                                      note=f"扩展档 {ratio:.4f}×{cap:.0f}"))
        write_manifest(cells, out_dir / "manifest_ext.csv")
        return 0
    if phase == "extwin":
        # R-050 扩展档位 × 近 7 年（min_date=2019-01-01）
        rs = ratios or [0.030, 0.040]
        cs = capitals or CAPITALS
        cells = []
        for ci, cap in enumerate(cs):
            for ri, ratio in enumerate(rs):
                cells.append(cell_row(f"W{ci * len(rs) + ri + 1:02d}", cap, ratio,
                                      min_date="2019-01-01",
                                      note=f"扩展档近7年 {ratio:.4f}×{cap:.0f}"))
        write_manifest(cells, out_dir / "manifest_extwin.csv")
        return 0
    if phase == "main":
        # 编号 = 资金列优先 × 档位行（G04=8401×0.012855 锚点格，与方案矩阵表一致）
        cells = []
        for ci, cap in enumerate(CAPITALS):
            for ri, ratio in enumerate(RATIOS):
                cells.append(cell_row(f"G{ci * len(RATIOS) + ri + 1:02d}", cap, ratio,
                                      note=f"{ratio:.4f}×{cap:.0f}"))
        write_manifest(cells, out_dir / "manifest_main.csv")
        return 0
    if best_pivot is None:
        print("[grid] --phase refine/corner/inject 需要 --best <主网格 pivot.csv>")
        return 1
    pivot = pd.read_csv(best_pivot, encoding="utf-8-sig")
    if "id" not in pivot.columns or "total_ret_pct" not in pivot.columns:
        print(f"[grid] pivot 缺列（需 id/total_ret_pct/dd_peak_pct/risk_ratio/capital）: {list(pivot.columns)}")
        return 1
    # 每资金档最优档（收益回撤比 argmax；排除回撤 >-20% 与 n_exec < 50）
    pivot = pivot.copy()
    pivot["ret"] = pd.to_numeric(pivot["total_ret_pct"], errors="coerce")
    pivot["dd"] = pd.to_numeric(pivot["dd_peak_pct"], errors="coerce")
    pivot["ddr"] = pivot["ret"] / pivot["dd"].abs().replace(0, np.nan)
    pivot["n_exec"] = pd.to_numeric(pivot["n_exec"], errors="coerce")
    feas = pivot[(pivot["dd"] > -20.0) & (pivot["n_exec"] >= 50)].copy()
    best_rows = feas.sort_values("ddr", ascending=False).groupby("capital").head(1)
    if phase == "refine":
        cells = []
        for _, b in best_rows.iterrows():
            cap = float(b["capital"])
            r_best = float(b["risk_ratio"])
            idx = RATIOS.index(r_best) if r_best in RATIOS else -1
            if idx <= 0 or idx >= len(RATIOS) - 1:
                continue  # 端点不精化
            # 相邻两区间各插 1 点（1/3、2/3 处）
            lo_l, lo_h = RATIOS[idx - 1], RATIOS[idx]
            hi_l, hi_h = RATIOS[idx], RATIOS[idx + 1]
            cells.append(cell_row(f"R{idx}L_{int(cap)}", cap, round(lo_l + (lo_h - lo_l) / 3, 6),
                                  note=f"精化(左区间1/3) {cap:.0f}"))
            cells.append(cell_row(f"R{idx}H_{int(cap)}", cap, round(hi_l + (hi_h - hi_l) / 3, 6),
                                  note=f"精化(右区间1/3) {cap:.0f}"))
        write_manifest(cells, out_dir / "manifest_refine.csv")
        print(f"[grid] 精化 {len(cells)} 格（规则：最优档非端点时相邻两区间各插 1 点）")
        return 0
    if phase == "corner":
        cells = []
        for _, b in best_rows.iterrows():
            cap = float(b["capital"])
            idx = RATIOS.index(float(b["risk_ratio"])) if float(b["risk_ratio"]) in RATIOS else -1
            for j in range(max(0, idx - 1), min(len(RATIOS), idx + 2)):
                if idx < 0:
                    continue
                for md, label in (("2019-01-01", "近7年"), ("2023-01-01", "近3年")):
                    cells.append(cell_row(f"C{int(cap)}_{j}_{label}", cap, RATIOS[j],
                                          min_date=md, note=f"拐点验证 {label}"))
        write_manifest(cells, out_dir / "manifest_corner.csv")
        print(f"[grid] 拐点窗 {len(cells)} 格（每资金档最优±1 档 × 近7/近3 年）")
        return 0
    if phase == "inject":
        cells = []
        for _, b in best_rows.iterrows():
            if float(b["capital"]) != 8401.0:
                continue
            cells.append(cell_row("INJ1", 8401.0, float(b["risk_ratio"]),
                                  monthly_inject=3000.0, min_date="2023-01-01",
                                  risk_growth=True, note="8,401 最优档×近3年月注入3000×risk_growth"))
        write_manifest(cells, out_dir / "manifest_inject.csv")
        print(f"[grid] 注入补测 {len(cells)} 格（8,401 最优档 × 近 3 年 + 月注入 3000 × risk_growth）")
        return 0
    print(f"[grid] 未知 phase={phase}（main/refine/corner/inject）")
    return 1


# ─────────────────────────── 子命令：timewindow ───────────────────────────

def timewindow_manifest(out_dir: str | Path) -> int:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = []
    # B1 起始点敏感性（2000 = 全量等价 T2000；与 G04 同参，作 V4 确定性双跑对比）
    for i, s in enumerate(STARTS):
        cells.append(cell_row(f"T{s[:4]}", 8401.0, 0.012855, min_date=s,
                              note=f"起始点 {s[:4]}"))
    # B2 滚动窗（空窗/近空窗 skip=True 不参与批处理——仅冒烟验证空处理路径用；
    # 正式结果以年度分布呈现，不跑引擎；合并参考窗带 run 标记）
    for (label, wmin, wmax, n, do_run) in WINDOWS:
        cells.append(cell_row(f"{label}", 8401.0, 0.012855, window_min=wmin,
                              window_max=wmax, skip=not do_run,
                              note=f"滚动窗 {label}（{n} 笔信号）"
                                   + ("" if do_run else "——空窗不跑引擎，仅年度分布")))
    for (label, wmin, wmax, n, _) in MERGE_WINDOWS:
        cells.append(cell_row(f"{label}", 8401.0, 0.012855, window_min=wmin,
                              window_max=wmax, note=f"合并参考窗 {label}（{n} 笔信号）"))
    # B3 制度对比（信号过滤：≤2019-12-31 用右开窗 window_max；≥2020 用 min_date）
    cells.append(cell_row("REGIME_OLD", 8401.0, 0.012855, window_max="2020-01-01",
                          note="制度对比 ≤2019（10% 涨跌停时代）"))
    cells.append(cell_row("REGIME_NEW", 8401.0, 0.012855, min_date="2020-01-01",
                          note="制度对比 ≥2020（创业板/科创板 20%）"))
    write_manifest(cells, out_dir / "manifest_timewindow.csv")
    print(f"[timewindow] {len(cells)} 格（起点 6 + 滚动窗可跑 6 + 合并 4 + 制度 2）")
    return 0


# ─────────────────────────── 子命令：run-one / batch / collect ───────────────────────────

def run_one_main(manifest: str, cell_id: str, out_dir: str) -> int:
    m = pd.read_csv(manifest, encoding="utf-8-sig", dtype={"id": str})
    row = m[m["id"] == cell_id]
    if not len(row):
        print(f"[run-one] 未找到 cell {cell_id}（manifest {manifest}）")
        return 1
    cell = row.iloc[0].to_dict()
    for k in ("capital", "risk_ratio", "max_positions", "monthly_inject"):
        try:
            cell[k] = float(cell[k]) if k != "max_positions" else int(cell[k])
        except (TypeError, ValueError):
            cell[k] = {"capital": 8401.0, "risk_ratio": 0.012855,
                       "max_positions": MAX_POS, "monthly_inject": 0.0}[k]
    cell["risk_growth"] = str(cell.get("risk_growth", "")).lower() in ("true", "1")
    cell["confirm_shortfall_skip"] = str(cell.get("confirm_shortfall_skip", "")).lower() in ("true", "1")
    for k in ("min_date", "window_min", "window_max", "max_date"):
        # CSV 空字符串读成 NaN → 清洗回 ""（2026-08-11 冒烟实测：漏 max_date 会污染数据末判定）
        cell[k] = str(cell[k]).strip() if str(cell[k]).strip() not in ("nan", "None", "") else ""
    import time
    t0 = time.time()
    try:
        m_out = run_cell(cell, out_dir)
    except Exception as exc:  # noqa: BLE001 - 单格失败写 .err，不中断批处理
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        Path(out_dir, f"{cell_id}.err").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        print(json.dumps({"id": cell_id, "error": str(exc)}, ensure_ascii=False))
        return 1
    m_out["seconds"] = round(time.time() - t0, 1)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    Path(out_dir, f"{cell_id}.json").write_text(json.dumps(m_out, ensure_ascii=False, indent=1),
                                                encoding="utf-8")
    print(json.dumps(m_out, ensure_ascii=False))
    return 0


def batch_main(manifest: str, workers: int, out_dir: str) -> int:
    m = pd.read_csv(manifest, encoding="utf-8-sig", dtype={"id": str})
    if "skip" in m.columns:
        m = m[~m["skip"].astype(str).str.lower().isin(["true", "1"])]
    cells = m["id"].tolist()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    for i in range(0, len(cells), workers):
        chunk = cells[i:i + workers]
        procs = []
        for cid in chunk:
            procs.append(subprocess.Popen(
                [sys.executable, str(Path(__file__)), "run-one",
                 "--cell", str(cid), "--manifest", str(manifest), "--out", str(out_dir)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for p in procs:
            p.wait()
        done += len(chunk)
        print(f"[batch] {done}/{len(cells)} 完成")
    print(f"[batch] 全部完成 → {out_dir}")
    return 0


def collect_main(out_dir: str, pivot_out: str) -> int:
    out_dir = Path(out_dir)
    rows = []
    fails = []
    for f in sorted(out_dir.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            fails.append((f.stem, "JSON 解析失败"))
            continue
        if d.get("selfcheck_pass") is False or d.get("selfcheck", {}).get("curve_end_matches") is False:
            fails.append((f.stem, "selfcheck 失败"))
        rows.append(d)
    if not rows:
        print("[collect] 无结果 json（先跑 batch）")
        return 1
    pivot = pd.DataFrame(rows)
    cols = ["id", "capital", "risk_ratio", "risk_amt", "total_ret_pct", "dd_peak_pct",
            "n_exec", "avg_r", "avg_r_no_top5", "reject_insufficient", "reject_risk_over",
            "exec_rate", "exposure_peak_pct", "max_streak", "min_date", "note", "seconds",
            "n_confirm_shortfall"]
    pivot = pivot[[c for c in cols if c in pivot.columns]]
    pivot_out = Path(pivot_out)
    pivot_out.parent.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(pivot_out, index=False, encoding="utf-8-sig")
    print(f"[collect] {len(rows)} 格合并 → {pivot_out}")
    if fails:
        print("  ⚠️ 自检门禁失败（标红停查，不静默剔除）:")
        for cid, why in fails:
            print(f"    ❌ {cid}: {why}")
        return 2
    print("  ✅ 全部格 selfcheck_pass")
    return 0


# ─────────────────────────── 子命令：regime（B4 宏观标注） ───────────────────────────

def regime_main(signals: str, index_cache: str | None = None) -> int:
    """对每个窗/起点段输出牛/熊/震荡交易日占比 + 信号层 avgR/胜率（直接统计，不跑引擎）"""
    from 回测系统.market_regime import load_index_df, regime_series
    df = read_signals(signals)
    df["date"] = df["date"].astype(str).str[:10]
    idx = load_index_df(cache_dir=index_cache)
    if idx is None or idx.empty:
        print("[regime] 指数数据不可得（无缓存且无法联网）→ 跳过宏观标注")
        return 1
    series = regime_series(idx)
    out_rows = []
    segs = [(f"s{s[:4]}", s, "2026-08-10") for s in STARTS] + \
           [(label, wmin, wmax) for (label, wmin, wmax, _, _) in WINDOWS] + \
           [(label, wmin, wmax) for (label, wmin, wmax, _, _) in MERGE_WINDOWS]
    for label, wmin, wmax in segs:
        s_mask = (series.index >= wmin) & (series.index < wmax) if wmax else series.index >= wmin
        seg = series[s_mask]
        n_days = len(seg)
        vc = seg.value_counts()
        def _pct(name: str) -> str:
            return f"{(vc.get(name, 0) / n_days * 100 if n_days else 0):.0f}%"
        sub = df[(df["date"] >= wmin) & (df["date"] < wmax if wmax else True)]
        rs = pd.to_numeric(sub["r_20d"], errors="coerce").dropna()
        out_rows.append({
            "label": label, "天数": n_days,
            "牛": _pct("牛"), "熊": _pct("熊"), "震荡": _pct("震荡"),
            "信号数": len(sub), "avgR": round(float(rs.mean()), 3) if len(rs) else None,
            "胜率": f"{(rs > 0).mean() * 100:.1f}%" if len(rs) else "-",
        })
    out = pd.DataFrame(out_rows)
    print(out.to_string(index=False))
    return 0


# ─────────────────────────── 子命令：mc（A5 蒙卡） ───────────────────────────

def mc_main(capital: float, risk_ratio: float, signals: str, nsim: int,
            max_positions: int) -> int:
    """候选档蒙卡：成交集 = r48 主网格同源成交（half_phase=True），复用 simulate 纯函数

    破产率 = P(逐路径累计 R 曲线任意时点 ≤ -(capital / (capital*risk_ratio))) = P(min 累计R ≤ -1/risk_ratio)
    """
    from 分析决策.跟踪.monte_carlo import simulate
    metrics, res = r44_run_one(signals, capital, risk_ratio, max_positions,
                               return_raw=True)
    trades = res["trades"]
    rs = [float(t["pnl"]) / float(t["risk_actual"])
          for t in trades if float(t.get("risk_actual") or 0) > 0]
    if len(rs) < 5:
        print(f"[mc] 成交不足 {len(rs)} 笔，无法蒙卡")
        return 1
    mc = simulate([{"r_multiple": r} for r in rs],
                  n_simulations=nsim, fee_per_trade_r=0.0)
    if "error" in mc:
        print(f"[mc] {mc['error']}")
        return 1
    fin = mc["final_equities"]
    dd = mc["max_drawdowns"]
    samples = mc["samples"]
    # 破产：任意时点累计 R ≤ -1/risk_ratio（亏光初始风险额预算 = 亏光本金）
    ruin_line = -1.0 / risk_ratio
    cum = np.cumsum(samples, axis=1)
    ruin = float(np.mean(cum.min(axis=1) <= ruin_line))
    p = lambda a, q: float(np.percentile(a, q))  # noqa: E731
    print(json.dumps({
        "capital": capital, "risk_ratio": risk_ratio,
        "risk_amt": round(capital * risk_ratio, 2), "n_exec": len(rs),
        "avg_r": round(float(mc["avg_r"]), 3),
        "prob_profit": round(float(mc["prob_profit"]), 4),
        "ruin_rate": round(ruin, 4),
        "ruin_line_R": round(ruin_line, 1),
        "fin_p05": round(p(fin, 5), 1), "fin_p50": round(p(fin, 50), 1),
        "fin_p95": round(p(fin, 95), 1),
        "dd_p05": round(p(dd, 5), 1), "dd_p50": round(p(dd, 50), 1),
        "dd_p95": round(p(dd, 95), 1),
        "streak_max": int(mc["streaks"].max()),
        "wr_p05": round(p((samples > 0).mean(axis=1), 5), 4),
    }, ensure_ascii=False, indent=1))
    return 0


# ─────────────────────────── 子命令：attr（R-050 归因 + 选择偏差） ───────────────────────────

BUCKETS = [0.5, 0.6721, 1.0, 1.5, 2.0, 2.5, 3.0]   # 0.6721 = 8401×0.008/100 每股风险上限对齐
BUCKET_LABELS = [f"{BUCKETS[i]:.2f}~{BUCKETS[i+1]:.2f}" for i in range(len(BUCKETS) - 1)]


def _bucket_of(risk: float) -> int | None:
    """每股风险 → 桶索引（0.5~3.0 之外 → None）"""
    if risk < BUCKETS[0] or risk > BUCKETS[-1]:
        return None
    for i in range(len(BUCKETS) - 1):
        if BUCKETS[i] <= risk < BUCKETS[i + 1]:
            return i
    return len(BUCKETS) - 2   # 上边界 3.0 归入末桶


def _sig_stats(rows: pd.DataFrame, r_col: str = "r_20d") -> dict:
    """信号层统计（r_20d 口径）：n/avgR/胜率/亏损笔占比"""
    rs = pd.to_numeric(rows[r_col], errors="coerce").dropna()
    if not len(rs):
        return {"n": 0, "avgR": None, "win_rate": None, "loss_pct": None}
    return {"n": int(len(rs)), "avgR": round(float(rs.mean()), 3),
            "win_rate": round(float((rs > 0).mean()), 3),
            "loss_pct": round(float((rs <= 0).mean()), 3)}


def attr_main(manifest: str, cells_csv: str, out_dir: str | Path,
              debug_rejects: bool) -> int:
    """R-050 归因 + 选择偏差：run_one(return_raw + debug_rejects) → 四表输出

    attr_pivot：每档 n/sum_pnl/risk_invested/mean_risk/U/avgR/avgR_no_top5/持仓/资金不足
    attr_decomp：相邻档 + 0.008→0.025 总分解（恒等式 + Δln 三因子贡献，和=100%）
    attr_buckets：成交集每股风险分桶质量，对照信号层同桶
    attr_subsets：成交集/超限拒集/资金不足错过集 avgR+胜率（信号层 r_20d 口径）+ 门禁
    """
    import math
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    m = pd.read_csv(manifest, encoding="utf-8-sig", dtype={"id": str})
    sel_ids = [c.strip() for c in cells_csv.split(",") if c.strip()]
    sel = m[m["id"].isin(sel_ids)]
    if not len(sel):
        print(f"[attr] manifest 中未找到 cells: {sel_ids}")
        return 1
    sig = read_signals(DEFAULT_SIGNALS)
    sig["key"] = sig["code"].astype(str) + "_" + sig["date"].astype(str).str[:10]

    pivot_rows, bucket_rows, subset_rows = [], [], []
    decomp_pairs = []
    prev = None
    gate_fail = []

    for _, cell in sel.iterrows():
        c = cell.to_dict()
        for k in ("capital", "risk_ratio", "max_positions", "monthly_inject"):
            try:
                c[k] = float(c[k]) if k != "max_positions" else int(c[k])
            except (TypeError, ValueError):
                c[k] = {"capital": 8401.0, "risk_ratio": 0.012855,
                        "max_positions": MAX_POS, "monthly_inject": 0.0}[k]
        c["risk_growth"] = str(c.get("risk_growth", "")).lower() in ("true", "1")
        c["confirm_shortfall_skip"] = str(c.get("confirm_shortfall_skip", "")).lower() in ("true", "1")
        for k in ("min_date", "window_min", "window_max", "max_date"):
            c[k] = str(c[k]).strip() if str(c[k]).strip() not in ("nan", "None", "") else ""
        metrics, res = r44_run_one(
            str(c["signals"]), float(c["capital"]), float(c["risk_ratio"]),
            int(c.get("max_positions", MAX_POS)),
            monthly_inject=float(c.get("monthly_inject", 0.0) or 0.0),
            min_date=c.get("min_date") or None,
            risk_growth=c["risk_growth"], return_raw=True,
            enriched_path=str(_build_enriched_cache(c["signals"])),
            debug_rejects=debug_rejects,
            confirm_shortfall_skip=c.get("confirm_shortfall_skip", False))
        trades = res["trades"]
        rejects = res.get("rejects") or []
        cap, ratio = float(c["capital"]), float(c["risk_ratio"])
        risk_amt = cap * ratio

        # ── attr_pivot ──
        pnls = [float(t["pnl"] or 0) for t in trades]
        risk_as = [float(t.get("risk_actual") or 0) for t in trades]
        sum_pnl = sum(pnls)
        risk_inv = sum(risk_as)
        n = len(trades)
        mean_risk = risk_inv / n if n else 0.0
        u = sum_pnl / risk_inv if risk_inv else 0.0
        avg_r = float(np.mean([p / ra for p, ra in zip(pnls, risk_as) if ra > 0])) if n else 0.0
        insuff = sum(v for k, v in (res.get("reasons") or {}).items() if k == "资金不足")
        pivot_rows.append({
            "id": c["id"], "capital": cap, "risk_ratio": ratio, "n": n,
            "sum_pnl": round(sum_pnl, 2), "risk_invested": round(risk_inv, 2),
            "mean_risk": round(mean_risk, 3), "U": round(u, 4), "avgR": round(avg_r, 4),
            "avgR_no_top5": metrics.get("avg_r_no_top5"),
            "ret_pct": metrics["total_ret_pct"], "dd_pct": metrics["dd_peak_pct"],
            "peak_positions": metrics.get("peak_positions"), "avg_positions": metrics.get("avg_positions"),
            "reject_insufficient": insuff,
        })

        # ── 成交集 join 信号层（每股风险 + r_20d）──
        tdf = pd.DataFrame(trades)
        tdf["key"] = tdf["code"].astype(str) + "_" + tdf["date"].astype(str).str[:10]
        tdf = tdf.merge(sig[["key", "risk", "r_20d"]], on="key", how="left")
        tdf["risk"] = pd.to_numeric(tdf["risk"], errors="coerce")
        tdf["risk"] = tdf["risk"].fillna(tdf["risk_actual"] / tdf["shares"].clip(lower=1))  # 反推兜底
        tdf["bucket"] = tdf["risk"].apply(_bucket_of)

        # ── attr_buckets（成交集 vs 信号层触发集同桶）──
        trig = sig[(sig["mode"] == "prebreak") & (sig["triggered_20d"] == 1)].copy()
        trig["risk"] = pd.to_numeric(trig["risk"], errors="coerce")
        trig["bucket"] = trig["risk"].apply(_bucket_of)
        for bi in range(len(BUCKET_LABELS)):
            t_sub = tdf[tdf["bucket"] == bi]
            s_sub = trig[trig["bucket"] == bi]
            ts = _sig_stats(t_sub, "r_20d")
            ss = _sig_stats(s_sub, "r_20d")
            bucket_rows.append({
                "id": c["id"], "ratio": ratio, "桶": BUCKET_LABELS[bi],
                "成交n": ts["n"], "成交avgR": ts["avgR"], "成交胜率": ts["win_rate"],
                "触发集n": ss["n"], "触发集avgR": ss["avgR"],
            })

        # ── debug_rejects 子集分析（门禁 + 质量）──
        if debug_rejects:
            reasons = res.get("reasons") or {}
            rdf = pd.DataFrame(rejects) if rejects else pd.DataFrame(
                columns=["code", "date", "risk_ps", "reason", "risk_amt_at"])
            rdf["key"] = rdf["code"].astype(str) + "_" + rdf["date"].astype(str).str[:10]
            rdf = rdf.merge(sig[["key", "risk", "r_20d"]], on="key", how="left")
            by_cat: dict[str, pd.DataFrame] = {}
            for cat in ("资金不足", "超限", "其他"):
                if cat == "资金不足":
                    sub_r = rdf[rdf["reason"] == "资金不足"]
                elif cat == "超限":
                    sub_r = rdf[rdf["reason"].str.startswith("每股风险", na=False)]
                else:
                    sub_r = rdf[~rdf["reason"].isin(["资金不足"]) &
                                ~rdf["reason"].str.startswith("每股风险", na=False)]
                by_cat[cat] = sub_r
            # 门禁：Σrejects 各类 == reasons 同类零差
            n_insuff = len(by_cat["资金不足"]); n_over = len(by_cat["超限"])
            r_insuff = int(reasons.get("资金不足", 0) or 0)
            r_over = sum(v for k, v in reasons.items() if str(k).startswith("每股风险") and v)
            if n_insuff != r_insuff or n_over != r_over:
                gate_fail.append(f"{c['id']}: rejects 资金不足 {n_insuff} vs reasons {r_insuff}"
                                 f" | 超限 {n_over} vs {r_over}")
            if int(metrics["n_exec"]) != len(trades):
                gate_fail.append(f"{c['id']}: n_exec {metrics['n_exec']} vs trades {len(trades)}")
            # 错过损失估算（每笔期望损失 ≈ avgR×risk_amt）
            miss = by_cat["资金不足"]
            miss_avg = _sig_stats(miss, "r_20d")["avgR"]
            miss_loss = (miss_avg * risk_amt * len(miss)) if miss_avg is not None else 0.0
            subset_rows.append({
                "id": c["id"], "ratio": ratio,
                "成交集": _sig_stats(tdf, "r_20d"),
                "超限拒集": _sig_stats(by_cat["超限"], "r_20d"),
                "资金不足错过集": _sig_stats(miss, "r_20d"),
                "错过损失估算(元)": round(miss_loss, 0),
                "错过笔数": len(miss),
            })
        # ── decomp 相邻档对 ──
        if prev is not None and prev["capital"] == cap:
            p0, p1 = prev, pivot_rows[-1]
            if p0["sum_pnl"] and p0["n"] and p0["mean_risk"]:
                ratio_pnl = p1["sum_pnl"] / p0["sum_pnl"]
                rU = p1["U"] / p0["U"] if p0["U"] else None
                rn = p1["n"] / p0["n"]
                rm = p1["mean_risk"] / p0["mean_risk"] if p0["mean_risk"] else None
                if rU and rm:
                    ln = lambda x: math.log(x)  # noqa: E731
                    denom = ln(rU) + ln(rn) + ln(rm)
                    decomp_pairs.append({
                        "档对": f"{p0['risk_ratio']:.4f}→{p1['risk_ratio']:.4f}",
                        "总盈亏比": round(ratio_pnl, 3),
                        "U比(质量)": round(rU, 3), "n比(数量)": round(rn, 3),
                        "mean_risk比(杠杆)": round(rm, 3),
                        "Δln质量%": round(ln(rU) / denom * 100, 1),
                        "Δln数量%": round(ln(rn) / denom * 100, 1),
                        "Δln杠杆%": round(ln(rm) / denom * 100, 1),
                        "Δln和%": 100.0,
                    })
        prev = pivot_rows[-1]

    pd.DataFrame(pivot_rows).to_csv(out_dir / "attr_pivot.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(bucket_rows).to_csv(out_dir / "attr_buckets.csv", index=False, encoding="utf-8-sig")
    decomp_cols = ["档对", "总盈亏比", "U比(质量)", "n比(数量)", "mean_risk比(杠杆)",
                   "Δln质量%", "Δln数量%", "Δln杠杆%", "Δln和%"]
    if decomp_pairs:
        pd.DataFrame(decomp_pairs).to_csv(out_dir / "attr_decomp.csv", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=decomp_cols).to_csv(out_dir / "attr_decomp.csv", index=False, encoding="utf-8-sig")
    if subset_rows:
        # 展开嵌套 dict → 宽表
        flat = []
        for r in subset_rows:
            row = {"id": r["id"], "ratio": r["ratio"]}
            for k in ("成交集", "超限拒集", "资金不足错过集"):
                for kk, vv in r[k].items():
                    row[f"{k}_{kk}"] = vv
            row["错过损失估算(元)"] = r["错过损失估算(元)"]
            row["错过笔数"] = r["错过笔数"]
            flat.append(row)
        pd.DataFrame(flat).to_csv(out_dir / "attr_subsets.csv", index=False, encoding="utf-8-sig")
    print(f"[attr] {len(pivot_rows)} 格 → {out_dir}")
    print("[attr] 门禁:", "✅ 全部零差" if not gate_fail else f"❌ {gate_fail}")
    return 0 if not gate_fail else 2


# ─────────────────────────── 入口 ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="R-048 比例网格 + R-049 时间窗稳健性")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("anchor")
    p.add_argument("--signals", default=str(DEFAULT_SIGNALS))

    p = sub.add_parser("grid")
    p.add_argument("--phase", choices=["main", "refine", "corner", "inject", "ext", "extwin"],
                   required=True)
    p.add_argument("--best", default=None, help="主网格 collect 的 pivot.csv（refine/corner/inject 用）")
    p.add_argument("--ratios", default=None, help="逗号分隔比例档（ext/extwin 用，默认 0.030,0.035,0.040）")
    p.add_argument("--capitals", default=None, help="逗号分隔资金档（ext/extwin 用，默认 4 档）")
    p.add_argument("--out", default="产出/输出/实验/r48_ratio_grid")

    p = sub.add_parser("timewindow")
    p.add_argument("--out", default="产出/输出/实验/r48_timewindow")

    p = sub.add_parser("run-one")
    p.add_argument("--cell", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("batch")
    p.add_argument("--manifest", required=True)
    p.add_argument("--workers", type=int, default=8,
                   help="并发 worker 数（5600X 6 核 12 线程；2026-08-11 提速默认 6→8）")
    p.add_argument("--out", required=True)

    p = sub.add_parser("collect")
    p.add_argument("--dir", required=True)
    p.add_argument("--pivot", required=True)

    p = sub.add_parser("regime")
    p.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    p.add_argument("--index-cache", default=None)

    p = sub.add_parser("attr")
    p.add_argument("--manifest", required=True)
    p.add_argument("--cells", required=True, help="逗号分隔 cell id（如 G01,G02,...）")
    p.add_argument("--out", required=True)
    p.add_argument("--debug-rejects", action="store_true",
                   help="开启被拒候选明细（sim_capital debug_rejects，选择偏差分析）")

    p = sub.add_parser("mc")
    p.add_argument("--capital", type=float, default=8401.0)
    p.add_argument("--risk-ratio", type=float, default=0.012855)
    p.add_argument("--signals", default=str(DEFAULT_SIGNALS))
    p.add_argument("--nsim", type=int, default=10000)
    p.add_argument("--max-positions", type=int, default=MAX_POS)

    args = ap.parse_args()
    if args.cmd == "anchor":
        return anchor_main(args.signals)
    if args.cmd == "grid":
        ratios = [float(x) for x in args.ratios.split(",")] if args.ratios else None
        capitals = [float(x) for x in args.capitals.split(",")] if args.capitals else None
        return grid_manifest(args.phase, args.best, args.out, ratios, capitals)
    if args.cmd == "timewindow":
        return timewindow_manifest(args.out)
    if args.cmd == "run-one":
        return run_one_main(args.manifest, args.cell, args.out)
    if args.cmd == "batch":
        return batch_main(args.manifest, args.workers, args.out)
    if args.cmd == "collect":
        return collect_main(args.dir, args.pivot)
    if args.cmd == "regime":
        return regime_main(args.signals, args.index_cache)
    if args.cmd == "mc":
        return mc_main(args.capital, args.risk_ratio, args.signals, args.nsim,
                       args.max_positions)
    if args.cmd == "attr":
        return attr_main(args.manifest, args.cells, args.out, args.debug_rejects)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
