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
    # P1-8(v4.1复核): 代码清单取自 factor_daily 全集(含退市), 而非 code_name_map(仅当前上市)
    codes = sorted(set(
        str(r[0]).zfill(6) for r in
        pl.scan_parquet(DATA / "factor_daily.parquet", columns=["股票代码"]).unique().collect().iter_rows()))
    if limit:
        codes = codes[:limit]
    t0 = time.time()
    all_rows = []
    fails = []
    done = 0
    for code in codes:
        rows = []
        for y in YEARS:
            rows += fetch_raw(code, y)
            time.sleep(0.05)
        if not rows:
            fails.append(code)  # P1-8: 记录失败清单
            done += 1
            continue
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
    # P1-8: 失败清单落盘
    if fails:
        Path(DATA / "raw_close_fails.txt").write_text("\n".join(fails), encoding="utf-8")
        print(f"[警告] {len(fails)} 只拉取失败: {fails[:10]}... 清单已存 raw_close_fails.txt")
    print(f"[完成] {done} 只不复权收盘价 -> {OUT}")


def _save(rows):
    """P1-9(v4.1复核): 分片落盘(每片独立文件, 避免O(n²)全量读+合并); 用 --merge 一次性合并回主文件"""
    df = pl.DataFrame(rows, schema={"日期": pl.Utf8, "股票代码": pl.Utf8, "收盘_不复权": pl.Float64},
                      orient="row")
    part = DATA / f"raw_close_part_{int(time.time()*1000)}.parquet"
    df.write_parquet(part, compression="zstd")


def merge_parts():
    """把分片合并回主文件(去重), 删除分片"""
    parts = sorted(Path(DATA).glob("raw_close_part_*.parquet"))
    if not parts:
        print("无分片")
        return
    dfs = [pl.read_parquet(p) for p in parts]
    merged = pl.concat(dfs).unique(subset=["日期", "股票代码"], keep="last")
    if OUT.exists():
        old = pl.read_parquet(OUT)
        merged = pl.concat([old, merged]).unique(subset=["日期", "股票代码"], keep="last")
    merged.write_parquet(OUT, compression="zstd")
    for p in parts:
        p.unlink()
    print(f"[merge] 合并 {len(parts)} 分片 → {OUT} ({len(merged)} 行)")


if __name__ == "__main__":
    if "--merge" in sys.argv:
        merge_parts()
    else:
        main()
