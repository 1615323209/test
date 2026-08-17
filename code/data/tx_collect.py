#!/usr/bin/env python3
"""腾讯源日K采集 — requests 直连 + 超时 + 多线程
接口: https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get
"""
import requests
import pandas as pd
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

def fetch_one(code, year=2026):
    """拉单只股票某年 hfq 日K，返回 DataFrame 或 None"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    symbol = f'{prefix}{code}'
    url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    params = {
        "_var": f"kline_dayhfq{year}",
        "param": f"{symbol},day,{year}-01-01,{year+1}-12-31,640,hfq",
        "r": "0.8205512681390605",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        text = r.text
        # JSONP 解析: kline_dayhfq2026={...}
        idx = text.find('={')
        if idx < 0:
            return None
        data = json.loads(text[idx+1:])
        d = data.get('data', {}).get(symbol, {})
        klines = d.get('hfqday') or d.get('day')
        if not klines:
            return None
        rows = []
        for k in klines:
            rows.append({
                '日期': k[0], '开盘': float(k[1]), '收盘': float(k[2]),
                '最高': float(k[3]), '最低': float(k[4]), '成交量': float(k[5]),
                'turnover': float(k[7]) if len(k) > 7 else 0.0,
                '成交额': float(k[8]) if len(k) > 8 else 0.0,
                '股票代码': code,
            })
        return pd.DataFrame(rows)
    except Exception:
        return None

def collect(codes, year=2026, n_threads=10, out_path=None):
    """并行采集，返回合并 DataFrame"""
    all_rows = []
    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        futs = {ex.submit(fetch_one, c, year): c for c in codes}
        for i, fut in enumerate(as_completed(futs)):
            df = fut.result()
            if df is not None and len(df) > 0:
                all_rows.append(df)
                ok += 1
            else:
                fail += 1
            if (i+1) % 500 == 0:
                el = time.time()-t0
                print(f"  [{i+1}/{len(codes)}] 成功{ok} 失败{fail} {el:.0f}s", flush=True)
    if all_rows:
        raw = pd.concat(all_rows, ignore_index=True)
        if out_path:
            raw.to_parquet(out_path)
        print(f"采集完成: {ok} 成功, {fail} 失败, {len(raw)} 行, {time.time()-t0:.0f}s")
        return raw
    print(f"采集失败: 0 成功")
    return None

if __name__ == '__main__':
    import sys
    codes = sys.argv[1].split(',') if len(sys.argv) > 1 else ['000001']
    year = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    df = collect(codes, year)
    if df is not None:
        print(df.tail(3))
