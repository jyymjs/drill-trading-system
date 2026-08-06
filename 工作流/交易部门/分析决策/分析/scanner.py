"""扫描器 - 对股票池执行策略筛选"""
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm
from 分析决策.分析.indicators import all_indicators
from 工具链.工具.logger import logger
from 数据基础.数据.fetcher import get_daily_kline
from 数据基础.配置.settings import (
    KLINE_YEARS,
    SCAN_MAX_WORKERS,
    SCAN_PROGRESS,
    SCAN_RETRY,
)
from 数据基础.配置.stock_pool import get_all_stocks, get_etf_list, is_st_name
from 策略.核心策略.base import BaseStrategy

# ── C23 收紧条件（2026-08-06 老板拍板替换进策略）──
# 单一来源：项目/回测系统/tighten_compare.py（T-024 复算口径，tighten_compare.DEFAULT_MOM /
# RISK_MIN / RISK_MAX）；此处同值定义（scanner 位于 分析决策 包，不依赖 项目/ 路径），
# 改动需两处同步。c23_capital_compare.py / sim_capital.py 直接引用 tighten_compare 常量。
C23_MOM_MAX = 0.10    # 动量上限：触发价 vs 20 交易日前收盘涨幅 ≤ 10%
C23_RISK_MIN = 0.5    # 止损距离下限（元）：trigger - stop ≥ 0.5（太近易被扫）
C23_RISK_MAX = 3.0    # 止损距离上限（元）：trigger - stop ≤ 3.0（太远盈亏比差）

# G8 像素感池级预筛（2026-08-06）：知识卡「像素感直接pass掉，交投清淡不是真实意愿」
# （2024-07-16 扫盘）——像素感严重（px < 阈值，评级内 = C 级硬降级）的品种在
# 指标计算/评级前直接排除（池级预筛）。px 只用基础K线列（开/高/低/收）numpy
# 向量化（~微秒级），预筛成本可忽略（像素感本身是评级内条件之一，预筛不改变
# 评级口径，只是提前排除必然被评 C 的票）。
# 阈值与策略评级内 PX_B 同值（策略/核心策略/samples/zuanqian_strategy.py PX_B=0.35，
# 与 C23 常量同款约定：改动需两处同步）。
G8_PX_THRESHOLD = 0.35


def scan_single_stock(
    stock: dict,
    strategy: BaseStrategy,
    years: int = KLINE_YEARS,
    mode: str = "normal",
) -> dict | None:
    """对单只股票执行策略筛选

    Args:
        mode: "normal"=完整6条件, "prebreak"=预突破5条件（不含DN）

    Returns:
        {"code":, "name":, "match": bool, "price":, ...} 或 None
    """
    code = stock["code"]
    name = stock["name"]

    for attempt in range(SCAN_RETRY):
        try:
            df = get_daily_kline(code, use_cache=True)
            if df.empty or len(df) < 60:
                return None

            # 板块上下文（G1 分板块涨跌停线 2026-08-06）：df.attrs 随切片/指标计算
            # 传播（pandas attrs 语义），gap_limit_detect 据此判定 20cm 票用 19.5% 线。
            df.attrs["code"] = code

            # 快速预过滤：在计算所有指标前快速排除（仅用基础K线列）
            if not strategy.quick_prefilter(df):
                return None

            # G8 像素感池级预筛（2026-08-06）：px < 阈值 = 像素感严重（交投清淡，
            # 老师"像素感直接pass掉"）→ 在指标计算/评级前直接排除（评级内同样判
            # C，预筛只提前排除，不改口径）。px 仅用基础K线列，成本 ~微秒级。
            from 分析决策.分析.indicators import pixelation_score
            if pixelation_score(df) < G8_PX_THRESHOLD:
                return None

            # 按需计算指标：策略声明的 required_indicators → 只算需要的列
            needed = strategy.required_indicators
            df = all_indicators(df, needed_cols=needed)

            # 执行策略
            if mode == "prebreak" and hasattr(strategy, 'prebreak_grade'):
                result = strategy.prebreak_grade(df)
                match = result.get("match", False)
                grade = result.get("grade", "C")
            elif hasattr(strategy, 'grade'):
                result = strategy.grade(df)
                match = result.get("match", False)
                grade = result.get("grade", "C")
            else:
                match = strategy.filter(df)
                grade = "?"
                result = {}

            if match:
                latest = df.iloc[-1]
                entry = {
                    "code": code,
                    "name": name,
                    "price": latest.get("收盘", 0),
                    "涨幅%": latest.get("涨跌幅", 0),
                    "换手率%": latest.get("换手率", 0),
                    "成交量": latest.get("成交量", 0),
                    "MA5": latest.get("MA5", 0),
                    "MA20": latest.get("MA20", 0),
                    "RSI": round(latest.get("RSI", 0), 1),
                    "评级": grade,
                    "策略": strategy.name,
                }

                # G3 0.5R 环境仓位标注（补完计划 · 2026-08-06）：
                # 知识库 经验型模式/知识卡.md「环境好（非右下角）→ 正常 1R；
                # 环境不好（右下角）→ 0.5R」（2024-06-22/29）——个股 60 日窗口
                # 右下角特征判定（indicators.environment_quality，与 B1 大盘闸门
                # 维度不同：B1 管大盘"做不做"，G3 管当日市场环境"做多少"）。
                # 标注供候选表可见（个股维度信息）；实际仓位缩放由 sim_open 执行
                # 当日头寸统一档（2024-06-01「同一市场环境头寸统一」：当日市场
                # 环境档 = 上证指数 60 日窗口判定，见 sim_trading._market_env_scale）。
                try:
                    from 分析决策.分析.indicators import environment_quality
                    env = environment_quality(df)
                    entry["环境"] = env["quality"]
                    entry["风险档"] = "0.5R" if env["quality"] in ("weak", "bad") else "1R"
                except (KeyError, ValueError, TypeError):
                    entry["环境"] = "good"
                    entry["风险档"] = "1R"

                # 预突破模式：附加条件单关键参数
                if mode == "prebreak":
                    entry["触发价"] = result.get("trigger_price", 0)
                    entry["止损价"] = result.get("stop_loss", 0)
                    entry["每股风险"] = result.get("risk_per_share", 0)
                    entry["TY高"] = result.get("ty_high", 0)
                    entry["TY低"] = result.get("ty_low", 0)

                    # 2026-08-06 实战发现：已突破（现价≥触发价）的股票也进了
                    # prebreak 候选——挂条件单会立即成交=追高，失去预突破意义。
                    # 老板拍板：标注"突破状态"供下游拆分（候选主表只留未突破，
                    # 已突破行保留供研究，不参与挂单候选）。
                    trigger = result.get("trigger_price", 0)
                    entry["突破状态"] = (
                        "已突破"
                        if trigger > 0 and latest.get("收盘", 0) >= trigger
                        else "未突破"
                    )

                    # 2026-08-06 T-020：P2 放量条件（dn_confirm 回测 X=1.5 甜点）接入
                    # 扫描候选——每只候选输出"放量阈值"（绝对成交量，单位同数据源=手），
                    # 口径对齐 dn_confirm 回测"触发日前 20 日均量"：扫描时最新日视为潜在
                    # 突破日 → 取不含最新日的前 20 根均量 × 1.5，每日用最新数据刷新。
                    # 突破日成交量 > 放量阈值 即达标起步线（量比>1.5；T-025 修正：量比越高越好，
                    # 1.5 仅作下限，不放上限）。
                    ref_vol = df["成交量"].iloc[max(0, len(df) - 21):len(df) - 1]
                    ref_mean = float(ref_vol.mean()) if len(ref_vol) > 0 else 0.0
                    entry["放量阈值"] = round(ref_mean * 1.5, 0) if ref_mean > 0 else 0

                    # 2026-08-06 C23 替换进策略（老板拍板：S 级 + dn_confirm 1.5 +
                    # 动量≤10% + 止损距离 0.5~3 元；现方案封存见 策略/核心策略/策略版本存档.md）。
                    # 动量口径对齐 tighten_compare.recompute：trigger / 触发日前第 20 根收盘 - 1；
                    # 扫描时最新日视为潜在突破日（与放量阈值同法）→ 前第 20 根 = iloc[-21]。
                    mom20 = None
                    if len(df) >= 22 and trigger > 0:
                        close20 = float(df["收盘"].iloc[-21])
                        if close20 > 0:
                            mom20 = trigger / close20 - 1.0
                    entry["动量20日%"] = round(mom20 * 100, 1) if mom20 is not None else None

                    # C23 判定：止损距离 = 每股风险（prebreak_grade 中 = trigger - stop）
                    c23_reasons = []
                    if mom20 is not None and mom20 > C23_MOM_MAX:
                        c23_reasons.append(f"动量{mom20:.1%}>10%")
                    risk_dist = result.get("risk_per_share", 0) or 0
                    if risk_dist > 0 and risk_dist < C23_RISK_MIN:
                        c23_reasons.append(f"止损{risk_dist:.2f}元<0.5")
                    if risk_dist > C23_RISK_MAX:
                        c23_reasons.append(f"止损{risk_dist:.2f}元>3")
                    entry["C23"] = "达标" if not c23_reasons else "不达标"
                    entry["C23原因"] = "；".join(c23_reasons) if c23_reasons else ""

                return entry
            return None

        except Exception as e:
            if attempt < SCAN_RETRY - 1:
                time.sleep(0.5)
                continue
            logger.debug("扫描 %s(%s) 失败: %s", name, code, e, exc_info=True)
            return None


def scan(
    strategy: BaseStrategy,
    max_workers: int = SCAN_MAX_WORKERS,
    show_progress: bool = SCAN_PROGRESS,
    progress_callback: Callable | None = None,
    security_type: str = "stock",
    mode: str = "normal",
) -> list[dict]:
    """全市场扫描

    Args:
        strategy: 策略实例
        max_workers: 并发线程数
        show_progress: 是否显示 tqdm 进度条
        progress_callback: Streamlit 进度回调 fn(current, total, stock_name)
        security_type: "stock"=仅股票, "etf"=仅ETF, "all"=全部
        mode: "normal"=标准评级, "prebreak"=预突破模式

    Returns:
        符合条件的标的列表 [{code, name, price, ...}, ...]
    """
    mode_label = {"normal": "标准6条件", "prebreak": "预突破5条件"}.get(mode, mode)
    logger.info("开始扫描 | 策略: %s | 模式: %s | 类型: %s | 并发: %d",
                strategy.name, mode_label, security_type, max_workers)
    logger.info("策略说明: %s", strategy.description)

    # 获取股票/ETF池
    if security_type == "etf":
        stocks = get_etf_list()
    elif security_type == "all":
        all_stocks = get_all_stocks()
        all_etfs = get_etf_list()
        stocks = all_stocks + [{"code": e["code"], "name": e["name"]} for e in all_etfs]
    else:
        stocks = get_all_stocks()
    logger.info("股票池数量: %d 只", len(stocks))

    # 2026-08-06 实战发现：ST 股混入扫描候选（600079 当日出现在 S 级），
    # 老板拍板接入品种筛选一票否决——ST/*ST 可能跳空跳过止损
    # （知识库：品种筛选/知识卡.md）。在池级过滤（省去无效拉取），
    # 判定以名称为准（数据源无独立 ST 标记字段，见 stock_pool.is_st_name）。
    st_removed = [s for s in stocks if is_st_name(s.get("name", ""))]
    if st_removed:
        stocks = [s for s in stocks if not is_st_name(s.get("name", ""))]
        logger.info("ST/*ST 一票否决: 剔除 %d 只（例: %s）",
                    len(st_removed), "、".join(f"{s['name']}({s['code']})" for s in st_removed[:5]))

    results = []
    total = len(stocks)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(scan_single_stock, s, strategy, KLINE_YEARS, mode): s
            for s in stocks
        }

        iterator = as_completed(futures)
        if show_progress and not progress_callback:
            iterator = tqdm(iterator, total=total, desc="扫描中", ncols=80)

        for i, future in enumerate(iterator):
            try:
                result = future.result(timeout=30)  # 30秒超时，防止单只股票永久挂起
                if result:
                    results.append(result)
            except Exception:
                pass

            if progress_callback:
                progress_callback(i + 1, total, "")

    # 排序：预突破模式按评级优先，标准模式按涨幅
    if mode == "prebreak":
        grade_order = {"S": 0, "A": 1, "B": 2}
        results.sort(key=lambda x: grade_order.get(x.get("评级", "C"), 3))
    else:
        results.sort(key=lambda x: x.get("涨幅%", 0), reverse=True)

    logger.info("扫描完成 | 符合条件: %d 只", len(results))
    return results


def split_prebreak_results(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """拆分预突破结果为（未突破候选, 已突破研究）

    2026-08-06 实战发现 + 老板拍板：prebreak 扫描应只输出"未突破"
    （现价 < 触发价）的候选；已突破（现价 ≥ 触发价）的挂单会立即成交
    = 追高，失去预突破意义，不参与挂单候选，但保留标注供研究。

    Args:
        results: scan() 的 prebreak 模式输出（含"突破状态"标记）

    Returns:
        (candidates, broken): 未突破候选主表 / 已突破研究列表
    """
    candidates = [r for r in results if r.get("突破状态") != "已突破"]
    broken = [r for r in results if r.get("突破状态") == "已突破"]
    return candidates, broken


def apply_vol_filter(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """放量过滤（2026-08-07 T-020 接线 · 老板拍板）

    只保留放量候选（最新日成交量 ≥ 前20日均量×1.5 = 量比>1.5）——与回测
    dn_confirm=1.5 口径对齐（回测：突破日量比 ≤1.5 视为未触发，不进场）。
    不达标行返回 rejected 供研究（不参与挂单候选，_vol 后缀保存）。
    放量阈值 0（数据不足）→ 放行侧（不因数据问题误杀挂单候选）。
    """
    passing, rejected = [], []
    for r in results:
        vol = r.get("成交量") or 0
        thr = r.get("放量阈值") or 0
        if thr > 0 and vol < thr:
            rejected.append(r)
        else:
            passing.append(r)
    return passing, rejected


def apply_c23_filter(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """C23 过滤（2026-08-06 老板拍板替换进策略）

    保留"达标"（动量≤10% 且 止损距离 0.5~3 元）为挂单候选主表；
    "不达标"返回 filtered 供研究（不参与挂单候选）。判定字段由
    scan_single_stock prebreak 分支写入（C23 / C23原因 / 动量20日%）。

    常量防漂移（2026-08-07 实盘开盘审计）：本地值与 tighten_compare 单一来源
    运行时校验一致（可用时），不一致即报错——防"两处同步"漏改。

    Args:
        results: prebreak 扫描候选（含 C23 标记）

    Returns:
        (passing, filtered): C23 达标候选主表 / 不达标研究列表
    """
    try:
        from 回测系统.tighten_compare import DEFAULT_MOM, RISK_MAX, RISK_MIN
        if (C23_MOM_MAX, C23_RISK_MIN, C23_RISK_MAX) != \
                (DEFAULT_MOM, RISK_MIN, RISK_MAX):
            raise RuntimeError(
                "C23 常量与 tighten_compare 单一来源不一致——请同步（scanner 本地值 "
                f"{(C23_MOM_MAX, C23_RISK_MIN, C23_RISK_MAX)} vs 回测 "
                f"{(DEFAULT_MOM, RISK_MIN, RISK_MAX)}）")
    except ImportError:
        pass  # 无 项目/ 路径（独立调用）→ 跳过校验，本地值兜底

    passing = [r for r in results if r.get("C23") == "达标"]
    filtered = [r for r in results if r.get("C23") != "达标"]
    return passing, filtered
