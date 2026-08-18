#!/usr/bin/env python3
"""run 级产物（改造2.0 4.1/4.2）：run_log.jsonl 唯一事件流 + run_summary.json + push_card.md

原则：loop 自己产出契约产物，cron/agent 只消费不现场解释。前端与推送都只读 run_log.jsonl，
不再各自解析 l1/l2/l3/l4 四份 CSV（保留 CSV 供人肉查历史）。
"""
import json, time
from pathlib import Path

STATE = Path(r"D:\quant_data\loop_state")
RUNS_DIR = STATE / "runs"
LOG = STATE / "run_log.jsonl"

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def log_event(stage, run_id, **fields):
    """run_log.jsonl 追加一行（append-only，唯一事件流）"""
    RUNS_DIR.mkdir(parents=True, exist_ok=True) if not RUNS_DIR.exists() else None
    rec = {"ts": _now(), "run_id": run_id, "stage": stage, **fields}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec

def read_events(stage=None, run_id=None, limit=None):
    """读 run_log.jsonl（可按 stage/run_id 过滤）"""
    if not LOG.exists():
        return []
    out = []
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if stage and r.get("stage") != stage:
                continue
            if run_id and r.get("run_id") != run_id:
                continue
            out.append(r)
    if limit:
        out = out[-limit:]
    return out

def new_run_id():
    """生成 run_id rYYYYMMDD_HHMM"""
    return "r" + time.strftime("%Y%m%d_%H%M")

def write_summary(run_id, data):
    """写 run_summary.json（前端目录），data 含 exit_reason/cost/gates 等"""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"run_{run_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def write_push_card(run_id, lines):
    """写 push_card.md（cron 直接 cat 的最终推送内容）"""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    (RUNS_DIR / "push_card.md").write_text(body, encoding="utf-8")
    return body
