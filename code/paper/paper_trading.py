#!/usr/bin/env python3
"""模拟盘引擎 — 持仓跟踪 + 自动交易 + 盈亏统计
用法: python paper_trading.py [日期]  (默认最新交易日)
规则: 10仓×2000元, 止损-8%, 止盈+12%, 保本(曾盈3%回落+1%),
      破MA20减半, 破MA60清, 时间止损20天(未涨5%), T+1
"""
import polars as pl
import pandas as pd
import json
import sys
from pathlib import Path
from datetime import datetime, date

DATA = Path("D:/quant_data")
FACTOR = DATA / "factor_daily.parquet"
FACTOR_INCR = DATA / "factor_daily_incr.parquet"
MARKET = DATA / "market_daily.parquet"
HS300 = DATA / "hs300.parquet"
STATE_FILE = DATA / "paper_positions.json"
CASH_FILE = DATA / "paper_cash.txt"
TRADES_FILE = DATA / "paper_trades.csv"

def factor_files():
    files = [FACTOR]
    if FACTOR_INCR.exists():
        files.append(FACTOR_INCR)
    return files

INIT_CAPITAL = 20000
N_SLOTS = 10
POSITION = 2000
STOP_LOSS = -0.08
TP = 0.12
TIME_STOP_DAYS = 20
TIME_STOP_GAIN = 0.05
PROTECT_GAIN = 0.03
TOP_N = 3
COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005

def slippage(ret_1d):
    vol_factor = min(abs(ret_1d) / 0.05, 1.0) if ret_1d is not None else 0.5
    return 0.001 + 0.002 * vol_factor

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return []

def save_state(positions):
    STATE_FILE.write_text(json.dumps(positions, ensure_ascii=False, indent=2))

def load_cash():
    if CASH_FILE.exists():
        return float(CASH_FILE.read_text().strip())
    return INIT_CAPITAL

def save_cash(cash):
    CASH_FILE.write_text(f"{cash:.2f}")

def load_trades():
    if TRADES_FILE.exists():
        return pd.read_csv(TRADES_FILE)
    return pd.DataFrame(columns=['日期','类型','代码','价格','股数','金额','费用','盈亏','备注'])

def save_trades(trades):
    trades.to_csv(TRADES_FILE, index=False)

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

def main():
    # 目标日期
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    else:
        dates = pl.read_parquet(MARKET, columns=['日期'])['日期'].to_list()
        target = max(dates)
    
    print(f"=== 模拟盘引擎 {target} ===")
    
    # 市场状态
    market = pl.read_parquet(MARKET)
    m = market.filter(pl.col('日期') == target)
    if len(m) == 0:
        print(f"错误: {target} 无市场数据"); return
    m = m.row(0, named=True)
    hs300 = pl.read_parquet(HS300).sort('日期')
    h = hs300.filter(pl.col('日期') == target)
    hs_above = len(h) > 0 and h['close'][0] > h['ma_20'][0]
    north = m.get('北向净买入')
    conds = sum([hs_above, m['涨停家数'] > 60, north is not None and north > 0])
    
    # 加载当日数据（前5日算limit_up_5d）
    all_dates = pl.read_parquet(MARKET, columns=['日期'])['日期'].to_list()
    idx = all_dates.index(target)
    start_date = all_dates[max(0, idx-6)]
    need_cols = ['日期','股票代码','收盘','ret_1d','ret_5d',
                 'limit_up','limit_down','is_suspended',
                 'turn_ratio','turn_ma5','vol_ratio','vol_change_5d','vol_10d',
                 'ma_5','ma_20','ma_60','ma5_dist','macd_dif','price_pos_20']
    df = pl.scan_parquet(factor_files()).filter(
        (pl.col('日期') >= start_date) & (pl.col('日期') <= target)
    ).select(need_cols).with_columns(
        pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
    ).collect()
    today_df = df.filter(pl.col('日期') == target)
    data_map = {r['股票代码']: r for r in today_df.iter_rows(named=True)}
    
    # 状态
    positions = load_state()
    cash = load_cash()
    trades = load_trades()
    today_trades = []
    
    # 1. 出场检查
    for h in positions[:]:
        code = h['code']
        row = data_map.get(code)
        if row is None: continue
        close = row['收盘']
        ret1d = row['ret_1d']
        held = (target - datetime.strptime(h['buy_date'], '%Y-%m-%d').date()).days
        if held < 1: continue  # T+1
        if ret1d is not None and ret1d <= -0.095: continue  # 跌停不可卖
        
        cost = h['buy_price']
        pnl = (close - cost) / cost
        h['peak'] = max(h.get('peak', cost), close)
        
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
            sell_price = close * (1 - slip)
            sell_shares = int(h['shares'] * sell_pct)
            if sell_shares < 100: sell_shares = h['shares']
            sell_amt = sell_shares * sell_price
            fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
            profit = sell_shares * (sell_price - cost) - fee
            cash += sell_amt - fee
            trades = pd.concat([trades, pd.DataFrame([{
                '日期': str(target), '类型': '卖出', '代码': code,
                '价格': round(sell_price,2), '股数': sell_shares,
                '金额': round(sell_amt,2), '费用': round(fee,2),
                '盈亏': round(profit,2), '备注': f'{reason}(盈亏{pnl*100:.1f}%)'
            }])], ignore_index=True)
            today_trades.append(f"卖出 {code} {sell_shares}股 @{sell_price:.2f} {reason} 盈亏{profit:+.0f}")
            h['shares'] -= sell_shares
            if sell_pct == 0.5: h['half_sold'] = True
            if h['shares'] < 100: positions.remove(h)
    
    # 2. 市场条件 + 买入
    if conds >= 2 and len(positions) < N_SLOTS and cash >= POSITION:
        held_codes = {h['code'] for h in positions}
        cand = today_df.filter(~pl.col('股票代码').is_in(held_codes))
        cand = cand.filter(
            (pl.col('is_suspended')==0)&(pl.col('limit_up')==0)&(pl.col('limit_down')==0)
            &(pl.col('price_pos_20')<0.85)&(pl.col('price_pos_20')>0.1)
            &(pl.col('收盘')>pl.col('ma_20'))&(pl.col('收盘')<19.5)
            &pl.col('turn_ma5').is_not_null()&pl.col('macd_dif').is_not_null()
            &pl.col('ret_5d').is_not_null())
        if len(cand) > 0:
            scored = compute_score(cand)
            top = scored.sort('score', descending=True).head(TOP_N)
            for row in top.iter_rows(named=True):
                if len(positions) >= N_SLOTS or cash < POSITION: break
                code = row['股票代码']
                slip = slippage(row['ret_1d'])
                buy_price = float(row['收盘']) * (1 + slip)
                shares = int(POSITION / buy_price / 100) * 100
                if shares < 100: continue
                cost = shares * buy_price
                fee = max(COMM_MIN, cost * COMM_RATE)
                if cost + fee > cash: continue
                cash -= cost + fee
                positions.append({
                    'code': code, 'buy_date': str(target),
                    'buy_price': round(buy_price,2), 'shares': shares,
                    'peak': buy_price, 'half_sold': False
                })
                trades = pd.concat([trades, pd.DataFrame([{
                    '日期': str(target), '类型': '买入', '代码': code,
                    '价格': round(buy_price,2), '股数': shares,
                    '金额': round(cost,2), '费用': round(fee,2),
                    '盈亏': 0, '备注': f'评分{round(float(row["score"]),1)}'
                }])], ignore_index=True)
                today_trades.append(f"买入 {code} {shares}股 @{buy_price:.2f}")
    
    # 3. 报告
    print(f"\n市场条件: {conds}/3 {'✅可操作' if conds >= 2 else '⚠️观望'}")
    print(f"现金: {cash:.0f}元")
    print(f"\n今日操作:")
    if today_trades:
        for t in today_trades: print(f"  {t}")
    else:
        print("  无操作")
    
    print(f"\n当前持仓 ({len(positions)}):")
    total_value = cash
    for h in positions:
        row = data_map.get(h['code'])
        if row:
            cur = row['收盘']
            pnl = (cur - h['buy_price']) / h['buy_price'] * 100
            value = h['shares'] * cur
            total_value += value
            print(f"  {h['code']} {h['shares']}股 成本{h['buy_price']:.2f} "
                  f"现价{cur:.2f} 盈亏{pnl:+.1f}% 市值{value:.0f}")
    
    total_pnl = total_value - INIT_CAPITAL
    print(f"\n总资产: {total_value:.0f}元 (总盈亏 {total_pnl:+.0f}元 {total_pnl/INIT_CAPITAL*100:+.1f}%)")
    
    # 4. 保存
    save_state(positions)
    save_cash(cash)
    save_trades(trades)
    print(f"\n状态已保存: positions={len(positions)}, 交易记录={len(trades)}笔")

if __name__ == '__main__':
    main()
