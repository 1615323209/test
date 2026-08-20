#!/usr/bin/env python3
"""补采失败的公告(空文件)——巨潮限流退避重试
找出 announcements/ 下的空 jsonl, 重新拉取(带退避)
用法: python -m data.retry_announcements
"""
import json, time, csv, sys
from pathlib import Path
from data.collect_announcements import fetch_anns, YEARS, ANN_DIR, ORG_MAP

def main():
    empties = sorted(Path(ANN_DIR).glob("*.jsonl"), key=lambda p: p.stat().st_size)
    empties = [p for p in empties if p.stat().st_size == 0]
    print(f"空文件 {len(empties)} 个，开始补采(退避)")
    orgmap = {}
    for r in csv.DictReader(open(ORG_MAP, encoding="utf-8")):
        orgmap[r["代码"]] = r["orgId"]
    ok = fail = 0
    for p in empties:
        code = p.stem
        org = orgmap.get(code)
        if not org:
            fail += 1
            continue
        # 退避重试(最多5次)
        for attempt in range(5):
            try:
                anns = []
                for yr in YEARS:
                    anns += fetch_anns(code, org, yr)
                    time.sleep(0.2)
                if anns:
                    with open(p, "w", encoding="utf-8") as f:
                        for a in anns:
                            f.write(json.dumps(a, ensure_ascii=False) + "\n")
                    ok += 1
                    print(f"  ✅ {code} 补采 {len(anns)} 条")
                    break
                else:
                    # 真没公告
                    with open(p, "w", encoding="utf-8") as f:
                        f.write("")
                    ok += 1
                    break
            except Exception as e:
                time.sleep(10 * (attempt + 1))
        else:
            fail += 1
            print(f"  ❌ {code} 补采失败(重试5次)")
    print(f"[补采完成] 成功 {ok}, 失败 {fail}")

if __name__ == "__main__":
    main()
