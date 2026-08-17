# go-stock 项目实际验证的接口调用模式

来源：D:/AI_project/code/03_Agent_bA/go-stock-dev

## 新浪接口调用（stock_data_api.go）

```python
import requests, time

url = f"http://hq.sinajs.cn/rn={int(time.time())}&list=sh600519,sz000858"
headers = {
    "Host": "hq.sinajs.cn",
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
}
resp = requests.get(url, headers=headers)
resp.encoding = 'gbk'
print(resp.text)
```

返回示例（GBK解码后）：
```
var hq_str_sh600519="贵州茅台,1182.200,1182.190,1204.980,...,2026-07-10,15:34:59";
```

字段顺序（逗号分隔，共32个字段）：
0=股票名, 1=今日开盘, 2=昨日收盘, 3=当前价格, 4=今日最高, 5=今日最低,
6=竞买价, 7=竞卖价, 8=成交股数, 9=成交金额,
10-19=买一至五（价+量交替）, 20-29=卖一至五,
30=日期, 31=时间

## 腾讯接口调用（stock_data_api.go）

```python
url = f"http://qt.gtimg.cn/?_={int(time.time())}&q=sh600519,sz000858"
resp = requests.get(url)
resp.encoding = 'gbk'
```

## 东方财富 clist 接口（全量股票列表）

```python
def fetch_all_stocks():
    base = "http://push2.eastmoney.com/api/qt/clist/get"  # 必须 http，不能用 https
    headers = {
        "Referer": "https://www.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    fs = "m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"  # 含北交所
    fields = "f12,f14,f2,f3,f5,f6,f8,f9,f10,f20,f21,f23"
    page, page_size = 1, 200
    all_stocks = []
    
    while True:
        params = {
            "pn": page, "pz": page_size, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": fs, "fields": fields,
            "_": int(time.time() * 1000)
        }
        resp = requests.get(base, params=params, headers=headers)
        data = resp.json()
        diff = data.get("data", {}).get("diff", [])
        if not diff:
            break
        all_stocks.extend(diff)
        if len(all_stocks) >= data["data"]["total"]:
            break
        page += 1
    return all_stocks
```

加上科创板（m:1+t:23+s:2048）和北交所（m:0+t:81+s:2048）可覆盖全市场。

## ⚠️ 东方财富反爬破解：Session 预热（2026-08 实测，Windows/Linux 均有效）

**症状**：直接 `requests.get(push2.eastmoney.com/...)` 返回 `Connection aborted / RemoteDisconnected`（服务端主动断连），**不限 IP**（云服务器、家庭宽带都拒）。AKShare 内部用独立 requests 调用，无法过反爬。

**破解**：用同一个 `requests.Session()` 先 GET 一次 `https://www.eastmoney.com/` 首页（建立 session/cookie），再用**同一 Session** 调 push2 API：

```python
s = requests.Session()
s.headers.update({'User-Agent': 'Mozilla/5.0 ...', 'Referer': 'https://www.eastmoney.com/'})
s.get('https://www.eastmoney.com/', timeout=15)   # 预热，关键一步
r = s.get('http://push2.eastmoney.com/api/qt/clist/get', params=params, timeout=15)  # 正常返回
```

**限频**：约连续 37 页（每页 200 条）后再次被断 → 重建全新 Session（`s.close()` + 新 Session + 重新预热）。大采集脚本要带重试：失败 → 重建 Session → 指数退避重试。

**坑**：`data.diff` 在 JSON 里可能是 dict（`{"0": {...}, "1": {...}}`）不是 list，取数据用 `list(diff.values())` 兼容。

## 个股资金流历史（push2his，2026-08 实测）

URL: `http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`（**必须 HTTP**，push2his 用 HTTPS 会 RemoteDisconnected）

```python
params = {
    'lmt': '0', 'klt': '101',
    'secid': f'{market}.{code}',   # market: sh→1, sz/bj→0
    'fields1': 'f1,f2,f3,f7',
    'fields2': 'f51,f52,...,f65',
    'ut': 'b2884a393a59ad64002292a3e90d46a5',   # 注意：和 clist 的 ut 不同！
    '_': str(int(time.time()*1000))
}
```

- `lmt=0` 返回约**半年 120 条**（每只，近端数据；历史更早拿不到）
- 返回 `data.klines`，每行逗号分隔：日期,主力净流入,小单,中单,大单,超大单,及各自占比,收盘价,涨跌幅
- 频控严格：连续请求会被限频，需指数退避 + 重建 Session；全市场 5000 只约需 1-2 小时（间隔 1.5s）

## 北向资金历史（datacenter-web，2026-08 实测）

URL: `https://datacenter-web.eastmoney.com/api/data/v1/get`（这个走 https 可以）

```python
params = {
    'sortColumns': 'TRADE_DATE', 'sortTypes': '-1',
    'pageSize': '1000', 'pageNumber': '1',
    'reportName': 'RPT_MUTUAL_DEAL_HISTORY',   # 北向资金，不是 RPT_MUTUAL_STOCK_NORTHSTA！
    'columns': 'ALL', 'source': 'WEB', 'client': 'WEB',
    'filter': '(MUTUAL_TYPE="005")'   # 005=北向, 001=沪股通, 003=深股通, 006=南向, 002=港股通沪, 004=港股通深
}
```

- 返回 JSON（非 JSONP），`result.pages` 分页，`result.data` 列表
- 覆盖 2014-11 至今全历史（约 2729 条），字段：TRADE_DATE / NET_DEAL_AMT（净买入）/ ACCUM_DEAL_AMT / BUY_AMT / SELL_AMT / HOLD_MARKET_CAP
- 注意：2026 年后的最新行部分字段可能为 null（接口数据缺失），分析时 dropna

## go-stock 项目已验证可用性（2026-07-11）

- 新浪 hq.sinajs.cn: ✅ 需 Referer
- 腾讯 qt.gtimg.cn: ✅ 无特殊要求
- 东方财富 push2.eastmoney.com clist: ✅ 需 Referer，total=4433（含北交所）
- 东方财富 push2his K线: ✅ HTTP，不能 HTTPS
- 东方财富 datacenter.eastmoney.com/securities F10: ✅ 需正确 filter=SECUCODE 格式，JSONP
- 东方财富 datacenter-web 龙虎榜/研报: ✅ JSONP，filter用 TradeDate/SecurityCode 大写字段
- 财联社电报 cls.cn/api/cache: ✅ 周末也有数据
- 财联社市场统计 x-quote.cls.cn: ✅ 含涨跌停数
- 天天基金估值 fundgz.1234567.com.cn: ✅ 交易时段有数据
- 东方财富 np-anotice-stock 公告: ✅ 需 Host header，ann_type 必须含 BJA
- 华尔街见闻 wallstreetcn.com: ✅ 可拉全量快讯
- 雪球热股 xueqiu.com: ✅ 需先获取 cookie
- 80.push2.eastmoney.com: ❌ SSL 问题，不要用
- push2ex.eastmoney.com 异动/涨停池: ⚠️ 周末404，交易日正常

## 股票异动接口

URL: `https://push2ex.eastmoney.com/getTopicThs`

参数：`topicType=1`(涨停池)，`Pageindex=1`，`pagesize=50`
⚠️ 周末/非交易日返回404，加重试兼容。交易日 data.pool 含 c(代码)/n(名称)/p(价格)/pc(涨跌幅)/vol(成交量) 等字段。

## 个股公告接口

URL: `https://np-anotice-stock.eastmoney.com/api/security/ann`

必带参数：`ann_type=SHA%2CCYB%2CSZA%2CBJA%2CINV`（必须含BJA北交所）
必带 Header: `Host: np-anotice-stock.eastmoney.com`，`Referer: https://data.eastmoney.com/notices/hsa/5.html`
返回字段：art_code(公告ID)/title(标题)/notice_date(日期)/column_name(公告类型)

## 财联社电报接口

URL: `https://www.cls.cn/api/cache?app=CailianpressWeb&os=web&sv=7.8.5&action=rta&name=telegraph&page=1&rn=20`

无特殊鉴权，可直接请求。返回 data.roll_data，字段含 id/title/content/ctime(发布时间)/is_important

## 财联社市场统计接口

URL: `https://x-quote.cls.cn/quote/index/home?app=CailianpressWeb&os=web&sv=8.4.6`

含涨跌家数、涨停跌停统计、主要指数行情。返回 data.up_down_dis 含 rise_num/fall_num/flat_num/up_10/down_10/suspend_num 等。

## 华尔街见闻快讯

URL: `https://api-one-wscn.awtmt.com/apiv1/content/lives?channel=global-channel&pageSize=20`

无特殊鉴权，直接 GET。返回 data.items，字段含 id/content_text/display_time/is_important。

## 投资日历

URL: POST `https://app.jiuyangongshe.com/jystock-app/api/v1/timeline/list`

body: `{"date":"2026-07","grade":"0"}`
需固定 headers: token=1cc6380a05c652b922b3d85124c85473, platform=3, Cookie=SESSION=...

## 东方财富搜索引擎（选股）

URL: POST `https://np-tjxg-g.eastmoney.com/api/smart-tag/stock/v3/pw/search-code`

⚠️ 需要 cookie 中的 `qgqp_b_id`（东方财富用户标识）。无此值返回错误提示。
板块搜索同理：np-tjxg-b.eastmoney.com
