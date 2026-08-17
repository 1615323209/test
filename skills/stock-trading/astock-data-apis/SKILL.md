---
name: astock-data-apis
description: A股数据接口调用规范 — 新浪/腾讯/东方财富公开行情接口的正确 Header、已知坑和数据说明。适用于任何需要抓取 A股实时行情、K线、板块数据的场景。
triggers:
  - 调用新浪/腾讯/东方财富行情接口
  - 获取 A 股全量股票列表
  - 拉取 K 线数据（实时/历史）
  - 量化回测、历史数据批量采集（15年全量日K等）
  - AKShare 安装/使用/故障排查
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

### 板块资金流（行业/概念）

行业板块：`fs=m:90+t:2+f:!50`，字段含 f62(主力净流入)/f66(超大单流入)/f75(大单流出)等
概念板块：`fs=m:90+t:3+f:!50`
排序：`fid=f62`（按主力净流入排序）
**周末/非交易日概念资金流接口可能返回502**，需加重试逻辑，3次重试间隔1s/2s/3s。

### 个股资金流

URL: `http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={market}.{code}&...`
market: sh→1, sz/bj→0

**完整参数（2026-08 实测有效）**：
```python
r = s.get('http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get', params={
    'lmt': '0', 'klt': '101', 'secid': f'{market}.{code}',
    'fields1': 'f1,f2,f3,f7',
    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
    'ut': 'b2884a393a59ad64002292a3e90d46a5',   # ← 专用 ut，与 clist 的 bd1d9ddb... 不同
    '_': str(int(time.time()*1000))
}, timeout=10)
```
- **必须用 push2his 域名**（push2 同路径只返回当日 1 条），**必须 HTTP**
- `lmt=0` 返回全部可用历史，实测约 120 条（近半年），参数传 5000/1000/100 都只返回 1 条（lmt 不受理，用 0）
- klines 每行逗号分隔：日期,主力净流入,小单,中单,大单,超大单,主力占比,小单占比,中单占比,大单占比,超大单占比,收盘价,涨跌幅
- 高频请求会限频（RemoteDisconnected），指数退避 + 重建 Session，1.5s 间隔

### 北向资金历史（2026-08 攻克）

```python
r = s.get('https://datacenter-web.eastmoney.com/api/data/v1/get', params={
    'reportName': 'RPT_MUTUAL_DEAL_HISTORY',   # ← 不是 RPT_MUTUAL_STOCK_NORTHSTA（那个返回空）
    'columns': 'ALL', 'sortColumns': 'TRADE_DATE', 'sortTypes': '-1',
    'pageSize': '1000', 'pageNumber': '1', 'source': 'WEB', 'client': 'WEB',
    'filter': '(MUTUAL_TYPE="005")'   # 005=北向资金, 001=沪股通, 003=深股通, 006=南向
}, timeout=15)
```
- 返回 `result.data` 数组（直接 r.json()，非 JSONP），分页用 pageNumber，共 3 页可拿全
- 关键字段：NET_DEAL_AMT（净买入）、ACCUM_DEAL_AMT（累计）、BUY_AMT/SELL_AMT、HOLD_MARKET_CAP、INDEX_CLOSE_PRICE
- 覆盖 2014-11 至今（2729 条），适合做回测市场环境过滤
- 2026 年后的行部分字段可能为 NaN（接口数据缺失），join 时用 left join + 容错

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

12. **云服务器 IP 封禁**：腾讯云/阿里云等机房 IP 访问东方财富 API（push2/his/push2ex）会被远端主动断开（`RemoteDisconnected`），TCP 层可通但 HTTP 层被封。诊断：`nc -w 3 push2.eastmoney.com 80` 通 + `curl` 超时/断开 = IP 被封。绕过方案见 `references/eastmoney-session-bypass.md`：**方案A 腾讯源**（纯行情用 `stock_zh_a_hist_tx`/`stock_zh_a_spot_tx`，不限制 IP）；**方案B Session 预热**（资金流/板块等东方财富独有数据，`requests.Session()` 先 GET 首页再调 API，Windows 实测有效）。

13. **AKShare 无法注入预热 Session**：AKShare 内部自管 requests 会话，外部无法注入。调东方财富独有接口（板块资金流、北向资金、个股资金流）必须绕过 AKShare 手写 requests + Session 预热。

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

## AKShare 历史数据批量采集

对于需要全量历史K线（量化回测、因子研究等场景），直接用 AKShare Python 库比逐只调 HTTP 接口高效得多。

### 数据源选择（关键）

AKShare 对 A 股日K提供两个后端，选择策略如下：

| 后端 | 函数 | 列名 | 云服务器 |
|------|------|------|----------|
| **腾讯源（推荐）** | `ak.stock_zh_a_hist_tx(symbol, start_date, end_date, adjust='hfq')` | 英文：date/open/close/high/low/volume/amount | ✅ 不限制 |
| 东方财富源 | `ak.stock_zh_a_hist(symbol, period='daily', start_date, end_date, adjust='qfq')` | 中文：日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅等 | ❌ 封禁云IP |

**股票列表同理**：
- `ak.stock_zh_a_spot_tx()` — 腾讯源，股票代码格式 `sh600519`
- `ak.stock_zh_a_spot_em()` — 东方财富源（云服务器不可用）

**腾讯源差异**：
- 代码传纯数字（如 `'000001'`），不是 `'sh000001'`（AKShare 内部会自动拼前缀）
- 返回列只有 date/open/close/high/low/volume/turnover/amount，**没有涨跌幅、换手率等衍生指标**——这些可以采集后用 pandas 计算
- 进度条走 tqdm（控制台会有一行进度输出，不影响重定向）
- **⚠️ 前复权(qfq)陷阱**：腾讯源的 qfq 对高分红股票（茅台、汾酒、中远海控等 ~200 只）会产生**负数价格**——前复权算法在极端分红送股场景下溢出。量化回测无法使用负数价格。**必须用后复权(hfq)**，hfq 全部为正且回测等价。验证方法：`df[df['收盘'] < 0]` 应为空

**⚠️ AKShare 腾讯源批量采集会挂起（2026-08 实测）**：`ak.stock_zh_a_hist_tx` 内部 requests 无 timeout，全量循环采集（4900 只）时偶发连接挂起 → 整个循环卡死（75 分钟没跑完 4919 只，CPU 还在转）。**全量/增量采集不要用 akshare 循环，改用 requests 直连 + 多线程**（见下）。

### 腾讯源日K requests 直连 + 并行采集（推荐，2026-08 实测 97 秒/4919 只）

akshare 底层用的真实接口（比流传的 `web.ifzq.gtimg.cn/appstock/app/fqkline/get` 可靠——那个老路径返回空 data）：

```python
import requests, json
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_one(code, year=2026):
    prefix = 'sh' if code.startswith('6') else 'sz'
    symbol = f'{prefix}{code}'
    url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    params = {
        "_var": f"kline_dayhfq{year}",
        "param": f"{symbol},day,{year}-01-01,{year+1}-12-31,640,hfq",
        "r": "0.8205512681390605",
    }
    r = requests.get(url, params=params, timeout=10)
    text = r.text
    idx = text.find('={')          # ⚠️ JSONP 响应: kline_dayhfq2026={...}
    if idx < 0: return None
    data = json.loads(text[idx+1:])
    d = data.get('data', {}).get(symbol, {})
    klines = d.get('hfqday') or d.get('day')   # hfq 时 key 是 hfqday
    if not klines: return None
    return pd.DataFrame([{
        '日期': k[0], '开盘': float(k[1]), '收盘': float(k[2]),
        '最高': float(k[3]), '最低': float(k[4]), '成交量': float(k[5]),
        'turnover': float(k[7]) if len(k) > 7 else 0.0,   # 列6跳过
        '成交额': float(k[8]) if len(k) > 8 else 0.0,
        '股票代码': code,
    } for k in klines])

# 10 线程并行：4919 只 × 0.2s ≈ 97 秒（vs akshare 单线程挂起）
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = {ex.submit(fetch_one, c): c for c in codes}
    all_rows = [f.result() for f in as_completed(futs) if f.result() is not None]
```

关键点：
- **JSONP 剥壳**：响应是 `kline_dayhfq{year}={json}` 格式，必须 `text.find('={')` 截断后 json.loads，直接 r.json() 会得到空/错结构
- **必须带 timeout=10**（akshare 没传 timeout 是挂起根因）
- 按年拉（param 里整年日期），一年 640 行上限够用；需要跨年就循环年份或改 param 起止日期
- symbol 带前缀（sh/sz），代码首字符 6 → sh，其他 → sz（北交所 bj 前缀需手动处理）
- 列 6 是杂项跳过，turnover 在列 7、amount 在列 8（与 akshare `iloc[:, [0,1,2,3,4,5,7,8]]` 一致）
- 失败重试建议 1-2 次即可（接口很稳，实测 4919 只 0 失败）

**云服务器全量采集**：脚本模板见 `templates/collect_daily_kline_tx.py`，支持 ST 过滤、北交所排除、上市不足1年过滤、断点续传、Parquet 输出。按 ~4500 只 × 0.3s 间隔 ≈ 22 分钟实际耗时（含网络波动）。

**东方财富源全量采集**（仅适用于家庭宽带/Windows 桌面）：脚本模板见 `templates/collect_daily_kline.py`，字段更丰富但受云IP限制。

详见 `references/akshare-historical-data.md`。

### 数据验证清单

采集完成后必须验证，否则回测结果不可信：

1. **逻辑校验**：`最高 < max(开盘,收盘)` 和 `最低 > min(开盘,收盘)` 均应为 0
2. **负数检查**：`(df['收盘'] < 0).sum()` 必须为 0（qfq有负数风险，hfq无）
3. **复权一致性**：后复权 vs 不复权的日收益率最大偏差应 < 0.1%（实测 0.02~0.04%），验证方法见 `references/factor-library.md`
4. **实盘对比**：抽查茅台/平安/招行等，最新收盘价应匹配腾讯实时行情 ± 当日涨跌
5. **涨跌停合理性**：涨>10.5% 和跌<-10.5% 的次数应在合理范围（科创板/创业板 20% 涨跌停、新股无限制）

### 因子库构建（内存管理）

1257万行全量加载 → OOM（exit 137）。两种方案：

**方案A：Polars 分批（推荐）** — 比 pandas 快 3-5x，内存省 70%。`scan_parquet` 惰性读取，`filter` 谓词下推，每批 500 只 collect → 计算因子 → 增量写入。模板见 `templates/build_factors_pl.py`。

**方案B：Pandas 分批（兼容）** — 每批 50 只 filter 读取，独立 batch 文件，最后 pyarrow 逐文件合并。模板见 `templates/build_factors.py`。

```python
# ❌ 错误：全量加载
df = pd.read_parquet(path)  # 12M行 → 1GB+，OOM

# ✅ 正确：分批 filter 读取
codes = pd.read_parquet(path, columns=['股票代码'])['股票代码'].unique()
for i in range(0, len(codes), 50):
    batch = pd.read_parquet(path, filters=[('股票代码', 'in', codes[i:i+50])])
    # 计算 → 保存 → gc.collect()
    del batch; gc.collect()
```

**低内存服务器（<4GB）前置步骤**：
```bash
# 扩容 swap — 磁盘换内存，防止 OOM
sudo fallocate -l 4G /swapfile2 && sudo chmod 600 /swapfile2
sudo mkswap /swapfile2 && sudo swapon /swapfile2
free -h  # 验证
```

关键点：
- 每批处理后立即 `gc.collect()`
- **不要**一次性加载全量 parquet
- **不要**用 `results` 列表累积全部数据（即使分批，最后 concat 依然 OOM）
- 保存时用 pyarrow `ParquetWriter` 逐文件合并，或读已有 parquet 增量合并
- **增量合并大文件（如每日更新 factor_daily 3.3GB）**：`read_parquet` 全量 + concat + `write_parquet` 必 OOM（实测 exit 137）。必须流式：`pl.concat([pl.scan_parquet(OLD), pl.LazyFrame(new)]).sink_parquet(OLD, compression='zstd')`（sink 流式写，峰值内存低）。⚠️ sink 有两个前置坑（2026-08 实测踩过）：
  - **列顺序必须一致**：new 先 `new.select(old_cols)`，且 old_cols 用**保序列表**（`pl.read_parquet(F, columns=None).columns`）。❌ 用 `set()` 存列名再 `list()` 会随机打乱顺序 → concat/sink 报 schema 不匹配（`DF [...] PROJECT */54 COLUMNS` 无明确错误信息）。校验用 set 做差集，select 用列表保序
  - **日期 dtype 必须一致**：pandas 读出的日期列是 String，旧 parquet 是 Date → sink 同样失败。在因子计算函数开头 `df.with_columns(pl.col('日期').cast(pl.Date))` 强制统一
- **因子重算也须分块**（低内存机）：按股票代码每批 800 只 filter → calc_factors → 只收集需要的行 → del + gc.collect()，312万行全量 calc_factors 在 2GB 机器同样 OOM
- 断点恢复：进度文件记录已完成代码

## 回测

模板 `templates/backtest.py` 实现完整的短线策略回测，基于因子库直接运行。

### 策略规则

- 资金：2万，最多2持，单只1万
- 止损：-5% 全清
- 止盈：+8% 卖50%，+15% 全清
- 时间止损：持仓5日未涨5% → 减仓离场
- 破MA10：减仓50%

### 选股条件（可从 SELECT dict 开关）

- MA5 > MA10
- MACD 金叉（dif > dea）
- 量比 > 1.5
- 20日价格位置 < 0.8（非高位）
- 排除涨停、停牌、当日跌超5%

### 内存策略

按 3 年一批加载因子 parquet（polars scan→filter→collect），每批处理完后 gc，避免一次加载全量 3.3GB 文件。

### 产出

- `backtest_result.parquet`：逐笔交易记录
- `backtest_report.txt`：总收益、胜率、盈亏比、最大回撤、出场原因分布

### 首测诊断（2026-08，184笔交易，结果 -50%）

首次回测即暴露两个结构性问题，调参时**优先改这两处，而不是堆更多选股条件**：

1. **追高**：`vol_ratio>1.5`（放量）+ MACD金叉 + 站上MA10 的组合往往追在短期高点——放量既可能是启动也可能是出货，A股短线追涨胜率天然偏低（首测 43.5%，62% 交易亏损出场）。
2. **止盈回吐**：止盈 +8% 卖半仓后，剩余半仓成本价仍是原买入价，股价从 +8% 回落到成本下方会触发止损，导致"赚半仓亏半仓"，盈亏比被拉低到 0.96（≈1）。修复方向：止盈后剩余仓位设保本止损（移动止损），或止盈直接全清。

### 市场情绪因子（市场级每日，2026-08 新增）

回测需要"市场环境过滤"（沪深300 MA20 + 涨停数>50 + 北向净流入 满足2/3），单靠个股因子不够。从因子库聚合 + 北向资金合并构建 `market_daily.parquet`（每天一行，回测时按日期 join）：

```python
# polars 按日期聚合（scan 惰性，3.3GB 不 OOM）
market = lf.group_by('日期').agg([
    pl.col('limit_up').sum().alias('涨停家数'),
    pl.col('limit_down').sum().alias('跌停家数'),
    (pl.col('ret_1d') > 0).sum().alias('上涨家数'),
    (pl.col('ret_1d') < 0).sum().alias('下跌家数'),
    pl.col('成交额').sum().alias('全市场成交额'),
]).sort('日期').collect()

# left join 北向资金（north_fund_flow.parquet），再算衍生：
# 涨跌家数差、上涨占比、北向净买入5/20日均
```

⚠️ **坑**：`上涨家数 - 下跌家数` 在 polars 里用 UInt32 会**无符号溢出**（-1952 变成 4294965344），必须先 `.cast(pl.Int64)`。

## 数据源选择（2026-08 实测）

北向资金/板块资金流的唯一免费公开源是东方财富 push2，云服务器被封后**全网无免费替代**：

| 源 | 行情 | 北向资金 | 板块资金 | 云服务器 |
|------|------|------|------|------|
| 腾讯 qt.gtimg.cn | ✅ | ❌ | ❌ | ✅ |
| 东方财富 push2 | ✅ | ✅ | ✅ | ❌ 封云IP |
| qstock | ✅ | ✅ | ✅ | ❌ 底层也是东方财富 |
| mootdx（通达信） | ✅ | ❌ | ❌ 有板块分类 | ✅ **独立服务器** |
| Baostock | ✅ K线+财务 | ❌ | ❌ | 未测 |

**mootdx** 特色：走通达信独立服务器，不被东方财富反爬，可获取板块分类（386k 条映射）、分钟线、F10 财务数据。函数：`Quotes.factory(market='std').block()` 获取板块，`.bars()` 获取 K 线，`.finance()` 获取财务。安装：`pip install mootdx`。

**qstock 陷阱**：虽然 CSDN 大量文章推荐用 qstock 获取北向资金，但其底层 `qstock/stock/ths_em_pool.py` 最终走东方财富接口，云服务器上 `import qstock` 即报 `RemoteDisconnected`。

**搜索替代**：Google/GitHub 在国内服务器被墙，CSDN (`so.csdn.net`) 可正常搜索 A 股数据源信息。\n## 参考文件

- references/eastmoney-session-bypass.md — 东方财富反爬绕过：Session 预热模式（先访问首页再调API）
- references/mootdx-guide.md — mootdx（通达信）Python 接口：板块分类、分钟K线、财务数据（独立服务器，不受东方财富反爬影响）
- references/go-stock-api-patterns.md — go-stock 项目中实际验证的接口调用模式
- references/fund-data-apis.md — 基金数据接口（基本信息、净值、估值、持仓、历史净值）完整模式
- references/akshare-historical-data.md — AKShare 安装、历史K线采集最佳实践、数据规模参考、网络限制
- references/factor-library.md — 因子库构建（35因子清单、计算逻辑、验证方法、回测准备）
- templates/collect_daily_kline.py — 全量日K批量采集脚本模板（东方财富源，Windows/家庭宽带用）
- templates/collect_daily_kline_tx.py — 腾讯源版全量日K采集脚本（云服务器可用）
- templates/build_factors_pl.py — 因子库构建脚本（polars版，推荐，更快更省内存）
- templates/build_factors.py — 因子库构建脚本（pandas版，分批filter读取，低内存兼容）
- templates/backtest.py — 短线策略回测引擎（基于因子库，2持/止损5%/止盈8-15%/时间止损）
- scripts/diagnose_cloud_ip.py — 云服务器 IP 封禁诊断脚本（测试东方财富 vs 腾讯连通性）
