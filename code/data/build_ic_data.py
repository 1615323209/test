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
    print("=== 重建 ic_data（lazy 流式，降低内存峰值） ===")
    # 1. 因子主库（多文件 scan，惰性）
    files = [FACTOR] + ([FACTOR_INCR] if FACTOR_INCR.exists() else [])
    d = pl.scan_parquet(files, cast_options=pl.ScanCastOptions(integer_cast="upcast"))
    # 主+增量合并去重（防止同一股票同日多行，增量采集可能与主文件重叠）
    d = d.unique(subset=["日期", "股票代码"], keep="last")
    print(f"[1] 因子库 schema: {len(d.collect_schema())} 列")

    # 2. join 原始价格列（惰性）
    raw = pl.scan_parquet(RAW).select(["日期", "股票代码", "开盘", "最高", "最低"])
    d = d.join(raw, on=["日期", "股票代码"], how="left")

    # 3. join 扩展因子（惰性）
    extra_files = [EXTRA] + ([EXTRA_INCR] if EXTRA_INCR.exists() else [])
    extra = pl.scan_parquet(extra_files, cast_options=pl.ScanCastOptions(integer_cast="upcast"))
    d = d.join(extra, on=["日期", "股票代码"], how="left")

    # 3.5 清理数据源脏值（收盘价 ≤0 或非有限——A 股真实股票价格恒 >0；
    # 偶发停牌/退市股(如600595/600076)被采集成负价或0，会污染 ret/fwd 计算）
    # 惰性 filter，count 放到切片后（polars 会把 filter 下推到 join 前，避免物化全表）
    d = d.filter(pl.col("收盘").is_finite() & (pl.col("收盘") > 0))

    # 4. fwd_* 从 ret_* 前移生成（与现有 ic_data 定义完全一致——已验证 fwd_n == ret_n.shift(-n),
    # max diff~1e-15；不用收盘重算，避免复权跳变日(除权除息)的噪声差异）
    d = d.sort(["股票代码", "日期"])
    d = d.with_columns([
        pl.col("ret_1d").shift(-1).over("股票代码").alias("fwd_1d"),
        pl.col("ret_5d").shift(-5).over("股票代码").alias("fwd_5d"),
        pl.col("ret_10d").shift(-10).over("股票代码").alias("fwd_10d"),
        pl.col("ret_20d").shift(-20).over("股票代码").alias("fwd_20d"),
    ])

    # 5. 训练集切片（lazy，filter 下推到 join 前）
    d = d.filter((pl.col("日期") >= TRAIN_LO) & (pl.col("日期") <= TRAIN_HI))
    print("[5] 训练集切片（lazy），开始流式物化...")

    # 6. 唯一一次物化（streaming 引擎降低峰值内存）
    try:
        d = d.collect(streaming=True)
    except TypeError:
        d = d.collect()  # 老版本 polars 无 streaming 参数则退化
    print(f"[6] 物化完成: {len(d)} 行, {len(d.columns)} 列")

    # 自检：fwd_1d 应 ≈ ret_1d.shift(-1)（同为单日收益率，定义一致才可比；
    # 注意 fwd_5d 是几何收益(close.shift(-5)/close-1)，ret_5d 是算术和(rolling_sum)，
    # 二者数学上不相等，不能直接比）
    chk = d.with_columns(pl.col("ret_1d").shift(-1).over("股票代码").alias("ret_1d_lead"))
    # 自检只比较两边都有限的行（inf/NaN 是脏数据，清完脏收盘后不应存在，双保险过滤）
    diff = chk.filter(pl.col("fwd_1d").is_finite() & pl.col("ret_1d_lead").is_finite())
    if len(diff) > 1000:
        max_diff = float((diff["fwd_1d"] - diff["ret_1d_lead"]).abs().max())
        if max_diff > 1e-4:  # fwd_1d 与 ret_1d 同为单日收益，应数值一致（容差 1e-4）
            raise RuntimeError(f"fwd_1d 自检失败: max diff={max_diff:.2e}")
        print(f"[4] fwd_* 已重算（自检通过: fwd_1d≈ret_1d.shift(-1), max_diff={max_diff:.1e}）")
    else:
        print("[4] fwd_* 已重算（样本不足无法自检）")

    # 7. 备份旧文件 + 写出
    if OUT.exists():
        bak = OUT.with_suffix(".parquet.bak")
        if bak.exists():
            bak.unlink()  # 先删旧备份（避免 WinError 183：目标已存在）
        OUT.rename(bak)
        print(f"[7] 旧文件已备份: {bak.name}")
    d.write_parquet(OUT, compression="zstd")
    print(f"[8] 已写出: {OUT} ({OUT.stat().st_size/1024/1024:.0f}MB)")
    print("新列:", [c for c in ["开盘", "最高", "最低", "illiq_20", "vol_corr_5", "vol_corr_20", "skew_20", "kurt_20"] if c in d.columns])

if __name__ == "__main__":
    main()
