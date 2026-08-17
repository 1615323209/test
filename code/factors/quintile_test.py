#!/usr/bin/env python3
"""代表性因子 quintile 分层验证
对 v7 使用的因子 + 挖掘 Top 因子做 5 层分组，看收益单调性
"""
import polars as pl
import pandas as pd
import numpy as np
from datetime import datetime

# 代表性因子（去相关后）
FACTORS = {
    'ret_5d_x_turn_ma5':    pl.col('ret_5d') * pl.col('turn_ma5'),      # 涨多×高换手
    'ma5_dist_x_turn_ma5':  pl.col('ma5_dist') * pl.col('turn_ma5'),    # 距5日线×高换手
    'vol_ratio_20_p_turn':  pl.col('vol_ratio_20') + pl.col('turn_ma20'),# 放量×高换手
    'vol_10d_p_chg':        pl.col('vol_10d') + pl.col('vol_change_5d'), # 高波动×放量
    'limit_up_5d':          pl.col('limit_up'),                       # 涨停惯性(正)
    'turn_ratio':           pl.col('turn_ratio'),                        # 换手率
}

print("=== 因子 quintile 分层验证（fwd_5d）===")
ic_data = 'D:/quant_data/ic_data.parquet'

for name, expr in FACTORS.items():
    df = pl.scan_parquet(ic_data).select(['日期','fwd_5d','ret_5d','turn_ma5',
        'ma5_dist','vol_ratio_20','turn_ma20','vol_10d','vol_change_5d',
        'limit_up_5d' if False else 'limit_up','turn_ratio'])
    df = df.with_columns(expr.alias('f'))
    df = df.filter(pl.col('f').is_not_null() & pl.col('fwd_5d').is_not_null())
    df = df.with_columns(
        pl.col('f').rank(method='average').over('日期').alias('rank')
    )
    # 按日分组 5 层
    df = df.with_columns(
        (pl.col('rank') / pl.col('rank').max().over('日期') * 5).cast(pl.Int32).clip(0,4).alias('quintile')
    )
    # 每层平均未来收益
    q = df.group_by('quintile').agg(
        平均fwd=pl.col('fwd_5d').mean()*100,
        样本数=pl.len()
    ).sort('quintile').collect()
    vals = [round(r['平均fwd'],2) for r in q.iter_rows(named=True)]
    mono = '✅单调' if all(vals[i] > vals[i+1] for i in range(len(vals)-1)) or all(vals[i] < vals[i+1] for i in range(len(vals)-1)) else '❌非单调'
    spread = round(vals[-1] - vals[0], 2)
    print(f"\n{name} (Q1→Q5 fwd_5d%): {vals} spread={spread} {mono}")
