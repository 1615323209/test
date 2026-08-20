---
name: go-stock-python-migration
description: go-stock 项目 Go/Wails 到 Python/FastAPI 的迁移审计与实施指南。当前 Python 项目是部分后端迁移，须按功能对等性持续验证。
triggers:
  - go-stock python 改造
  - go-stock 迁移
  - python 股票工具开发
---

# go-stock Python 迁移与对等性审计

> Python 项目当前是部分后端迁移，不应以测试数量或路由数量宣称已完成 Go/Wails 产品的完整重写。完整验收必须分别覆盖：数据能力、Agent 语义、持久化、管理 API、前端交互、桌面交付和发布更新。

## 原项目信息

- 路径：D:/AI_project/code/03_Agent_bA/go-stock-dev（Go + Wails + eino）
- 技术栈：Go 1.26 + Wails v2 + eino (CloudWeGo AI框架) + SQLite/GORM + Vue3/NaiveUI

## 技术替换映射（已全部落地）

| Go 原件 | Python 替代 |
|---|---|
| Wails 桌面层 | FastAPI 后端 + 原 Vue3 前端（改 API 调用方式）|
| GORM + SQLite | SQLAlchemy + SQLite（WAL/busy_timeout）|
| eino ReAct Agent | 自写 ReAct + PlanExecute Agent |
| resty HTTP 客户端 | httpx AsyncClient（连接池复用）|
| chromedp 浏览器自动化 | Playwright (Python) |
| cron 定时任务 | APScheduler |
| freecache 内存缓存 | cachetools TTLCache |
| go-ego/gse 分词 | jieba |
| Wails EventsEmit 推送 | FastAPI SSE |

## 已完成的 Python 项目结构

路径：`D:/AI_project/code/03_Agent_bA/go-stock-py/`

```
go-stock-py/
├── main.py                     # uvicorn 入口 (python main.py → :18888)
├── pyproject.toml              # 依赖配置
├── db/
│   ├── database.py             # SQLAlchemy engine + Session + init_db
│   └── models.py               # 30+ ORM 模型（含FundBasic/FollowedFund）
├── core/
│   ├── logger.py               # loguru 双输出
│   ├── http_client.py          # httpx 全局连接池 + UA随机化
│   ├── cache.py                # cachetools TTLCache
│   └── system.py               # APScheduler/预警/钉钉/爬虫/Skill/VIP/更新
├── data/
│   ├── stock_data.py           # 实时行情（新浪+腾讯+东财全量4433支）
│   ├── kline.py                # K线（东财+新浪+pytdx）
│   ├── fund.py                 # 基金信息/估值/净值/持仓（HTML+pingzhongdata+API）
│   ├── fund_flow.py            # 板块/概念资金流
│   ├── market_news.py          # 财联社/华尔街见闻/龙虎榜/投资日历
│   ├── market_statistic.py     # 市场统计+全球股指（财联社接口）
│   ├── stock_changes.py        # 股票异动+历史统计
│   ├── search_stock.py         # 东方财富选股+自定义策略
│   ├── f10.py                  # 财务/融资融券/沪深港通/公告
│   ├── research_report.py      # 研报/互动易
│   ├── macro_group.py          # 宏观/分组/东财AI
│   ├── kline_screenshot.py     # Playwright K线截图
│   └── settings.py             # 设置管理+多AI配置CRUD
├── agent/
│   └── agent.py                # ReAct Agent + 15 tools + 会话记忆
├── api/
│   └── app.py                  # FastAPI 38路由 + SSE流式 + / → /docs 重定向
├── utils/
│   └── utils.py                # GBK解码/交易日判断/Markdown转换
└── tests/
    ├── conftest.py              # 共享 isolated_db fixture (monkeypatch)
    ├── test_phase1.py (14)      ├── test_stock_data.py (13)
    ├── test_kline_funflow.py (8)├── test_market_news.py (5)
    ├── test_search_changes.py(6)├── test_f10_statistic.py (6)
    ├── test_research_macro.py(5)├── test_agent.py (9)
    ├── test_fund.py (5)         └── test_api.py (7)
```

## 回归修复与验证规则

对 Python 迁移项目修复 Agent、定时任务、持久化或管理 API 时：

1. 先写最小离线回归测试，断言原始错误语义，而非只验证“不崩溃”。例如，中文股票名称搜索必须调用关键词搜索服务；流式 Agent 的最终 assistant 文本必须进入保存的会话历史；未实现的数据能力不得注册为 Agent 工具。
2. 测试使用 SQLite 时，测试创建的会话、任务和调度作业必须通过 `try/finally`、fixture 或临时数据库清理，不能污染开发数据库。
3. 改动 FastAPI 生命周期后，除了单元测试，还要用 `TestClient` 上下文实际运行启动和关闭流程，检查恢复逻辑、路由注册、管理 API 的创建/查询/删除及退出清理。
4. 新增模块级 ORM 类型注解、装饰器引用或生命周期依赖时，将 import 放在首次使用之前。Python 在模块导入阶段求值；晚导入会使应用在启动时发生 `NameError`。
5. 上游 HTTP 失败不能被转换成 `{}` 这类成功空响应。返回稳定、可识别的错误结构或对应 HTTP 错误，并在不泄露敏感信息的情况下保留诊断状态。
6. 完成后运行专项回归、全量 `pytest -q` 和 `compileall`。网络依赖测试与离线回归要分开解读，避免将上游波动误判为代码回归。
7. 新增持久化模块时，服务层不要用 `from db.database import get_session` 缓存会话工厂。测试 fixture 会替换 `db.database.get_session` 到隔离 SQLite；应使用 `import db.database as database` 并在每个服务函数内调用 `database.get_session()`，否则全量测试会泄漏到开发库或其他测试状态。
8. 对既有 SQLite 表新增 ORM 字段时，`Base.metadata.create_all()` 不会修改旧表。将幂等 `PRAGMA table_info` + `ALTER TABLE ... ADD COLUMN` 升级放入 `db/database.py::_ensure_sqlite_columns()`，并测试新库建表和旧库升级两条路径。
9. MCP 集成必须使用实际协议客户端进行 `initialize` 与 `list_tools`，再持久化工具 JSON Schema。将动态工具命名为 `mcp_{server_id}_{tool_name}` 防止跨服务同名冲突；未通过工具发现的 Server 不得向 Agent 暴露工具。
10. 对 SSE Agent 结果归档，只在请求提供明确股票上下文且流正常完成时保存聚合文本。中止、错误和泛市场闲聊不得写成某只股票的分析结果。

## 启动方式

```
cd D:/AI_project/code/03_Agent_bA/go-stock-py
pip install -e .          # 安装依赖
python main.py            # 监听 http://0.0.0.0:18888，访问 /docs 查看 Swagger UI
```

根路径 `/` 自动重定向到 `/docs`。注意：**浏览器里输入 `localhost:18888`，不能用 `0.0.0.0:18888`**（0.0.0.0 是监听地址，不是访问地址）。

## Pytest fixture 隔离模式（重要教训）

**不要用 `importlib.reload(db.database)` 做测试隔离。**

正确做法（conftest.py）：
1. 创建临时 SQLite 文件
2. monkeypatch 替换 engine/SessionLocal/get_session
3. 用 ORIGINAL Base 建表（所有模型已注册）
4. 各数据模块的 DB 函数用延迟导入：

```python
def _get_session():
    from db.database import get_session  # 函数内导入，拿到 monkeypatched 版本
    return get_session()
```

避免 reload 因为 reload 会创建新的 Base 类，而 `db.models` 里的 ORM 模型注册在旧 Base 上，导致建表为空。

## Go 版接口 URL 查找方法

改造时需要查找 Go 源码里的真实 URL 的模式：
- 行情/资金流：查找 `push2.eastmoney.com`、`push2his.eastmoney.com`
- F10 数据：查找 `datacenter.eastmoney.com`（注意不是 `datacenter-web`，后者接口不同）
- 龙虎榜：`datacenter-web.eastmoney.com`，需带 JSONP callback
- 市场统计：用财联社 `x-quote.cls.cn`，不用 push2
- 公告：`np-anotice-stock.eastmoney.com`，需 Host header
- 投资日历：`app.jiuyangongshe.com` POST

详见 `astock-data-apis` skill。

## 未适配项（开发者自行扩展）

- Wails 桌面 UI → 改前端 axios/fetch 调用即可
- Go Tushare 对接 → pip install tushare + 补充接口
- 特殊代码前缀 `sb873721` → normalize_code() 补规则
