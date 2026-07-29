# Claude Code 项目指令

## 语言规则
- 所有回复均使用中文。

## 量化策略规则
- **所有量化条件参数（阈值、周期、比例等）的调整必须经用户书面同意后执行。**
- 策略参数包括但不限于：DL_MIN_BARS、DL_MAX_RANGE_PCT、TY_MIN_BARS、TY_MAX_BODY_RATIO、TY_MAX_RANGE_PCT、DN_MIN_VOL_RATIO、DN_MIN_BODY_PCT、VCP_MIN_CONTRACTION、LOOKBACK、VCP_WINDOW、TIGHT_WINDOW、VOL_SURGE、BODY_MIN 等。
- 未经明确授权，不得修改 `strategy/samples/zuanqian_strategy.py` 中的任何数值参数。
- 如需建议参数调整，先给出具体修改内容和理由，等待用户确认后再执行。
