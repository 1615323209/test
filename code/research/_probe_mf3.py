"""调试板块资金日线——换Referer/UA组合"""
import requests, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

combos = [
    {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"},
    {"User-Agent": UA, "Referer": "https://data.eastmoney.com/bkzj/hy.html"},
    {"User-Agent": UA, "Referer": "https://www.eastmoney.com/"},
]
url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
params = {"lmt": "0", "klt": "101", "secid": "90.BK0727", "fields1": "f1,f2,f3,f7",
          "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"}
for i, h in enumerate(combos):
    try:
        r = requests.get(url, params=params, headers=h, timeout=12)
        j = r.json()
        kl = ((j.get("data") or {}).get("klines")) or []
        print(f"组合{i+1} ({h['Referer']}): {len(kl)} 天", f"样例 {kl[0]}" if kl else "")
    except Exception as e:
        print(f"组合{i+1}: FAIL {str(e)[:60]}")
    time.sleep(1)
