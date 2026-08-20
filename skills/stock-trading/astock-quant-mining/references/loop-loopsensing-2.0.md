# 改造2.0：loop 闭环化 + 可感知（进行中）

触发：改造1.0（沙箱 AST 化/崩溃修复/口径对齐）落地后的第二轮复核 + 定时任务体系接入。
目标一句话：**让 loop 挖到的因子真进实盘，让 loop 每 2 小时真能推进一步，让不看代码就知道它在干什么、卡在哪。**
批次依赖：批次1 不做完，批次3 吞吐指标测不出（L3 一直崩）；批次2 与 3 可并行。

## 三处断裂诊断（都有代码证据）

### 断裂一：闭环断在 L3 之后——挖到的因子进不了实盘
- `paper/daily_picks.py:29-44`：`W = {'s1':0.25,...}` 与六个 `pl.col(...)` 表达式**硬编码** → 09:20 Top3 永远是 v7 原班六因子。
- `backtest/backtest_engine.py:41-49`：`BASE_FACTORS` 硬编码 → L3 基线永远是 v7，新因子只作 extra_factors 临时注入不沉淀。
- **L4 结构性不可能产出结论（两个独立根因）**：
  - `paper/live_positions.py:90`：成交记录 factor 列恒写 `""`，`l4_evaluate` 靠 `t.get("factor")==name` 取样本 → 任何因子 rets 恒空 → n<min_n → 恒"观察"。
  - `factor_loop_l3l4.py:185`：`expected = factor.get("l4_expected", 0.0)`，但 run_l3 只在**降级启用**写 0.0，正常启用从不写 → μ1=μ0=0 → `lnLR=Σ[(x-0)²-(x-0)²]/2σ²≡0` → SPRT 恒落 A、B 之间 → 恒"观察"。
  - 结论：四层 loop 实际只有三层在跑，**L4 是装饰**。

### 断裂二：吞吐断在两个硬故障——启用数恒为 0
- `factor_loop_l3l4.py:104-112`：C22 把 seg_design/seg_2024 改成了 float，但 report 仍写 `round(seg_design["total_ret_pct"],2)` → **对 float 下标 TypeError**，每候选 L3 必崩，run_l3 无 try，异常冒到 main 释放锁后整个 run 失败 → 不可能有因子被启用。
- `run_loop_cron.py:11,21`：子进程超时 540s，但单批 2 候选自述 8-12 分钟(480-720s) → **配置自相矛盾**，正常就被 kill 在 L3 附近。
- L2 成本随池线性增长：池到 20 个因子时，单候选 L2 做 40+ 次全表运算。

### 断裂三：感知断在没有 run 级产物
- 逐门数据散在 l1_log.csv（每候选一行），无按 run 聚合产物 → 推送要 agent 现场读 CSV 再算，慢且不稳。
- dashboard.json 只有 6 个标量，看不出"这 2 小时发生了什么"。
- run_loop_cron.py `print(stdout[-2000:])` 推的是日志尾巴不是结论。

## 验收指标
| 维度 | 目标 |
|---|---|
| 启用链路 | 新启用因子当日进 active_factors.json，次日 09:20 生效（灰度权重），daily_picks 每只带因子归因 |
| L4 有效性 | 有成交即可判定；factor 归因非空率 100% |
| 吞吐 | ≥40 候选过 G0/G1、≥8 进 G2、≥1 入池/天 |
| 单 run 时长 | 恒 ≤ 预算（默认 420s），exit_reason ∈ {done, budget}，无 killed |
| 感知 | cron 只 `cat push_card.md`，零二次加工 |
| 干预 | control.json 暂停/veto/pin/调预算，操作留痕 |

## 批次1 止血（已完成，commit 6e1be9c，全部 push）
| 缺陷 | 修法 |
|---|---|
| 1 seg_design/seg_2024 float 下标 TypeError | report 直接写 `seg_design` float（勿再 `["total_ret_pct"]`） |
| 2 l4_expected 正常启用从不写 → lnLR≡0 | run_l3 判启用时：降级写 0.0，正常写单笔预期 `train_m["total_ret_pct"]/max(n_trades,1)`；辅助 `train_m_per_cand()` |
| 3 live_trades factor 列恒空 | live_positions `--from-pick` 归因：cmd_add 读当天 picks 的 top_factors（无→留空+提示），手工加仓写 `manual`；cmd_sell 从持仓 `p.get("factor","manual")` 带入 record_trade |
| 4 cron 540s<单批 8-12min | 见 SKILL.md cron 节——预算驱动 `--budget-sec 420`，超时 budget+120 |
| 5 build_alpha360 残留 design_y NameError | 进度打印 `len(design_y)` → `len(dl_train_y)`（改造4.2 变量改名遗留） |
| 6 L4 realized(小数) vs exp_pct(百分数) 量纲 | rets 入口 `×100` 为百分比，mu1 直接传 expected（不再 /100） |

**验证**：python -m pyflakes 清 undefined name；`--smoke` 走完 L3 并在 backtest_history.csv 落一行启用/回滚（实测 t_rev 出"回滚"，seg={'design_2021_2023':2.69,'holdout_2024':-0.92} 正常）。

## 批次2 闭环（已完成，commit 3a24d54，全部 push；实现坑见 SKILL.md 关键pitfall 新增项）
- **2.1 active_factors.json**（`D:\quant_data\active_factors.json`）：打分因子单一真相源，写入方只有 run_l3 权重重算 + L4 回滚；原子写+version 单调+3 份 .bak。读取方三处：daily_picks.compute_score（按 expr+weight 动态构造，文件缺失/校验失败→回退 v7 内置并注明"active_factors 不可用已回退基线"）、backtest_engine.BASE_FACTORS（启动读同一份，`--baseline-only` 复现 v7）、l3_evaluate 注入（直接读文件替代 pool 现场拼）。**每条 expr 进 safe_compile 再消费**（不信任手改文件）。灰度规则：L3 启用→`weight=0.5×weight_target` status=灰度；L4 实盘确认→weight_target 启用；L4 回滚或灰期满 20 交易日无足够样本→retired 权重归零；总权重≤0.5 约束。
- **2.2 归因链 picks→trades→L4**：daily_picks 输出 `top_factors`（score 里贡献最大的 1-2 个因子 `s_i×weight` 头部）写 `daily_picks/picks_{date}.csv` 新列；live_positions --add 支持 --from-pick 带出；l4_evaluate 的 `t.get("factor")==name` 改为 `name in factor.split('|')`，多因子共享样本可接受但 l4_log 记 `shared_attr=1`。
- **2.3 l4_expected 由 L3 写入** → 已随批次1缺陷2完成。

## 批次3 吞吐（已完成，commit 330e40f，全部 push）
- **3.1 预算驱动**（批次1完成）：`--budget-sec 420`；`run_batch` 每候选循环前查 `(time.time()-t_run)+est_per_cand > budget_sec` 则 stop，`est_per_cand` 默认 240（后续由 run_summary 校准），exit_reason 存 `ck["last_exit_reason"]`；`run_loop_cron` 子进程超时 `budget+120`。
- **3.2 批量生成**：`l1_refine(batch_id, factor_idx, api_key, ddict, ..., n_batch=1)`。当 `len(factors)>1` 时，对每个候选跑 `l1_gate_pipeline` 预筛（G0/G1 毫秒+秒），活着的进 `cands_pool`、死的 `_reject(fi_expr, f"{rgate}: {rwhy}")` 丢已拒绝库；`cands_pool.sort(key=lambda x: abs(x[1].get("t_nw_design",0) or 0), reverse=True)` 选最有希望者作主候选，第二个作 `f["_backup"]`。全被 G0/G1 筛掉则记 `best_fail_reason="批量生成候选全部止于 G0/G1"` 并 continue 修正轮。`make_prompt` 加 `n_batch` 参数：>1 时要求一次输出多个方向不同的因子。**注意 n_peek 口径**（文档要求）：G0/G1 不看标签不耗窥视，窥视次数 = 实际进 G2 的候选数。
- **3.3 因子向量缓存**（新增 `paper/vec_cache.py`）：`get_vec(expr, df)` 内部先 `safe_compile(expr)`（**表达式可能是字符串，须先编译再 `.rank().over("日期")`，否则 `AttributeError: 'str' object has no attribute 'rank'`**），返回设计段全样本浮点32 rank 向量存 `loop_state/vec_cache/{expr_hash}.npy`，超配额按 mtime LRU 淘汰。`l2_dedup` 数值去重改：候选+池内因子向量都从缓存拿，pandas DataFrame(`{"日期","_c"}`+`_p`) 按 `groupby("日期").apply(corr)` 算逐日 pearson/spearman，MI 通道改 `sample(n=min(50000,len), random_state=7)`。`l2_orthogonal` 的 y 与基准 X 都改 `get_vec` 拿。**实测提速：首次 0.3s → 缓存读 0.012s（25×），池内因子秒回**。
  - **⚠️ 防护 + clear() 回归（commit e80f411，2026-08-18 实测）**：`vec_cache` 被测试 `clear()` 清空后，L2 从空缓存拿不到向量 → **所有候选被"残差ICIR不显著(cond=0.0)"误杀**，且 checkpoint 无异常（池只剩 2 个历史因子），看起来像"挖不出因子"而非"缓存坏了"。**诊断**：`l2_log.csv` 拒绝原因清一色退化值(`cond=0.0`)+`ls vec_cache/*.npy` 空 → 基础设施问题不是数据问题。**防护**：`get_vec` 内加 `_check_vec()`（空/全NaN/有效样本<1%/全零 → 抛 ValueError，不再静默返回坏数组）；`l2_orthogonal`/`l2_dedup` 的 `get_vec` 调用包 try，异常时明确拒绝并打印原因（cond=-1+信息），`l2_dedup` 拒绝带 `f"候选向量异常: ..."` 原因（不再静默 `False,0,0` 或 `pass` 放行）。**流程铁律**：任何 clear()/删缓存测试后必须先重跑主链路（smoke 或 v7 漏斗）确认没破坏再继续。
- **3.4 回测缓存**（`factor_loop_l3l4.py`）：`_bt_cache` 模块级 dict + `_cached_backtest(injected, start_year, end_year, return_by_year)`，键=`sha1(sorted(f"{name}:{expr}:{weight:.5f}")|start|end|"1.0")[:24]`（回测引擎版本号并入键尾部，引擎改版缓存作废）。`l3_evaluate` 加 `use_bt_cache=True` 开关（False 走直接 run_backtest）。同批候选共享已启用因子注入集 → 命中率高。
- **实测坑**：L3 冒烟用不存在的因子列（如 `illiq_20`）会 ColumnNotFoundError——回测引擎用的是 `factor_bt` 精简数据，不是 ic_data（illiq_20/skew/vol_corr 只在 ic_data）；用回测数据里存在的列（`-pl.col('ret_5d')`）测。

## 批次4 感知（已完成，commit d804e39，全部 push）
- **新增 `report/` 模块**（`report/__init__.py` 空文件 + 三个脚本）：
  - `report/run_reporter.py`：「唯一事件流」run_log.jsonl（append-only，`log_event(stage, run_id, **fields)` 写 `loop_state/run_log.jsonl`）+ `new_run_id()`（`rYYYYMMDD_HHMM`）+ `write_summary()`（`loop_state/runs/run_{id}.json`）+ `write_push_card()`（`loop_state/runs/push_card.md`）。stage ∈ {run_start,gen,gate,l2,pool_add,l3,l4,alert,run_end}。前端与推送只消费它，不再解析 4 份 CSV（CSV 只留人肉查历史）。
  - `report/build_dashboard.py`：`build_dashboard_html()` 生成**自包含单文件** `loop_state/dashboard.html`——内联 json 数据 + SVG 手绘漏斗矩形 + polyline 趋势，**零 CDN/零框架/双击即开/可当附件推送**。六视图：今日漏斗(死因分布)/因子池/实盘因子(active_factors)/Run时间线/健康分标量。严格只读（数字可溯源 run_log.jsonl，不做业务计算）。移动端单列布局。
  - `report/daily_report.py`：日报聚合（`build_daily()` 读 run_log 当日 run_end/l2/l3/alert 事件 + l1_log 死因分布 + active_factors version），写 `daily_card.md` 并重生成 dashboard.html。
- **4.1 run_log 事件流接入 main**：`factor_mining_loop.main` 在 lock 后 `log_event("run_start", ...)`，批完成 added>0 记 l2、启用 n>0 记 l3、数据健康失败记 alert、finally 前 try 记 exception、末尾记 run_end（exit_reason/duration/pool/enabled/health/alerts）。**main 加了整体 try/except**（原来异常直接冒到释放锁，现在 except 记 alert 再 finally 释放锁）。
- **4.2 push_card 生成**：`_make_push_card(run_id, ck, dash, exit_reason, alerts, duration_sec=0)`——**空转 run（无新增池/无启用/无告警）折叠成一行**（感知不疲劳）；非空转含标题/漏斗摘要(Gx拒N)/启用/池状态(启用/灰度/观察/档案)/健康分/告警/详情链接。**死因与 gen_n 从 l1_log.csv 聚合**（`gate != 'g0-g4' and != '?'` 计入对应 g 门；run_log 无逐候选 gate 行，故读 CSV）。注意 `_make_push_card` 不能依赖 `dash['duration_sec']`（dashboard.json 无此键）——duration 作为参数传入。
- **4.3 三层推送 cron**：
  - **任务8 日报**（每日 18:05 no_agent）`e2d6c59c69b5`：script `quant_daily_report.py`（subprocess 调 `python -m report.daily_report`，cat `daily_card.md`）。
  - **任务9 周报**（周五 18:15 Agent）`7776ac6429ef`：prompt 驱动——读 run_log/dashboard/checkpoint/active_factors/l4_log，分析池演化/门禁健康/瓶颈判断，给建议动作（如切信息轴 A1/A4）。与短线周回顾(任务5, 15:45)并列不混。
  - 任务7 已改 `--budget-sec 420` + 读 push_card（批次1随 run_loop_cron）。
- **4.4 cron 数据侧改造**：`quant_data_update.py` 末尾追加 build_ic_data 步骤（否则新因子列进不了 ic_data，L1 视野停旧 schema）+ 发 `data_ready` 事件（`loop_state/events.jsonl`）。
- **验证**：`run_log.jsonl` 有 run_start/run_end 事件流；`push_card.md` 生成含标题/池状态/告警/详情；`dashboard.html` 六视图关键块齐全（grep 今日漏斗/因子池/实盘因子/Run时间线/健康分）；`daily_report.py` 输出日报卡。**注意 smoke 在 l1_log 无过 G2 候选时 gate_audit 会报"L1 t_nw_design 恒空"**——这是历史失败候选占多数触发的门禁保护告警，正常（t_nw_design 只在过 G2 的候选才有）。

## 批次5 干预（已完成，commit b862629，改造2.0 全部 5 批次完成）
- **新增 `report/control.py`**：`load()` 黑洞读取 control.json（缺失/损坏回退默认带 status flag）；`apply_control()` 返回 ctl + 生效动作列表；`veto_once()` 把命中项写已拒绝库（manual_veto）；`write_pause_card(note)` 写心跳卡片。
- **control.json**（`loop_state/control.json`）：`{paused, budget_sec, veto, pin, max_pool, note}`。paused→loop 只写心跳卡片后退出；veto→expr 型写已拒绝库 `manual_veto`（名字/hash 型无法直接写 expr_key 的拒绝库，只计留痕）；pin→不参与 calc_weights 迭代剔除（active_factors 用 status="pin"）；**不得放松红线类阈值**（t_NW 门槛/fwd_* 黑名单/总权重上限——只能改文档+代码审核）。
- **main 接入**：run_start 后 `apply_control()`，有动作先 `log_event("control", run_id, actions=...)` 留痕；`paused` → `write_pause_card()` + `log_event("run_end", exit_reason="paused", control=True)` 后 return；再 `veto_once()`。
- **验收（实测通过）**：置 `{"paused": true, "note":"测试暂停"}` → 下一 run 只输心跳卡（push_card.md="⏸️ loop 已暂停..."），run_log 完整留痕三行：`run_start` → `control: actions=["paused=true（测试暂停一轮）"]` → `run_end: exit_reason="paused", control=true`。测试后必须恢复 `paused:false` 及完整字段。
- **看板访问**：dashboard.html 是本地单文件快照（非实时），`file:///D:/quant_data/loop_state/dashboard.html` 双击即开；局域网 `python -m http.server 8080 --bind 0.0.0.0` 后 `http://<IP>:8080/dashboard.html`。公网/http 云地址需部署 zzh 服务器但无 SSH/上传凭据时勿擅自假设，先确认访问方式。
