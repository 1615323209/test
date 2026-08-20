---
name: astock-data-apis
description: A股数据接口调用规范 — 新浪/腾讯/东方财富公开行情接口的正确 Header、已知坑和数据说明。适用于任何需要抓取 A股实时行情、K线、板块数据的场景。
triggers:
  - 调用新浪/腾讯/东方财富行情接口
  - 获取 A 股全量股票列表
  - 拉取 K 线数据
  - 接口返回空或 SSL 错误时排查
---

# A股公开数据接口规范

## 股票数量参考

A股全市场约 5400+ 支（随时间增加）：
- 沪市主板约 1700 支
- 深市主板+创业板约 2700 支
- 科创板约 600 支
- 北交所约 250 支

东方财富 clist 接口一次查全量约返回 4433 条（`fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23,m:0+t:81+s:2048` 含沪深主板+创业板+北交所），循环分页（每页 pz=200，约 23 页）可拿完。

## 接口一：新浪实时行情

URL: `http://hq.sinajs.cn/rn={timestamp}&list={codes}`

codes 格式：`sh600519,sz000858,hk01810,gb_aapl`

必须携带的 Header：
```
Host: hq.sinajs.cn
Referer: https://finance.sina.com.cn/
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```

不带 Referer 会被拒绝（返回空或 403）。

返回格式：GBK 编码，需 decode('gbk')，数据用逗号分隔，字段顺序固定（股票名、开盘、昨收、现价、最高、最低、买一至买五、卖一至卖五、成交量、成交额、日期、时间）。

### 新浪全市场代码→名称列表（比东财稳定，推荐做代码名映射）

URL: `http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData`
参数：`page=1&num=500&sort=symbol&asc=1&node=hs_a`（node=hs_a 沪深A股含创业板/科创板/北交所）
返回 JSON 数组：`[{"symbol":"bj920000","code":"920000","name":"安徽凤凰",...}]`
分页遍历 num=100~500/页，直到返回空。**比东财 search 稳定**——东财 search 按关键词返回的是 `passportWeb`（股吧用户账号）而非新闻文章，不适合做个股新闻来源。

## 接口二：腾讯实时行情

URL: `http://qt.gtimg.cn/?_={timestamp}&q={codes}`

codes 格式：`sh600519,sz000858`

无特殊 Header 要求，直接请求可用。返回字段更丰富，含五档盘口、市值、PE等，GBK 编码。

## 接口三：东方财富行情列表（clist）

URL: `http://push2.eastmoney.com/api/qt/clist/get` ← **必须用 http://，不能用 https://（Windows 环境会 SSL 握手失败）**

关键参数：
- pn: 页码（从1开始）
- pz: 每页数量（最大200）
- fs: 市场过滤，如 `m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23,m:0+t:81+s:2048` 为沪深主板+创业板+北交所
- fields: 请求字段
- fid: 排序字段，f3=涨跌幅，f62=主力净流入

需携带 Header：
```
Referer: https://www.eastmoney.com/
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
Accept: application/json, text/plain, */*
```

返回 JSON，`data.total` 为总数量，`data.diff` 为当页数据列表。全市场约4433支（含北交所）。

**坑：clist 的 `fs` 含北交所 `s:2048` 可能触发接口拒连（Connection aborted/RemoteDisconnected）**。实测：
- `fs=m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23`（沪深主板+创业板+科创板）→ 稳定返回
- 加 `m:0 t:81 s:2048`（北交所）→ 偶发 RemoteDisconnected，重试会全失败
- 若某域名（如 `82.push2`）拒连，换 `push2.eastmoney.com` 或 `52.push2.eastmoney.com` 常能恢复；临时限流会自愈，等几秒重试即可
- 单页 pz=5000 拉全量也易拒连，**用 pz=500 翻页**更稳
- 做"代码→公司名"映射优先用新浪全市场列表（上节），别依赖东财 search（返回股吧账号非文章）


### 板块资金流（行业/概念）

行业板块：`fs=m:90+t:2+f:!50`，字段含 f62(主力净流入)/f66(超大单流入)/f75(大单流出)等
概念板块：`fs=m:90+t:3+f:!50`
排序：`fid=f62`（按主力净流入排序）
**周末/非交易日概念资金流接口可能返回502**，需加重试逻辑，3次重试间隔1s/2s/3s。

### 个股资金流

URL: `http://push2.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={market}.{code}&...`
market: sh→1, sz/bj→0

### 基金数据接口

见 references/fund-data-apis.md —— 基金基本信息（HTML爬取+pingzhongdata.js备用）、实时估值（fundgz.1234567.com.cn）、历史净值（api.fund.eastmoney.com/f10/lsjz）、十大持仓、场内ETF。

## 东方财富市场代码映射（Python normalize_code 参考）

1. push2.eastmoney.com **必须用 HTTP**，不能 HTTPS（Windows 环境 schannel: server closed abruptly）。
   push2 his.eastmoney.com（K线）、push2ex.eastmoney.com（异动/涨停）同理。

2. 非交易日（周末、节假日）实时行情接口可能返回空列表，部分接口（概念资金流、涨停池）可能502，加重试兼容。

3. 新浪接口必须用 HTTP，必须带 `Host: hq.sinajs.cn` 和 `Referer: https://finance.sina.com.cn/`。

4. 东方财富部分接口（如 xuangu.eastmoney.com 选股）需要 cookie 中的 `qgqp_b_id`。

5. GBK 解码优先级：gb18030 > gbk > chardet。不要先调 chardet——短字节样本（4字节）chardet 会误判为 Latin-1 等西欧编码，导致中文乱码。

6. F10 数据（财务报表、融资融券、沪深港通）用 `https://datacenter.eastmoney.com/securities/api/data/v1/get`（不是 datacenter-web.eastmoney.com），返回 JSONP 需手动剥壳 `callback(...)`。

7. 个股公告接口：`https://np-anotice-stock.eastmoney.com/api/security/ann`，必须带 `Host: np-anotice-stock.eastmoney.com`，`ann_type=SHA%2CCYB%2CSZA%2CBJA%2CINV`（含北交所），否则返回空或断连。

8. 龙虎榜：`https://datacenter-web.eastmoney.com/api/data/v1/get`，reportName=RPT_DAILYBILLBOARD_DETAILSNEW，filter 格式 `(TRADE_DATE<='2026-07-10')(TRADE_DATE>='2026-07-10')`，JSONP 响应。

9. 市场统计（涨跌家数/涨停跌停）：**不要用 push2 的 market/s/get**（已404）。用财联社 `https://x-quote.cls.cn/quote/index/home?app=CailianpressWeb&os=web&sv=8.4.6`。

10. 投资日历：`https://app.jiuyangongshe.com/jystock-app/api/v1/timeline/list`，POST 方法，body `{"date":"2026-07","grade":"0"}`，需要固定 token/Cookie header。

11. Python URL 模板字符串里如有 `{data}` 必须写成 `{{data}}`，否则 str.format() 会误认为变量引用报 KeyError。

## 接口：巨潮资讯网（cninfo）历史公告 —— 唯一可靠的历史公告/新闻源

**用途**：要练"新闻/公告情绪因子"入池，必须有**历史公告**（做 PIT 回测）。东财 F10 公告只有近期、新浪资讯无历史分页、财联社电报接口路径常 404——**巨潮资讯网（沪深交易所官方披露）是唯一能查任意年份精确日期历史公告的来源**。

### 关键接口

**1. 全市场股票 orgId（深市）**
```
GET http://www.cninfo.com.cn/new/data/szse_stock.json
```
返回 6237 只，每条含 `code`(000001) `zwjc`(平安银行) `orgId`(gssz0000001) `category`(A股)。**沪市同路径 `sse_stock.json` 需要登录 Cookie，拿不到**——沪市用下方 topSearch。

**2. 单只股票 orgId + 退市标记（任意市场，逐只查）**
```
POST http://www.cninfo.com.cn/new/information/topSearch/query
data: keyWord={代码}&maxNum=5
```
返回含 `code` `zwjc`(名称) `orgId` `delisted`(布尔,是否退市)。**这是过滤退市股的可靠方法**。

**⚠️ orgId 不能凭代码简单推导**——只有部分股票符合 `gssz0+code`/`gssh0+code`（平安银行 000001→gssz0000001、茅台 600519→gssh0600519 成立），但宁德时代→`GD165627`、中芯国际→`gshk0000981`、中国平安→`9900002221` 各不同。**必须逐只 topSearch 拿正确 orgId**。

**3. 历史公告查询（带精确日期）**
```
POST http://www.cninfo.com.cn/new/hisAnnouncement/query
data: pageNum=1&pageSize=30&column=szse&tabName=fulltext&plate=&stock={code},{orgId}&searchkey=&secid=&category=&trade=&seDate={起始}~{结束}&sortName=&sortType=&isHLtitle=true
```
- `stock` 参数**必须带 orgId**（只传代码返回 0 条）
- `seDate` 指定历史年份（如 `2023-01-01~2023-12-31`）——**能精确回溯任意年份**
- 返回 `data.totalAnnouncement`（总数）+ `data.announcements[]`（每条含 `announcementTitle` 标题 + `announcementTime`(毫秒时间戳→换算日期) + `adjunctUrl` PDF）
- 分页 `pageNum` 递增直到 `page*30 >= total`
- 需带 `Referer: http://www.cninfo.com.cn/new/` + User-Agent

**实测容量**：茅台 2023 全年 80 条、宁德时代 200 条、中芯国际 100 条、万科 187 条。全市场约 4700 只上市股 × 年均 ~60 条 × 4 年 ≈ 110 万条公告，全量采集需数小时~一天后台断点续跑。

**完整采集器**：`D:\quant_project\code\data\collect_announcements.py`（阶段A 构建 code→orgId 映射含退市过滤、阶段B 逐股拉 2021-2024 公告存 jsonl 断点续跑），或参考 `references/cninfo-historical-announcements.md`。

### 坑
- 须 `stock={code},{orgId}` 成对传，orgId 漏了查不到
- `seDate` 是查询历史的关键参数，日常只能东财 F10 拉近期，历史必走巨潮
- 公告接口偶发断连，加重试；采集务必断点续跑（逐股存 jsonl）

## 常用字段映射（东方财富 fields）

f2=现价, f3=涨跌幅, f5=成交量, f6=成交额, f8=换手率, f9=动态PE,
f10=量比, f12=代码, f14=名称, f15=最高, f16=最低, f17=开盘,
f18=昨收, f20=总市值, f21=流通市值, f23=市净率

## 东方财富市场代码映射

Python normalize_code() 规则：
- 60xxxx / 688xxx / 900xxx → sh 前缀
- 00xxxx / 30xxxx / 20xxxx → sz 前缀
- 83xxxx / 43xxxx / 87xxxx → bj 前缀（北交所）
- 5位纯数字 → hk 前缀（港股）
- 纯字母 → gb_ 前缀（美股）

转为东方财富 secid：sh→"1." + 纯数字，sz/bj→"0." + 纯数字，hk→"116."
转为 datacenter SECUCODE：纯数字 + ".SH"/".SZ"/".BJ"

## 参考文件

- references/go-stock-api-patterns.md — go-stock 项目中实际验证的接口调用模式
- references/fund-data-apis.md — 基金数据接口（基本信息、净值、估值、持仓、历史净值）完整模式
- references/cninfo-historical-announcements.md — 巨潮历史公告接口探测记录 + 采集器用法（见上文"巨潮资讯网"章节）
