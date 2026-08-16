#!/usr/bin/env python3
"""v7 横截面多因子打分策略
每日全市场打分 → 选 Top N
因子：挖掘出的反转组合（负IC取反）+ limit_up 惯性
"""
import polars as pl
import pandas as pd
from pathlib import Path
from datetime import datetime
import gc

DATA = Path("/home/ubuntu/quant_data")
FACTOR = DATA / "factor_daily.parquet"  # 需要全量因子（ma5_dist, turn_ma5 等）
MARKET = DATA / "market_daily.parquet"
HS300 = DATA / "hs300.parquet"
OUTPUT = DATA / "backtest_v7_trades.parquet"
REPORT = DATA / "backtest_v7_report.txt"

INIT_CAPITAL = 20000
N_SLOTS = 10
POSITION = 2000
STOP_LOSS = -0.08
TP = 0.12
TIME_STOP_DAYS = 20
TIME_STOP_GAIN = 0.05
MIN_CASH = 2000
PROTECT_GAIN = 0.03
TOP_N = 3             # 每日最多买入 3 只
LIMIT_UP_TH = 60      # 市场过滤放宽（打分策略每日可做）

COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005

def slippage(ret_1d):
    vol_factor = min(abs(ret_1d) / 0.05, 1.0) if ret_1d is not None else 0.5
    return 0.001 + 0.002 * vol_factor

print("=== v7 横截面打分策略 ===")

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

# 预加载全量因子（只需要打分用的列）
print("加载因子数据...")
need_cols = ['日期','股票代码','收盘','成交量','ret_1d','ret_5d',
             'limit_up','limit_down','is_suspended',
             'turn_ratio','turn_ma5','turn_ma20','vol_ratio','vol_ratio_20',
             'vol_change_5d','vol_10d','vol_20d',
             'ma_5','ma_20','ma_60','ma5_dist','ma20_dist',
             'macd_dif','macd_dea','price_pos_20','up_streak']
chunk = pl.scan_parquet(FACTOR).select(need_cols).collect(streaming=True)
# 计算近5日涨停次数
chunk = chunk.with_columns(
    pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
)
chunk = chunk.filter(pl.col('日期') >= datetime(2021,1,1).date())
chunk_dates = sorted(chunk['日期'].unique().to_list())
print(f"  加载完成 {len(chunk):,}行, {len(chunk_dates)}天")

def compute_score(df):
    """横截面打分：每列因子转秩分(0-1)后加权"""
    # 反转因子（负IC取反，值越小得分越高 → 用 -rank）
    d = df.with_columns([
        # 因子1: ret_5d×turn_ma5 取反（低换手+回调=高分）
        (-pl.col('ret_5d') * pl.col('turn_ma5')).rank().over('日期').alias('s1'),
        # 因子2: ma5_dist×turn_ma5 取反
        (-pl.col('ma5_dist') * pl.col('turn_ma5')).rank().over('日期').alias('s2'),
        # 因子3: vol_10d+vol_change_5d 取反（低波动+缩量=高分）
        (-pl.col('vol_10d') - pl.col('vol_change_5d')).rank().over('日期').alias('s3'),
        # 因子4: 涨停惯性（正IC）
        pl.col('limit_up_5d').rank().over('日期').alias('s4'),
        # 因子5: 换手低
        (-pl.col('turn_ratio')).rank().over('日期').alias('s5'),
        # 因子6: MACD多头（正贡献）
        pl.col('macd_dif').rank().over('日期').alias('s6'),
    ])
    # 加权总分（0-600）
    d = d.with_columns(
        (pl.col('s1')*0.25 + pl.col('s2')*0.20 + pl.col('s3')*0.15 +
         pl.col('s4')*0.15 + pl.col('s5')*0.15 + pl.col('s6')*0.10).alias('score')
    )
    return d

all_trades = []
holdings = []
cash = INIT_CAPITAL

for year_start in range(2021, 2027, 2):
    year_end = min(year_start + 1, 2026)
    d1 = datetime(year_start,1,1).date()
    d2 = datetime(year_end,12,31).date()
    print(f"回测 {year_start}-{year_end}...")
    
    sub = chunk.filter((pl.col('日期') >= d1) & (pl.col('日期') <= d2))
    sub_dates = sorted(sub['日期'].unique().to_list())
    
    for today in sub_dates:
        today_data = sub.filter(pl.col('日期') == today)
        
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
        
        # 市场过滤
        if not market_ok(today): continue
        
        # 打分选股
        if len(holdings) < N_SLOTS and cash >= MIN_CASH:
            held_codes = {h['code'] for h in holdings}
            candidates = today_data.filter(~pl.col('股票代码').is_in(held_codes))
            # 基础过滤：可交易
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
    print(f"  [诊断] cash={cash:.0f}, 持仓={len(holdings)}, 交易={len(all_trades)}")

# 强制平仓
if holdings:
    last = dates[-1]
    last_chunk = chunk.filter(pl.col('日期') == last)
    for h in holdings:
        row = last_chunk.filter(pl.col('股票代码') == h['code'])
        if len(row) > 0:
            close = row['收盘'][0]
            ret1d = row['ret_1d'][0]
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
if len(trades) > 0:
    trades.to_parquet(OUTPUT)
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
    annual = ((INIT_CAPITAL+total_pnl)/INIT_CAPITAL)**(1/years)-1
    
    report = f"""
=== v7 横截面打分回测 (2021-2026) ===
初始资金: {INIT_CAPITAL:,}元
最终资金: {INIT_CAPITAL+total_pnl:,.0f}元
总收益: {total_pnl:+,.0f}元 ({ret:+.1f}%)
年化: {annual*100:+.1f}%
交易成本: {total_fee:,.0f}元
交易次数: {len(trades)}
胜率: {win_rate:.1f}%
盈亏比: {pl_ratio:.2f}
最大回撤: {dd:,.0f}元 ({dd_pct:.1f}%)

出场原因:
{trades['reason'].value_counts().to_string()}
"""
else:
    report = "无交易"
print(report)
REPORT.write_text(report)
