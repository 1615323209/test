#!/usr/bin/env python3
"""v6 最终参数 + 样本内外验证
训练：2021-2023  验证：2024-2026
参数：ret5d>0.05, vol<0.5, 避4/7月
"""
import polars as pl
import pandas as pd
from pathlib import Path
from datetime import datetime
import gc

DATA = Path("D:/quant_data")
FACTOR = DATA / "factor_bt.parquet"
MARKET = DATA / "market_daily.parquet"
HS300 = DATA / "hs300.parquet"

INIT_CAPITAL = 20000
N_SLOTS = 10
POSITION = 2000
STOP_LOSS = -0.08
TP = 0.12
TIME_STOP_DAYS = 20
TIME_STOP_GAIN = 0.05
MIN_CASH = 2000
PROTECT_GAIN = 0.03
MAX_PER_DAY = 2
RET5D_MIN = 0.05
VOL_MAX = 0.5
LIMIT_UP_TH = 80
BAD_MONTHS = {4, 7}

COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005

market = pl.read_parquet(MARKET)
market_dict = {r['日期']: r for r in market.to_dicts()}
hs300 = pl.read_parquet(HS300).sort('日期')
hs300_dict = {r['日期']: (r['close'], r['ma_20']) for r in hs300.to_dicts()}
dates = sorted(market_dict.keys())

def hs300_above_ma20(d):
    v = hs300_dict.get(d)
    return bool(v and v[1] is not None and v[0] > v[1])

def market_ok(d):
    m = market_dict.get(d)
    if not m: return False
    conds = 0
    if hs300_above_ma20(d): conds += 1
    if m['涨停家数'] is not None and m['涨停家数'] > LIMIT_UP_TH: conds += 1
    north = m.get('北向净买入')
    if north is not None and north > 0: conds += 1
    return conds >= 2

def run_period(start_year, end_year, label):
    """跑一段时间区间"""
    d1 = datetime(start_year,1,1).date()
    d2 = datetime(end_year,12,31).date()
    chunk = pl.scan_parquet(FACTOR).filter(
        (pl.col('日期') >= d1) & (pl.col('日期') <= d2)
    ).collect().sort('日期')
    chunk_dates = sorted(chunk['日期'].unique().to_list())
    
    holdings = []
    cash = INIT_CAPITAL
    n_trades = 0; pnl_total = 0.0; fee_total = 0.0; wins = 0
    pnl_list = []
    
    for today in chunk_dates:
        today_data = chunk.filter(pl.col('日期') == today)
        for h in holdings[:]:
            row_data = today_data.filter(pl.col('股票代码') == h['code'])
            if len(row_data) == 0: continue
            row = row_data.row(0, named=True)
            close = row['收盘']; cost = h['buy_price']
            pnl = (close-cost)/cost
            held = (today - h['buy_date']).days
            peak = h.get('peak', cost); h['peak'] = max(peak, close)
            reason = None; sell_pct = 1.0
            if pnl <= STOP_LOSS: reason = '止损'
            elif pnl >= TP: reason = '止盈'
            elif h['peak'] >= cost*(1+PROTECT_GAIN) and pnl < 0.01: reason = '保本'
            elif row['ma_60'] is not None and close < row['ma_60']: reason = '破MA60'
            elif row['ma_20'] is not None and close < row['ma_20']:
                if not h.get('half_sold'): reason, sell_pct = '破MA20减半', 0.5
                else: reason = '破MA20清仓'
            elif held >= TIME_STOP_DAYS and pnl < TIME_STOP_GAIN: reason = '时间止损'
            if reason:
                sell_shares = int(h['shares']*sell_pct)
                if sell_shares < 100: sell_shares = h['shares']
                sell_amt = sell_shares*close
                fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
                profit = sell_shares*(close-cost) - fee
                n_trades += 1; pnl_total += profit; fee_total += fee
                pnl_list.append(profit)
                if profit > 0: wins += 1
                cash += sell_amt - fee
                h['shares'] -= sell_shares
                if sell_pct == 0.5: h['half_sold'] = True
                if h['shares'] < 100: holdings.remove(h)
        if today.month in BAD_MONTHS: continue
        if not market_ok(today): continue
        if len(holdings) < N_SLOTS and cash >= MIN_CASH:
            held_codes = {h['code'] for h in holdings}
            candidates = today_data.filter(~pl.col('股票代码').is_in(held_codes))
            cond = (pl.col('is_suspended')==0) & (pl.col('limit_up')==0) & (pl.col('limit_down')==0)
            cond &= (pl.col('limit_up_5d')>=1) & (pl.col('ret_1d')<0) & (pl.col('ret_1d')>-0.06)
            cond &= (pl.col('ret_5d')>RET5D_MIN) & (pl.col('vol_ratio')<VOL_MAX) & (pl.col('vol_ratio')>0.3)
            cond &= (pl.col('收盘')>pl.col('ma_20')) & (pl.col('收盘')<pl.col('ma_5'))
            cond &= (pl.col('price_pos_20')<0.85) & (pl.col('price_pos_20')>0.2)
            picks = candidates.filter(cond).sort('vol_ratio', descending=False)
            slots = min(N_SLOTS-len(holdings), MAX_PER_DAY)
            for row in picks.head(slots).iter_rows(named=True):
                code = row['股票代码']; buy_price = float(row['收盘'])
                shares = int(POSITION/buy_price/100)*100
                if shares < 100: continue
                cost = shares*buy_price; fee = max(COMM_MIN, cost*COMM_RATE)
                if cost+fee > cash: continue
                cash -= cost+fee
                holdings.append({'code':code,'buy_date':today,'buy_price':buy_price,
                                 'shares':shares,'peak':buy_price,'half_sold':False})
    # 区间末平仓
    for h in holdings:
        row = chunk.filter((pl.col('日期')==chunk_dates[-1]) & (pl.col('股票代码')==h['code']))
        if len(row) > 0:
            close = row['收盘'][0]
            sell_amt = h['shares']*close
            fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
            profit = h['shares']*(close-h['buy_price']) - fee
            n_trades += 1; pnl_total += profit; pnl_list.append(profit)
            if profit > 0: wins += 1
    ret = pnl_total/INIT_CAPITAL*100
    win_rate = wins/n_trades*100 if n_trades else 0
    cum = pd.Series(pnl_list).cumsum()
    dd = (cum-cum.cummax()).min() if len(cum) else 0
    del chunk; gc.collect()
    print(f"  {label}: 收益{ret:+.2f}% 笔数{n_trades} 胜率{win_rate:.1f}% 成本{fee_total:.0f}元 回撤{dd/INIT_CAPITAL*100:.1f}%")
    return ret, n_trades

print("=== v6 样本内外验证 ===")
print("训练期 2021-2023:")
run_period(2021, 2023, "2021-2023")
print("验证期 2024-2026:")
run_period(2024, 2026, "2024-2026")
print("\n全周期 2021-2026:")
run_period(2021, 2026, "2021-2026")
