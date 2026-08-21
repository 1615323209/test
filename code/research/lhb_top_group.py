"""龙虎榜净买额 Top-N 事件分组的分年稳定性验证"""
import os
import sys
from pathlib import Path
PROJ = Path(os.environ.get("QUANT_PROJECT", r"D:/quant_project"))
sys.path.insert(0, str(PROJ / "code"))
import polars as pl

lhb = pl.read_parquet(r"D:/quant_data/lhb_hist.parquet").with_columns(pl.col("日期").str.to_date())
lhb = lhb.group_by(["日期", "代码"]).agg(pl.col("净买额").sum()).rename({"代码": "股票代码"})
lhb = lhb.with_columns(pl.col("净买额").rank().over("日期").alias("netbuy_rank"),
                       pl.col("日期").dt.year().alias("年"))

ic = pl.read_parquet(r"D:/quant_data/ic_data.parquet", columns=["日期", "股票代码", "fwd_5d"])
d = ic.join(lhb.select(["日期", "股票代码", "netbuy_rank", "年"]), on=["日期", "股票代码"], how="inner")
d = d.filter(pl.col("年").is_not_null())

print("=== 龙虎榜上榜股票的净买额分组 fwd_5d (分年) ===")
print(f"{'年':>6} {'净买Top20%':>10} {'净买Bottom20%':>12} {'全部上榜':>10} {'样本':>8}")
for y, g in d.group_by("年"):
    if len(g) < 100:
        continue
    q80 = g["netbuy_rank"].quantile(0.8)
    q20 = g["netbuy_rank"].quantile(0.2)
    top = g.filter(pl.col("netbuy_rank") >= q80)["fwd_5d"].mean()
    bot = g.filter(pl.col("netbuy_rank") <= q20)["fwd_5d"].mean()
    allm = g["fwd_5d"].mean()
    print(f"{y[0]:>6} {top*100:>+10.3f}% {bot*100:>+12.3f}% {allm*100:>+10.3f}% {len(g):>8,}")

# Top20% 的 5日正收益占比
d2 = d.with_columns(pl.col("netbuy_rank").quantile(0.8).over("年").alias("q80"))
top = d2.filter(pl.col("netbuy_rank") >= pl.col("q80"))
print(f"\n净买Top20%: 样本{len(top)}, fwd_5d均值{top['fwd_5d'].mean()*100:+.3f}%, 正收益占比{(top['fwd_5d']>0).mean()*100:.1f}%")
