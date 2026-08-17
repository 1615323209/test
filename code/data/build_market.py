"""构建市场级每日因子：北向资金 + 涨停/跌停/涨跌家数 + 成交额"""
import polars as pl
import pandas as pd
from pathlib import Path

FACTOR = Path("D:/quant_data/factor_daily.parquet")
FACTOR_INCR = Path("D:/quant_data/factor_daily_incr.parquet")
NORTH = Path("D:/quant_data/north_fund_flow.parquet")
OUT = Path("D:/quant_data/market_daily.parquet")

print("=== 市场情绪因子 ===")

# 1. 从因子库聚合每日市场统计（主文件 + 增量文件，多文件 scan 自动统一 schema）
print("[1] 聚合每日涨跌家数/涨停数...")
files = [FACTOR] + ([FACTOR_INCR] if FACTOR_INCR.exists() else [])
lf = pl.scan_parquet(files)
market = lf.group_by('日期').agg([
    pl.col('股票代码').n_unique().alias('股票数'),
    pl.col('limit_up').sum().alias('涨停家数'),
    pl.col('limit_down').sum().alias('跌停家数'),
    (pl.col('ret_1d') > 0).sum().alias('上涨家数'),
    (pl.col('ret_1d') < 0).sum().alias('下跌家数'),
    pl.col('成交额').sum().alias('全市场成交额'),
    pl.col('收盘').mean().alias('平均涨跌'),
]).sort('日期').collect()

print(f"  市场因子: {len(market)} 天")

# 2. 北向资金
print("[2] 合并北向资金...")
north = pd.read_parquet(NORTH)
north['TRADE_DATE'] = pd.to_datetime(north['TRADE_DATE']).dt.date
north = north[['TRADE_DATE', 'NET_DEAL_AMT', 'ACCUM_DEAL_AMT', 'BUY_AMT', 'SELL_AMT', 'HOLD_MARKET_CAP']]
north = north.rename(columns={
    'TRADE_DATE': '日期',
    'NET_DEAL_AMT': '北向净买入',
    'ACCUM_DEAL_AMT': '北向累计净买入',
    'BUY_AMT': '北向买入额',
    'SELL_AMT': '北向卖出额',
    'HOLD_MARKET_CAP': '北向持股市值',
})
north_pl = pl.from_pandas(north)

# 合并
result = market.join(north_pl, on='日期', how='left')

# 3. 衍生因子
result = result.with_columns([
    pl.col('上涨家数').cast(pl.Int64),
    pl.col('下跌家数').cast(pl.Int64),
    pl.col('股票数').cast(pl.Int64),
])
result = result.with_columns([
    (pl.col('上涨家数') - pl.col('下跌家数')).alias('涨跌家数差'),
    (pl.col('上涨家数') / pl.col('股票数')).alias('上涨占比'),
    pl.col('北向净买入').rolling_mean(5).alias('北向净买入5日均'),
    pl.col('北向净买入').rolling_mean(20).alias('北向净买入20日均'),
])

# 排序保存
result = result.sort('日期')
result.write_parquet(OUT)
print(f"\n=== 完成 ===")
print(f"输出: {OUT} ({OUT.stat().st_size/1024/1024:.0f}KB)")
print(f"行数: {len(result)}, 列: {result.columns}")
print(result.tail(3))
