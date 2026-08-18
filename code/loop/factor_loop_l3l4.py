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
def _icir(p):
    """统一取因子 ICIR（改造 2.1：池内因子的 ICIR 在 ic_metrics.icir，顶层没有）。
    优先可交易域 icir_tradable，否则全域 ic_metrics.icir，再否则 0.0"""
    t = p.get("icir_tradable")
    if t is not None:
        return abs(float(t))
    m = p.get("ic_metrics") or {}
    v = m.get("icir")
    if v is not None:
        return abs(float(v))
    return 0.0

def calc_weights(pool):
    """pool: 启用因子列表 [{name, icir, half_life}]。返回 {name: weight}（缩放后）
    L3 文档第四章修正：
    - ICIR 优先用可交易域口径 icir_tradable（缺陷 3，全域 ICIR 含不可买样本假 alpha）
    - half_life_unknown 按 short_lived 保守处理，封顶 0.04（缺陷 L3-4）
    改造 2.1：用 _icir 统一取数（原来 p["icir"] 顶层 KeyError），tradable 为空记 icir_fallback
    """
    if not pool:
        return {}
    ics = [_icir(p) for p in pool]
    # 可交易域 ICIR 缺失降级到全域（口径降级要可见）
    for p in pool:
        if p.get("icir_tradable") is None and "icir_fallback" not in p:
            p["icir_fallback"] = "full_domain"
    med = float(np.median(ics)) if any(ics) else 0.05
    weights = {}
    for p in pool:
        icir = _icir(p)
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
# 改造2.0 3.4：L3 回测结果缓存（同一批候选共享已启用因子注入集，命中直接复用 metrics）
# 键 = hash(候选注入 + 年份区间)；模块级 dict 在单 run 内共享，命中率高
_bt_cache = {}

def _bt_key(injected, start_year, end_year):
    import hashlib as _hl
    # 注入集序列化（+ 各权重）作为键
    parts = sorted(f"{n}:{e}:{w:.5f}" for n, (e, w) in injected.items())
    return _hl.sha1(f"{parts}|{start_year}|{end_year}|1.0".encode()).hexdigest()[:24]

def _cached_backtest(injected, start_year, end_year, return_by_year=False):
    """改造2.0 3.4：带缓存的全量回测"""
    key = _bt_key(injected, start_year, end_year)
    rkey = f"{key}|{return_by_year}"
    if rkey in _bt_cache:
        return _bt_cache[rkey]
    m = run_backtest(extra_factors=injected, start_year=start_year, end_year=end_year,
                     verbose=False, return_by_year=return_by_year)
    # 只缓存总指标（year_ret 也在 m 里，随 return_by_year 区分）
    _bt_cache[rkey] = m
    return m

def l3_evaluate(cand, cumulative_tested, extra_factors=None, verbose=True, use_bt_cache=True):
    """候选因子过 L3：训练集+验证集回测，动态阈值判定。
    改造2.0 3.4：use_bt_cache 用磁盘/mem 缓存回测结果（同批共享注入集命中率高）
    返回 (状态, 报告dict)。状态: 启用/回滚/观察
    """
    name = cand["name"]
    expr = cand["expr"]
    # 构造注入：expr 转 rank 表达式（打分用）
    injected = {name: (f"({expr}).rank().over('日期')", 0.05)}
    if extra_factors:
        injected.update(extra_factors)
    _bt = _cached_backtest if use_bt_cache else \
        (lambda inj, sy, ey, return_by_year=False: run_backtest(extra_factors=inj, start_year=sy,
                                                                end_year=ey, verbose=False, return_by_year=return_by_year))
    # 训练集回测（改造 C22：return_by_year 一次得全段，分段披露从分年结果拆，省 2 次独立回测）
    train_m = _bt(injected, 2021, 2024, return_by_year=True)
    # 验证集回测
    valid_m = _bt(injected, 2025, 2026, return_by_year=False)
    # N = Σn_peek（L3 文档缺陷 2：旧口径按候选个数累加，低估修正轮窥视次数）
    N = cumulative_tested + cand.get("n_peek", 1)
    thr = dynamic_threshold(N)
    train_gain = train_m["total_ret_pct"] - BASELINE["train"]["total_ret_pct"]
    valid_gain = valid_m["total_ret_pct"] - BASELINE["valid"]["total_ret_pct"]
    # 分段披露（L1 已用 2024 做 G4，非独立 OOS）：从 train_m.year_ret 拆 2021-2023 / 2024
    yr = train_m.get("year_ret", {})
    seg_design = round(sum(v for y, v in yr.items() if y <= 2023), 2)
    seg_2024 = round(yr.get(2024, 0.0), 2)
    report = {
        "name": name, "N": N, "N_effective": N, "threshold": round(thr, 3),
        "train": train_m, "valid": valid_m,
        "train_gain": round(train_gain, 2), "valid_gain": round(valid_gain, 2),
        "seg": {
            "design_2021_2023": seg_design,  # 改造2.0缺陷1：已是float，直接写（原 dict 下标 TypeError）
            "holdout_2024": seg_2024,
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
    改造 2.3：pnl_pct 从 csv 读来是字符串 → float() 清洗，丢弃不可解析行记 bad_rows
    改造 2.2：多因子归因——factor 列用 | 分隔，name in split('|') 命中即可；共享样本记 shared_attr
    改造 3.1：min_n 样本分级真正生效（原来只 if n<5，短寿命 5 笔未生效）
    返回 (状态, 报告)
    """
    name = factor["name"]
    # 2.3: float 清洗（csv 字符串 → 数值；丢弃坏行）
    bad_rows = 0
    rets = []
    shared_attr = 0
    for t in paper_trades:
        # 2.2: 多因子归因匹配（factor 列可能 "s1|s6"）
        f_col = t.get("factor") or ""
        names = [x.strip() for x in str(f_col).split("|") if x.strip()]
        if name not in names:
            continue
        if len(names) > 1:
            shared_attr += 1  # 共享归因（SPRT 单因子序贯，不要求互斥，但事后要打折）
        try:
            rets.append(float(t.get("pnl_pct", 0)))
        except (TypeError, ValueError):
            bad_rows += 1
    n = len(rets)
    # 3.1: 样本分级生效（half_life_unknown 按短寿命保守处理，10 交易日+5 笔）
    short_lived = factor.get("short_lived", False) or factor.get("half_life_unknown", False)
    min_n = 5 if short_lived else 10
    if n < min_n:
        return "观察", {"n": n, "min_n": min_n, "reason": f"样本不足({n}<{min_n}笔)", "bad_rows": bad_rows}
    # 改造2.0缺陷6：量纲统一——rets 从 live_trades 读的是小数(如0.072)，统一 ×100 为百分比，
    # 与 expected(百分数) 同量纲，避免 dev 算出 ≈-98% 的假偏差
    rets = [r * 100.0 for r in rets]
    # 预期基准：降级验证因子 → 0
    expected = factor.get("l4_expected", 0.0)
    if factor.get("degraded_enabled"):
        expected = 0.0
    # SPRT（2.3: 明确 sigma 优先级；量纲统一为百分比后 mu1 直接传 expected）
    if sigma_prior is not None and sigma_prior > 0:
        sigma = sigma_prior
    elif len(rets) > 1:
        sigma = max(float(np.std(rets)), 0.01)
    else:
        sigma = 0.02
    decision, lnLR = sprt_decision(rets, expected, sigma=sigma)  # mu1 百分数（与 rets 同量纲）
    # 偏差检测（绝对值地板 2%）
    realized = float(np.mean(rets)) if rets else 0.0
    exp_pct = expected
    denom = max(abs(exp_pct), 2.0)
    dev = (realized - exp_pct) / denom * 100
    report = {"n": n, "realized_pct": round(realized, 3), "expected_pct": exp_pct,
              "deviation_pct": round(dev, 1), "lnLR": round(lnLR, 3),
              "sprt": decision, "short_lived": short_lived,
              "shared_attr": shared_attr}  # 改造2.2：共享归因样本数（事后打折依据）
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
    """刷新 dashboard.json（池健康分）
    改造 2.2：half_life=None（L2 半衰期算不出）不再 TypeError，空值项记 None + coverage"""
    n_enabled = sum(1 for p in pool if p["status"] in ("启用", "实盘确认"))
    # 收集非空 half_life（None = 半衰期算不出，不参与均值，但要统计覆盖率）
    hls = [h for p in pool if (h := p.get("half_life")) is not None]
    hlf_cov = round(len(hls) / len(pool), 2) if pool else 0.0
    if not pool:
        score = 100.0
    else:
        avg_hl = float(np.mean(hls)) if hls else None
        # 健康分 = 加权：启用数(30%) + 半衰期(30%) + 平均ICIR(20%) + 实盘偏差(20%)
        s_hl = (min(avg_hl / 12, 1.0) * 30) if hls else 0.0  # 半衰期全缺失 → 该项 0 分
        s_ic = min(float(np.mean([_icir(p) for p in pool])) / 0.3, 1.0) * 20
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
        "avg_half_life": round(float(avg_hl), 1) if avg_hl is not None else None,
        "half_life_coverage": hlf_cov,  # 改造 2.2：半衰期覆盖率（缺失项单独暴露）
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
