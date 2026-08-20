"""验证: 腾讯接口能否同时给 不复权(day) 与 后复权(hfqday), 用于算复权因子"""
import requests, json, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
sym = "sh600519"  # 贵州茅台
r = requests.get(url, params={"_var": "kline_dayqfq2026", "param": f"{sym},day,2026-01-01,2026-08-20,640,qfq"},
                 headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=15)
m = re.search(r'kline_dayqfq2026=({.*})', r.text, re.S)
j = json.loads(m.group(1)) if m else {}
d = j.get("data") or {}
print("data keys:", list(d.keys()))
print("sym data keys:", list(d.get(sym, {}).keys()) if sym in d else "N/A")
stk = d.get(sym) or {}
# 看有无 day / qfqday / hfqday 并存
for k in ["day", "qfqday", "hfqday"]:
    kl = stk.get(k)
    print(f"  {k}: {'有, ' + str(len(kl)) + '条' if kl else '无'} 样例 {kl[0] if kl else ''}")
# qt 字段(常见于腾讯行情, 含复权因子)
if "qt" in stk:
    print("  qt:", str(stk["qt"])[:150])