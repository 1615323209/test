"""龙虎榜因子 L1 体检（设计段2021-2023, fwd_5d）——上榜/净买/机构/D5表现
龙虎榜是非价量长历史信号(替代板块资金), 检验它在5日周期的预测力
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

lhb = pl.read_parquet(r"D:/quant_data/lhb_hist.parquet")
lhb = lhb.with_columns(pl.col("日期").str.to_date())
print(f"龙虎榜: {len(lhb)} 行, D5涨幅非空率 {lhb['D5涨幅'].is_not_null().mean():.0%}")

# 对齐 ic_data(含fwd_5d) 设计段
ic = pl.read_parquet(r"D:/quant_data/ic_data.parquet",
                     columns=["日期", "股票代码", "fwd_5d", "ret_1d"])
ic = ic.filter((pl.col("日期") >= pl.date(2021, 1, 1)) & (pl.col("日期") <= pl.date(2023, 12, 31)))

# 龙虎榜事件: 以"上榜日"为事件日, 看上榜后 fwd_5d 表现(事件驱动)
evt = lhb.filter(pl.col("日期") <= pl.date(2023, 12, 31)).select(
    ["日期", "代码", "净买额", "当日涨幅", "D1涨幅", "D5涨幅", "上榜类型"])
evt = evt.rename({"代码": "股票代码"})

# 与 ic_data 对齐(上榜日当天行情 + fwd_5d)
j = evt.join(ic, on=["日期", "股票代码"], how="inner")
print(f"对齐样本: {len(j)} 行 (上榜且有fwd_5d)")

# 1. 龙虎榜上榜事件的平均 fwd_5d vs 全市场基准
base5 = ic["fwd_5d"].drop_nulls()
print(f"\n=== 龙虎榜上榜后 5日表现 ===")
print(f"全市场 fwd_5d 均值: {base5.mean()*100:+.3f}%")
print(f"上榜股票 fwd_5d 均值: {j['fwd_5d'].mean()*100:+.3f}%  (样本{len(j)})")
print(f"上榜超额: {(j['fwd_5d'].mean()-base5.mean())*100:+.3f}%")

# 2. 净买额分组的 fwd_5d (净买>0 vs <0)
pos = j.filter(pl.col("净买额") > 0)
neg = j.filter(pl.col("净买额") < 0)
print(f"\n净买>0: {len(pos)} 样本, fwd_5d={pos['fwd_5d'].mean()*100:+.3f}%")
print(f"净买<0: {len(neg)} 样本, fwd_5d={neg['fwd_5d'].mean()*100:+.3f}%")

# 3. 机构上榜 vs 普通 (按上榜类型关键词)
inst = j.filter(pl.col("上榜类型").str.contains("机构"))
print(f"\n机构上榜: {len(inst)} 样本, fwd_5d={inst['fwd_5d'].mean()*100:+.3f}%")

# 4. 用 D1涨幅做因子(上榜后1日已实现) 无法预测; 用"净买额"当日截面 rank 对 fwd_5d 的 IC
# 逐日: 当日上榜股票的 净买额rank vs fwd_5d (事件内横截面)
ics = []
for day in j["日期"].unique().to_list():
    sub = j.filter(pl.col("日期") == day)
    if len(sub) < 5:
        continue
    try:
        rho, _ = spearmanr(sub["净买额"].to_numpy(), sub["fwd_5d"].to_numpy())
        if np.isfinite(rho):
            ics.append(rho)
    except Exception:
        pass
if len(ics) > 20:
    print(f"\n=== 净买额 vs fwd_5d 事件内截面 IC ===")
    print(f"IC均值: {np.mean(ics):+.4f}, ICIR: {np.mean(ics)/(np.std(ics)+1e-12)*np.sqrt(len(ics)):+.2f}, N={len(ics)}天")
