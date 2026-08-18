#!/usr/bin/env python3
"""改造2.0 4.3：日报生成（任务8，每日18:05 no_agent）
读 run_log.jsonl 当日 12 个 run 聚合：候选漏斗总账 / 入池/启用清单 / 死因Top3 / 成本 / 告警 / active_factors版本
输出 daily_picks 日报卡片（cron cat）+ 重生成 dashboard.html
"""
import json, sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
STATE = Path(r"D:\quant_data\loop_state")
LOG = STATE / "run_log.jsonl"
RUNS = STATE / "runs"

def _today():
    return date.today().isoformat()

def load_events():
    if not LOG.exists():
        return []
    out = []
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out

def build_daily():
    evs = load_events()
    today = _today()
    runs = [e for e in evs if e.get("ts", "").startswith(today)]
    run_ids = {e.get("run_id") for e in runs if e.get("run_id")}
    # 各 run 的 run_end
    ends = [e for e in runs if e.get("stage") == "run_end"]
    starts = [e for e in runs if e.get("stage") == "run_start"]
    alerts = [e for e in runs if e.get("stage") == "alert"]
    l2 = [e for e in runs if e.get("stage") == "l2"]
    l3 = [e for e in runs if e.get("stage") == "l3"]
    n_runs = len(ends) or len(starts)
    total_added = sum(e.get("added", 0) for e in l2)
    total_enabled = sum(e.get("enabled", 0) for e in l3)
    # active_factors 版本（读最后一 run 的 summary 或 active_factors）
    af_ver = None
    try:
        import sys as _s
        _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from paper.active_factors import load_data
        af_ver = load_data().get("version")
    except Exception:
        pass
    # 死因 Top3（从 l1_log.csv 聚合本日 gate 分布）
    death = {}
    try:
        import csv
        lp = STATE / "l1_log.csv"
        if lp.exists():
            for r in csv.DictReader(open(lp, encoding="utf-8")):
                g = r.get("gate", "")
                if g and g not in ("g0-g4", "?", "L1失败"):
                    death[f"g{g}"] = death.get(f"g{g}", 0) + 1
    except Exception:
        pass
    top_death = sorted(death.items(), key=lambda x: -x[1])[:3]

    lines = [f"【因子挖掘日报 {today}】"]
    lines.append(f"今日 {n_runs} 个 run｜生成入池 {total_added}｜L3 启用 {total_enabled}｜active_factors v{af_ver}")
    if top_death:
        lines.append("死因Top3: " + " ".join(f"g{g}×{v}" for g, v in top_death))
    else:
        lines.append("死因Top3: （本日无 gate 拦截记录）")
    if alerts:
        lines.append("⚠️ 告警 " + str(len(alerts)) + " 条: " + " | ".join(a.get("msg", "")[:40] for a in alerts[:3]))
    else:
        lines.append("✅ 无告警")
    cost = "API 成本查 dashboard"
    lines.append("成本: " + cost)
    lines.append("详情: dashboard.html")
    return lines

def main():
    lines = build_daily()
    text = "\n".join(lines)
    RUNS.mkdir(exist_ok=True)
    (RUNS / "daily_card.md").write_text(text, encoding="utf-8")
    print(text)
    # 重生成 dashboard.html（如存在 build_dashboard）
    try:
        from report.build_dashboard import build_dashboard_html
        build_dashboard_html()
    except Exception as e:
        print(f"(dashboard 生成跳过: {e})")

if __name__ == "__main__":
    main()
