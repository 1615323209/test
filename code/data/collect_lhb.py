#!/usr/bin/env python3
"""龙虎榜历史采集器（东财 datacenter, 多年历史, 替代板块资金的长历史信号）
采集: RPT_DAILYBILLBOARD_DETAILSNEW 全量(2021-01-01 ~ 今日), 按日分页
落盘: D:/quant_data/lhb_hist.parquet (断点续跑)
  日期 | 代码 | 名称 | 收盘价 | 当日涨幅 | 龙虎榜净买额 | 成交总额 | 流通市值 | 换手率
  D1/D2/D5/D10涨幅 | 上榜原因 | 上榜类型
用法: python -m data.collect_lhb [--days N] [--rebuild]
"""
import requests, time, sys
from pathlib import Path
import polars as pl

DATA = Path("D:/quant_data")
OUT = DATA / "lhb_hist.parquet"
UA = "Mozilla/5.0"
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
START = "2021-01-01"


def fetch_day(day, page=1, size=500):
    """拉单日龙虎榜(分页), 返回 (rows, total)"""
    r = requests.get(BASE, params={
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL",
        "filter": f"(TRADE_DATE='{day}')",
        "pageNumber": str(page), "pageSize": str(size),
        "sortColumns": "TRADE_DATE", "sortTypes": "-1",
    }, headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=15)
    j = r.json()
    d = j.get("result") or {}
    rows = d.get("data") or []
    return rows, d.get("count") or 0


def to_row(r):
    def f(x, dflt=None):
        return dflt if x in (None, "-", "--") else x
    return {
        "日期": str(r.get("TRADE_DATE", ""))[:10],
        "代码": str(r.get("SECURITY_CODE", "")),
        "名称": r.get("SECURITY_NAME_ABBR", ""),
        "收盘价": f(r.get("CLOSE_PRICE")),
        "当日涨幅": f(r.get("CHANGE_RATE")),
        "净买额": f(r.get("BILLBOARD_NET_AMT")),
        "成交额": f(r.get("BILLBOARD_DEAL_AMT")),
        "流通市值": f(r.get("FREE_MARKET_CAP")),
        "换手率": f(r.get("TURNOVERRATE")),
        "D1涨幅": f(r.get("D1_CLOSE_ADJCHRATE")),
        "D2涨幅": f(r.get("D2_CLOSE_ADJCHRATE")),
        "D5涨幅": f(r.get("D5_CLOSE_ADJCHRATE")),
        "D10涨幅": f(r.get("D10_CLOSE_ADJCHRATE")),
        "上榜原因": r.get("EXPLANATION", ""),
        "上榜类型": r.get("EXPLAIN", ""),
        "买入额": f(r.get("BILLBOARD_BUY_AMT")),
        "卖出额": f(r.get("BILLBOARD_SELL_AMT")),
    }


def main():
    rebuild = "--rebuild" in sys.argv
    days = None
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    # 交易日清单（从 market_daily 取, 与系统一致）
    md = pl.read_parquet(DATA / "market_daily.parquet", columns=["日期"]).sort("日期")
    all_days = [d.strftime("%Y-%m-%d") for d in md["日期"].to_list() if str(d) >= START]
    if days:
        all_days = all_days[-days:]
    print(f"待采 {len(all_days)} 个交易日 ({all_days[0]} ~ {all_days[-1]})")

    # 断点续跑
    done_days = set()
    if OUT.exists() and not rebuild:
        old = pl.read_parquet(OUT, columns=["日期"])
        done_days = set(old["日期"].unique().to_list())
        todo = [d for d in all_days if d not in done_days]
    else:
        todo = all_days
        if OUT.exists():
            OUT.unlink()
    print(f"已完成 {len(done_days)} 天, 待采 {len(todo)} 天")

    t0 = time.time()
    for i, day in enumerate(todo):
        rows, total = fetch_day(day)
        all_rows = []
        if rows:
            all_rows += [to_row(r) for r in rows]
            # 分页
            pages = (total + 499) // 500
            for pg in range(2, pages + 1):
                rows2, _ = fetch_day(day, page=pg)
                if rows2:
                    all_rows += [to_row(r) for r in rows2]
                time.sleep(0.3)
        if all_rows:
            df = pl.DataFrame(all_rows)
            if OUT.exists():
                old_all = pl.read_parquet(OUT)
                merged = pl.concat([old_all, df]).unique(subset=["日期", "代码"], keep="first")
            else:
                merged = df
            merged.write_parquet(OUT, compression="zstd")
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(todo)} 天, 累计 {sum(1 for _ in open(OUT)) if OUT.exists() else 0} 行? {time.time()-t0:.0f}s")
    # 最终统计
    if OUT.exists():
        fin = pl.read_parquet(OUT)
        print(f"[完成] 龙虎榜: {len(fin)} 行, {fin['日期'].n_unique()} 天 "
              f"({fin['日期'].min()} ~ {fin['日期'].max()}), {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()