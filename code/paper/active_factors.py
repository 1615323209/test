#!/usr/bin/env python3
"""active_factors.json——打分因子的单一真相源（改造2.0 2.1）

收敛 daily_picks.W / backtest_engine.BASE_FACTORS / checkpoint.pool[].weight
三处各自维护"当前用哪些因子"的问题。原子写 + version 单调 + 3 份 .bak。
消费端：daily_picks / backtest_engine / l3_evaluate 注入，全部去掉硬编码。
"""
import json, os, time, shutil
from pathlib import Path

PATH = Path(r"D:\quant_data\active_factors.json")

# ---- v7 基线内置常量（active_factors 缺失/校验失败时回退，不可静默）----
V7_BASELINE = [
    {"name": "s1", "expr": "(-pl.col('ret_5d') * pl.col('turn_ma5'))", "weight": 0.25,
     "origin": "v7_baseline", "status": "pin", "since": "2026-08-01"},
    {"name": "s2", "expr": "(pl.col('ma5_dist') * pl.col('turn_ma5'))", "weight": 0.15,
     "origin": "v7_baseline", "status": "pin", "since": "2026-08-01"},
    {"name": "s3", "expr": "((pl.col('close') / pl.col('ma_20')) - 1)", "weight": 0.15,
     "origin": "v7_baseline", "status": "pin", "since": "2026-08-01"},
    {"name": "s5", "expr": "pl.col('turn_ratio')", "weight": 0.20,
     "origin": "v7_baseline", "status": "pin", "since": "2026-08-01"},
    {"name": "s6", "expr": "(pl.col('macd_dif') - pl.col('macd_dea'))", "weight": 0.25,
     "origin": "v7_baseline", "status": "pin", "since": "2026-08-01"},
]
MAX_TOTAL_WEIGHT = 0.5

def _default():
    return {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "v7_baseline",
        "total_weight": round(sum(f["weight"] for f in V7_BASELINE), 2),
        "factors": list(V7_BASELINE),
        "retired": [],
    }

def _atomic_write(data):
    """原子写：temp → fsync → replace；version 单调 + 保留 3 份 .bak"""
    try:
        old = load_data()
        data["version"] = old.get("version", 0) + 1 if old else data.get("version", 1)
    except Exception:
        data["version"] = data.get("version", 1)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["total_weight"] = round(sum(f.get("weight", 0) for f in data.get("factors", [])), 2)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 保留 3 份 .bak
    if PATH.exists():
        for i in range(3, 0, -1):
            src = PATH.with_suffix(f".bak{i}")
            if (i > 1) and PATH.with_suffix(f".bak{i-1}").exists():
                shutil.copy2(PATH.with_suffix(f".bak{i-1}"), src)
        shutil.copy2(PATH, PATH.with_suffix(".bak1"))
    os.replace(tmp, PATH)

def load_data():
    """读 active_factors.json（原子读；损坏时回退默认，记 flag）"""
    if not PATH.exists():
        d = _default()
        try:
            _atomic_write(d)
        except Exception:
            pass
        return d
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default()

def get_active():
    """当前激活因子列表（灰度+启用+pin），消费端构造打分用。
    表达式安全：调用方必须 safe_compile 后再用（不信任文件内容，硬要求）"""
    d = load_data()
    return d.get("factors", []), d

def safe_expr(expr):
    """对读到的 expr 做沙箱校验（改造2.0 硬要求：消费端也过沙箱）。
    返回 (polars_expr, err)；err 非 None 则该因子不可用（回退时跳过）"""
    from loop.expr_sandbox import safe_compile
    return safe_compile(expr)

def set_factor(name, **fields):
    """更新某因子的字段；不存在则新增。原子写。
    改造2.0 灰度规则由调用方(l3/l4)决定 status/weight，这里只落盘"""
    data = load_data()
    factors = data.setdefault("factors", [])
    for f in factors:
        if f.get("name") == name:
            f.update(fields)
            break
    else:
        factors.append({"name": name, "weight": 0.02, "status": "灰度", **fields})
    _atomic_write(data)
    return data

def retire(name, reason, at=None, last_weight=0.0):
    """移入 retired（灰度期满/回滚），权重归零"""
    data = load_data()
    factors = data.setdefault("factors", [])
    data.setdefault("retired", [])
    for f in factors:
        if f.get("name") == name:
            data["retired"].append({"name": name, "reason": reason,
                                    "at": at or time.strftime("%Y-%m-%d"),
                                    "last_weight": f.get("weight", last_weight)})
            factors.remove(f)
            break
    _atomic_write(data)
    return data

def pin_names():
    """pin（不参与迭代剔除）的因子名集合"""
    d = load_data()
    return {f["name"] for f in d.get("factors", []) if f.get("status") == "pin"}
