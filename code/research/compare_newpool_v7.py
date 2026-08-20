#!/usr/bin/env python3
"""新因子池(反转6+近5日涨停) vs v7 基线 —— 5日/4仓5000/实盘口径对比回测
"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
from backtest.backtest_engine import run_backtest

# 新因子池: 基于 backtest need_cols 可用列(illiq_20/rsi_14/price_pos_60 不在factor_daily)
NEW_FACTORS = [
    ("n1", "(-pl.col('ma5_dist'))", 0.20),        # 偏离5日均线(反向)
    ("n2", "(-pl.col('up_streak'))", 0.15),       # 连涨天数(反向)
    ("n3", "pl.col('turn_ma5')", 0.10),           # 低换手替代非流动性
    ("n4", "(-pl.col('price_pos_20'))", 0.20),    # 20日位置(低位)
    ("n5", "(-pl.col('turn_ratio'))", 0.10),      # 低换手
    ("n6", "(-pl.col('ma20_dist'))", 0.15),       # 20日线偏离(低位)
    ("n7", "(-pl.col('limit_up_5d'))", 0.10),     # 近5日涨停(反向)
]
NEW = {n: (e, w) for n, e, w in NEW_FACTORS}

print("=== 新因子池(反转7) vs v7基线 ===\n")
m_new = run_backtest(extra_factors=NEW, start_year=2021, end_year=2026, verbose=False)
print(f"[新因子池] 总收益: {m_new['total_ret_pct']:+.1f}% | 年化: {m_new['annual_pct']:+.1f}% | "
      f"交易: {m_new['n_trades']}笔 | 胜率: {m_new['win_rate']:.1f}% | 盈亏比: {m_new['pl_ratio']:.2f} | 回撤: {m_new['max_dd_pct']:.1f}%")

m_v7 = run_backtest(extra_factors=None, start_year=2021, end_year=2026, verbose=False)
print(f"[v7基线]   总收益: {m_v7['total_ret_pct']:+.1f}% | 年化: {m_v7['annual_pct']:+.1f}% | "
      f"交易: {m_v7['n_trades']}笔 | 胜率: {m_v7['win_rate']:.1f}% | 盈亏比: {m_v7['pl_ratio']:.2f} | 回撤: {m_v7['max_dd_pct']:.1f}%")
