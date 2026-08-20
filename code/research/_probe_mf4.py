"""备选板块资金历史接口探测"""
import requests, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"

# 1. 东财 dataapi bkzj 历史(带日期参数?)
def t1():
    url = "https://data.eastmoney.com/dataapi/bkzj/getbkzj"
    for extra in [{"date": "2026-08-15"}, {"klt": "101", "lmt": "5"}, {"cb": "jq"}]:
        try:
            r = requests.get(url, params={"key": "f62", "code": "m:90+s:4", **extra},
                             headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=10)
            j = r.json()
            d = (j.get("data") or {})
            print(f"  getbkzj {list(extra.keys())}: {len((d.get('diff') or []))} 板块, total={d.get('total')}")
        except Exception as e:
            print(f"  getbkzj {list(extra.keys())}: FAIL {str(e)[:50]}")
        time.sleep(0.5)

# 2. 新浪 板块资金(行业) - 历史
def t2():
    url = "http://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
    try:
        r = requests.get(url, params={"page": "1", "num": "10", "sort": "netamount", "asc": "0"},
                         headers={"User-Agent": UA}, timeout=10)
        print(f"  sina hy: {r.status_code} len={len(r.text)} {r.text[:120]}")
    except Exception as e:
        print(f"  sina hy FAIL: {str(e)[:50]}")

# 3. 腾讯板块资金
def t3():
    url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/mktHs/rank"
    try:
        r = requests.get(url, params={"l": "1", "t": "2", "p": "1", "n": "10"},
                         headers={"User-Agent": UA}, timeout=10)
        print(f"  qq rank: {r.status_code} len={len(r.text)} {r.text[:120]}")
    except Exception as e:
        print(f"  qq rank FAIL: {str(e)[:50]}")

print("=== 备选历史板块资金 ===")
t1(); t2(); t3()
