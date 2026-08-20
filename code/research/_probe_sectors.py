"""探测板块代码全集来源：解析 data.eastmoney.com/bkzj/hy.html 的行业板块表"""
import requests, re, json

HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://data.eastmoney.com/bkzj/hy.html"}

url = "https://data.eastmoney.com/bkzj/hy.html"
try:
    r = requests.get(url, headers=HDRS, timeout=15)
    txt = r.text
    print(f"hy.html status={r.status_code} len={len(txt)}")
    # 找 BK 代码 (行业板块代码 BKxxxx 出现在 JS 或 data 里)
    bks = set(re.findall(r'BK\d{4}', txt))
    print(f"HTML 中 BK 代码: {len(bks)} 个, 样例 {sorted(bks)[:10]}")
    # 找板块名称模式
    names = re.findall(r'"f14":"([^"]{1,8}板块)"', txt)
    print(f"f14 板块名(前10): {names[:10]}")
except Exception as e:
    print(f"FAIL: {str(e)[:80]}")

# 备选: 东财板块资金接口(文档实测可用的 getbkzj)
print("\n=== 备选: 板块资金接口 getbkzj ===")
try:
    r = requests.get("https://data.eastmoney.com/dataapi/bkzj/getbkzj",
                     params={"key": "f62", "code": "m:90+s:4"},
                     headers={"User-Agent": HDRS["User-Agent"], "Referer": "https://data.eastmoney.com/"},
                     timeout=15)
    j = r.json()
    diff = (j.get("data") or {}).get("diff") or []
    print(f"getbkzj: {len(diff)} 板块")
    for it in diff[:5]:
        print(f"  {it.get('f12')} {it.get('f14')} f62={it.get('f62')}")
except Exception as e:
    print(f"getbkzj FAIL: {str(e)[:80]}")
