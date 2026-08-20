#!/usr/bin/env python3
"""不复权收盘价补采（流程改造A1）——腾讯接口拉不复权日线, 产出 收盘_不复权
原理: 腾讯 newfqkline 不带fq参数=不复权, 与现有hfq同源
输出: D:/quant_data/raw_close.parquet (日期, 股票代码, 收盘_不复权)
用法: python -m data.collect_raw_close [--limit N]  [--start 2021]
"""
import requests, json, re, time, sys
from pathlib import Path
import polars as pl

DATA = Path("D:/quant_data")
OUT = DATA / "raw_close.parquet"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]


def symbol(code):
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("4", "8", "9")):
        return "bj" + code  # 北交所(部分可能拉不到, 非短线主战场)
    return "sz" + code


def fetch_raw(code, year):
    """单只单年不复权日线 -> [(date, close)]"""
    sym = symbol(code)
    var = f"kline_day{year}"
    try:
        r = requests.get(URL, params={"_var": var, "param": f"{sym},day,{year}-01-01,{year+1}-12-31,640,"},
                         headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=12)
        m = re.search(var + r'=({.*})', r.text, re.S)
        if not m:
            return []
        j = json.loads(m.group(1))
        stk = (j.get("data") or {}).get(sym) or {}
        kl = stk.get("day") or []
        return [(x[0], float(x[1])) for x in kl if len(x) >= 2 and float(x[1]) > 0]
    except Exception:
        return []


def main():
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    # 代码清单
    codes = [str(r[0]).zfill(6) for r in
             pl.read_csv(DATA / "code_name_map.csv", schema_overrides={"代码": pl.Utf8}).select("代码").iter_rows()]
    if limit:
        codes = codes[:limit]
    t0 = time.time()
    all_rows = []
    done = 0
    for code in codes:
        rows = []
        for y in YEARS:
            rows += fetch_raw(code, y)
            time.sleep(0.05)
        if rows:
            # 去重(跨年边界)
            seen = set()
            uniq = []
            for d, c in rows:
                if d not in seen:
                    seen.add(d)
                    uniq.append((d, code, c))
            all_rows += uniq
        done += 1
        if done % 100 == 0:
            print(f"  {done}/{len(codes)} 只, 累计{len(all_rows)}行, {time.time()-t0:.0f}s")
            # 增量落盘
            _save(all_rows)
            all_rows = []
    if all_rows:
        _save(all_rows)
    print(f"[完成] {done} 只不复权收盘价 -> {OUT}")


def _save(rows):
    df = pl.DataFrame(rows, schema={"日期": pl.Utf8, "股票代码": pl.Utf8, "收盘_不复权": pl.Float64},
                      orient="row")
    if OUT.exists():
        old = pl.read_parquet(OUT)
        merged = pl.concat([old, df]).unique(subset=["日期", "股票代码"], keep="last")
        merged.write_parquet(OUT, compression="zstd")
    else:
        df.write_parquet(OUT, compression="zstd")


if __name__ == "__main__":
    main()
