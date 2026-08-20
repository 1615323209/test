#!/usr/bin/env python3
"""云服务器端板块资金历史回补脚本（curl 方式, 已验证云服务器 curl 可用）
读 stdin 的 JSON [(code, name),...] → 拉 121 天 → 输出 JSON 到 stdout
"""
import json, subprocess, time, sys

UA = "Mozilla/5.0"
REF = "https://quote.eastmoney.com/"

def fetch(code):
    url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?"
           f"lmt=0&klt=101&secid=90.{code}&fields1=f1,f2,f3,f7"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65")
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "12", url,
                            "-H", f"User-Agent: {UA}", "-H", f"Referer: {REF}"],
                           capture_output=True, text=True, timeout=15)
        j = json.loads(r.stdout)
        return (j.get("data") or {}).get("klines") or []
    except Exception:
        return None

def main():
    codes = json.loads(sys.stdin.read())
    out = []
    for code, name in codes:
        kl = fetch(code)
        if kl is None:
            out.append({"date": "ERR", "code": code, "name": "fetch fail",
                        "main": 0, "super": 0, "big": 0, "mid": 0, "small": 0})
            continue
        for line in kl:
            p = line.split(",")
            if len(p) >= 6:
                out.append({"date": p[0], "code": code, "name": name,
                            "main": float(p[1]), "super": float(p[2]),
                            "big": float(p[3]), "mid": float(p[4]), "small": float(p[5])})
        time.sleep(0.1)
    print(json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    main()
