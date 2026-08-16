#!/usr/bin/env python3
"""模拟盘工具 — 每日 v7 打分选股清单
用法: python daily_picks.py [日期]  (默认最新交易日)
输出: Top 3 候选 + 打分明细 + 市场状态
"""
import polars as pl
import pandas as pd
from pathlib import Path
from datetime import datetime, date
import sys

DATA = Path("/home/ubuntu/quant_data")
FACTOR = DATA / "factor_daily.parquet"
FACTOR_INCR = DATA / "factor_daily_incr.parquet"
MARKET = DATA / "market_daily.parquet"
HS300 = DATA / "hs300.parquet"
OUT = DATA / "daily_picks"

def factor_files():
    """因子文件列表（主文件 + 增量）"""
    files = [FACTOR]
    if FACTOR_INCR.exists():
        files.append(FACTOR_INCR)
    return files

INIT_CAPITAL = 20000
POSITION = 2000

# v7 打分权重
W = {'s1': 0.25, 's2': 0.20, 's3': 0.15, 's4': 0.15, 's5': 0.15, 's6': 0.10}

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
        (pl.col('s1')*W['s1'] + pl.col('s2')*W['s2'] + pl.col('s3')*W['s3'] +
         pl.col('s4')*W['s4'] + pl.col('s5')*W['s5'] + pl.col('s6')*W['s6']).alias('score')
    )

def main():
    # 目标日期
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    else:
        # 最新交易日
        dates = pl.read_parquet(MARKET, columns=['日期'])['日期'].to_list()
        target = max(dates)
    print(f"=== 模拟盘选股 {target} ===\n")
    
    # 市场状态
    market = pl.read_parquet(MARKET)
    m = market.filter(pl.col('日期') == target)
    if len(m) == 0:
        print(f"错误: {target} 无市场数据")
        return
    m = m.row(0, named=True)
    hs300 = pl.read_parquet(HS300).sort('日期')
    h = hs300.filter(pl.col('日期') == target)
    hs_ma20 = h['ma_20'][0] if len(h) > 0 else None
    hs_close = h['close'][0] if len(h) > 0 else None
    hs_above = hs_close is not None and hs_ma20 is not None and hs_close > hs_ma20
    print(f"涨停家数: {m['涨停家数']}  跌停: {m['跌停家数']}  上涨占比: {m['上涨占比']*100:.1f}%")
    print(f"沪深300: {hs_close:.0f} (MA20: {hs_ma20:.0f}) {'站上MA20' if hs_above else 'MA20下方'}")
    north = m.get('北向净买入')
    print(f"北向净买入: {north if north is not None else '无数据'}")
    conds = sum([hs_above, m['涨停家数'] > 60, north is not None and north > 0])
    print(f"市场条件满足: {conds}/3 {'✅可操作' if conds >= 2 else '⚠️观望'}\n")
    
    # 打分选股（加载前5天数据以计算 limit_up_5d）
    need_cols = ['日期','股票代码','收盘','ret_1d','ret_5d',
                 'limit_up','limit_down','is_suspended',
                 'turn_ratio','turn_ma5','vol_ratio','vol_change_5d','vol_10d',
                 'ma_5','ma_20','ma_60','ma5_dist','macd_dif','price_pos_20']
    # 找到 target 前 5 个交易日
    all_dates = pl.read_parquet(MARKET, columns=['日期'])['日期'].to_list()
    idx = all_dates.index(target)
    start_date = all_dates[max(0, idx-6)]
    df = pl.scan_parquet(factor_files()).filter(
        (pl.col('日期') >= start_date) & (pl.col('日期') <= target)
    )
    df = df.select(need_cols).with_columns(
        pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
    ).collect()
    df = df.filter(pl.col('日期') == target)
    
    cand = df.filter(
        (pl.col('is_suspended')==0)&(pl.col('limit_up')==0)&(pl.col('limit_down')==0)
        &(pl.col('price_pos_20')<0.85)&(pl.col('price_pos_20')>0.1)
        &(pl.col('收盘')>pl.col('ma_20'))
        &(pl.col('收盘') < 19.5)   # 2000元/仓买得起100股（留滑点余量）
        &pl.col('turn_ma5').is_not_null()
        &pl.col('vol_change_5d').is_not_null()
        &pl.col('macd_dif').is_not_null()
        &pl.col('ret_5d').is_not_null())
    
    if len(cand) == 0:
        print("无候选股票")
        return
    
    scored = compute_score(cand)
    top = scored.sort('score', descending=True).head(5)
    
    print("=== Top 5 候选 ===")
    rows = []
    for r in top.iter_rows(named=True):
        code = r['股票代码']
        price = float(r['收盘'])
        shares = int(POSITION/price/100)*100
        rows.append({
            '排名': len(rows)+1, '代码': code, '收盘': round(price,2),
            '评分': round(float(r['score']),1),
            '可买股数': shares if shares >= 100 else '不足100股',
            'ret_5d': round(r['ret_5d'],3), 'vol_ratio': round(r['vol_ratio'],2),
            'turn_ratio': round(r['turn_ratio'],2),
            '近5日涨停': int(r['limit_up_5d']),
            '站上MA20': round(r['ma_20'],2), 'price_pos': round(r['price_pos_20'],2),
        })
        print(f"  {rows[-1]['排名']}. {code}  收盘{price:.2f}  评分{rows[-1]['评分']}  "
              f"{'可买'+str(shares)+'股' if shares>=100 else '不足100股'}")
        print(f"     ret_5d={r['ret_5d']:.3f} vol={r['vol_ratio']:.2f} turn={r['turn_ratio']:.2f} "
              f"涨停5d={int(r['limit_up_5d'])} pos={r['price_pos_20']:.2f}")
    
    # 保存
    OUT.mkdir(exist_ok=True)
    fname = OUT / f"picks_{target}.csv"
    pd.DataFrame(rows).to_csv(fname, index=False)
    print(f"\n已保存: {fname}")
    print("\n⚠️ 模拟盘记录规则: 每仓2000元, 止损-8%, 止盈+12%, 破MA20减半, 破MA60清仓")

if __name__ == '__main__':
    main()
