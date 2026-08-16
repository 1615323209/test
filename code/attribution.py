"""交易归因分析 — 110笔交易，找赢/亏差异"""
import pandas as pd
import numpy as np

trades = pd.read_parquet('/home/ubuntu/quant_data/backtest_v5opt_trades.parquet')
trades['sell_date'] = pd.to_datetime(trades['sell_date'])
trades['buy_date'] = pd.to_datetime(trades['buy_date'])
trades['win'] = trades['pnl'] > 0

print(f"总交易: {len(trades)}, 赢: {trades['win'].sum()}, 亏: {(~trades['win']).sum()}")
print(f"总收益: {trades['pnl'].sum():.0f}元")

# 1. 赢/亏特征对比
feats = ['ret_5d','vol_ratio','turn_ratio','price_pos_20','limit_up_5d','macd_dif','ret_1d','up_streak']
print("\n=== 1. 赢/亏买入特征对比 ===")
comp = trades.groupby('win')[feats].mean().round(3)
comp['笔数'] = trades.groupby('win').size()
print(comp.to_string())
print("\n差异(赢-亏):")
diff = trades[trades['win']].mean(numeric_only=True)[feats] - trades[~trades['win']].mean(numeric_only=True)[feats]
print(diff.round(4).to_string())

# 2. 因子分桶胜率
print("\n=== 2. 关键因子分桶 ===")
for f in ['ret_5d', 'vol_ratio', 'price_pos_20', 'limit_up_5d', 'turn_ratio']:
    try:
        bins = pd.qcut(trades[f], 4, duplicates='drop')
        g = trades.groupby(bins, observed=True).agg(
            笔数=('pnl','size'), 胜率=('pnl', lambda x: (x>0).mean()*100),
            平均盈亏=('pnl','mean'))
        print(f"\n--- {f} 四分位 ---")
        print(g.round(1).to_string())
    except Exception as e:
        print(f"{f}: {e}")

# 3. 出场原因 vs 盈亏
print("\n=== 3. 出场原因归因 ===")
g = trades.groupby('reason').agg(
    笔数=('pnl','size'), 总盈亏=('pnl','sum'),
    胜率=('pnl', lambda x: (x>0).mean()*100),
    平均持有天=('held_days','mean'))
print(g.round(1).to_string())

# 4. 持有天数分桶
print("\n=== 4. 持有天数 vs 盈亏 ===")
bins = [0, 2, 4, 6, 8, 10, 20, 100]
labels = ['1-2','3-4','5-6','7-8','9-10','11-20','20+']
trades['持有分组'] = pd.cut(trades['held_days'], bins=bins, labels=labels)
g = trades.groupby('持有分组', observed=True).agg(
    笔数=('pnl','size'), 胜率=('pnl', lambda x: (x>0).mean()*100),
    平均盈亏=('pnl','mean'))
print(g.round(1).to_string())

# 5. 月度效应
print("\n=== 5. 买入月份 vs 盈亏 ===")
trades['买入月'] = trades['buy_date'].dt.month
g = trades.groupby('买入月').agg(
    笔数=('pnl','size'), 总盈亏=('pnl','sum'),
    胜率=('pnl', lambda x: (x>0).mean()*100))
print(g.round(1).to_string())

# 6. 年度
print("\n=== 6. 年度 vs 盈亏 ===")
trades['买入年'] = trades['buy_date'].dt.year
g = trades.groupby('买入年').agg(
    笔数=('pnl','size'), 总盈亏=('pnl','sum'),
    胜率=('pnl', lambda x: (x>0).mean()*100))
print(g.round(1).to_string())

# 7. 赢的交易特征（详细看）
print("\n=== 7. 盈利交易TOP ===")
print(trades[trades['win']].sort_values('pnl', ascending=False).head(10)
      [['code','buy_date','sell_date','pnl','held_days','reason','ret_5d','vol_ratio','limit_up_5d']].to_string(index=False))
print("\n=== 亏损交易TOP ===")
print(trades[~trades['win']].sort_values('pnl').head(10)
      [['code','buy_date','sell_date','pnl','held_days','reason','ret_5d','vol_ratio','limit_up_5d']].to_string(index=False))
