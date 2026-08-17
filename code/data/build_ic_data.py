#!/usr/bin/env python3
"""重建 ic_data.parquet（训练集 2021-2024 切片）

修复（L1 文档缺陷 15/17）：
- 补原始价格列 开盘/最高/最低（A1 日内结构分解前置，数据字典将可描述它们）
- 并入扩展 5 因子 illiq_20/vol_corr_5/vol_corr_20/skew_20/kurt_20（A4，已发表 anomaly）
- fwd_1d/5d/10d/20d 从收盘重新计算（close.shift(-n)/close - 1）

用法: python -m data.build_ic_data
输出: D:/quant_data/ic_data.parquet（覆盖，先备份旧文件）
"""
import polars as pl
from pathlib import Path
from datetime import date

DATA = Path("D:/quant_data")
FACTOR = DATA / "factor_daily.parquet"
FACTOR_INCR = DATA / "factor_daily_incr.parquet"
EXTRA = DATA / "factor_extra_daily.parquet"
EXTRA_INCR = DATA / "factor_extra_incr.parquet"
RAW = DATA / "a_stock_daily_hfq.parquet"
OUT = DATA / "ic_data.parquet"
TRAIN_LO, TRAIN_HI = date(2021, 1, 1), date(2024, 12, 31)

def main():
    print("=== 重建 ic_data ===")
    # 1. 因子主库（多文件 scan）
    files = [FACTOR] + ([FACTOR_INCR] if FACTOR_INCR.exists() else [])
    d = pl.scan_parquet(files, cast_options=pl.ScanCastOptions(integer_cast="upcast")).collect()
    print(f"[1] 因子库: {len(d)} 行, {d['股票代码'].n_unique()} 只, {len(d.columns)} 列")

    # 2. join 原始价格列（开盘/最高/最低）
    raw = pl.scan_parquet(RAW).select(["日期", "股票代码", "开盘", "最高", "最低"]).collect()
    d = d.join(raw, on=["日期", "股票代码"], how="left")
    print(f"[2] 已并入 开盘/最高/最低: {len(d)} 行")

    # 3. join 扩展因子
    extra_files = [EXTRA] + ([EXTRA_INCR] if EXTRA_INCR.exists() else [])
    extra = pl.scan_parquet(extra_files, cast_options=pl.ScanCastOptions(integer_cast="upcast")).collect()
    d = d.join(extra, on=["日期", "股票代码"], how="left")
    print(f"[3] 已并入扩展5因子: {len(d)} 行")

    # 4. fwd_* 从收盘重算（按股票分组 shift）
    d = d.with_columns([
        (pl.col("收盘").shift(-1) / pl.col("收盘") - 1).over("股票代码").alias("fwd_1d"),
        (pl.col("收盘").shift(-5) / pl.col("收盘") - 1).over("股票代码").alias("fwd_5d"),
        (pl.col("收盘").shift(-10) / pl.col("收盘") - 1).over("股票代码").alias("fwd_10d"),
        (pl.col("收盘").shift(-20) / pl.col("收盘") - 1).over("股票代码").alias("fwd_20d"),
    ])
    print("[4] fwd_* 已重算")

    # 5. 训练集切片
    d = d.filter((pl.col("日期") >= TRAIN_LO) & (pl.col("日期") <= TRAIN_HI))
    print(f"[5] 训练集切片: {len(d)} 行, {len(d.columns)} 列")

    # 6. 备份旧文件 + 写出
    if OUT.exists():
        bak = OUT.with_suffix(".parquet.bak")
        OUT.rename(bak)
        print(f"[6] 旧文件已备份: {bak.name}")
    d.write_parquet(OUT, compression="zstd")
    print(f"[7] 已写出: {OUT} ({OUT.stat().st_size/1024/1024:.0f}MB)")
    print("新列:", [c for c in ["开盘", "最高", "最低", "illiq_20", "vol_corr_5", "vol_corr_20", "skew_20", "kurt_20"] if c in d.columns])

if __name__ == "__main__":
    main()
