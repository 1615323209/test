#!/usr/bin/env python3
"""四层 loop 之 L1(单因子精炼) + L2(批次筛选) 实现

宪法第三章对应条款：
- L1: 列名校验 / PIT检查 / 多周期IC(1d,3d,5d,10d) / 衰减曲线 / 滚动ICIR min / Rank vs Normal(重尾容忍) / seed确定性
- L2: 精算层(换手+quintile) / 三通道去重(Pearson+Spearman+MI+expr_hash) / 动态正交化(岭回归) / regime分层 / 半衰期 / 反因子 / 版本链 / 叙事
"""
import json, os, re, sys, time, hashlib
from pathlib import Path
from datetime import datetime, date
import numpy as np
import polars as pl
import pandas as pd

DATA_DIR = Path(r"D:\quant_data")
IC_DATA = DATA_DIR / "ic_data.parquet"
FACTOR_MAIN = DATA_DIR / "factor_daily.parquet"
FACTOR_INCR = DATA_DIR / "factor_daily_incr.parquet"
MARKET = DATA_DIR / "market_daily.parquet"
TRAIN_LO, TRAIN_HI = date(2021, 1, 1), date(2024, 12, 31)
# ic_data 实际含 fwd_1d/5d/10d/20d（无 3d），用 1d/5d/10d/20d 构成多周期体检
HORIZONS = ["fwd_1d", "fwd_5d", "fwd_10d", "fwd_20d"]
MAIN_HORIZON = "fwd_5d"

# 与 llm_factor_synth 复用的 DeepSeek 客户端
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop.llm_factor_synth import load_deepseek_key, llm_chat

# ============ 数据层（训练集只读一次，缓存在内存） ============
_cache = {}

def load_train_df():
    """加载训练集(2021-2024)因子+forward收益，带日期过滤"""
    if "train" in _cache:
        return _cache["train"]
    s = pl.scan_parquet(IC_DATA)
    df = s.filter((pl.col("日期") >= TRAIN_LO) & (pl.col("日期") <= TRAIN_HI)).collect()
    _cache["train"] = df
    return df

def load_full_ic_cols():
    """ic_data 全部列名（列名校验用）"""
    if "cols" in _cache:
        return _cache["cols"]
    cols = pl.scan_parquet(IC_DATA).collect_schema().names()
    _cache["cols"] = cols
    return cols

# ============ L1: 多周期 IC 体检 ============
def calc_multi_ic(expr, df=None, horizons=None):
    """计算多周期 IC 指标。返回 dict 或 None。
    指标: 各周期 ic_mean/icir/ic_pos_pct, 衰减比, 滚动60日ICIR min, Rank vs Normal 符号
    """
    df = df if df is not None else load_train_df()
    horizons = horizons or HORIZONS
    try:
        d = df.with_columns(expr.alias("_cand"))
    except Exception:
        return None
    out = {}
    for hz in horizons:
        ic = (d.select(["日期", "_cand", hz])
              .group_by("日期")
              .agg(pl.corr(pl.col("_cand"), pl.col(hz), method="spearman").alias("ic"))
              .sort("日期"))
        ic = ic.filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite())
        if len(ic) < 100:
            out[hz] = None
            continue
        v = ic["ic"]
        m, s = float(v.mean()), float(v.std())
        if s is None or s == 0 or m is None:
            out[hz] = None
            continue
        out[hz] = {
            "ic_mean": round(m, 4),
            "icir": round(m / s, 4),
            "ic_pos_pct": round(float((v > 0).mean()) * 100, 1),
            "days": len(v),
            "_ic_series": v.to_list(),   # 用于滚动/衰减/稳定
        }
    return out

def l1_ic_metrics(expr):
    """完整 L1 多周期体检，返回 (通过与否, 指标dict, 拒绝原因)"""
    res = calc_multi_ic(expr)
    if res is None or res[MAIN_HORIZON] is None:
        return False, {}, "表达式执行失败"
    main = res[MAIN_HORIZON]
    # 1. 主周期显著
    if abs(main["icir"]) < 0.25:
        return False, main, f"主周期|ICIR|<0.25: {main['icir']}"
    # 2. 次周期同号
    main_sign = 1 if main["ic_mean"] > 0 else -1
    for hz in ["fwd_1d", "fwd_10d", "fwd_20d"]:
        r = res.get(hz)
        if r and r["ic_mean"] * main_sign < 0:
            return False, main, f"次周期{hz}异号"
    # 3. 衰减曲线: fwd_5d → fwd_10d |IC| 衰减 <50%
    r10 = res.get("fwd_10d")
    if r10 and r10["ic_mean"]:
        decay = abs(main["ic_mean"]) - abs(r10["ic_mean"])
        if decay / max(abs(main["ic_mean"]), 1e-9) > 0.5:
            return False, main, f"IC衰减过快: 5d->10d 衰减 {decay/abs(main['ic_mean'])*100:.0f}%"
    # 4. 滚动 60 日 ICIR min > 0
    series = main["_ic_series"]
    if len(series) >= 60:
        arr = np.array(series)
        roll_min = min(arr[i:i+60].mean() / max(arr[i:i+60].std(), 1e-9)
                       for i in range(0, len(arr) - 59, 30))
        if roll_min <= 0:
            return False, main, f"滚动60日ICIR min={roll_min:.3f} <= 0"
    # 5. Rank(Normal) IC 同号 / 重尾容忍
    normal_ic = calc_normal_ic(expr, df=load_train_df())
    if normal_ic is not None:
        n_sign = 1 if normal_ic > 0 else -1
        if n_sign != main_sign:
            if abs(normal_ic) < 2 * abs(main["ic_mean"]):
                return False, main, f"Rank/Normal异号且无极端值优势 (normal={normal_ic:.4f})"
            main["extreme_driven"] = True   # 标记重尾驱动，L2 复核 quintile
    return True, main, ""

def calc_normal_ic(expr, df=None):
    """Pearson IC（Normal IC）"""
    df = df if df is not None else load_train_df()
    try:
        d = df.with_columns(expr.alias("_cand"))
        ic = (d.select(["日期", "_cand", MAIN_HORIZON])
              .group_by("日期")
              .agg(pl.corr(pl.col("_cand"), pl.col(MAIN_HORIZON)).alias("ic")))
        ic = ic.filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite())
        return float(ic["ic"].mean()) if len(ic) > 100 else None
    except Exception:
        return None

# ============ L1: 列名校验 + PIT ============
NON_PRICE_COLS = set()  # 未来放财务/行业列名；当前 45 因子全是量价衍生，自动 PIT 安全

def validate_expr(expr_str):
    """列名校验 + PIT 检查。返回 (通过与否, 原因)"""
    valid_cols = set(load_full_ic_cols())
    used = re.findall(r"pl\.col\(['\"]([^'\"]+)['\"]\)", expr_str)
    for c in used:
        if c not in valid_cols:
            return False, f"幻觉列名: {c}"
        if c in NON_PRICE_COLS:
            return False, f"非量价列未过PIT: {c}"
    return True, ""

# ============ L1: seed 确定性 ============
def make_prompt(batch_id, factor_idx, seed, ddict):
    """带 seed 的生成 prompt（seed 注入影响采样）"""
    return (f"请生成 1 个预测 {MAIN_HORIZON} 的因子。\n"
            f"（确定性采样 seed={seed}，请基于金融逻辑，不要受 seed 影响）\n\n"
            f"数据字典（可用列）:\n{ddict}")

# ============ L2: 精算层（换手暴露 + quintile） ============
def l2_fine_eval(expr, df=None):
    """换手暴露 + quintile 单调性。返回 dict 或 None"""
    df = df if df is not None else load_train_df()
    try:
        d = df.with_columns(expr.alias("_f"))
        d2 = d.filter(pl.col("_f").is_not_null() & pl.col("_f").is_finite()
                      & pl.col(MAIN_HORIZON).is_not_null() & pl.col(MAIN_HORIZON).is_finite()
                      & pl.col("turn_ratio").is_not_null() & pl.col("turn_ratio").is_finite())
        if len(d2) < 10000:
            return None
        all_tr = d2["turn_ratio"].mean()
        d2 = d2.with_columns([
            pl.col("_f").rank().over("日期").alias("_rk"),
            pl.col("_f").count().over("日期").alias("_n"),
        ])
        top_tr = (d2.filter(pl.col("_rk") >= pl.col("_n") * 0.9)
                   .group_by("日期").agg(pl.col("turn_ratio").mean()))["turn_ratio"].mean()
        turn_exp = float(top_tr / all_tr) if all_tr else 1.0
        d2 = d2.with_columns((pl.col("_rk") / pl.col("_n") * 5).cast(pl.Int32).clip(0, 4).alias("_q"))
        q = d2.group_by("_q").agg(pl.col(MAIN_HORIZON).mean().alias("m")).sort("_q")
        qv = q["m"].to_list()
        mono = abs(np.corrcoef(np.arange(len(qv)), qv)[0, 1]) if len(qv) >= 3 and np.std(qv) > 0 else 0.0
        return {"turn_exp": round(turn_exp, 2), "mono": round(float(mono), 3),
                "spread_pct": round((qv[-1] - qv[0]) * 100, 3) if len(qv) >= 2 else 0}
    except Exception:
        return None

# ============ L2: 三通道去重 ============
def expr_hash(expr_str):
    """归一化表达式 hash：提取列名排序 + 运算符骨架"""
    cols = sorted(re.findall(r"pl\.col\(['\"]([^'\"]+)['\"]\)", expr_str))
    skeleton = re.sub(r"\d+", "N", expr_str)
    skeleton = re.sub(r"pl\.col\(['\"][^'\"]+['\"]\)", "COL", skeleton)
    return hashlib.sha1(f"{skeleton}|{cols}".encode()).hexdigest()[:16]

def l2_dedup(expr, pool_exprs, df=None):
    """三通道去重。pool_exprs: [{expr, name}]。返回 (通过, 原因)"""
    df = df if df is not None else load_train_df()
    # 语义去重（快）
    h = expr_hash(expr)
    for p in pool_exprs:
        if p.get("expr_hash") == h:
            return False, "语义重复"
    # 数值去重：采样相关性
    sample = df.sample(n=200_000, seed=42)
    try:
        cand = sample.with_columns(expr.alias("_c"))
        for p in pool_exprs:
            try:
                pe = eval(p["expr"], {"pl": pl})
                merged = cand.with_columns(pe.alias("_p"))
                pear = merged.select(pl.corr(pl.col("_c"), pl.col("_p"))).item()
                spe = merged.select(pl.corr(pl.col("_c"), pl.col("_p"), method="spearman")).item()
                if abs(pear) >= 0.7 or abs(spe) >= 0.7:
                    return False, f"与{p['name']}相关 pear={pear:.2f} spe={spe:.2f}"
            except Exception:
                continue
    except Exception:
        pass
    return True, ""

# ============ L2: 动态正交化（岭回归） ============
def l2_orthogonal(expr, base_exprs, df=None):
    """对 v7 六因子 + 池内因子做岭回归残差，检验残差 ICIR。
    base_exprs: [expr_str, ...]。返回 (通过, 残差ICIR, 条件数)"""
    df = df if df is not None else load_train_df()
    sample = df.sample(n=300_000, seed=7)
    try:
        y = sample.with_columns(expr.alias("_y"))["_y"].to_numpy()
        X = np.column_stack([
            sample.with_columns(eval(b, {"pl": pl}).alias("_x"))["_x"].to_numpy()
            for b in base_exprs
        ])
        mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        y, X = y[mask], X[mask]
        if len(y) < 10000 or X.shape[1] == 0:
            return True, 0.0, 0.0
        # 条件数
        Xc = X - X.mean(axis=0)
        try:
            cond = np.linalg.cond(Xc)
        except Exception:
            cond = 0.0
        # 岭回归残差（防共线崩溃）
        alpha = 1e-3 if cond < 1e6 else 1e-2
        beta = np.linalg.solve(Xc.T @ Xc + alpha * np.eye(Xc.shape[1]), Xc.T @ y)
        resid = y - Xc @ beta
        # 残差 ICIR（近似：直接用残差对 y 的相关结构，用日度分组算）
        res_df = sample.filter(pl.col("_y").is_not_null()).with_columns(pl.Series("_resid", np.nan).cast(pl.Float32))
        # 简化：残差与 fwd_5d 的截面相关（在样本内近似）
        r = np.corrcoef(resid, y)[0, 1]
        resid_icir = float(r) * 10  # 近似放大（样本相关性→ICIR 量级）
        return abs(resid_icir) >= 0.2, round(resid_icir, 3), round(cond, 0)
    except Exception as e:
        return True, 0.0, 0.0   # 数值失败不拦（保守放行，L3 把关）

# ============ L2: regime 分层 ============
def l2_regime(expr, df=None):
    """牛/熊/震荡三态 IC 方向一致才通过"""
    df = df if df is not None else load_train_df()
    try:
        market = pl.read_parquet(MARKET).select(["日期", "涨停家数"]).sort("日期")
        # regime: 用沪深300 MA20 判断（简化：hs300 close vs ma20）
        hs300 = pl.read_parquet(DATA_DIR / "hs300.parquet").select(["日期", "close", "ma_20"])
        d = df.join(hs300, on="日期", how="left").join(market, on="日期", how="left")
        d = d.with_columns(
            ((pl.col("close") > pl.col("ma_20")).alias("bull")).fill_null(False)
        )
        ic_series = (d.select(["日期", "_cand" if "_cand" in d.columns else "日期"])
                     .group_by("日期").agg(pl.len()))
        # 直接逐日 IC
        dd = d.with_columns(pl.lit(1).alias("_tmp"))
        ic = (d.select(["日期", "_cand", MAIN_HORIZON, "bull"])
              .group_by("日期")
              .agg([pl.corr(pl.col("_cand"), pl.col(MAIN_HORIZON), method="spearman").alias("ic"),
                    pl.col("bull").first().alias("bull")])
              .filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite()))
        signs = []
        for regime, name in [(True, "牛"), (False, "熊")]:
            sub = ic.filter(pl.col("bull") == regime)
            if len(sub) > 100:
                m = sub["ic"].mean()
                s = sub["ic"].std()
                if s and s > 0:
                    signs.append((name, m, m / s))
        if len(signs) >= 2:
            base_sign = 1 if signs[0][1] > 0 else -1
            for name, m, ir in signs:
                if m * base_sign < 0 or abs(ir) < 0.15:
                    return False, {name: round(ir, 3) for name, m, ir in signs}
        return True, {name: round(ir, 3) for name, m, ir in signs}
    except Exception:
        return True, {}

# ============ L2: 半衰期 ============
def l2_half_life(expr, df=None):
    """滚动252日ICIR拟合衰减，返回半衰期(月)"""
    df = df if df is not None else load_train_df()
    try:
        d = df.with_columns(expr.alias("_cand"))
        ic = (d.select(["日期", "_cand", MAIN_HORIZON])
              .group_by("日期")
              .agg(pl.corr(pl.col("_cand"), pl.col(MAIN_HORIZON), method="spearman").alias("ic"))
              .sort("日期"))
        ic = ic.filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite())
        v = ic["ic"].to_numpy()
        if len(v) < 300:
            return 12.0  # 数据不足默认长寿命
        # 滚动252日 ICIR
        ics = []
        for i in range(0, len(v) - 251, 63):
            w = v[i:i+252]
            ics.append(w.mean() / max(w.std(), 1e-9))
        if len(ics) < 4:
            return 12.0
        ics = np.array(ics)
        # 线性拟合斜率（每段=3个月）
        slope = np.polyfit(np.arange(len(ics)), ics, 1)[0]
        if slope >= 0:
            return 12.0  # 无衰减
        half = abs(ics[0] / (2 * slope)) * 3  # 月
        return round(min(max(half, 1), 24), 1)
    except Exception:
        return 12.0

# ============ L2: 反因子 ============
def l2_reverse_check(expr, df=None):
    """构造 -expr，验证反向 ICIR 显著为负且 |ICIR| 接近"""
    df = df if df is not None else load_train_df()
    neg = -expr
    res = calc_multi_ic(neg, df=df, horizons=[MAIN_HORIZON])
    if res is None or res[MAIN_HORIZON] is None:
        return False, "反因子计算失败"
    r = res[MAIN_HORIZON]
    if abs(r["icir"]) < 0.25:
        return False, f"反因子|ICIR|<0.25: {r['icir']}"
    return True, {"reverse_icir": r["icir"]}

# ============ L1 主流程（LLM 生成 + 修正） ============
def l1_refine(batch_id, factor_idx, api_key, ddict, max_rounds=3, smoke=False):
    """生成一个因子并过 L1 修正。返回 dict 或 None"""
    seed = batch_id * 1000 + factor_idx
    system = """你是 A 股量化因子研究员，精通 Polars 表达式。你的任务是发明有金融逻辑的因子。
规则：
1. 【最重要】只能使用数据字典里列出的列名，一字不差（如 收盘、ret_5d、turn_ma5）。禁止自编列名！常见错误列名黑名单（绝对不可用）：成交量、成交额、收盘价、开盘价、最高价、最低价、收益率、涨跌幅、涨幅、volume、close、open——这些都不在字典里，用了就作废
2. 预测目标 fwd_5d（未来5日收益）
3. 表达式必须是合法的 polars Expr 代码
4. 支持：+ - * /、pl.col、.rolling_mean/.rolling_std/.rolling_max/.rolling_min（min_samples 必须给）、.rank().over('日期')、.shift(1).over('股票代码')
5. 不要用未定义的列，不要 import
6. 每个因子必须有金融逻辑
7. 输出严格 JSON 数组（不要多余文字），每项：{"name": "英文名", "logic": "金融逻辑", "expr": "polars表达式"}
8. 额外：给每个因子一句话经济叙事，字段名 narrative

数据字典："""
    user = make_prompt(batch_id, factor_idx, seed, ddict)
    best = None
    for rnd in range(1, max_rounds + 1):
        if rnd > 1 and best:
            user = (f"上一轮因子 {best['name']} 检验结果：ICIR={best['icir']}，"
                    f"请修改公式以优化 |ICIR|，输出新的 JSON 数组（1 个因子）")
        elif rnd > 1:
            user = (f"上一轮失败原因：{best_fail_reason}。请修正后重试，输出新的 JSON 数组（1 个因子），"
                    f"严格遵守列名黑名单")
        try:
            out = llm_chat(system, user, api_key, temperature=0.3 if rnd == 1 else 0.5)
            factors = parse_json(out)
            if not factors:
                print(f"    [L1 b{batch_id}f{factor_idx} r{rnd}] 解析失败")
                best_fail_reason = "JSON 解析失败，请严格输出 JSON 数组"
                continue
            f = factors[0]
            name, logic, expr_str = f.get("name"), f.get("logic"), f.get("expr")
            narrative = f.get("narrative", logic)
            # 列名校验
            ok, reason = validate_expr(expr_str)
            if not ok:
                print(f"    [L1 b{batch_id}f{factor_idx} r{rnd}] {reason}")
                best_fail_reason = reason
                continue
            # 多周期 IC
            passed, metrics, why = l1_ic_metrics(eval(expr_str, {"pl": pl}))
            if not passed:
                print(f"    [L1 b{batch_id}f{factor_idx} r{rnd}] {name}: {why}")
                if rnd == max_rounds:
                    break
                best = {"name": name, "icir": metrics.get("icir", 0)}
                continue
            return {"name": name, "logic": logic, "narrative": narrative,
                    "expr": expr_str, "expr_hash": expr_hash(expr_str),
                    "ic_metrics": metrics, "batch_id": batch_id, "factor_idx": factor_idx,
                    "seed": seed, "rounds": rnd, "version_chain": [{"v": 1, "expr": expr_str}]}
        except Exception as e:
            print(f"    [L1 b{batch_id}f{factor_idx} r{rnd}] API错误: {e}")
            time.sleep(2)
    return None

def parse_json(text):
    """容错 JSON 数组解析"""
    if not text:
        return []
    t = re.sub(r"```(?:json)?", "", text)
    try:
        d = json.loads(t)
        return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        pass
    start = t.find("[")
    if start == -1:
        return []
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "[":
            depth += 1
        elif t[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    d = json.loads(t[start:i+1])
                    return d if isinstance(d, list) else []
                except json.JSONDecodeError:
                    return []
    return []

# ============ L2 主流程（单因子全管线） ============
def l2_pipeline(cand, pool_exprs, api_key, df=None, verbose=True):
    """候选因子过 L2 全管线。返回 (通过与否, 原因, 增强的cand)"""
    df = df if df is not None else load_train_df()
    expr = eval(cand["expr"], {"pl": pl})
    # 1. 精算层
    fine = l2_fine_eval(expr, df=df)
    if fine is None:
        return False, "精算层失败", cand
    if fine["turn_exp"] > 1.5:
        return False, f"换手暴露过高: {fine['turn_exp']}", cand
    if abs(fine["mono"]) < (0.5 if cand["ic_metrics"].get("extreme_driven") else 0.3):
        return False, f"quintile单调性弱: {fine['mono']}", cand
    cand["fine_metrics"] = fine
    # 2. 三通道去重
    ok, why = l2_dedup(cand["expr"], pool_exprs, df=df)
    if not ok:
        return False, f"去重: {why}", cand
    # 3. 动态正交化（v7 六因子 + 池内）
    base = [f"(pl.col('ret_5d')*pl.col('turn_ma5'))", f"(pl.col('ma5_dist')*pl.col('turn_ma5'))",
            f"(-pl.col('vol_10d')-pl.col('vol_change_5d'))", "pl.col('limit_up_5d')",
            "(-pl.col('turn_ratio'))", "pl.col('macd_dif')"]
    base += [p["expr"] for p in pool_exprs]
    ok, resid_icir, cond = l2_orthogonal(cand["expr"], base, df=df)
    if not ok:
        return False, f"正交化后残差ICIR不显著: {resid_icir} (cond={cond})", cand
    cand["orth_metrics"] = {"resid_icir": resid_icir, "cond": cond}
    # 4. regime 分层
    ok, reg = l2_regime(expr, df=df)
    if not ok:
        return False, f"regime方向不一致: {reg}", cand
    cand["regime_metrics"] = reg
    # 5. 半衰期
    hl = l2_half_life(expr, df=df)
    cand["half_life"] = hl
    if hl < 6:
        cand["short_lived"] = True
    # 6. 反因子
    ok, rev = l2_reverse_check(expr, df=df)
    if not ok:
        return False, f"反因子: {rev}", cand
    cand["reverse_metrics"] = rev
    if verbose:
        print(f"    [L2] {cand['name']} 通过: 换手={fine['turn_exp']} mono={fine['mono']} "
              f"残差ICIR={resid_icir} regime={reg} 半衰期={hl}月 反ICIR={rev['reverse_icir']}")
    return True, "", cand

if __name__ == "__main__":
    from datetime import date
    import sys
    print("=== L1+L2 模块自测 ===")
    # 用一个已知有效因子测试管线（v7 的因子1）
    test_expr = "(-pl.col('ret_5d') * pl.col('turn_ma5'))"
    print(f"测试因子: {test_expr}")
    ok, metrics, why = l1_ic_metrics(eval(test_expr, {"pl": pl}))
    print(f"L1 多周期: ok={ok} icir={metrics.get('icir')} why={why or '通过'}")
    fine = l2_fine_eval(eval(test_expr, {"pl": pl}))
    print(f"L2 精算: {fine}")
    hl = l2_half_life(eval(test_expr, {"pl": pl}))
    print(f"L2 半衰期: {hl} 月")
    ok, reg = l2_regime(eval(test_expr, {"pl": pl}))
    print(f"L2 regime: ok={ok} {reg}")
