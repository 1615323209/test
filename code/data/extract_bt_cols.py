"""提取回测需要的精简列，减小传输体积"""
import polars as pl
from pathlib import Path

FACTOR = Path("D:/quant_data/factor_daily.parquet")
OUT = Path("D:/quant_data/factor_bt.parquet")

COLS = ['日期', '股票代码', '收盘', '成交量',
        'ret_1d', 'ret_5d', 'limit_up', 'limit_down', 'is_suspended',
        'turn_ratio', 'vol_ratio', 'ma_5', 'ma_20', 'ma_60', 'up_streak',
        'macd_dif', 'macd_dea', 'price_pos_20']

print("提取精简列...")
df = pl.scan_parquet(FACTOR).select(COLS)
# 近5日涨停次数
df = df.with_columns(
    pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
).collect(streaming=True)

# 转 float32 减小体积
for c in df.columns:
    if c not in ['日期', '股票代码']:
        df = df.with_columns(pl.col(c).cast(pl.Float32))

df.write_parquet(OUT, compression='zstd')
print(f"完成: {OUT} ({OUT.stat().st_size/1024/1024:.0f}MB, {len(df):,}行)")
