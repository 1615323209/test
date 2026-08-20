#!/usr/bin/env python3
"""龙虎榜净买额因子（lhb_netbuy）——事件驱动信号, 有4年分年验证背书

背景: 龙虎榜净买额分组信号(2021-2024分年方向一致)
  - 净买Top20%: fwd_5d +2.213% (远超0.45%成本线)
  - 净买Bottom20%: fwd_5d -2.7% (强负信号, 回避)
  - 全样本线性IC为负(上榜整体利空) → 不做截面score因子, 做事件加分+回避标记

用法: 被 daily_picks 调用, 提供 lhb_netbuy 列 + 上榜标记
数据: D:/quant_data/lhb_hist.parquet (每日采集需挂cron)
"""
from pathlib import Path
import polars as pl

DATA = Path("D:/quant_data")
LHB = DATA / "lhb_hist.parquet"

_cache = None
_cache_date = None


def load_lhb_factor():
    """读龙虎榜 → (日期, 股票代码, lhb_netbuy[当日净买额], lhb_up[是否上榜]) 日频"""
    global _cache, _cache_date
    import datetime as dt
    today = dt.date.today().isoformat()
    if _cache is not None and _cache_date == today:
        return _cache
    if not LHB.exists():
        return None
    lhb = pl.read_parquet(LHB).with_columns(pl.col("日期").str.to_date())
    # 同股同日多席位合并
    lhb = lhb.group_by(["日期", "代码"]).agg(pl.col("净买额").sum()).rename({"代码": "股票代码"})
    lhb = lhb.with_columns([
        pl.col("净买额").alias("lhb_netbuy"),
        pl.lit(1).alias("lhb_up"),
    ])
    _cache = lhb.select(["日期", "股票代码", "lhb_netbuy", "lhb_up"])
    _cache_date = today
    return _cache


def lhb_verdict(netbuy):
    """净买额 → 事件标记: 强买/弱买/回避/未上榜"""
    if netbuy is None or netbuy == 0:
        return "未上榜", ""
    if netbuy > 0:
        return "上榜净买", f"🟢净买{netbuy/1e8:+.1f}亿"
    return "上榜净卖", f"🔴净卖{abs(netbuy)/1e8:.1f}亿"


if __name__ == "__main__":
    f = load_lhb_factor()
    if f is None:
        print("无龙虎榜数据")
    else:
        print(f"龙虎榜因子: {len(f)} 行, 最新 {f['日期'].max()}")
        print(f.head(3))
