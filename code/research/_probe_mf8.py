"""板块资金历史数据 - 多来源并行探测"""
import requests, json, re, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

def probe(name, url, params=None, headers=None, is_jsonp=False):
    h = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h, timeout=12)
        if is_jsonp:
            m = re.search(r'=\(?({.*})\)?;?$', r.text, re.S)
            j = json.loads(m.group(1)) if m else {}
        else:
            j = r.json()
        return j
    except Exception as e:
        return f"FAIL {str(e)[:60]}"

# 1. 东财 dataapi 板块资金历史(klt日线) - 不同端点
print("=== 1. 东财 dataapi 历史端点 ===")
for ep in ["getbkzj", "getbkzjhistory", "bkzj/getbkzj", "bkzj/getbkzjhistory"]:
    j = probe(ep, f"https://data.eastmoney.com/dataapi/{ep}",
              {"key": "f62", "code": "m:90+s:4", "klt": "101", "lmt": "10"})
    if isinstance(j, dict):
        d = j.get("data") or {}
        print(f"  {ep}: keys={list(d.keys())[:6] if isinstance(d, dict) else type(d)}")
    else:
        print(f"  {ep}: {j}")

# 2. 新浪板块资金(历史) - 行业板块日线
print("\n=== 2. 新浪板块资金 ===")
j = probe("sina_hy", "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
          {"page": "1", "num": "10", "sort": "netamount", "asc": "0"})
if isinstance(j, str):
    print(f"  newSinaHy: {j[:80]}")
else:
    print(f"  newSinaHy: {str(j)[:150]}")

# 3. 同花顺板块资金
print("\n=== 3. 同花顺板块资金 ===")
j = probe("ths", "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock",
          {"stock_type": "a", "type": "30", "list_type": "normal"},
          headers={"User-Agent": UA, "Referer": "https://www.10jqka.com.cn/", "X-Requested-With": "XMLHttpRequest"})
print(f"  ths: {str(j)[:150]}")

# 4. 腾讯板块资金
print("\n=== 4. 腾讯板块资金 ===")
for u in ["https://proxy.finance.qq.com/ifzqgtimg/appstock/app/mktHs/rank",
          "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/mktHs/mktHsRank"]:
    j = probe("qq", u, {"l": "1", "t": "2", "p": "1", "n": "10"})
    print(f"  {u.split('/')[-1]}: {str(j)[:120]}")
