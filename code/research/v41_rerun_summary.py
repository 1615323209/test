"""v4.2复核 重跑总表(任务4-6): v7修正后基线 + 纯新池(双段清线4因子) + 纯单因子下限
v4.2修正:
- A1: 买入佣金进pnl(backtest_engine已修)
- A2: 任务5因子池 = go_nogo_grid_v2 双段清线的4个缩量因子(与任务1对齐)
- A3: 判定文字全部从 metrics 生成, 禁止数字字面量
产出: docs/选股追踪/v4.1_重跑总表_2026-08-21.md (覆盖更新)
"""
import os
import sys
from pathlib import Path, os
PROJ = Path(os.environ.get("QUANT_PROJECT", r"D:/quant_project"))
sys.path.insert(0, str(PROJ / "code"))
import datetime as dt
from backtest.backtest_engine import run_backtest

OUT = Path(r"D:/quant_project/docs/选股追踪/v4.1_重跑总表_2026-08-21.md")

# A2(v4.2): 双段清线因子清单(go_nogo_grid_v2_2026-08-21.md 常量)
# IC 均为负 → 拟交易端是低端 → 表达式取负号(买低端)
CLEARED = ["vol_ratio", "turn_ratio", "vol_ratio_20", "vol_change_5d"]
NEW = {
    "n1": ("(-pl.col('vol_ratio'))", 0.25),      # 双段 +1.24/+1.00
    "n2": ("(-pl.col('turn_ratio'))", 0.25),     # 双段 +1.26/+0.75
    "n3": ("(-pl.col('vol_ratio_20'))", 0.25),   # 双段 +0.87/+0.72
    "n4": ("(-pl.col('vol_change_5d'))", 0.25),  # 双段 +0.83/+0.47
}
# A2 验收: 断言 key 集合与清线因子清单一致
assert set(NEW.keys()) == {f"n{i+1}" for i in range(len(CLEARED))}, "NEW 与 CLEARED 数量不符"
for n, (expr, _) in NEW.items():
    assert "vol_" in expr or "turn_" in expr, f"{n} 表达式与清线因子无关"

# 任务4: v7 基线(全修正后)
m_v7 = run_backtest(start_year=2021, end_year=2026, verbose=False)
# 任务5: 纯新池(include_base=False, 双段清线4因子)
m_new = run_backtest(extra_factors=NEW, start_year=2021, end_year=2026, verbose=False, include_base=False)
# 任务6: 纯单因子下限(双段清线最强 turn_ratio 低端=低换手)
m_single = run_backtest(extra_factors={"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)},
                        start_year=2021, end_year=2026, verbose=False, include_base=False)


def fmt(m):
    return (f"总收益 {m['total_ret_pct']:+.1f}% | 年化 {m['annual_pct']:+.1f}% | "
            f"交易 {m['n_trades']}笔 | 胜率 {m['win_rate']:.1f}% | 盈亏比 {m['pl_ratio']:.2f} | "
            f"回撤 {m['max_dd_pct']:.1f}% | 回退占比 {m.get('hfq_fallback_ratio', 0):.1%} | "
            f"avg_px {m.get('avg_px_ret_pct', 0):+.3f}% | avg_net {m.get('avg_net_ret_pct', 0):+.3f}%")

# A3(v4.2): 判定全部从 metrics 生成
better = m_new["total_ret_pct"] > m_v7["total_ret_pct"]
gap = m_new["total_ret_pct"] - m_v7["total_ret_pct"]

lines = [
    f"# v4.1复核 重跑总表（{dt.date.today()} v4.2修正版）",
    "",
    "> v4.2修正: A1买入佣金进pnl / A2任务5因子池与任务1对齐(双段清线4因子) / A3判定从metrics生成",
    "> 全修正后口径: A1实盘价现金流 + 买入滑点 + 买入佣金摊入 + 损益复权收益率 + 交易日止损",
    "",
    "## 任务4: v7 基线（修正后）",
    f"- {fmt(m_v7)}",
    "",
    "## 任务5: 纯新池 vs v7（include_base=False, 双段清线4因子）",
    f"- 纯新池({','.join(CLEARED)}): {fmt(m_new)}",
    f"- v7 基线: {fmt(m_v7)}",
    "",
    "## 任务6: 纯单因子下限参照（turn_ratio 低换手, 双段清线因子）",
    f"- {fmt(m_single)}",
    "",
    "## 判定",
    f"- 任务1: {len(CLEARED)} 个因子双段清线（{'/'.join(CLEARED)}）",
    f"- 任务5: 纯新池 {m_new['total_ret_pct']:+.1f}% vs v7 {m_v7['total_ret_pct']:+.1f}%"
    f"（差 {gap:+.1f}pp）→ 纯新池{'优于' if better else '劣于'} v7",
    f"- 任务6: 单因子 turn_ratio {m_single['total_ret_pct']:+.1f}%（avg_px {m_single.get('avg_px_ret_pct', 0):+.3f}%）",
    "- **本表不足以判定 alpha 存废**：任务1 与任务5/6 口径不同（网格毛收益 vs 回测含规则/成本），",
    "结论以 B2 梯度隔离实验为准（见 docs/选股追踪/B2_梯度隔离_*.md）。",
]
report = "\n".join(lines)
OUT.write_text(report, encoding="utf-8")
print(report)
print(f"\n[已存] {OUT}")
