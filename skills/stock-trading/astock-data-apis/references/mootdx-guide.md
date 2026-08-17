# mootdx（通达信 Python 接口）

## 为什么用它

东方财富 push2 接口封禁云服务器 IP，而 mootdx 走**通达信独立服务器**，不受此限制。

## 安装

```bash
pip install mootdx
```

## 核心用法

```python
from mootdx.quotes import Quotes
client = Quotes.factory(market='std')  # 自动选择最快服务器

# 板块分类
df = client.block()  # 返回 ~386k 条板块-股票映射
# 列: blockname, block_type, code_index, code

# 历史K线
df = client.bars(symbol='600519', frequency=9, start=0, offset=100)
# frequency: 9=日线, 8=周线, 7=月线, 6=5分钟, 5=15分钟, 4=30分钟, 3=60分钟

# 财务数据
df = client.finance(0)  # F10 财务摘要

# 股票列表
df = client.stocks(market=1)  # 0=深圳, 1=上海

# 指数K线
df = client.index_bars(symbol='399300', frequency=9, start=0, offset=500)
```

## 适用场景

- 板块分类/行业归属（弥补腾讯源无板块信息的短板）
- 分钟级K线
- 财务数据
- 云服务器上替代东方财富的行情补充

## 不支持

- 北向资金（需东方财富或港交所）
- 板块资金流（东方财富独有）
- 主力资金净流入（东方财富独有）

## 与腾讯源互补

| 数据 | 腾讯 qt.gtimg.cn | mootdx |
|------|------|--------|
| 日K (hfq) | ✅ stock_zh_a_hist_tx | ✅ bars() |
| 股票列表 | ✅ stock_zh_a_spot_tx | ✅ stocks() |
| 板块分类 | ❌ | ✅ block() |
| 分钟K | ❌ | ✅ bars(freq=5~60) |
| 财务 | ❌ | ✅ finance() |
