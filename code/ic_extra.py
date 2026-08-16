#!/usr/bin/env python3
"""扩展因子 IC 体检 — 5个新因子的横截面 IC（fwd_1d/5d/10d/20d）
数据：factor_extra_daily.parquet join ic_data.parquet 的 forward 收益
用法：python3 ic_extra.py [--seg]（--seg 输出半年分段稳定性）
"""
import polars as pl
import pandas as pd
from pathlib import Path
import sys

DATA = Path("/home/ubuntu/quant_data")
EXTRA = DATA / "factor_extra_daily.parquet"
IC = DATA / "ic_data.parquet"
OUT = DATA / "ic_extra_report.csv"

EXTRA_COLS = ['illiq_20', 'vol_corr_5', 'vol_corr_20', 'skew_20', 'kurt_20']
HORIZONS = ['fwd_1d', 'fwd_5d', 'fwd_10d', 'fwd_20d']

print("=== 扩展因子 IC 体检 ===")
df = (pl.scan_parquet(EXTRA)
      .join(pl.scan_parquet(IC).select(['日期', '股票代码'] + HORIZONS),
            on=['日期', '股票代码'], how='inner')
      .collect())
print(f"行数: {len(df):,}")

rows = []
for f in EXTRA_COLS:
    for h in HORIZONS:
        ic = (df.select(['日期', f, h])
              .group_by('日期')
              .agg(pl.corr(pl.col(f), pl.col(h), method='spearman').alias('ic'))
              .sort('日期'))
        ic = ic.filter(pl.col('ic').is_not_null())
        v = ic['ic']
        m, s = v.mean(), v.std()
        if s is None or s == 0 or m is None:
            continue
        r = {'factor': f, 'horizon': h, 'ic_mean': round(float(m), 4),
             'icir': round(float(m/s), 3), 'ic_pos_pct': round(float((v > 0).mean())*100, 1),
             'days': len(v)}
        if '--seg' in sys.argv:
            ic2 = ic.with_columns(((pl.col('日期').dt.year() - 2010) * 2
                                   + (pl.col('日期').dt.month() > 6)).alias('seg'))
            seg = ic2.group_by('seg').agg(pl.col('ic').mean().alias('seg_ic')).sort('seg')
            seg_ics = seg['seg_ic'].to_list()
            sign = 1 if m > 0 else -1
            r['seg_ok_ratio'] = round(sum(1 for x in seg_ics if x*sign > 0)/len(seg_ics), 3)
            r['last2_ok'] = all(x*sign > 0 for x in seg_ics[-2:])
        rows.append(r)

res = pd.DataFrame(rows)
res.to_csv(OUT, index=False)
print(f"\n输出: {OUT}")
strong = res[res['icir'].abs() >= 0.25]
print(f"|ICIR|>=0.25 的: {len(strong)} 条")
print(strong.to_string(index=False))
