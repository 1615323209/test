"""C2 完整跑对账差异定位：分年/分区间对比 cash vs book"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
from backtest.backtest_engine import run_backtest

# 分段跑, 找哪个区间对账差
for (y0, y1) in [(2021, 2022), (2022, 2023), (2023, 2024), (2024, 2025), (2025, 2026), (2021, 2024), (2021, 2026)]:
    m = run_backtest(extra_factors={"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)},
                     start_year=y0, end_year=y1, verbose=False, include_base=False)
    print(f"{y0}-{y1}: ret={m['total_ret_pct']:+.1f}% cash={m.get('cash_pnl',0):.0f} book={m.get('book_pnl',0):.0f} divgap={m.get('div_gap_pct',0):+.1f}%")
