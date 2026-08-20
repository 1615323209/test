"""同花顺行业资金流 hyzjl 深挖 - 表格结构 + 历史"""
import requests, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

# 1. hyzjl 页面看表格结构
r = requests.get("https://data.10jqka.com.cn/funds/hyzjl/",
                 headers={"User-Agent": UA, "Referer": "https://data.10jqka.com.cn/"}, timeout=12)
txt = r.text
print("hyzjl 页面:", r.status_code, "len=", len(txt))

# 提取表头
th = re.findall(r'<th[^>]*>(.*?)</th>', txt, re.S)
print("表头:", [re.sub('<[^>]+>','',c).strip()[:10] for c in th[:12]])

# 提取数据行(板块名+资金)
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', txt, re.S)
print("行数:", len(rows))
for rw in rows[:2]:
    cells = re.findall(r'<td[^>]*>(.*?)</td>', rw, re.S)
    clean = [re.sub('<[^>]+>','',c).strip()[:14] for c in cells]
    print("  行:", clean[:10])

# 2. 分页接口确认(带页码)
print("\n=== 分页接口 ===")
for pg in [2, 5]:
    try:
        r2 = requests.get(f"https://data.10jqka.com.cn/funds/hyzjl/field/tradezdf/order/desc/page/{pg}/",
                          headers={"User-Agent": UA, "Referer": "https://data.10jqka.com.cn/funds/hyzjl/",
                                   "X-Requested-With": "XMLHttpRequest"}, timeout=12)
        rows2 = re.findall(r'<tr[^>]*>(.*?)</tr>', r2.text, re.S)
        print(f"  page/{pg}: {len(rows2)} 行")
    except Exception as e:
        print(f"  page/{pg}: FAIL {str(e)[:50]}")
