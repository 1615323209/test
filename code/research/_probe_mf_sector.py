"""验证板块资金日线接口(121天窗口) + 板块列表"""
import requests, json

HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://data.eastmoney.com/"}

# 1. 板块列表 getbkzj(128行业板块)
r = requests.get("https://data.eastmoney.com/dataapi/bkzj/getbkzj",
                 params={"key": "f62", "code": "m:90+s:4"},
                 headers={"User-Agent": HDRS["User-Agent"], "Referer": "https://data.eastmoney.com/"},
                 timeout=15)
j = r.json()
diff = (j.get("data") or {}).get("diff") or []
print(f"行业板块 {len(diff)} 个:")
for it in diff[:3]:
    print(f"  {it.get('f12')} {it.get('f14')}")

# 2. 板块资金日线 fflow/daykline (文档实测可用)
bk = diff[0]["f12"]  # BK0727
url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
params = {"lmt": "0", "klt": "101", "secid": f"90.{bk}",
          "secid2": "0", "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"}
try:
    r = requests.get(url, params=params, headers=HDRS, timeout=15)
    j = r.json()
    klines = (j.get("data") or {}).get("klines") or []
    print(f"\n板块 {bk} 资金日线: {len(klines)} 天")
    if klines:
        print(f"  样例: {klines[0]}")
        print(f"  最新: {klines[-1]}")
except Exception as e:
    print(f"日线 FAIL: {str(e)[:80]}")
