#!/usr/bin/env python3
"""每日数据更新 v2 — 并行采集 + 因子重算 + 合并
采集：2026-01-01 ~ 今日 hfq 日K（覆盖因子窗口）
因子：重算全部，只保留新交易日合并
"""
import polars as pl
import pandas as pd
import time, sys
from pathlib import Path
from datetime import datetime, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # code/ 根（跨目录 import）
from data.tx_collect import collect
from factors.factors import calc_factors
from factors.extra_factors import calc_extra_factors, EXTRA_FACTOR_COLS

DATA = Path("D:/quant_data")
FACTOR = DATA / "factor_daily.parquet"
MARKET = DATA / "market_daily.parquet"
RAW = DATA / "daily_update_raw.parquet"

print("=== 每日数据更新 v2（并行）===")

# 1. 股票列表 + 最新日期
print("[1/5] 读取现有数据...")
codes = pl.read_parquet(FACTOR, columns=['股票代码'])['股票代码'].unique().to_list()
last_date = max(pl.read_parquet(FACTOR, columns=['日期'])['日期'].to_list())
print(f"  股票数: {len(codes)}, 最新日期: {last_date}")

today = datetime.now().date()
if last_date >= today:
    print("  数据已是最新，无需更新")
    exit(0)

# 2. 并行采集（2026年数据覆盖窗口）
print("[2/5] 并行采集（10线程）...")
t0 = time.time()
raw = collect(codes, year=2026, n_threads=10, out_path=RAW)
if raw is None:
    print("采集失败"); exit(1)
raw['日期'] = pd.to_datetime(raw['日期']).dt.date
print(f"  采集 {time.time()-t0:.0f}s, {len(raw)} 行")

# 3. 因子重算（分块，避免 OOM）
print("[3/5] 因子重算（分块）...")
raw_pl = pl.from_pandas(raw).sort(['股票代码','日期'])
all_new = []
all_new_extra = []
BATCH = 800
code_list = raw_pl['股票代码'].unique().to_list()
for i in range(0, len(code_list), BATCH):
    batch_codes = code_list[i:i+BATCH]
    b = raw_pl.filter(pl.col('股票代码').is_in(batch_codes))
    fb = calc_factors(b)
    nb = fb.filter(pl.col('日期') > last_date)
    if len(nb) > 0:
        all_new.append(nb)
    # 扩展因子（新因子库，日期与主库同步）
    fb_extra = calc_extra_factors(b)[['日期', '股票代码'] + EXTRA_FACTOR_COLS]
    nb_extra = fb_extra.filter(pl.col('日期') > last_date)
    if len(nb_extra) > 0:
        all_new_extra.append(nb_extra)
    del b, fb, nb, fb_extra, nb_extra
    import gc; gc.collect()
    if (i//BATCH+1) % 2 == 0:
        print(f"  batch {i//BATCH+1}/{(len(code_list)+BATCH-1)//BATCH} 完成")
new_f = pl.concat(all_new) if all_new else None
new_f_extra = pl.concat(all_new_extra) if all_new_extra else None
if new_f is None or len(new_f) == 0:
    print("  无新交易日，结束"); exit(0)
print(f"  新因子数据: {len(new_f)} 行")

# 4. 合并（只保留新交易日）
print("[4/5] 合并 factor_daily...")
new_dates = sorted(new_f['日期'].unique().to_list())
print(f"  新交易日: {new_dates}")

# 校验列（保序）
old_cols = pl.read_parquet(FACTOR, columns=None).columns
old_set = set(old_cols)
new_cols = set(new_f.columns)
missing = old_set - new_cols
extra = new_cols - old_set
if missing:
    print(f"  错误: 缺列 {missing}"); exit(1)
if extra:
    print(f"  警告: 多列 {extra}（忽略）")
new_f = new_f.select(old_cols)

# 4. 合并：新数据写增量文件（不动大文件，读取时多文件 scan）
print("[4/5] 写入增量文件...")
INCR = DATA / "factor_daily_incr.parquet"
new_f = new_f.select(old_cols)
if INCR.exists():
    old_incr = pl.read_parquet(INCR)
    merged_incr = pl.concat([old_incr, new_f]).sort(['股票代码','日期'])
    merged_incr.write_parquet(INCR, compression='zstd')
    print(f"  factor_daily_incr: {len(old_incr):,} → {len(merged_incr):,} 行")
else:
    new_f.write_parquet(INCR, compression='zstd')
    print(f"  factor_daily_incr: 新建 {len(new_f):,} 行")
print(f"  注: factor_daily(3.3GB) 未改动，读取时 scan 两个文件")

# 4b. 扩展因子增量（factor_extra_incr.parquet）
EXTRA_INCR = DATA / "factor_extra_incr.parquet"
if new_f_extra is not None and len(new_f_extra) > 0:
    new_f_extra = new_f_extra.select(['日期', '股票代码'] + EXTRA_FACTOR_COLS)
    if EXTRA_INCR.exists():
        old_extra = pl.read_parquet(EXTRA_INCR)
        merged_extra = pl.concat([old_extra, new_f_extra]).sort(['股票代码','日期'])
        merged_extra.write_parquet(EXTRA_INCR, compression='zstd')
        print(f"  factor_extra_incr: {len(old_extra):,} → {len(merged_extra):,} 行")
    else:
        new_f_extra.write_parquet(EXTRA_INCR, compression='zstd')
        print(f"  factor_extra_incr: 新建 {len(new_f_extra):,} 行")
else:
    print("  无新扩展因子行")

# 5. 更新 market_daily
print("[5/5] 更新 market_daily...")
market = pl.read_parquet(MARKET)
for d in new_dates:
    fd = new_f.filter(pl.col('日期') == d)
    n_total = len(fd)
    n_up = (fd['ret_1d'] > 0).sum()
    n_limit_up = (fd['limit_up'] == 1).sum()
    n_limit_down = (fd['limit_down'] == 1).sum()
    market_row = pl.DataFrame({
        '日期': [d], '上涨家数': [n_up], '下跌家数': [n_total - n_up],
        '股票数': [n_total], '涨跌家数差': [2*n_up - n_total],
        '上涨占比': [n_up/n_total if n_total else 0],
        '涨停家数': [n_limit_up], '跌停家数': [n_limit_down],
    })
    market = pl.concat([market, market_row], how='diagonal_relaxed')
market = market.sort('日期')
market.write_parquet(MARKET)
print(f"  market_daily: {len(market)} 天")
print("\n=== 更新完成 ===")
