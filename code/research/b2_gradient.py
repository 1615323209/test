"""B2(v4.2): 五档梯度隔离实验——决定"是否停挖价量因子"的唯一依据
每档只解开一个约束, include_base=False, 2021-2026
E0: v7规则+择时门+动量筛 | turn_ratio(复现现状)
E1: 持满5日+择时门+动量筛 | turn_ratio(退出规则值多少钱)
E2: 持满5日+无择时+动量筛 | turn_ratio(择时门值多少钱)
E3: 持满5日+无择时+无动量筛 | turn_ratio(动量筛值多少钱, 与网格口径最接近)
E4: 持满5日+无择时+无动量筛 | 四因子等权(真正的目标问题)
产出: docs/选股追踪/B2_梯度隔离_YYYY-MM-DD.md
"""
import sys, datetime as dt
sys.path.insert(0, r"D:/quant_project/code")
from pathlib import Path
from backtest.backtest_engine import run_backtest

OUT = Path(f"D:/quant_project/docs/选股追踪/B2_梯度隔离_{dt.date.today()}.md")

# 双段清线4因子(等权)
NEW4 = {
    "n1": ("(-pl.col('vol_ratio'))", 0.25),
    "n2": ("(-pl.col('turn_ratio'))", 0.25),
    "n3": ("(-pl.col('vol_ratio_20'))", 0.25),
    "n4": ("(-pl.col('vol_change_5d'))", 0.25),
}
TURN = {"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)}

def run(tag, factors, exit_mode="v7", market_gate=True, trend_filter=True, hold_days=5):
    m = run_backtest(extra_factors=factors, start_year=2021, end_year=2026,
                     verbose=False, include_base=False,
                     exit_mode=exit_mode, hold_days=hold_days,
                     market_gate=market_gate, trend_filter=trend_filter)
    print(f"{tag}: px={m.get('avg_px_ret_pct',0):+.3f}% net={m.get('avg_net_ret_pct',0):+.3f}% "
          f"n={m['n_trades']} ret={m['total_ret_pct']:+.1f}%")
    return m

print("=== B2 五档梯度隔离 ===\n")
E = {}
E["E0"] = run("E0 v7规则+门+筛", TURN)
E["E1"] = run("E1 持满5日+门+筛", TURN, exit_mode="hold_n", hold_days=5)
E["E2"] = run("E2 持满5日+无门+筛", TURN, exit_mode="hold_n", hold_days=5, market_gate=False)
E["E3"] = run("E3 持满5日+无门+无筛", TURN, exit_mode="hold_n", hold_days=5, market_gate=False, trend_filter=False)
E["E4"] = run("E4 持满5日+无门+无筛+4因子", NEW4, exit_mode="hold_n", hold_days=5, market_gate=False, trend_filter=False)

# 增量归因表
lines = [
    f"# B2 梯度隔离实验（{dt.date.today()}）",
    "",
    "> v4.2 唯一依据文档 ｜ include_base=False ｜ 2021-2026 ｜ 成本线0.45%/笔(5000元仓)",
    "> 因子: E0-E3 turn_ratio 单因子; E4 四因子等权(双段清线4缩量因子)",
    "",
    "| 档 | avg_px% | avg_net% | Δnet vs 上档 | 该档解开的约束 | 交易 | 总收益% |",
    "|---|---|---|---|---|---|---|",
]
prev_net = None
for tag, desc in [("E0", "基准(v7规则)"), ("E1", "退出规则→持满5日"), ("E2", "择时门→关闭"),
                  ("E3", "动量筛→关闭"), ("E4", "单因子→四因子等权")]:
    m = E[tag]
    net = m.get("avg_net_ret_pct", 0)
    d = f"{net-prev_net:+.3f}" if prev_net is not None else "-"
    lines.append(f"| {tag} | {m.get('avg_px_ret_pct',0):+.3f} | {net:+.3f} | {d} | {desc} | "
                 f"{m['n_trades']} | {m['total_ret_pct']:+.1f} |")
    prev_net = net

# 关键对账点
lines += [
    "",
    "## 关键对账点（E3 vs 网格）",
    f"- E3 avg_px = {E['E3'].get('avg_px_ret_pct',0):+.3f}% vs 网格 turn_ratio 设计段低端 +1.26%",
    "- 对账区间 [+0.9%, +1.5%]: " + ("✅ 落在区间内" if 0.9 <= E['E3'].get('avg_px_ret_pct',0) <= 1.5 else "❌ 未落区间, 需定位残余口径差"),
    "",
    "## 判定（B3 决策树, 用 E4）",
    f"- E4.avg_net = {E['E4'].get('avg_net_ret_pct',0):+.3f}%  |  E4.avg_px = {E['E4'].get('avg_px_ret_pct',0):+.3f}%",
]
e4_net = E["E4"].get("avg_net_ret_pct", 0)
e4_px = E["E4"].get("avg_px_ret_pct", 0)
if e4_net > 0:
    lines += ["- **路径1: E4.avg_net > 0 → alpha 成立, 问题在退出规则 → 重设计退出规则, 不停挖**"]
elif e4_px > 0.6:
    lines += ["- **路径2: E4.avg_net ≤ 0 但 E4.avg_px > 0.6% → alpha 存在但过不了成本线 → 降成本/换信号源**"]
else:
    lines += ["- **路径3: E4.avg_px ≤ 0 → 5日尺度价量空间无可交易 alpha → 停止挖价量因子, 转向龙虎榜/资金流**"]

report = "\n".join(lines)
OUT.write_text(report, encoding="utf-8")
print(f"\n[已存] {OUT}")
