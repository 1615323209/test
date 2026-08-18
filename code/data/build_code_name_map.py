#!/usr/bin/env python3
"""拉取 A 股全市场 代码→公司名 映射，存 code_name_map.csv（新浪公开接口）
用法: python -m data.build_code_name_map
输出: D:/quant_data/code_name_map.csv
"""
import requests, csv, time, json, re
from pathlib import Path

OUT = Path("D:/quant_data/code_name_map.csv")
HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def fetch_page(node, page, num=100):
    """新浪行情节点分页取代码+名称"""
    url = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {"page": page, "num": num, "sort": "symbol", "asc": 1,
              "node": node, "symbol": "", "_s_r_a": "page"}
    r = requests.get(url, params=params, headers=HDRS, timeout=10)
    r.encoding = "gbk"
    txt = r.text.strip()
    if not txt or txt == "null":
        return []
    data = json.loads(txt)
    return data if isinstance(data, list) else []


def main():
    print("=== 拉取 A 股代码→名称映射（新浪） ===")
    rows = []
    nodes = ["hs_a"]  # 沪深A股（含创业板/科创板/北交所）
    for node in nodes:
        page = 1
        while page <= 60:  # 100/页 x 60 页 = 6000 只，足够
            try:
                diff = fetch_page(node, page)
                if not diff:
                    break
                for item in diff:
                    code = str(item.get("code", "")).strip()
                    name = str(item.get("name", "")).strip()
                    if code and name:
                        rows.append((code, name))
                page += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"  {node} 第{page}页失败: {str(e)[:60]}")
                break
        print(f"  {node}: 累计 {len(rows)} 只")
    # 去重写
    seen = set()
    uniq = [r for r in rows if not (r[0] in seen or seen.add(r[0]))]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["代码", "公司"])
        w.writerows(uniq)
    print(f"  已存 {OUT}: {len(uniq)} 只股票")
    # 抽查
    import polars as pl
    d = pl.read_csv(OUT, schema_overrides={"代码": pl.Utf8})
    for c in ["601611", "688590", "603106", "600519"]:
        hit = d.filter(pl.col("代码") == c)
        print(f"    抽查 {c}: {hit['公司'][0] if len(hit) else '未找到'}")


if __name__ == "__main__":
    main()
