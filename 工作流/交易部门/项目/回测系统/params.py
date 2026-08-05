"""回测参数：BacktestParams dataclass + 校验 + 参数快照

参数默认值遵循已批准计划《回测独立项目》（R-005）中"待老板确认的方法学口径"。
所有口径默认值集中于此文件/risk_model，调整只需改一处。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, date

# 合法取值
VALID_MODES = ("normal", "prebreak", "both")
VALID_GRADES = ("S", "A", "B")

# 网格锚定：首个信号日索引固定为 249，保证首窗 ≥250 根
# （DL 120 根结构 + 过高点重置 + 60 根底线，保守取整；网格与 --start/--end 无关）
GRID_ANCHOR = 249


def _parse_yyyymmdd(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


@dataclass
class BacktestParams:
    """回测运行参数（CLI 参数一一对应）"""

    # 信号日期范围（YYYYMMDD，None=缓存边界；只过滤记录，不改网格）
    start: str | None = None
    end: str | None = None
    # 策略注册名（唯一主策略：zuanqian_strategy）
    strategy: str = "zuanqian_strategy"
    # normal=6条件已突破 / prebreak=5条件预突破 / both=同窗双评级对比
    mode: str = "normal"
    # 信号日步长（交易日）
    interval: int = 5
    # 观察窗多值，各自独立统计
    holds: list[int] = field(default_factory=lambda: [5, 10, 20])
    # 记录哪些等级信号
    grades: list[str] = field(default_factory=lambda: ["S", "A", "B"])
    # 覆盖策略 DL 候选根数（S,A,B；None=用策略默认 90,70,60；T-4.2 敏感性测试用）
    dl_cands: str | None = None
    # 只跑指定代码（冒烟/验收用）；None=股票池全量
    codes: list[str] | None = None
    # 并发线程数
    max_workers: int = 5
    # 交易成本模型（佣金万1.3+印花税万5，2026-08-04 老板确认费率）
    enable_cost: bool = True
    # 成本倍率（D2 2倍成本压力测试=2.0，2026-08-05 方案 D 类；1.0=基线）
    cost_multiplier: float = 1.0
    # C5 移动止损（2026-08-05 老板拍板，价格行为学04课借鉴）：持仓中每确认新结构低点
    # （买入后新高之后的回调低点）→ 止损上移到 低点×0.99；日线收盘判定；默认关=现有出场行为。
    # 先回测对照验证后上线（开/关对照实验见 c5_trail_compare.py）。
    moving_stop: bool = False
    # 覆盖默认输出目录
    output_dir: str | None = None
    # run 后自动验收自检的抽样笔数（0=不自动自检）
    verify_samples: int = 0
    # 严格逐窗重算指标（对照验证慢路径，默认关）
    recompute_each_window: bool = False
    # 运行标识（时间戳），用于目录隔离；不参与 diff 对比
    run_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))

    def validate(self) -> None:
        """参数合法性校验，非法直接抛 ValueError"""
        for name in ("start", "end"):
            v = getattr(self, name)
            if v is not None:
                try:
                    _parse_yyyymmdd(v)
                except ValueError:
                    raise ValueError(f"--{name} 必须是 YYYYMMDD 格式，收到: {v!r}")
        if self.start and self.end and _parse_yyyymmdd(self.start) > _parse_yyyymmdd(self.end):
            raise ValueError(f"--start({self.start}) 不能晚于 --end({self.end})")
        if self.mode not in VALID_MODES:
            raise ValueError(f"--mode 必须是 {'/'.join(VALID_MODES)}，收到: {self.mode!r}")
        if not isinstance(self.interval, int) or self.interval < 1:
            raise ValueError(f"--interval 必须是 ≥1 的整数，收到: {self.interval!r}")
        if not isinstance(self.holds, (list, tuple)) or not self.holds:
            raise ValueError("--hold 必须至少给一个正整数观察窗")
        holds = []
        for h in self.holds:
            if not isinstance(h, int) or h < 1:
                raise ValueError(f"--hold 必须是正整数，收到: {h!r}")
            if h not in holds:
                holds.append(h)
        self.holds = sorted(holds)
        if not self.grades:
            raise ValueError("--grade 必须至少给一个评级")
        for g in self.grades:
            if g not in VALID_GRADES:
                raise ValueError(f"--grade 只能是 {'/'.join(VALID_GRADES)}，收到: {g!r}")
        self.grades = sorted(set(self.grades))
        if not isinstance(self.max_workers, int) or self.max_workers < 1:
            raise ValueError(f"--max-workers 必须是 ≥1 的整数，收到: {self.max_workers!r}")
        if not isinstance(self.cost_multiplier, (int, float)) or self.cost_multiplier < 1.0:
            raise ValueError(f"--cost-multiplier 必须是 ≥1 的数值，收到: {self.cost_multiplier!r}")
        if self.dl_cands is not None:
            try:
                cands = [int(x) for x in self.dl_cands.split(",")]
            except ValueError:
                raise ValueError(f"--dl-cands 需为 S,A,B 三个整数，收到: {self.dl_cands!r}")
            if len(cands) != 3:
                raise ValueError(f"--dl-cands 需为 S,A,B 三个整数，收到: {self.dl_cands!r}")
            if not (cands[0] > cands[1] > cands[2]):
                raise ValueError(f"--dl-cands 需严格降序（S>A>B），收到: {self.dl_cands!r}")

    # ── 参数快照 ──

    def snapshot(self, strategy_info: dict | None = None) -> dict:
        """参数快照（params.json 内容）：纯 JSON 可序列化，无运行时对象"""
        data = asdict(self)
        data["holds"] = self.holds
        data["grades"] = self.grades
        data["codes"] = self.codes
        data["grid_anchor"] = GRID_ANCHOR
        data["methodology"] = {
            "normal_stop": "止损价 = 进场价 - max(2×ATR14, 2%×进场价)",
            "entry_time": "信号日T收盘（prebreak=触发价，首根最高≥trigger才进场）",
            "prebreak_untracked": "未触发计信号数/触发率，不参与胜率/平均R/回撤",
            "exit_simplified": "v1 仅 止损 + hold到期收盘 两种出场",
            "moving_stop": "C5 2026-08-05 老板拍板：持仓中每确认新结构低点（买入后新高后回调低点，日线收盘判定）→ 止损上移 低点×0.99；默认关（对照实验用）",
        }
        if strategy_info:
            data["strategy_info"] = strategy_info
        return data

    def save_snapshot(self, path: str, strategy_info: dict | None = None) -> None:
        """写 params.json（UTF-8，indent=2）"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(strategy_info), f, ensure_ascii=False, indent=2, default=str)
