"""同花顺行业资金流(备用源) + 龙虎榜历史接口 探测"""
import requests, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

# 1. 同花顺 hyzjl 当日(本地IP通?) - 完整解析
print("=== 1. 同花顺行业资金流(本地) ===")
try:
    r = requests.get("https://data.10jqka.com.cn/funds/hyzjl/",
                     headers={"User-Agent": UA, "Referer": "https://data.10jqka.com.cn/"}, timeout=12)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.S)
    cnt = 0
    for rw in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', rw, re.S)
        if len(cells) >= 7:
            clean = [re.sub('<[^>]+>', '', c).strip() for c in cells]
            print(f"  {clean[1]} 涨{clean[3]} 净额{clean[6]}亿")
            cnt += 1
            if cnt >= 5: break
    print(f"  共解析 {cnt} 个行业(前5)")
except Exception as e:
    print(f"  FAIL {str(e)[:60]}")

# 2. 龙虎榜历史(东财) - 多年历史
print("\n=== 2. 龙虎榜历史 ===")
try:
    r = requests.get("https://datacenter-web.eastmoney.com/api/data/v1/get",
                     params={"reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
                             "columns": "ALL", "filter": "(TRADE_DATE>='2024-01-01')",
                             "pageNumber": "1", "pageSize": "3", "sortColumns": "TRADE_DATE",
                             "sortTypes": "-1"},
                     headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=12)
    j = r.json()
    d = j.get("result") or {}
    rows = d.get("data") or []
    print(f"  RPT_DAILYBILLBOARD: {len(rows)} 条")
    if rows:
        r0 = rows[0]
        print(f"    样例: { {k: r0.get(k) for k in list(r0.keys())[:8]} }")
except Exception as e:
    print(f"  FAIL {str(e)[:80]}")
