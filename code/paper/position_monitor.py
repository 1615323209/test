#!/usr/bin/env python3
"""持仓监控（no_agent cron 用）——盯 live_positions.json 的每只持仓
每只: 现价 vs 成本 → 盈亏%; 触发规则:
  - 止损 -4% (STOP_LOSS)
  - 止盈 +6% (TAKE_PROFIT)
  - 5交易日时间止损 (TIME_STOP_DAYS)
现价来源: 腾讯实时行情接口(免key)
输出: 有持仓时打印状态; 有触发时打印告警(no_agent 非空stdout即推送)
用法: python -m paper.position_monitor
"""
import json, sys
from pathlib import Path
from datetime import date, datetime
import requests

DATA = Path("D:/quant_data")
STATE = DATA / "live_positions.json"
STOP_LOSS = -0.04
TAKE_PROFIT = 0.06
TIME_STOP_DAYS = 5
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"
NAME_MAP = {}


def load_names():
    global NAME_MAP
    try:
        import polars as pl
        m = pl.read_csv(DATA / "code_name_map.csv", schema_overrides={"代码": pl.Utf8})
        NAME_MAP = {r["代码"]: r["名称"] for r in m.iter_rows(named=True)}
    except Exception:
        pass


def symbol(code):
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("4", "8", "9")):
        return "bj" + code
    return "sz" + code


def fetch_price(code):
    """腾讯实时行情: 名称,现价,昨收"""
    try:
        r = requests.get(f"https://qt.gtimg.cn/q={symbol(code)}",
                         headers={"User-Agent": UA}, timeout=8)
        txt = r.text
        parts = txt.split("~")
        if len(parts) > 4:
            name = parts[1]
            price = float(parts[3]) if parts[3] else None
            prev = float(parts[4]) if parts[4] else None
            return name, price, prev
    except Exception:
        pass
    return None, None, None


def main():
    if not STATE.exists():
        print("无持仓文件")
        return
    pos = json.loads(STATE.read_text(encoding="utf-8"))
    if not pos:
        print("当前无持仓。")
        return
    load_names()
    lines = [f"📊 持仓监控 {date.today()}"]
    alerts = []
    for p in pos:
        code = p["code"]
        cost = float(p["cost"])
        shares = int(p["shares"])
        name, price, prev = fetch_price(code)
        display_name = name or NAME_MAP.get(code, code)
        if price is None:
            lines.append(f"  {display_name}({code}) 现价获取失败")
            continue
        pnl_pct = (price - cost) / cost * 100
        pnl_amt = (price - cost) * shares
        # 持仓天数(自然日→交易日近似)
        buy_d = datetime.strptime(p.get("date", "2026-08-21"), "%Y-%m-%d").date()
        held = (date.today() - buy_d).days
        status = ""
        if pnl_pct <= STOP_LOSS * 100:
            status = f" 🚨止损触发(-{abs(STOP_LOSS)*100:.0f}%)"
            alerts.append(f"🚨 {display_name}({code}) 亏{pnl_pct:.1f}% 触发止损线{-STOP_LOSS*100:.0f}% → 建议卖出")
        elif pnl_pct >= TAKE_PROFIT * 100:
            status = f" 🎯止盈触发(+{TAKE_PROFIT*100:.0f}%)"
            alerts.append(f"🎯 {display_name}({code}) 赚{pnl_pct:+.1f}% 触发止盈线+{TAKE_PROFIT*100:.0f}% → 建议卖出")
        elif held >= TIME_STOP_DAYS:
            status = f" ⏰时间止损({TIME_STOP_DAYS}日)"
            alerts.append(f"⏰ {display_name}({code}) 持仓{held}天 ≥{TIME_STOP_DAYS}日 → 时间止损建议")
        lines.append(
            f"  {display_name}({code}) 成本{cost:.2f} 现价{price:.2f} "
            f"盈亏{pnl_pct:+.1f}%({pnl_amt:+.0f}元) 持仓{held}天{status}")
    print("\n".join(lines))
    if alerts:
        print("\n" + "\n".join(alerts))


if __name__ == "__main__":
    main()
