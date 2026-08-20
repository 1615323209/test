"""同花顺 gnzjl 页面深挖数据接口"""
import requests, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
r = requests.get("https://data.10jqka.com.cn/funds/gnzjl/",
                 headers={"User-Agent": UA, "Referer": "https://data.10jqka.com.cn/"}, timeout=12)
txt = r.text

# 找 ajax 接口模式 (同花顺常见: /funds/gnzjl/field/tradezdf/order/desc/page/1/ajax/1/)
print("=== 接口模式线索 ===")
m = re.findall(r'(funds/[a-z]+/[^"\']{0,80})', txt)
print("funds路径:", sorted(set(m))[:8])

# 找 js 文件引用
js = re.findall(r'src="([^"]+\.js[^"]*)"', txt)
print("js文件:", js[:6])

# 直接试同花顺经典 ajax 接口
print("\n=== 试 ajax 接口 ===")
for url in [
    "https://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/page/1/ajax/1/",
    "https://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/page/1/",
]:
    try:
        r2 = requests.get(url, headers={"User-Agent": UA,
                                        "Referer": "https://data.10jqka.com.cn/funds/gnzjl/",
                                        "X-Requested-With": "XMLHttpRequest"}, timeout=12)
        print(f"  {url.split('funds/')[1][:40]}: {r2.status_code} len={len(r2.text)}")
        # 抽板块行
        rows = re.findall(r'<tr>.*?</tr>', r2.text, re.S)
        print(f"    表格行: {len(rows)}")
        if rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', rows[0], re.S)
            print(f"    首行单元格: {[re.sub('<[^>]+>','',c).strip()[:12] for c in cells[:6]]}")
    except Exception as e:
        print(f"  FAIL {str(e)[:50]}")
