# AKShare — A股历史日K数据批量采集（2026-08 验证版）

AKShare 是开源 Python 库，封装东方财富/腾讯等数据源。**云服务器环境优先用腾讯源**，东方财富源封禁机房 IP。

## 安装

```bash
pip install akshare pyarrow pandas
# pyarrow 必须装——Parquet 存储引擎，不装会导致 to_parquet() 崩 ImportError
# PEP 668 环境用 venv：python3 -m venv venv && venv/bin/pip install akshare pyarrow pandas
```

## 数据源选择（关键）

| 后端 | 函数 | 云服务器 | 复权推荐 |
|------|------|----------|----------|
| **腾讯源** | `ak.stock_zh_a_hist_tx(symbol, start_date, end_date, adjust='hfq')` | ✅ 不限制IP | **hfq**（qfq有负数bug） |
| 东方财富源 | `ak.stock_zh_a_hist(symbol, period='daily', start_date, end_date, adjust='qfq')` | ❌ 封禁云IP | qfq（正常） |

腾讯源返回列（英文）：date/open/close/high/low/volume/turnover/amount — **无涨跌幅/换手率**，需自行计算。

## 获取全A股列表

```python
# 腾讯源（云服务器可用）
stock_list = ak.stock_zh_a_spot_tx()  # code 列格式: sh600519, 需 str[2:] 去前缀

# 东方财富源（仅家庭宽带可用）
stock_list = ak.stock_zh_a_spot_em()
```

## 数据采集最佳实践

### 过滤策略
- 排除 ST：`df[~df['name'].str.contains('ST', na=False)]`（腾讯源列名是 `name`，东方财富源是 `名称`）
- 排除北交所（`bj` / `83`/`87` 开头）
- 排除上市不满 250 个交易日（约1年）

### 存储格式
- **Parquet**（推荐）：`df.to_parquet('data.parquet', index=False)` — 比 CSV 小 5-10 倍
- **⚠️ pyarrow 必须装**，否则报 `ImportError: Unable to find a usable engine`

### 断点续传
- 维护已采集代码集合 → 进度文件（`.collect_progress.txt`）
- **每 100 只存一次 parquet + 进度文件**（双写原子性）
- 崩溃后修复：对比进度文件和 parquet 的唯一代码集，差异部分从进度删除
- 模板脚本见 `templates/collect_daily_kline_tx.py`

### 请求控制
- `time.sleep(0.3)` 间隔
- 最多 3 次重试，间隔递增（2s/4s/6s）

## 已知坑点（2026-08 验证）

1. **腾讯源 qfq 前复权负数**：约 200 只高分红股票（茅台/汾酒/中远海控等）前复权价格为负数。`df[df['收盘'] < 0]` 非零 = 数据不可用于回测。**必须用 hfq**。
2. **云服务器 IP 封禁**：东方财富 API 识别腾讯云/阿里云 IP 主动断开（`RemoteDisconnected`），TCP 通但 HTTP 不通。切换到腾讯源解决。
3. **pyarrow 缺失**：`to_parquet()` 崩，`pip install pyarrow`。
4. **进度文件与数据失同步**：采集脚本保存前崩溃时，进度已标记但数据丢失。用 `fix_progress.py` 对比修复。
5. **PEP 668**：Ubuntu 需 venv，不能直接 `pip install`。

## 数据规模参考（实测）

- 过滤后股票数：~4,800 只（排除ST/北交所）
- 15年记录/只：~3,900-4,000 天
- 总记录数：~1,240 万行
- Parquet 大小：~260 MB
- 全量采集耗时（腾讯源 0.3s 间隔）：~20-30 分钟
