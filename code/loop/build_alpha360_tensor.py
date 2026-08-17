#!/usr/bin/env python3
"""方向二：Alpha360 张量构建
从 a_stock_daily_hfq.parquet 构造 30天×8特征 张量 + fwd_5d 标签（PIT 安全：只用过去30天）

特征(8): close/open/high/low/volume/amount/turnover/ret_1d（按股票 z-score 标准化）
标签: fwd_5d = 未来5日收盘收益（%）

用法:
  python -m loop.build_alpha360_tensor --sample-every 5   # 降采样快速验证
  python -m loop.build_alpha360_tensor                    # 全量（约 470万样本，~4.5GB）

输出: D:/quant_data/alpha360/{train,val}_x.npy / _y.npy / _meta.json
"""
import polars as pl
import numpy as np
import json, argparse, time
from pathlib import Path
from datetime import date
from numpy.lib.stride_tricks import sliding_window_view

DATA = Path("D:/quant_data")
OUT = DATA / "alpha360"
WINDOW = 30
FCOLS = ['收盘', '开盘', '最高', '最低', '成交量', '成交额', 'turnover']
TRAIN_LO, TRAIN_HI = date(2021, 1, 1), date(2024, 12, 31)
DESIGN_LO, DESIGN_HI = date(2021, 1, 1), date(2023, 12, 31)
HOLDOUT_LO, HOLDOUT_HI = date(2024, 1, 1), date(2024, 12, 31)
VAL_LO, VAL_HI = date(2025, 1, 1), date(2026, 12, 31)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-every", type=int, default=1, help="隔 N 天采样一个样本（降采样）")
    ap.add_argument("--pit-stats", action="store_true",
                    help="PIT 严格版：z-score 只用训练期(<=2024-12-31)统计，不用全样本（防特征泄漏）")
    args = ap.parse_args()
    S = args.sample_every
    OUT.mkdir(exist_ok=True)

    t0 = time.time()
    # 1. 读数据（2020-12 起，为 2021 提供 30 天窗口）
    print("[1/4] 读取原始K线...")
    df = (pl.scan_parquet(DATA / "a_stock_daily_hfq.parquet")
          .filter(pl.col('日期') >= date(2020, 12, 1))
          .sort(['股票代码', '日期'])
          .collect())
    print(f"  {len(df)} 行, {df['股票代码'].n_unique()} 只, {time.time()-t0:.0f}s")

    # 2. 特征工程（按股票）
    print("[2/4] 特征工程（z-score + ret_1d + fwd_5d）...")
    if args.pit_stats:
        # PIT 严格版：z-score 用训练期统计（只到 2024-12-31），防止验证期信息泄漏进特征
        print("  PIT 严格版：z-score 用训练期(<=2024-12-31)统计")
        stat_exprs = []
        for c in FCOLS:
            stat_exprs.append(pl.col(c).mean().alias(f'{c}_m'))
            stat_exprs.append(pl.col(c).std().alias(f'{c}_s'))
        stat = (df.filter(pl.col('日期') <= TRAIN_HI)
                .group_by('股票代码')
                .agg(stat_exprs))
        d = df.join(stat, on='股票代码', how='left')
        d = d.with_columns([
            pl.col(c).sub(pl.col(f'{c}_m')).truediv(pl.col(f'{c}_s').add(1e-9)).alias(f"z_{c}")
            for c in FCOLS
        ])
    else:
        d = df.with_columns([
            pl.col(c).sub(pl.col(c).mean().over('股票代码')).truediv(
                pl.col(c).std().over('股票代码').add(1e-9)).alias(f"z_{c}")
            for c in FCOLS
        ])
    d = d.with_columns([
        (pl.col('收盘') / pl.col('收盘').shift(1) - 1).over('股票代码').alias('ret_1d'),
        (pl.col('收盘').shift(-5) / pl.col('收盘') - 1).over('股票代码').alias('fwd_5d'),
    ])
    feats = [f"z_{c}" for c in FCOLS] + ['ret_1d']
    print(f"  特征: {feats}")

    # 3. 按股票滑窗
    print("[3/4] 滑窗构建张量...")
    # 三段切分（train_alpha360 泄漏修复：设计段训练/2024内层验证/2025-2026真验证）
    design_x, design_y, design_meta = [], [], []
    holdout_x, holdout_y, holdout_meta = [], [], []
    valid_x, valid_y, valid_meta = [], [], []
    n_stocks = d['股票代码'].n_unique()

    for i, (code, g) in enumerate(d.group_by('股票代码', maintain_order=True)):
        g = g.sort('日期')
        dates = g['日期'].to_list()
        X = g.select(feats).to_numpy().astype(np.float32)  # [T, F]
        y = g['fwd_5d'].to_numpy().astype(np.float32)      # [T]
        if len(X) < WINDOW + 5:
            continue
        # 滑窗 [T-W+1, W, F]
        xw = sliding_window_view(X, (WINDOW, X.shape[1]))[:, 0, :, :]
        yw = y[WINDOW - 1:]  # 对齐：第 t 行窗口的标签 = fwd_5d[t]
        dw = dates[WINDOW - 1:]
        # 切分（过滤 fwd_5d 缺失的末尾样本；x 的 NaN 中性化填充 0）
        for j in range(0, len(xw), S):
            dt = dw[j]
            if not np.isfinite(yw[j]):
                continue
            xx = np.nan_to_num(xw[j], nan=0.0).astype(np.float32)
            if DESIGN_LO <= dt <= DESIGN_HI:
                design_x.append(xx); design_y.append(yw[j]); design_meta.append((dt.isoformat(), code))
            elif HOLDOUT_LO <= dt <= HOLDOUT_HI:
                holdout_x.append(xx); holdout_y.append(yw[j]); holdout_meta.append((dt.isoformat(), code))
            elif VAL_LO <= dt <= VAL_HI:
                valid_x.append(xx); valid_y.append(yw[j]); valid_meta.append((dt.isoformat(), code))
        if (i + 1) % 1000 == 0:
            print(f"  {i+1}/{n_stocks} 只完成, design={len(design_y)}, holdout={len(holdout_y)}, valid={len(valid_y)}, {time.time()-t0:.0f}s")

    # 4. 写盘
    print("[4/4] 写盘...")
    def save(prefix, x, y, meta):
        x = np.stack(x) if x else np.zeros((0, WINDOW, len(feats)), np.float32)
        y = np.array(y, np.float32) if y else np.zeros(0, np.float32)
        np.save(OUT / f"{prefix}_x.npy", x)
        np.save(OUT / f"{prefix}_y.npy", y)
        (OUT / f"{prefix}_meta.json").write_text(
            json.dumps({"dates": [m[0] for m in meta], "codes": [m[1] for m in meta],
                        "features": feats, "window": WINDOW}, ensure_ascii=False), encoding="utf-8")
        print(f"  {prefix}: x{x.shape} y{y.shape} {len(meta)}样本")

    save("design", design_x, design_y, design_meta)
    save("holdout", holdout_x, holdout_y, holdout_meta)
    save("valid", valid_x, valid_y, valid_meta)
    print(f"=== 完成 {time.time()-t0:.0f}s ===")

if __name__ == "__main__":
    main()
