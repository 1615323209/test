#!/usr/bin/env python3
"""云服务器回补板块资金历史（121天窗口）——本地IP被反爬, 云服务器IP可用

流程: 本地板块清单 → SSH 到云服务器跑 python 拉全部板块 121 天 → 输出 JSON 到 stdout
     → 本地接收 → 合并入 moneyflow_sector.parquet

用法: python -m data.backfill_sector_history [--dry-run]
"""
import json, subprocess, sys, time
from pathlib import Path
import polars as pl

DATA = Path("D:/quant_data")
OUT = DATA / "moneyflow_sector.parquet"
OUT_INCR = DATA / "moneyflow_sector_incr.parquet"
SSH = ["ssh", "-o", "ConnectTimeout=10", "ubuntu@49.235.150.119"]

# 云服务器上的拉取脚本(单次执行)
CLOUD_SCRIPT = r'''
import json, urllib.request, time, sys
HDRS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
codes = json.loads(sys.stdin.read())  # [(code, name), ...]
out = []
for code, name in codes:
    url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?"
           f"lmt=0&klt=101&secid=90.{code}&fields1=f1,f2,f3,f7"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65")
    try:
        req = urllib.request.Request(url, headers=HDRS)
        j = json.loads(urllib.request.urlopen(req, timeout=10).read())
        kl = (j.get("data") or {}).get("klines") or []
        for line in kl:
            p = line.split(",")
            if len(p) >= 6:
                out.append({"date": p[0], "code": code, "name": name,
                            "main": float(p[1]), "super": float(p[2]),
                            "big": float(p[3]), "mid": float(p[4]), "small": float(p[5])})
    except Exception as e:
        out.append({"date": "ERR", "code": code, "name": str(e)[:40],
                    "main": 0, "super": 0, "big": 0, "mid": 0, "small": 0})
    time.sleep(0.15)
print(json.dumps(out, ensure_ascii=False))
'''


def main():
    dry = "--dry-run" in sys.argv
    # 板块清单(本地 128)
    if OUT_INCR.exists():
        d = pl.read_parquet(OUT_INCR).select(["板块代码", "板块名称"]).unique()
    else:
        print("缺少板块清单 moneyflow_sector_incr.parquet")
        return
    codes = [(r["板块代码"], r["板块名称"]) for r in d.iter_rows(named=True)]
    print(f"板块清单: {len(codes)} 个, 开始经云服务器回补 121 天历史...")

    # SSH 执行
    t0 = time.time()
    try:
        proc = subprocess.run(SSH + ["python3", "-c", CLOUD_SCRIPT],
                              input=json.dumps(codes, ensure_ascii=False),
                              capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            print(f"SSH 失败: {proc.stderr[:300]}")
            return
        rows = json.loads(proc.stdout.strip())
    except Exception as e:
        print(f"SSH 执行失败: {e}")
        return
    rows = [r for r in rows if r["date"] != "ERR"]
    errs = [r for r in rows if r["date"] == "ERR"]
    if errs:
        print(f"⚠️ {len(errs)} 个板块拉取失败: {errs[:3]}")
    if not rows:
        print("无数据返回")
        return

    df = pl.DataFrame(rows)
    print(f"云服务器返回: {len(df)} 行, 覆盖 {df['date'].n_unique()} 个交易日 "
          f"({df['date'].min()} ~ {df['date'].max()})")

    if dry:
        print("[dry-run] 不写入")
        return

    # 合并入主文件(121天历史) + 去重
    if OUT.exists():
        old = pl.read_parquet(OUT)
        merged = pl.concat([old, df]).unique(subset=["date", "code"], keep="last")
    else:
        merged = df
    merged.write_parquet(OUT, compression="zstd")
    print(f"✅ 已入库 {OUT.name}: {len(old) if OUT.exists() else 0} → {len(merged)} 行, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
