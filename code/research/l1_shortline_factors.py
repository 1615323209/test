#!/usr/bin/env python3
"""短线三条件因子 L1 体检（阶段6）——放量/站上10日线/近5日涨停
在 ic_data 设计段(2021-2023) 算 IC/ICIR/t_NW + Top5毛收益, 与成本线0.45%比
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

ic = pl.read_parquet(r"D:/quant_data/ic_data.parquet")
ic = ic.filter((pl.col("日期") >= pl.date(2021, 1, 1)) & (pl.col("日期") <= pl.date(2023, 12, 31)))

# 三条件因子定义（在 ic_data 已有列上构造）
ic = ic.with_columns([
    pl.col("vol_ratio").alias("s_放量"),          # 放量: 今日量/5日均量
    (pl.col("收盘") > pl.col("ma_10")).cast(pl.Int8).alias("s_站上10日线"),
    pl.col("limit_up").rolling_sum(5, min_samples=1).over("股票代码").alias("s_近5日涨停"),
])
# 交互项: 放量×站上均线（短线经典组合）
ic = ic.with_columns((pl.col("vol_ratio") * pl.col("s_站上10日线")).alias("s_放量站上"))

FACTORS = ["s_放量", "s_站上10日线", "s_近5日涨停", "s_放量站上"]
for f in FACTORS:
    d = ic.filter(pl.col(f).is_not_null() & pl.col("fwd_5d").is_not_null())
    if len(d) < 10000:
        print(f"{f}: 样本不足 {len(d)}")
        continue
    # 逐日 Spearman IC
    ics = []
    for day in d["日期"].unique().to_list():
        sub = d.filter(pl.col("日期") == day)
        if len(sub) < 20:
            continue
        try:
            rho, _ = spearmanr(sub[f].to_numpy(), sub["fwd_5d"].to_numpy())
            if np.isfinite(rho):
                ics.append(rho)
        except Exception:
            pass
    if len(ics) < 50:
        print(f"{f}: IC天数不足 {len(ics)}")
        continue
    icm = float(np.mean(ics))
    t = newey_west_t(ics)
    icir = icm / (np.std(ics) + 1e-12)  # v4.1复核 P0-3: 经典ICIR, 不乘sqrtN
    icir_ann = icir * np.sqrt(252)
    # Top5 毛收益（按因子值高排名）
    top5 = (d.select(["日期", pl.col(f).rank(descending=True).over("日期").alias("rk"), "fwd_5d"])
            .filter(pl.col("rk") <= 5))
    t5 = float(top5["fwd_5d"].mean()) if len(top5) else None
    clear = "✅清线" if (t5 or 0) >= 0.0045 else ""
    t5s = f"{t5*100:+.2f}%" if t5 is not None else "N/A"
    print(f"{f}: IC={icm:+.4f} ICIR={icir:+.3f} ICIR_ann={icir_ann:+.3f} t_NW={t:+.2f} | Top5_5d={t5s} {clear}")
