"""
钻潜交易系统 - 统一评级策略（V2 课程标准版）

基于路肖南钻潜交易内训 30 节课程 + 23 份市场扫描录屏提炼。
评级体系严格遵循课程标准：DL / PT / LK / TY / DN / SF

评级 S/A/B/C（与课程一致）：
  S级（优质）= 全部条件良好，核心条件优秀
  A级（常规）= 核心条件满足，部分有瑕疵
  B级（瑕疵）= 基本满足但有明显不足
  C级       = 不满足交易条件（不展示）

优先级分层：
  Tier 0（一票否决）：结构太小 / 完全释放>40% / 无回踩直接冲
  Tier 1（核心三要素）：PT（平台测试）> TY（统一区间）≈ DN（动能）
  Tier 2（质量分级）：DL（结构长度）> LK（轮廓紧凑度）> SF（释放级别）
  Tier 3（加减分）：通道感 / 像素感 / 过高点
"""
import pandas as pd
import numpy as np
from strategy.base import BaseStrategy
from analysis.indicators import (
    platform_test_count, profile_compactness,
    retracement_detect, channel_detect, overshoot_detect
)


class ZuanQianStrategy(BaseStrategy):
    """钻潜交易系统 - 统一评级策略 V2（课程标准版）"""

    name = "钻潜评级策略"
    description = "基于钻潜交易系统6条件 DL/PT/LK/TY/DN/SF，输出S/A/B/C评级"

    required_indicators = ["VOL_RATIO", "BODY_RATIO", "MA20", "MA5"]

    # ── 评级阈值 ──

    # DL 独立结构（K线根数阈值）
    DL_S = 120
    DL_A = 90
    DL_B = 60
    DL_RANGE_S = 0.20
    DL_RANGE_A = 0.30
    DL_RANGE_B = 0.35

    # PT 平台测试次数
    PT_S = 3       # ≥3次有效测试
    PT_A = 2       # 2-3次
    PT_B = 2       # ≥2次（可含瑕疵）

    # LK 轮廓紧凑度（实体占比 0~1）
    LK_S = 0.50    # 紧凑
    LK_A = 0.40    # 中等
    LK_B = 0.30    # 松散边缘

    # TY 统一区间
    TY_S = 5       # 最少K线
    TY_A = 4
    TY_B = 3
    TY_RANGE_S = 0.03
    TY_RANGE_A = 0.05
    TY_RANGE_B = 0.08

    # DN 动能（量比, 实体比）
    DN_S = (2.5, 0.05)
    DN_A = (1.8, 0.04)
    DN_B = (1.5, 0.03)

    # SF 释放级别（涨幅阈值）
    SF_FULL_RELEASE = 0.40  # 完全释放排除阈值

    def get_params(self) -> dict:
        return {
            "评级体系": "S/A/B/C (课程标准)",
            "6条件": "DL独立结构 / PT平台测试 / LK轮廓质量 / TY统一区间 / DN动能 / SF释放级别",
            "优先级": "Tier0一票否决 → Tier1核心三要素 → Tier2质量分级 → Tier3加减分",
            "S级需满足": "全部≥A且至少3个S",
            "A级需满足": "全部≥A或仅1个B",
            "B级需满足": "最多3个B",
        }

    # ── 辅助计算 ──

    @staticmethod
    def _body_pct_series(high, low, op, cl):
        hl = high - low
        body = np.abs(cl - op)
        result = np.divide(body, hl, out=np.ones_like(body), where=hl > 0)
        return np.clip(result, 0.0, 1.0)

    # ── Tier 0：一票否决 ──

    def _tier0_reject(self, df: pd.DataFrame) -> str | None:
        """Tier 0 检查：任一条件不满足直接淘汰

        Returns:
            None = 通过，字符串 = 淘汰原因
        """
        n = len(df)
        close = df["收盘"].values
        low = df["最低"].values

        # 1. 结构太小（K线数<60）
        if n < 60:
            return f"结构太小(仅{n}根)"

        # 2. 完全释放（从60日低点涨幅>40%）
        low_60 = low[-60:].min()
        if low_60 > 0 and (close[-1] - low_60) / low_60 > self.SF_FULL_RELEASE:
            return f"完全释放({(close[-1]-low_60)/low_60:.1%}>40%)"

        # 3. 无回踩轨迹——直接往上冲
        retrace = retracement_detect(df)
        if not retrace["has_retracement"]:
            # 检查最近是否在上涨
            if len(df) >= 10:
                recent_close = close[-10:]
                if all(recent_close[i] <= recent_close[i + 1] for i in range(min(5, len(recent_close) - 1))):
                    return "直接上涨无回踩"

        return None

    # ── 各条件评级 ──

    def _tier0_prebreak(self, df: pd.DataFrame) -> str | None:
        """Tier 0 预突破专用——比标准模式宽松

        预突破模式不卡"回踩轨迹"和"通道上涨"——这个阶段股票还在盘整中，
        尚未突破，回踩/通道是突破后才需要判断的。
        只检查：结构太小 + 完全释放
        """
        n = len(df)
        close = df["收盘"].values
        low = df["最低"].values

        if n < 60:
            return f"结构太小(仅{n}根)"

        low_60 = low[-60:].min()
        if low_60 > 0 and (close[-1] - low_60) / low_60 > self.SF_FULL_RELEASE:
            return f"完全释放({(close[-1]-low_60)/low_60:.1%}>40%)"

        return None

    def _grade_dl(self, df: pd.DataFrame) -> tuple[str, str]:
        """独立结构评级：S/A/B/C"""
        n = len(df)
        high = df["最高"].values
        low = df["最低"].values
        close = df["收盘"].values

        # 过高点检测：如果存在过高点，从新起点重新计数
        overshoot = overshoot_detect(df)
        if overshoot["has_overshoot"]:
            pos = overshoot["position"]
            if pos > 0:
                n = min(n - pos, n)
                high = high[pos:]
                low = low[pos:]
                close = close[pos:]

        candidates = [(120, self.DL_RANGE_S, 'S'),
                      (90, self.DL_RANGE_A, 'A'),
                      (60, self.DL_RANGE_B, 'B')]
        for bars, max_range, grade in candidates:
            if n < bars + 10:
                continue
            search_end = n - bars + 1
            if search_end <= 0:
                continue
            for start in range(search_end - 1, max(0, search_end - 30) - 1, -1):
                seg_h = high[start:start + bars].max()
                seg_l = low[start:start + bars].min()
                avg_c = close[start:start + bars].mean()
                rng = (seg_h - seg_l) / avg_c

                # 结构级别检测：横盘区间振幅≥3%才有意义
                if rng < 0.03:
                    continue
                if rng <= max_range:
                    return grade, f"{bars}根, 波幅{rng:.1%}"
        return 'C', "未找到充分盘整"

    def _grade_pt(self, df: pd.DataFrame) -> tuple[str, str]:
        """平台测试评级：S/A/B/C

        统计价格对同一水平位的有效测试次数。
        """
        test_count = platform_test_count(df, tolerance=0.01, min_gap=3)
        if test_count >= self.PT_S:
            return 'S', f"{test_count}次有效平台测试"
        elif test_count >= self.PT_A:
            return 'A', f"{test_count}次有效平台测试"
        elif test_count >= self.PT_B:
            return 'B', f"{test_count}次平台测试"
        return 'C', f"仅{test_count}次测试, 平台不充分"

    def _grade_lk(self, df: pd.DataFrame) -> tuple[str, str]:
        """轮廓质量评级：S/A/B/C

        基于K线实体占比（紧凑度）+ 像素感检测。
        """
        compactness = profile_compactness(df, window=20)

        # 像素感检测：连续同向K线占比
        close = df["收盘"].values[-30:]
        same_dir = 0
        for i in range(1, len(close)):
            if (close[i] >= close[i - 1]) == (close[i - 1] >= close[i - 2]) if i >= 2 else True:
                same_dir += 1
        continuity_ratio = same_dir / max(len(close) - 1, 1)

        # 综合评分
        score = compactness * 0.6 + continuity_ratio * 0.4

        if score >= self.LK_S and compactness >= 0.45:
            return 'S', f"紧凑(实体比{compactness:.2f}, 连续性{continuity_ratio:.2f})"
        elif score >= self.LK_A:
            return 'A', f"中等(实体比{compactness:.2f}, 连续性{continuity_ratio:.2f})"
        elif score >= self.LK_B:
            return 'B', f"松散(实体比{compactness:.2f}, 连续性{continuity_ratio:.2f})"
        return 'C', f"像素感强(实体比{compactness:.2f}, 连续性{continuity_ratio:.2f})"

    def _grade_ty(self, df: pd.DataFrame) -> tuple[str, str]:
        """统一区间评级：S/A/B/C"""
        n = len(df)
        high, low = df["最高"].values, df["最低"].values
        op, cl = df["开盘"].values, df["收盘"].values
        body_ratios = self._body_pct_series(high, low, op, cl)
        ma20 = df["MA20"].values if "MA20" in df.columns else None

        # 最少K线数要求
        thresholds = [(5, 0.03, 'S'), (4, 0.05, 'A'), (3, 0.08, 'B')]
        for bars, max_range, grade in thresholds:
            if n < bars + 5:
                continue
            for end in range(n - 1, max(0, n - 20) - 1, -1):
                start = end - bars + 1
                if start < 0:
                    continue
                seg_body = body_ratios[start:end + 1]
                if seg_body.mean() > 0.6:
                    continue
                seg_hl = (high[start:end + 1].max() - low[start:end + 1].min())
                if ma20 is not None and (ma20[end] == 0 or pd.isna(ma20[end])):
                    continue

                seg_range = seg_hl / (ma20[end] if ma20 is not None else cl[end]) if max(cl[end], 1) else 0
                if seg_range > max_range:
                    continue
                price_chg = abs(cl[end] - cl[start]) / cl[start] if cl[start] > 0 else 0
                if price_chg > max_range * 2:
                    continue
                return grade, f"{bars}根K线, 波幅{seg_range:.1%}"
        return 'C', "未发现窄幅整理或K线不足"

    def _grade_dn(self, df: pd.DataFrame) -> tuple[str, str]:
        """动能评级：S/A/B/C

        含通道感惩罚——有通道感则降一级。
        """
        latest = df.iloc[-1]
        vol = latest.get("VOL_RATIO", 0)
        body = abs(latest["收盘"] - latest["开盘"]) / latest["收盘"] if latest["收盘"] > 0 else 0

        # 通道感检测
        ch = channel_detect(df)
        channel_penalty = ch["is_channel"]

        base_grade = 'C'
        base_reason = f"量比{vol:.2f}x, 实体{body:.1%}"

        for (v_min, b_min), grade in [(self.DN_S, 'S'), (self.DN_A, 'A'), (self.DN_B, 'B')]:
            if vol >= v_min and body >= b_min:
                base_grade = grade
                base_reason = f"量比{vol:.2f}x, 实体{body:.1%}"
                break

        if channel_penalty and base_grade in ('S', 'A'):
            # 有通道感降一级
            downgrade = {'S': 'A', 'A': 'B', 'B': 'C'}
            base_grade = downgrade.get(base_grade, base_grade)
            base_reason += ", 通道感降级"

        return base_grade, base_reason

    def _grade_sf(self, df: pd.DataFrame, dl_start: int | None) -> tuple[str, str]:
        """释放级别评级：1st=S, 2nd=A, 3rd=C"""
        if dl_start is None or dl_start < 20:
            return 'A', "数据不足,保守2nd"

        # 检测释放幅度
        before = df.iloc[max(0, dl_start - 60):dl_start]
        if len(before) < 20:
            return 'A', "数据不足,保守2nd"

        b_range = (before["最高"].max() - before["最低"].min()) / before["最低"].min()
        if b_range > 0.25:
            return 'C', f"3rd(前期释放{b_range:.1%}>25%)"
        elif b_range > 0.12:
            return 'A', f"2nd(部分释放{b_range:.1%})"
        return 'S', f"1st(积累充分{b_range:.1%})"

    # ── 独立结构检测（返回起始索引） ──

    def _detect_consolidation_phase_v2(self, df: pd.DataFrame) -> int | None:
        """检测独立结构起始位置"""
        n = len(df)
        high = df["最高"].values
        low = df["最低"].values
        close = df["收盘"].values

        # 过高点检测
        overshoot = overshoot_detect(df)
        offset = 0
        if overshoot["has_overshoot"]:
            offset = overshoot["position"]
            high = high[offset:]
            low = low[offset:]
            close = close[offset:]
            n = n - offset

        for bars, max_range in [(120, 0.20), (90, 0.30), (60, 0.35)]:
            if n < bars + 10:
                continue
            search_end = n - bars + 1
            if search_end <= 0:
                continue
            for start in range(search_end - 1, max(0, search_end - 20) - 1, -1):
                seg_h = high[start:start + bars].max()
                seg_l = low[start:start + bars].min()
                avg_c = close[start:start + bars].mean()
                # 结构级别检测
                if (seg_h - seg_l) / avg_c < 0.03:
                    continue
                if (seg_h - seg_l) / avg_c <= max_range:
                    return offset + start if offset > 0 else start
        return None

    # ── 综合评级 ──

    def grade(self, df: pd.DataFrame) -> dict:
        """对股票进行完整评级（V2 课程标准版）

        Returns:
            {"grade": "S"/"A"/"B"/"C",
             "scores": {"DL": ("S", "说明"), ...},
             "dl_start": int|None,
             "match": bool}
        """
        if df.empty or len(df) < 60:
            return {"grade": "C", "scores": {"数据": ("C", f"仅{len(df)}行")}, "dl_start": None, "match": False}

        # ── Tier 0：一票否决 ──
        reject = self._tier0_reject(df)
        if reject:
            return {"grade": "C", "scores": {"Tier0": ("C", reject)}, "dl_start": None, "match": False}

        scores = {}

        # ── Tier 1：核心三要素 ──

        # PT 平台测试（最优先）
        pt_g, pt_r = self._grade_pt(df)
        scores["PT平台测试"] = (pt_g, pt_r)

        # TY 统一区间
        ty_g, ty_r = self._grade_ty(df)
        scores["TY统一区间"] = (ty_g, ty_r)

        # DN 动能
        dn_g, dn_r = self._grade_dn(df)
        scores["DN动能"] = (dn_g, dn_r)

        # ── Tier 2：质量分级 ──

        # DL 独立结构
        dl_g, dl_r = self._grade_dl(df)
        scores["DL独立结构"] = (dl_g, dl_r)

        # LK 轮廓质量
        lk_g, lk_r = self._grade_lk(df)
        scores["LK轮廓质量"] = (lk_g, lk_r)

        # SF 释放级别
        dl_start = None
        sf_g, sf_r = 'C', "无法判断"
        try:
            dl_start = self._detect_consolidation_phase_v2(df)
            if dl_start is not None:
                sf_g, sf_r = self._grade_sf(df, dl_start)
            else:
                sf_g, sf_r = 'C', "未找到独立结构"
        except Exception:
            sf_g, sf_r = 'C', "计算异常"
        scores["SF释放级别"] = (sf_g, sf_r)

        # ── Tier 3：加减分 ──
        # 通道感、像素感已在 LK 和 DN 中处理
        # 过高点已在 DL 中处理

        # ── 综合评级 ──
        grade = self._calculate_overall_grade(scores)
        match = grade in ('S', 'A', 'B')

        return {"grade": grade, "scores": scores, "dl_start": dl_start, "match": match}

    def _calculate_overall_grade(self, scores: dict) -> str:
        """根据各条件评级计算综合评级

        优先级逻辑：
        - Tier 1（PT/TY/DN）任一C → 整体C
        - Tier 2（DL/LK/SF）任一C → 最高B
        - S级：全部≥A，且至少3个S
        - A级：全部≥A 或 仅1个B
        - B级：最多3个B
        """
        core = ["PT平台测试", "TY统一区间", "DN动能", "DL独立结构", "LK轮廓质量", "SF释放级别"]
        grades = [scores.get(k, ('C', ''))[0] for k in core]

        # Tier 1 一票否决
        tier1 = grades[:3]
        if any(g == 'C' for g in tier1):
            return 'C'

        # Tier 2 任一C → 最高B
        tier2 = grades[3:]
        tier2_c = sum(1 for g in tier2 if g == 'C')
        if tier2_c > 0:
            # Tier 1 不能有B
            if any(g == 'B' for g in tier1):
                return 'C'
            return 'B'

        # 计算各等级数量
        s_count = sum(1 for g in grades if g == 'S')
        b_count = sum(1 for g in grades if g == 'B')

        # S级：全部≥A，且至少3个S
        if s_count >= 3 and b_count == 0:
            return 'S'

        # A级：全部≥A 或 仅1个B
        if b_count <= 1:
            return 'A'

        # B级：最多3个B
        if b_count <= 3:
            return 'B'

        return 'C'

    def quick_prefilter(self, df: pd.DataFrame) -> bool:
        """快速预过滤（课程标准版）"""
        n = len(df)
        if n < 60:
            return False

        close = df["收盘"].values
        high = df["最高"].values
        low = df["最低"].values

        # 1. 近期波动不能过大
        recent_high = high[-60:].max()
        recent_low = low[-60:].min()
        if (recent_high - recent_low) / close[-1] > 0.50:
            return False

        # 2. 排除完全释放
        low_60 = low[-60:].min()
        if low_60 > 0 and (close[-1] - low_60) / low_60 > 0.40:
            return False

        # 3. 排除通道上涨
        recent_highs = high[-8:]
        recent_lows = low[-8:]
        if all(recent_highs[i] <= recent_highs[i + 1] for i in range(min(7, len(recent_highs) - 1))):
            total_range = recent_highs.max() - recent_lows.min()
            trend_range = abs(recent_highs[-1] - recent_highs[0])
            if trend_range > 0 and total_range / trend_range < 0.4:
                return False

        return True

    def prebreak_grade(self, df: pd.DataFrame) -> dict:
        """预突破评级：检查 DL/PT/LK/TY/SF 5 条件（不含 DN）

        用于"补充计划"模式——事先画线，挂条件单等待突破触发。
        DN 动能不检查，改为输出突破触发价和止损价。

        Returns:
            {"grade": "S"/"A"/"B"/"C",
             "scores": {...},
             "trigger_price": float,      # 突破买入条件单触发价
             "stop_loss": float,          # 原始止损价
             "risk_per_share": float,     # 每股风险
             "ty_high": float,            # TY统一区间上沿
             "ty_low": float,             # TY统一区间下沿
             "match": bool}
        """
        if df.empty or len(df) < 60:
            return {"grade": "C", "scores": {"数据": ("C", f"仅{len(df)}行")},
                    "trigger_price": 0, "stop_loss": 0, "risk_per_share": 0,
                    "ty_high": 0, "ty_low": 0, "match": False}

        # Tier 0 一票否决（预突破专用——比正常模式宽松，不卡回踩）
        reject = self._tier0_prebreak(df)
        if reject:
            return {"grade": "C", "scores": {"Tier0": ("C", reject)},
                    "trigger_price": 0, "stop_loss": 0, "risk_per_share": 0,
                    "ty_high": 0, "ty_low": 0, "match": False}

        # TY 边界检测（核心——条件单需要）
        ty_info = self._detect_ty_boundaries(df)

        scores = {}

        # PT 平台测试
        pt_g, pt_r = self._grade_pt(df)
        scores["PT平台测试"] = (pt_g, pt_r)

        # TY 统一区间
        ty_g, ty_r = self._grade_ty(df)
        scores["TY统一区间"] = (ty_g, ty_r)

        # DL 独立结构
        dl_g, dl_r = self._grade_dl(df)
        scores["DL独立结构"] = (dl_g, dl_r)

        # LK 轮廓质量
        lk_g, lk_r = self._grade_lk(df)
        scores["LK轮廓质量"] = (lk_g, lk_r)

        # SF 释放级别
        sf_g, sf_r = 'C', "无法判断"
        try:
            dl_start = self._detect_consolidation_phase_v2(df)
            if dl_start is not None:
                sf_g, sf_r = self._grade_sf(df, dl_start)
            else:
                sf_g, sf_r = 'C', "未找到独立结构"
        except Exception:
            sf_g, sf_r = 'C', "计算异常"
        scores["SF释放级别"] = (sf_g, sf_r)

        # 综合评级（5条件，不含DN）
        grade = self._calculate_prebreak_grade(scores)
        match = grade in ('S', 'A', 'B')

        # 条件单参数
        if match and ty_info["ty_high"] > 0:
            trigger = round(ty_info["ty_high"] * 1.002, 2)  # TY上沿 + 0.2%
            stop = round(ty_info["ty_low"] * 0.998, 2)       # TY下沿 - 0.2%
            risk_per_share = round(trigger - stop, 2)
        else:
            trigger = 0
            stop = 0
            risk_per_share = 0

        return {
            "grade": grade,
            "scores": scores,
            "trigger_price": trigger,
            "stop_loss": stop,
            "risk_per_share": risk_per_share,
            "ty_high": ty_info.get("ty_high", 0),
            "ty_low": ty_info.get("ty_low", 0),
            "match": match,
        }

    def _detect_ty_boundaries(self, df: pd.DataFrame) -> dict:
        """检测统一区间精确边界（用于条件单价格计算）

        Returns:
            {"ty_high": float, "ty_low": float, "ty_bars": int}
        """
        n = len(df)
        high = df["最高"].values
        low = df["最低"].values
        close = df["收盘"].values

        for bars in [5, 4, 3]:
            if n < bars + 5:
                continue
            for end in range(n - 1, max(0, n - 20) - 1, -1):
                start = end - bars + 1
                if start < 0:
                    continue
                seg_high = high[start:end + 1].max()
                seg_low = low[start:end + 1].min()
                seg_range = (seg_high - seg_low) / close[end] if close[end] > 0 else 999
                if seg_range <= 0.05:
                    return {"ty_high": round(seg_high, 2), "ty_low": round(seg_low, 2), "ty_bars": bars}
        return {"ty_high": 0, "ty_low": 0, "ty_bars": 0}

    def _calculate_prebreak_grade(self, scores: dict) -> str:
        """预突破综合评级（5条件，不含DN）—— 比 grade() 更严格

        预突破模式是"上膛待发"，结构质量必须高才值得挂单等突破。
        标准（都比正常评级的B级上限收紧）：
        - 任一条件为 C → C（不通过）
        - S级：全部≥A + ≥3个S
        - A级：全部≥A + ≥2个S
        - B级：全部≥A（不允许B）
        """
        core = ["PT平台测试", "TY统一区间", "DL独立结构", "LK轮廓质量", "SF释放级别"]
        grades = [scores.get(k, ('C', ''))[0] for k in core]

        # 任一 C → 淘汰
        if any(g == 'C' for g in grades):
            return 'C'

        # 不允许 B — 结构必须过硬
        if any(g == 'B' for g in grades):
            return 'C'

        s_count = sum(1 for g in grades if g == 'S')

        if s_count >= 3:
            return 'S'
        if s_count >= 2:
            return 'A'
        return 'B'

    # ── 兼容接口 ──

    def filter(self, df: pd.DataFrame) -> bool:
        result = self.grade(df)
        return result["match"]

    def debug_filter(self, df: pd.DataFrame) -> dict:
        result = self.grade(df)
        steps = {}
        for name, (g, r) in result["scores"].items():
            passed = g != 'C'
            steps[name] = {"passed": passed, "reason": f"[{g}] {r}"}
        steps["综合评级"] = {"passed": result["match"], "reason": f"评级: {result['grade']}"}
        return {"match": result["match"], "steps": steps}
