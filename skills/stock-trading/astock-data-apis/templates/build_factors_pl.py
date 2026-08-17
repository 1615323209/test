#!/usr/bin/env python3
"""A股因子库构建 — Polars版（推荐，比pandas快3-5倍，内存省70%）
输入：a_stock_daily_hfq.parquet
输出：factor_daily.parquet
策略：每批500只scan_parquet + filter collect，增量写入parquet。
      适用于2-4GB低内存服务器（配合6GB swap）。
依赖：pip install polars pyarrow
"""
import polars as pl
from pathlib import Path
import gc
import pyarrow.parquet as pq
import pyarrow as pa

INPUT = Path("/home/ubuntu/quant_data/a_stock_daily_hfq.parquet")
OUTPUT = Path("/home/ubuntu/quant_data/factor_daily.parquet")
CHUNK = 500  # 每批股票数

print("=== Polars因子库 ===")

codes = sorted(pl.scan_parquet(INPUT).select('股票代码').unique().collect()['股票代码'].to_list())
total = len(codes)
print(f"股票: {total} 只, 每批 {CHUNK} 只")

def calc_factors(df):
    df = df.sort(['股票代码', '日期'])
    df = df.with_columns([
        pl.col('收盘').pct_change().over('股票代码').alias('ret_1d'),
        pl.col('收盘').pct_change(5).over('股票代码').alias('ret_5d'),
        pl.col('收盘').pct_change(10).over('股票代码').alias('ret_10d'),
        pl.col('收盘').pct_change(20).over('股票代码').alias('ret_20d'),
    ])
    df = df.with_columns([
        pl.col('ret_1d').rolling_std(5,min_periods=5).over('股票代码').mul(15.8745).alias('vol_5d'),
        pl.col('ret_1d').rolling_std(10,min_periods=10).over('股票代码').mul(15.8745).alias('vol_10d'),
        pl.col('ret_1d').rolling_std(20,min_periods=20).over('股票代码').mul(15.8745).alias('vol_20d'),
        pl.col('收盘').rolling_mean(5,min_periods=5).over('股票代码').alias('ma_5'),
        pl.col('收盘').rolling_mean(10,min_periods=10).over('股票代码').alias('ma_10'),
        pl.col('收盘').rolling_mean(20,min_periods=20).over('股票代码').alias('ma_20'),
        pl.col('收盘').rolling_mean(60,min_periods=60).over('股票代码').alias('ma_60'),
        pl.col('成交量').rolling_mean(5,min_periods=5).over('股票代码').alias('vol_ma5'),
        pl.col('成交量').rolling_mean(20,min_periods=20).over('股票代码').alias('vol_ma20'),
    ])
    prev_c = pl.col('收盘').shift(1).over('股票代码')
    df = df.with_columns([
        pl.max_horizontal((pl.col('最高')-pl.col('最低')).abs(),(pl.col('最高')-prev_c).abs(),(pl.col('最低')-prev_c).abs()).alias('_tr')
    ])
    df = df.with_columns([
        pl.col('_tr').rolling_mean(14,min_periods=14).over('股票代码').alias('atr_14'),
        (pl.col('_tr').rolling_mean(14,min_periods=14).over('股票代码')/pl.col('收盘')).alias('atr_ratio'),
    ])
    ma5_s1=pl.col('ma_5').shift(1).over('股票代码'); ma20_s1=pl.col('ma_20').shift(1).over('股票代码')
    df = df.with_columns([
        ((pl.col('收盘')-pl.col('ma_5'))/pl.col('ma_5')).alias('ma5_dist'),
        ((pl.col('收盘')-pl.col('ma_20'))/pl.col('ma_20')).alias('ma20_dist'),
        ((pl.col('ma_5')>pl.col('ma_20'))&(ma5_s1<=ma20_s1)).cast(pl.Int32).alias('ma5_ma20_cross'),
        ((pl.col('ma_5')<pl.col('ma_20'))&(ma5_s1>=ma20_s1)).cast(pl.Int32).alias('ma5_ma20_dead'),
    ])
    exprs = [
        (pl.col('成交量')/pl.col('vol_ma5')).alias('vol_ratio'),
        (pl.col('成交量')/pl.col('vol_ma20')).alias('vol_ratio_20'),
        pl.col('成交量').pct_change(5).over('股票代码').alias('vol_change_5d'),
    ]
    if 'turnover' in df.columns:
        t_ma5=pl.col('turnover').rolling_mean(5,min_periods=5).over('股票代码')
        exprs.extend([t_ma5.alias('turn_ma5'),pl.col('turnover').rolling_mean(20,min_periods=20).over('股票代码').alias('turn_ma20'),(pl.col('turnover')/t_ma5).alias('turn_ratio')])
    df = df.with_columns(exprs)
    df = df.with_columns([
        pl.col('最高').rolling_max(20,min_periods=20).over('股票代码').alias('high_20d'),
        pl.col('最低').rolling_min(20,min_periods=20).over('股票代码').alias('low_20d'),
        pl.col('最高').rolling_max(60,min_periods=60).over('股票代码').alias('high_60d'),
        pl.col('最低').rolling_min(60,min_periods=60).over('股票代码').alias('low_60d'),
    ])
    df = df.with_columns([
        ((pl.col('收盘')-pl.col('low_20d'))/(pl.col('high_20d')-pl.col('low_20d')+1e-10)).alias('price_pos_20'),
        ((pl.col('收盘')-pl.col('low_60d'))/(pl.col('high_60d')-pl.col('low_60d')+1e-10)).alias('price_pos_60'),
    ])
    ema12=pl.col('收盘').ewm_mean(span=12,min_periods=12).over('股票代码'); ema26=pl.col('收盘').ewm_mean(span=26,min_periods=26).over('股票代码')
    dif=ema12-ema26; dea=dif.ewm_mean(span=9,min_periods=9).over('股票代码')
    df=df.with_columns([dif.alias('macd_dif'),dea.alias('macd_dea'),(2*(dif-dea)).alias('macd_hist')])
    delta=pl.col('收盘').diff().over('股票代码'); gain=delta.clip(0,None); loss=delta.clip(None,0).abs()
    df=df.with_columns([(100-100/(1+gain.rolling_mean(14,min_periods=14).over('股票代码')/(loss.rolling_mean(14,min_periods=14).over('股票代码')+1e-10))).alias('rsi_14')])
    bb_mid=pl.col('收盘').rolling_mean(20,min_periods=20).over('股票代码'); bb_std=pl.col('收盘').rolling_std(20,min_periods=20).over('股票代码')
    df=df.with_columns([bb_mid.alias('bb_mid'),(bb_mid+2*bb_std).alias('bb_upper'),(bb_mid-2*bb_std).alias('bb_lower'),((bb_mid+2*bb_std-(bb_mid-2*bb_std))/bb_mid).alias('bb_width'),((pl.col('收盘')-(bb_mid-2*bb_std))/(bb_mid+2*bb_std-(bb_mid-2*bb_std)+1e-10)).alias('bb_pos')])
    df=df.with_columns([(pl.col('ret_1d')>0.095).cast(pl.Int32).alias('limit_up'),(pl.col('ret_1d')<-0.095).cast(pl.Int32).alias('limit_down'),(pl.col('成交量')==0).cast(pl.Int32).alias('is_suspended')])
    return df.drop(['_tr'])

for i in range(0, total, CHUNK):
    batch = codes[i:i+CHUNK]
    df = pl.scan_parquet(INPUT).filter(pl.col('股票代码').is_in(batch)).collect()
    factored = calc_factors(df)
    if i == 0:
        factored.write_parquet(OUTPUT, compression='zstd')
    else:
        existing = pq.read_table(OUTPUT)
        combined = pa.concat_tables([existing, factored.to_arrow()])
        pq.write_table(combined, OUTPUT, compression='zstd')
    print(f"  [{i+1}/{total}] OK ({len(factored):,}行)")
    del df, factored; gc.collect()

print(f"\n=== 完成: {OUTPUT} ({Path(OUTPUT).stat().st_size/1024/1024:.0f}MB) ===")
