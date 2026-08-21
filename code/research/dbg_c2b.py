"""C2 对账差异深挖：单笔 px_ret vs hfq_ret vs 现金, 找系统性偏差"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl
from backtest import backtest_engine as be

# 直接 monkeypatch 打印每笔的账目构成
orig_append = None

# 方案: 改 backtest_engine 里的卖出段, 输出每笔 px_ret/hfq_ret/cash价差
# 简化: 用一个小样本回测, 但需要能看到 trades 内部——run_backtest 不返回 trades
# 先看 trade 记录的字段: pnl(复权) vs 现金口径差
m = be.run_backtest(extra_factors={"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)},
                    start_year=2021, end_year=2022, verbose=False, include_base=False,
                    return_by_year=False)
print("keys:", [k for k in m.keys()])

# 检查现金口径 vs 复权口径的核心: cost_per_share(不复权×滑点) vs buy_price(复权×滑点)
# 关键怀疑: hfq_ret = sell_price/buy_price - 1 (复权), 但 gross = shares × cost_per_share(不复权) × hfq_ret
# → 金额 = 不复权本金 × 复权收益率 → 除权时复权收益率放大/缩小, 与现金(不复权)不匹配
print("\n核心怀疑: gross = 不复权本金 × 复权收益率(含分红)")
print("→ 除权日: 复权价连续(自动调整), 不复权价跳变(除权缺口)")
print("→ 若持仓跨除权日: 复权收益率=含分红真实收益 ✓, 但不复权现金价差=除权缺口 ✗")
print("→ 但两者不该差 33%! 除非 buy_price/cost_per_share 混用")