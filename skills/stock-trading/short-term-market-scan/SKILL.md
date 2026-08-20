---
name: short-term-market-scan
description: 每日开盘前（9:00-9:25）短线市场环境扫描。判断今日是否适合操作，输出"可操作/观望"结论。直接调用财联社/东方财富公开接口，无需任何API Key。
triggers:
  - "今天能操作吗"
  - "今天市场怎么样"
  - "开盘前扫描"
  - "大盘环境"
  - "今日市场情绪"
---

# Short-Term Market Scan（短线开盘前环境扫描）

## 触发时机
每个交易日 9:00~9:25 执行，判断今日是否适合短线操作。

## 数据源（全部来自 go-stock-dev 已验证的公开接口）

### 1. 大盘指数 + 涨跌分布
来源：财联社市场接口（无需鉴权）

```
GET https://x-quote.cls.cn/quote/index/home?app=CailianpressWeb&os=web&sv=8.4.6
Headers:
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36
  Referer: https://www.cls.cn/
```

关注字段：
- index_quote[].secu_name / last_px / change（上证/深证/创业板点位和涨跌幅）
- up_down_dis.up_num（涨停数）/ down_num（跌停数）/ rise_num（上涨家数）/ fall_num（下跌家数）/ average_rise（平均涨跌幅）

### 2. 板块主力资金流向
来源：东方财富板块资金接口（无需鉴权）

```
GET https://data.eastmoney.com/dataapi/bkzj/getbkzj?key=f62&code=m%3A90%2Bs%3A4
Headers:
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36
  Referer: https://data.eastmoney.com/
```

关注字段：
- data.diff[].f14（板块名）/ f62（主力净流入，正=流入 负=流出）
- 取 f62 前5名（净流入最多）和后5名（净流出最多）

### 3. 财联社快讯（突发利好/利空）
来源：财联社电报接口（无需鉴权）

```
GET https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9
Headers:
  Referer: https://www.cls.cn/
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)...
```

关注字段：
- data.roll_data[].title / content（近30条快讯标题和内容）
- 重点看 level="A"（重要快讯）

## 执行步骤

1. 并行请求以上3个接口
2. 解析数据，按以下评分模型打分

## 评分模型（满分5分）

| 指标 | 条件 | 得分 |
|------|------|------|
| 上涨家数 | rise_num > fall_num | +1 |
| 涨停数量 | up_num > 50 | +1 |
| 平均涨幅 | average_rise > 0 | +1 |
| 板块资金 | 前5板块至少3个净流入 > 5亿 | +1 |
| 无重大利空 | 快讯无"暴跌/崩盘/重大风险/监管处罚" | +1 |

## 输出格式

```
===== 今日市场环境扫描 =====
时间：YYYY-MM-DD HH:MM

大盘指数：
  上证指数：XXXX.XX（+X.XX%）
  深证成指：XXXX.XX（+X.XX%）
  创业板指：XXXX.XX（+X.XX%）

市场情绪：
  上涨：XXXX家 | 下跌：XXXX家
  涨停：XX家   | 跌停：XX家
  平均涨幅：+X.XX%

资金热点（主力净流入前3板块）：
  1. XX板块：+XX亿
  2. XX板块：+XX亿
  3. XX板块：+XX亿

重要快讯（最近3条A级）：
  - HH:MM: XXXX

环境评分：X/5分
操作建议：【可以操作】/ 【今日观望】

理由：...
```

## 判断规则

> ⚠️ **2026-08-18 用户明确要求：环境评分不得作为选股/买入的硬关卡**（用户"只信选股因子"）。本 skill 的评分只作为**市场环境快照/参考备注**，不再决定"今日能不能选股/买入"。唯一保留的环境安全阀：跌停家数≥200 的系统性跌停潮才标"🚨极端风险谨慎追高"。因子选股（daily_picks 或 short-term-stock-pick）**无条件输出**，环境评分只作一行备注。不要把本节的"评分<=1观望空仓"当硬规则去拦选股——用户已否决过一次。

- 评分 >= 3：可以操作，附热点板块方向
- 评分 2：谨慎操作，仓位减半
- 评分 <= 1：今日观望（**仅供参考备注，不拦截因子选股**）

## 注意事项

本 5 分制盘面环境评分**只作参考备注，不作量化因子选股的拦截关卡**。用户明确「只信选股因子，不需要市场环境规则去选股」——09:20 cron 已改为：**量化因子选股（daily_picks）必执行并作为主信号输出，盘面环境评分只是展示性参考**；唯一保留的环境拦截是**系统性跌停潮（跌停家数 ≥200）标"极端风险谨慎追高"**。

- ✅ 正确用法：输出环境评分供用户参考，同时照常给出量化因子选股 + 盘面候选合并建议。

## 注意事项

- 竞价阶段（9:15-9:25）数据更准确，尽量在9:20后执行
- 遇到接口返回空数据，说明非交易日，直接输出"今日非交易日"
- 快讯接口有时返回cached数据，需检查 ctime 是否为今日
