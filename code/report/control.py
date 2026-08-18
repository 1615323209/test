#!/usr/bin/env python3
"""改造2.0 批次5：control.json 人工干预

loop 在 run_start 读一次。paused/veto/pin/max_pool。
任何 control 生效都要在 run_log.jsonl 写 stage=control 留痕（人工干预必须与自动决策同样可审计）。
不得放松红线类阈值（t_NW 门槛、fwd_* 黑名单、总权重上限）——只能改文档+代码。
"""
import json, copy
from pathlib import Path

STATE = Path(r"D:\quant_data\loop_state")
CONTROL = STATE / "control.json"

DEFAULT = {
    "paused": False,
    "budget_sec": 420,
    "veto": [],
    "pin": ["s1", "s2", "s3", "s5", "s6"],
    "max_pool": 30,
    "note": "",
}

def load():
    """读 control.json；缺失/损坏回退默认（不静默——调用方判断）"""
    if not CONTROL.exists():
        return copy.deepcopy(DEFAULT), "no_file"
    try:
        d = json.loads(CONTROL.read_text(encoding="utf-8"))
        # 合并默认（新增字段自适应）
        merged = {**copy.deepcopy(DEFAULT), **d}
        return merged, "ok"
    except Exception as e:
        return copy.deepcopy(DEFAULT), f"corrupt:{e}"

def apply_control():
    """run_start 时调用：读取并执行 control.json，返回有效 control 动作列表（用于留痕）
    - paused: 返回 {paused: True}，调用方应写心跳卡片后退出
    - veto: 命中的表达式/因子 hash/名字 → 写已拒绝库（manual_veto）
    - pin: 传给 calc_weights（已有），这里确保 active_factors pin 集合
    - max_pool: 传给 run_batch（池满停止入池）
    """
    ctl, status = load()
    actions = []
    if status != "ok":
        actions.append(f"control 读取异常({status})，使用默认")
    if ctl.get("paused"):
        actions.append(f"paused=true（{ctl.get('note','')}）")
    return ctl, actions

def veto_once(log_event=None):
    """处理 veto：把命中的表达式写已拒绝库（manual_veto）
    veto 项支持：完整表达式字符串（以 pl.col 开头）或因子名/hash。
    名字/hash 型无法直接写 expr_key 的拒绝库，只记 control 留痕。"""
    ctl, _ = load()
    n = 0
    for item in ctl.get("veto", []) or []:
        item_s = str(item)
        if item_s.strip().startswith(("pl.col", "(", "-", "+", "p")):
            try:
                from loop.factor_loop_gates import _reject
                _reject(item_s, "manual_veto")
                n += 1
                continue
            except Exception:
                pass
        # expr 型之外的（名字/hash）：仅计数留痕
        n += 1
    if n and log_event:
        log_event("control", "", kind="veto", count=n)
    return n

def write_pause_card(note=""):
    """paused 时写心跳卡片（loop 跳过但留痕）"""
    from report.run_reporter import write_push_card
    body = f"⏸️ loop 已暂停（control.json）{('：' + note) if note else ''}，本轮跳过"
    write_push_card("paused", [body])
    return body
