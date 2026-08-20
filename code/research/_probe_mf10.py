"""从东财板块资金页面提取历史报表名 + 同花顺板块资金历史"""
import requests, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

# 1. 东财板块资金历史页面源码 → 找 reportName
print("=== 1. 东财板块资金页面 reportName ===")
for page in ["https://data.eastmoney.com/bkzj/hy.html",
             "https://data.eastmoney.com/bkzj/gn.html",
             "https://data.eastmoney.com/bkzj/bk.html"]:
    try:
        r = requests.get(page, headers={"User-Agent": UA}, timeout=12)
        names = set(re.findall(r'reportName["\s:=]+([A-Z_0-9]+)', r.text))
        print(f"  {page.split('/')[-1]}: {sorted(names)[:8]}")
    except Exception as e:
        print(f"  {page.split('/')[-1]}: FAIL {str(e)[:50]}")

# 2. 同花顺板块资金历史(行业) - 10jqka 数据中心
print("\n=== 2. 同花顺板块资金历史 ===")
# 同花顺概念/行业资金流接口 (10jqka)
try:
    r = requests.get("https://q.10jqka.com.cn/api/gpm/v1/industry/",
                     headers={"User-Agent": UA, "Referer": "https://q.10jqka.com.cn/"}, timeout=12)
    print(f"  industry api: {r.status_code} {r.text[:120]}")
except Exception as e:
    print(f"  industry api: FAIL {str(e)[:50]}")

# 3. 同花顺资金流历史接口 (data.10jqka.com.cn)
try:
    r = requests.get("https://data.10jqka.com.cn/funds/gnzjl/",
                     headers={"User-Agent": UA, "Referer": "https://data.10jqka.com.cn/"}, timeout=12)
    print(f"  funds/gnzjl: {r.status_code} len={len(r.text)}")
    # 找数据接口
    m = re.findall(r'ajax_url[^,]{0,120}', r.text)
    print(f"    ajax线索: {m[:3]}")
except Exception as e:
    print(f"  funds/gnzjl: FAIL {str(e)[:50]}")
