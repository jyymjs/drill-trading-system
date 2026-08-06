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
import numpy as np
import pandas as pd
from 分析决策.分析.indicators import (
    accumulation_zone,
    channel_detect,
    conflict_zscore,
    flatness_score,
    gap_limit_detect,
    one_line_detect,
    overshoot_detect,
    pixelation_score,
    platform_test_count,
    poc_level,
    profile_compactness,
    reaction_quality,
    step_down_trace,
    support_bounce,
)
from 策略.核心策略.base import BaseStrategy


class ZuanQianStrategy(BaseStrategy):
    """钻潜交易系统 - 统一评级策略 V2（课程标准版）"""

    name = "钻潜评级策略"
    description = "基于钻潜交易系统6条件 DL/PT/LK/TY/DN/SF，输出S/A/B/C评级"

    required_indicators = ["VOL_RATIO", "BODY_RATIO", "MA20", "MA5"]

    # ── 评级阈值 ──

    # DL 独立结构（K线根数阈值）——2026-08-04 T-4.3 裁决：一档化，根数仅作 ≥60 通过门槛
    # （T-4.2 验证：70+ 根 0.448R 差于 60-69 根 0.596R（prebreak/20d），「结构越大越被充分定价」；
    #   评级维度交还结构紧凑度 DL_RANGE，2026-08-04 老板确认 T-4.3 方案1）
    DL_S = 60
    DL_A = 60
    DL_B = 60
    DL_CANDS = (60, 60, 60)   # 独立结构候选根数（S/A/B），_grade_dl 使用（T-4.3 一档化：仅容差区分）
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

    # TY 统一区间（2024年修正：3根=不足C级，非瑕疵B级）
    TY_S = 5       # 最少K线 — 优秀
    TY_A = 4       # 最少K线 — 达标
    TY_B = 4       # 最少K线 — 正常（3根是不足C）
    TY_RANGE_S = 0.03
    TY_RANGE_A = 0.05
    TY_RANGE_B = 0.08

    # DN 动能（相对量比, decisive）——基于至多3根合并规则（T-4.2 重写：相对比较口径）
    # 老师：动能 = 与之前调整段的强弱对比（相对比较）+ 收完（影线少/阳线连续），非绝对量比/实体门槛
    # 相对量比 = 启动K合并窗口均量 ÷ 前面调整段日均量（1.0=持平，1.5=明显放量）
    # decisive = 阳线占比*0.5 + 实体/振幅均值*0.5（收完/坚决度）
    # 1根单K达标=S(强突破), 2根合并达标=A(良好), 3根合并达标=B(边缘)
    DN_S = (1.5, 0.50)   # 单根→强冲突感
    DN_A = (1.2, 0.45)   # 2根合并→中等
    DN_B = (1.0, 0.40)   # 3根合并→偏弱

    # SF 释放级别（涨幅阈值）
    SF_FULL_RELEASE = 0.40  # 完全释放排除阈值

    # ── 量化阈值（视觉概念→数值） ──
    # 像素感（影线占比，越高越好）
    PX_S = 0.55
    PX_A = 0.45
    PX_B = 0.35

    # 冲突感（z-score）
    CZ_S = 2.0
    CZ_A = 1.5
    CZ_B = 1.0

    # 横盘感（综合评分 0~1）
    FL_S = 0.60
    FL_A = 0.45
    FL_B = 0.30

    # 明显反应（覆盖率）
    RQ_S = 0.60
    RQ_A = 0.45
    RQ_B = 0.30

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

    def _tier0_reject(self, df: pd.DataFrame, check_retracement: bool = True) -> str | None:
        """Tier 0 一票否决：结构太小 / 完全释放 / 无回踩(可选)

        Args:
            check_retracement: True=标准模式（检查回踩），False=预突破模式（跳过）
        """
        n = len(df)
        close = df["收盘"].values
        low = df["最低"].values

        # 1. 结构太小
        if n < 60:
            return f"结构太小(仅{n}根)"

        # 2. 完全释放（从60日低点涨幅>40%）
        low_60 = low[-60:].min()
        if low_60 > 0 and (close[-1] - low_60) / low_60 > self.SF_FULL_RELEASE:
            return f"完全释放({(close[-1]-low_60)/low_60:.1%}>40%)"

        # 3. 通道感检测（2024年扫盘最高频排除条件）
        ch = channel_detect(df)
        if ch["is_channel"]:
            return f"通道上涨(R²={ch['strength']:.2f})"

        # 3.5 经常跳空/涨跌停（品种筛选一票否决#4 · 补完计划 G1）
        # 知识卡原文「经常跳空/涨跌停品种——连续性不好」：跳空会跳过止损，
        # 本应"亏 1R 止损"实际可能跳空/封板成交在更差价位；最新日一字封板
        # 涨停无法买入、跌停无法卖出。
        gl = gap_limit_detect(df)
        if gl["excluded"]:
            return f"经常跳空/涨跌停({gl['reason']})"

        # 3.6 一字形排列（品种筛选一票否决#5 · 补完计划 G2）
        # 知识卡原文「一字形排列（调整全是一字形）」：连续性差的极端形态，
        # 进场后无法按规则管理。
        ol = one_line_detect(df)
        if ol["excluded"]:
            return f"一字形排列({ol['reason']})"

        # 4. 向下踩的轨迹 + 明显反应——仅标准模式检查（量化版）
        if check_retracement:
            # 4a. 向下踩轨迹（下影线+阳线反弹模式）
            trace = step_down_trace(df)
            if not trace["has_trace"]:
                # 无向下踩 → 可能是"直接往上冲"
                recent_close = close[-10:]
                direct_rush = all(recent_close[i] <= recent_close[i + 1]
                                  for i in range(min(5, len(recent_close) - 1)))
                if direct_rush:
                    return "直接上涨无回踩轨迹"

            # 4b. 明显反应质量（踩了之后反弹的速度和幅度）
            # 有踩但没有明显反应 → "碰一下慢慢蹭上去不算"：不在此否决（Tier 0 只做一票否决）
            # 降级逻辑实现于 grade() 的 Tier 3（2026-08-05 老板拍板 A1），
            # 避免与 _tier0_reject 的"否决理由"字符串返回语义混淆

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

        candidates = [(self.DL_CANDS[0], self.DL_RANGE_S, 'S'),
                      (self.DL_CANDS[1], self.DL_RANGE_A, 'A'),
                      (self.DL_CANDS[2], self.DL_RANGE_B, 'B')]
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
        test_count = platform_test_count(df, tolerance=0.005, min_gap=8)  # T-4.4 校准：原 0.01/3 致 99% S 虚高
        if test_count >= self.PT_S:
            return 'S', f"{test_count}次有效平台测试"
        elif test_count >= self.PT_A:
            return 'A', f"{test_count}次有效平台测试"
        elif test_count >= self.PT_B:
            return 'B', f"{test_count}次平台测试"
        return 'C', f"仅{test_count}次测试, 平台不充分"

    def _grade_lk(self, df: pd.DataFrame) -> tuple[str, str]:
        """轮廓质量评级：S/A/B/C

        量化三个维度（2024年新增）：
        1) 像素感评分（影线占比+连续性+振幅一致性）
        2) 横盘感评分（斜率+MAD离散度）
        3) 紧凑度（实体占比，原始指标）
        """
        compactness = profile_compactness(df, window=20)
        px = pixelation_score(df, window=30)
        fl = flatness_score(df, window=20)

        # 综合评分（紧凑度权重最高，像素感+横盘感辅助）
        score = compactness * 0.45 + px * 0.25 + fl * 0.30

        # 像素感严重 → 硬降级
        if px < self.PX_B:
            return 'C', f"像素感严重(px={px:.2f}, 紧凑{compactness:.2f})"

        # 横盘感极差 → 硬降级
        if fl < self.FL_B:
            return 'C', f"无横盘感(fl={fl:.2f}, 斜率过大)"

        if score >= self.LK_S and compactness >= 0.45:
            return 'S', f"优质(紧凑{compactness:.2f}, px{px:.2f}, fl{fl:.2f})"
        elif score >= self.LK_A:
            return 'A', f"中等(紧凑{compactness:.2f}, px{px:.2f}, fl{fl:.2f})"
        elif score >= self.LK_B:
            return 'B', f"松散(紧凑{compactness:.2f}, px{px:.2f}, fl{fl:.2f})"
        return 'C', f"轮廓差(紧凑{compactness:.2f}, px{px:.2f}, fl{fl:.2f})"

    def _grade_ty(self, df: pd.DataFrame) -> tuple[str, str]:
        """统一区间评级：S/A/B/C"""
        n = len(df)
        high, low = df["最高"].values, df["最低"].values
        op, cl = df["开盘"].values, df["收盘"].values
        body_ratios = self._body_pct_series(high, low, op, cl)
        ma20 = df["MA20"].values if "MA20" in df.columns else None

        # 最少K线数要求（2024修正：3根=不足C级，非瑕疵）
        thresholds = [(5, 0.03, 'S'), (4, 0.05, 'A'), (4, 0.08, 'B')]
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

                # 统一区间波幅 = 区间振幅 / 基准价（MA20 优先，否则收盘价）
                denom = ma20[end] if ma20 is not None else cl[end]
                if denom <= 0:
                    continue
                seg_range = seg_hl / denom
                if seg_range > max_range:
                    continue
                price_chg = abs(cl[end] - cl[start]) / cl[start] if cl[start] > 0 else 0
                if price_chg > max_range * 2:
                    continue
                return grade, f"{bars}根K线, 波幅{seg_range:.1%}"
        return 'C', "未发现窄幅整理或K线不足"

    def _grade_dn(self, df: pd.DataFrame) -> tuple[str, str]:
        """动能评级：S/A/B/C（基于至多3根合并规则 + 冲突感量化）

        三个维度综合判定：
        1) 量比+实体（原始数值）
        2) 冲突感z-score（启动K vs 调整结构的"露头"程度）
        3) 动能坚决度（连续阳线+影线少）

        降级规则：通道感 / TY-DN间隔>1根
        """
        # 通道感检测
        ch = channel_detect(df)
        channel_penalty = ch["is_channel"]

        # 启动K与TY间隔检测
        ty_end_idx = self._find_last_ty_index(df)

        # 冲突感z-score（量化"露头"）
        n_total = len(df)
        conflict_z = 0.0

        # 尝试不同合并根数：1根→S, 2根→A, 3根→B
        specs = [(1, self.DN_S, 'S'), (2, self.DN_A, 'A'), (3, self.DN_B, 'B')]
        for n, (v_min, b_min), grade in specs:
            if n_total < n:
                continue
            window = df.tail(n)
            dn_start_idx = n_total - n

            # 合并量比（相对口径：启动K窗口均量 vs 前面调整段日均量）
            window_vol = float(window["成交量"].sum())
            ref = df["成交量"].iloc[:dn_start_idx]
            ref_mean = float(ref.tail(20).mean()) if len(ref) > 0 else 0.0
            vol = window_vol / (ref_mean * n) if ref_mean > 0 else 0.0

            # 合并实体
            first_open = window["开盘"].iloc[0]
            last_close = window["收盘"].iloc[-1]
            body = abs(last_close - first_open) / last_close if last_close > 0 else 0

            # 冲突感z-score
            conflict_z = conflict_zscore(df, dn_start_idx)

            # 动能坚决度：合并K线中阳线占比 + 影线少
            decisive = 0.5  # 默认
            try:
                hl = window["最高"].values - window["最低"].values
                yang_count = sum(1 for i in range(len(window))
                                 if window["收盘"].iloc[i] > window["开盘"].iloc[i])
                yang_ratio = yang_count / len(window)
                shadow_ratios = np.divide(
                    np.abs(window["收盘"].values - window["开盘"].values),
                    hl, out=np.ones(len(window)), where=hl > 0
                )
                decisive = float(yang_ratio * 0.5 + np.mean(shadow_ratios) * 0.5)
            except Exception:
                pass

            # 判定通过：相对量比达标 且 收完度达标（实体仅展示，不设门槛——老师语义）
            if vol >= v_min and decisive >= b_min:
                base_grade = grade
                base_reason = f"量比{vol:.2f}x, 收完{decisive:.2f}(并{n}根), 实体{body:.1%}, z={conflict_z:.1f}"

                # 冲突感加分/降级
                if conflict_z >= self.CZ_S and base_grade != 'S':
                    base_reason += ", 冲突感强→提级"
                elif conflict_z < self.CZ_B and base_grade in ('S', 'A'):
                    base_grade = {'S': 'A', 'A': 'B'}.get(base_grade, base_grade)
                    base_reason += ", 冲突感不足→降级"

                # 坚决度降级
                if decisive < 0.4 and base_grade in ('S', 'A'):
                    base_grade = {'S': 'A', 'A': 'B'}.get(base_grade, base_grade)
                    base_reason += f", 不坚决(决{decisive:.2f})→降级"

                # 降级规则（通道感 / TY-DN间隔）
                downgrades = []
                if channel_penalty:
                    downgrades.append("通道感")
                ty_gap = None
                if ty_end_idx is not None and ty_end_idx >= 0:
                    ty_gap = dn_start_idx - ty_end_idx
                if ty_gap is not None and ty_gap > 1:
                    downgrades.append(f"TY-DN间隔{ty_gap}根")

                if downgrades:
                    for d in downgrades:
                        base_grade = {'S': 'A', 'A': 'B', 'B': 'C'}.get(base_grade, base_grade)
                    base_reason += ", " + "/".join(downgrades) + "降级"

                return base_grade, base_reason

        return 'C', f"量比{vol:.2f}x, 实体{body:.1%}, z={conflict_z:.1f}(不达标)"

    def _find_last_ty_index(self, df: pd.DataFrame) -> int | None:
        """查找最后一个统一区间的结束位置

        Returns:
            TY最后一根K线的索引（0-based），未找到返回None
        """
        n = len(df)
        high, low = df["最高"].values, df["最低"].values
        op, cl = df["开盘"].values, df["收盘"].values
        body_ratios = self._body_pct_series(high, low, op, cl)
        ma20 = df["MA20"].values if "MA20" in df.columns else None

        for bars in [5, 4]:
            if n < bars + 5:
                continue
            for end in range(n - 1, max(0, n - 30) - 1, -1):
                start = end - bars + 1
                if start < 0:
                    continue
                seg_body = body_ratios[start:end + 1]
                if seg_body.mean() > 0.6:
                    continue
                seg_hl = (high[start:end + 1].max() - low[start:end + 1].min())
                denom = ma20[end] if ma20 is not None and not pd.isna(ma20[end]) else cl[end]
                if denom <= 0:
                    continue
                seg_range = seg_hl / denom
                if seg_range <= 0.05:
                    return end
        return None

    def _grade_sf(self, df: pd.DataFrame, dl_start: int | None) -> tuple[str, str]:
        """释放级别评级：1st=S, 2nd=A, 3rd=C

        数据不足裁决（2026-08-05 老板拍板 A2）：dl_start 缺失或 <20 → 看不清 → 观望 C
        （原实现给 A 属语义反了：数据不足应观望，而非放行"伪A"）
        """
        if dl_start is None or dl_start < 20:
            return 'C', "数据不足,看不清观望"

        # 检测释放幅度
        before = df.iloc[max(0, dl_start - 60):dl_start]
        if len(before) < 20:
            return 'C', "数据不足,看不清观望"

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

        A1 降级（2026-08-05 老板拍板）：有回踩但无明显反应（"碰一下慢慢蹭上去"）
        → 综合评级降一档（S→A / A→B / B→C），见下方 Tier 3 注释。

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
        except (KeyError, ValueError, IndexError, TypeError) as e:
            sf_g, sf_r = 'C', f"计算异常:{e}"
        scores["SF释放级别"] = (sf_g, sf_r)

        # ── Tier 3：加减分 ──
        # 通道感、像素感已在 LK 和 DN 中处理
        # 过高点已在 DL 中处理
        # 回踩轨迹和反应质量作为加分项

        trace = step_down_trace(df)
        react = reaction_quality(df)
        bonus = 0  # 加分累计

        if trace["quality"] == "good":
            bonus += 1
            scores["回踩轨迹"] = ('S', f"深{trace['depth_pct']:.1f}x, 反弹{trace['rebound_pct']:.0%}")
        elif trace["has_trace"]:
            scores["回踩轨迹"] = ('A', f"深{trace['depth_pct']:.1f}x")

        if react["quality"] == "good":
            bonus += 1
            scores["明显反应"] = ('S', f"速度{react['speed']:.1%}, 覆盖{react['coverage']:.0%}")
        elif react["has_reaction"]:
            scores["明显反应"] = ('A', f"覆盖{react['coverage']:.0%}")

        # A1 降级（2026-08-05 老板拍板：蹭上去不算——有回踩但无明显反应 → 综合评级降一档）
        # 设计选择：与 _tier0_reject 的"一票否决"字符串语义隔离（否决即返回 C），
        # 在综合评级算出后统一降档，不混入 _calculate_overall_grade 各分支
        # （其内部提级/降级规则交错，统一降档最不易引入回归）；C 级保持 C
        downgrade_reason = None
        if trace["has_trace"] and not react["has_reaction"]:
            downgrade_reason = f"回踩但无明显反应(覆盖{react['coverage']:.0%})"

        # ── 综合评级 ──
        grade = self._calculate_overall_grade(scores, bonus)
        if downgrade_reason:
            grade = {'S': 'A', 'A': 'B', 'B': 'C', 'C': 'C'}.get(grade, grade)
            scores["回踩反应"] = ('C', f"{downgrade_reason}, 综合评级降一档")
        match = grade in ('S', 'A', 'B')

        # 意图模式标注（独立模式，不影响标准评级；供报告/prebreak 使用）
        intent = None
        try:
            intent = self._check_intent_mode(df)
        except Exception:
            intent = None

        return {"grade": grade, "scores": scores, "dl_start": dl_start, "match": match,
                "intent": intent}

    def _calculate_overall_grade(self, scores: dict, bonus: int = 0) -> str:
        """根据各条件评级计算综合评级

        优先级逻辑：
        - Tier 1（PT/TY/DN）任一C → 整体C
        - Tier 2（DL/LK/SF）任一C → 最高B
        - S级：全部≥A，且至少3个S
        - A级：全部≥A 或 仅1个B
        - B级：最多3个B
        - Tier3加分：回踩轨迹好+反应明显，bonus≥2可提一级
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
            if any(g == 'B' for g in tier1):
                return 'C'
            return 'B'

        # ── T-4.4 裁决（2026-08-04 老板确认完整方案）：去掉「DL 必须 S」硬规则 ──
        # 原 T-4.5 老师硬规则（"DL 必须 S，没有后面的选项"）经 T-4.3 一档化后失真——
        # DL≠S 仅代表容差稍大，回测质量反而更好（DL=S 0.210 vs 非S 0.270，normal/20d）；
        # DL≠S 不再压 B，按正常条件组合评级

        # 计算各等级数量
        s_count = sum(1 for g in grades if g == 'S')
        a_count = sum(1 for g in grades if g == 'A')
        b_count = sum(1 for g in grades if g == 'B')

        # 2024年规则修正：全部A级无一S → 降为B（老师不会关注均A交易）
        all_a_no_s = (s_count == 0 and b_count == 0 and a_count == len(grades))

        # S级（T-4.4 数据校准）：3-4个S + PT必须S + TY≠S + SF≠S
        # （模拟验证：S∈[3,4]=0.266峰值、5S=-0.021亏钱；TY=S负贡献、SF=S最毒、PT=S唯一强正贡献）
        pt_g, ty_g, sf_g = grades[0], grades[1], grades[5]
        if 3 <= s_count <= 4 and b_count == 0 and pt_g == 'S' and ty_g != 'S' and sf_g != 'S':
            return 'S'

        # A级：全部≥A 或 仅1个B
        if b_count <= 1:
            if all_a_no_s:
                # 均A但有回踩+反应双优 → 恢复为A
                return 'A' if bonus >= 2 else 'B'
            return 'A'

        # B级：最多3个B
        if b_count <= 3:
            # 有加分且B少 → 可提为A
            if bonus >= 2 and b_count <= 1:
                return 'A'
            return 'B'

        return 'C'

    # ── 意图模式（内训第14节两脉流程，2026-08-04 补课代码化） ──

    def _check_intent_mode(self, df: pd.DataFrame) -> dict | None:
        """意图模式判定（量化版）

        一脉：独立结构 → 筹码集中区 → POC 验证 → 有利突破或依托
        二脉：关键位三次测试（放宽：可不在一结构内） → 有利突破或明显反应回踩
        硬约束：被回踩/被破位的必须是独立结构（DL≥B 视为结构存在）

        Returns:
            {"mode": "intent", "poc": float, "concentration": float,
             "zone_ratio": float, "support": bool, "reason": str} 或 None
        """
        # 硬约束：独立结构必须存在（DL 至少 B——结构规模足够）
        dl_g, _dl_r = self._grade_dl(df)
        if dl_g in ("C",):
            return None
        # 一脉：筹码集中区（POC 集中度 ≥15% 或 筹码区集中）
        poc = poc_level(df)
        acc = accumulation_zone(df)
        zone_ok = poc["concentration"] > 0.15 or acc["concentrated"]
        if not zone_ok:
            return None
        # 有利突破或依托（老师：意图模式必须看懂行情，禁止当标准模式卡条件）
        # 收紧：依托必须真实（支撑反弹），反应必须质量 good（不取宽松 has_reaction）
        bounce = support_bounce(df)
        react = reaction_quality(df)
        support_ok = bounce.get("has_support", False) or react.get("quality") == "good"
        if not support_ok:
            return None
        parts = []
        if acc.get("poc_near_current"):
            parts.append("POC近当前")
        if bounce.get("has_support"):
            parts.append(bounce.get("reason", "依托"))
        if react.get("has_reaction"):
            parts.append("明显反应")
        return {
            "mode": "intent",
            "poc": round(poc["poc"], 3),
            "concentration": round(poc["concentration"], 3),
            "zone_ratio": round(acc["zone_ratio"], 3),
            "support": bool(bounce.get("has_support", False)),
            "reason": "/".join(parts) if parts else "筹码集中+反应",
        }

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

        # Tier 0 一票否决（预突破模式——不卡回踩）
        reject = self._tier0_reject(df, check_retracement=False)
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
        except (KeyError, ValueError, IndexError, TypeError) as e:
            sf_g, sf_r = 'C', f"计算异常:{e}"
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

        # S级（T-4.4 同步校准）：3-4个S + TY≠S + SF≠S（TY/SF 的 S 档负贡献）
        if 3 <= s_count <= 4 and grades[1] != 'S' and grades[4] != 'S':
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
