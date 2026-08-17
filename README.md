# A股量化自动挖掘系统 · 使用手册

> 最后更新：2026-08-17
> 最高准则：`docs/宪法.md`（原则层：架构+运维）
> 实现数值：`docs/四层loop架构细则/`（L1/L2/L3/L4 + 工程保障，全部阈值/公式）
> 配套文档：docs/策略版本日志.md（策略演进+挖掘历史）/ docs/FACTOR_LIBRARY.md（因子数据）/ docs/股票数据资产.md（行情/资金/市场）/ docs/新闻数据资产.md（新闻管线）

---

## 一、系统概览

2 万资金 A 股短线策略（v7 为基线），四层自动 loop 持续挖掘、验证、整合新因子。

```
L4 实盘验证 ← L3 回测评估 ← L2 批次筛选 ← L1 单因子精炼
   (SPRT)      (双集回测)     (去重/正交)     (LLM生成)
        ↑____________事件总线(回滚→补位等)____________|
```

**基线（v7，扣费后净额）**：
- 训练集 2021-2024: +4.80% / 97笔 / 胜率27.8% / 回撤-5.68%
- 验证集 2025-2026: +1.85% / 9笔 / 胜率33.3% / 回撤-2.15%

**因子状态机**：`候选 → 启用 → 实盘确认 / 回滚`

---

## 二、目录结构

```
D:\quant_data\                      # 数据（全部本地化）
├── factor_daily.parquet            # 45因子全库 3.3GB
├── ic_data.parquet                 # IC体检数据 1.4GB（fwd_1d/5d/10d/20d）
├── factor_bt.parquet               # 回测精简 497MB
├── a_stock_daily_hfq.parquet       # 日K后复权 283MB
├── factor_daily_incr.parquet       # 每日增量
├── market_daily / hs300 / north_fund_flow.parquet
└── loop_state\                     # loop 运行状态
    ├── checkpoint.json             # 检查点（原子写 + .bak×3）
    ├── dashboard.json              # 健康度看板
    ├── run.lock                    # 全局锁（TTL 1h + pid 检测）
    ├── events.log                  # 事件总线
    ├── pending_events.json         # 锁占用时事件排队
    ├── l1_log.csv / l2_log.csv     # 各层监控
    ├── backtest_history.csv        # L3 回测历史
    └── data_health.json            # 数据健康扫描

D:\quant_project\code\              # 代码（按四层架构 + 功能分层，仅保留 loop 必需）
├── loop\                           # 四层 loop 核心（宪法 L1-L4）★重点维护
│   ├── factor_mining_loop.py       # ★ 总控入口
│   ├── factor_loop_infra.py        # 工程保障（检查点/锁/事件/健康扫描）
│   ├── factor_loop_l1l2.py         # L1+L2 管线
│   ├── factor_loop_l3l4.py         # L3+L4 + 权重 + dashboard
│   ├── llm_factor_synth.py         # LLM 因子合成（L1 生成引擎）
│   └── run_loop_cron.py            # cron 包装（纯Python）
├── backtest\                       # L3 回测引擎
│   └── backtest_engine.py          # 回测引擎（L3 唯一入口）
├── factors\                        # 因子计算（数据管线）
│   ├── factors.py                  # 45 因子计算（calc_factors）
│   └── extra_factors.py            # 扩展因子（5个）
├── paper\                          # L4 实盘（手动跟踪）
│   ├── daily_picks.py              # v7 打分选股（实盘候选）
│   ├── live_positions.py           # 真实持仓管理（--add/--sell/--status）
│   └── update_daily.py / update_hs300.py  # 每日数据更新
├── data\                           # 数据采集
│   └── tx_collect.py               # 腾讯日K采集（update_daily 依赖）
└── _archive\                       # 历史版本/一次性脚本（归档不删）

D:\quant_project\docs\              # 量化架构文档（入库）
├── 宪法.md                         # 原则层（架构+运维）
├── 四层loop架构细则\               # 数值层（L1-L4 + 工程保障）
├── 策略版本日志.md / FACTOR_LIBRARY.md
└── 股票数据资产.md / 新闻数据资产.md

D:\quant_project\skills\            # 量化技能文档
```

**脚本一览**：

| 脚本 | 用途 | 输出 |
|------|------|------|
| `loop/factor_mining_loop.py` | ★四层 loop 总控 | 因子池/检查点 |
| `loop/llm_factor_synth.py` | ★L1 生成：DeepSeek 因子合成 | llm_factors.csv |
| `loop/factor_loop_l1l2.py` | L1+L2 管线（精炼+筛选） | l1_log/l2_log.csv |
| `loop/factor_loop_l3l4.py` | L3+L4（回测评估+实盘验证） | backtest_history/l4_log.csv |
| `backtest/backtest_engine.py` | ★L3 回测（可注入因子+分年段） | 指标 dict |
| `paper/daily_picks.py` | ★L4 实盘选股（v7 打分） | daily_picks/picks_*.csv |
| `paper/live_positions.py` | L4 真实持仓管理 | live_positions.json |
| `paper/update_daily.py` | 每日数据更新（行情+因子+市场情绪） | factor_daily_incr.parquet |
| `paper/update_hs300.py` | 沪深300 更新 | hs300.parquet |

**L1 生成引擎**（三大方向，见 L1 文档第八章）：`loop/llm_factor_synth.py`（方向一·LLM合成）/ `loop/formula_beam_search.py`（方向三·公式束搜索）/ `loop/build_alpha360_tensor.py` + `loop/train_alpha360.py`（方向二·Alpha360 深度学习）

---

## 三、常用命令

```bash
cd D:\quant_project\code

# 查看因子池状态
python -m loop.factor_mining_loop --status

# 手动跑 1 批（5 个候选，约 5-15 分钟，耗 API）
python -m loop.factor_mining_loop --batch 1

# 快速验证链路（1 候选）
python -m loop.factor_mining_loop --smoke

# 只跑 L4 实盘评估
python -m loop.factor_mining_loop --l4-only

# 查看健康度看板
cat D:\quant_data\loop_state\dashboard.json

# 查看各层日志
cat D:\quant_data\loop_state\l2_log.csv
cat D:\quant_data\loop_state\backtest_history.csv
```

**cron 定时任务**（Hermes 内）：
```
任务名: 量化因子挖掘loop  |  job_id: 6865cd674132
调度:   every 2h          |  入口: run_loop_cron.py
查看:   cronjob action=list
```

---

## 四、四层 pipeline 核心规则（宪法摘要）

### L1 单因子精炼（生成+体检）
- LLM 生成（DeepSeek，seed=batch×1000+idx 保证可复现）
- 列名校验（幻觉列名直接拦截，有黑名单）
- 多周期 IC：fwd_1d/5d/10d/20d，主周期 5d |ICIR|≥0.25 + 次周期同号
- IC 衰减：5d→10d 衰减 <50%；滚动60日ICIR min>0；Rank/Normal 同号（重尾容忍）
- 修正轮 ≤3 轮

### L2 批次筛选（去重+验证）
- 精算层：换手暴露 ≤1.5 + quintile |mono|≥0.3
- 去重：Pearson+Spearman+expr_hash（语义）
- 动态正交化：对 v7 六因子+池内全部因子做岭回归残差（cond>1e6 自动切换），|残差ICIR|≥0.2
- regime 分层：牛/熊/震荡三态 IC 方向一致
- 半衰期 <6月 → 短寿命标记；反因子 ICIR 显著为负（方向真实性）

### L3 回测评估（双集+校正）
- 训练集(2021-24)回测 + 验证集(2025-26)复核
- 动态阈值：提升 ≥ 0.5% + 0.05×ln(N)，N=累计测试数（防 p-hacking）
- 验证集 <20 笔降级：仅"不恶化"检查（≥-2%），L4 预期基准降为 0
- 权重：w_i = 0.05·|ICIR_i|/median(池)，封顶0.10下限0.02；短寿命封顶0.04；总权重≤0.5（迭代剔除）

### L4 实盘验证（SPRT）
- 正式判定：≥20交易日+≥10笔；短寿命缩到 10日+5笔；≥5笔仅预警不回滚
- SPRT：μ0=0（H0无超额）/ μ1=回测预期（H1），ln(LR)≥+2.94 启用，≤-2.94 回滚
- 偏差公式带 2% 绝对值地板；regime 切换 → 观察不立即回滚
- 升实盘：3个月回撤 ≤ max(基线+3%,8%) + 累计收益不为负 + 跑输基线20%人工评审

### 事件总线与锁
- 事件：factor_rolled_back→L3补位 / batch_completed→L3 / regime_changed→L4复核 / data_ready→例行
- run.lock：{pid, start_time, ttl:3600}，pid 已死或超 TTL 视为僵尸锁强制接管
- 单进程消费者，回测严格串行

---

## 五、红线（务必遵守）

1. **不删数据**：回滚=标记废弃，保留全部记录（_archive 同理）
2. **不擅自重跑长任务**：单次全量回测 >10 分钟先说明
3. **列名/PIT 校验**：幻觉列名、非量价列未过 PIT 一律拦截
4. **所有决策留痕**：events.log / 各层 CSV / checkpoint
5. **人工确认点**：池内第 1 个因子启用前需人工确认一次

---

## 六、接入新会话指引

任何 Hermes 会话接手本系统：
1. 读本手册 + `宪法.md`（30 项机制）
2. `python factor_mining_loop.py --status` 看池子现状
3. `cat D:\quant_data\loop_state\dashboard.json` 看健康分（<60 触发全量体检）
4. 用 `--batch 1` 手动触发或等 cron；结果看 `--status` 和 `backtest_history.csv`
5. 若需改阈值/规则 → 改 `宪法.md`（人话为准，改完通知 loop 无感知，规则在下一次判定生效）

---

## 七、当前状态（2026-08-16）

- 5 个 profile 已迁移云服务器，量化全部本地化（服务器已弃用）
- 因子池：2 个候选（oversold_bounce_opt_5d ICIR 0.625 / volume_reversal_5d ICIR 0.253）
- cron 每 2 小时自动跑一批
- 待办：候选攒够 3 个 → 自动触发 L3；第 1 个因子启用需人工确认
