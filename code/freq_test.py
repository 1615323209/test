#!/usr/bin/env python3
"""v7 提频测试 + Sharpe + 沪深300对比
测试 TOP_N = 3 / 5 / 8
"""
import polars as pl
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import gc

DATA = Path("/home/ubuntu/quant_data")
FACTOR = DATA / "factor_daily.parquet"
MARKET = DATA / "market_daily.parquet"
HS300 = DATA / "hs300.parquet"
OUT = DATA / "v7_freq_test.csv"

INIT_CAPITAL = 20000
N_SLOTS = 10
POSITION = 2000
STOP_LOSS = -0.08
TP = 0.12
TIME_STOP_DAYS = 20
TIME_STOP_GAIN = 0.05
MIN_CASH = 2000
PROTECT_GAIN = 0.03
LIMIT_UP_TH = 60
COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005

def slippage(ret_1d):
    vol_factor = min(abs(ret_1d) / 0.05, 1.0) if ret_1d is not None else 0.5
    return 0.001 + 0.002 * vol_factor

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

def compute_score(df):
    d = df.with_columns([
        (-pl.col('ret_5d') * pl.col('turn_ma5')).rank().over('日期').alias('s1'),
        (-pl.col('ma5_dist') * pl.col('turn_ma5')).rank().over('日期').alias('s2'),
        (-pl.col('vol_10d') - pl.col('vol_change_5d')).rank().over('日期').alias('s3'),
        pl.col('limit_up_5d').rank().over('日期').alias('s4'),
        (-pl.col('turn_ratio')).rank().over('日期').alias('s5'),
        pl.col('macd_dif').rank().over('日期').alias('s6'),
    ])
    return d.with_columns(
        (pl.col('s1')*0.25 + pl.col('s2')*0.20 + pl.col('s3')*0.15 +
         pl.col('s4')*0.15 + pl.col('s5')*0.15 + pl.col('s6')*0.10).alias('score')
    )

# 预加载全量（2021-2026）
print("加载数据...")
chunk = pl.scan_parquet(FACTOR).filter(pl.col('日期') >= datetime(2021,1,1).date())
chunk = chunk.select(['日期','股票代码','收盘','ret_1d','ret_5d',
              'limit_up','limit_down','is_suspended',
              'turn_ratio','turn_ma5','vol_ratio','vol_change_5d','vol_10d',
              'ma_5','ma_20','ma_60','ma5_dist','macd_dif','price_pos_20'])
chunk = chunk.with_columns(
    pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
).collect()
chunk_dates = sorted(chunk['日期'].unique().to_list())
print(f"  加载完成 {len(chunk):,}行, {len(chunk_dates)}天")

def run_backtest(top_n):
    holdings = []
    cash = INIT_CAPITAL
    pnl_list = []
    n_trades = 0; wins = 0
    date_pnl = {}
    
    # 一次性按日期分组（避免逐日全表filter）
    date_groups = {d: g for d, g in zip(chunk_dates, chunk.partition_by('日期', maintain_order=True))}
    
    for today in chunk_dates:
        day_pnl = 0.0
        today_data = date_groups[today]
        # 出场
        for h in holdings[:]:
            held = (today - h['buy_date']).days
            if held < 1: continue
            row_data = today_data.filter(pl.col('股票代码') == h['code'])
            if len(row_data) == 0: continue
            row = row_data.row(0, named=True)
            close = row['收盘']; ret1d = row['ret_1d']
            if ret1d is not None and ret1d <= -0.095: continue
            cost = h['buy_price']
            pnl = (close-cost)/cost
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
                slip = slippage(ret1d)
                sell_price = close*(1-slip)
                sell_shares = int(h['shares']*sell_pct)
                if sell_shares < 100: sell_shares = h['shares']
                sell_amt = sell_shares*sell_price
                fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
                profit = sell_shares*(sell_price-cost) - fee
                pnl_list.append(profit); n_trades += 1
                if profit > 0: wins += 1
                day_pnl += profit
                cash += sell_amt - fee
                h['shares'] -= sell_shares
                if sell_pct == 0.5: h['half_sold'] = True
                if h['shares'] < 100: holdings.remove(h)
        # 持仓市值变化（每日盯市）
        for h in holdings:
            row_data = today_data.filter(pl.col('股票代码') == h['code'])
            if len(row_data) > 0:
                close = row_data['收盘'][0]
                day_pnl += h['shares'] * (close - h.get('last_close', h['buy_price']))
                h['last_close'] = close
        date_pnl[today] = day_pnl
        
        if not market_ok(today): continue
        if len(holdings) < N_SLOTS and cash >= MIN_CASH:
            held_codes = {h['code'] for h in holdings}
            candidates = today_data.filter(~pl.col('股票代码').is_in(held_codes))
            candidates = candidates.filter(
                (pl.col('is_suspended')==0)&(pl.col('limit_up')==0)&(pl.col('limit_down')==0)
                &(pl.col('price_pos_20')<0.85)&(pl.col('price_pos_20')>0.1)
                &(pl.col('收盘')>pl.col('ma_20')))
            if len(candidates) > 0:
                scored = compute_score(candidates)
                top = scored.sort('score', descending=True).head(top_n)
                for row in top.iter_rows(named=True):
                    code = row['股票代码']
                    slip = slippage(row['ret_1d'])
                    buy_price = float(row['收盘'])*(1+slip)
                    shares = int(POSITION/buy_price/100)*100
                    if shares < 100: continue
                    cost = shares*buy_price
                    fee = max(COMM_MIN, cost*COMM_RATE)
                    if cost+fee > cash: continue
                    cash -= cost+fee
                    holdings.append({'code':code,'buy_date':today,'buy_price':buy_price,
                                     'shares':shares,'peak':buy_price,'half_sold':False,
                                     'last_close':buy_price})
    # 末段平仓
    last_df = date_groups[chunk_dates[-1]]
    for h in holdings:
        row = last_df.filter(pl.col('股票代码') == h['code'])
        if len(row) > 0:
            close = row['收盘'][0]
            ret1d = row['ret_1d'][0]
            slip = slippage(ret1d)
            sell_price = close*(1-slip)
            sell_amt = h['shares']*sell_price
            fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
            profit = h['shares']*(sell_price-h['buy_price']) - fee
            pnl_list.append(profit); n_trades += 1
            if profit > 0: wins += 1
    
    del date_groups; gc.collect()
    
    total_pnl = sum(pnl_list)
    ret = total_pnl/INIT_CAPITAL*100
    win_rate = wins/n_trades*100 if n_trades else 0
    # 每日序列 Sharpe（年化，252天）
    series = pd.Series([v for k,v in sorted(date_pnl.items())])
    if len(series) > 30 and series.std() > 0:
        sharpe = series.mean() / series.std() * np.sqrt(252)
    else:
        sharpe = 0
    # 回撤
    cum = pd.Series(pnl_list).cumsum()
    dd = (cum-cum.cummax()).min() if len(cum) else 0
    del_date_pnl = None
    return {'top_n': top_n, '收益%': round(ret,2), '笔数': n_trades,
            '胜率%': round(win_rate,1), 'Sharpe': round(float(sharpe),2),
            '回撤%': round(dd/INIT_CAPITAL*100,1)}

# 沪深300同期收益
hs = pl.read_parquet(HS300).filter(pl.col('日期') >= datetime(2021,1,1).date())
hs_ret = (hs['close'].last() / hs['close'].first() - 1) * 100
print(f"\n沪深300 同期收益: {hs_ret:.1f}%")

print("\n=== TOP_N 提频测试 ===")
results = []
for n in [3, 5, 8]:
    r = run_backtest(n)
    results.append(r)
    print(f"  TOP_N={n}: 收益{r['收益%']:+.2f}% 笔数{r['笔数']} 胜率{r['胜率%']}% Sharpe={r['Sharpe']} 回撤{r['回撤%']}%")

df = pd.DataFrame(results)
df.to_csv(OUT, index=False)
print(f"\n结果: {OUT}")
