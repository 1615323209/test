"""调试板块资金日线接口参数"""
import requests

HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://data.eastmoney.com/"}

url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

# 文档: secid=90.BK0475 已验证可用。试多组参数
variants = [
    {"lmt": "0", "klt": "101", "secid": "90.BK0475", "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"},
    {"lmt": "2000", "klt": "101", "secid": "90.BK0475", "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"},
    {"lmt": "0", "klt": "101", "secid": "90.BK0727", "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"},
]
for i, p in enumerate(variants):
    try:
        r = requests.get(url, params=p, headers=HDRS, timeout=12)
        j = r.json()
        data = j.get("data")
        kl = (data or {}).get("klines") or [] if data else []
        print(f"变体{i+1} secid={p['secid']} lmt={p['lmt']}: {len(kl)} 天", f"样例 {kl[0]}" if kl else f"raw={str(j)[:120]}")
    except Exception as e:
        print(f"变体{i+1}: FAIL {str(e)[:70]}")
