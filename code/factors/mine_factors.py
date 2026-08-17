#!/usr/bin/env python3
"""因子挖掘 v2 — 两两组合 + IC 筛选（内存复用版）
一次加载基础数据到内存，候选因子逐个计算横截面 IC
"""
import polars as pl
import pandas as pd
from pathlib import Path
import itertools, time, gc

IC_DATA = Path("D:/quant_data/ic_data.parquet")
OUT = Path("D:/quant_data/mined_factors.csv")

HORIZON = 'fwd_5d'
BASE = ['ret_1d','ret_5d','ret_10d','ret_20d',
    'vol_5d','vol_10d','vol_20d',
    'ma_5','ma_10','ma_20','ma_60',
    'ma5_dist','ma20_dist','ma5_ma20_cross','ma5_ma20_dead',
    'vol_ratio','vol_ratio_20','vol_change_5d',
    'turn_ma5','turn_ma20','turn_ratio',
    'atr_14','atr_ratio',
    'high_20d','low_20d','high_60d','low_60d',
    'price_pos_20','price_pos_60',
    'macd_dif','macd_dea','macd_hist',
    'rsi_14','bb_width','bb_pos',
    'limit_up','limit_down','is_suspended',
    'up_streak','down_streak']

print(f"=== 因子挖掘 v2 ===")
print(f"基础因子: {len(BASE)} → 候选: {len(BASE)*(len(BASE)-1)//2*3}")

# 一次加载到内存（只保留 fwd_5d 非空的行 + 需要列）
t0 = time.time()
df = pl.scan_parquet(IC_DATA).select(['日期', HORIZON] + BASE).collect()
print(f"加载: {len(df):,}行 × {len(df.columns)}列, {time.time()-t0:.0f}s")

# 内存中预计算每组的 corr（用 group_by 前先 drop null）
def compute_ic(name, expr):
    d = df.with_columns(expr.alias('_cand'))
    ic = (d.select(['日期','_cand',HORIZON])
          .group_by('日期')
          .agg(pl.corr(pl.col('_cand'), pl.col(HORIZON), method='spearman').alias('ic')))
    ic_vals = ic['ic'].fill_nan(None).drop_nulls()
    if len(ic_vals) < 200:
        return None
    ic_mean = ic_vals.mean()
    ic_std = ic_vals.std()
    if ic_std is None or ic_std == 0:
        return None
    icir = ic_mean / ic_std
    ic_pos = (ic_vals > 0).mean() * 100
    return {'expr': name, 'ic_mean': round(float(ic_mean),4),
            'icir': round(float(icir),4), 'ic_pos_pct': round(float(ic_pos),1),
            'days': len(ic_vals)}

results = []
combos = list(itertools.combinations(BASE, 2))
t0 = time.time()

for i, (a, b) in enumerate(combos):
    ca, cb = pl.col(a), pl.col(b)
    for name, expr in [
        (f'{a}_x_{b}', ca * cb),
        (f'{a}_d_{b}', ca - cb),
        (f'{a}_p_{b}', ca + cb),
    ]:
        r = compute_ic(name, expr)
        if r:
            results.append(r)
    
    if (i+1) % 100 == 0:
        elapsed = time.time() - t0
        print(f"  [{i+1}/{len(combos)}] 有效候选 {len(results)}, {elapsed:.0f}s")
        pd.DataFrame(results).to_csv(OUT, index=False)

df_res = pd.DataFrame(results)
df_res.to_csv(OUT, index=False)
print(f"\n=== 完成 ===")
print(f"有效候选: {len(df_res)}, 耗时 {time.time()-t0:.0f}s")
print(f"文件: {OUT}")

strong = df_res[(df_res['ic_mean'].abs() > 0.03) & (df_res['icir'].abs() > 0.5)].sort_values('icir', ascending=False)
print(f"\n=== 强因子 (|IC|>0.03 且 |ICIR|>0.5): {len(strong)} 个 ===")
print(strong.head(30).to_string(index=False))
