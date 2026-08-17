#!/usr/bin/env python3
"""阶段1-步骤1：计算 forward return（未来1/5/10/20日收益）"""
import polars as pl
from pathlib import Path

FACTOR = Path("D:/quant_data/factor_daily.parquet")
OUT = Path("D:/quant_data/ic_data.parquet")

print("=== 计算 forward return ===")

# 加载必要列，计算未来收益
lf = pl.scan_parquet(FACTOR).select([
    '日期', '股票代码', '收盘',
    'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d',
    'vol_5d', 'vol_10d', 'vol_20d',
    'ma_5', 'ma_10', 'ma_20', 'ma_60',
    'ma5_dist', 'ma20_dist', 'ma5_ma20_cross', 'ma5_ma20_dead',
    'vol_ratio', 'vol_ratio_20', 'vol_change_5d',
    'turn_ma5', 'turn_ma20', 'turn_ratio',
    'atr_14', 'atr_ratio',
    'high_20d', 'low_20d', 'high_60d', 'low_60d',
    'price_pos_20', 'price_pos_60',
    'macd_dif', 'macd_dea', 'macd_hist',
    'rsi_14', 'bb_width', 'bb_pos',
    'limit_up', 'limit_down', 'is_suspended',
    'up_streak', 'down_streak',
])

# forward return：未来N日收益（用 shift(-n)）
lf = lf.with_columns([
    pl.col('收盘').shift(-1).over('股票代码').truediv(pl.col('收盘')).sub(1).alias('fwd_1d'),
    pl.col('收盘').shift(-5).over('股票代码').truediv(pl.col('收盘')).sub(1).alias('fwd_5d'),
    pl.col('收盘').shift(-10).over('股票代码').truediv(pl.col('收盘')).sub(1).alias('fwd_10d'),
    pl.col('收盘').shift(-20).over('股票代码').truediv(pl.col('收盘')).sub(1).alias('fwd_20d'),
])

# 流式收集（避免OOM）
print("streaming collect...")
df = lf.collect(streaming=True)

# 转 float32 减小体积
for c in df.columns:
    if c not in ['日期', '股票代码']:
        df = df.with_columns(pl.col(c).cast(pl.Float32))

df.write_parquet(OUT, compression='zstd')
print(f"完成: {OUT} ({OUT.stat().st_size/1024/1024:.0f}MB, {len(df):,}行)")
print(f"列: {df.columns}")
