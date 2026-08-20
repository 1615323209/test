# 巨潮资讯网（cninfo）历史公告接口 — 探测与采集记录

2026-08-18 实测。目标：拿全市场（除退市股）2021-2024 历史公告，练"新闻情绪因子"入池回测（此前因缺历史数据无法做 PIT 对齐的回测）。

## 为什么是巨潮

| 候选源 | 结果 |
|--------|------|
| 东财 F10 公告 `np-anotice-stock.eastmoney.com/api/security/ann` | ✅ 可用但**只有近期**（拉不到历史年份） |
| 东财 getNews/getListInfo | 404 |
| 东财 search-api-web 按关键词 | 返回 `passportWeb`（股吧用户账号）非新闻文章，不可用 |
| 财联社电报 `telegraphList` | 404 |
| 新浪滚动 `feed.mix.sina.com.cn/api/roll/get` | 需注册 lid，翻历史不可靠 |
| 同花顺资讯 | 可拉近期，历史深度未知 |
| **巨潮资讯网 cninfo** | ✅ **任意年份精确日期**，沪深交易所官方披露 |

## 接口细节（已验证）

### 1. 深市全市场股票 orgId
```
GET http://www.cninfo.com.cn/new/data/szse_stock.json
→ 6237 只, 每条 {code, pinyin, category:'A股', orgId, zwjc}
   e.g. 000001 → orgId=gssz0000001, zwjc=平安银行
```
沪市同路径 sse_stock.json 返回 HTML 非 JSON（需要登录 Cookie），不可用。

### 2. 任意股票 orgId + 退市标记（核心）
```
POST http://www.cninfo.com.cn/new/information/topSearch/query
data: keyWord={代码}&maxNum=5
→ [{code, zwjc:名称, orgId, delisted:布尔, type}]
```
**逐只查**（约 0.2s/只，5544 只全量 ~20 分钟）。delisted 直接标注退市，过滤简单。

### orgId 无统一推导规则（重要坑）
实测：
- 000001 平安银行 → `gssz0000001`
- 600519 贵州茅台 → `gssh0600519`
- 000002 万科A → `gssz0000002`
- 000858 五粮液 → `gssz0...` 规则可推
- **300750 宁德时代 → `GD165627`** ← 推导(gssz0300750)失败
- **688981 中芯国际 → `gshk0000981`**
- **601318 中国平安 → `9900002221`**

结论：**必须逐只 topSearch**，不要按 `gssz0+code`/`gssh0+code` 推导（仅部分股票成立）。

### 3. 历史公告查询
```
POST http://www.cninfo.com.cn/new/hisAnnouncement/query
data:
  pageNum: 1
  pageSize: 30
  column: szse
  tabName: fulltext
  plate: ""
  stock: "{code},{orgId}"    ← 必须成对，只传 code 返回 0
  searchkey: ""
  secid: ""
  category: ""
  trade: ""
  seDate: "2023-01-01~2023-12-31"   ← 历史年份关键参数
  sortName: ""
  sortType: ""
  isHLtitle: "true"
→ {totalAnnouncement, announcements:[{announcementTitle, announcementTime(ms时间戳), adjunctUrl}]}
```
分页直到 `pageNum*30 >= totalAnnouncement`。

### 实测容量（2023 全年）
- 600519 茅台：80 条
- 300750 宁德时代：200 条
- 688981 中芯国际：100 条
- 000002 万科：187 条

## 采集器实现（D:\quant_project\code\data\collect_announcements.py）
- 阶段A `--build-map`：逐只 topSearch 建 `stock_org_map.csv`（代码/orgId/名称/delisted），每 50 只落盘一次，可断点续跑
- 阶段B `--collect [N]`：逐股拉 2021-2024 公告，存 `announcements/{code}.jsonl`（每条 date+title+pdf），已存在则跳过（断点续跑），每 20 只打印进度
- 全量规模：~4700 上市股 × 年均60条 × 4年 ≈ 110万条，需数小时~一天后台跑

## 后续因子构建方向
公告标题 → 关键词情绪分类（利好/利空/中性，已实现于 `data/news_sentiment.py`）→ 日频新闻情绪因子 → PIT 对齐（公告日期 ≤ 交易日收盘算 T 日信号，否则 T+1）→ 走 L1-L4 回测。
