"""
钻潜交易系统 - 标准模式选股策略

基于路肖南钻潜交易内训视频知识提取，将视觉K线规则量化为可计算条件：

核心条件（6条件体系）:
1. 独立结构 (DL)  - 长期盘整，≥90根K线，低波动
2. 平台位 (PT)    - 水平支撑/阻力，多次测试
3. 轮廓 (LK)      - 筹码积累形态，平整横盘
4. 统一区间 (TY)  - 极窄幅，小K线，结构末尾 ≥4根
5. 动能 (DN)      - 启动K线，相对冲突感 + 绝对力度
6. 释放级别       - 积累 > 消耗

简化实现：使用波动率收缩 + 窄幅整理 + 放量突破 的经典模式
参考：Mark Minervini VCP (波动率收缩) + 钻潜交易系统的独立结构和统一区间

=========================================
优化历史：
  2026-07-29 v2: 向量化检测 + 预过滤 + 诊断模式 + 按需指标
=========================================
"""
import pandas as pd
import numpy as np
from strategy.base import BaseStrategy


class ZuanQianStandardStrategy(BaseStrategy):
    """钻潜标准模式 - 波动率收缩+窄幅整理+放量突破"""

    name = "钻潜标准模式"
    description = "波动率收缩+窄幅整理+放量突破（基于钻潜交易系统6条件量化实现）"

    # 声明策略实际需要的指标列（减少不必要的计算）
    required_indicators = ["MA20", "VOL_RATIO", "MA_CROSS", "RSI"]

    # --- 可调参数（未经用户同意不得修改） ---
    DL_MIN_BARS = 90           # 独立结构最少K线数
    DL_MAX_RANGE_PCT = 0.30    # 独立结构最大波幅(相对价格)
    TY_MIN_BARS = 4            # 统一区间最少K线数
    TY_MAX_BODY_RATIO = 0.6    # 统一区间K线实体/波幅最大比例
    TY_MAX_RANGE_PCT = 0.05    # 统一区间最大波幅(相对MA)
    DN_MIN_VOL_RATIO = 1.5     # 启动放量最小倍数
    DN_MIN_BODY_PCT = 0.03     # 启动K线最小实体幅度
    VCP_MIN_CONTRACTION = 0.4  # 波动率最小收缩比例(相对于前期)

    # --- 内部常量（不对外暴露） ---
    _LOOKBACK_WINDOW = 150     # 至少需要多少根K线
    _PRE_BARS = 60             # 独立结构前检查多少根K线

    def get_params(self) -> dict:
        return {
            "独立结构最少K线": self.DL_MIN_BARS,
            "独立结构最大波幅": f"{self.DL_MAX_RANGE_PCT*100:.0f}%",
            "统一区间最少K线": self.TY_MIN_BARS,
            "统一区间最大波幅": f"{self.TY_MAX_RANGE_PCT*100:.1f}%",
            "启动放量倍数": f"{self.DN_MIN_VOL_RATIO:.1f}x",
            "波动率收缩比例": f"{self.VCP_MIN_CONTRACTION*100:.0f}%",
        }

    @staticmethod
    def _check_params():
        """参数合理性校验（在 filter 开头调用）"""
        assert 0 < ZuanQianStandardStrategy.DL_MAX_RANGE_PCT < 1, \
            "DL_MAX_RANGE_PCT 必须在 0~1 之间"
        assert 0 < ZuanQianStandardStrategy.TY_MAX_RANGE_PCT < 0.5, \
            "TY_MAX_RANGE_PCT 必须在 0~0.5 之间"
        assert ZuanQianStandardStrategy.DL_MIN_BARS >= 20, \
            "DL_MIN_BARS 不能小于20"

    # ------------------------------------------------------------------ #
    #  快速预过滤：在指标计算前快速排除明显不符合的股票
    # ------------------------------------------------------------------ #
    def quick_prefilter(self, df: pd.DataFrame) -> bool:
        """快速预过滤（仅使用基础K线列，不依赖任何技术指标）

        Returns:
            False = 直接跳过（无需继续），True = 需完整检测
        """
        if len(df) < self._LOOKBACK_WINDOW:
            return False

        close = df["收盘"].values
        high = df["最高"].values
        low = df["最低"].values
        n = len(df)

        # 1) 近120天波动幅度过大 → 不符合独立结构（盘整）
        lookback = min(120, n)
        recent_high = high[-lookback:].max()
        recent_low = low[-lookback:].min()
        recent_range = (recent_high - recent_low) / close[-lookback]
        if recent_range > self.DL_MAX_RANGE_PCT * 2.5:
            return False

        # 2) 最新收盘价在全市场均线附近（用20日均线近似 → 用收盘价的20日均值代替）
        #  简化：最近20天均价 > 60天均价 → 至少不是明显下跌趋势
        ma20_approx = close[-20:].mean()
        ma60_approx = close[-60:].mean()
        if ma20_approx < ma60_approx * 0.90:
            return False

        # 3) 最近有明显异动（暴涨暴跌）→ 不太可能处于盘整末尾
        recent_volatility = (high[-20:].max() - low[-20:].min()) / close[-1]
        if recent_volatility > 0.25:
            return False

        return True

    # ------------------------------------------------------------------ #
    #  向量化检测函数（替代旧版 Python 滑动窗口循环）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _body_pct_series(high: np.ndarray, low: np.ndarray,
                          open_: np.ndarray, close: np.ndarray) -> np.ndarray:
        """K线实体占波幅比例（向量化）"""
        hl = high - low
        body = np.abs(close - open_)
        # 防除零：波幅为0时返回1
        result = np.divide(body, hl, out=np.ones_like(body), where=hl > 0)
        return np.clip(result, 0.0, 1.0)

    def _detect_consolidation_phase(self, df: pd.DataFrame) -> int | None:
        """检测独立结构（DL）起始位置（向量化实现）

        用滚动最高/最低替代 Python 循环。
        """
        n = self.DL_MIN_BARS
        total = len(df)
        if total < n + 10:
            return None

        high = df["最高"].values
        low = df["最低"].values
        close = df["收盘"].values

        # 滚动窗口最高/最低
        # 从后往前找最近一段符合条件的盘整
        search_start = max(0, total - n * 3)
        search_end = total - n + 1

        # 用向量化计算所有窗口的波幅
        # 用 as_strided 或简单循环+计算：只计算几个候选位置
        # 从最新往前最多检查 n/2 个位置
        candidates = np.linspace(search_start, search_end - 1,
                                 min(n // 2, search_end - search_start), dtype=int)

        for start in reversed(candidates):
            end = start + n
            segment_high = high[start:end].max()
            segment_low = low[start:end].min()
            avg_price = close[start:end].mean()
            range_pct = (segment_high - segment_low) / avg_price

            if range_pct <= self.DL_MAX_RANGE_PCT:
                return start
        return None

    def _detect_uniform_interval(self, df: pd.DataFrame,
                                  dl_start: int) -> int | None:
        """检测统一区间（TY）在独立结构末尾出现（向量化实现）"""
        n = self.TY_MIN_BARS
        high = df["最高"].values
        low = df["最低"].values
        op = df["开盘"].values
        cl = df["收盘"].values
        total = len(df)

        # 计算所有K线的实体比例（一次性）
        body_ratios = self._body_pct_series(high, low, op, cl)

        # 在结构尾部查找：从最新K线往前最多30根
        lookback = min(30, total - dl_start)
        search_start = max(dl_start, total - lookback)
        search_end = total - n + 1

        if search_end <= search_start:
            return None

        # 候选窗口：取最近的 search_end - search_start 个位置
        for end in range(search_end - 1, search_start - 1, -1):
            start = end - n + 1
            segment_body = body_ratios[start:end + 1]
            seg_high = high[start:end + 1].max()
            seg_low = low[start:end + 1].min()
            ma20 = df["MA20"].iloc[end]
            if pd.isna(ma20) or ma20 == 0:
                continue

            # 条件合并判断（短路：不满足就跳过）
            if segment_body.mean() > self.TY_MAX_BODY_RATIO:
                continue
            range_pct = (seg_high - seg_low) / ma20
            if range_pct > self.TY_MAX_RANGE_PCT:
                continue
            price_chg = (cl[end] - cl[start]) / cl[start]
            if abs(price_chg) > self.TY_MAX_RANGE_PCT * 2:
                continue
            return start
        return None

    def _check_momentum(self, df: pd.DataFrame, ty_start: int) -> bool:
        """检测动能（DN）"""
        latest = df.iloc[-1]
        ty_high = df.iloc[ty_start:]["最高"].max()

        if latest["收盘"] <= ty_high:
            return False
        if latest.get("VOL_RATIO", 0) < self.DN_MIN_VOL_RATIO:
            return False
        body = abs(latest["收盘"] - latest["开盘"])
        if body / latest["收盘"] < self.DN_MIN_BODY_PCT:
            return False

        # 冲突感：当前K线波幅显著大于TY平均波幅
        ty_segment = df.iloc[ty_start:-1]
        if len(ty_segment) > 0:
            avg_ty_range = (ty_segment["最高"] - ty_segment["最低"]).mean()
            cur_range = latest["最高"] - latest["最低"]
            if cur_range < avg_ty_range * 1.5:
                return False
        return True

    def _check_release_level(self, df: pd.DataFrame, dl_start: int) -> int:
        """判断释放级别（1st/2nd/3rd）"""
        if dl_start < self._PRE_BARS:
            return 2
        before = df.iloc[max(0, dl_start - self._PRE_BARS):dl_start]
        if len(before) < 20:
            return 2
        before_range = (before["最高"].max() - before["最低"].min()) / before["最低"].min()
        if before_range > 0.25:
            return 3
        elif before_range > 0.12:
            return 2
        return 1

    # ------------------------------------------------------------------ #
    #  主筛选逻辑
    # ------------------------------------------------------------------ #
    def filter(self, df: pd.DataFrame) -> bool:
        self._check_params()
        if df.empty or len(df) < self._LOOKBACK_WINDOW:
            return False

        dl_start = self._detect_consolidation_phase(df)
        if dl_start is None:
            return False

        ty_start = self._detect_uniform_interval(df, dl_start)
        if ty_start is None:
            return False

        if not self._check_momentum(df, ty_start):
            return False

        release = self._check_release_level(df, dl_start)
        if release == 3:
            return False

        latest = df.iloc[-1]
        if latest["收盘"] < latest.get("MA20", 0) * 0.98:
            return False
        return True

    # ------------------------------------------------------------------ #
    #  诊断模式：输出每步检测结果
    # ------------------------------------------------------------------ #
    def debug_filter(self, df: pd.DataFrame) -> dict:
        """诊断模式：返回逐步检测结果"""
        steps = {}
        if df.empty or len(df) < self._LOOKBACK_WINDOW:
            return {
                "match": False,
                "steps": {"数据长度": {
                    "passed": False,
                    "reason": f"数据不足: {len(df)}行 < {self._LOOKBACK_WINDOW}行",
                }},
            }

        steps["数据长度"] = {"passed": True, "reason": f"{len(df)}行"}

        dl_start = self._detect_consolidation_phase(df)
        steps["独立结构(DL)"] = {
            "passed": dl_start is not None,
            "reason": f"起始于 idx={dl_start}" if dl_start is not None else "未找到≥90根K线的盘整期",
        }
        if dl_start is None:
            return {"match": False, "steps": steps}

        ty_start = self._detect_uniform_interval(df, dl_start)
        steps["统一区间(TY)"] = {
            "passed": ty_start is not None,
            "reason": f"起始于 idx={ty_start}" if ty_start is not None else "结构末尾无窄幅整理",
        }
        if ty_start is None:
            return {"match": False, "steps": steps}

        momentum = self._check_momentum(df, ty_start)
        vol_ratio = df.iloc[-1].get("VOL_RATIO", 0)
        steps["动能(DN)"] = {
            "passed": momentum,
            "reason": f"量比={vol_ratio:.2f} {'>= ' + str(self.DN_MIN_VOL_RATIO) if momentum else '< ' + str(self.DN_MIN_VOL_RATIO)}",
        }
        if not momentum:
            return {"match": False, "steps": steps}

        release = self._check_release_level(df, dl_start)
        release_names = {1: "1st(积累充分)", 2: "2nd(部分释放)", 3: "3rd(释放过度)"}
        steps["释放级别"] = {
            "passed": release != 3,
            "reason": release_names.get(release, f"level={release}"),
        }
        if release == 3:
            return {"match": False, "steps": steps}

        latest = df.iloc[-1]
        ma20_val = latest.get("MA20", 0)
        steps["均线过滤"] = {
            "passed": latest["收盘"] >= ma20_val * 0.98,
            "reason": f"收盘={latest['收盘']:.2f} MA20={ma20_val:.2f}",
        }

        match = all(s["passed"] for s in steps.values())
        return {"match": match, "steps": steps}


class ZuanQianVCPStrategy(BaseStrategy):
    """钻潜VCP版本 - 更通用的波动率收缩策略"""

    name = "钻潜VCP模式"
    description = "通用型波动率收缩突破（放宽独立结构要求，适合更广的A股场景）"

    required_indicators = ["MA20", "VOL_RATIO"]

    # --- 可调参数（未经用户同意不得修改） ---
    LOOKBACK = 120
    VCP_WINDOW = 60
    TIGHT_WINDOW = 10
    VOL_SURGE = 1.8
    BODY_MIN = 0.025

    def get_params(self) -> dict:
        return {
            "回溯期": self.LOOKBACK,
            "VCP窗口": self.VCP_WINDOW,
            "窄幅窗口": self.TIGHT_WINDOW,
            "放量倍数": f"{self.VOL_SURGE:.1f}x",
        }

    def quick_prefilter(self, df: pd.DataFrame) -> bool:
        """快速预过滤"""
        if len(df) < self.LOOKBACK:
            return False
        close = df["收盘"].values
        high = df["最高"].values
        low = df["最低"].values
        # 近10天波动幅度不能太大
        recent_vol = (high[-self.TIGHT_WINDOW:].max() - low[-self.TIGHT_WINDOW:].min()) / close[-1]
        if recent_vol > 0.15:
            return False
        # 收盘在60日均价附近
        ma60_price = close[-60:].mean()
        if close[-1] < ma60_price * 0.85:
            return False
        return True

    @staticmethod
    def _check_params():
        """参数合理性校验"""
        assert ZuanQianVCPStrategy.VOL_SURGE >= 1.0, \
            "VOL_SURGE 不能小于1.0"
        assert ZuanQianVCPStrategy.TIGHT_WINDOW >= 3, \
            "TIGHT_WINDOW 不能小于3"

    def filter(self, df: pd.DataFrame) -> bool:
        self._check_params()
        if df.empty or len(df) < self.LOOKBACK:
            return False
        df = df.iloc[-self.LOOKBACK:].copy()
        latest = df.iloc[-1]
        high = df["最高"].values
        low = df["最低"].values
        op = df["开盘"].values
        cl = df["收盘"].values

        # 1. 波动率收缩（向量化）
        recent_range = (high[-self.TIGHT_WINDOW:].max() - low[-self.TIGHT_WINDOW:].min()) / low[-1]
        long_range = (high[-self.VCP_WINDOW:].max() - low[-self.VCP_WINDOW:].min()) / low[-1]
        if long_range == 0 or recent_range / long_range > 0.5:
            return False

        # 2. 窄幅整理（向量化）
        hl = high[-self.TIGHT_WINDOW:] - low[-self.TIGHT_WINDOW:]
        body = np.abs(cl[-self.TIGHT_WINDOW:] - op[-self.TIGHT_WINDOW:])
        body_ratios = np.divide(body, hl, out=np.ones_like(body), where=hl > 0)
        if body_ratios.mean() > 0.65:
            return False

        # 3. 放量突破
        if latest.get("VOL_RATIO", 0) < self.VOL_SURGE:
            return False
        body_size = abs(latest["收盘"] - latest["开盘"])
        if body_size / latest["收盘"] < self.BODY_MIN:
            return False

        # 4. 突破近期高点
        recent_high_10 = high[-self.TIGHT_WINDOW:-1].max()
        if latest["收盘"] <= recent_high_10:
            return False

        # 5. 均线过滤
        if latest["收盘"] < latest.get("MA20", 0):
            return False
        return True

    def debug_filter(self, df: pd.DataFrame) -> dict:
        steps = {}
        if df.empty or len(df) < self.LOOKBACK:
            return {"match": False, "steps": {"数据长度": {
                "passed": False, "reason": f"{len(df)}行 < {self.LOOKBACK}行",
            }}}
        df = df.iloc[-self.LOOKBACK:].copy()
        steps["数据长度"] = {"passed": True, "reason": f"{len(df)}行"}
        high = df["最高"].values
        low = df["最低"].values

        recent_range = (high[-self.TIGHT_WINDOW:].max() - low[-self.TIGHT_WINDOW:].min()) / low[-1]
        long_range = (high[-self.VCP_WINDOW:].max() - low[-self.VCP_WINDOW:].min()) / low[-1]
        contraction_ok = long_range > 0 and recent_range / long_range <= 0.5
        steps["波动率收缩"] = {
            "passed": contraction_ok,
            "reason": f"近{self.TIGHT_WINDOW}天波幅={recent_range:.2%}, "
                       f"近{self.VCP_WINDOW}天波幅={long_range:.2%}, "
                       f"比例={recent_range/long_range:.1%}" if long_range > 0 else "长周期波幅为0",
        }
        if not contraction_ok:
            return {"match": False, "steps": steps}
        latest = df.iloc[-1]
        steps["放量"] = {
            "passed": latest.get("VOL_RATIO", 0) >= self.VOL_SURGE,
            "reason": f"量比={latest.get('VOL_RATIO', 0):.2f} "
                       f"(阈值={self.VOL_SURGE}x)",
        }
        match = all(s["passed"] for s in steps.values())
        return {"match": match, "steps": steps}
