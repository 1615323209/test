# A股因子库构建（基于日K数据）

## 场景

量化回测前，在原始OHLCV数据上批量计算因子。输入 `a_stock_daily_hfq.parquet`（后复权日K），输出 `factor_daily.parquet`（原始数据 + ~35个因子）。

## 因子清单

| 类别 | 因子 | 计算 |
|------|------|------|
| 收益率 | ret_1d/5d/10d/20d | `收盘.pct_change(N)` |
| 波动率 | vol_5d/10d/20d | `ret_1d.rolling(N).std() * sqrt(252)` |
| ATR | atr_14, atr_ratio | TR=max(H-L,|H-C1|,|L-C1|), rolling(14).mean() |
| 均线 | ma_5/10/20/60 | `收盘.rolling(N).mean()` |
| 均线衍生 | ma5_dist, ma20_dist | `(收盘-MA)/MA` |
| 均线交叉 | ma5_ma20_cross/dead | 金叉/死叉标记 |
| 成交量 | vol_ma5/20, vol_ratio | 量比=vol/vol_ma5 |
| 换手率 | turn_ma5/20, turn_ratio | 换手率均值及比率 |
| 价格位置 | price_pos_20/60 | `(收-低)/(高-低)` 20/60日区间位置 |
| MACD | dif/dea/hist | 标准(12,26,9) |
| RSI | rsi_14 | 标准14日RSI |
| 布林带 | bb_upper/mid/lower/width/pos | 20日 ± 2σ |
| 涨跌停 | limit_up/down | ret>9.5% / ret<-9.5% |
| 停牌 | is_suspended | 成交量==0 |
| 连涨连跌 | up_streak/down_streak | 连续涨/跌天数 |

## 实现要点

### 分组计算
- 每只股票独立计算（`groupby('股票代码')`），防止跨股票数据污染
- 逐只处理减少内存（~200只一批），边算边存到parquet

### 边界处理
- 前20天无数据（rolling窗口不足），直接保留NaN——回测时过滤
- 涨跌停阈值设为9.5%（覆盖10%涨跌停 + 主板/创业板差异）
- 除数保护：`+1e-10` 防止 `high-low==0` 时除零

### 运行规模
- 输入：~1,240万行，283MB → 输出：预计 800MB-1.2GB
- 预计耗时：10-20分钟

### 断点续传
- 用进度文件名记录已完成的股票代码，重启时自动跳过

## 验证

后复权数据应通过以下检查：
1. `df['收盘'] < 0` → 0（无负数）
2. 后复权日收益率 ≈ 不复权日收益率（偏差 < 0.05%）
3. 涨跌停分布合理（主板10%，科创/创业板20%，新股无限制可超20%）
4. `df.isnull().sum()` → 仅rolling窗口期有NaN

## Polars 实现注意事项

**推荐用 Polars 而非 Pandas**（3-5x 快、70% 省内存），但需避开以下坑：

### 1. with_columns 嵌套深度限制
❌ 连续链式 `df.with_columns(...).with_columns(...)...` 超过 ~8 层会触发 Polars 查询计划栈溢出（`This error occurred with the following context stack: [1] 'with_columns' [2] 'with_columns' ...`）
✅ 解决方法：用中间变量 + 扁平化，或改用 eager 模式（`collect()` 后再计算下一组因子）

```python
# ❌ 连续 9+ 个 with_columns
lf = lf.with_columns([...])  # 1
lf = lf.with_columns([...])  # 2
# ... 到第 9 层 → 栈溢出

# ✅ 控制层数（每层多放表达式）或分批 collect
df = df.collect()  # eager化
df = df.with_columns([exprs1, exprs2, exprs3])  # 合并到一层
```

### 2. deprecation: min_periods → min_samples
Polars 1.21+ 将 `rolling_mean(min_periods=N)` 改为 `rolling_mean(min_samples=N)`。传旧参数名仅警告不报错，但应统一用新名。

### 3. 分批写入模式（防止 OOM）
❌ 累计追加：`read full existing → concat new → write back` ——文件越大内存越炸，exit 137
✅ 独立 batch 文件 + 最后 pyarrow 逐文件合并：

```python
# ✅ 模式：每批存独立 parquet，最后合并
for i in range(batches):
    factored.write_parquet(TMPDIR / f"batch_{i:04d}.parquet")

# 最终合并：逐文件读取，内存恒定
writer = pq.ParquetWriter(OUTPUT, schema)
for f in sorted(TMPDIR.glob("batch_*.parquet")):
    writer.write_table(pq.read_table(f))
writer.close()
```

### 4. 内存基线参考（用于容量规划）
| 阶段 | 规模 | 内存占用 |
|------|------|----------|
| 原始日K parquet | 1257万行 × 9列 | 283MB 文件 → ~900MB loaded |
| 因子 parquet | 1257万行 × 52列 | 3.3GB 文件 → ~5GB loaded |
| 每批 500 只处理中 | ~125万行 | ~150-300MB |

低于 4GB 内存的机器必须先扩 swap（`sudo fallocate -l 4G /swapfile2`）再跑。

### 5. Polars 整数减法溢出（UInt32 → 巨大正数）

`count()`/`sum()` 聚合出的整数列默认是 UInt32/Int32，**减法运算遇负值会无符号溢出**成 4294965344 之类的大数：

```python
# ❌ 上涨家数 - 下跌家数 = 1419 - 3371 = -1952 → 显示 4294965344
market = lf.group_by('日期').agg([
    (pl.col('ret_1d') > 0).sum().alias('上涨家数'),
    (pl.col('ret_1d') < 0).sum().alias('下跌家数'),
])
market = market.with_columns((pl.col('上涨家数') - pl.col('下跌家数')).alias('差'))

# ✅ 减法前先 cast 成 Int64
market = market.with_columns([
    pl.col('上涨家数').cast(pl.Int64),
    pl.col('下跌家数').cast(pl.Int64),
])
market = market.with_columns((pl.col('上涨家数') - pl.col('下跌家数')).alias('差'))
```

## 市场情绪因子（市场级，每日一个值）

个股因子之外的第二个维度：全市场每日聚合，用于回测的**市场环境过滤**。输出 `market_daily.parquet`（每日一行）：

| 因子 | 来源 | 说明 |
|------|------|------|
| 涨停家数/跌停家数 | factor 库 `limit_up/down` 按日期 sum | 市场情绪温度 |
| 上涨/下跌家数、涨跌家数差 | `ret_1d` 符号按日期 count | 广度指标 |
| 上涨占比 | 上涨家数/股票数 | 归一化 |
| 全市场成交额 | `成交额` 按日期 sum | 量能 |
| 北向净买入/累计/5日/20日均 | north_fund_flow 按日期 join | 外资情绪（2014起有数据） |

构建用 `pl.scan_parquet(factor).group_by('日期').agg(...)`（惰性，不会OOM），再 `join` 北向资金表。回测时用字典 `{日期: row}` 预加载，O(1) 查询。

**市场环境过滤三条件**（满足2/3才操作）：① 沪深300 在 MA20 上方 ② 涨停家数 > 50 ③ 北向净买入 > 0。

## 参考脚本

- **Polars 版（推荐）**: `templates/build_factors_pl.py`
- **Pandas 兼容版**: `templates/build_factors.py`
