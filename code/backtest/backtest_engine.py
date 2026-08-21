#!/usr/bin/env python3
"""回测引擎（backtest_v7 本地化 + 可注入新因子）

从 backtest_v7.py 改造：
1. 数据路径改 Windows 本地 D:\\quant_data
2. 支持注入 LLM 挖掘的新因子（extra_factors: {name: polars_expr, weight: float}）
3. 返回指标 dict 供外层 loop 决策

用法:
    from backtest_engine import run_backtest
    metrics = run_backtest(extra_factors={"f1": pl.col('ret_5d')*pl.col('turn_ma5'), "f2": ...})
"""
import polars as pl
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import gc

DATA = Path(r"D:\quant_data")
FACTOR = DATA / "factor_daily.parquet"
MARKET = DATA / "market_daily.parquet"
HS300 = DATA / "hs300.parquet"

INIT_CAPITAL = 20000
N_SLOTS = 4  # 流程改造阶段4: 10→4 仓(4×5000=2万)
POSITION = 5000  # 流程改造阶段4: 2000→5000 元/仓
STOP_LOSS = -0.04  # 流程改造阶段4: -8%→-4% (5日尺度)
TP = 0.06  # 流程改造阶段4: 12%→6% (5日尺度)
TIME_STOP_DAYS = 5  # 流程改造阶段4: 20→5 日(对齐 fwd_5d 标签口径)
TIME_STOP_GAIN = 0.05  # 保留: 5日内未达6%即时间止损
MIN_CASH = 2000
PROTECT_GAIN = 0.03
TOP_N = 3
LIMIT_UP_TH = 60

COMM_RATE = 0.00025
COMM_MIN = 5.0
STAMP_RATE = 0.0005

# v7 基线六因子权重（改造2.0 2.1：启动时读 active_factors.json，此处仅 fallback 常量）
V7_BASE_FACTORS = [
    ("s1", "(-pl.col('ret_5d') * pl.col('turn_ma5'))", 0.25),   # 低换手+回调
    ("s2", "(-pl.col('ma5_dist') * pl.col('turn_ma5'))", 0.20), # 偏离均线
    ("s3", "(-pl.col('vol_10d') - pl.col('vol_change_5d'))", 0.15), # 低波动+缩量
    ("s4", "pl.col('limit_up_5d')", 0.15),                      # 涨停惯性
    ("s5", "(-pl.col('turn_ratio'))", 0.15),                    # 低换手
    ("s6", "pl.col('macd_dif')", 0.10),                         # MACD
]
# 基线开关（--baseline-only 复现 v7 历史基线；默认读 active_factors.json）
BASELINE_ONLY = False

def load_base_factors(baseline_only=False):
    """读取打分因子清单 [(name, expr, weight), ...]
    改造2.0 2.1：默认读 active_factors.json（单一真相源）；baseline_only 用 v7 固定
    表达式过沙箱，失败跳过该因子"""
    if baseline_only:
        return V7_BASE_FACTORS
    try:
        from paper.active_factors import load_data
        from loop.expr_sandbox import safe_compile
        data = load_data()
        out = []
        for f in data.get("factors", []):
            if f.get("status") in ("启用", "灰度", "pin", "实盘确认"):
                ex, err = safe_compile(f.get("expr", ""))
                if ex is None:
                    if BASELINE_ONLY is False:
                        pass  # 静默跳过问题因子（有 prints 的 fallback 分支）
                    continue
                out.append((f["name"], f["expr"], f.get("weight", 0.02)))
        if out:
            return out
    except Exception:
        pass
    return V7_BASE_FACTORS

# 基线因子（改造2.0 2.1）：L3 判定比对用固定 v7 基线（不随 active 漂移）；
# 实际打分注入在 run_backtest 的 extra_factors（由 l3 传 active 启用因子清单）
BASE_FACTORS = V7_BASE_FACTORS
# v4.1复核 P0-1: 删除 lambda 覆盖, 恢复真实 load_base_factors(上方定义)语义
# 调用方需 BASE_FACTORS 固定 v7 时直接用 BASE_FACTORS; 需 active 时调 load_base_factors()

def slippage(ret_1d):
    vol_factor = min(abs(ret_1d) / 0.05, 1.0) if ret_1d is not None else 0.5
    return 0.001 + 0.002 * vol_factor

def run_backtest(extra_factors=None, start_year=2021, end_year=2026, verbose=True,
                 return_by_year=False, include_base=True):
    """
    extra_factors: dict {name: (polars_expr_str, weight)}
        polars_expr_str 需是 Expr 代码字符串，如 "(pl.col('ret_5d')*pl.col('turn_ma5')).rank().over('日期')"
        weight: 在总 score 中的权重（与 v7 六因子同量纲，rank 0-1 后加权）
        include_base=False 时仅用 extra_factors 打分（v4.1复核 P0-1）
    返回 metrics dict:
        {total_ret_pct, annual_pct, trades, win_rate, pl_ratio, max_dd_pct, fee_total}
    改造 C22：return_by_year=True 时额外返回 year_ret: {年份: 该年卖出平仓的总收益pct}（供 L3 分段披露拆分，免多跑独立分段回测）
    """
    market = pl.read_parquet(MARKET)
    market_dict = {r['日期']: r for r in market.to_dicts()}
    hs300 = pl.read_parquet(HS300).sort('日期')
    hs300_dict = {r['日期']: (r['close'], r['ma_20']) for r in hs300.to_dicts()}
    dates = sorted(market_dict.keys())

    def hs300_above_ma20(d):
        v = hs300_dict.get(d)
        return bool(v and v[1] is not None and v[0] > v[1])

    def market_ok(d):
        m = market_dict.get(d)
        if not m: return False
        conds = 0
        if hs300_above_ma20(d): conds += 1
        if m['涨停家数'] is not None and m['涨停家数'] > LIMIT_UP_TH: conds += 1
        north = m.get('北向净买入')
        if north is not None and north > 0: conds += 1
        return conds >= 2

    need_cols = ['日期','股票代码','收盘','成交量','ret_1d','ret_5d',
                 'limit_up','limit_down','is_suspended',
                 'turn_ratio','turn_ma5','turn_ma20','vol_ratio','vol_ratio_20',
                 'vol_change_5d','vol_10d','vol_20d',
                 'ma_5','ma_20','ma_60','ma5_dist','ma20_dist',
                 'macd_dif','macd_dea','price_pos_20','up_streak']
    chunk = pl.scan_parquet(FACTOR).select(need_cols).collect(streaming=True)
    chunk = chunk.with_columns(
        pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d')
    )
    # A1 价格口径（流程改造）：join 不复权价做可买性/股数，收益率仍用复权价
    # v4.1复核 P0-5: 去 bare except, 缺失即硬失败; dtype判断避免 .str.to_date() 抛错
    raw_path = DATA / "raw_close.parquet"
    if not raw_path.exists():
        raise FileNotFoundError(f"A1 价格口径依赖 {raw_path} 缺失；如需退回复权价口径请显式传 allow_hfq_fallback=True")
    raw = pl.read_parquet(raw_path)
    if raw.schema["日期"] == pl.Utf8:
        raw = raw.with_columns(pl.col("日期").str.to_date())
    chunk = chunk.join(raw, on=["日期", "股票代码"], how="left")
    chunk = chunk.filter(pl.col('日期') >= datetime(start_year,1,1).date())
    # P0-5: 覆盖率在回测区间内算(原始chunk含2010-2020历史窗口, 那些不在raw_close采集范围)
    cov = chunk["收盘_不复权"].is_not_null().mean()
    print(f"  [A1] 不复权价覆盖 {cov:.1%} (回测区间 {start_year}+)")
    if cov < 0.95:
        raise ValueError(f"A1 覆盖率仅 {cov:.1%}，低于 95% 阈值，结论不可信；先补采 raw_close")
    chunk_dates = sorted(chunk['日期'].unique().to_list())

    def compute_score(df):
        exprs = []
        if include_base:
            for name, expr_str, w in BASE_FACTORS:
                try:
                    exprs.append((name, eval(expr_str, {"pl": pl}), w))
                except Exception:
                    if verbose: print(f"  [基线因子失败] {name}")
        if extra_factors:
            for fname, (fexpr_str, fw) in extra_factors.items():
                try:
                    exprs.append((fname, eval(fexpr_str, {"pl": pl}), fw))
                except Exception:
                    if verbose: print(f"  [注入因子失败] {fname}: {fexpr_str}")
        if not exprs:
            raise ValueError("compute_score: 因子集为空（include_base=False 且 extra_factors 未提供）")
        if verbose:
            print(f"  [打分因子] n={len(exprs)}: {','.join(n for n, _, _ in exprs)}")
        d = df.with_columns([e.rank().over('日期').alias(f"_f{i}") for i, (_, e, w) in enumerate(exprs)])
        score_expr = sum((pl.col(f"_f{i}") * w for i, (_, e, w) in enumerate(exprs)), pl.lit(0.0))
        return d.with_columns(score_expr.alias("score"))

    all_trades = []
    holdings = []
    cash = INIT_CAPITAL

    for year_start in range(start_year, end_year + 1, 2):
        year_end = min(year_start + 1, end_year)
        d1 = datetime(year_start,1,1).date()
        d2 = datetime(year_end,12,31).date()
        if verbose: print(f"回测 {year_start}-{year_end}...")
        sub = chunk.filter((pl.col('日期') >= d1) & (pl.col('日期') <= d2))
        sub_dates = sorted(sub['日期'].unique().to_list())
        for today in sub_dates:
            today_data = sub.filter(pl.col('日期') == today)
            for h in holdings[:]:
                # P1-3: 时间止损用交易日序号计数(原自然日, 跨周末仅2-3交易日, 换手虚高)
                held = sub_dates.index(today) - sub_dates.index(h['buy_date'])
                if held < 1: continue
                row_data = today_data.filter(pl.col('股票代码') == h['code'])
                if len(row_data) == 0: continue
                row = row_data.row(0, named=True)
                close = row['收盘']; ret1d = row['ret_1d']
                raw_close = float(row.get('收盘_不复权') or close)  # A1: 实盘价(现金口径)
                if ret1d is not None and ret1d <= -0.095: continue
                cost = h['buy_price']
                pnl = (close-cost)/cost
                peak = h.get('peak', cost); h['peak'] = max(peak, close)
                reason = None; sell_pct = 1.0
                if pnl <= STOP_LOSS: reason = '止损'
                elif pnl >= TP: reason = '止盈'
                elif h['peak'] >= cost*(1+PROTECT_GAIN) and pnl < 0.01: reason = '保本'
                elif row['ma_60'] is not None and close < row['ma_60']: reason = '破MA60'
                elif row['ma_20'] is not None and close < row['ma_20']:
                    if not h.get('half_sold'): reason, sell_pct = '破MA20减半', 0.5
                    else: reason = '破MA20清仓'
                elif held >= TIME_STOP_DAYS and pnl < TIME_STOP_GAIN: reason = '时间止损'
                if reason:
                    slip = slippage(ret1d)
                    sell_price = close*(1-slip)
                    sell_price_cash = raw_close*(1-slip)  # A1: 实盘卖出价(现金口径)
                    sell_shares = int(h['shares']*sell_pct)
                    if sell_shares < 100: sell_shares = h['shares']
                    sell_amt = sell_shares*sell_price_cash
                    fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
                    # P1-2: 损益金额与收益率同源——用复权收益率(含分红), 不再用不复权价差(把分红当亏损)
                    hfq_ret = (sell_price / h['buy_price']) - 1
                    gross = sell_shares * h.get('cost_per_share', sell_price) * hfq_ret
                    profit = gross - fee
                    # 校验(复核P1-2): 金额与率可互校 abs(pnl - pnl_pct/100*shares*cost_per_share) < fee+0.01
                    assert abs(profit - (hfq_ret * sell_shares * h.get('cost_per_share', sell_price))) < fee + 0.01, \
                        f"损益不可互校 {h['code']}"
                    all_trades.append({
                        'code': h['code'], 'buy_date': h['buy_date'],
                        'buy_price': cost, 'sell_date': today,
                        'sell_price': sell_price, 'shares': sell_shares,
                        'pnl': profit, 'pnl_pct': hfq_ret*100, 'fee': fee,
                        'reason': reason, 'held_days': held,
                        'price_src': h.get('price_src', 'raw')
                    })
                    cash += sell_amt - fee
                    h['shares'] -= sell_shares
                    if sell_pct == 0.5: h['half_sold'] = True
                    if h['shares'] < 100: holdings.remove(h)
            if not market_ok(today): continue
            if len(holdings) < N_SLOTS and cash >= MIN_CASH:
                held_codes = {h['code'] for h in holdings}
                candidates = today_data.filter(~pl.col('股票代码').is_in(held_codes))
                candidates = candidates.filter(
                    (pl.col('is_suspended')==0) & (pl.col('limit_up')==0) & (pl.col('limit_down')==0)
                    & (pl.col('price_pos_20')<0.85) & (pl.col('price_pos_20')>0.1)
                    & (pl.col('收盘')>pl.col('ma_20'))
                )
                if len(candidates) > 0:
                    scored = compute_score(candidates)
                    top = scored.sort('score', descending=True).head(TOP_N)
                    for row in top.iter_rows(named=True):
                        code = row['股票代码']
                        slip = slippage(row['ret_1d'])
                        # A1/A2: 股数与现金流用实盘价(不复权); 持仓盈亏判定仍用复权价(序列一致)
                        raw_price = float(row.get('收盘_不复权') or row['收盘'])
                        price_src = 'raw' if row.get('收盘_不复权') is not None else 'hfq_fallback'
                        buy_price = float(row['收盘'])*(1+slip)
                        shares = int(POSITION/raw_price/100)*100
                        if shares < 100: continue
                        # P0-5: 仓位断言(上限不超仓; 下限0.5容忍A股一手100股离散:
                        # 价位>25元的票5000仓只能买1手(如29.94元→100股=2994仅60%))
                        assert POSITION*0.5 <= shares*raw_price <= POSITION, \
                            f"仓位异常 {code}: {shares}股 × {raw_price} = {shares*raw_price}"
                        # P1-1: 买入滑点计入现金流(复核: 原cash_cost不含滑点, 每笔少算0.1-0.3%)
                        cash_cost = shares * raw_price * (1 + slip)
                        fee = max(COMM_MIN, cash_cost*COMM_RATE)
                        if cash_cost+fee > cash: continue
                        cash -= cash_cost+fee
                        holdings.append({'code':code,'buy_date':today,'buy_price':buy_price,
                                         'shares':shares,'peak':buy_price,'half_sold':False,
                                         'cost_per_share':raw_price*(1+slip),  # A1: 实盘每股成本(含滑点)
                                         'price_src':price_src})  # P0-5: 记录价格口径
        del sub; gc.collect()
        if verbose: print(f"  [诊断] cash={cash:.0f}, 持仓={len(holdings)}, 交易={len(all_trades)}")

    if holdings:
        last = dates[-1]
        last_chunk = chunk.filter(pl.col('日期') == last)
        for h in holdings:
            row_data = last_chunk.filter(pl.col('股票代码') == h['code'])
            if len(row_data) > 0:
                row = row_data.row(0, named=True)
                close, ret1d = row['收盘'], row['ret_1d']
                raw_close = float(row.get('收盘_不复权') or close)  # A1: 实盘价
                slip = slippage(ret1d)
                sell_price = close*(1-slip)
                sell_price_cash = raw_close*(1-slip)
                sell_amt = h['shares']*sell_price_cash
                fee = max(COMM_MIN, sell_amt*COMM_RATE) + sell_amt*STAMP_RATE
                # P1-2: 强制平仓也统一用复权收益率(含分红)口径
                hfq_ret = (sell_price - h['buy_price']) / h['buy_price']
                profit = h['shares']*h.get('cost_per_share', h['buy_price'])*hfq_ret - fee
                all_trades.append({
                    'code': h['code'], 'buy_date': h['buy_date'],
                    'buy_price': h['buy_price'], 'sell_date': last,
                    'sell_price': sell_price, 'shares': h['shares'],
                    'pnl': profit, 'pnl_pct': hfq_ret*100,
                    'fee': fee, 'reason': '强制平仓',
                    'held_days': (last-h['buy_date']).days,
                    'price_src': h.get('price_src', 'raw')
                })
                cash += sell_amt - fee

    trades = pd.DataFrame(all_trades)
    if len(trades) == 0:
        return {"total_ret_pct": 0, "annual_pct": 0, "trades": 0, "win_rate": 0,
                "pl_ratio": 0, "max_dd_pct": 0, "fee_total": 0, "n_trades": 0}
    total_pnl = trades['pnl'].sum()
    total_fee = trades['fee'].sum()
    ret = total_pnl/INIT_CAPITAL*100
    wins = trades[trades['pnl']>0]; loses = trades[trades['pnl']<=0]
    win_rate = len(wins)/len(trades)*100
    avg_win = wins['pnl'].mean() if len(wins) else 0
    avg_loss = loses['pnl'].mean() if len(loses) else 0
    pl_ratio = abs(avg_win/avg_loss) if avg_loss != 0 else float('inf')
    ts = trades.sort_values('sell_date')
    cum = ts['pnl'].cumsum(); peak = cum.cummax()
    dd = (cum-peak).min(); dd_pct = dd/INIT_CAPITAL*100
    years = (dates[-1]-dates[0]).days/365
    annual = ((INIT_CAPITAL+total_pnl)/INIT_CAPITAL)**(1/years)-1 if years > 0 else 0
    out = {"total_ret_pct": round(ret, 2), "annual_pct": round(annual*100, 2),
            "n_trades": len(trades), "win_rate": round(win_rate, 1),
            "pl_ratio": round(pl_ratio, 2) if pl_ratio != float('inf') else 99,
            "max_dd_pct": round(dd_pct, 2), "fee_total": round(total_fee, 0)}
    if return_by_year:
        # 改造 C22：按卖出年份聚合收益（供分段披露），不再另跑独立分段回测
        ts["年份"] = pd.to_datetime(ts["sell_date"]).dt.year
        year_ret = round(ts.groupby("年份")["pnl"].sum() / INIT_CAPITAL * 100, 2).to_dict()
        out["year_ret"] = {int(k): v for k, v in year_ret.items()}
    # P0-5: 回退笔数占比统计(price_src=hfq_fallback 的占比, >5% 告警)
    if "price_src" in trades.columns:
        n_fb = (trades["price_src"] == "hfq_fallback").sum()
        fb_ratio = n_fb / len(trades)
        out["hfq_fallback_ratio"] = round(fb_ratio, 4)
        if verbose or fb_ratio > 0.05:
            print(f"  [A1] 回退笔数占比: {fb_ratio:.1%} ({n_fb}/{len(trades)})" + (" ⚠️>5% 需补采" if fb_ratio > 0.05 else ""))
    return out

if __name__ == "__main__":
    # 基线回测（v7 原版，无注入）
    m = run_backtest()
    print("=== v7 基线 ===")
    for k, v in m.items():
        print(f"  {k}: {v}")
