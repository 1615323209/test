---
name: a-share-quant-factor-mining
description: A股量化因子自动挖掘系统（四层loop架构）。2万资金v7基线策略，L1单因子精炼/L2批次筛选/L3回测评估/L4实盘验证+工程保障。触发词：量化、因子挖掘、quant、factor mining、四层loop、宪法、loop状态。
---

# A股量化因子自动挖掘（四层 loop）

> 这是用户本地 A 股量化项目，跟 go-stock（AI 股票分析工具）是两码事。用户说「量化项目 / 因子 / loop / 我们的代码」时指这里，**不是 go-stock**。

## 关键路径

- 代码：`D:\quant_project\code\`（入口 `factor_mining_loop.py`，cron 包装 `run_loop_cron.py`）
- 数据：`D:\quant_data\`（factor_daily 3.3GB / ic_data 1.4GB / factor_bt 497MB / a_stock_daily_hfq 283MB）
- 状态/检查点：`D:\quant_data\loop_state\`（checkpoint.json / dashboard.json / run.lock / events.log / l1_log.csv / l2_log.csv / backtest_history.csv）
- 项目技能：`D:\quant_project\skills\`
- 最高准则（原则层）：`D:\AI_project\note\03_Agent_bA\00_量化架构\宪法.md`
- 实现数值（阈值/公式唯一来源）：`D:\AI_project\note\03_Agent_bA\00_量化架构\四层loop架构细则\`（L1-L4 + 工程保障 5 个文件）
- 使用手册：`D:\quant_project\README.md`

## 四层 loop 架构

L1 单因子精炼（LLM 生成 + 多周期 IC 体检）→ L2 批次筛选（去重/正交/regime/半衰期）→ L3 回测评估（训练集 2021-24 + 验证集 2025-26 双集，动态阈值防 p-hacking）→ L4 实盘验证（SPRT 序贯检验）。

- 因子状态机：`候选 → 启用 → 实盘确认 / 回滚`
- 事件总线驱动（回滚→补位 / 批完成→回测 / regime变化→复核 / 数据就绪→例行），不靠轮询
- 上层只对下层交付做决策，下层不感知上层；L1/L2 不可见验证集数据（防泄漏）

## 常用命令

```bash
cd D:\quant_project\code
python factor_mining_loop.py --status    # 看因子池现状
python factor_mining_loop.py --batch 1   # 手动跑1批（5候选，约5-15min，耗 DeepSeek API）
python factor_mining_loop.py --smoke     # 1候选快速验证链路
python factor_mining_loop.py --l4-only   # 只跑 L4 实盘评估
cat D:\quant_data\loop_state\dashboard.json   # 健康分（<60 触发全量体检）
```

cron：Hermes job「量化因子挖掘loop」（每 2h，入口 `run_loop_cron.py`）。

## 红线（务必遵守）

- 不删数据：回滚=标记废弃保留全部记录，`_archive` 同理
- 不擅自重跑长任务：单次全量回测 >10 分钟先说明
- LLM 生成的因子表达式先过列名校验（黑名单）；非量价列未经 PIT 检查不得进池（防前视偏差）
- 所有自动决策留痕（events.log / 各层 CSV / checkpoint）
- 池内第 1 个因子启用前需人工确认一次，之后按规则自动

## 已知结论与坑

- 基线 v7 横截面打分（每日 6 因子 rank 加权 Top3），扣费后净额：训练集 +4.80% / 验证集 +1.85%（同期沪深300 -10.9%）；v8 regime 降仓失败
- 成本（37% 本金/年）是小资金最大杀手，须降频
- A股反转市场：turn_ma5 -0.09 最强，limit_up +0.042
- 2万/10仓买不起 >20 元股（需价格过滤 <19.5）
- DeepSeek 官方 API：`base_url=https://api.deepseek.com`（无 /v1），`reasoning_effort=high`（见 astock-data-apis skill 的接口规范）
- 数据全量加载 1257 万行会 OOM（已弃用云服务器，全部本地 16GB）；回测用 factor_bt 精简版，增量更新用多文件 scan 不动大文件
- polars：mean() 遇 NaN 需 fill_nan().drop_nulls()；concat 要求 schema 保序且 dtype 一致

## 当前状态（2026-08-17）

- 因子池：2 个候选（oversold_bounce_opt_5d ICIR 0.625 / volume_reversal_5d ICIR 0.253），攒够 3 个自动触发 L3
- **代码已按四层架构分层 + git 化**（2026-08-17 完成）：`code/` 分 loop/backtest/factors/paper/data 五个子目录，清理 18 个与 loop 无关的历史脚本；仓库 `github.com/1615323209/test` 已同步。L4 已从模拟盘切换为实盘手动跟踪（live_positions.py）。cron 体系（量化loop every2h + 每日数据更新 15:40）已重建。
- ⚠️ 本 skill 与 `astock-quant-mining`（stock-trading 分类）内容重叠，后者为更新维护中的版本，结构/命令/pitfall 以 astock-quant-mining 为准。

## 改动规范

改阈值/规则 → 改 `宪法.md`（人话为准），loop 无感知、下一轮判定生效。数值冲突时以 `四层loop架构细则/` 为准，原则冲突以 `宪法.md` 为准。
