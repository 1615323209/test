"""更新 hs300 指数数据（增量）"""
import akshare as ak
import polars as pl
from pathlib import Path

HS300 = Path('/home/ubuntu/quant_data/hs300.parquet')

old = pl.read_parquet(HS300).sort('日期')
last_date = old['日期'].max()
print(f"旧数据: {len(old)} 行, 最新: {last_date}")

df = ak.stock_zh_index_daily_tx(symbol='sz399300')
new_raw = pl.from_pandas(df).rename({'date':'日期','open':'open','close':'close',
                                     'high':'high','low':'low','amount':'amount'})
new = new_raw.filter(pl.col('日期') > last_date)
print(f"新数据: {len(new)} 行, {new['日期'].to_list() if len(new)>0 else '无'}")

if len(new) > 0:
    # 对齐旧列结构（date + 日期 + ma_20占位）
    new = new.with_columns([
        pl.col('日期').alias('date'),
        pl.lit(None, dtype=pl.Float64).alias('ma_20'),
    ])
    new = new.select(['date','open','close','high','low','amount','日期','ma_20'])
    merged = pl.concat([old, new]).sort('日期')
    # 重算 ma_20（保证窗口正确）
    merged = merged.with_columns(
        pl.col('close').rolling_mean(20, min_samples=20).alias('ma_20')
    )
    merged = merged.select(['date','open','close','high','low','amount','日期','ma_20'])
    merged.write_parquet(HS300)
    print(f"已更新: {len(merged)} 行")
    print(merged.tail(4))
else:
    print("无需更新")
