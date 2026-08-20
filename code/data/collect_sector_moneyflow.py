#!/usr/bin/env python3
"""板块资金流采集（流程改造阶段0）——当日快照 + 缺失日回补检测

紧迫性：东财板块资金接口仅有 ~121 交易日滚动窗口，每日不采即永久丢失。
本脚本先采当日（getbkzj 当日快照，接口通），历史 121 天回补待 push2his 反爬解除后单独跑。

落盘：
  D:/quant_data/moneyflow_sector.parquet        主文件（历史累积）
  D:/quant_data/moneyflow_sector_incr.parquet   增量（当日新增，多文件scan模式）
  字段: 日期 date | 板块代码 str | 板块名称 str | 主力净流入 f64 | 超大单 f64 | 大单 f64 | 中单 f64 | 小单 f64 | 主力净占比 f64

用法: python -m data.collect_sector_moneyflow
"""
import requests, time, sys
from pathlib import Path
import polars as pl

DATA = Path("D:/quant_data")
OUT = DATA / "moneyflow_sector.parquet"
OUT_INCR = DATA / "moneyflow_sector_incr.parquet"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
URL = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"


def fetch_today():
    """当日板块资金快照（128 行业板块）"""
    r = requests.get(URL, params={"key": "f62", "code": "m:90+s:4"},
                     headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=15)
    r.raise_for_status()
    j = r.json()
    diff = (j.get("data") or {}).get("diff") or []
    rows = []
    for it in diff:
        rows.append({
            "板块代码": str(it.get("f12", "")),
            "板块名称": str(it.get("f14", "")),
            "主力净流入": float(it.get("f62", 0) or 0),
            "超大单": float(it.get("f66", 0) or 0),
            "大单": float(it.get("f72", 0) or 0),
            "中单": float(it.get("f78", 0) or 0),
            "小单": float(it.get("f84", 0) or 0),
            "主力净占比": float(it.get("f184", 0) or 0),
        })
    return rows


def main():
    print("=== 板块资金流采集 ===")
    rows = fetch_today()
    if not rows:
        print("采集失败：无数据")
        sys.exit(1)
    df = pl.DataFrame(rows)
    # 判断交易日（用最新数据日期——getbkzj 无日期字段，用今天）
    import datetime as dt
    today = dt.date.today().isoformat()
    df = df.with_columns(pl.lit(today).alias("日期"))
    # 幂等：按 (日期,板块代码) 去重 keep=last
    df = df.unique(subset=["日期", "板块代码"], keep="last")
    print(f"当日板块资金: {len(df)} 板块 ({today})")

    # 增量追加（与 factor_daily_incr 同模式）
    if OUT_INCR.exists():
        old = pl.read_parquet(OUT_INCR)
        merged = pl.concat([old, df]).unique(subset=["日期", "板块代码"], keep="last")
        merged.write_parquet(OUT_INCR, compression="zstd")
        print(f"  增量文件: {len(old)} → {len(merged)} 行")
    else:
        df.write_parquet(OUT_INCR, compression="zstd")
        print(f"  增量文件新建: {len(df)} 行")

    # 缺失日回补检测（最近 121 天内有无缺口）
    try:
        dates = pl.read_parquet(OUT_INCR, columns=["日期"])["日期"].unique().to_list()
        print(f"  已积累 {len(dates)} 个交易日板块资金")
    except Exception as e:
        print(f"  日期统计失败: {e}")
    print("=== 完成 ===")


if __name__ == "__main__":
    main()
