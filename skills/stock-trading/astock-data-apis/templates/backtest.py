#!/usr/bin/env python3
"""短线策略回测引擎 — 基于因子库
规则：2持、1万/只、止损5%、止盈8%/15%、破MA10减仓、5日时间止损
选股：MA5>MA10, MACD金叉, 量比>1.5, 非高位（price_pos_20<0.8）

使用前确保：
  1. 因子库已构建: factor_daily.parquet
  2. pip install polars pyarrow pandas numpy
"""
import polars as pl
import numpy as np
from pathlib import Path
from datetime import datetime
import gc

FACTOR = Path("factor_daily.parquet")
OUTPUT = Path("backtest_result.parquet")
REPORT = Path("backtest_report.txt")

# ===== 可调参数 =====
INIT_CAPITAL = 20000
MAX_HOLD = 2
POSITION_SIZE = 10000
STOP_LOSS = -0.05
TAKE_PROFIT_1 = 0.08
TAKE_PROFIT_2 = 0.15
TIME_STOP = 5           # 持仓N日
MIN_UP = 0.05            # 时间止损阈值
YEAR_BATCH = 3           # 内存分批

# ===== 选股开关 =====
SELECT = {
    'ma5_above_ma10': True,
    'macd_golden': True,
    'vol_ratio_gt_1.5': True,
    'price_pos_below_0.8': True,
    'limit_up_exclude': True,
    'not_suspended': True,
    'ret_1d_gt_neg5': True,
}

# ===== 加载数据 =====
print("加载因子数据...")
df = pl.scan_parquet(FACTOR)
codes = df.select('股票代码').unique().collect()['股票代码'].to_list()
dates = sorted(df.select('日期').unique().collect()['日期'].to_list())
dates = [d for d in dates if d >= datetime(2011,1,1).date()]
print(f"{len(codes)}只, {len(dates)}天 ({dates[0]}~{dates[-1]})")

# ===== 回测主循环 =====
all_trades = []
holdings = []
cash = INIT_CAPITAL

for year_start in range(2011, 2027, YEAR_BATCH):
    year_end = min(year_start + YEAR_BATCH - 1, 2026)
    d1, d2 = datetime(year_start,1,1).date(), datetime(year_end,12,31).date()
    print(f"\n回测 {year_start}-{year_end}...")
    
    chunk = df.filter((pl.col('日期')>=d1)&(pl.col('日期')<=d2)).collect()
    
    for today in sorted(chunk['日期'].unique().to_list()):
        today_data = chunk.filter(pl.col('日期')==today)
        
        # 1. 检查持仓出场
        for h in holdings[:]:
            row = today_data.filter(pl.col('股票代码')==h['code'])
            if len(row)==0: continue
            r = row.row(0, named=True)
            close = r['收盘']; cost = h['buy_price']
            pnl = (close-cost)/cost
            held_days = (today-h['buy_date']).days
            
            sell_reason, sell_pct = None, 0
            if pnl <= STOP_LOSS:        sell_reason, sell_pct = '止损', 1.0
            elif pnl >= TAKE_PROFIT_2:   sell_reason, sell_pct = '止盈15%', 1.0
            elif pnl >= TAKE_PROFIT_1 and not h.get('half_sold'):
                sell_reason, sell_pct = '止盈8%', 0.5
            elif held_days >= TIME_STOP and pnl < MIN_UP:
                sell_reason, sell_pct = '时间止损', 1.0
            elif r['ma_10'] and close < r['ma_10']:
                sell_reason, sell_pct = '破MA10', 0.5 if not h.get('half_sold') else 1.0
            
            if sell_reason and sell_pct > 0:
                ss = int(h['shares']*sell_pct)
                if ss > 0:
                    cash += ss*close
                    all_trades.append({'code':h['code'],'buy_date':h['buy_date'],
                        'buy_price':cost,'sell_date':today,'sell_price':close,
                        'shares':ss,'pnl':ss*(close-cost),'pnl_pct':pnl*100,
                        'reason':sell_reason,'held_days':held_days})
                h['shares'] -= ss
                if sell_pct==0.5: h['half_sold'] = True
                if h['shares']<=0: holdings.remove(h)
        
        # 2. 选股进场
        if len(holdings) < MAX_HOLD and cash >= POSITION_SIZE:
            held = {h['code'] for h in holdings}
            cand = today_data.filter(~pl.col('股票代码').is_in(held))
            cond = pl.lit(True)
            if SELECT['not_suspended']: cond &= pl.col('is_suspended')==0
            if SELECT['limit_up_exclude']: cond &= pl.col('limit_up')==0
            if SELECT['ret_1d_gt_neg5']: cond &= pl.col('ret_1d')>-0.05
            if SELECT['ma5_above_ma10']: cond &= pl.col('ma_5')>pl.col('ma_10')
            if SELECT['macd_golden']: cond &= pl.col('macd_dif')>pl.col('macd_dea')
            if SELECT['vol_ratio_gt_1.5']: cond &= pl.col('vol_ratio')>1.5
            if SELECT['price_pos_below_0.8']: cond &= pl.col('price_pos_20')<0.8
            
            picks = cand.filter(cond).sort('vol_ratio', descending=True)
            slots = MAX_HOLD - len(holdings)
            for r in picks.head(slots).iter_rows(named=True):
                bp = float(r['收盘']); shares = int(POSITION_SIZE/bp/100)*100
                if shares<100: continue
                cost = shares*bp
                if cost>cash: continue
                cash -= cost
                holdings.append({'code':r['股票代码'],'buy_date':today,
                    'buy_price':bp,'shares':shares,'half_sold':False})
    
    del chunk; gc.collect()

# 强制平仓
if holdings:
    last = df.filter(pl.col('日期')==dates[-1]).collect()
    for h in holdings:
        r = last.filter(pl.col('股票代码')==h['code'])
        if len(r)>0:
            c = r['收盘'][0]; profit = h['shares']*(c-h['buy_price'])
            all_trades.append({'code':h['code'],'buy_date':h['buy_date'],
                'buy_price':h['buy_price'],'sell_date':dates[-1],
                'sell_price':c,'shares':h['shares'],'pnl':profit,
                'pnl_pct':(c/h['buy_price']-1)*100,
                'reason':'强制平仓','held_days':(dates[-1]-h['buy_date']).days})
            cash += h['shares']*c

# ===== 报告 =====
import pandas as pd
trades = pd.DataFrame(all_trades)
if len(trades)>0:
    trades.to_parquet(OUTPUT)
    tp = trades['pnl'].sum()
    tr = tp/INIT_CAPITAL*100
    wins = trades[trades['pnl']>0]
    losses = trades[trades['pnl']<=0]
    wr = len(wins)/len(trades)*100
    aw = wins['pnl'].mean() if len(wins)>0 else 0
    al = losses['pnl'].mean() if len(losses)>0 else 0
    pr = abs(aw/al) if al!=0 else float('inf')
    cs = trades.sort_values('sell_date')['pnl'].cumsum()
    dd = (cs-cs.cummax()).min()
    
    rpt = f"""
=== 短线策略回测报告 ===
时间: {dates[0]} ~ {dates[-1]}
初始: {INIT_CAPITAL:,}元 → 最终: {INIT_CAPITAL+tp:,.0f}元
总收益: {tp:+,.0f}元 ({tr:+.1f}%)
交易: {len(trades)}笔  胜率: {wr:.1f}%
均盈: {aw:+,.0f}元  均亏: {al:+,.0f}元  盈亏比: {pr:.2f}
最大回撤: {dd:,.0f}元 ({dd/INIT_CAPITAL*100:.1f}%)
出场: {trades['reason'].value_counts().to_dict()}
"""
    print(rpt); Path(REPORT).write_text(rpt)
else:
    print("无符合条件的交易")
