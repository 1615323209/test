#!/usr/bin/env bash
# 云服务器端板块资金历史回补（bash + curl, 已验证云服务器 shell curl 可用）
# 读 stdin: 每行 "BK0727 医疗服务"  → 拉 121 天 → 输出 JSON 行到 stdout
set -e
while read -r code name; do
  [ -z "$code" ] && continue
  resp=$(curl -s --max-time 12 \
    "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=0&klt=101&secid=90.${code}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65" \
    -H "User-Agent: Mozilla/5.0" -H "Referer: https://quote.eastmoney.com/")
  echo "$resp" | python3 -c "
import json,sys
try:
    j=json.load(sys.stdin)
    kl=(j.get('data') or {}).get('klines') or []
    for line in kl:
        p=line.split(',')
        if len(p)>=6:
            print(json.dumps({'date':p[0],'code':'$code','name':'$name',
                'main':float(p[1]),'super':float(p[2]),'big':float(p[3]),
                'mid':float(p[4]),'small':float(p[5])}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({'date':'ERR','code':'$code','name':str(e)[:40],
        'main':0,'super':0,'big':0,'mid':0,'small':0}))
"
  sleep 0.1
done
