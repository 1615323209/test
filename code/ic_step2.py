#!/usr/bin/env python3
"""阶段1-步骤2：45因子 IC 体检
横截面 IC = 每个交易日，因子值与未来收益的 Spearman 相关
汇总：IC均值 / IC标准差 / ICIR / IC>0比例 / 胜率
"""
import polars as pl
from pathlib import Path

IC_DATA = Path("/home/ubuntu/quant_data/ic_data.parquet")
OUT = Path("/home/ubuntu/quant_data/ic_report.csv")

FACTORS = ['ret_1d','ret_5d','ret_10d','ret_20d',
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
HORIZONS = ['fwd_1d','fwd_5d','fwd_10d','fwd_20d']

print("=== 45因子 IC 体检 ===")
print(f"因子数: {len(FACTORS)}, 预测周期: {HORIZONS}")

# 流式加载
lf = pl.scan_parquet(IC_DATA)

results = []
for f in FACTORS:
    for h in HORIZONS:
        # 按日期算横截面 Spearman 相关
        ic = (lf.select(['日期', f, h])
              .group_by('日期')
              .agg(pl.corr(pl.col(f), pl.col(h), method='spearman').alias('ic'))
              .collect())
        ic_vals = ic['ic'].fill_nan(None).drop_nulls()
        if len(ic_vals) == 0:
            continue
        ic_mean = ic_vals.mean()
        ic_std = ic_vals.std()
        icir = ic_mean / ic_std if ic_std and ic_std > 0 else 0
        ic_pos = (ic_vals > 0).mean() * 100
        ic_abs = (ic_vals.abs() > 0.02).mean() * 100
        results.append({
            'factor': f, 'horizon': h,
            'ic_mean': round(ic_mean, 4),
            'ic_std': round(ic_std, 4),
            'icir': round(icir, 4),
            'ic_pos_pct': round(ic_pos, 1),
            'ic_abs02_pct': round(ic_abs, 1),
            'days': len(ic_vals),
        })

# 输出报告
import pandas as pd
rep = pd.DataFrame(results)
rep.to_csv(OUT, index=False)

print(f"\n=== 有效因子 TOP（按 |ICIR| 排序，fwd_5d）===")
for h in HORIZONS:
    sub = rep[rep['horizon'] == h].copy()
    sub['abs_icir'] = sub['icir'].abs()
    top = sub.sort_values('abs_icir', ascending=False).head(10)
    print(f"\n--- {h} ---")
    print(top[['factor','ic_mean','icir','ic_pos_pct']].to_string(index=False))

print(f"\n报告: {OUT}")
