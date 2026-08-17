# 基金数据接口完整模式

从 go-stock 项目（fund_data_api.go, 2179行）逆向验证的基金数据接口。

## 1. 基金基本信息爬取

### 优先级1：东方财富 HTML 页面

URL: `http://fund.eastmoney.com/{code}.html` (HTTP)

Python 用 BeautifulSoup 解析：
- 基金名称：`.merchandiseDetail .fundDetail-tit`
- 类型/规模/基金公司/基金经理/评级/跟踪标的：`.infoOfFund table td`
- 阶段收益（近1/3/6/12/36/60月/今年来/成立来）：`.dataOfFund dl > dd`
- 阶段收益也存在于 `#increaseAmount_stage table` 和 `.dataOfFund table` 中

Header 要求：
```
User-Agent: Mozilla/5.0 ...
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
Accept-Language: zh-CN,zh;q=0.9,en;q=0.8
Referer: http://fund.eastmoney.com/
```

### 优先级2：pingzhongdata.js 备用

URL: `http://fund.eastmoney.com/pingzhongdata/{code}.js`

通过正则提取 JS 变量：
- `var fS_name = "(基金名)"` → `re.search(r'var\s+fS_name\s*=\s*"([^"]*)"', text)`
- `var Data_performance = {...};` → 阶段收益在 `sylList` 中
- `var Data_currentFundManager = [...];` → 基金经理信息
- `var Data_fluctuationScale = {...};` → 波动率

Header 要求：Referer 必须指向 `http://fund.eastmoney.com/{code}.html`

## 2. 实时估值（交易时段）

URL: `https://fundgz.1234567.com.cn/js/{code}.js` (HTTPS)

响应格式：`jsonpgz({...});`

Python 解析：
```python
match = re.search(r'jsonpgz\((.+)\)', resp.text)
data = json.loads(match.group(1))
```

关键字段：
- gsz: 实时估算值
- gztime: 估算时间
- gszzl: 估值涨跌幅 %
- dwjz: 最新净值
- jzrq: 净值日期

## 3. 历史净值（分页）

URL: `http://api.fund.eastmoney.com/f10/lsjz`

参数：
- fundCode: 基金代码
- pageIndex: 页码 (1开始)
- pageSize: 每页数量 (最大30)
- startDate: 开始日期 (YYYY-MM-DD，可选)
- endDate: 结束日期 (可选)

header 要求：Referer: `http://fund.eastmoney.com/f10/jjjz_{code}.html`

响应字段（Data.LSJZList[]）：
- JZRQ: 净值日期
- DWJZ: 单位净值
- LJJZ: 累计净值
- JZZZL: 涨跌幅 %

## 4. 基金前十大持仓

URL: `http://fund.eastmoney.com/F10/{code}_1.html`

Python 用 BeautifulSoup 解析：`.tzxq .box tr` 第2行起：
- td[0]: 股票代码
- td[1]: 股票名称
- td[3]: 占净值比
- td[4]: 持仓数量（万股）

## 5. 场内 ETF 基金

场内 ETF（代码 5xxxxx/1xxxxx 等）走不同路径：
- 实时行情用新浪接口（以 fund_ 前缀：`fund_510050`）
- 净值从 API 获取
- K线从东方财富 K线接口获取（secid 使用 fund 前缀）

判断是否为场内基金：代码以 5/1/15/16 开头且长度≥5

## 已知坑

1. 基金 HTML 页面可能返回"抱歉，您查找的基金不存在"或内容过短(<500字节)，需fallback到 pingzhongdata.js
2. pingzhongdata.js 中的基金名称可能不全（如ETF联接没有全名），需从 HTML 补充
3. 实时估值接口非交易时段返回的 gsz=0 或空，gztime 为空字符串
4. 场内 ETF 需要用专门的方法获取报价/净值/K线
5. 基金数据应持久化到 DB，后续查询优先从 DB 读取，减少重复请求
