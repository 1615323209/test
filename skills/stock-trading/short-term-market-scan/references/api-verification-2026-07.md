# API 接口验证记录（2026-07 实测）

## 测试环境
- 系统：Windows 10
- 测试时间：2026-07-05 周日（非交易日）
- 工具：curl via git-bash

---

## 可用接口（✅ 正常）

### 1. 财联社大盘指数 + 涨跌分布
```
GET https://x-quote.cls.cn/quote/index/home?app=CailianpressWeb&os=web&sv=8.4.6
Headers:
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36
  Referer: https://www.cls.cn/
```
返回结构：JSON，字段 data.index_quote[] + data.up_down_dis
实测返回：上证/深证/创业板/科创50/沪深300/中证500 点位，涨停157 跌停24 等

### 2. 东方财富板块资金流向
```
GET https://data.eastmoney.com/dataapi/bkzj/getbkzj?key=f62&code=m%3A90%2Bs%3A4
Headers:
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36
  Referer: https://data.eastmoney.com/
```
返回结构：JSON，data.diff[].f14（板块名）/ f62（主力净流入，元）
实测返回：汽车零部件+68.93亿、消费电子+39.92亿、半导体-194.77亿 等

### 3. 财联社快讯电报
```
GET https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9
Headers:
  Referer: https://www.cls.cn/
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36
```
返回结构：JSON，data.roll_data[].title/content/ctime/level
注意：level="A" 为重要快讯；非交易日 ctime 可能是昨日数据，需校验日期

### 4. 东方财富历史 K 线
```
GET https://push2his.eastmoney.com/api/qt/stock/kline/get?fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=1&secid={1或0}.{代码}&beg=0&end=20500101&lmt=60
Headers:
  Referer: https://quote.eastmoney.com/
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```
返回：data.name（股票名）+ data.klines[]（逗号分隔：日期,开,收,高,低,量,额,振幅,涨跌幅,涨跌额,换手率）
实测：贵州茅台600519 → 正常返回，MA10可正常计算

### 5. 腾讯实时行情（个股替代方案）
```
GET https://qt.gtimg.cn/q={sh或sz}{代码}
Headers:
  Referer: https://gu.qq.com/
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```
返回：~分隔字符串，关键下标（0-based）：
  [1]股票名 [3]最新价 [4]昨收 [5]今开 [6]成交量(手)
  [8]涨跌额 [9]涨跌幅% [33]成交额(千元) [44]PE [46]换手率
实测：sh600519 → 贵州茅台 1194.45元，-0.71%

---

## 曾经报错但实际可用（⚠️ 需注意用法）

### push2.eastmoney.com 行情接口
之前用 `80.push2.eastmoney.com` 子域名，curl 会报 `schannel: server closed abruptly`。
原因：80.push2.eastmoney.com 走的是 80 端口 HTTP，但 curl 默认走 HTTPS 443，TLS 握手失败。
正确做法：直接用 `push2.eastmoney.com`（不带 "80." 前缀），加 Referer 即可正常返回。

### 6. 东方财富全量股票列表（clist 分页接口）
```
GET https://push2.eastmoney.com/api/qt/clist/get?pn={页码}&pz={每页数量}&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23&fields=f12,f14,f2,f3
Headers:
  Referer: https://www.eastmoney.com/
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```
返回：data.total（全量股票数），data.diff[].f12（代码）f14（名称）f2（最新价）f3（涨跌幅）
实测（2026-07-11 周六非交易日）：total=4096（沪深主板+创业板，不含科创板和北交所）
全量拉取方式：pz 设 200，循环 ceil(total/200) 页即可。加上科创板（fs=m:1+t:23）和北交所（fs=m:0+t:81+s:2048）可覆盖全市场约 5000+ 支。

### 7. 新浪实时行情（批量，必须带 Referer）
```
GET http://hq.sinajs.cn/rn={时间戳}&list={sh/sz}{代码},{sh/sz}{代码},...
Headers:
  Host: hq.sinajs.cn
  Referer: https://finance.sina.com.cn/
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```
注意：不带 Referer 直接请求会被拦截返回空。返回 GBK 编码，需转码处理。
实测：sh600519（贵州茅台）返回完整行情，数据格式：逗号分隔，第1个字段为股票名（GBK）。

---

## 市场前缀速查
- 沪市（sh）：secid=1.XXXXXX，腾讯前缀=sh
- 深市（sz）：secid=0.XXXXXX，腾讯前缀=sz
- 6开头→沪市，0/3开头→深市
