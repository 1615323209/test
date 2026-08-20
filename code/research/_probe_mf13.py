"""同花顺行业资金流历史 - 找日期参数"""
import requests, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

# 同花顺资金流历史常见模式
print("=== 试日期参数 ===")
tests = [
    ("date参数", "https://data.10jqka.com.cn/funds/hyzjl/date/20260818/"),
    ("ajax+date", "https://data.10jqka.com.cn/funds/hyzjl/date/20260818/field/tradezdf/order/desc/page/1/"),
    ("yyyymmdd", "https://data.10jqka.com.cn/funds/hyzjl/20260818/"),
]
for name, u in tests:
    try:
        r = requests.get(u, headers={"User-Agent": UA,
                                     "Referer": "https://data.10jqka.com.cn/funds/hyzjl/"}, timeout=12)
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.S)
        print(f"  {name}: {r.status_code} len={len(r.text)} 行={len(rows)}")
    except Exception as e:
        print(f"  {name}: FAIL {str(e)[:50]}")

# 找页面里的日期/历史线索
r = requests.get("https://data.10jqka.com.cn/funds/hyzjl/",
                 headers={"User-Agent": UA, "Referer": "https://data.10jqka.com.cn/"}, timeout=12)
# 看有无日期选择器/历史数据提示
m = re.findall(r'(历史|日期|time|date)[^<>]{0,50}', r.text[:20000])
print("\n日期线索:", sorted(set(m))[:10])

# 看是否有 js 配置(数据接口)
cfg = re.findall(r'var\s+(\w+)\s*=\s*["\']([^"\']*fund[^"\']*)["\']', r.text)
print("fund配置:", cfg[:5])
