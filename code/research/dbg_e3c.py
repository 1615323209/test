"""E3 逐笔诊断: 打 buy/sell 日期、px_ret、以及当日买入的股票是否真是 turn_ratio 最低"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl
import pandas as pd

# 直接跑 E3 短区间并拦截 all_trades
import backtest.backtest_engine as be

# monkeypatch: 复制 run_backtest 源码改返回 trades
import inspect, re
src = inspect.getsource(be.run_backtest)
# 在 return out 前插入 trades 导出
src_mod = src.replace("return out", "out['_trades'] = trades.head(20).to_dict('records'); return out")
# 执行修改版
ns = {}
exec(src_mod, {**vars(be), "__name__": "bt_mod"}, ns)
run_mod = ns["run_backtest"]

m = run_mod(extra_factors={"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)},
            start_year=2021, end_year=2022, verbose=False, include_base=False,
            exit_mode="hold_n", hold_days=5, market_gate=False, trend_filter=False)
print(f"E3短区间: ret={m['total_ret_pct']}% n={m['n_trades']} avg_px={m.get('avg_px_ret_pct')}%")
for t in m.get("_trades", []):
    print(f"  {t['code']} {t['buy_date']}→{t['sell_date']} held={t['held_days']} "
          f"px={t['px_ret_pct']:+.2f}% net={t['net_ret_pct']:+.2f}% reason={t['reason']}")
