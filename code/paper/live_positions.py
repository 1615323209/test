#!/usr/bin/env python3
"""真实持仓管理（手动下单模式）— 量化 L4 实盘统一
用法:
  python live_positions.py --status [现价对]        查看持仓（可带 代码:现价 逗号分隔）
  python live_positions.py --add 600354 12.50 8000 [2026-08-17]   买入: 代码 成本价 金额 [日期]
  python live_positions.py --sell 600354 [股数|all] [2026-08-17]  卖出
规则（用户实盘策略）: 最多2只 / 单票<=1万 / 止损-5% / 止盈+12%
文件: D:/quant_data/live_positions.json
"""
import json, sys, argparse, csv
from pathlib import Path
from datetime import date

DATA = Path("D:/quant_data")
STATE = DATA / "live_positions.json"
TRADES = DATA / "live_trades.csv"

# 流程改造3.0（2026-08-20用户决策）: 5日持仓 + 4仓×5000 + 止损-4% 止盈+6%
MAX_POSITIONS = 4
MAX_AMOUNT = 5000.0
STOP_LOSS = -0.04
TAKE_PROFIT = 0.06
TIME_STOP_DAYS = 5  # 5交易日时间止损

def load():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return []

def save(pos):
    STATE.write_text(json.dumps(pos, ensure_ascii=False, indent=2), encoding="utf-8")

def cmd_status(args):
    pos = load()
    if not pos:
        print("当前无持仓。")
        return
    prices = {}
    if args.status and "," in args.status:
        for kv in args.status.split(","):
            if ":" in kv:
                code, px = kv.split(":")
                prices[code.strip()] = float(px)
    print(f"当前持仓 {len(pos)}/{MAX_POSITIONS} 只（止损-{abs(STOP_LOSS)*100:.0f}% / 止盈+{TAKE_PROFIT*100:.0f}%）:")
    total_cost = 0.0
    for p in pos:
        code, name = p["code"], p.get("name", "")
        cost, shares = p["cost"], p["shares"]
        amt = cost * shares
        total_cost += amt
        line = f"  {code} {name} 成本{cost:.2f} x{shares}股 = {amt:.0f}元 买入日{p['date']}"
        if code in prices:
            px = prices[code]
            pnl = (px - cost) / cost
            flag = "⚠️止损" if pnl <= STOP_LOSS else ("🎯止盈" if pnl >= TAKE_PROFIT else "")
            line += f" 现价{px:.2f} 盈亏{pnl:+.1%} {flag}"
        print(line)
    print(f"总成本 {total_cost:.0f} 元 / 上限 {MAX_POSITIONS*MAX_AMOUNT:.0f} 元")

def load_pick_factors(code, d):
    """改造2.0缺陷3/2.2：从当天 picks 文件按代码带出 top_factors 归因"""
    try:
        import polars as _pl
        picks_dir = DATA / "daily_picks"
        # 找最新 picks 文件（优先当日，回退最近）
        cands = sorted(picks_dir.glob(f"picks_{d}.csv")) or sorted(picks_dir.glob("picks_*.csv"))
        if not cands:
            return ""
        pf = _pl.read_csv(cands[-1])
        row = pf.filter(_pl.col("股票代码") == code)
        if row.height == 0 and "代码" in pf.columns:
            row = pf.filter(_pl.col("代码") == code)
        if row.height > 0 and "top_factors" in pf.columns:
            return str(row["top_factors"][0])
        return ""
    except Exception:
        return ""

def cmd_add(args):
    pos = load()
    if len(pos) >= MAX_POSITIONS:
        print(f"已达持仓上限 {MAX_POSITIONS} 只，先卖出再买入。")
        return 1
    code = args.add[0]
    cost = float(args.add[1])
    amount = float(args.add[2])
    d = args.add[3] if len(args.add) > 3 else str(date.today())
    if amount > MAX_AMOUNT:
        print(f"单票 {amount:.0f} 元超过上限 {MAX_AMOUNT:.0f} 元，拒绝记录。")
        return 1
    # 按 100 股取整
    shares = int(amount / cost / 100) * 100
    if shares <= 0:
        print("金额不足一手(100股)。")
        return 1
    # 改造2.0缺陷3/2.2：--from-pick 从 picks 带出 top_factors 归因；手工加仓写 manual
    factor = ""
    if args.from_pick:
        factor = load_pick_factors(code, d)
        if not factor:
            print(f"  （{code} 未在 {d} picks 中找到 top_factors，factor 留空）")
    else:
        factor = "manual"
    pos.append({"code": code, "name": "", "cost": cost, "shares": shares,
                "amount": cost * shares, "date": d, "factor": factor})
    save(pos)
    print(f"已记录买入: {code} 成本{cost:.2f} x{shares}股 = {cost*shares:.0f}元 ({d}) factor={factor}")
    print(f"当前 {len(pos)}/{MAX_POSITIONS} 只，剩余仓位 {MAX_POSITIONS*MAX_AMOUNT - sum(p['amount'] for p in pos):.0f} 元")
    return 0

def record_trade(code, cost, price, shares, d, factor=""):
    """记录真实成交到 live_trades.csv（L4 SPRT 数据源，L4 文档缺陷 3 配套）
    改造2.0：factor 列写入归因（多因子用 | 分隔），不再恒空"""
    pnl_pct = (price - cost) / cost
    new = not TRADES.exists()
    with open(TRADES, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "code", "cost", "price", "shares", "pnl_pct", "factor"])
        w.writerow([d, code, cost, price, shares, round(pnl_pct, 4), factor])
    return pnl_pct

def cmd_sell(args):
    pos = load()
    code = args.sell[0]
    d = args.sell[2] if len(args.sell) > 2 else str(date.today())
    price = float(args.sell[3]) if len(args.sell) > 3 else None  # 可选：卖出价（记录成交用）
    p = next((x for x in pos if x["code"] == code), None)
    if not p:
        print(f"未找到持仓 {code}。")
        return 1
    sold_shares = 0
    if len(args.sell) > 1 and args.sell[1] != "all":
        shares = int(args.sell[1])
        if shares >= p["shares"]:
            sold_shares = p["shares"]
            pos.remove(p)
            print(f"已清仓 {code}（{d}）。")
        else:
            sold_shares = shares
            p["shares"] -= shares
            p["amount"] = p["cost"] * p["shares"]
            print(f"已卖出 {code} {shares}股，剩余 {p['shares']}股（{d}）。")
    else:
        sold_shares = p["shares"]
        pos.remove(p)
        print(f"已清仓 {code}（{d}）。")
    if price and sold_shares > 0:
        # 改造2.0缺陷3：卖单的 factor 归因从持仓记录带出（买入时 --from-pick 写入的）
        factor = p.get("factor", "manual")
        pnl = record_trade(code, p["cost"], price, sold_shares, d, factor)
        print(f"已记录成交: {code} 成本{p['cost']:.2f}→卖{price:.2f} 盈亏{pnl:+.2%} factor={factor}")
    else:
        print(f"（未提供卖出价，未记录成交；可在盘中监控时补记）")
    save(pos)
    return 0

def main():
    ap = argparse.ArgumentParser(description="真实持仓管理")
    ap.add_argument("--status", nargs="?", const="", help="查看持仓（可选 代码:现价,代码:现价）")
    ap.add_argument("--add", nargs="+", help="买入: 代码 成本价 金额 [日期]")
    ap.add_argument("--sell", nargs="+", help="卖出: 代码 [股数|all] [日期]")
    ap.add_argument("--from-pick", action="store_true",
                    help="改造2.0：买入时从当天 picks 带出 top_factors 归因（手工加仓不传则写 manual）")
    a = ap.parse_args()
    if a.add:
        return cmd_add(a)
    if a.sell:
        return cmd_sell(a)
    return cmd_status(a)

if __name__ == "__main__":
    sys.exit(main())
