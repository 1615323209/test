"""getbkzj 历史回补可行性——验证 date 参数是否真的无效/有无历史接口"""
import requests, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}

def probe(name, url, params):
    try:
        r = requests.get(url, params=params, headers=H, timeout=12)
        j = r.json()
        d = j.get("data") or {}
        diff = d.get("diff") or []
        if diff:
            it = diff[0]
            print(f"{name}: {len(diff)} 板块, f14={it.get('f14')}, f62={it.get('f62')}, keys={list(it.keys())[:8]}")
        else:
            print(f"{name}: empty, raw={str(j)[:100]}")
    except Exception as e:
        print(f"{name}: FAIL {str(e)[:50]}")
    time.sleep(0.5)

base = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
# 变体1: date 在 query 但用不同字段名
for k in ["date", "day", "tradeDate", "startdate", "dateStr"]:
    probe(f"date={k}", base, {"key": "f62", "code": "m:90+s:4", k: "2026-08-10"})
# 变体2: 历史板块资金接口 (bkzj/history)
probe("history", "https://data.eastmoney.com/dataapi/bkzj/getbkzj", {"key": "f62", "code": "m:90+s:4", "klt": "101", "lmt": "10"})
