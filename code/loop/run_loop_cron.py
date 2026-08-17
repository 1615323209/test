#!/usr/bin/env python3
"""量化因子挖掘 loop cron 入口（纯 Python，无 bash 依赖）
每 2 小时跑 1 批；输出精简摘要
"""
import subprocess, sys, os
from pathlib import Path

CODE = Path(r"D:\quant_project\code")
PY = r"D:\02_download\APP\Anaconda\python.exe"

def run(cmd, timeout=540):
    """子进程超时保护（cron 总预算 600s，子任务留余量）"""
    try:
        r = subprocess.run(cmd, cwd=str(CODE), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, stdout="", stderr="子进程超时")

# 跑一批
r1 = run([PY, "-m", "loop.factor_mining_loop", "--batch", "1"])
print(r1.stdout[-2000:] or "(无输出)")
if r1.stderr:
    print("STDERR:", r1.stderr[-1000:])

print("---")
# 状态摘要
r2 = run([PY, "-m", "loop.factor_mining_loop", "--status"])
print(r2.stdout[:800])

print("---")
dash = CODE.parent / "quant_data" / "loop_state" / "dashboard.json"
if dash.exists():
    print(dash.read_text(encoding="utf-8")[:600])

# 非零退出码视为失败（通知用）
sys.exit(0 if r1.returncode == 0 else 1)
