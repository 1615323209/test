"""C2 对账差异定位：直接对比每笔的 现金价差 vs 复权损益"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl
from datetime import datetime
from pathlib import Path

DATA = Path("D:/quant_data")

# 手动复现一笔交易的账目: 买入 sh600519 某日 → 卖出某日
# 对比: 不复权现金差 vs 复权收益差
factor = pl.read_parquet(DATA / "factor_daily.parquet", columns=["日期", "股票代码", "收盘"])
raw = pl.read_parquet(DATA / "raw_close.parquet").with_columns(pl.col("日期").str.to_date())

# 找一只除权股: 茅台2021-2022 (茅台2021-06 分红)
for code in ["600519", "000001", "000858"]:
    f = factor.filter(pl.col("股票代码") == code).sort("日期")
    r = raw.filter(pl.col("股票代码") == code).sort("日期")
    j = f.join(r, on=["日期", "股票代码"], how="inner")
    if len(j) < 100:
        continue
    j = j.with_columns([
        (pl.col("收盘") / pl.col("收盘_不复权")).alias("adj"),  # 复权因子
    ])
    # 看复权因子变化(除权日跳变)
    adj_chg = j.with_columns(pl.col("adj").diff().alias("adj_diff")).filter(pl.col("adj_diff").abs() > 0.01)
    print(f"{code}: 复权因子范围 {j['adj'].min():.3f}~{j['adj'].max():.3f}, 跳变日 {adj_chg.height}")
    if adj_chg.height:
        print(f"  除权日样例: {adj_chg.select(['日期','adj','adj_diff']).head(3)}")
        # 除权日前后: 复权价 vs 不复权价
        d0 = adj_chg['日期'][0]
        seg = j.filter((pl.col('日期') >= d0 - pl.duration(days=3)) & (pl.col('日期') <= d0 + pl.duration(days=3)))
        print(seg.select(['日期', '收盘', '收盘_不复权', 'adj']))
