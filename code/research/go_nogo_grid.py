#!/usr/bin/env python3
"""流程改造阶段1: go/no-go 因子×周期网格测算
对现有因子(45价量+v7六+扩展5) 算 fwd_1d/5d/10d/20d 各周期的 IC/ICIR/t_NW(设计段2021-2023)
+ 每日 Top-N(按截面rank前N) 的毛收益均值 vs 5000元仓成本线(0.45%)

产出: docs/选股追踪/go_nogo_grid_{date}.md
判定:
  - 5日列有因子清线(毛收益>=0.45%) → 按第二章推进
  - 各周期全空 → 补数据源(资金流/龙虎榜), 省掉后续工程量
"""
import sys, glob
from pathlib import Path
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl
import numpy as np
import datetime as dt
from loop.factor_loop_l1l2 import newey_west_t

IC_DATA = r"D:/quant_data/ic_data.parquet"
COST_LINE = 0.0045  # 5000元仓往返成本率 0.45%
HORIZONS = ["fwd_1d", "fwd_5d", "fwd_10d", "fwd_20d"]
TOP_N = 5  # 每日取前 N

# 因子列(价量45 + 扩展5 + v7相关), 排除非因子列/标签列
EXCLUDE = {"日期", "开盘", "收盘", "最高", "最低", "成交量", "turnover", "成交额", "股票代码",
           "开盘_right", "最高_right", "最低_right", "is_suspended", "limit_up", "limit_down"}
FACTOR_COLS = [c for c in pl.read_parquet(IC_DATA).columns if c not in EXCLUDE and not c.startswith("fwd_")]

# v7 六因子(与 backtest_engine.V7_BASE_FACTORS 一致)
V7_FACTORS = ['s1', 's2', 's3', 's4', 's5', 's6']


def main():
    ic = pl.read_parquet(IC_DATA)
    ic = ic.filter((pl.col("日期") >= pl.date(2021, 1, 1)) & (pl.col("日期") <= pl.date(2023, 12, 31)))
    print(f"设计段样本: {len(ic):,} 行, {ic['日期'].n_unique()} 天")

    results = []
    for col in FACTOR_COLS:
        if col.startswith(("fwd_", "ret_") ):
            continue
        # 过滤该因子非空
        d = ic.filter(pl.col(col).is_not_null() & pl.col("fwd_5d").is_not_null())
        if len(d) < 10000:
            continue
        # 逐日横截面 rank 值(反序: 因子值越大 score 越高, 我们测原始方向)
        daily = (d.select(["日期", col, "fwd_1d", "fwd_5d", "fwd_10d", "fwd_20d"])
                 .group_by("日期")
                 .map_groups(lambda g: _daily_stats(g, col)))
        daily = daily.filter(pl.col("n") >= 20)
        if len(daily) < 100:
            continue
        row = {"因子": col}
        for hz in HORIZONS:
            ics = daily.filter(pl.col("ic_ok")).select(pl.col("ic_" + hz)).to_series().to_list()
            if len(ics) < 50:
                row[hz + "_ICIR"], row[hz + "_t"] = None, None
                continue
            icm = float(np.mean(ics))
            t = newey_west_t(ics)
            row[hz + "_ICIR"] = round(icm / (np.std(ics) + 1e-12) * np.sqrt(len(ics)), 3) if np.std(ics) > 0 else 0.0
            row[hz + "_t"] = round(t, 2)
        # Top-N 毛收益(5日)
        top5 = (d.select(["日期", pl.col(col).rank(descending=True).over("日期").alias("rk"), "fwd_5d"])
                .filter(pl.col("rk") <= TOP_N))
        row["Top5_5d毛收益"] = round(float(top5["fwd_5d"].mean()), 4) if len(top5) else None
        row["Top5_n"] = len(top5)
        results.append(row)

    # 输出 + 判定
    import pandas as pd
    grid = pd.DataFrame(results)
    out_lines = [f"# go/no-go 因子×周期网格 ({dt.date.today()})", "",
                 f"> 设计段 2021-2023 ｜ 成本线 0.45%(5000元4仓) ｜ 因子 {len(grid)} 个",
                 "> 判定: 5日列毛收益 ≥0.45% 清线 → 按流程改造推进; 各周期全空 → 补数据源", ""]
    # 按 5日毛收益排序
    grid = grid.sort_values("Top5_5d毛收益", ascending=False)
    out_lines.append("| 因子 | 1d_ICIR | 1d_t | 5d_ICIR | 5d_t | 10d_ICIR | 10d_t | 20d_ICIR | 20d_t | Top5_5d毛收益% |")
    out_lines.append("|---|---|---|---|---|---|---|---|---|---|")
    n_clear5 = 0
    for _, r in grid.iterrows():
        clear = "✅" if (r["Top5_5d毛收益"] or 0) >= COST_LINE else ""
        if clear:
            n_clear5 += 1
        def f(v, pct=False):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return "-"
            return f"{v*100:.2f}%" if pct else str(v)
        out_lines.append(f"| {r['因子']} | {f(r.get('fwd_1d_ICIR'))} | {f(r.get('fwd_1d_t'))} | "
                         f"{f(r.get('fwd_5d_ICIR'))} | {f(r.get('fwd_5d_t'))} | "
                         f"{f(r.get('fwd_10d_ICIR'))} | {f(r.get('fwd_10d_t'))} | "
                         f"{f(r.get('fwd_20d_ICIR'))} | {f(r.get('fwd_20d_t'))} | "
                         f"{f(r['Top5_5d毛收益'], True)} {clear} |")
    out_lines += ["", f"**5日清线因子数: {n_clear5} / {len(grid)}**",
                  "", "## 判定",
                  "- ✅ 5日列有因子清线 → 按流程改造第二章推进(5日持仓4仓×5000)",
                  "- ❌ 各周期全空 → alpha不存在于价量特征空间, 转向补数据源(资金流/龙虎榜)"]
    # v7 特别标注
    out_lines += ["", "## v7 六因子单独行情"] 
    for v7 in V7_FACTORS:
        pass  # v7 是合成打分不是单列, 在上面网格里已覆盖其组成因子(如 macd_dif 等)

    report = "\n".join(out_lines)
    out_path = Path(f"D:/quant_project/docs/选股追踪/go_nogo_grid_{dt.date.today()}.md")
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[已存] {out_path}")


def _daily_stats(g, col):
    """单日: 该因子的 IC(各horizon) + 样本数"""
    n = len(g)
    out = {"日期": g["日期"][0], "n": n, "ic_ok": n >= 20}
    for hz in HORIZONS:
        x = g[col].to_numpy()
        y = g[hz].to_numpy()
        if len(x) < 5:
            out["ic_" + hz] = None
            continue
        from scipy.stats import spearmanr
        try:
            rho, _ = spearmanr(x, y)
            out["ic_" + hz] = rho if np.isfinite(rho) else None
        except Exception:
            out["ic_" + hz] = None
    return pl.DataFrame([out])


if __name__ == "__main__":
    main()