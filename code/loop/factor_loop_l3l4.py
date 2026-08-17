#!/usr/bin/env python3
"""四层 loop 之 L3(回测评估) + L4(实盘验证) 实现

宪法第三章对应条款：
- L3: 训练集(2021-2024)回测 → 验证集(2025-2026)复核 / 动态阈值(0.5%+0.05lnN) / 权重归一化+总权重≤0.5+迭代剔除 / 验证集<20笔降级
- L4: SPRT(μ0=0, μ1=预期, 边界±2.94) / 偏差绝对值地板2% / regime感知 / 样本分级 / 降级因子预期=0
"""
import json, math, sys, time
from pathlib import Path
from datetime import date
import numpy as np

DATA_DIR = Path(r"D:\quant_data")
STATE_DIR = DATA_DIR / "loop_state"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest.backtest_engine import run_backtest

# 基线（宪法第一章实测）
BASELINE = {
    "train": {"total_ret_pct": 4.80, "n_trades": 97, "win_rate": 27.8, "pl_ratio": 3.21, "max_dd_pct": -5.68},
    "valid": {"total_ret_pct": 1.85, "n_trades": 9, "win_rate": 33.3, "pl_ratio": 3.56, "max_dd_pct": -2.15},
}

# ============ L3: 权重计算（含总权重约束与迭代剔除） ============
def calc_weights(pool):
    """pool: 启用因子列表 [{name, icir, half_life}]。返回 {name: weight}（缩放后）
    L3 文档第四章修正：
    - ICIR 优先用可交易域口径 icir_tradable（缺陷 3，全域 ICIR 含不可买样本假 alpha）
    - half_life_unknown 按 short_lived 保守处理，封顶 0.04（缺陷 L3-4）
    """
    if not pool:
        return {}
    ics = [abs(p.get("icir_tradable") or p["icir"]) for p in pool]
    med = float(np.median(ics)) if ics else 0.05
    weights = {}
    for p in pool:
        icir = abs(p.get("icir_tradable") or p["icir"])
        w = 0.05 * icir / max(med, 1e-6)
        w = min(w, 0.10)                        # 封顶
        if p.get("short_lived") or p.get("half_life_unknown"):
            w = min(w, 0.04)                    # 短寿命/未知寿命截断法封顶 0.04，不保下限
        else:
            w = max(w, 0.02)                    # 下限（仅正常因子）
        weights[p["name"]] = w
    # 总权重 ≤0.5，超限等比例缩放；缩放后 <0.02 剔除再缩放（迭代收敛）
    for _ in range(20):
        total = sum(weights.values())
        if total <= 0.5:
            break
        scale = 0.5 / total
        weights = {k: v * scale for k, v in weights.items()}
        # 剔除跌破下限的（正常因子 <0.02；短寿命 <0.015 视为无意义）
        drop = [k for k, v in weights.items() if v < (0.02 if not any(p["name"]==k and p.get("short_lived") for p in pool) else 0.015)]
        if not drop:
            break
        for k in drop:
            weights.pop(k, None)
    return {k: round(v, 4) for k, v in weights.items()}

def dynamic_threshold(N):
    """多重检验动态阈值：0.5% + 0.05×ln(N)"""
    return 0.5 + 0.05 * math.log(max(N, 1))

# ============ L3: 单因子回测判定 ============
def l3_evaluate(cand, cumulative_tested, extra_factors=None, verbose=True):
    """候选因子过 L3：训练集+验证集回测，动态阈值判定。
    返回 (状态, 报告dict)。状态: 启用/回滚/观察
    """
    name = cand["name"]
    expr = cand["expr"]
    # 构造注入：expr 转 rank 表达式（打分用）
    injected = {name: (f"({expr}).rank().over('日期')", 0.05)}
    if extra_factors:
        injected.update(extra_factors)
    # 训练集回测
    train_m = run_backtest(extra_factors=injected, start_year=2021, end_year=2024, verbose=False)
    # 验证集回测
    valid_m = run_backtest(extra_factors=injected, start_year=2025, end_year=2026, verbose=False)
    # N = Σn_peek（L3 文档缺陷 2：旧口径按候选个数累加，低估修正轮窥视次数）
    N = cumulative_tested + cand.get("n_peek", 1)
    thr = dynamic_threshold(N)
    train_gain = train_m["total_ret_pct"] - BASELINE["train"]["total_ret_pct"]
    valid_gain = valid_m["total_ret_pct"] - BASELINE["valid"]["total_ret_pct"]
    # 分段披露（L3 文档缺陷 4）：2021-2023 设计段 / 2024 内层留出（已被 L1 消费）
    seg_design = run_backtest(extra_factors=injected, start_year=2021, end_year=2023, verbose=False)
    seg_2024 = run_backtest(extra_factors=injected, start_year=2024, end_year=2024, verbose=False)
    report = {
        "name": name, "N": N, "N_effective": N, "threshold": round(thr, 3),
        "train": train_m, "valid": valid_m,
        "train_gain": round(train_gain, 2), "valid_gain": round(valid_gain, 2),
        "seg": {
            "design_2021_2023": round(seg_design["total_ret_pct"], 2),
            "holdout_2024": round(seg_2024["total_ret_pct"], 2),
            "valid_2025_2026": round(valid_m["total_ret_pct"], 2),
            "note": "2024 已被 L1 用作 G4 内层留出，非独立 OOS",
        },
    }
    # 判定
    if train_gain >= thr:
        # 验证集：<20 笔降级（仅不恶化检查 ≥-2%）；≥20 笔必须为正
        if valid_m["n_trades"] < 20:
            valid_ok = valid_gain >= -2.0
            report["valid_degraded"] = True
        else:
            valid_ok = valid_gain >= 0
        if valid_ok:
            status = "启用"
        else:
            status = "回滚"
            report["reason"] = f"验证集恶化: gain={valid_gain}%"
    else:
        status = "回滚"
        report["reason"] = f"训练集增益不足: {train_gain}% < {thr}%"
    if verbose:
        print(f"    [L3] {name}: 训练增益={train_gain}% (阈值{thr}%) 验证增益={valid_gain}% "
              f"验证笔数={valid_m['n_trades']} → {status}")
    return status, report

# ============ L4: SPRT 序贯检验 ============
def sprt_decision(returns, mu1, sigma=None, alpha=0.05, beta=0.05):
    """SPRT 判定。returns: 已实现收益列表。mu1: H1 预期收益。
    返回 (决策, lnLR): '启用'/'回滚'/'观察'
    """
    if not returns:
        return "观察", 0.0
    r = np.array(returns, dtype=float)
    n = len(r)
    if sigma is None or sigma <= 0:
        sigma = max(np.std(r), 0.01)
    # H0: μ0=0, H1: μ1
    mu0 = 0.0
    # 对数似然比：Σ [ (xi-μ0)² - (xi-μ1)² ] / 2σ²
    lnLR = np.sum(((r - mu0) ** 2 - (r - mu1) ** 2) / (2 * sigma ** 2))
    A = math.log((1 - beta) / alpha)   # ≈2.94
    B = math.log(beta / (1 - alpha))   # ≈-2.94
    if lnLR >= A:
        return "启用", lnLR
    if lnLR <= B:
        return "回滚", lnLR
    return "观察", lnLR

def l4_evaluate(factor, paper_trades, sigma_prior=None, verbose=True):
    """因子实盘验证。paper_trades: [{pnl_pct, date}]。
    返回 (状态, 报告)
    """
    name = factor["name"]
    rets = [t.get("pnl_pct", 0) for t in paper_trades if t.get("factor") == name]
    n = len(rets)
    # 样本分级（L4 文档缺陷 2：half_life_unknown 按短寿命保守处理，10 交易日+5 笔）
    short_lived = factor.get("short_lived", False) or factor.get("half_life_unknown", False)
    min_n = 5 if short_lived else 10
    if n < 5:
        return "观察", {"n": n, "reason": "样本不足(早期预警线未到)"}
    # 预期基准：降级验证因子 → 0
    expected = factor.get("l4_expected", 0.0)
    if factor.get("degraded_enabled"):
        expected = 0.0
    # SPRT
    sigma = sigma_prior or np.std(rets) if len(rets) > 1 else 0.02
    decision, lnLR = sprt_decision(rets, expected / 100.0, sigma=sigma)
    # 偏差检测（绝对值地板 2%）
    realized = np.mean(rets) if rets else 0
    exp_pct = expected
    denom = max(abs(exp_pct), 2.0)
    dev = (realized - exp_pct) / denom * 100
    report = {"n": n, "realized_pct": round(realized, 3), "expected_pct": exp_pct,
              "deviation_pct": round(dev, 1), "lnLR": round(lnLR, 3),
              "sprt": decision, "short_lived": short_lived}
    # regime 感知：deviation 超 ±50% 且 SPRT 倾向回滚 → 回滚；否则观察
    if decision == "启用" and abs(dev) <= 50:
        status = "实盘确认"
    elif decision == "回滚" and abs(dev) > 50:
        status = "回滚"
    else:
        status = "观察"
    report["status"] = status
    if verbose:
        print(f"    [L4] {name}: n={n} 实盘={realized:.3f}% 预期={exp_pct}% 偏差={dev:.0f}% SPRT={decision} → {status}")
    return status, report

# ============ Dashboard ============
def update_dashboard(pool, history, l4_log, state_dir=STATE_DIR):
    """刷新 dashboard.json（池健康分）"""
    n_enabled = sum(1 for p in pool if p["status"] in ("启用", "实盘确认"))
    if not pool:
        score = 100.0
    else:
        avg_hl = np.mean([p.get("half_life", 12) for p in pool])
        # 健康分 = 加权：启用数(30%) + 半衰期(30%) + 平均ICIR(20%) + 实盘偏差(20%)
        s_hl = min(avg_hl / 12, 1.0) * 30
        s_ic = min(np.mean([abs(p.get("icir", 0)) for p in pool]) / 0.3, 1.0) * 20
        s_act = min(n_enabled / 10, 1.0) * 30
        s_live = 20.0
        if l4_log:
            last = l4_log[-1]
            if abs(last.get("deviation_pct", 0)) <= 50:
                s_live = 20.0
            else:
                s_live = 10.0
        score = s_hl + s_ic + s_act + s_live
    dash = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pool_size": len(pool),
        "enabled": n_enabled,
        "health_score": round(score, 1),
        "avg_half_life": round(float(np.mean([p.get("half_life", 12) for p in pool])), 1) if pool else 0,
        "last_backtest": history[-1] if history else None,
        "last_l4": l4_log[-1] if l4_log else None,
    }
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    (state_dir / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, indent=2), encoding="utf-8")
    return dash

if __name__ == "__main__":
    print("=== L3+L4 模块自测 ===")
    # 权重计算
    pool = [
        {"name": "f1", "icir": 0.45, "half_life": 12},
        {"name": "f2", "icir": 0.30, "half_life": 3, "short_lived": True},
        {"name": "f3", "icir": 0.55, "half_life": 9},
    ]
    w = calc_weights(pool)
    print(f"权重: {w}, 总和={sum(w.values()):.3f}")
    print(f"动态阈值 N=10: {dynamic_threshold(10):.3f}%")
    # SPRT
    dec, lr = sprt_decision([2.0, 3.0, 1.5, 2.5, 2.0], 0.02, sigma=0.02)
    print(f"SPRT 正收益序列: {dec} (lnLR={lr:.2f})")
    dec2, lr2 = sprt_decision([-2.0, -3.0, -1.5, -2.5, -2.0], 0.02, sigma=0.02)
    print(f"SPRT 负收益序列: {dec2} (lnLR={lr2:.2f})")
