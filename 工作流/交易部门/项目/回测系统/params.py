"""回测参数：BacktestParams dataclass + 校验 + 参数快照

参数默认值遵循已批准计划《回测独立项目》（R-005）中"待老板确认的方法学口径"。
所有口径默认值集中于此文件/risk_model，调整只需改一处。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

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
    # 并发进程数（2026-08-06 线程→进程升级；None=自动探测 os.cpu_count() 逻辑核，
    # 6 核 12 线程机器默认吃满 12 线程，实测超线程收益 ~1.2-1.3x）
    max_workers: int | None = None
    # 交易成本模型（佣金万1.3+印花税万5，2026-08-04 老板确认费率）
    enable_cost: bool = True
    # 成本倍率（D2 2倍成本压力测试=2.0，2026-08-05 方案 D 类；1.0=基线）
    cost_multiplier: float = 1.0
    # C5 移动止损（2026-08-05 老板拍板，价格行为学04课借鉴）：持仓中每确认新结构低点
    # （买入后新高之后的回调低点）→ 止损上移到 低点×0.99；日线收盘判定；默认关=现有出场行为。
    # 先回测对照验证后上线（开/关对照实验见 c5_trail_compare.py）。
    moving_stop: bool = False
    # B1 环境闸门 + C3 量能过滤 + C4 情绪闸门（2026-08-05 老板拍板执行优化方案第3波）：
    #   环境闸门：信号日主闸门指数（默认上证）当日跌幅跌破阈值（建议 -2%）→ 环境不利，
    #     模式 veto=一票否决 / downgrade=降一档（对照可选项）；
    #   量能过滤：信号日近 vol_window 日均成交额 < min_amount 万元 → 不进场（"无量直接不碰"）；
    #   情绪闸门：信号日全市场下跌家数占比 > sent_threshold（建议 70%）→ 环境否决
    #     （普跌日盲区实证：2026-05-29 全市场 71.4% 股票跌但上证仅 -0.73%，21 笔信号全亏
    #     -20.3R——指数闸门管不了"家数普跌"；C4 2026-08-05 老板拍板，与指数闸门并列任一触发即否决）；
    #   实现于执行层（gate.py），不改 grade() 评级核心（评级与执行分离）；
    #   默认开=正式接入（2026-08-05 回测对照 b1c3_compare/c4_sentiment_compare.py），
    #   关闭用 --no-env-gate / --no-volume-filter / --no-sentiment-gate（对照实验用）。
    env_gate: bool = True
    env_drop_pct: float = -2.0      # 指数当日跌幅阈值（%，建议值）
    env_mode: str = "veto"          # veto=否决 / downgrade=降一档
    env_index: str = "上证指数"     # 主闸门指数
    volume_filter: bool = True      # C3 量能硬过滤开关
    min_amount: float = 5000.0      # 日均成交额阈值（万元，建议值）
    vol_window: int = 5             # 均额窗口（交易日，含信号日）
    # C1 财报日避让第一层：预约披露日不新开仓（2026-08-05 老板拍板执行优化方案 C1 项）：
    #   信号日当天是该股预约披露日（first_appoint == 信号日）→ 否决该信号（不新开仓）；
    #   持仓期内跨过披露日 → 输出警示（记录到报告，不强制平仓——第一层设计）；
    #   实现于执行层（prbook_gate.py），不改 grade() 评级核心（评级与执行分离）；
    #   默认开=正式接入（对照实验用 --no-prbook-gate，见 c1_prbook_compare.py）。
    prbook_gate: bool = True
    sentiment_gate: bool = True     # C4 情绪闸门开关（涨跌家数维度，与指数闸门并列）
    sent_threshold: float = 70.0    # 全市场下跌家数占比阈值（%，建议值）
    missing_sentiment: str = "pass" # 涨跌家数缺失：pass=放行并计数 / veto=否决
    # 突破日量能确认（2026-08-06 老板拍板实验参数 · 默认 0.0=关，不改变现有行为）：
    #   prebreak 触发（突破价成交）后，检查突破日（触发日）量比 = 触发日成交量 ÷ 触发日前 20 日均量
    #   （口径对齐 DN 相对量比：启动K均量/前面调整段日均量，DN 用前 20 日均量；不含触发日）
    #   > dn_confirm 才计入交易；量能不达标 → 视为未触发（不进场，不参与统计）。
    #   背景：prebreak 5条件评级不含 DN（动能）——突破发生在评级后，无法提前评动能；
    #   老板质疑"力度小的突破（无量/磨上去）假突破概率高"→ 回测验证（dn_confirm_compare.py），
    #   有效则建议接入扫描/挂单指引（阈值取哪个），无效报告原因。
    #   对照组 = 0.0（纯价格触发，现状行为）。
    dn_confirm: float = 0.0
    # C23 收紧（T-027 2026-08-06 老板拍板"回测 = 现行策略 V2"）：信号层过滤
    #   动量≤10%（信号日触发价/收盘 vs 20 交易日前收盘，无前视版）且 止损距离 0.5~3 元。
    #   阈值单一来源：回测系统/tighten_compare.py（DEFAULT_MOM / RISK_MIN / RISK_MAX）。
    #   默认关 = 显式开（--c23）：V1 基线回测仍可跑，V2 用 --c23；
    #   与 sim_capital.py --c23 语义一致（信号层过滤，不改评级与跟踪核心）。
    c23: bool = False
    # G3 分步建仓（2026-08-06 · 2024-06-29 周会原文定案）：0.5R = 分步建仓第一步
    # （非终局减半）——进场 0.5R → 下一根收线确认（收下去/动能接受，判定见
    # indicators.half_position_confirm）→ 确认补至 1R 继续跟踪 / 不确认 0.5R
    # 马上平仓（"优势不突出，动能无法接受"）。默认关 = 现有行为（回测对照用
    # --phase-in；模拟层 sim_trading 恒开启分步——模拟 = 实盘执行口径）。
    phase_in: bool = False
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
        if self.max_workers is None:
            # 自动探测：默认吃满全部逻辑核（T-016 优化，2026-08-06）
            self.max_workers = os.cpu_count() or 6
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
        # B1 环境闸门参数校验（2026-08-05 第3波）
        if self.env_drop_pct >= 0:
            raise ValueError(f"--env-drop-pct 必须是负值（跌幅阈值），收到: {self.env_drop_pct!r}")
        if self.env_mode not in ("veto", "downgrade"):
            raise ValueError(f"--env-mode 只能是 veto/downgrade，收到: {self.env_mode!r}")
        if not isinstance(self.min_amount, (int, float)) or self.min_amount <= 0:
            raise ValueError(f"--min-amount 必须 > 0（万元），收到: {self.min_amount!r}")
        if not isinstance(self.vol_window, int) or self.vol_window < 1:
            raise ValueError(f"--vol-window 必须是 ≥1 的整数，收到: {self.vol_window!r}")
        # 突破日量能确认（2026-08-06 实验参数）：0.0=关；>0 为量比阈值（触发日/前20日均量）
        if not isinstance(self.dn_confirm, (int, float)) or self.dn_confirm < 0:
            raise ValueError(f"--dn-confirm 必须 ≥ 0（0=关），收到: {self.dn_confirm!r}")
        # C4 情绪闸门参数校验（2026-08-05 第3波）
        if not (0 < self.sent_threshold <= 100):
            raise ValueError(f"--sent-threshold 必须是 (0,100] 的占比百分比，收到: {self.sent_threshold!r}")
        if self.missing_sentiment not in ("pass", "veto"):
            raise ValueError(f"--missing-sentiment 只能是 pass/veto，收到: {self.missing_sentiment!r}")

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
            "prbook_gate": "C1 2026-08-05 老板拍板（优化方案 C1 定案第3条·第一层）：信号日=该股预约披露日 → 不新开仓；持仓期跨披露日 → 警示不强制平仓；默认开",
        }
        if strategy_info:
            data["strategy_info"] = strategy_info
        return data

    def save_snapshot(self, path: str, strategy_info: dict | None = None) -> None:
        """写 params.json（UTF-8，indent=2）"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(strategy_info), f, ensure_ascii=False, indent=2, default=str)
