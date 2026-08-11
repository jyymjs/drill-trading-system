# 回测系统（R-005 项目）· 实验脚本模板与加速规范（R-061，2026-08-12）

> 本 README 是**未来一切实验脚本的接入规范**——新实验默认走加速模式，
> 避免 r57 时代的重复读盘（~60,000 次 read_kline 全实验 30 分钟）。

## 一、加速公共件（replay_cache.py）——未来实验必用

| 组件 | 用途 | 用法 |
|---|---|---|
| `KlineCache` | K 线内存缓存（628 个唯一代码只读一次） | `kc = KlineCache(); df = kc.get(code)` |
| `ReplayResultCache` | 重放结果缓存 `(开关指纹, 窗) → {code_date: result}`，key 防错配 | `rc.set(key, rep_map); rc.window(key, min_date)` |
| `parallel_replay` | 多进程分片并行（Windows spawn 安全） | `parallel_replay(codes, worker_fn)` |

**新实验脚本模板**（重放类实验标准结构）：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目/ 加入路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 交易部门根

from 回测系统.replay_cache import KlineCache, ReplayResultCache, parallel_replay
from 回测系统.r57_exit_matrix import replay_all, GROUPS  # 或自建 switches

# 1) 声明开关组合（key 用 frozenset 指纹自动防错配）
SWITCHES = dict(enable_breakeven=True, enable_trailing=True,
                enable_active=True, enable_ttp=True)
# 2) 重放（缓存命中自动秒回；未命中并行全量重放一次）
results = replay_all(SWITCHES, hold=None, min_date=None)   # hold=None = 无限期
# 3) 窗口裁剪（min_date 只过滤输出行集，零重放成本）
seven_y = replay_all(SWITCHES, hold=20, min_date="2019-01-01")
```

## 二、铁律（审计标准，2026-08-12 沉淀）

1. **门禁先行**：正式实验前 `r57_exit_matrix.py --validate` 零超差（重放 vs 引擎
   逐笔对账 + 资金层对账）——禁止带病分析
2. **缓存指纹含内容**：改实验逻辑（出场开关/口径）后先删/验旧缓存再跑——
   易犯错误 #14（enrich 缓存指纹已含关键列 SHA256，勿回退）
3. **信号层与资金层交叉对账**：信号层数字变而资金层不变 → 先查缓存再查代码
   （R-060 事故教训）
4. **蒙卡 seed 标注**：seed 流切换（stdlib↔numpy）在报告中注明（分布估计不变，
   具体数字微调）
5. **计时留痕**：实验报告记录 wall-clock 实测（R-061 后 r57 全实验 ~2-4 分钟，
   重构前 30 分钟）

## 三、脚本速查

| 脚本 | 用途 | 耗时（R-061 后） |
|---|---|---|
| `r57_exit_matrix.py` | 正式对照主流程（门禁 + 54 格 + 18 格 + 蒙卡） | ~2-4 分钟（原 30 分钟）|
| `r57_exit_matrix.py --validate` | 门禁（重放 vs 引擎对账） | ~2-3 分钟 |
| `r60_inf_replay.py` | 无限期重放写回（B/E/F） | 并行（原串行全量）|
| `r58_inf_capital.py` | 无限期资金层复算对账 | enriched 一次构建全组复用 |
| `r59_v4_mc.py` | V4 蒙卡（信号层自助 + 资金层） | 秒级（numpy）|

## 四、当前策略参数（V4，2026-08-12 定版）

- 信号：prebreak S 级 / dn_confirm 1.5 / C23（动量≤10% + 止损距离 0.5~3）
- 建仓：0.5R 分步 + delay2 确认 + R-053 突破质量
- 资金：风险额 0.025×资金 / 无限制 / 不定额注入
- 出场：四规则全开（E 组合：1R 平保/移动获利/TTP36%/主动出场）+ 无限期持有
- 详细：`策略/核心策略/策略规格书.md` + `策略/知识库/系统/实验索引-20260811-12-V4定版.md`
