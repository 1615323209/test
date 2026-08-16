# A股因子库清单（FACTOR LIBRARY）

> 更新时间：2026-08-16
> 数据文件：`factor_daily.parquet`（3.3GB）+ `factor_daily_incr.parquet`（每日增量）—— **2026-08-16 起存于 Windows 本地 `D:\quant_data\`**
> 构建脚本：`build_factors_pl.py`（全量，polars 分批）/ `factors.py`（因子计算模块，增量共用）
> 因子挖掘历史与完整研究背景见 [策略版本日志.md](./策略版本日志.md)

---

## 〇、因子数据文件（本地地址）

**2026-08-16 起全部量化数据存于 Windows 本地 `D:\quant_data\`**。本表只列**因子类**数据；行情/资金/市场类见 [股票数据资产.md](./股票数据资产.md)，新闻类见 [新闻数据资产.md](./新闻数据资产.md)。

| 文件 | 内容 | 大小 | 实际地址 |
|------|------|------|----------|
| `factor_daily.parquet` | **45 因子全库**（9原始列+45因子列=54列） | 3.3GB | `D:\quant_data\factor_daily.parquet` |
| `ic_data.parquet` | IC 体检数据（因子+forward收益×4） | 1.4GB | `D:\quant_data\ic_data.parquet` |
| `factor_bt.parquet` | 回测精简版（17列，含 limit_up_5d） | 497MB | `D:\quant_data\factor_bt.parquet` |
| `factor_extra_daily.parquet` | 扩展5因子（ILLIQ/量价相关/偏度/峰度） | 512MB | `D:\quant_data\factor_extra_daily.parquet` |
| `factor_daily_incr.parquet` | 每日增量因子（多文件scan合并读取） | 小 | `D:\quant_data\factor_daily_incr.parquet` |
| `mined_factors_v2.csv` | 因子挖掘 v2 候选（612） | 36KB | `D:\quant_data\mined_factors_v2.csv` |
| `mined_factors_v2_fine.csv` | 因子挖掘 v2 精算 Top 30 | 3.6KB | `D:\quant_data\mined_factors_v2_fine.csv` |
| `llm_factors.csv` | LLM 因子合成输出（L1 生成引擎产物） | 小 | `D:\quant_data\llm_factors.csv` |
| `ic_report.csv` | IC 体检报告 | 8.8KB | `D:\quant_data\ic_report.csv` |
| `fdr_passed.csv` | FDR 校正通过名单 | 127KB | `D:\quant_data\fdr_passed.csv` |

> 代码脚本在 `D:\quant_project\code\`，量化技能文档在 `D:\quant_project\skills\`。

---

## 一、库概况
| 项目 | 值 |
|------|-----|
| 股票数量 | 4919 只（A股全量） |
| 数据行数 | 1258 万行 |
| 时间范围 | 2010-01-04 ~ 2026-08-14（4030+ 交易日） |
| 列结构 | 9 原始列 + 45 因子列 = 54 列 |
| 复权方式 | 后复权（hfq，前复权对高分红股产生负数） |
| 数据源 | 腾讯 `qt.gtimg.cn` / `proxy.finance.qq.com`（增量） |
| 存储格式 | Parquet（zstd 压缩） |

**9 个原始列**：日期、开盘、收盘、最高、最低、成交量、turnover（换手率）、成交额、股票代码

**构建方式**：`build_factors_pl.py` 按 500 只/批切片 → 每批算因子写独立临时文件 → ParquetWriter 逐文件合并（2GB 内存机器防 OOM 的标准模式）。`factors.py` 为抽取出的公共计算模块，供 `update_daily.py` 每日增量复用，两处公式必须保持一致。

**每日增量**：新交易日因子写入 `factor_daily_incr.parquet`（小文件，直接 concat），所有读取方用多文件 `scan_parquet([factor_daily, factor_daily_incr])` 合并读取，不动 3.3GB 大文件（避免 OOM）。

---

## 二、45 个因子详细清单

### 2.1 收益类（4 个）

| 因子 | 定义 | 公式 |
|------|------|------|
| ret_1d | 当日收益 | `收盘.pct_change()` |
| ret_5d | 5日累计收益 | `ret_1d.rolling_sum(5)` |
| ret_10d | 10日累计收益 | `ret_1d.rolling_sum(10)` |
| ret_20d | 20日累计收益 | `ret_1d.rolling_sum(20)` |

### 2.2 波动类（5 个）

| 因子 | 定义 | 公式 |
|------|------|------|
| vol_5d | 5日年化波动率 | `ret_1d.rolling_std(5) × √252(15.8745)` |
| vol_10d | 10日年化波动率 | `ret_1d.rolling_std(10) × 15.8745` |
| vol_20d | 20日年化波动率 | `ret_1d.rolling_std(20) × 15.8745` |
| atr_14 | 14日真实波幅均值 | `TR.rolling_mean(14)`，TR=max(高-低, \|高-昨收\|, \|低-昨收\|) |
| atr_ratio | ATR 相对收盘比 | `atr_14 / 收盘` |

### 2.3 均线类（8 个）

| 因子 | 定义 | 公式 |
|------|------|------|
| ma_5 / ma_10 / ma_20 / ma_60 | 收盘均线 | `收盘.rolling_mean(5/10/20/60)` |
| ma5_dist | 收盘偏离 MA5 | `(收盘 - ma_5) / ma_5` |
| ma20_dist | 收盘偏离 MA20 | `(收盘 - ma_20) / ma_20` |
| ma5_ma20_cross | MA5 上穿 MA20（金叉） | `(MA5>MA20) & (昨MA5<=昨MA20)`，0/1 |
| ma5_ma20_dead | MA5 下穿 MA20（死叉） | `(MA5<MA20) & (昨MA5>=昨MA20)`，0/1 |

### 2.4 量价类（8 个）

| 因子 | 定义 | 公式 |
|------|------|------|
| vol_ma5 | 5日均量 | `成交量.rolling_mean(5)` |
| vol_ma20 | 20日均量 | `成交量.rolling_mean(20)` |
| vol_ratio | 量比（5日基准） | `成交量 / vol_ma5` |
| vol_ratio_20 | 量比（20日基准） | `成交量 / vol_ma20` |
| vol_change_5d | 5日量能变化 | `成交量.pct_change(5)` |
| turn_ma5 | 5日平均换手率 | `turnover.rolling_mean(5)` |
| turn_ma20 | 20日平均换手率 | `turnover.rolling_mean(20)` |
| turn_ratio | 换手率比 | `turnover / turn_ma5` |

### 2.5 位置类（6 个）

| 因子 | 定义 | 公式 |
|------|------|------|
| high_20d / high_60d | 区间最高价 | `最高.rolling_max(20/60)` |
| low_20d / low_60d | 区间最低价 | `最低.rolling_min(20/60)` |
| price_pos_20 | 20日价格位置 | `(收盘-low_20d) / (high_20d-low_20d)`，0~1 |
| price_pos_60 | 60日价格位置 | `(收盘-low_60d) / (high_60d-low_60d)`，0~1 |

### 2.6 技术指标类（9 个）

| 因子 | 定义 | 公式 |
|------|------|------|
| macd_dif | MACD 快慢线差 | `EMA12 - EMA26` |
| macd_dea | MACD 信号线 | `dif 的 EMA9` |
| macd_hist | MACD 柱 | `2 × (dif - dea)` |
| rsi_14 | 相对强弱 | `100 - 100/(1 + 14日均涨幅/14日均跌幅)` |
| bb_mid | 布林带中轨 | `收盘.rolling_mean(20)` |
| bb_upper | 布林带上轨 | `mid + 2×std20` |
| bb_lower | 布林带下轨 | `mid - 2×std20` |
| bb_width | 布林带宽度 | `(upper - lower) / mid` |
| bb_pos | 布林带位置 | `(收盘 - lower) / (upper - lower)`，0~1 |

### 2.7 标记类（5 个）

| 因子 | 定义 | 公式 |
|------|------|------|
| limit_up | 涨停标记 | `ret_1d > 0.095`，0/1 |
| limit_down | 跌停标记 | `ret_1d < -0.095`，0/1 |
| is_suspended | 停牌标记 | `成交量 == 0`，0/1 |
| up_streak | 连涨天数 | `ret_1d>0` 的连续段计数（rle_id + cum_sum） |
| down_streak | 连跌天数 | `ret_1d<0` 的连续段计数 |

---

## 三、IC 体检结果（45 因子 × 4 个预测期）

**方法**：横截面 IC = 每日因子值与未来 N 日收益（fwd_1d/5d/10d/20d）的 Spearman 相关，汇总 IC 均值 / ICIR（均值÷标准差）/ IC>0 占比。数据：`ic_report.csv`。

### 核心结论：A股短线是反转市场

**几乎所有因子 IC 为负**——放量/高换手/涨多/波动大 → 未来跌。唯一强正 IC 是涨停惯性。

### 强单因子（|IC|>0.03 且 |ICIR|>0.5）

| 因子 | 预测期 | IC 均值 | ICIR | IC>0 占比 |
|------|--------|---------|------|-----------|
| limit_up | fwd_1d | **+0.0415** | **+0.66** | 75.3% |
| limit_down | fwd_1d | -0.0342 | -0.63 | 17.0% |
| limit_down | fwd_5d | -0.0303 | -0.64 | 17.2% |
| turn_ma5 | fwd_20d | **-0.0940** | -0.51 | 29.9% |

### 代表性负 IC 因子（fwd_20d 预测）

| 因子 | IC 均值 | ICIR | 解读 |
|------|---------|------|------|
| turn_ma5 | -0.094 | -0.51 | 高换手 → 跌（最强反转信号） |
| vol_10d | -0.077 | -0.43 | 高波动 → 跌 |
| vol_20d | -0.079 | -0.40 | 高波动 → 跌 |
| ret_20d | -0.069 | -0.46 | 涨多 → 跌 |
| macd_dif | -0.061 | -0.45 | 强势指标 → 跌 |
| ma20_dist | -0.058 | -0.40 | 偏离均线远 → 跌 |
| atr_ratio | -0.080 | -0.39 | 波动大 → 跌 |
| vol_ratio_20 | -0.026 | -0.29 | 放量 → 跌 |
| limit_up | -0.011 | -0.20 | 涨停惯性仅限次日（fwd_1d），20日转负 |

> 这解释了 v1 追涨策略（放量+MACD金叉+站上均线）为何亏 89.5%——全踩在负 IC 因子上。

---

## 四、因子在策略中的用途

### v7 横截面打分（当前最优策略，+6.9%）

每日全市场 6 因子加权排名选 Top 3（负 IC 因子取反），公式：

```
score = rank(-ret_5d×turn_ma5)   × 0.25   # 低换手+回调（反转）
      + rank(-ma5_dist×turn_ma5) × 0.20   # 偏离均线（反转）
      + rank(-vol_10d-vol_change_5d)×0.15 # 低波动+缩量（反转）
      + rank(limit_up_5d)        × 0.15   # 涨停惯性（正IC，需现算 rolling_sum(5)）
      + rank(-turn_ratio)        × 0.15   # 低换手（反转）
      + rank(macd_dif)           × 0.10
```

- 基础过滤：可交易（非停牌/涨停/跌停）+ 站上 MA20 + price_pos 0.1~0.85 + 市场 2/3 条件
- 验证：walk-forward 106 笔 OOS +9.3%，盈利段 6/11，同期沪深300 -10.9%
- 提频测试：TOP_N=3 最优，扩大选股数收益下降（边际信号质量差）

### 回测精简版

`factor_bt.parquet`（497MB，17 列）：从 3.3GB 全库提取回测所需列（含 limit_up_5d），2GB 内存机器可全量加载。

### 模拟盘

`daily_picks.py` / `paper_trading.py`：每日按 v7 打分输出清单并模拟交易，读取方统一走多文件 scan（factor_daily + factor_daily_incr）。

---

## 五、维护注意

1. **因子公式唯一来源**：`factors.py` 的 `calc_factors()`，任何改动必须同步 `build_factors_pl.py` 与增量链路。
2. **增量文件**：`factor_daily_incr.parquet` 定期（月度）用 sink 流式合并进大文件后删除；所有消费方必须走多文件 scan，漏读增量会静默用旧数据选股。
3. **已知 polars 坑**：`min_periods` 已改名 `min_samples`；spearman corr 的 NaN 需 `fill_nan(None).drop_nulls()`；单日 rolling 窗口不足全 null（选股脚本需先加载目标日前 5 个交易日）。
4. **回测哑火防护**：固定仓位（现金<1万即永久空仓）是历史教训，回测与模拟盘均用动态仓位。
