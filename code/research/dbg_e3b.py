"""E3 口径差验证: 50元价格上限剔除比例 + 高价股 turn_ratio 分布"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl

ic = pl.read_parquet("D:/quant_data/ic_data.parquet")
ic = ic.filter((pl.col("日期") >= pl.date(2021, 1, 1)) & (pl.col("日期") <= pl.date(2023, 12, 31)))
d = ic.filter((pl.col("is_suspended")==0) & (pl.col("limit_up")==0) & (pl.col("limit_down")==0)
              & pl.col("turn_ratio").is_not_null() & pl.col("fwd_5d").is_not_null())
print(f"可交易域: {len(d)} 行")

# 50元上限(5000/100)剔除比例
d = d.with_columns(pl.col("收盘").alias("hfq_price"))
d = d.with_columns((pl.col("收盘") <= 50).alias("affordable"))
print(f"50元内可买占比: {d['affordable'].mean():.1%}")

# 对比: 全市场 turn_ratio 低端 Top5 的 fwd_5d vs 50元内低端 Top5
for label, dd in [("全市场", d), ("50元内", d.filter(pl.col("affordable")))]:
    top5 = (dd.select(["日期", pl.col("turn_ratio").rank(descending=False).over("日期").alias("rk"), "fwd_5d"])
            .filter(pl.col("rk") <= 5))
    print(f"{label} turn_ratio低端Top5: fwd_5d均值 {top5['fwd_5d'].mean()*100:+.2f}% (n={len(top5)})")

# 高价股(>50)的低换手股表现
hi = d.filter(~pl.col("affordable"))
top5_hi = (hi.select(["日期", pl.col("turn_ratio").rank(descending=False).over("日期").alias("rk"), "fwd_5d"])
           .filter(pl.col("rk") <= 5))
print(f"高价股(>50) turn_ratio低端Top5: fwd_5d均值 {top5_hi['fwd_5d'].mean()*100:+.2f}% (n={len(top5_hi)})")
