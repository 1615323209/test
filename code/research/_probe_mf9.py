"""push2his 板块资金历史 - 替代域名/端点 + 东财datacenter"""
import requests, json, re, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

def get(url, params=None, headers=None, jp=False):
    h = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    if headers: h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h, timeout=12)
        if jp:
            m = re.search(r'=\(?({.*})\)?;?$', r.text, re.S)
            return json.loads(m.group(1)) if m else None
        return r.json()
    except Exception as e:
        return f"FAIL {str(e)[:55]}"

# A. push2his 域名变体
print("=== A. push2his 域名变体 ===")
p = {"lmt": "0", "klt": "101", "secid": "90.BK0727", "fields1": "f1,f2,f3,f7",
     "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"}
for host in ["push2his.eastmoney.com", "push2his2.eastmoney.com", "push2his1.eastmoney.com",
             "push2his.eastmoney.com:443", "99.push2his.eastmoney.com"]:
    j = get(f"https://{host}/api/qt/stock/fflow/daykline/get", p)
    if isinstance(j, dict):
        kl = ((j.get("data") or {}).get("klines")) or []
        print(f"  {host}: {len(kl)} 天" + (f" 末{kl[-1][:30]}" if kl else ""))
    else:
        print(f"  {host}: {j}")
    time.sleep(0.5)

# B. 东财 datacenter(数据中心历史接口)
print("\n=== B. 东财 datacenter 历史 ===")
# 板块资金历史: datacenter-web.eastmoney.com 数据中心
j = get("https://datacenter-web.eastmoney.com/api/data/v1/get",
        {"reportName": "RPT_INDUSTRY_MONEYFLOW", "columns": "ALL",
         "filter": '(TRADE_DATE>\'2024-01-01\')', "pageNumber": "1", "pageSize": "5"},
        headers={"Referer": "https://data.eastmoney.com/"})
if isinstance(j, dict):
    d = j.get("result") or {}
    print(f"  RPT_INDUSTRY_MONEYFLOW: {len(d.get('data') or [])} 条")
    if d.get("data"):
        print("   样例:", {k: d["data"][0].get(k) for k in list(d["data"][0].keys())[:8]})
else:
    print(f"  RPT_INDUSTRY_MONEYFLOW: {j}")
