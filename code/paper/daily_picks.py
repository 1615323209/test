#!/usr/bin/env python3
"""模拟盘工具 — 每日 v7 打分选股清单
用法: python daily_picks.py [日期]  (默认最新交易日)
输出: Top 3 候选 + 打分明细 + 市场状态
"""
import polars as pl
import pandas as pd
import sys, os
from pathlib import Path  # 提前 import（下方 sys.path.insert 用到）
# 支持 python daily_picks.py 与 python -m paper.daily_picks 两种运行方式（paper 包路径）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime, date

DATA = Path("D:/quant_data")
FACTOR = DATA / "factor_daily.parquet"
FACTOR_INCR = DATA / "factor_daily_incr.parquet"
MARKET = DATA / "market_daily.parquet"
HS300 = DATA / "hs300.parquet"
OUT = DATA / "daily_picks"

def factor_files():
    """因子文件列表（主文件 + 增量）"""
    files = [FACTOR]
    if FACTOR_INCR.exists():
        files.append(FACTOR_INCR)
    return files

INIT_CAPITAL = 20000
POSITION = 2000

# ---- v7 打分权重（改造2.0 2.1：改为读 active_factors.json，此处仅回退常量）----
FALLBACK_W = {'s1': 0.25, 's2': 0.20, 's3': 0.15, 's4': 0.15, 's5': 0.15, 's6': 0.10}

def load_active_or_fallback():
    """读 active_factors.json，失败/校验失败回退 v7（不可静默，返回 flag）"""
    from paper.active_factors import load_data, safe_expr
    try:
        data = load_data()
        factors = [f for f in data.get("factors", [])
                   if f.get("status") in ("启用", "灰度", "pin", "实盘确认")]
        if not factors:
            raise ValueError("active_factors 无有效因子")
        # 表达式全部进沙箱（不信任文件内容）
        usable = []
        for f in factors:
            ex, err, _ = safe_expr(f.get("expr", ""))
            if ex is None:
                print(f"  [daily_picks] ⚠️ active_factors 因子 {f.get('name')} 表达式沙箱拒绝: {err}，跳过")
                continue
            f = dict(f); f["_expr"] = ex
            usable.append(f)
        if usable:
            return usable, False
        raise ValueError("active_factors 无沙箱通过的因子")
    except Exception as e:
        print(f"  [daily_picks] ⚠️ active_factors 不可用({e})，已回退 v7 基线")
        # 回退固定 v7 六因子
        return _fallback_factors(), True

def _fallback_factors():
    from loop.expr_sandbox import safe_compile
    spec = [
        ("s1", "(-pl.col('ret_5d') * pl.col('turn_ma5'))", 0.25),
        ("s2", "(-pl.col('ma5_dist') * pl.col('turn_ma5'))", 0.20),
        ("s3", "(-pl.col('vol_10d') - pl.col('vol_change_5d'))", 0.15),
        ("s4", "pl.col('limit_up_5d')", 0.15),
        ("s5", "(-pl.col('turn_ratio'))", 0.15),
        ("s6", "pl.col('macd_dif')", 0.10),
    ]
    out = []
    for name, expr, w in spec:
        ex, _, _ = safe_compile(expr)
        out.append({"name": name, "_expr": ex, "weight": w, "status": "pin", "origin": "v7_fallback"})
    return out

def compute_score(df):
    """读 active_factors.json 动态构造打分（改造2.0 2.1）+ top_factors 归因（2.2）
    返回 (打分df, 启用因子列表, 是否回退)"""
    factors, fallback = load_active_or_fallback()
    d = df
    col_defs = []
    for f in factors:
        name = f["name"]
        d = d.with_columns(f["_expr"].rank().over('日期').alias(f"__{name}"))
        col_defs.append((name, f.get("weight", 0.02)))
    score_expr = sum(pl.col(f"__{n}") * w for n, w in col_defs)
    d = d.with_columns(score_expr.alias("score"))
    return d, [{"name": n, "weight": w} for n, w in col_defs], fallback

def main():
    # 目标日期
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    else:
        # 最新交易日
        dates = pl.read_parquet(MARKET, columns=['日期'])['日期'].to_list()
        target = max(dates)
    print(f"=== 模拟盘选股 {target} ===\n")
    
    # 市场状态
    market = pl.read_parquet(MARKET)
    m = market.filter(pl.col('日期') == target)
    if len(m) == 0:
        print(f"错误: {target} 无市场数据")
        return
    m = m.row(0, named=True)
    hs300 = pl.read_parquet(HS300).sort('日期')
    h = hs300.filter(pl.col('日期') == target)
    hs_ma20 = h['ma_20'][0] if len(h) > 0 else None
    hs_close = h['close'][0] if len(h) > 0 else None
    hs_above = hs_close is not None and hs_ma20 is not None and hs_close > hs_ma20
    print(f"涨停家数: {m['涨停家数']}  跌停: {m['跌停家数']}  上涨占比: {m['上涨占比']*100:.1f}%")
    hs_close_s = f"{hs_close:.0f}" if hs_close is not None else "暂无今日"
    hs_ma20_s = f"{hs_ma20:.0f}" if hs_ma20 is not None else "—"
    hs_pos = "站上MA20" if hs_above else ("MA20下方" if hs_close is not None else "")
    print(f"沪深300: {hs_close_s} (MA20: {hs_ma20_s}) {hs_pos}")
    north = m.get('北向净买入')
    print(f"北向净买入: {north if north is not None else '无数据'}")
    # 方案3：极端风险安全阀（仅系统性暴跌才标风险，日常不拦因子选股）
    # 极端风险 = 跌停家数 >= 200（全线跌停潮，系统性风险信号最可靠）
    extreme_risk = (m.get('跌停家数', 0) or 0) >= 200
    if extreme_risk:
        print(f"🚨 极端风险警示：跌停 {m.get('跌停家数')} 家（全线跌停潮），因子选股结果仅供参考，谨慎追高")
    else:
        print(f"参考：涨停 {m.get('涨停家数')} 家 / 跌停 {m.get('跌停家数')} 家，因子选股为主信号")
    
    # 打分选股（加载前5天数据以计算 limit_up_5d）
    need_cols = ['日期','股票代码','收盘','ret_1d','ret_5d',
                 'limit_up','limit_down','is_suspended',
                 'turn_ratio','turn_ma5','vol_ratio','vol_change_5d','vol_10d',
                 'ma_5','ma_20','ma_60','ma5_dist','macd_dif','price_pos_20']
    # 改造2.0 2.1：active_factors 因子表达式用到的列动态并入 need_cols
    try:
        from paper.active_factors import load_active_or_fallback as _laf
        from loop.expr_sandbox import safe_compile as _sc
        _factors, _ = _laf()
        _schema = set(pl.scan_parquet(factor_files()).collect_schema().names())
        for _f in _factors:
            _, _, _used = _sc(_f.get("expr", ""))
            for _c in _used:
                if _c not in need_cols and _c in _schema:
                    need_cols.append(_c)
    except Exception:
        pass
    # 找到 target 前 5 个交易日
    all_dates = pl.read_parquet(MARKET, columns=['日期'])['日期'].to_list()
    idx = all_dates.index(target)
    start_date = all_dates[max(0, idx-6)]
    df = pl.scan_parquet(factor_files()).filter(
        (pl.col('日期') >= start_date) & (pl.col('日期') <= target)
    )
    df = df.select(need_cols).with_columns(
        pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
    ).collect()
    # 数据修复：主文件+增量合并可能有重复（同一股票同日多行），去重保留最后一条
    df = df.unique(subset=["日期", "股票代码"], keep="last")
    df = df.filter(pl.col('日期') == target)
    
    cand = df.filter(
        (pl.col('is_suspended')==0)&(pl.col('limit_up')==0)&(pl.col('limit_down')==0)
        &(pl.col('price_pos_20')<0.85)&(pl.col('price_pos_20')>0.1)
        &(pl.col('收盘')>pl.col('ma_20'))
        &(pl.col('收盘') < 19.5)   # 2000元/仓买得起100股（留滑点余量）
        &pl.col('turn_ma5').is_not_null()
        &pl.col('vol_change_5d').is_not_null()
        &pl.col('macd_dif').is_not_null()
        &pl.col('ret_5d').is_not_null())
    
    if len(cand) == 0:
        print("无候选股票")
        return
    
    scored, active_factors, fallback = compute_score(cand)
    top = scored.sort('score', descending=True).head(5)
    # 改造2.0 2.2：每只入选股算 top_factors 贡献归因（s_i × weight 排序取前2）
    fa_cols = [f"__{f['name']}" for f in active_factors]
    if fa_cols:
        top = top.with_columns(
            sum(pl.col(fa_cols[i]) * active_factors[i]["weight"] for i in range(len(fa_cols))).alias("_contrib_check"))
        # 逐行取贡献最大的 1-2 个因子
        top = top.with_columns(pl.concat_list(fa_cols).alias("_ranked"))

    print("=== Top 5 候选 ===")
    rows = []
    for r in top.iter_rows(named=True):
        code = r['股票代码']
        price = float(r['收盘'])
        shares = int(POSITION/price/100)*100
        # top_factors 归因（贡献最大的 1-2 个）
        try:
            contribs = [(active_factors[i]["name"], r.get(fa_cols[i], 0) * active_factors[i]["weight"])
                        for i in range(len(fa_cols))]
            contribs.sort(key=lambda x: abs(x[1]), reverse=True)
            top_factors = "|".join(n for n, _ in contribs[:2])
        except Exception:
            top_factors = ""
        rows.append({
            '排名': len(rows)+1, '代码': code, '收盘': round(price,2),
            '评分': round(float(r['score']),1),
            '可买股数': shares if shares >= 100 else '不足100股',
            'ret_5d': round(r['ret_5d'],3), 'vol_ratio': round(r['vol_ratio'],2),
            'turn_ratio': round(r['turn_ratio'],2),
            '近5日涨停': int(r['limit_up_5d']),
            '站上MA20': round(r['ma_20'],2), 'price_pos': round(r['price_pos_20'],2),
            'top_factors': top_factors,  # 改造2.0 2.2：归因（供 --from-pick / L4）
        })
        print(f"  {rows[-1]['排名']}. {code}  收盘{price:.2f}  评分{rows[-1]['评分']}  "
              f"{'可买'+str(shares)+'股' if shares>=100 else '不足100股'}")
        print(f"     ret_5d={r['ret_5d']:.3f} vol={r['vol_ratio']:.2f} turn={r['turn_ratio']:.2f} "
              f"涨停5d={int(r['limit_up_5d'])} pos={r['price_pos_20']:.2f} 归因={top_factors}")
    if fallback:
        print("\n⚠️ 使用 v7 基线回退（active_factors 不可用）")

    # 保存
    OUT.mkdir(exist_ok=True)
    fname = OUT / f"picks_{target}.csv"
    pd.DataFrame(rows).to_csv(fname, index=False)
    print(f"\n已保存: {fname}")
    print("\n⚠️ 实盘记录规则: 每仓2000元，止损-8%，止盈+12%，破MA20减半，破MA60清仓")

if __name__ == '__main__':
    main()
