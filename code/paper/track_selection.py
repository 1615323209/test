#!/usr/bin/env python3
"""选股追踪（两个都要：验证因子 + 盯盘提醒）
- 验证因子: 每次 daily_picks 选出的股票 + 后续 1/3/5/10 日涨幅归档 selection_log.csv,
  定期统计命中率(涨>0占比) —— 验证因子选股到底灵不灵
- 盯盘提醒: 对选股结果后续几个交易日跟踪, 涨幅超阈值(如+4%)或跌破(如-3%)输出提醒
用法:
  python -m paper.track_selection              # 回填历史后续涨幅 + 统计命中率
  python -m paper.track_selection --record     # 记录今天选股(供 cron 选股后调用)
数据:
  选股记录: D:/quant_data/daily_picks/selection_log.csv
  行情: factor_daily(+incr) 提供收盘价
"""
import sys, os
from pathlib import Path
import pandas as pd
import polars as pl
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA = Path("D:/quant_data")
PICKS_DIR = DATA / "daily_picks"
LOG = PICKS_DIR / "selection_log.csv"
FACTOR = DATA / "factor_daily.parquet"
FACTOR_INCR = DATA / "factor_daily_incr.parquet"
HORIZONS = [1, 3, 5, 10]       # 后续 N 日涨幅
UP_ALERT = 0.04                # 盯盘: 涨幅超 +4% 提醒
DOWN_ALERT = -0.03             # 盯盘: 跌幅超 -3% 提醒


def load_prices():
    """加载全市场收盘价(日期,代码,收盘)"""
    files = [FACTOR]
    if FACTOR_INCR.exists():
        files.append(FACTOR_INCR)
    d = (pl.scan_parquet(files, cast_options=pl.ScanCastOptions(integer_cast="upcast"))
         .select(["日期", "股票代码", "收盘"])
         .collect())
    d = d.unique(subset=["日期", "股票代码"], keep="last").sort(["股票代码", "日期"])
    return d


def latest_pick_date():
    """最新选股日期(从 picks 文件或直接取数据最新交易日)"""
    dates = pl.read_parquet(FACTOR, columns=["日期"])["日期"].to_list()
    if FACTOR_INCR.exists():
        dates += pl.read_parquet(FACTOR_INCR, columns=["日期"])["日期"].to_list()
    return max(dates)


def record_today():
    """把今天最新选股的 Top N 记录到 selection_log(幂等: 已有该日期则跳过)"""
    # 从 picks 文件读取最新选股
    import glob
    picks = sorted(glob.glob(str(PICKS_DIR / "picks_*.csv")))
    if not picks:
        print("[track] 无选股记录文件")
        return
    newest = picks[-1]  # 最新
    # 解析日期
    from datetime import datetime
    dstr = Path(newest).stem.replace("picks_", "")
    pick_date = datetime.strptime(dstr, "%Y-%m-%d").date()
    # 幂等: 已记录过则跳过
    if LOG.exists():
        old = pd.read_csv(LOG)
        if pick_date in set(old["pick_date"]):
            print(f"[track] {pick_date} 已记录，跳过")
            return
    # 读选股结果
    df = pd.read_csv(newest)
    if df.empty:
        print(f"[track] {newest} 无数据")
        return
    # 记录每条
    price_map = load_prices().filter(pl.col("日期") == pick_date)
    rows = []
    for _, r in df.iterrows():
        raw = r.get("代码", "")
        code = str(int(float(raw))).zfill(6) if str(raw).replace(".0", "").isdigit() else str(raw).zfill(6)
        sel_price = r.get("收盘", 0)
        score = r.get("评分", "")
        factors = r.get("top_factors", r.get("归因", ""))
        rows.append({"pick_date": pick_date, "code": code, "sel_price": sel_price,
                     "score": score, "factors": factors})
    nrec = pd.DataFrame(rows)
    if LOG.exists():
        pd.concat([pd.read_csv(LOG), nrec], ignore_index=True).to_csv(LOG, index=False)
    else:
        nrec.to_csv(LOG, index=False)
    print(f"[track] 已记录 {pick_date} 选股 {len(nrec)} 只到 selection_log.csv")


def backfill_and_stats():
    """回填历史选股的后续 N 日涨幅 + 统计命中率 + 盯盘提醒"""
    if not LOG.exists():
        print("[track] 无选股记录，请先 --record")
        return
    log = pd.read_csv(LOG)
    if log.empty:
        print("[track] 选股记录为空")
        return
    prices = load_prices()
    # 构建 涨跌幅 查询: 每只股票的收盘价序列
    out_rows = []
    import datetime as dt
    for _, row in log.iterrows():
        raw_code = row["code"]
        code = str(int(float(raw_code))).zfill(6) if str(raw_code).replace(".0", "").replace(".", "").isdigit() else str(raw_code).zfill(6)
        pick_date = pd.Timestamp(row["pick_date"]).date()
        sel_price = float(row["sel_price"]) if pd.notna(row["sel_price"]) else None
        # 该股票 pick_date 之后的所有收盘
        stk = prices.filter(pl.col("股票代码") == code).sort("日期")
        rec = {"pick_date": pick_date, "code": code,
               "sel_price": sel_price, "score": row.get("score"), "factors": row.get("factors")}
        cur = None
        for h in HORIZONS:
            # 目标日期 = pick_date 往后数 h 个交易日
            dates = stk.filter(pl.col("日期") > pick_date)["日期"].to_list()
            if len(dates) >= h:
                fut_close = stk.filter(pl.col("日期") == dates[h-1])["收盘"][0]
                if sel_price and sel_price > 0:
                    rec[f"ret_{h}d"] = round(fut_close / sel_price - 1, 4)
            rec.setdefault(f"ret_{h}d", None)
        # 最新价(追踪盯盘)
        if len(stk) > 0:
            last = stk.select("收盘").to_series().drop_nulls().to_list()
            if last:
                cur = float(last[-1])
            rec["latest_price"] = cur
            if sel_price and cur:
                rec["total_ret"] = round(cur / sel_price - 1, 4)
            else:
                rec["total_ret"] = None
        out_rows.append(rec)
    out = pd.DataFrame(out_rows)
    # 写回后续涨幅(保留原选股记录并更新)
    # 命中率统计
    print("=" * 46)
    print("选股后续表现统计（因子命中率）")
    print("=" * 46)
    for h in HORIZONS:
        col = f"ret_{h}d"
        if col in out.columns:
            valid = out[col].dropna()
            if len(valid) > 0:
                hit = (valid > 0).mean() * 100
                avg = valid.mean() * 100
                print(f"  后续{h}日: 样本{len(valid)} | 命中率(涨){hit:.0f}% | 均值{avg:+.2f}%")
    # 盯盘提醒
    print("=" * 46)
    print("盯盘提醒（后续涨跌超阈值）")
    print("=" * 46)
    fresh = out[out["pick_date"] >= pd.Timestamp("today").date() - pd.Timedelta(days=7)]
    for _, r in fresh.iterrows():
        if pd.notna(r.get("total_ret")) and pd.notna(r.get("sel_price")):
            tr = float(r["total_ret"])
            flag = "🚨请关注" if tr >= UP_ALERT or tr <= DOWN_ALERT else ""
            if flag:
                print(f"  {r['code']} | 选入{r['sel_price']} → 现价{float(r['latest_price']):.2f} | {tr:+.1%} {flag}")
    # 落盘更新
    LOG2 = PICKS_DIR / "selection_backfill.csv"
    out.to_csv(LOG2, index=False)
    print(f"\n[track] 回填明细已存 {LOG2.name}")


if __name__ == "__main__":
    if "--record" in sys.argv:
        record_today()
    backfill_and_stats()
