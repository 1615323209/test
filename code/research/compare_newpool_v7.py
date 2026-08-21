#!/usr/bin/env python3
"""新因子池(反转6+近5日涨停) vs v7 基线 —— 5日/4仓5000/实盘口径对比回测
"""
import os
import sys
from pathlib import Path
PROJ = Path(os.environ.get("QUANT_PROJECT", r"D:/quant_project"))
sys.path.insert(0, str(PROJ / "code"))
from backtest.backtest_engine import run_backtest

# v4.1复核 P0-1: 修正因子定义(纯新池, include_base=False)
# n3 改负号(低换手, 与注释一致); 删除与v7重复的 n5(-turn_ratio); n7 待P0-2定向后定
NEW_FACTORS = [
    ("n1", "(-pl.col('ma5_dist'))", 0.20),        # 偏离5日均线(反向)
    ("n2", "(-pl.col('up_streak'))", 0.20),       # 连涨天数(反向)
    ("n3", "(-pl.col('turn_ma5'))", 0.15),        # 低换手(修正: 原为正号选高换手, 与注释矛盾)
    ("n4", "(-pl.col('price_pos_20'))", 0.25),    # 20日位置(低位)
    ("n6", "(-pl.col('ma20_dist'))", 0.20),       # 20日线偏离(低位)
]
NEW = {n: (e, w) for n, e, w in NEW_FACTORS}

print("=== 新因子池(反转5) vs v7基线 ===\n")
m_new = run_backtest(extra_factors=NEW, start_year=2021, end_year=2026, verbose=False, include_base=False)
print(f"[纯新池 include_base=False] 总收益: {m_new['total_ret_pct']:+.1f}% | 年化: {m_new['annual_pct']:+.1f}% | "
      f"交易: {m_new['n_trades']}笔 | 胜率: {m_new['win_rate']:.1f}% | 盈亏比: {m_new['pl_ratio']:.2f} | 回撤: {m_new['max_dd_pct']:.1f}%")

m_v7 = run_backtest(extra_factors=None, start_year=2021, end_year=2026, verbose=False)
print(f"[v7基线]   总收益: {m_v7['total_ret_pct']:+.1f}% | 年化: {m_v7['annual_pct']:+.1f}% | "
      f"交易: {m_v7['n_trades']}笔 | 胜率: {m_v7['win_rate']:.1f}% | 盈亏比: {m_v7['pl_ratio']:.2f} | 回撤: {m_v7['max_dd_pct']:.1f}%")
