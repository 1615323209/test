#!/usr/bin/env python3
"""因子挖掘 v3 — 解决 2340 候选全冗余问题（FACTOR_LIBRARY.md 方法论改造）

针对旧版（mine_factors.py）的四个改造：
1. 预先去相关（贪心：按 |IC| 排序，|corr|<0.7 才保留）→ 组合前先剔除冗余基础因子
2. 运算扩展：x(乘)/d(差)/p(和)/r(除) 4 种
3. 滚动 IC 稳定性：IC 序列按半年分段，要求方向一致段 >=60% 且最近2段不反向
4. 精算层目标函数：score = |ICIR| / (换手暴露 + 0.5)，Top 候选再加 quintile 单调性验证

用法: python3 mine_factors_v2.py [--top N]
"""
import polars as pl
import pandas as pd
import numpy as np
from pathlib import Path
import itertools, time, sys, os

IC_DATA = Path("D:/quant_data/ic_data.parquet")
OUT = Path("D:/quant_data/mined_factors_v2.csv")
CORR_TH = 0.7      # 去相关阈值
ICIR_MIN = 0.25    # 初筛 ICIR
SEG_OK_MIN = 0.6   # 同号段最低比例
HORIZON = 'fwd_5d'

BASE = ['ret_1d','ret_5d','ret_10d','ret_20d',
    'vol_5d','vol_10d','vol_20d',
    'ma_5','ma_10','ma_20','ma_60',
    'ma5_dist','ma20_dist','ma5_ma20_cross','ma5_ma20_dead',
    'vol_ratio','vol_ratio_20','vol_change_5d',
    'turn_ma5','turn_ma20','turn_ratio',
    'atr_14','atr_ratio',
    'high_20d','low_20d','high_60d','low_60d',
    'price_pos_20','price_pos_60',
    'macd_dif','macd_dea','macd_hist',
    'rsi_14','bb_width','bb_pos',
    'limit_up','limit_down','is_suspended',
    'up_streak','down_streak']

t_start = time.time()
def log(msg):
    print(f"[{time.time()-t_start:6.0f}s] {msg}", flush=True)

# ---------- 1. 加载 ----------
log(f"加载 {IC_DATA.name} ...")
df = pl.scan_parquet(IC_DATA).select(['日期', HORIZON] + BASE).collect()
log(f"内存 DataFrame: {len(df):,}行 × {len(df.columns)}列")
n_days = df['日期'].n_unique()

# ---------- 2. 基础因子 IC + 去相关预筛 ----------
log("计算基础因子全期 IC ...")
def series_ic(factor):
    ic = (df.select(['日期', factor, HORIZON])
          .group_by('日期')
          .agg(pl.corr(pl.col(factor), pl.col(HORIZON), method='spearman').alias('ic')))
    v = ic['ic'].fill_nan(None).drop_nulls()
    if len(v) < 200: return None
    m, s = v.mean(), v.std()
    if s is None or s == 0: return None
    return {'factor': factor, 'ic_mean': float(m), 'icir': float(m/s)}

base_ic = {}
for f in BASE:
    r = series_ic(f)
    if r: base_ic[f] = r
log(f"基础因子 IC 完成: {len(base_ic)} 个有效")

log(f"抽样算相关矩阵（{CORR_TH} 阈值去相关）...")
sample = df.sample(n=300_000, seed=42).select(BASE).drop_nulls()
corr_map = {}   # (a,b) -> pearson
bc = list(base_ic.keys())
for i, a in enumerate(bc):
    for b in bc[i+1:]:
        c = sample.select(pl.corr(pl.col(a), pl.col(b))).item()
        corr_map[(a, b)] = c
log(f"相关矩阵完成: {len(corr_map)} 对")

# 贪心选择：按 |IC| 降序，与已选集合最大相关 < 阈值才保留
ranked = sorted(base_ic.items(), key=lambda kv: -abs(kv[1]['icir']))
selected = []
for f, r in ranked:
    if not selected:
        selected.append(f); continue
    mx = max((abs(corr_map[(a, f)] if (a, f) in corr_map else corr_map[(f, a)])
              for a in selected if (a, f) in corr_map or (f, a) in corr_map), default=0.0)
    if mx < CORR_TH:
        selected.append(f)
log(f"去相关后保留 {len(selected)} 个独立基础因子: {selected}")
for f in selected:
    r = base_ic[f]
    log(f"    {f:18} IC={r['ic_mean']:+.4f} ICIR={r['icir']:+.3f}")

# ---------- 3. 组合生成 + IC + 分段稳定性 ----------
def compute_ic_full(name, expr):
    """返回 {ic_mean, icir, ic_pos_pct, seg_ok_ratio, last2_ok, days, seg_ics}"""
    d = df.with_columns(expr.alias('_cand'))
    ic = (d.select(['日期', '_cand', HORIZON])
          .group_by('日期')
          .agg(pl.corr(pl.col('_cand'), pl.col(HORIZON), method='spearman').alias('ic')))\
         .sort('日期')
    ic = ic.filter(pl.col('ic').is_not_null() & pl.col('ic').is_finite())
    if len(ic) < 200:
        return None
    v = ic['ic']
    m, s = v.mean(), v.std()
    if s is None or s == 0 or m is None:
        return None
    icir = m / s
    # 半年分段（年*2 + 下半年）
    ic2 = ic.with_columns(((pl.col('日期').dt.year() - 2010) * 2
                           + (pl.col('日期').dt.month() > 6)).alias('seg'))
    seg = ic2.group_by('seg').agg(pl.col('ic').mean().alias('seg_ic')).sort('seg')
    seg_ics = seg['seg_ic'].to_list()
    sign = 1 if m > 0 else -1
    seg_ok = sum(1 for x in seg_ics if x * sign > 0) / len(seg_ics)
    last2_ok = all(x * sign > 0 for x in seg_ics[-2:])
    return {'expr': name, 'ic_mean': round(float(m), 4), 'icir': round(float(icir), 4),
            'ic_pos_pct': round(float((v > 0).mean()) * 100, 1), 'days': len(v),
            'seg_ok_ratio': round(seg_ok, 3), 'last2_ok': last2_ok, 'seg_ics': seg_ics}

candidates = []
n_sel = len(selected)
combos = list(itertools.combinations(selected, 2))
SMOKE = int(os.environ.get('MINE_SMOKE', '0'))
if SMOKE:
    combos = combos[:SMOKE]
    log(f"SMOKE 模式: 只跑前 {SMOKE} 组组合")
log(f"组合: C({n_sel},2)×4 = {len(combos)*4} 候选")
t0 = time.time()
for i, (a, b) in enumerate(combos):
    ca, cb = pl.col(a), pl.col(b)
    for name, expr in [
        (f'{a}_x_{b}', ca * cb),
        (f'{a}_d_{b}', ca - cb),
        (f'{a}_p_{b}', ca + cb),
        (f'{a}_r_{b}', ca / (cb + 1e-12)),
    ]:
        r = compute_ic_full(name, expr)
        if r: candidates.append(r)
    if (i + 1) % 50 == 0:
        log(f"  [{i+1}/{len(combos)}] 候选 {len(candidates)}, {time.time()-t0:.0f}s")
        pd.DataFrame([{k: v for k, v in c.items() if k != 'seg_ics'} for c in candidates]).to_csv(OUT, index=False)

# ---------- 4. 初筛 ----------
df_res = pd.DataFrame([{k: v for k, v in c.items() if k != 'seg_ics'} for c in candidates])
df_res.to_csv(OUT, index=False)
log(f"全部候选完成: {len(df_res)}")

mask = (df_res['icir'].abs() >= ICIR_MIN) & (df_res['seg_ok_ratio'] >= SEG_OK_MIN) & (df_res['last2_ok'])
short = df_res[mask].sort_values('icir', key=abs, ascending=False)
log(f"初筛通过（|ICIR|>={ICIR_MIN} 且 同号段>={SEG_OK_MIN:.0%} 且 最近2段同号）: {len(short)} 个")
if len(short) == 0:
    log("无候选通过初筛，结束。可放宽 ICIR_MIN / SEG_OK_MIN 后重跑。")
    sys.exit(0)
for _, r in short.head(20).iterrows():
    log(f"    {r['expr']:32} IC={r['ic_mean']:+.4f} ICIR={r['icir']:+.3f} 同号段={r['seg_ok_ratio']:.0%}")

# ---------- 5. 精算层（Top 30）：换手暴露 + quintile 单调性 ----------
TOP = int(sys.argv[sys.argv.index('--top')+1]) if '--top' in sys.argv else 30
short30 = short.head(TOP)
log(f"精算层: Top {len(short30)} 候选 → 换手暴露 + quintile 单调性")

def fine_eval(expr_str, expr, all_tr):
    d = df.with_columns(expr.alias('_f'))
    d2 = d.filter(pl.col('_f').is_not_null() & pl.col(HORIZON).is_not_null() & pl.col('turn_ratio').is_not_null())
    d2 = d2.with_columns([
        pl.col('_f').rank().over('日期').alias('_rk'),
        pl.col('_f').count().over('日期').alias('_n'),
    ])
    # 换手暴露：每日 top10% 股票的 turn_ratio 均值 / 全市场 turn_ratio 均值
    top_tr = (d2.filter(pl.col('_rk') >= pl.col('_n') * 0.9)
               .group_by('日期').agg(pl.col('turn_ratio').mean()))['turn_ratio'].mean()
    turn_exp = float(top_tr / all_tr) if all_tr else 1.0
    # quintile 单调性：5层 fwd_5d 均值，|Spearman(层,均值)|
    d2 = d2.with_columns((pl.col('_rk') / pl.col('_n') * 5).cast(pl.Int32).clip(0, 4).alias('_q'))
    q = d2.group_by('_q').agg(pl.col(HORIZON).mean().alias('m')).sort('_q')
    qv = q['m'].to_list()
    mono = abs(np.corrcoef(np.arange(len(qv)), qv)[0, 1]) if len(qv) >= 3 and np.std(qv) > 0 else 0.0
    spread = qv[-1] - qv[0] if len(qv) >= 2 else 0.0
    return turn_exp, round(float(mono), 3), round(float(spread) * 100, 3), [round(float(x) * 100, 2) for x in qv]

fine_rows = []
# 全市场 turn_ratio 均值（与候选无关，预计算一次）
_all_tr = (df.filter(pl.col('turn_ratio').is_not_null())
            .group_by('日期').agg(pl.col('turn_ratio').mean()))['turn_ratio'].mean()
for _, row in short30.iterrows():
    expr_str = row['expr']
    # 因子名可能含 '_'，用最后一次出现的运算符定位
    for opmark in ['_x_', '_d_', '_p_', '_r_']:
        if opmark in expr_str:
            a, b = expr_str.rsplit(opmark, 1)
            op = opmark[1]
            break
    ca, cb = pl.col(a), pl.col(b)
    expr = {'x': ca * cb, 'd': ca - cb, 'p': ca + cb, 'r': ca / (cb + 1e-12)}[op]
    turn_exp, mono, spread, qv = fine_eval(expr_str, expr, _all_tr)
    score = abs(row['icir']) / (turn_exp + 0.5)
    fine_rows.append({**row, 'turn_exp': round(turn_exp, 2), 'mono': mono,
                      'spread_pct': spread, 'quintiles': qv, 'score': round(float(score), 3)})

fine_df = pd.DataFrame(fine_rows).sort_values('score', ascending=False)
fine_df.to_csv(OUT.replace('.csv', '_fine.csv'), index=False)
log("=== 精算结果 Top 15（score = |ICIR|/(换手暴露+0.5)，附 quintile 单调性）===")
for _, r in fine_df.head(15).iterrows():
    log(f"  {r['expr']:32} score={r['score']:.3f} ICIR={r['icir']:+.3f} "
        f"换手暴露={r['turn_exp']:.2f} 单调={r['mono']:.2f} spread={r['spread_pct']:+.2f}% "
        f"Q1→Q5={r['quintiles']}")
log(f"输出: {OUT} / {OUT.replace('.csv','_fine.csv')}")
