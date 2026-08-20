"""隔夜段可行性测算（只读，不写任何数据）

问题：v7 选出的票，次日收益里"隔夜段"（今收→次开）占多少？够不够覆盖往返成本？

做法：把次日单日收益拆成两段——
gap = 次日开盘 / 今日收盘 - 1 （隔夜，持仓约 17 小时）
intraday = 次日收盘 / 次日开盘 - 1 （日内，持仓约 4 小时）
fullday = 次日收盘 / 今日收盘 - 1 （= 现有 fwd_1d 口径）

若 edge 主要在 gap → "今收买、次开卖"这条最短合法周期成立；
若主要在 intraday → 最短周期不成立，缩周期等于把利润段砍掉。

口径与防泄漏：
- 只用 T 日及以前的数据构造打分，标签取 T+1 的开盘/收盘，不引用任何 fwd_* 列
- 复权口径：行情与因子库同源后复权（a_stock_daily_hfq），避免除权跳变造成假 gap
- 次日必须是"紧邻交易日"（自然日间隔 <=4 天），否则丢弃，防停牌复牌跳空污染
- 候选池与实盘一致（daily_picks 的过滤条件），排名也在候选池内做

用法（在 D:\\quant_project\\code 下）:
& "D:\\02_download\\APP\\Anaconda\\python.exe" -m research.overnight_edge
"""
import polars as pl
from pathlib import Path

DATA = Path("D:/quant_data")
FACTOR = DATA / "factor_daily.parquet"
FACTOR_INCR = DATA / "factor_daily_incr.parquet"
RAW = DATA / "a_stock_daily_hfq.parquet"

POSITION = 2000.0  # 单仓金额（宪法：2万本金 / 10仓）
COMM_RATE, COMM_MIN = 0.00025, 5.0
STAMP_RATE = 0.0005  # 印花税，仅卖出
SLIP_LO, SLIP_HI = 0.001, 0.003  # 单边滑点区间（backtest_engine 口径）
TOP_N = 3  # v7 每日买入数

# v7 六因子（权威定义：backtest_engine.V7_BASE_FACTORS）
V7 = [
    ("s1", (-pl.col('ret_5d') * pl.col('turn_ma5')), 0.25),
    ("s2", (-pl.col('ma5_dist') * pl.col('turn_ma5')), 0.20),
    ("s3", (-pl.col('vol_10d') - pl.col('vol_change_5d')), 0.15),
    ("s4", pl.col('limit_up_5d'), 0.15),
    ("s5", (-pl.col('turn_ratio')), 0.15),
    ("s6", pl.col('macd_dif'), 0.10),
]

NEED = ['日期', '股票代码', '收盘', 'ret_1d', 'ret_5d', 'turn_ratio', 'turn_ma5',
        'ma5_dist', 'vol_10d', 'vol_change_5d', 'macd_dif', 'ma_20', 'price_pos_20',
        'limit_up', 'limit_down', 'is_suspended']


def round_trip_cost_pct(slip):
    """一次往返成本占仓位比例（买入=收盘价，卖出=次日开盘价）"""
    buy_comm = max(COMM_MIN, POSITION * COMM_RATE)
    sell_comm = max(COMM_MIN, POSITION * COMM_RATE)
    stamp = POSITION * STAMP_RATE
    slip_cost = POSITION * slip * 2
    return (buy_comm + sell_comm + stamp + slip_cost) / POSITION * 100


def load():
    files = [FACTOR] + ([FACTOR_INCR] if FACTOR_INCR.exists() else [])
    d = pl.scan_parquet(files).select(NEED).unique(subset=['日期', '股票代码'], keep='last')
    raw = pl.scan_parquet(RAW).select(['日期', '股票代码', '开盘', '最高', '最低'])
    d = d.join(raw, on=['日期', '股票代码'], how='left')
    d = d.filter(pl.col('收盘').is_finite() & (pl.col('收盘') > 0)
                 & pl.col('开盘').is_finite() & (pl.col('开盘') > 0))
    d = d.sort(['股票代码', '日期'])
    d = d.with_columns([
        pl.col('limit_up').rolling_sum(5, min_samples=5).over('股票代码').alias('limit_up_5d'),
        pl.col('开盘').shift(-1).over('股票代码').alias('open_next'),
        pl.col('收盘').shift(-1).over('股票代码').alias('close_next'),
        pl.col('日期').shift(-1).over('股票代码').alias('date_next'),
        pl.col('limit_down').shift(-1).over('股票代码').alias('ld_next'),
    ])
    d = d.with_columns(
        (pl.col('date_next') - pl.col('日期')).dt.total_days().alias('date_diff'))
    d = d.filter(pl.col('open_next').is_not_null() & (pl.col('date_diff') <= 4))
    d = d.with_columns([
        (pl.col('open_next') / pl.col('收盘') - 1).alias('gap'),
        (pl.col('close_next') / pl.col('open_next') - 1).alias('intraday'),
        (pl.col('close_next') / pl.col('收盘') - 1).alias('fullday'),
        (pl.col('最高') / pl.col('最低') - 1).alias('range_today'),
        pl.col('日期').dt.year().alias('年份'),
    ])
    # 涨跌停幅度上限内（超出 21% 视为数据异常/复权残留）
    d = d.filter(pl.col('gap').abs() <= 0.21)
    return d.collect(streaming=True)


def universe_stats(d):
    print("\n=== 一、全市场隔夜跳空分布（每只股票每天一个样本）===")
    print(f"{'年份':>6} {'样本数':>10} {'均值%':>8} {'中位%':>8} {'标准差%':>8} "
          f"{'P(>0)':>7} {'P(>0.9%)':>9} {'q25%':>7} {'q75%':>7}")
    for y, g in sorted(d.group_by('年份'), key=lambda x: x[0]):
        gp = g['gap'].drop_nulls()
        if len(gp) == 0:
            continue
        print(f"{y[0] if isinstance(y, tuple) else y:>6} {len(gp):>10,} "
              f"{gp.mean()*100:>8.3f} {gp.median()*100:>8.3f} {gp.std()*100:>8.2f} "
              f"{(gp > 0).mean()*100:>6.1f}% {(gp > 0.009).mean()*100:>8.1f}% "
              f"{gp.quantile(0.25)*100:>7.2f} {gp.quantile(0.75)*100:>7.2f}")
    gp = d['gap'].drop_nulls()
    print(f"{'全期':>6} {len(gp):>10,} {gp.mean()*100:>8.3f} {gp.median()*100:>8.3f} "
          f"{gp.std()*100:>8.2f} {(gp > 0).mean()*100:>6.1f}% {(gp > 0.009).mean()*100:>8.1f}% "
          f"{gp.quantile(0.25)*100:>7.2f} {gp.quantile(0.75)*100:>7.2f}")


def v7_picks(d):
    """按 daily_picks 的实盘口径过滤候选池 → 池内排名 → 取 TOP_N"""
    cand = d.filter(
        (pl.col('is_suspended') == 0) & (pl.col('limit_up') == 0) & (pl.col('limit_down') == 0)
        & (pl.col('price_pos_20') < 0.85) & (pl.col('price_pos_20') > 0.1)
        & (pl.col('收盘') > pl.col('ma_20'))
        & (pl.col('收盘') < 19.5)
        & pl.col('turn_ma5').is_not_null() & pl.col('vol_change_5d').is_not_null()
        & pl.col('macd_dif').is_not_null() & pl.col('ret_5d').is_not_null()
        & pl.col('limit_up_5d').is_not_null() & pl.col('vol_10d').is_not_null()
        & pl.col('turn_ratio').is_not_null() & pl.col('ma5_dist').is_not_null())
    scored = cand.with_columns(
        [e.rank().over('日期').alias(f'__{n}') for n, e, _ in V7])
    scored = scored.with_columns(
        sum(pl.col(f'__{n}') * w for n, _, w in V7).alias('score'))
    top = scored.filter(
        pl.col('score').rank(descending=True).over('日期') <= TOP_N)
    return scored, top


def decompose(cand, top):
    print(f"\n=== 二、v7 Top{TOP_N} 的次日收益分解（隔夜 / 日内）===")
    print(f"{'年份':>6} {'选股数':>8} {'隔夜%':>8} {'日内%':>8} {'整日%':>8} "
          f"{'隔夜占比':>9} {'隔夜P(>0)':>10}")
    for y, g in sorted(top.group_by('年份'), key=lambda x: x[0]):
        yy = y[0] if isinstance(y, tuple) else y
        gp, ind, fd = g['gap'], g['intraday'], g['fullday']
        share = (gp.mean() / fd.mean() * 100) if fd.mean() not in (None, 0) else float('nan')
        print(f"{yy:>6} {len(g):>8,} {gp.mean()*100:>8.3f} {ind.mean()*100:>8.3f} "
              f"{fd.mean()*100:>8.3f} {share:>8.0f}% {(gp > 0).mean()*100:>9.1f}%")
    gp, ind, fd = top['gap'], top['intraday'], top['fullday']
    share = (gp.mean() / fd.mean() * 100) if fd.mean() not in (None, 0) else float('nan')
    print(f"{'全期':>6} {len(top):>8,} {gp.mean()*100:>8.3f} {ind.mean()*100:>8.3f} "
          f"{fd.mean()*100:>8.3f} {share:>8.0f}% {(gp > 0).mean()*100:>9.1f}%")

    print(f"\n=== 三、Top{TOP_N} 相对候选池（有没有选股能力）===")
    print(f"{'口径':>14} {'隔夜均值%':>10} {'日内均值%':>10} {'整日均值%':>10} {'隔夜P(>0)':>10}")
    for label, s in (("候选池全体", cand), (f"v7 Top{TOP_N}", top)):
        print(f"{label:>14} {s['gap'].mean()*100:>10.3f} {s['intraday'].mean()*100:>10.3f} "
              f"{s['fullday'].mean()*100:>10.3f} {(s['gap'] > 0).mean()*100:>9.1f}%")
    edge = (top['gap'].mean() - cand['gap'].mean()) * 100
    print(f" 隔夜段选股超额（Top - 池均值）: {edge:+.3f}%")

    # IC：打分 vs 隔夜标签（横截面 Spearman，逐日算再平均）
    print("\n=== 四、v7 打分对隔夜段的横截面 IC ===")
    ic_tbl = (cand.select(['日期', 'score', 'gap', 'intraday', 'fullday'])
              if 'score' in cand.columns else None)
    if ic_tbl is None:
        print(" （候选池未带 score，跳过）")
    else:
        for lab in ('gap', 'intraday', 'fullday'):
            per_day = (ic_tbl.drop_nulls(['score', lab])
                       .group_by('日期')
                       .agg(pl.corr('score', lab, method='spearman').alias('ic'))
                       .drop_nulls('ic'))
            ic = per_day['ic']
            icir = ic.mean() / ic.std() if ic.std() else float('nan')
            print(f" {lab:>9}: IC均值={ic.mean():+.4f} IC标准差={ic.std():.4f} "
                  f"ICIR={icir:+.3f} 正IC天数占比={(ic > 0).mean()*100:.1f}% N={len(ic)}天")


def cost_verdict(top):
    print("\n=== 五、成本线判定（单仓 2000 元，今收买入、次开卖出）===")
    for slip, name in ((SLIP_LO, "乐观(滑点0.1%)"), ((SLIP_LO + SLIP_HI) / 2, "中性(滑点0.2%)"),
                       (SLIP_HI, "悲观(滑点0.3%)")):
        c = round_trip_cost_pct(slip)
        net = top['gap'].mean() * 100 - c
        n_year = len(top) / max(top['年份'].n_unique(), 1)
        money = net / 100 * POSITION * n_year
        print(f" {name}: 往返成本={c:.3f}% 隔夜毛收益={top['gap'].mean()*100:.3f}% "
              f"净={net:+.3f}%/笔 → {n_year:.0f}笔/年 × 2000元 ≈ {money:+.0f} 元/年")
    print(" 注：卖在次日开盘，若开盘跌停则卖不掉（下面给出占比）")
    ld = top['ld_next'].drop_nulls()
    if len(ld):
        print(f" 次日开盘跌停（不可卖）占比: {(ld == 1).mean()*100:.2f}%")


def main():
    print("=== 隔夜段可行性测算（只读）===")
    for p in (FACTOR, RAW):
        if not p.exists():
            print(f"缺文件: {p}")
            return
    d = load()
    print(f"样本: {len(d):,} 行, {d['日期'].min()} ~ {d['日期'].max()}, "
          f"{d['股票代码'].n_unique():,} 只股票")
    universe_stats(d)
    cand, top = v7_picks(d)  # cand 已带全池 score，供 IC 计算
    print(f"候选池: {len(cand):,} 股票-天, 选中: {len(top):,} 股票-天, "
          f"覆盖 {top['日期'].n_unique():,} 个交易日")
    decompose(cand, top)
    cost_verdict(top)
    print("\n=== 结论怎么读 ===")
    print(" · 若『隔夜占比』>60% 且隔夜净收益为正 → 今收买次开卖成立，可进一步做完整回测")
    print(" · 若『隔夜占比』<40% → 利润在日内，T+1 下你拿不到，缩周期是负收益")
    print(" · 若隔夜 IC 与整日 IC 同号且量级接近 → v7 因子对隔夜段同样有效")
    print(" · 若隔夜净收益为负但毛收益为正 → 是成本问题，看第五章需要多大本金才翻正")


if __name__ == "__main__":
    main()
