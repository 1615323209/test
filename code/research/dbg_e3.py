"""E3 口径差排查: 逐笔看 px_ret vs 网格 fwd_5d"""
import sys
sys.path.insert(0, r"D:/quant_project/code")
import polars as pl
from backtest import backtest_engine as be

# monkeypatch: 让 run_backtest 返回 trades (临时复制逻辑太麻烦, 直接改全局拿 trades)
# 用一个小技巧: 读 backtest_engine 源码在内存里加个 return_trades 参数
import inspect
src = inspect.getsource(be)
# 简单方案: 直接构造 E3 场景跑短区间, 打每笔的 buy/sell/px_ret
m = be.run_backtest(extra_factors={"turn_ratio": ("(-pl.col('turn_ratio'))", 1.0)},
                    start_year=2021, end_year=2022, verbose=False, include_base=False,
                    exit_mode="hold_n", hold_days=5, market_gate=False, trend_filter=False)
print("E3短区间 metrics:", {k: m.get(k) for k in ['total_ret_pct','avg_px_ret_pct','avg_net_ret_pct','n_trades','avg_held_days']})
print("keys:", [k for k in m.keys()])
