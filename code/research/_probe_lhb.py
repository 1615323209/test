"""龙虎榜接口完整探测 - 分页/总条数/字段"""
import requests

UA = "Mozilla/5.0"
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"

def q(**kw):
    params = {"reportName": "RPT_DAILYBILLBOARD_DETAILSNEW", "columns": "ALL",
              "sortColumns": "TRADE_DATE", "sortTypes": "-1", **kw}
    r = requests.get(BASE, params=params,
                     headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}, timeout=15)
    return r.json()

# 1. 单日条数 + 分页
j = q(filter="(TRADE_DATE='2026-08-20')", pageNumber="1", pageSize="500")
d = j.get("result") or {}
rows = d.get("data") or []
print(f"2026-08-20: {len(rows)} 条 (pageSize=500), total={d.get('count')}")
# 2. 全年总量 (2021)
j2 = q(filter="(TRADE_DATE>='2021-01-01')(TRADE_DATE<='2021-12-31')", pageNumber="1", pageSize="1")
d2 = j2.get("result") or {}
print(f"2021全年 total: {d2.get('count')}")
# 3. 完整字段列表
if rows:
    print("全部字段:", list(rows[0].keys()))