"""验证 getbkzj 的 date 参数是否返回历史日数据"""
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
url = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
H = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}

for date in ["2026-08-15", "2026-08-10", "2026-06-15", "2026-02-26"]:
    try:
        r = requests.get(url, params={"key": "f62", "code": "m:90+s:4", "date": date}, headers=H, timeout=10)
        j = r.json()
        d = j.get("data") or {}
        diff = d.get("diff") or []
        # 看第一条的时间字段
        sample = ""
        if diff:
            it = diff[0]
            sample = {k: it.get(k) for k in ("f14", "f62", "f66") if k in it}
        print(f"date={date}: {len(diff)} 板块 {sample}")
    except Exception as e:
        print(f"date={date}: FAIL {str(e)[:50]}")