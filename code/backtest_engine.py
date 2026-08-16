#!/usr/bin/env python3
"""回测引擎（backtest_v7 本地化 + 可注入新因子）

从 backtest_v7.py 改造：
1. 数据路径改 Windows 本地 D:\\quant_data
2. 支持注入 LLM 挖掘的新因子（extra_factors: {name: polars_expr, weight: float}）
3. 返回指标 dict 供外层 loop 决策

用法:
    from backtest_engine import run_backtest
    metrics = run_backtest(extra_factors={"f1": pl.col('ret_5d')*pl.col('turn_ma5'), "f2": ...})
"""
import polars as pl
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import gc

DATA = Path(r"D:\quant_data")
FACTOR = DATA / "factor_daily.parquet"
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
TOP_N = 3
LIMIT_UP_TH = 60

COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005

# v7 基线六因子权重
BASE_FACTORS = [
    ("s1", "(-pl.col('ret_5d') * pl.col('turn_ma5'))", 0.25),   # 低换手+回调
    ("s2", "(-pl.col('ma5_dist') * pl.col('turn_ma5'))", 0.20), # 偏离均线
    ("s3", "(-pl.col('vol_10d') - pl.col('vol_change_5d'))", 0.15), # 低波动+缩量
    ("s4", "pl.col('limit_up_5d')", 0.15),                      # 涨停惯性
    ("s5", "(-pl.col('turn_ratio'))", 0.15),                    # 低换手
    ("s6", "pl.col('macd_dif')", 0.10),                         # MACD
]

def slippage(ret_1d):
    vol_factor = min(abs(ret_1d) / 0.05, 1.0) if ret_1d is not None else 0.5
    return 0.001 + 0.002 * vol_factor

def run_backtest(extra_factors=None, start_year=2021, end_year=2026, verbose=True):
    """
    extra_factors: dict {name: (polars_expr_str, weight)}
        polars_expr_str 需是 Expr 代码字符串，如 "(pl.col('ret_5d')*pl.col('turn_ma5')).rank().over('日期')"
        weight: 在总 score 中的权重（与 v7 六因子同量纲，rank 0-1 后加权）
    返回 metrics dict:
        {total_ret_pct, annual_pct, trades, win_rate, pl_ratio, max_dd_pct, fee_total}
    """
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

    need_cols = ['日期','股票代码','收盘','成交量','ret_1d','ret_5d',
                 'limit_up','limit_down','is_suspended',
                 'turn_ratio','turn_ma5','turn_ma20','vol_ratio','vol_ratio_20',
                 'vol_change_5d','vol_10d','vol_20d',
                 'ma_5','ma_20','ma_60','ma5_dist','ma20_dist',
                 'macd_dif','macd_dea','price_pos_20','up_streak']
    chunk = pl.scan_parquet(FACTOR).select(need_cols).collect(streaming=True)
    chunk = chunk.with_columns(
        pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
    )
    chunk = chunk.filter(pl.col('日期') >= datetime(start_year,1,1).date())
    chunk_dates = sorted(chunk['日期'].unique().to_list())

    def compute_score(df):
        exprs = []
        for name, expr_str, w in BASE_FACTORS:
            exprs.append((eval(expr_str, {"pl": pl}), w))
        if extra_factors:
            for fname, (fexpr_str, fw) in extra_factors.items():
                try:
                    e = eval(fexpr_str, {"pl": pl})
                    exprs.append((e, fw))
                except Exception:
                    if verbose: print(f"  [注入因子失败] {fname}: {fexpr_str}")
        d = df.with_columns([e.rank().over('日期').alias(f"_f{i}") for i, (e, w) in enumerate(exprs)])
        score_expr = sum((pl.col(f"_f{i}") * w for i, (e, w) in enumerate(exprs)), pl.lit(0.0))
        return d.with_columns(score_expr.alias("score"))

    all_trades = []
    holdings = []
    cash = INIT_CAPITAL

    for year_start in range(start_year, end_year + 1, 2):
        year_end = min(year_start + 1, end_year)
        d1 = datetime(year_start,1,1).date()
        d2 = datetime(year_end,12,31).date()
        if verbose: print(f"回测 {year_start}-{year_end}...")
        sub = chunk.filter((pl.col('日期') >= d1) & (pl.col('日期') <= d2))
        sub_dates = sorted(sub['日期'].unique().to_list())
        for today in sub_dates:
            today_data = sub.filter(pl.col('日期') == today)
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
                    all_trades.append({
                        'code': h['code'], 'buy_date': h['buy_date'],
                        'buy_price': cost, 'sell_date': today,
                        'sell_price': sell_price, 'shares': sell_shares,
                        'pnl': profit, 'pnl_pct': pnl*100, 'fee': fee,
                        'reason': reason, 'held_days': held
                    })
                    cash += sell_amt - fee
                    h['shares'] -= sell_shares
                    if sell_pct == 0.5: h['half_sold'] = True
                    if h['shares'] < 100: holdings.remove(h)
            if not market_ok(today): continue
            if len(holdings) < N_SLOTS and cash >= MIN_CASH:
                held_codes = {h['code'] for h in holdings}
                candidates = today_data.filter(~pl.col('股票代码').is_in(held_codes))
                candidates = candidates.filter(
                    (pl.col('is_suspended')==0) & (pl.col('limit_up')==0) & (pl.col('limit_down')==0)
                    & (pl.col('price_pos_20')<0.85) & (pl.col('price_pos_20')>0.1)
                    & (pl.col('收盘')>pl.col('ma_20'))
                )
                if len(candidates) > 0:
                    scored = compute_score(candidates)
                    top = scored.sort('score', descending=True).head(TOP_N)
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
                                         'shares':shares,'peak':buy_price,'half_sold':False})
        del sub; gc.collect()
        if verbose: print(f"  [诊断] cash={cash:.0f}, 持仓={len(holdings)}, 交易={len(all_trades)}")

    if holdings:
        last = dates[-1]
        last_chunk = chunk.filter(pl.col('日期') == last)
        for h in holdings:
            row_data = last_chunk.filter(pl.col('股票代码') == h['code'])
            if len(row_data) > 0:
                row = row_data.row(0, named=True)
                close, ret1d = row['收盘'], row['ret_1d']
                slip = slippage(ret1d)
                sell_price = close*(1-slip)
                sell_amt = h['shares']*sell_price
                fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
                profit = h['shares']*(sell_price-h['buy_price']) - fee
                all_trades.append({
                    'code': h['code'], 'buy_date': h['buy_date'],
                    'buy_price': h['buy_price'], 'sell_date': last,
                    'sell_price': sell_price, 'shares': h['shares'],
                    'pnl': profit, 'pnl_pct': (sell_price-h['buy_price'])/h['buy_price']*100,
                    'fee': fee, 'reason': '强制平仓',
                    'held_days': (last-h['buy_date']).days
                })
                cash += sell_amt - fee

    trades = pd.DataFrame(all_trades)
    if len(trades) == 0:
        return {"total_ret_pct": 0, "annual_pct": 0, "trades": 0, "win_rate": 0,
                "pl_ratio": 0, "max_dd_pct": 0, "fee_total": 0, "n_trades": 0}
    total_pnl = trades['pnl'].sum()
    total_fee = trades['fee'].sum()
    ret = total_pnl/INIT_CAPITAL*100
    wins = trades[trades['pnl']>0]; loses = trades[trades['pnl']<=0]
    win_rate = len(wins)/len(trades)*100
    avg_win = wins['pnl'].mean() if len(wins) else 0
    avg_loss = loses['pnl'].mean() if len(loses) else 0
    pl_ratio = abs(avg_win/avg_loss) if avg_loss != 0 else float('inf')
    ts = trades.sort_values('sell_date')
    cum = ts['pnl'].cumsum(); peak = cum.cummax()
    dd = (cum-peak).min(); dd_pct = dd/INIT_CAPITAL*100
    years = (dates[-1]-dates[0]).days/365
    annual = ((INIT_CAPITAL+total_pnl)/INIT_CAPITAL)**(1/years)-1 if years > 0 else 0
    return {"total_ret_pct": round(ret, 2), "annual_pct": round(annual*100, 2),
            "n_trades": len(trades), "win_rate": round(win_rate, 1),
            "pl_ratio": round(pl_ratio, 2) if pl_ratio != float('inf') else 99,
            "max_dd_pct": round(dd_pct, 2), "fee_total": round(total_fee, 0)}

if __name__ == "__main__":
    # 基线回测（v7 原版，无注入）
    m = run_backtest()
    print("=== v7 基线 ===")
    for k, v in m.items():
        print(f"  {k}: {v}")
