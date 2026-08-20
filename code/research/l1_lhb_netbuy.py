"""龙虎榜净买额因子构造 + 全样本L1体检(零值当中性, 文档口径)
因子: lhb_netbuy 当日上榜净买额rank(未上榜=0), 全样本零值当中性
"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl
import numpy as np
from loop.factor_loop_l1l2 import newey_west_t
from scipy.stats import spearmanr

# 1. 龙虎榜 → 日频因子 (净买额 → 当日截面rank)
lhb = pl.read_parquet(r"D:/quant_data/lhb_hist.parquet").with_columns(pl.col("日期").str.to_date())
# 同一股票可能同一天上榜多次(多席位), 合并净买额
lhb = lhb.group_by(["日期", "代码"]).agg(
    pl.col("净买额").sum(),
    pl.col("D1涨幅").first(),
).rename({"代码": "股票代码"})
# 当日截面净买额 rank (未上榜=0)
lhb = lhb.with_columns(
    pl.col("净买额").rank().over("日期").alias("netbuy_rank")
)
print(f"龙虎榜日频: {len(lhb)} 行 (上榜股-天)")

# 2. 全量对齐 ic_data(含fwd_5d), 零值当中性
ic = pl.read_parquet(r"D:/quant_data/ic_data.parquet",
                     columns=["日期", "股票代码", "fwd_5d"])
ic = ic.filter((pl.col("日期") >= pl.date(2021, 1, 1)) & (pl.col("日期") <= pl.date(2023, 12, 31)))
d = ic.join(lhb.select(["日期", "股票代码", "netbuy_rank"]), on=["日期", "股票代码"], how="left")
d = d.with_columns(pl.col("netbuy_rank").fill_null(0.0))  # 未上榜=0 中性
print(f"全样本: {len(d)} 行, 上榜率 {(d['netbuy_rank'] > 0).mean():.2%}")

# 3. 全样本截面 IC (零值当中性)
ics = []
for day in d["日期"].unique().to_list():
    sub = d.filter(pl.col("日期") == day)
    if len(sub) < 20:
        continue
    try:
        rho, _ = spearmanr(sub["netbuy_rank"].to_numpy(), sub["fwd_5d"].to_numpy())
        if np.isfinite(rho):
            ics.append(rho)
    except Exception:
        pass
icm = float(np.mean(ics))
t = newey_west_t(ics)
icir = icm / (np.std(ics) + 1e-12) * np.sqrt(len(ics))
print(f"\n=== 全样本(零值当中性) ===")
print(f"IC={icm:+.4f} ICIR={icir:+.2f} t_NW={t:+.2f} N={len(ics)}天")

# 4. Top-N 毛收益(5日) —— 按 netbuy_rank 高排名
top5 = (d.select(["日期", pl.col("netbuy_rank").rank(descending=True).over("日期").alias("rk"), "fwd_5d"])
        .filter(pl.col("rk") <= 5))
print(f"Top5_5d毛收益: {top5['fwd_5d'].mean()*100:+.3f}% (样本{len(top5)})")

# 5. 对照: 仅上榜子样本的 IC(事件内)
evt = d.filter(pl.col("netbuy_rank") > 0)
ics2 = []
for day in evt["日期"].unique().to_list():
    sub = evt.filter(pl.col("日期") == day)
    if len(sub) < 5:
        continue
    try:
        rho, _ = spearmanr(sub["netbuy_rank"].to_numpy(), sub["fwd_5d"].to_numpy())
        if np.isfinite(rho):
            ics2.append(rho)
    except Exception:
        pass
if len(ics2) > 20:
    print(f"仅上榜子样本 IC: {np.mean(ics2):+.4f} (N={len(ics2)}天) —— 对比全样本看稀释程度")
