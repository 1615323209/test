# 东方财富反爬绕过：Session 预热模式

## 问题

直接对东方财富 API (`push2.eastmoney.com`, `push2his.eastmoney.com` 等) 发 HTTP 请求返回 `RemoteDisconnected` 或 `ConnectionError`——即使 TCP 端口可达、Referer/UA 正确。

## 根因

东方财富反爬引擎对裸 API 请求（无浏览器上下文）主动断连。云服务器 IP 和家庭宽带 IP 均可能触发——**这是接口级反爬，不区分 IP 类型**。

## 解决方案

用 `requests.Session()` 先访问东方财富首页，建立浏览器级别的会话状态，再用同一个 Session 调 API。

```python
import requests

s = requests.Session()
s.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
})

# 步骤1：预热 — 访问首页建立会话
r = s.get('https://www.eastmoney.com/', timeout=10)
# → HTTP 200

# 步骤2：调 API — 同一个 Session 对象
params = {
    'pn': '1', 'pz': '5', 'fid': 'f3',
    'fs': 'm:0+t:6,m:0+t:13',
    'fields': 'f12,f14,f3',
    'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
    '_': str(int(time.time() * 1000))
}
r = s.get('http://push2.eastmoney.com/api/qt/clist/get',
          params=params,
          headers={'Referer': 'https://www.eastmoney.com/'},
          timeout=10)
# → HTTP 200
```

## 频率限制与 Session 重建（2026-08 实测）

预热不是永久有效。连续请求过多后 session 被标记断开：

- **pz=200，间隔 0.3s**：约 37 页后断开
- **pz=50，间隔 0.5s**：约 50 页后断开

断开后**必须重建全新 Session**（刷新同一 Session 无效）：

```python
def push2_with_rebuild(s, params):
    for attempt in range(3):
        try:
            r = s.get(url, params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
        except:
            pass
        # 重建全新Session
        s.close()
        s = requests.Session()
        s.headers.update({'User-Agent': '...', 'Referer': '...'})
        s.get('https://www.eastmoney.com/', timeout=10)
        time.sleep(3)
    return None

# 预防性重建：每20页主动重建
if page % 20 == 0:
    s.close()
    s = requests.Session()
    s.headers.update({...})
    s.get('https://www.eastmoney.com/', timeout=10)
    time.sleep(2)
```

## AKShare 不能直接用

AKShare 内部有独立的 requests 会话管理，无法注入预热过的 Session。`ak.stock_individual_fund_flow()` 同样报 `RemoteDisconnected`。必须绕过 AKShare，手写 requests + Session 预热。

## data.diff 格式陷阱

push2 clist 返回的 `data.diff` **不是 list 而是 dict**（key 为行号）：

```python
# ❌ 错误
items = data['data']['diff']  # dict
for d in items[:5]:  # → KeyError: slice(None, 5, None)

# ✅ 正确
diff = data['data'].get('diff', {})
items = list(diff.values()) if isinstance(diff, dict) else diff
```

## 接口速查

**行业板块资金流**：`fs=m:90+t:2, fid=f62, fields=f2,f3,f12,f14,f62,f66,f184`
**概念板块资金流**：`fs=m:90+t:3`（同上参数）
**北向资金历史**（2026-08 已攻克）：`https://datacenter-web.eastmoney.com/api/data/v1/get` → 关键是 `reportName=RPT_MUTUAL_DEAL_HISTORY`（**不是** `RPT_MUTUAL_STOCK_NORTHSTA`，后者返回空/失败），配合 `filter=(MUTUAL_TYPE="005")` 返回 JSON（非 JSONP，`r.json()` 直接解析，`data.result.data` 是列表）。MUTUAL_TYPE 映射：北向资金=005，沪股通=001，深股通=003，南向资金=006，港股通沪=002，港股通深=004。分页参数 `pageSize=1000&pageNumber=N`，`data.result.pages` 是总页数。实测拿到约 2729 条（**2014-11 ~ 2026-08，完整12年历史**，不是2022起）。字段含 NET_DEAL_AMT(当日净买额)/BUY_AMT/SELL_AMT/ACCUM_DEAL_AMT/HOLD_MARKET_CAP 等。实时分钟可用 `push2 kamt.rtmin`（收盘后多为 `-`）。
**个股资金流历史**：`http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`（⚠️ 是 **push2his** 不是 push2，且必须 HTTP）→ `lmt=0`(全部≈120条半年)+`klt=101`+`secid=市场.代码`+`ut=b2884a393a59ad64002292a3e90d46a5`（**此接口专用 ut，不是 bd1d9ddb**）。klines 逗号分隔：f52=主力净流入/f53=小单/f54=中单/f55=大单/f56=超大单。lmt 参数对 push2 域名无效（只返回1条当天），历史必须 push2his
