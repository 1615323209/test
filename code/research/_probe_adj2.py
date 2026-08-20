"""验证: 腾讯不复权日线(day) vs 后复权(hfqday) 能否同时取到 → 算复权因子"""
import requests, json, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
sym = "sh600519"

def fetch(fq, var):
    r = requests.get(url, params={"_var": var, "param": f"{sym},day,2024-01-01,2024-12-31,320,{fq}"},
                     headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=15)
    m = re.search(var + r'=({.*})', r.text, re.S)
    j = json.loads(m.group(1)) if m else {}
    stk = (j.get("data") or {}).get(sym) or {}
    kl = stk.get("day") or stk.get(fq + "day") or []
    return [(x[0], float(x[1])) for x in kl if len(x) >= 2]  # (date, close)

# 不复权(不带fq参数 or fq=)
for fq, var in [("", "kline_day2024"), ("qfq", "kline_dayqfq2024"), ("hfq", "kline_dayhfq2024")]:
    try:
        kl = fetch(fq, var)
        print(f"{fq or 'raw'}: {len(kl)} 条, 首 {kl[0] if kl else '-'}, 末 {kl[-1] if kl else '-'}")
    except Exception as e:
        print(f"{fq or 'raw'}: FAIL {str(e)[:50]}")
