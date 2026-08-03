"""适配层：回测与交易部现有代码之间的关键缝

重构完成时 base.py 提升为交易部统一层，backtest 改 import 统一层；
engine/stats/report 因只依赖接口而零改动。
"""
