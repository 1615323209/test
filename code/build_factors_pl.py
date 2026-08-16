#!/usr/bin/env python3
"""A股因子库 - Polars版，独立batch+最后合并"""
import polars as pl
import pyarrow.parquet as pq
from pathlib import Path
import gc, shutil

INPUT = Path("/home/ubuntu/quant_data/a_stock_daily_hfq.parquet")
OUTPUT = Path("/home/ubuntu/quant_data/factor_daily.parquet")
TMPDIR = Path("/home/ubuntu/quant_data/factor_tmp")
CHUNK = 500

TMPDIR.mkdir(exist_ok=True)
for f in TMPDIR.glob("batch_*.parquet"):
    f.unlink()

print("=== Polars因子库 v3 (独立batch) ===")

codes = sorted(pl.scan_parquet(INPUT).select('股票代码').unique().collect()['股票代码'].to_list())
total = len(codes)
print(f"股票: {total} 只")

def calc_factors(df):
    df = df.sort(['股票代码', '日期'])
    df = df.with_columns([
        pl.col('收盘').pct_change().over('股票代码').alias('ret_1d'),
        pl.col('收盘').pct_change(5).over('股票代码').alias('ret_5d'),
        pl.col('收盘').pct_change(10).over('股票代码').alias('ret_10d'),
        pl.col('收盘').pct_change(20).over('股票代码').alias('ret_20d'),
    ])
    df = df.with_columns([
        pl.col('ret_1d').rolling_std(5, min_samples=5).over('股票代码').mul(15.8745).alias('vol_5d'),
        pl.col('ret_1d').rolling_std(10, min_samples=10).over('股票代码').mul(15.8745).alias('vol_10d'),
        pl.col('ret_1d').rolling_std(20, min_samples=20).over('股票代码').mul(15.8745).alias('vol_20d'),
        pl.col('收盘').rolling_mean(5, min_samples=5).over('股票代码').alias('ma_5'),
        pl.col('收盘').rolling_mean(10, min_samples=10).over('股票代码').alias('ma_10'),
        pl.col('收盘').rolling_mean(20, min_samples=20).over('股票代码').alias('ma_20'),
        pl.col('收盘').rolling_mean(60, min_samples=60).over('股票代码').alias('ma_60'),
        pl.col('成交量').rolling_mean(5, min_samples=5).over('股票代码').alias('vol_ma5'),
        pl.col('成交量').rolling_mean(20, min_samples=20).over('股票代码').alias('vol_ma20'),
    ])
    prev_c = pl.col('收盘').shift(1).over('股票代码')
    df = df.with_columns(pl.max_horizontal(
        (pl.col('最高')-pl.col('最低')).abs(),
        (pl.col('最高')-prev_c).abs(), (pl.col('最低')-prev_c).abs()).alias('_tr'))
    df = df.with_columns([
        pl.col('_tr').rolling_mean(14, min_samples=14).over('股票代码').alias('atr_14'),
        (pl.col('_tr').rolling_mean(14, min_samples=14).over('股票代码')/pl.col('收盘')).alias('atr_ratio'),
    ])
    ma5s = pl.col('ma_5').shift(1).over('股票代码')
    ma20s = pl.col('ma_20').shift(1).over('股票代码')
    df = df.with_columns([
        ((pl.col('收盘')-pl.col('ma_5'))/pl.col('ma_5')).alias('ma5_dist'),
        ((pl.col('收盘')-pl.col('ma_20'))/pl.col('ma_20')).alias('ma20_dist'),
        ((pl.col('ma_5')>pl.col('ma_20'))&(ma5s<=ma20s)).cast(pl.Int32).alias('ma5_ma20_cross'),
        ((pl.col('ma_5')<pl.col('ma_20'))&(ma5s>=ma20s)).cast(pl.Int32).alias('ma5_ma20_dead'),
    ])
    exprs = [
        (pl.col('成交量')/pl.col('vol_ma5')).alias('vol_ratio'),
        (pl.col('成交量')/pl.col('vol_ma20')).alias('vol_ratio_20'),
        pl.col('成交量').pct_change(5).over('股票代码').alias('vol_change_5d'),
    ]
    if 'turnover' in df.columns:
        t5 = pl.col('turnover').rolling_mean(5, min_samples=5).over('股票代码')
        exprs.extend([t5.alias('turn_ma5'),
            pl.col('turnover').rolling_mean(20, min_samples=20).over('股票代码').alias('turn_ma20'),
            (pl.col('turnover')/t5).alias('turn_ratio')])
    df = df.with_columns(exprs)
    df = df.with_columns([
        pl.col('最高').rolling_max(20, min_samples=20).over('股票代码').alias('high_20d'),
        pl.col('最低').rolling_min(20, min_samples=20).over('股票代码').alias('low_20d'),
        pl.col('最高').rolling_max(60, min_samples=60).over('股票代码').alias('high_60d'),
        pl.col('最低').rolling_min(60, min_samples=60).over('股票代码').alias('low_60d'),
    ])
    df = df.with_columns([
        ((pl.col('收盘')-pl.col('low_20d'))/(pl.col('high_20d')-pl.col('low_20d')+1e-10)).alias('price_pos_20'),
        ((pl.col('收盘')-pl.col('low_60d'))/(pl.col('high_60d')-pl.col('low_60d')+1e-10)).alias('price_pos_60'),
    ])
    e12=pl.col('收盘').ewm_mean(span=12, min_samples=12).over('股票代码')
    e26=pl.col('收盘').ewm_mean(span=26, min_samples=26).over('股票代码')
    dif=e12-e26; dea=dif.ewm_mean(span=9, min_samples=9).over('股票代码')
    df=df.with_columns([dif.alias('macd_dif'),dea.alias('macd_dea'),(2*(dif-dea)).alias('macd_hist')])
    d=pl.col('收盘').diff().over('股票代码'); g=d.clip(0,None); l=d.clip(None,0).abs()
    df=df.with_columns((100-100/(1+g.rolling_mean(14,min_samples=14).over('股票代码')/(l.rolling_mean(14,min_samples=14).over('股票代码')+1e-10))).alias('rsi_14'))
    mid=pl.col('收盘').rolling_mean(20,min_samples=20).over('股票代码')
    std=pl.col('收盘').rolling_std(20,min_samples=20).over('股票代码')
    df=df.with_columns([mid.alias('bb_mid'),(mid+2*std).alias('bb_upper'),(mid-2*std).alias('bb_lower'),
        ((mid+2*std-(mid-2*std))/mid).alias('bb_width'),
        ((pl.col('收盘')-(mid-2*std))/(mid+2*std-(mid-2*std)+1e-10)).alias('bb_pos')])
    df=df.with_columns([(pl.col('ret_1d')>0.095).cast(pl.Int32).alias('limit_up'),
        (pl.col('ret_1d')<-0.095).cast(pl.Int32).alias('limit_down'),
        (pl.col('成交量')==0).cast(pl.Int32).alias('is_suspended')])
    return df.drop(['_tr'])

# 阶段1：逐批保存
for i in range(0, total, CHUNK):
    batch_codes = codes[i:i+CHUNK]
    bn = i//CHUNK + 1
    print(f"  [{i+1}/{total}] batch_{bn:04d}...", end=' ')
    df = pl.scan_parquet(INPUT).filter(pl.col('股票代码').is_in(batch_codes)).collect()
    factored = calc_factors(df)
    f = TMPDIR / f"batch_{bn:04d}.parquet"
    factored.write_parquet(f, compression='zstd')
    print(f"OK ({len(factored):,}行)")
    del df, factored; gc.collect()

# 阶段2：合并
print("\n合并...")
writer = None
for f in sorted(TMPDIR.glob("batch_*.parquet")):
    t = pq.read_table(f)
    if writer is None: writer = pq.ParquetWriter(OUTPUT, t.schema)
    writer.write_table(t)
writer.close()
shutil.rmtree(TMPDIR)
print(f"=== 完成: {OUTPUT.stat().st_size/1024/1024:.0f}MB ===")
