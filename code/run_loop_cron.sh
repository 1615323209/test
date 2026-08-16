#!/bin/bash
# 量化因子挖掘 loop cron 入口
# 每 2 小时跑 1 批；输出精简摘要
# 注意：cron 环境无 GNU timeout，脚本自身用 Python 控制批大小，不依赖外部超时
cd /d/quant_project/code || exit 1
PY="/d/02_download/APP/Anaconda/python.exe"

# 跑一批（每批 5 候选；进程锁防并发）
"$PY" factor_mining_loop.py --batch 1 2>&1 | tail -8

echo "---"
# 状态摘要
"$PY" factor_mining_loop.py --status 2>&1 | head -6
echo "---"
cat /d/quant_data/loop_state/dashboard.json 2>/dev/null | head -12
