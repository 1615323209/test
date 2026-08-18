#!/usr/bin/env python3
"""历史公告采集器（巨潮资讯网 cninfo）
目标: 收集全市场(除退市股) 2021-2024 历史公告，产出 代码→公告(带日期) 库，供练新闻情绪因子回测

两阶段:
  阶段A: 构建 code→orgId 映射(stock_org_map.csv) —— 逐只 topSearch(带delisted过滤)
  阶段B: 逐股拉 2021-2024 公告(分页) 存 announcements/{code}.jsonl —— 断点续跑
用法:
  python -m data.collect_announcements --build-map      # 阶段A: 构建orgId映射
  python -m data.collect_announcements --collect 10     # 阶段B: 采前N只(测试), 默认全量
"""
import requests, json, time, csv, sys, os
from pathlib import Path

DATA = Path("D:/quant_data")
CODE_MAP = DATA / "code_name_map.csv"     # 代码→公司名(5544只未退市)
ORG_MAP = DATA / "stock_org_map.csv"       # 代码→orgId
ANN_DIR = DATA / "announcements"           # 公告库 {code}.jsonl

HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
        "Referer": "http://www.cninfo.com.cn/new/"}
SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"
ANN_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
YEARS = ["2021-01-01~2021-12-31", "2022-01-01~2022-12-31",
         "2023-01-01~2023-12-31", "2024-01-01~2024-12-31"]


def get_org(code):
    """topSearch 拿单只股票 orgId + delisted"""
    try:
        r = requests.post(SEARCH_URL, data={"keyWord": code, "maxNum": "5"},
                          headers=HDRS, timeout=8)
        arr = r.json()
        for it in arr:
            if it.get("code") == code:
                return it.get("orgId"), it.get("delisted"), it.get("zwjc")
        return None, None, None
    except Exception:
        return None, None, None


def build_org_map():
    """阶段A: 逐只补 orgId，存 stock_org_map.csv(过滤退市)"""
    if not CODE_MAP.exists():
        print("缺少 code_name_map.csv，先跑 data.build_code_name_map")
        return
    # 读已有代码清单
    import polars as pl
    codes = [str(r[0]).zfill(6) for r in pl.read_csv(CODE_MAP, schema_overrides={"代码": pl.Utf8})
             .select("代码").iter_rows()]
    print(f"共 {len(codes)} 只待查 orgId")
    rows = []
    done = set()
    if ORG_MAP.exists():
        rows = list(csv.DictReader(open(ORG_MAP, encoding="utf-8")))
        done = {r["代码"] for r in rows}
    t0 = time.time()
    for i, c in enumerate(codes):
        if c in done:
            continue
        org, delisted, name = get_org(c)
        if delisted is True or delisted == "true":
            rows.append({"代码": c, "orgId": "", "名称": name or "", "delisted": "1"})
        elif org:
            rows.append({"代码": c, "orgId": org, "名称": name or "", "delisted": "0"})
        else:
            rows.append({"代码": c, "orgId": "", "名称": "", "delisted": ""})  # 查不到,留空待补
        if (i + 1) % 50 == 0:
            with open(ORG_MAP, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["代码", "orgId", "名称", "delisted"])
                w.writeheader(); w.writerows(rows)
            print(f"  进度 {i+1}/{len(codes)}, 已存{len(rows)}, {time.time()-t0:.0f}s")
        time.sleep(0.1)
    with open(ORG_MAP, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["代码", "orgId", "名称", "delisted"])
        w.writeheader(); w.writerows(rows)
    usable = sum(1 for r in rows if r["orgId"] and r["delisted"] != "1")
    print(f"[阶段A完成] 可采集 {usable} 只（已过滤退市/查不到）-> {ORG_MAP}")


def fetch_anns(code, org, year_range):
    """拉单只股票某年的公告(分页)"""
    out = []
    page = 1
    while page <= 30:
        params = {"pageNum": str(page), "pageSize": "30", "column": "szse",
                  "tabName": "fulltext", "plate": "", "stock": f"{code},{org}",
                  "searchkey": "", "secid": "", "category": "", "trade": "",
                  "seDate": year_range, "sortName": "", "sortType": "",
                  "isHLtitle": "true"}
        try:
            r = requests.post(ANN_URL, data=params, headers=HDRS, timeout=10)
            j = r.json()
            anns = j.get("announcements") or []
            if not anns:
                break
            for a in anns:
                ts = a.get("announcementTime")
                dt = time.strftime("%Y-%m-%d", time.localtime(ts/1000)) if ts else "?"
                out.append({"date": dt, "title": a.get("announcementTitle", ""),
                            "code": code, "pdf": a.get("adjunctUrl", "")})
            total = j.get("totalAnnouncement") or 0
            if page * 30 >= total:
                break
            page += 1
            time.sleep(0.15)
        except Exception as e:
            print(f"    {code} 拉取失败: {str(e)[:50]}")
            break
    return out


def collect(limit=None):
    """阶段B: 逐股拉 2021-2024 公告，存 announcements/{code}.jsonl"""
    if not ORG_MAP.exists():
        print("先跑 --build-map")
        return
    import polars as pl
    targets = [(r["代码"], r["orgId"]) for r in csv.DictReader(open(ORG_MAP, encoding="utf-8"))
               if r["orgId"] and r["delisted"] != "1"]
    ANN_DIR.mkdir(exist_ok=True)
    if limit:
        targets = targets[:limit]
    t0 = time.time()
    done = 0
    for code, org in targets:
        f = ANN_DIR / f"{code}.jsonl"
        if f.exists():  # 断点续跑
            done += 1
            continue
        anns = []
        for yr in YEARS:
            anns += fetch_anns(code, org, yr)
            time.sleep(0.1)
        with open(f, "w", encoding="utf-8") as fh:
            for a in anns:
                fh.write(json.dumps(a, ensure_ascii=False) + "\n")
        done += 1
        if done % 20 == 0:
            print(f"  {done}/{len(targets)} 只完成, 累计公告{sum(1 for p in ANN_DIR.glob('*.jsonl') for _ in open(p, encoding='utf-8'))}, {time.time()-t0:.0f}s")
    n_files = len(list(ANN_DIR.glob("*.jsonl")))
    if limit:
        print(f"[阶段B测试] 采集 {limit} 只完成，公告库 {ANN_DIR}")
    else:
        print(f"[阶段B完成] 采集 {n_files} 只股票的历史公告 -> {ANN_DIR}")


if __name__ == "__main__":
    if "--build-map" in sys.argv:
        build_org_map()
    elif "--collect" in sys.argv:
        i = sys.argv.index("--collect")
        lim = int(sys.argv[i+1]) if len(sys.argv) > i+1 else None
        collect(lim)
    else:
        print(__doc__)
