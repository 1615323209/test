"""C2 对账差异诊断：book_pnl(复权损益) vs cash_pnl(现金账) 差为什么这么大"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl
from datetime import datetime
from pathlib import Path

DATA = Path("D:/quant_data")
FACTOR = DATA / "factor_daily.parquet"

# 复现回测的账目, 加诊断
# 简化: 直接用回测结果分析——差异来源假设:
# 1. 强制平仓的 fee 里 buy_fee_alloc 未参与(持仓可能never卖)
# 2. cash+=sell_amt(不复权) vs pnl(复权) —— 分红差异
# 3. 半仓卖出后 shares0 未调整 → buy_fee_alloc 摊错

# 直接跑一个小回测, 打每笔的字段看分布
from backtest.backtest_engine import run_backtest
import pandas as pd

# 用单因子 quick 测 (2021-2022 缩短)
m = run_backtest(extra_factors={"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)},
                 start_year=2021, end_year=2022, verbose=False, include_base=False)
print(f"short-run: ret={m['total_ret_pct']}% cash={m.get('cash_pnl')} book={m.get('book_pnl')} divgap={m.get('div_gap_pct')}%")