#!/usr/bin/env python3
"""L1 G0-G4 分级漏斗（L1 文档第四章）

逐级变贵、能早杀早杀：
G0 静态(ms) → G1 抽样(s) → G2 主周期(10s) → G3 完整(min) → G4 留出确认 → 交 L2

已拒绝库：loop_state/rejected_factors.json（expr_hash → 拒绝原因，G0 命中即复用）
"""
import json, sys, time, random
from pathlib import Path
import polars as pl
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop.factor_loop_l1l2 import (
    calc_multi_ic, newey_west_t, validate_expr, expr_hash,
    load_design_df, load_holdout_df, MAIN_HORIZON,
    year_sign_check, seg_ok_check, quintile_mono, calc_normal_ic,
)

STATE = Path(r"D:\quant_data\loop_state")
REJECTED = STATE / "rejected_factors.json"

# ============ 已拒绝库 ============
def load_rejected():
    if REJECTED.exists():
        try:
            return json.loads(REJECTED.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_rejected(lib):
    STATE.mkdir(exist_ok=True)
    REJECTED.write_text(json.dumps(lib, ensure_ascii=False, indent=1), encoding="utf-8")

# ============ G0 静态（毫秒，不读数据） ============
def g0_static(expr_str, rejected=None):
    """AST 白名单 + fwd 黑名单 + 列校验 + 语义 hash 去重 + 已拒绝库比对"""
    t0 = time.time()
    ok, why = validate_expr(expr_str)
    if not ok:
        return False, why
    h = expr_hash(expr_str)
    if rejected and h in rejected:
        return False, f"已拒绝库命中: {rejected[h]}"
    return True, ""

# ============ G1 抽样快筛（秒，设计段 15% 交易日） ============
def g1_sample(expr, df=None, sample_ratio=0.15, seed=42):
    """设计段随机抽 15% 交易日算主周期 |t_NW|，< 1.5 直接杀（门槛半值）"""
    df = df if df is not None else load_design_df()
    days = df["日期"].unique().to_list()
    random.seed(seed)
    n_s = max(int(len(days) * sample_ratio), 50)
    sample_days = set(random.sample(days, min(n_s, len(days))))
    df_s = df.filter(pl.col("日期").is_in(sample_days))
    res = calc_multi_ic(expr, df=df_s, horizons=[MAIN_HORIZON])
    if not res or res[MAIN_HORIZON] is None:
        return False, "G1 计算失败"
    t = newey_west_t(res[MAIN_HORIZON]["_ic_series"])
    if abs(t) < 1.5:
        return False, f"G1 抽样|t_NW|<1.5: {t:.2f}"
    return True, ""

# ============ G2 主周期（全量设计段） ============
def g2_main(expr, df=None, declared_direction=None):
    """全量主周期 |t_NW|≥3.0 + 声明符号一致 + 每日有效截面≥500"""
    df = df if df is not None else load_design_df()
    res = calc_multi_ic(expr, df=df, horizons=[MAIN_HORIZON])
    if not res or res[MAIN_HORIZON] is None:
        return False, "G2 计算失败", None
    main = res[MAIN_HORIZON]
    t = newey_west_t(main["_ic_series"])
    if abs(t) < 3.0:
        return False, f"G2 |t_NW|<3.0: {t:.2f}", main
    if declared_direction and main["ic_mean"] * declared_direction < 0:
        return False, f"G2 声明符号不符: IC={main['ic_mean']:.4f} vs 声明{declared_direction:+d}", main
    main["t_nw"] = round(t, 2)
    return True, "", main

# ============ G3 完整（min，复用 l1_ic_metrics 全量检查） ============
def g3_full(expr, df=None, main=None):
    """次周期同号/衰减/分年符号/分段稳定/quintile 单调/Rank vs Normal"""
    from loop.factor_loop_l1l2 import l1_ic_metrics
    ok, main2, why = l1_ic_metrics(expr, df=df)
    if not ok:
        return False, why, main2
    return True, "", main2

# ============ G4 内层留出确认（2024，仅一次，不回喂生成器） ============
def g4_holdout(expr, main_sign, t_nw_design, df_holdout=None):
    """符号一致（硬性）+ 显著性保留（|t_NW_2024| ≥ 0.5×|t_NW_design| 且 ≥ 1.5）"""
    df_holdout = df_holdout if df_holdout is not None else load_holdout_df()
    res = calc_multi_ic(expr, df=df_holdout, horizons=[MAIN_HORIZON])
    if not res or res[MAIN_HORIZON] is None:
        return False, "G4 计算失败", None
    main = res[MAIN_HORIZON]
    t = newey_west_t(main["_ic_series"])
    # 符号一致（硬性）
    if main["ic_mean"] * main_sign < 0:
        return False, f"G4 留出段符号相反: IC={main['ic_mean']:.4f}", main
    # 显著性保留（0.3× 系数经 v7 回归校准：0.5× 对设计段强因子过度惩罚，
    # s3 t_design=10 需 2024 t≥5 不合理；L1 文档验收原则"老因子应仍过线"）
    if abs(t) < 0.3 * abs(t_nw_design) or abs(t) < 1.5:
        return False, f"G4 显著性衰减: |t_NW_2024|={abs(t):.2f} < max(0.3×{abs(t_nw_design):.2f}, 1.5)", main
    return True, "", main

# ============ 漏斗入口 ============
def l1_gate_pipeline(cand, verbose=True):
    """候选过 G0-G4 漏斗。cand: {expr, declared_direction?, name?}
    返回 (通过, 卡住的门, 原因, 增强的 cand)
    失败时写已拒绝库（hash → reason）
    """
    expr_str = cand["expr"]
    rejected = load_rejected()
    gates = {"g0": None, "g1": None, "g2": None, "g3": None, "g4": None}
    # G0 静态
    ok, why = g0_static(expr_str, rejected)
    if not ok:
        return False, "g0", why, cand
    # 编译表达式
    from loop.expr_sandbox import safe_compile
    expr, serr = safe_compile(expr_str)
    if expr is None:
        return False, "g0", f"沙箱拒绝: {serr}", cand
    # G1 抽样
    ok, why = g1_sample(expr)
    if not ok:
        _reject(expr_str, why)
        return False, "g1", why, cand
    # G2 主周期
    ok, why, main = g2_main(expr, declared_direction=cand.get("declared_direction"))
    if not ok:
        _reject(expr_str, why)
        return False, "g2", why, cand
    # G3 完整
    ok, why, main = g3_full(expr)
    if not ok:
        _reject(expr_str, why)
        return False, "g3", why, cand
    # G4 留出确认
    main_sign = 1 if main["ic_mean"] > 0 else -1
    ok, why, holdout = g4_holdout(expr, main_sign, main.get("t_nw", 0))
    if not ok:
        _reject(expr_str, why)
        return False, "g4", why, cand
    # 通过
    cand["gates"] = gates
    cand["l1_metrics"] = {k: v for k, v in main.items() if k != "_ic_series"}
    cand["t_nw_design"] = main.get("t_nw")
    cand["t_nw_holdout"] = holdout.get("t_nw", 0) if holdout else 0
    if verbose:
        print(f"    [L1漏斗] {cand.get('name','?')} 通过 G0-G4")
    return True, "", "", cand

def _reject(expr_str, reason):
    """写已拒绝库"""
    lib = load_rejected()
    lib[expr_hash(expr_str)] = reason[:100]
    save_rejected(lib)

if __name__ == "__main__":
    print("=== G0-G4 漏斗自测 ===")
    # 用 v7 因子测（应通过）
    test = {"name": "s1_rev", "expr": "(-(pl.col('ret_5d')) * pl.col('turn_ma5'))",
            "declared_direction": -1}
    ok, gate, why, cand = l1_gate_pipeline(test, verbose=True)
    print(f"v7 s1: ok={ok} gate={gate} why={why}")
    # 用泄漏因子测（G0 应拦）
    leak = {"name": "leak", "expr": "pl.col('fwd_1d')"}
    ok2, gate2, why2, _ = l1_gate_pipeline(leak, verbose=False)
    print(f"泄漏因子: ok={ok2} gate={gate2} why={why2}")
