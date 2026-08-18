#!/usr/bin/env python3
"""量化因子挖掘 loop cron 入口（纯 Python，无 bash 依赖）
改造2.0 3.1+4.2：预算驱动 + 推送改为读 push_card.md（loop 自己产出结论，cron 只搬运）
"""
import subprocess, sys, os
from pathlib import Path

CODE = Path(r"D:\quant_project\code")
PY = r"D:\02_download\APP\Anaconda\python.exe"
STATE = Path(r"D:\quant_data\loop_state")
BUDGET = 420
# 改造3.1：子进程超时 = budget + 120（留时间给状态输出与 dashboard 生成）
TIMEOUT = BUDGET + 120

def run(cmd, timeout=TIMEOUT):
    try:
        r = subprocess.run(cmd, cwd=str(CODE), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="子进程超时")

# 跑一批（改造3.1：预算驱动，budget 内正常结束，killed 只意味着真故障）
r1 = run([PY, "-m", "loop.factor_mining_loop", "--batch", "1",
          "--n-cands", "5", "--budget-sec", str(BUDGET)])

# 改造4.2：推送内容 = push_card.md 原文（loop 自己产出），不再打印 stdout 日志尾巴
card = STATE / "runs" / "push_card.md"
push = ""
if card.exists():
    push = card.read_text(encoding="utf-8")
else:
    push = "【因子挖掘】本 run 未生成 push_card.md，降级输出摘要：\n"
    push += (r1.stdout[-800:] or "(无输出)")
    if r1.stderr:
        push += "\nSTDERR: " + r1.stderr[-500:]

print(push)

# 空转 run 额外附一行（感知不疲劳）
if r1.stdout and "空转" in push:
    print("\n(空转 run，已折叠为一行为告警候选)")

# 非零退出码视为失败（通知用）
sys.exit(0 if r1.returncode == 0 else 1)
