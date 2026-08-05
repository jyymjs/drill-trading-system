#!/usr/bin/env python3
"""市场环境复盘模块（R-008 · 复盘三支柱 → 自动化）

学习借鉴：B站 BV1dbSMBuEfX（袁帅马「市场复盘三支柱」）——「大盘决定仓位，个股决定买卖」
把开盘前视觉复盘代码化：指数一致性（防失增）→ 大周期 → 压力位 → 环境结论 + 仓位建议。

首版范围（确认书 R-008 签字版）：
  1. 5 大指数（上证/深成/创业板/科创50/沪深300）日线 + 5/15/60分钟
  2. 一致性判断（多指数同向 vs 背离）
  3. 日线大周期（上涨/下跌/震荡）
  4. 压力位提示（60日新高附近回落）
  5. 环境三档（进攻/中性/防守）+ 建议仓位区间
  6. 终端版式输出 + 存档

边界（首版不做）：连板天梯/涨跌家数、板块产业链、自动调仓。
"""
from __future__ import annotations

import io
import sys
import unicodedata as _ud
from datetime import datetime
from pathlib import Path

import numpy as np

# Windows 控制台 GBK 保护
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 配置 ──

# 指数清单（名称, akshare symbol）——平均股价 880003 不可得，用沪深300 替代
INDEXES = [
    ("上证指数", "sh000001"),
    ("深证成指", "sz399001"),
    ("创业板指", "sz399006"),
    ("科创50", "sh000688"),
    ("沪深300", "sh000300"),
]

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "产出" / "输出" / "market_review"

# ── 数据获取（akshare 日线——干净可靠；分钟接口东财风控暂不可用，压力位用日线近似） ──


def fetch_index_daily(symbol: str, bars: int = 150) -> dict | None:
    """拉取指数日线（akshare），返回 {日期: {close, high, low, pct}} 按日期升序"""
    import akshare as ak
    df = ak.stock_zh_index_daily(symbol=symbol)
    if df is None or len(df) == 0:
        return None
    df = df.tail(bars)
    rows = {}
    prev_close = None
    for _, r in df.iterrows():
        d = str(r["date"])[:10]
        close = float(r["close"])
        pct = (close / prev_close - 1) * 100 if prev_close else 0.0
        rows[d] = {
            "close": close,
            "high": float(r["high"]),
            "low": float(r["low"]),
            "pct": pct,
        }
        prev_close = close
    return rows


# ── 分析 ──

# 周期判断（日线简化版）：收盘相对 MA20/MA60
def analyze_cycle(daily: dict) -> tuple[str, str]:
    """日线大周期：上涨/下跌/震荡 + 说明"""
    closes = [v["close"] for v in daily.values()]
    if len(closes) < 60:
        return "数据不足", "日线不足 60 根"
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:])
    cur = closes[-1]
    if cur > ma20 > ma60:
        return "上涨周期", f"收盘{cur:.0f} > MA20({ma20:.0f}) > MA60({ma60:.0f})，多头排列"
    if cur < ma20 < ma60:
        return "下跌周期", f"收盘{cur:.0f} < MA20({ma20:.0f}) < MA60({ma60:.0f})，空头排列"
    return "震荡周期", f"MA20({ma20:.0f}) 与 MA60({ma60:.0f}) 纠缠，方向未明"


def analyze_consistency(daily_map: dict) -> tuple[str, list]:
    """指数一致性：当日涨跌幅同向性（防失增——指数背离=赚指数不赚钱）

    显著性阈值：|涨跌幅| < 0.2% 视为平盘（微幅波动不判方向，避免误报背离）
    """
    infos = []
    for name, rows in daily_map.items():
        if not rows:
            continue
        last = list(rows.values())[-1]
        infos.append((name, last["pct"]))
    SIG = 0.2  # 显著性阈值 %
    ups = [i for i in infos if i[1] is not None and i[1] > SIG]
    downs = [i for i in infos if i[1] is not None and i[1] < -SIG]
    flat = [i for i in infos if i[1] is not None and -SIG <= i[1] <= SIG]
    if not ups or not downs:
        if ups:
            verdict = "全部一致（普涨）" if not flat else "基本一致（部分平盘）"
        elif downs:
            verdict = "全部一致（普跌）" if not flat else "基本一致（部分平盘）"
        else:
            verdict = "全部平盘（无显著方向）"
        return verdict, infos
    # 有显著涨有显著跌 = 背离
    diver = [f"{n}({p:+.2f}%)" for n, p in infos
             if p is not None and (p > SIG or p < -SIG)
             and ((p > SIG) != (ups[0][1] > 0))]
    return f"存在背离（{len(diver)} 个反向）", infos


def analyze_pressure(daily: dict) -> tuple[bool, str]:
    """压力位提示（日线近似）：近 60 个交易日内最高点，当前是否接近（>99%）"""
    if not daily or len(daily) < 30:
        return False, "日线数据不足"
    items = list(daily.values())
    highs = [v["high"] for v in items[-60:]]
    recent_high = max(highs)
    cur = items[-1]["close"]
    if cur / recent_high >= 0.99:
        return True, f"收盘{cur:.0f} 逼近60日高点{recent_high:.0f}（顶压力区）"
    return False, f"距60日高点{recent_high:.0f} 尚有 {recent_high/cur-1:.1%} 空间"


def conclude(cycle: str, consistency: str, pressure: bool) -> tuple[str, str, str]:
    """环境三档 + 建议仓位 + 说明（大盘决定仓位——指数周期为主，一致性/压力位修正）"""
    if cycle == "上涨周期" and "背离" not in consistency and not pressure:
        return "进攻", "60%~80%", "上涨周期 + 指数一致 + 无顶压力：环境好，可重仓做多"
    if cycle == "下跌周期":
        if "背离" in consistency:
            return "防守", "20%~40%", "下跌周期 + 指数背离：环境差，轻仓防守"
        return "防守", "20%~40%", "下跌周期：环境弱，轻仓防守"
    # 震荡周期
    if pressure:
        return "中性偏防守", "30%~50%", "震荡周期 + 顶压力：谨慎，控制仓位"
    if "背离" in consistency:
        return "中性", "40%~60%", "震荡周期 + 指数分歧：中性仓位，等方向"
    return "中性", "40%~60%", "震荡周期 + 指数一致：中性仓位，个股为主"


# ── 版式渲染 ──


def _disp_w(s: str) -> int:
    return sum(2 if _ud.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, w: int, align: str = "l") -> str:
    gap = w - _disp_w(s)
    if gap <= 0:
        return s
    if align == "r":
        return " " * gap + s
    return s + " " * gap


def render_report(result: dict) -> str:
    """终端版式报告（蒙特卡洛同款中文风格）"""
    W = 78
    line = "-" * W
    C1, C2, C3 = 36, 24, 12

    def row(name, value="", ret=""):
        return f"  {_pad(name, C1-4)} | {_pad(value, C2-2, 'r')} | {_pad(ret, C3-2)}"

    out = [line]
    today = datetime.now().strftime("%Y-%m-%d")
    out.append(f"市场环境复盘报告（{today}）".center(W))
    out.append(line)
    out.append(row("指标", "数值", "备注"))
    out.append(line)

    # 板块 1：指数一致性
    out.append(row(">>> 指数一致性（防失增）"))
    for name, pct in result["consistency"]["infos"]:
        if pct is None:
            out.append(row(f"{name}", "数据缺失"))
        else:
            out.append(row(f"{name}", f"{pct:+.2f}%", "↑" if pct > 0 else ("↓" if pct < 0 else "→")))
    out.append(row("一致性结论", result["consistency"]["verdict"]))
    out.append(line)

    # 板块 2：周期判断
    out.append(row(">>> 大周期（日线）"))
    out.append(row("上证周期", result["cycle"][0]))
    out.append(row("依据", result["cycle"][1]))
    out.append(line)

    # 板块 3：压力位
    out.append(row(">>> 压力位（60日近似）"))
    out.append(row("上证压力", "顶压力区" if result["pressure"][0] else "空间尚可", result["pressure"][1]))
    out.append(line)

    # 板块 4：环境结论
    out.append(row(">>> 环境结论"))
    out.append(row("环境档位", result["verdict"], result["conclusion"]))
    out.append(row("建议仓位", result["position"]))
    out.append(line)

    if result.get("errors"):
        out.append(f"数据缺失：{'；'.join(result['errors'])}")
    out.append("数据源：akshare 指数日线 | 学习借鉴：复盘三支柱（R-008）")
    out.append("说明：环境结论为参考快照，人工视觉复核兜底；平均股价不可得，以沪深300替代；分钟接口暂不可用，压力位用日线近似")
    out.append(line)
    return "\n".join(out)


# ── 主入口 ──


def run() -> dict:
    """执行市场环境复盘，返回结果 dict"""
    daily_map = {}
    errors = []
    for name, symbol in INDEXES:
        try:
            rows = fetch_index_daily(symbol)
            daily_map[name] = rows or {}
        except Exception as e:
            daily_map[name] = {}
            errors.append(f"{name}: {type(e).__name__}")
    if not any(daily_map.values()):
        return {"error": "指数数据全部获取失败（网络或 akshare 异常）"}

    consistency = analyze_consistency(daily_map)
    cycle = analyze_cycle(daily_map.get("上证指数", {}))
    pressure = analyze_pressure(daily_map.get("上证指数", {}))
    verdict, position, conclusion = conclude(cycle[0], consistency[0], pressure[0])

    return {
        "consistency": {"verdict": consistency[0], "infos": consistency[1]},
        "cycle": cycle,
        "pressure": pressure,
        "verdict": verdict,
        "position": position,
        "conclusion": conclusion,
        "errors": errors,
    }


def main() -> int:
    result = run()
    if "error" in result:
        print(f"\n❌ {result['error']}")
        return 1
    text = render_report(result)
    print("\n" + text)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"market_review_{datetime.now().strftime('%Y%m%d')}.txt").write_text(
        text + "\n", encoding="utf-8")
    print(f"\n  已存档: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
