#!/usr/bin/env python3
"""流程改造阶段1: go/no-go 因子×周期网格测算 v2 (v4.1复核 P0-2/P0-3 修正版)

修正点:
- P0-2a: 双端都报(Top5_高端/Top5_低端), 拟交易端=IC符号决定, 清线只认拟交易端
- P0-2b: 可交易性过滤(is_suspended==0, limit_up==0, limit_down==0) 与回测候选池一致
- P0-2c: 离散/二值因子(unique<20)改用分组均值, 不参与Top-N清线判定
- P0-2d: 设计段2021-2023 + 留出段2024 各跑一遍, 两段同方向且都清线才标✅
- P0-3: ICIR 为经典 mean/std(不乘sqrtN), 输出列 IC/ICIR/ICIR_ann/t_NW
产出: docs/选股追踪/go_nogo_grid_v2_{date}.md
"""
import os
import sys, glob
from pathlib import Path
PROJ = Path(os.environ.get("QUANT_PROJECT", r"D:/quant_project"))
sys.path.insert(0, str(PROJ / "code"))
import polars as pl
import numpy as np
import datetime as dt
from loop.factor_loop_l1l2 import newey_west_t
from scipy.stats import spearmanr

IC_DATA = r"D:/quant_data/ic_data.parquet"
COST_LINE = 0.0045  # 5000元仓往返成本率 0.45%
HORIZONS = ["fwd_1d", "fwd_5d", "fwd_10d", "fwd_20d"]
TOP_N = 5
DESIGN = (pl.date(2021, 1, 1), pl.date(2023, 12, 31))
HOLDOUT = (pl.date(2024, 1, 1), pl.date(2024, 12, 31))

EXCLUDE = {"日期", "开盘", "收盘", "最高", "最低", "成交量", "turnover", "成交额", "股票代码",
           "开盘_right", "最高_right", "最低_right", "is_suspended", "limit_up", "limit_down"}


def tradeable(d):
    """可交易性过滤（与回测候选池一致）"""
    return d.filter((pl.col("is_suspended") == 0) & (pl.col("limit_up") == 0) & (pl.col("limit_down") == 0))


def daily_ic(sub, col, label):
    """单日 Spearman IC"""
    if len(sub) < 20:
        return None
    try:
        rho, _ = spearmanr(sub[col].to_numpy(), sub[label].to_numpy())
        return rho if np.isfinite(rho) else None
    except Exception:
        return None


def factor_stats(d, col, label, d_raw=None):
    """单段: 逐日IC → IC/ICIR/ICIR_ann/t_NW + 双端Top-N毛收益
    d_raw: 未过滤样本(含涨停/停牌), 用于D2过滤前后对比
    返回 dict 或 None(样本不足)"""
    dd = d.filter(pl.col(col).is_not_null() & pl.col(label).is_not_null())
    if len(dd) < 10000:
        return None
    # 逐日 IC
    ics = []
    for day in dd["日期"].unique().to_list():
        sub = dd.filter(pl.col("日期") == day)
        rho = daily_ic(sub, col, label)
        if rho is not None:
            ics.append(rho)
    if len(ics) < 50:
        return None
    icm = float(np.mean(ics))
    icir = icm / (np.std(ics) + 1e-12)
    t = newey_west_t(ics)
    # 双端 Top-N
    rk_desc = pl.col(col).rank(descending=True).over("日期")
    rk_asc = pl.col(col).rank(descending=False).over("日期")
    hi = dd.select(["日期", rk_desc.alias("rk"), label]).filter(pl.col("rk") <= TOP_N)
    lo = dd.select(["日期", rk_asc.alias("rk"), label]).filter(pl.col("rk") <= TOP_N)
    hi_r = float(hi[label].mean()) if len(hi) else None
    lo_r = float(lo[label].mean()) if len(lo) else None
    # D2(v4.2): 涨停占比在未过滤样本上算 + 过滤前后对比
    lu_ratio = 0.0
    raw_hi_r = None
    if d_raw is not None:
        rdd = d_raw.filter(pl.col(col).is_not_null() & pl.col(label).is_not_null())
        if len(rdd) >= 10000:
            lu_hi = rdd.select(["日期", rk_desc.alias("rk"), "limit_up"]).filter(pl.col("rk") <= TOP_N)
            lu_ratio = float(lu_hi["limit_up"].sum()) / len(lu_hi) if len(lu_hi) else 0
            raw_hi = rdd.select(["日期", rk_desc.alias("rk"), label]).filter(pl.col("rk") <= TOP_N)
            raw_hi_r = float(raw_hi[label].mean()) if len(raw_hi) else None
    return {"ic": icm, "icir": icir, "icir_ann": icir * np.sqrt(252), "t": t,
            "hi": hi_r, "lo": lo_r, "lu_ratio": lu_ratio, "raw_hi": raw_hi_r, "n_ic": len(ics)}


def discrete_group(d, col, label):
    """离散/二值因子: 分组均值"""
    dd = d.filter(pl.col(col).is_not_null() & pl.col(label).is_not_null())
    if len(dd) < 5000:
        return None
    grp = dd.group_by(col).agg(pl.col(label).mean().alias("ret"), pl.len().alias("n")).sort(col)
    return {r[col]: (round(r["ret"], 5), r["n"]) for r in grp.to_dicts()}


def main():
    # 只读需要的列(降低内存峰值, 避免OOM)
    need = ["日期", "股票代码", "fwd_5d", "is_suspended", "limit_up", "limit_down"]
    ic_all = pl.read_parquet(IC_DATA, columns=need)
    FACTOR_COLS = [c for c in pl.read_parquet(IC_DATA).columns
                   if c not in EXCLUDE and not c.startswith(("fwd_", "ret_"))]

    ic_design = tradeable(ic_all.filter((pl.col("日期") >= DESIGN[0]) & (pl.col("日期") <= DESIGN[1])))
    ic_hold = tradeable(ic_all.filter((pl.col("日期") >= HOLDOUT[0]) & (pl.col("日期") <= HOLDOUT[1])))
    # D2(v4.2): 未过滤样本(含涨停/停牌)用于过滤前后对比
    ic_raw_design = ic_all.filter((pl.col("日期") >= DESIGN[0]) & (pl.col("日期") <= DESIGN[1]))
    ic_raw_hold = ic_all.filter((pl.col("日期") >= HOLDOUT[0]) & (pl.col("日期") <= HOLDOUT[1]))
    print(f"设计段: {len(ic_design):,} 行 / {ic_design['日期'].n_unique()} 天 (未过滤 {len(ic_raw_design):,})")
    print(f"留出段: {len(ic_hold):,} 行 / {ic_hold['日期'].n_unique()} 天 (未过滤 {len(ic_raw_hold):,})")

    out_lines = [f"# go/no-go 因子×周期网格 v2 ({dt.date.today()})",
                 "",
                 "> v4.1复核修正: 双端判定/可交易性过滤/离散分组均值/留出段2024/真ICIR(P0-2,P0-3)",
                 f"> 成本线 {COST_LINE*100:.2f}% ｜ 拟交易端=IC符号决定 ｜ 两段同方向且都清线才标 ✅",
                 ""]
    # 表头
    hdr = (f"| 因子 | IC | ICIR | ICIR_ann | t_NW | 设计段_高端% | 设计段_低端% | 拟交易端 | "
           f"设计段清线% | 留出段清线% | 双段✅ | 涨停占比(过滤前) | 过滤前毛收益% |")
    out_lines += [hdr, "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    n_clear = 0
    for col in FACTOR_COLS:
        # 按需读取该因子列(避免全量载入所有因子列, 降内存峰值)
        fcol = pl.read_parquet(IC_DATA, columns=["日期", "股票代码", col])
        d_design = ic_design.join(fcol, on=["日期", "股票代码"], how="left")
        d_hold = ic_hold.join(fcol, on=["日期", "股票代码"], how="left")
        d_raw_design = ic_raw_design.join(fcol, on=["日期", "股票代码"], how="left")
        # 离散因子: 分组均值, 不参与Top-N清线
        if d_design[col].n_unique() < 20:
            gd = discrete_group(d_design, col, "fwd_5d")
            gh = discrete_group(d_hold, col, "fwd_5d") if d_hold[col].n_unique() < 20 else None
            gd_s = ";".join(f"{k}:{v[0]*100:.2f}%(n={v[1]})" for k, v in (gd or {}).items()) or "样本不足"
            out_lines.append(f"| {col} | - | - | - | - | - | - | 离散(分组) | {gd_s[:60]} | "
                             f"{(';'.join(f'{k}:{v[0]*100:.2f}%' for k,v in (gh or {}).items()))[:60] if gh else '-'} | - | - | - |")
            continue
        # 连续因子: 双端
        sd = factor_stats(d_design, col, "fwd_5d", d_raw=d_raw_design)
        if sd is None:
            continue
        sh = factor_stats(d_hold, col, "fwd_5d")
        # 拟交易端
        side = "高端" if sd["ic"] >= 0 else "低端"
        ret_d = sd["hi"] if side == "高端" else sd["lo"]
        ret_h = (sh["hi"] if side == "高端" else sh["lo"]) if sh else None
        clear_d = (ret_d or 0) >= COST_LINE
        clear_h = (ret_h or 0) >= COST_LINE if ret_h is not None else False
        both = "✅" if (clear_d and clear_h) else ""
        if clear_d and clear_h:
            n_clear += 1
        lu = sd["lu_ratio"]
        raw_hi = sd.get("raw_hi")
        out_lines.append(
            f"| {col} | {sd['ic']:+.4f} | {sd['icir']:+.3f} | {sd['icir_ann']:+.3f} | {sd['t']:+.2f} | "
            f"{sd['hi']*100 if sd['hi'] is not None else 0:+.2f} | {sd['lo']*100 if sd['lo'] is not None else 0:+.2f} | "
            f"{side} | {ret_d*100 if ret_d is not None else 0:+.2f} | "
            f"{ret_h*100 if ret_h is not None else 0:+.2f} | {both} | "
            f"{lu*100:.0f}% | {raw_hi*100 if raw_hi is not None else 0:+.2f} |")

    out_lines += ["", f"**双段清线因子数: {n_clear}**", "",
                  "## 判定",
                  "- ✅ 拟交易端+留出段双清线 → alpha 存在且跨段稳定, 可作策略起点",
                  "- ❌ 无双段清线 → 价量特征空间在可交易+成本口径下无可交易 alpha, 转向补数据源"]
    report = "\n".join(out_lines)
    out_path = Path(f"D:/quant_project/docs/选股追踪/go_nogo_grid_v2_{dt.date.today()}.md")
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[已存] {out_path}")


if __name__ == "__main__":
    main()
