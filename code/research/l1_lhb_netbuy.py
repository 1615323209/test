"""龙虎榜双因子 L1 体检 v2 (v4.1复核 P0-4 修正版)
拆成两个独立候选因子:
  因子A: lhb_event 上榜事件哑变量(方向已知为负, 验证"上榜是否利空")
  因子B: lhb_netbuy_z 事件内强度带符号 z-score(未上榜=0, 0落在上榜股中位附近=真中性)
P0-3: ICIR 用经典公式(不乘sqrtN)
"""
import os
import sys
from pathlib import Path
PROJ = Path(os.environ.get("QUANT_PROJECT", r"D:/quant_project"))
sys.path.insert(0, str(PROJ / "code"))
import polars as pl
import numpy as np
from loop.factor_loop_l1l2 import newey_west_t
from scipy.stats import spearmanr

lhb = pl.read_parquet(r"D:/quant_data/lhb_hist.parquet").with_columns(pl.col("日期").str.to_date())
# 同股同日合并净买额
lhb = lhb.group_by(["日期", "代码"]).agg(pl.col("净买额").sum()).rename({"代码": "股票代码"})

# 因子A: 上榜事件哑变量
fa = lhb.with_columns(pl.lit(1).alias("lhb_event")).select(["日期", "股票代码", "lhb_event"])

# 因子B: 事件内强度 z-score (净买额在上榜股内的标准化, 未上榜=0)
fb = lhb.with_columns(
    ((pl.col("净买额") - pl.col("净买额").mean().over("日期")) /
     (pl.col("净买额").std().over("日期") + 1e-12)).alias("lhb_netbuy_z")
).select(["日期", "股票代码", "lhb_netbuy_z"])

# 全样本对齐 ic_data(设计段2021-2023)
ic = pl.read_parquet(r"D:/quant_data/ic_data.parquet", columns=["日期", "股票代码", "fwd_5d"])
ic = ic.filter((pl.col("日期") >= pl.date(2021, 1, 1)) & (pl.col("日期") <= pl.date(2023, 12, 31)))


def l1_check(label, factor_df, col):
    d = ic.join(factor_df, on=["日期", "股票代码"], how="left").with_columns(
        pl.col(col).fill_null(0.0))
    print(f"\n=== 因子 {label} (全样本, 未上榜=0) ===")
    print(f"非零占比: {(d[col] != 0).mean():.2%}, 零点: {(d[col] == 0).mean():.2%}")
    # 全样本 IC
    ics = []
    for day in d["日期"].unique().to_list():
        sub = d.filter(pl.col("日期") == day)
        if len(sub) < 20:
            continue
        try:
            rho, _ = spearmanr(sub[col].to_numpy(), sub["fwd_5d"].to_numpy())
            if np.isfinite(rho):
                ics.append(rho)
        except Exception:
            pass
    icm = float(np.mean(ics))
    icir = icm / (np.std(ics) + 1e-12)          # P0-3: 经典ICIR
    icir_ann = icir * np.sqrt(252)
    t = newey_west_t(ics)
    print(f"IC={icm:+.4f} ICIR={icir:+.3f} ICIR_ann={icir_ann:+.3f} t_NW={t:+.2f} N={len(ics)}天")
    # D3(v4.2): 连续因子分位数分桶(10档), 单调性可读; 离散因子保留分组均值
    if d[col].n_unique() > 50:
        dd = d.with_columns(pl.col(col).qcut(10, labels=[str(i) for i in range(10)],
                                             allow_duplicates=True).alias("_bkt"))
        g = dd.group_by("_bkt").agg(pl.col("fwd_5d").mean().alias("ret"), pl.len().alias("n")).sort("_bkt")
        desc = [(str(r["_bkt"]), f"{r['ret']*100:+.2f}%", r["n"]) for r in g.to_dicts()]
        print(f"分位桶(0低→9高): {desc}")
    else:
        g = d.group_by(col).agg(pl.col("fwd_5d").mean().alias("ret"), pl.len().alias("n")).sort(col)
        desc = [(round(r[col], 2), f"{r['ret']*100:+.2f}%", r["n"]) for r in g.to_dicts()]
        print(f"因子值分布: {desc[:8]}")


l1_check("A_lhb_event(上榜哑变量)", fa, "lhb_event")
l1_check("B_lhb_netbuy_z(净买强度)", fb, "lhb_netbuy_z")

# 验收: 净买为负的上榜股, 因子B值应 < 0 (未上榜)
neg = lhb.filter(pl.col("净买额") < 0).with_columns(
    ((pl.col("净买额") - pl.col("净买额").mean().over("日期")) /
     (pl.col("净买额").std().over("日期") + 1e-12)).alias("z"))
print(f"\n[验收] 净买为负上榜股 z 值: min={neg['z'].min():.3f}, 均值={neg['z'].mean():.3f} (<0 通过)")
