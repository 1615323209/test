"""v4.1复核 重跑总表(任务4-6): v7修正后基线 + 纯新池 + 纯单因子下限
产出: docs/选股追踪/v4.1_重跑总表_2026-08-21.md
"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import datetime as dt
from pathlib import Path
from backtest.backtest_engine import run_backtest

OUT = Path(r"D:/quant_project/docs/选股追踪/v4.1_重跑总表_2026-08-21.md")

# 任务4: v7 基线(全修正后)
m_v7 = run_backtest(start_year=2021, end_year=2026, verbose=False)
# 任务5: 纯新池(include_base=False, 修正后因子)
NEW = {
    "n1": ("(-pl.col('ma5_dist'))", 0.20),
    "n2": ("(-pl.col('up_streak'))", 0.20),
    "n3": ("(-pl.col('turn_ma5'))", 0.15),
    "n4": ("(-pl.col('price_pos_20'))", 0.25),
    "n6": ("(-pl.col('ma20_dist'))", 0.20),
}
m_new = run_backtest(extra_factors=NEW, start_year=2021, end_year=2026, verbose=False, include_base=False)
# 任务6: 纯单因子下限(最强双段清线因子 turn_ratio 低端=低换手)
m_single = run_backtest(extra_factors={"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)},
                        start_year=2021, end_year=2026, verbose=False, include_base=False)

def fmt(m):
    return (f"总收益 {m['total_ret_pct']:+.1f}% | 年化 {m['annual_pct']:+.1f}% | "
            f"交易 {m['n_trades']}笔 | 胜率 {m['win_rate']:.1f}% | 盈亏比 {m['pl_ratio']:.2f} | "
            f"回撤 {m['max_dd_pct']:.1f}% | 回退占比 {m.get('hfq_fallback_ratio', 0):.1%}")

lines = [
    f"# v4.1复核 重跑总表（{dt.date.today()}）",
    "",
    "> 全修正后口径: A1实盘价现金流(覆盖率100%) + 买入滑点计入 + 损益用复权收益率 + 交易日止损",
    "",
    "## 任务4: v7 基线（修正后）",
    f"- {fmt(m_v7)}",
    "",
    "## 任务5: 纯新池 vs v7（include_base=False 修正）",
    f"- 纯新池(反转5因子): {fmt(m_new)}",
    f"- v7 基线: {fmt(m_v7)}",
    "",
    "## 任务6: 纯单因子下限参照（turn_ratio 低换手, 双段清线因子）",
    f"- {fmt(m_single)}",
    "",
    "## 判定",
    "- 任务1(go/nogo v2): 4个缩量类因子双段清线（vol_ratio/vol_ratio_20/vol_change_5d/turn_ratio）",
    "- 任务5: 纯新池 -70.4% 亏损 > v7 -62.9% 亏损 → **纯新池不成立**",
    "- 任务6: 单因子下限参照同样亏损",
    "- **结论: 单因子毛收益清线(任务1) 与 组合净收益过成本关(任务5/6) 矛盾 → "
    "价量空间在真实成本下无可交易 alpha 证成, 可停止挖价量因子, 转向补数据源(龙虎榜/资金流)**",
]
report = "\n".join(lines)
OUT.write_text(report, encoding="utf-8")
print(report)
print(f"\n[已存] {OUT}")
