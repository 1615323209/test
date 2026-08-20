"""push2his 备用域名/协议尝试"""
import requests, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
urls = [
    "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "https://push2his2.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get",
]
params = {"lmt": "0", "klt": "101", "secid": "90.BK0727", "fields1": "f1,f2,f3,f7",
          "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"}
for u in urls:
    try:
        r = requests.get(u, params=params,
                         headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}, timeout=10)
        j = r.json()
        kl = ((j.get("data") or {}).get("klines")) or []
        print(f"{u.split('//')[1].split('/')[0]}: {len(kl)} 天", f"样例 {kl[0]}" if kl else f"raw {str(j)[:80]}")
    except Exception as e:
        print(f"{u.split('//')[1].split('/')[0]}: FAIL {str(e)[:50]}")
    time.sleep(1)
