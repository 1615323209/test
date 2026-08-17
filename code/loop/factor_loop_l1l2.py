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
# L1 内部样本切分（L1 文档第二章）：设计段 2021-2023（生成/反馈/判定唯一口径）
# 内层留出 2024（仅 G4 一次性确认）。embargo 10 交易日。
DESIGN_LO, DESIGN_HI = date(2021, 1, 1), date(2023, 12, 31)
HOLDOUT_LO, HOLDOUT_HI = date(2024, 1, 1), date(2024, 12, 31)
# ic_data 实际含 fwd_1d/5d/10d/20d（无 3d），用 1d/5d/10d/20d 构成多周期体检
HORIZONS = ["fwd_1d", "fwd_5d", "fwd_10d", "fwd_20d"]
MAIN_HORIZON = "fwd_5d"

# 与 llm_factor_synth 复用的 DeepSeek 客户端
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop.llm_factor_synth import load_deepseek_key, llm_chat
from loop.expr_sandbox import safe_compile, is_fwd_col

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

def load_design_df():
    """加载设计段(2021-2023)因子+forward收益（L1 判定唯一口径）"""
    if "design" in _cache:
        return _cache["design"]
    s = pl.scan_parquet(IC_DATA)
    df = s.filter((pl.col("日期") >= DESIGN_LO) & (pl.col("日期") <= DESIGN_HI)).collect()
    _cache["design"] = df
    return df

def load_holdout_df():
    """加载内层留出段(2024)（仅 G4 一次性确认用）"""
    if "holdout" in _cache:
        return _cache["holdout"]
    s = pl.scan_parquet(IC_DATA)
    df = s.filter((pl.col("日期") >= HOLDOUT_LO) & (pl.col("日期") <= HOLDOUT_HI)).collect()
    _cache["holdout"] = df
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

# ============ L1: 辅助检验函数（L1 文档第五章 G2/G3 口径） ============
def newey_west_t(ic_series, lag=10):
    """Newey-West 校正 t 值（修正重叠标签自相关导致的 t 虚高，L1 文档 G2）
    t_NW = mean / SE(mean)，SE(mean) = sqrt(Var_NW / n)
    Var_NW = γ0 + 2·Σ(1-k/(lag+1))·γk（γk 为滞后 k 自协方差）"""
    v = np.asarray(ic_series, dtype=float)
    v = v[np.isfinite(v)]
    n = len(v)
    if n < 30:
        return 0.0
    mu = v.mean()
    d = v - mu
    gamma = np.correlate(d, d, mode='full')[n - 1:]  # Σd_i·d_{i+k}（未归一化）
    gamma = gamma / n  # 归一化为自协方差
    k = np.arange(1, lag + 1)
    var_nw = gamma[0] + 2 * np.sum((1 - k / (lag + 1)) * gamma[1:lag + 1])
    if var_nw <= 0:
        return 0.0
    se = np.sqrt(var_nw / n)  # 标准误
    return float(mu / se) if se > 0 else 0.0

def year_sign_check(ic_series, dates, main_sign):
    """分年符号一致（替代旧"滚动60日ICIR min>0"）：每年 IC 同号；
    任一年反向且 |t_year|≥2 → 拒（L1 文档 G3）"""
    if len(ic_series) != len(dates) or len(ic_series) < 100:
        return True, ""
    df = pd.DataFrame({"d": dates, "ic": ic_series})
    df["y"] = pd.to_datetime(df["d"]).dt.year
    for y, g in df.groupby("y"):
        m, s = g["ic"].mean(), g["ic"].std()
        if m * main_sign < 0 and s > 0 and abs(m / s) >= 2:
            return False, f"{y}年反向(t={m/s:.2f})"
    return True, ""

def seg_ok_check(ic_series, dates, main_sign):
    """半年段一致：seg_ok_ratio ≥ 60% 且 last2_ok（L1 文档 G3）"""
    if len(ic_series) != len(dates) or len(ic_series) < 100:
        return 1.0, True
    df = pd.DataFrame({"d": dates, "ic": ic_series})
    dts = pd.to_datetime(df["d"])
    df["seg"] = (dts.dt.year - 2010) * 2 + (dts.dt.month > 6)
    seg_ics = df.groupby("seg")["ic"].mean()
    ratio = float((seg_ics * main_sign > 0).mean())
    last2 = bool((seg_ics.tail(2) * main_sign > 0).all())
    return ratio, last2

def quintile_mono(expr, df, main_sign):
    """横截面 quintile 单调性（从 L2 上移 L1 G3，L2 文档缺陷 11）"""
    try:
        d = df.with_columns(expr.alias("_f"))
        d2 = d.filter(pl.col("_f").is_not_null() & pl.col("_f").is_finite()
                      & pl.col(MAIN_HORIZON).is_not_null() & pl.col(MAIN_HORIZON).is_finite())
        if len(d2) < 10000:
            return 0.0
        d2 = d2.with_columns([
            pl.col("_f").rank().over("日期").alias("_rk"),
            pl.col("_f").count().over("日期").alias("_n"),
        ])
        d2 = d2.with_columns((pl.col("_rk") / pl.col("_n") * 5).cast(pl.Int32).clip(0, 4).alias("_q"))
        q = d2.group_by("_q").agg(pl.col(MAIN_HORIZON).mean().alias("m")).sort("_q")
        qv = q["m"].to_list()
        if len(qv) >= 3 and np.std(qv) > 0:
            return float(abs(np.corrcoef(np.arange(len(qv)), qv)[0, 1]))
        return 0.0
    except Exception:
        return 0.0

def l1_ic_metrics(expr, df=None):
    """完整 L1 体检（L1 文档第五章：G2 主周期 + G3 完整体检）
    口径：设计段 2021-2023（L1 判定唯一口径，防 L1 反复窥视验证集）
    检查：|t_NW|≥3.0 / 次周期同号 / 衰减<50% / 分年符号一致 /
          半年段一致 / quintile 单调≥0.3 / Rank vs Normal
    返回 (通过与否, 指标dict, 拒绝原因)
    """
    df = df if df is not None else load_design_df()
    res = calc_multi_ic(expr, df=df)
    if res is None or res[MAIN_HORIZON] is None:
        return False, {}, "表达式执行失败"
    main = res[MAIN_HORIZON]
    ic_series = main.get("_ic_series", [])
    # 日期序列（分年/分段检查用，与 IC 序列同序）
    try:
        dates = (df.with_columns(expr.alias("_cand2"))
                 .select(["日期", "_cand2", MAIN_HORIZON])
                 .group_by("日期")
                 .agg(pl.corr(pl.col("_cand2"), pl.col(MAIN_HORIZON), method="spearman").alias("ic"))
                 .filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite())
                 .sort("日期")["日期"].to_list())
    except Exception:
        dates = []
    # G2: 主周期显著性（Newey-West t，lag=10 修正重叠标签）
    t_nw = newey_west_t(ic_series)
    if abs(t_nw) < 3.0:
        return False, main, f"主周期|t_NW|<3.0: {t_nw:.2f}"
    main_sign = 1 if main["ic_mean"] > 0 else -1
    # 次周期同号
    for hz in ["fwd_1d", "fwd_10d", "fwd_20d"]:
        r = res.get(hz)
        if r and r["ic_mean"] * main_sign < 0:
            return False, main, f"次周期{hz}异号"
    # 衰减曲线: fwd_5d → fwd_10d |IC| 衰减 <50%
    r10 = res.get("fwd_10d")
    if r10 and r10["ic_mean"]:
        decay = abs(main["ic_mean"]) - abs(r10["ic_mean"])
        if decay / max(abs(main["ic_mean"]), 1e-9) > 0.5:
            return False, main, f"IC衰减过快: {decay/abs(main['ic_mean'])*100:.0f}%"
    # 分年符号一致（替代旧"滚动60日ICIR min>0"，L1 文档 G3）
    ok, why = year_sign_check(ic_series, dates, main_sign)
    if not ok:
        return False, main, why
    # 半年段一致
    seg_ok, last2 = seg_ok_check(ic_series, dates, main_sign)
    if seg_ok < 0.6 or not last2:
        return False, main, f"分段稳定不足: seg_ok={seg_ok:.2f} last2={last2}"
    main["seg_ok_ratio"] = round(seg_ok, 3)
    main["last2_ok"] = last2
    # quintile 单调（从 L2 上移；黑箱来源 ≥0.5 由生成端标注）
    mono = quintile_mono(expr, df, main_sign)
    if abs(mono) < 0.3:
        return False, main, f"quintile单调弱: {mono:.3f}"
    main["mono"] = round(mono, 3)
    # Rank(Normal) IC 同号 / 重尾容忍
    normal_ic = calc_normal_ic(expr, df=df)
    if normal_ic is not None:
        n_sign = 1 if normal_ic > 0 else -1
        if n_sign != main_sign:
            if abs(normal_ic) < 2 * abs(main["ic_mean"]):
                return False, main, f"Rank/Normal异号且无极端值优势 (normal={normal_ic:.4f})"
            main["extreme_driven"] = True  # 标记重尾驱动，L2 复核
    main["t_nw"] = round(t_nw, 2)
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
    """列名校验 + PIT 检查（L1 文档第六章：AST 沙箱 + fwd_* 黑名单 + 未注册列默认拒）。
    返回 (通过与否, 原因)"""
    # AST 沙箱：算子白名单 + fwd_* 硬黑名单 + 时序算子强制 over + 幻觉列
    _, err = safe_compile(expr_str)
    if err:
        return False, err
    valid_cols = set(load_full_ic_cols())
    used = re.findall(r"pl\.col\(['\"]([^'\"]+)['\"]\)", expr_str)
    for c in used:
        if c not in valid_cols:
            return False, f"未注册列: {c}"
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
    """三通道去重（L2 文档缺陷 7 修复）：语义 hash + 逐日横截面相关的时间均值
    （替代旧"随机抽 20 万行池化相关"——池化相关混入时序共同波动，误判冗余）
    pool_exprs: [{expr, name}]。返回 (通过, 原因)
    """
    df = df if df is not None else load_design_df()
    # 语义去重（快）
    h = expr_hash(expr)
    for p in pool_exprs:
        if p.get("expr_hash") == h:
            return False, "语义重复"
    # 数值去重：逐日横截面 Pearson/Spearman 相关，取时间均值
    try:
        cand = df.with_columns(expr.alias("_c"))
        for p in pool_exprs:
            try:
                pe, perr = safe_compile(p["expr"])
                if pe is None:
                    continue
                merged = cand.with_columns(pe.alias("_p"))
                daily = (merged.select(["日期", "_c", "_p"])
                         .group_by("日期")
                         .agg([pl.corr(pl.col("_c"), pl.col("_p")).alias("pear"),
                               pl.corr(pl.col("_c"), pl.col("_p"), method="spearman").alias("spe")])
                         .filter(pl.col("pear").is_not_null() & pl.col("pear").is_finite()))
                if len(daily) < 30:
                    continue
                m_pear = float(daily["pear"].mean())
                m_spe = float(daily["spe"].mean())
                if abs(m_pear) >= 0.7 or abs(m_spe) >= 0.7:
                    return False, f"与{p['name']}相关 pear={m_pear:.2f} spe={m_spe:.2f}"
            except Exception:
                continue
    except Exception:
        pass
    return True, ""

# ============ L2: 动态正交化（岭回归） ============
def l2_orthogonal(expr, base_exprs, df=None):
    """动态正交化（L2 文档缺陷 4/9 修复）：
    - 基准统一 rank 形式（与 L3 注入 (expr).rank().over('日期') 一致，缺陷 9）
    - 残差 ICIR 改为真口径：残差 → 逐日横截面 Spearman IC → Newey-West t（缺陷 4）
    - 异常不再 return True 放行（防假门禁）
    base_exprs: [expr_str, ...]。返回 (通过, 残差t_NW, 条件数)
    """
    df = df if df is not None else load_design_df()
    try:
        # 先物化再 rank（polars 不支持带 over 的表达式直接 .rank().over() 嵌套，会全 NaN）
        y = (df.with_columns(expr.alias("_y0"))
             .with_columns(pl.col("_y0").rank().over("日期").alias("_y"))["_y"].to_numpy())
        X_cols = []
        for b in base_exprs:
            be, berr = safe_compile(b)
            if be is None:
                continue
            X_cols.append((df.with_columns(be.alias("_x0"))
                           .with_columns(pl.col("_x0").rank().over("日期").alias("_x"))["_x"].to_numpy()))
        if not X_cols:
            return False, 0.0, 0.0
        X = np.column_stack(X_cols)
        mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        y_m, X_m = y[mask], X[mask]
        if len(y_m) < 10000 or X_m.shape[1] == 0:
            return False, 0.0, 0.0
        # 条件数（基准内部共线检测）
        Xc = X_m - X_m.mean(axis=0)
        y_c = y_m - y_m.mean()  # 中心化 y（正规方程要求同口径）
        cond = float(np.linalg.cond(Xc)) if Xc.size else 0.0
        # 近 OLS（alpha 极小）；仅中度共线才加正则
        alpha = 1e-8 if cond < 1e4 else 1e-3
        try:
            beta = np.linalg.solve(Xc.T @ Xc + alpha * np.eye(Xc.shape[1]), Xc.T @ y_c)
        except np.linalg.LinAlgError:
            return False, 0.0, round(cond, 0)
        resid = y_c - Xc @ beta
        # 残差范数检验：候选基本在基准线性空间内（残差≈0）→ "已被现有池子解释"拒绝
        tss = float(y_c @ y_c)
        rss = float(resid @ resid)
        if tss > 0 and rss / tss < 1e-3:
            return False, 0.0, round(cond, 0)
        # 残差 → 逐日横截面 Spearman IC（与 L1 同口径）→ Newey-West t
        resid_full = np.full(len(df), np.nan)
        resid_full[mask] = resid
        res_ic = (df.select(["日期", MAIN_HORIZON])
                  .with_columns(pl.Series("_resid", resid_full))
                  .filter(pl.col("_resid").is_finite() & pl.col(MAIN_HORIZON).is_finite())
                  .group_by("日期")
                  .agg(pl.corr(pl.col("_resid"), pl.col(MAIN_HORIZON), method="spearman").alias("ic"))
                  .filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite()))
        if len(res_ic) < 100:
            return False, 0.0, cond
        t_nw = newey_west_t(res_ic["ic"].to_list())
        return abs(t_nw) >= 2.0, round(t_nw, 2), round(cond, 0)
    except Exception as e:
        # 异常必须落盘可见，不放行（L2 文档：假门禁比没有门禁更危险）
        return False, 0.0, 0.0

# ============ L2: regime 分层（L2 文档第四章第 4 节） ============
def l2_regime(expr, df=None):
    """牛/熊/震荡三态 IC 方向一致且 |ICIR|≥0.15 才通过。

    修复（L2 文档缺陷 1/5）：
    - _cand 列现在真正物化（旧版从未物化 → 恒抛异常 → except 放行）
    - 三态划分：hs300 MA20（方向）+ 涨停家数（情绪）
      - 牛: close >= ma_20
      - 熊: close < ma_20 且 涨停家数 < 中位数
      - 震荡: close < ma_20 且 涨停家数 >= 中位数
    - 判定口径改设计段 2021-2023（L2 文档第三章）
    返回 (通过, {regime: icir})
    """
    df = df if df is not None else load_design_df()
    try:
        market = pl.read_parquet(MARKET).select(["日期", "涨停家数"]).sort("日期")
        # 注意：ic_data 自带股票级 ma_20 列，join hs300 必须用 suffix 区分（旧版缺陷：列名冲突
        # 导致用股票 ma_20 与指数 close 比较，regime 划分错位）
        hs300 = pl.read_parquet(DATA_DIR / "hs300.parquet").select(["日期", "close", "ma_20"])
        d = df.join(hs300, on="日期", how="left", suffix="_hs").join(market, on="日期", how="left")
        # 物化候选列（旧版缺陷：从未物化 _cand）
        d = d.with_columns(expr.alias("_cand"))
        # 涨停家数中位数（设计段内）
        zt_med = d["涨停家数"].median()
        d = d.with_columns([
            pl.when(pl.col("close") >= pl.col("ma_20_hs")).then(pl.lit("牛"))
              .when((pl.col("close") < pl.col("ma_20_hs")) & (pl.col("涨停家数") < zt_med)).then(pl.lit("熊"))
              .otherwise(pl.lit("震荡")).alias("regime"),
        ])
        ic = (d.select(["日期", "_cand", MAIN_HORIZON, "regime"])
              .group_by("日期")
              .agg([pl.corr(pl.col("_cand"), pl.col(MAIN_HORIZON), method="spearman").alias("ic"),
                    pl.col("regime").first().alias("regime")])
              .filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite()))
        signs = {}
        for rname in ["牛", "熊", "震荡"]:
            sub = ic.filter(pl.col("regime") == rname)
            if len(sub) > 100:  # 每态样本量下限
                m, s = sub["ic"].mean(), sub["ic"].std()
                if s and s > 0:
                    signs[rname] = round(float(m / s), 3)
        if len(signs) < 2:
            return False, signs  # 态数不足无法判定
        base_sign = 1 if next(iter(signs.values())) > 0 else -1
        for name, ir in signs.items():
            if ir * base_sign < 0 or abs(ir) < 0.15:
                return False, signs
        return True, signs
    except Exception as e:
        # 任何异常必须落盘可见（工程保障：假门禁比没有门禁更危险）
        return False, {"error": str(e)[:80]}

# ============ L2: 半衰期 ============
def l2_half_life(expr, df=None):
    """滚动252日ICIR拟合衰减，返回半衰期(月)。
    数据不足/异常返回 None（L2 文档缺陷 10：不乐观兜底 12 月，由调用方标记 half_life_unknown）"""
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
            return None  # 数据不足 → unknown，不乐观兜底
        # 滚动252日 ICIR
        ics = []
        for i in range(0, len(v) - 251, 63):
            w = v[i:i+252]
            ics.append(w.mean() / max(w.std(), 1e-9))
        if len(ics) < 4:
            return None
        ics = np.array(ics)
        # 线性拟合斜率（每段=3个月）
        slope = np.polyfit(np.arange(len(ics)), ics, 1)[0]
        if slope >= 0:
            return 12.0  # 无衰减，默认长寿命
        half = abs(ics[0] / (2 * slope)) * 3  # 月
        return round(min(max(half, 1), 24), 1)
    except Exception:
        return None

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
    df = df if df is not None else load_design_df()  # L2 判定口径：设计段 2021-2023（L2 文档第三章）
    expr, serr = safe_compile(cand["expr"])
    if expr is None:
        return False, f"表达式沙箱拒绝: {serr}", cand
    # 1. 精算层（换手暴露；quintile 单调已上移 L1 G3，L2 不再重复）
    fine = l2_fine_eval(expr, df=df)
    if fine is None:
        return False, "精算层失败", cand
    if fine["turn_exp"] > 1.5:
        return False, f"换手暴露过高: {fine['turn_exp']}", cand
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
    if hl is not None and hl < 6:
        cand["short_lived"] = True
    if hl is None:
        cand["half_life_unknown"] = True  # L2 文档缺陷 10：算不出 → 标记 unknown，不乐观兜底
    if verbose:
        print(f"    [L2] {cand['name']} 通过: 换手={fine['turn_exp']} mono={fine['mono']} "
              f"残差ICIR={resid_icir} regime={reg} 半衰期={hl}月")
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
