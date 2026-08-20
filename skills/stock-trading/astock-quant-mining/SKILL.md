---
name: astock-quant-mining
description: A股量化自动挖掘系统（D:\quant_project）—— 四层 factor-mining loop、回测、模拟盘。含项目路径、代码分层、运行命令、GitHub 同步与 GFW 访问。
triggers:
  - 量化 因子挖掘
  - 因子池
  - 四层 loop
  - factor_mining_loop
  - backtest_engine
  - quant_project
  - A股 回测
  - 因子回测
---

# A股量化自动挖掘系统（quant_project）

## 项目位置与权威文档
- 代码 `D:\quant_project\code\`（已按四层架构分层）
- 数据 `D:\quant_data\`（factor_daily.parquet 3.3GB / ic_data.parquet 1.4GB / factor_bt.parquet 497MB / a_stock_daily_hfq.parquet 283MB / loop_state\）
- 文档 `D:\quant_project\docs\`：宪法.md（原则层，最高准则）、四层loop架构细则\（L1-L4 全部阈值/公式）、策略版本日志.md、FACTOR_LIBRARY.md、股票/新闻数据资产.md、L1挖掘观测记录.md
- 知识库额外两份（note 目录，不进 quant_project git）：
  - `D:\AI_project\note\03_Agent_bA\Cron任务推送内容逻辑.md` — 7 个 QQ cron 定时任务的推送内容逻辑（触发动作/推送格式/上下文衔接/异常降级/数据流图），维护 cron 或回顾推送逻辑直接查它
  - `D:\AI_project\note\03_Agent_bA\00_量化架构\L1挖掘观测记录.md` — L1 漏斗实测到的市场现象（regime/方向性观测），如"隔夜-日内拉锯 A股符号反转"（LLM 连声明 -1 但实测 IC=+0.053，G2 连拦）；后续挖掘结论继续追加，每个含数据口径+2 待办（核对 gap 定义是否被 hfq 除权跳空污染、尝试 +1 方向）
- 项目本地技能 `D:\quant_project\skills\`（quant-backtest-pipeline / astock-data-apis）
- Python 解释器：Anaconda `D:\02_download\APP\Anaconda\python.exe`
- 2万资金 A 股短线，基线 v7 横截面打分策略

## 四层 loop 架构（宪法）
L1 单因子精炼（LLM生成 + 多周期IC体检）→ L2 批次筛选（去重/正交/regime）→ L3 回测评估（训练2021-24/验证2025-26双集）→ L4 实盘验证（SPRT）。事件总线触发（回滚→补位 / 批完成→回测 / regime变化→复核）。因子状态机：候选→启用→实盘确认/回滚；回滚只标记废弃，不物理删除数据。训练集是 L1/L2 唯一可用区间，验证集仅 L3 复核，防信息泄漏。

## 架构优化版口径（2026-08-17 起，以新 L1-L4/工程保障文档为准）
四层细则文档已整体替换为架构优化版，代码已全面同步（**代码-文档差异已逐项清零**，commit f8db954 起共 10 个 commit 至 b58fc11；五份细则文档头部有「实现状态同步」区块，已修缺陷标 ✅）。核心变更：
- **L1 内部样本切分**：设计段 2021-2023（生成/反馈/判定唯一口径）｜内层留出 2024（仅 G4 一次性确认，embargo 10 交易日）｜验证集 2025-2026（L1/L2 禁止可见）。`load_design_df()`/`load_holdout_df()` 已加。
- **G2 主周期门槛**：`|t_NW| ≥ 3.0`（Newey-West lag=10 修正重叠标签），替代旧 |ICIR|≥0.25（裸 t≈6.75 虚高 √5 倍）。等价严格度：设计段 ~730 日，t_NW = ICIR×√730/√5。
- **分年符号一致**（每年 IC 同号、任一年反向且 |t_year|≥2 → 拒）替代旧"滚动 60 日 ICIR min>0"——旧规则过严（v7 因子都过不了，本质是对平滑度的过拟合）。
- **quintile 单调 ≥0.3 从 L2 上移 L1 G3**（黑箱来源 ≥0.5）；L2 只保留换手暴露 ≤1.5 + 池上下文检查。
- **反因子检验废除**（Spearman 对取负精确反号，数学恒等成立，纯浪费一次全量 IC）。
- **半衰期异常返回 None**（标记 `half_life_unknown`，按 short_lived 保守处理：L3 权重封顶 0.04、L4 观察期 10 日+5 笔），不再乐观兜底 12 月。
- **L3 N = Σn_peek**（修正轮每轮窥视计 1 次，替代按候选个数累加）；权重 ICIR 用可交易域口径 `icir_tradable`；报告分三段披露（2021-2023/2024/2025-2026，2024 注明"已被 L1 消费，非独立 OOS"）。
- **L4 数据源**：run_l4 优先 live_trades.csv（真实成交）> live_positions.json，不再默认 paper_trades.csv。
- **AST 沙箱**（expr_sandbox.py）：所有表达式入口走 `safe_compile`（算子白名单 + fwd_* 硬黑名单 + 时序算子强制 `.over('股票代码')` + 禁负数 shift），取代裸 eval。llm_factor_synth.build_dict 排除全部 fwd_* 列。**二次加固（改造1.0批次A，commit 7484f72）**：列提取与 over 归属全部 AST 化（`safe_compile` 返回 `(expr, err, cols)` 三元组，`validate_expr` 消费 cols 做未注册列检查，不再自跑正则）——关闭正则绕过面（`pl.col('^fwd_5d$')` 正则选择器、多参数 `col('a','b')`、关键字负 `shift(n=-1)`、`.over('日期')` 给时序算子抵账）。注意：横截面 rank 按 `over('日期')` 是合法用法，时序算子才强制 `over('股票代码')`（over 覆盖用分组键集合传播，非全局 bool）。回归测试 `tests/test_expr_sandbox.py` 14 用例。**改造1.0 复核批次 24 项已全部施工完成**（批次 A commit 7484f72/54122cd、批次 B commit 94e5733、批次 C commit e18db51，均 push）——含 AST 二次加固（closure 正则绕过面）+ 3 处运行时崩溃 + 静默失效（min_n/IC外置空转/N累加器/漏斗表/g1 fail-open）+ G2 补可交易性三项 + embargo purge + DL 单独切段 + role 从 LLM 读取。批次 C（改造1.0 21-24）全部落地：C21 `llm_factor_synth.main` 直接调 l1_refine（单一 prompt 来源，删旧自由发明 prompt + eval_ic 路径）；C22 `run_backtest` 加 `return_by_year` 参数返回 `year_ret:{年份:pct}`，`l3_evaluate` 分段披露从 train 分年结果拆（**4 次全量回测→2 次**）；C23 `gate_audit` 三类告警齐备（恒100%通过 / t_nw_design 恒空 / g1_fail_open 兜底触发率>10%，l1_log 新增 `g1_fail_open` 列）；C24 `g2_main` 预算多周期结果缓存进 `main["_res_cache"]`，`g3_full(expr, main=main)` 复用（单候选全量 IC 只算一次）。完整实录见 `references/review-hardening-batchA.md`。执行顺序经验：P0(泄漏+崩溃)→P1(口径)→P2(效率)，每修一条先写会失败的最小复现再改；人工定调改文档服从代码校准值时，批次施工勿顺手把代码改回文档旧值。**`l1_gate_pipeline` 现传 `role`/`label_spec` 且非 score 角色跳过 G2-G4 只过静态门 → l2_pipeline 档案登记分支**（改造4.3，role/label_spec 从 LLM 读取+白名单，非法降级 score）。
- 阈值校准铁律：新口径必须先用 v7 六因子回归验证——老因子在新阈值下应仍过线，否则调阈值不弃口径。
- **改造2.0（loop 闭环化 + 可感知，2026-08-18 起，进行中）**：目标是\"loop 挖到的因子真进实盘 + 每 2 小时真推进一步 + 不看代码知道它卡在哪\"。三处断裂诊断：①闭环断在 L3——`l3_evaluate` 判启用只改 checkpoint 字段，`daily_picks.W`/`backtest_engine.BASE_FACTORS` 硬编码 v7，新因子永远进不了实盘打分；**L4 结构性不可能产出结论**（两个独立根因：`live_positions.py` 成交记录 factor 列恒空串 `""` → l4_evaluate 取不到样本；正常启用路径从不写 `l4_expected` → μ1=μ0=0 → `lnLR≡0` → SPRT 恒\"观察\"）。②吞吐断在两个硬故障（seg_design float 下标 TypeError 致每候选 L3 必崩、启用数恒 0；cron 540s < 单批实际 8-12 分钟）。③感知断在无 run 级产物。**批次1 止血 6 项已完成并 push（commit 6e1be9c）**：seg float 下标、l4_expected 由 L3 写入（正常=单笔预期 `train_ret/n`，降级=0）、live_trades factor 列（`--from-pick` 归因：买入带 picks top_factors/手工 manual，卖出带持仓 factor）、预算驱动、build_alpha360 design_y 残留、L4 量纲统一（rets ×100 为百分比，mu1 直接传 expected）。**批次2 闭环已完成并 push（commit 3a24d54）**：①新增 `paper/active_factors.py`——打分因子**单一真相源**（原子写 temp→fsync→replace + version 单调 + 3 份 .bak + 读回退 v7 + pin 集合 + set_factor/retire 辅助）。daily_picks/backtest_engine BASE_FACTORS/l3 注入三处全部去掉硬编码改读它；**daily_picks 每只入选股输出 `top_factors` 归因**（s_i×weight 排序取前 2 写进 queries csv 新列），`--from-pick` 按该归因写 live_positions/record_trade 的 factor 列，`l4_evaluate` 多因子匹配（`name in factor.split('|')`）+ `shared_attr=1` 记录。②灰度规则：L3 判\"启用\" → 以 `weight=0.5×target + status=灰度` 进 active_factors（新因子不满权重上实盘），L4 实盘确认才升 target，回滚/灰度期满分移入 retired。③`l4_expected` 由 L3 正常启用时写入单笔预期（`train_ret/n_trades`，降级=0）。**批次3 吞吐已完成并 push（commit 330e40f）**：3.1 预算驱动（批次1，`--budget-sec 420` + run_batch 每候选检查预算 exit_reason=budget + run_loop_cron 超时 budget+120 + 推送改读 `push_card.md`）；**3.2 批量生成**（`l1_refine` 加 `n_batch` 参数：LLM 多候选先全过 G0/G1 预筛，只留最有希望的 1-2 个进 G2+，其余丢已拒绝库 `_reject`；`make_prompt` 加 `n_batch` 支持批量要求；system/修正轮 prompt 对应）；**3.3 向量缓存**（新增 `paper/vec_cache.py`：因子设计段逐日 rank 向量按 `expr_hash` 缓存，`get_vec(expr, df)` 内部 safe_compile；l2_dedup/l2_orthogonal 改读缓存纯内存 numpy 逐日相关——**首次算 0.3s、缓存读 0.012s（25×），池内因子秒回，L2 成本与池大小脱钩**）；**3.4 回测缓存**（`l3_evaluate` 加 `_bt_cache` 模块级 dict，键=`hash(sorted(injected 表达式+权重)|年份区间)`，同批多候选共享启用因子注入集命中率高）。**批次1-4 已完成并 push**（6e1be9c/3a24d54/330e40f/d804e39）。**批次4 感知层**：新增 `report/` 模块（`run_reporter.py` 唯一事件流 run_log.jsonl + run_summary + push_card.md；`build_dashboard.py` 自包含单文件 dashboard.html 六视图零 CDN；`daily_report.py` 日报聚合）。`factor_mining_loop.main` 接入 run_start/l2/l3/alert/run_end 事件流 + 整体 try/except；`_make_push_card` 空转 run 折叠一行（死因从 l1_log 聚合）；cron 新增任务8日报(e2d6c59c69b5, 18:05 no_agent) 与 任务9周报(7776ac6429ef, 周五18:15 Agent)；任务6数据更新末尾加 build_ic_data + data_ready 事件。**批次5 干预已完成并 push（commit b862629，改造2.0 全部 5 批次完成）**：新增 `report/control.py`——control.json 人工干预（`paused`/`veto`/`pin`/`max_pool`，黑洞读取，缺失/损坏回退默认带 flag）。`factor_mining_loop.main` 在 run_start 读 control：`paused=true` → 只写心跳卡片（`write_pause_card`）退出 + run_log 留痕 `control:paused=true` + `run_end:paused`；`veto` 处理（expr 型写已拒绝库 `manual_veto`，名字/hash 型只计留痕）；`apply_control` 返回生效动作列表写 `stage=control` 留痕——人工干预必须与自动决策同样可审计。**红线：control.json 不得放松 t_NW 门槛/fwd_* 黑名单/总权重上限**（只能改文档+代码）。验收：置 paused=true → 下一 run 只心跳+留痕，已恢复。\n- **看板访问（改造2.0批次5后）**：dashboard.html 是**本地单文件 HTML 快照**（非实时），位于 `D:\quant_data\loop_state\dashboard.html`，每天日报（18:05 任务8）或手动 `python -m report.build_dashboard` 重新生成。本地访问 `file:///D:/quant_data/loop_state/dashboard.html` 双击即开；局域网可用 `cd /d/quant_data/loop_state && python -m http.server 8080 --bind 0.0.0.0` 后访问 `http://<局域网IP>:8080/dashboard.html`。**要给公网/http 云地址需部署到 zzh 服务器，但最终结论（2026-08-18）：用户明确拒绝 agent 在云服务器执行 sudo 且**明确放弃看板云部署（"别再折腾了，看板这功能就算了"）——不要再尝试云部署 dashboard，本地/局域网即可。**本机 `~/.ssh/id_ed25519` 可无密码 SSH `ubuntu@49.235.150.119`（known_hosts 已有记录），服务器装 Caddy 监听 80、web 根 `/usr/share/caddy`（root 属主 ubuntu 不可写，需 sudo）**——若用户改主意需其自己在服务器跑 sudo 部署，勿擅自动手。
- **L4 SPRT 量纲陷阱（改造2.0缺陷6）**：`l4_evaluate` 里 realized 从 live_trades 读的是**小数**（0.072），`expected`（l4_expected）是**百分数**（1.8），两者直接算 dev 会得 ≈-98% 的假偏差。凡 L4 判 dev/SPRT 必须统一量纲：rets 在入口 `×100` 为百分比，sprt 的 mu1 直接传 expected（不再 /100）。
- **dashboard 实盘验证视图（commit 9022f5b 补）**：dashboard 六视图含"实盘验证(L4)"——`report/build_dashboard._live_validation()` 从 `live_trades.csv` 按 factor 列（\| 分隔归因）聚合每因子成交样本数/实盘均值%/预期%/偏差%（偏差=(实盘-预期)/max(|预期|,2%)），`_live_table()` 渲染；**无成交时显示"暂无归因成交数据"空态而非空白**（live_trades 不存在或全 manual/空归因 → has_data=false）。数据仍是**静态快照**（`-m report.build_dashboard` 重生成时抓取，非实时）。
- **G0-G4 分级漏斗**（factor_loop_gates.py，`l1_gate_pipeline(cand)`）：G0 静态（AST 沙箱+已拒绝库比对，毫秒）→ G1 抽样（设计段 15% 交易日，**3 种子取 \|ICIR\| 中位数 < 0.05 才杀**；计算失败放行 G2）→ G2 主周期（全量 \|t_NW\|≥3.0 + 声明符号一致）→ G3 完整（复用 l1_ic_metrics）→ G4 留出确认（2024 符号一致 + \|t_NW_2024\| ≥ max(0.2×\|t_NW_design\|, 1.5)）。失败自动写已拒绝库 `loop_state/rejected_factors.json`（expr_hash → reason，G0 命中复用）。
- **G1 抽样坑（重要）**：抽样 t_NW 样本量敏感、波动巨大，会误杀强因子（实测 gap 全量 t_NW=9.69，抽样 15% 天 t=-0.64）。必须用样本量无关的 ICIR + 多种子中位数；rolling 类因子抽样下序列不连续算不出 → 放行 G2 判定（G1 是省钱筛子不是判定门）。
- **G4 系数校准链 0.5×→0.3×→0.2×**：0.5× 对设计段强因子过度惩罚（s3 t_design=10 需 2024 t≥5 不合理）；0.3× 仍拦 gap（t_design=9.69 要求 2024 t≥2.91，实际 2.22 已显著 p≈0.03）。留出段回归均值是常态，0.2× 只拦"真塌"（t<1.5 或符号反）。校准方法：v7 六因子 + 新主题因子回归验证，老因子应仍过线。
- **role 分流**（L2 文档第二章）：`role != "score"`（exit/timing）候选只做去重 + 档案登记（`archived_only`），run_batch 标 status="档案"，不进打分池、不触发 L3——L3 的 exit/timing 评估形态未就绪前禁止用 score 口径硬套。
- **涨跌停判定按板块**（factors.py）：创业板(30x)/科创板(68x) 阈值 ±19.5%，其余 ±9.5%（旧版统一 ±9.5% 对 20% 板错判）。注意：只影响**重算后的因子数据**，已存在的 factor_daily.parquet 旧值需全量重建才生效（增量 update_daily 不重算历史）。
- 本版改造的完整实录（每项缺陷的代码位置/症状/修复/验证样例）见 `references/architecture-v2-refactor.md`。
- **代码↔文档对齐工作流（2026-08-17 大量实践）**：当多份权威架构文档（L1-L4/工程保障）被整篇替换，代码要做全量对齐时——①先通读全部新文档，提炼每份的"已修复/待实施"缺陷清单；②按 P0(泄漏/假门禁)→P1(口径)→P2 顺序逐项改代码；③**每个新门槛/阈值必须先用 v7 六因子回归验证**（老因子应仍过线，否则调阈值不弃口径，如 G4 系数 0.5×→0.3×→0.2×、G1 t_NW→ICIR）；④修完 compileall + import + smoke 全链路；⑤在文档头部加「实现状态同步」区块，已修缺陷标 ✅、剩余标 🔧 与代码一致，避免"文档承诺代码没有"。改完 commit 前用 `git status` 确认无残留、push 用「Everything up-to-date」验证远程已同步（连不上≠未同步，GFW 间歇）。**同类复核文档（改造N.M.md，新增🧪实测复现标记）**是"已有实现的复核"而非整篇替换：逐项对，修复后立刻跑回归/复现用例（不要只靠读代码），施工后回写缺陷表状态并加"实测验证"列。详见 `references/review-hardening-batchA.md`。**文档精简偏好（2026-08-18）**：用户要求把文档开头的冗长"实现状态同步/改造N.M实测复核"两大段**收敛为一行「实现概览」**（一句话点出该层核心已落地功能 + 指向文末），**已完成开发项完整总结放到每份文档文末「开发完成项总结」章节**（按类别汇总已完成功能项+对应文件+🔧 待实施标注）。整理时注意：表格行不要被总结插入块夹散（Careful：L4/工程保障 patch 时表格尾行漏到总结下方，需手动移回）。

## 代码分层（2026-08-17 清理后，18 个 .py）
- `loop\\` 四层loop核心 + 因子生成引擎 + 安全层（11）：factor_mining_loop（总控）、factor_loop_infra（检查点/锁/事件/健康）、factor_loop_l1l2、factor_loop_l3l4、llm_factor_synth（L1 LLM生成）、run_loop_cron、formula_beam_search（★方向三）、build_alpha360_tensor + train_alpha360（★方向二）、**expr_sandbox（★AST 安全沙箱，所有表达式入口）**、**factor_loop_gates（★G0-G4 分级漏斗，L1 候选入口）**
- `backtest\`（1）：backtest_engine（L3 唯一回测引擎，被 factor_loop_l3l4 依赖）
- `factors\`（2）：factors、extra_factors（数据管线，update_daily 依赖）
- `paper\\` L4实盘（4）：daily_picks（★因子打分选股）、live_positions（★真实持仓管理）、update_daily、update_hs300
- `report\\`（改造2.0批次4-5新增，4）：run_reporter（★run_log.jsonl 唯一事件流 + push_card）、build_dashboard（★自包含 dashboard.html 六视图）、daily_report（日报聚合）、control（★batch5 control.json 人工干预：paused/veto/pin/max_pool）
- `data\\`（1）：tx_collect（update_daily 依赖的采集）
- `_archive\` 历史/已删脚本备份（本地保留不删，git 历史可恢复）
- 已删除 18 个与 loop 无关文件：mine_factors*、fdr、ic_step1/2、ic_extra、quintile_test、attribution、build_factors_pl、build_extra_factors、collect_hfq、extract_bt_cols、build_market、paper_trading、freq_test、sensitivity、validation、walk_forward_v7——需要时 `git log --all -- <path>` 从历史找回

## 运行命令
```
cd D:\quant_project\code
python -m loop.factor_mining_loop --status
python -m loop.factor_mining_loop --batch 1   # 跑1批（耗 DeepSeek API，约5-15分钟）
python -m loop.factor_mining_loop --smoke
python -m loop.factor_mining_loop --l4-only
```

## 信号机制（用户明确偏好，勿回退 2026-08-18）
用户明确表态：\"**不需要这些规则去选股，我只需要相信我的选股因子**\"——**市场环境评分（涨停家数/上涨家数/北向/板块资金那套 5 分制、或 daily_picks 里的 3 分制）不得成为拦截/过滤选股结果的关卡**。已按**方案3**落地（commit 3b9e5cf）：
- **daily_picks**：去掉\"市场条件满足 X/3 → 观望/可操作\"的决策性结论，改为环境仅作一行备注（`参考：涨停X家/跌停X家，因子选股为主信号`）。因子打分 Top 照常输出，这就是主信号。
- **cron 09:20 盘前扫描**：去掉\"评分≤2 直接结束/观望不选股\"的硬拦——量化因子选股必执行并作为主信号输出，盘面环境只作参考备注。
- **安全阀（唯一保留的环境拦截）**：跌停家数 ≥200 的系统性跌停潮才标\"🚨 极端风险，谨慎追高\"（因子选股仍输出但标注仅供参考）。
- 教训：用户\"只信因子\"的偏好下，环境评分作为硬关卡会把因子选股整个跳过（评分≤2 直接结束 → 因子 Top 永远不推），这会直接导致用户觉得\"系统没让我买\"。**任何涉及\"是否推送/是否买入\"的判断，先确认是不是把环境当成了选股拦截**；正确形态是环境降级为备注、因子选股无条件输出。
- 若后续想恢复环境拦截或改回\"可操作/观望\"结论，先跟用户确认——这已被用户明确否决过一次。

## 选股追踪+分析（2026-08-18，用户"两个都要"要求落地）
用户明确要**既有追踪又要分析**、并**沉淀报告**（不只是推送）。已实现 `paper/track_selection.py` 四维闭环：
- **命中率统计**：选股记录 `daily_picks/selection_log.csv`，回填后续 1/3/5/10 日涨幅，统计命中率(涨>0占比)+均值。
- **因子归因分析**（核心，用户最看重）：按 `top_factors` 分组统计每因子命中率/均值——**判断哪个因子靠谱**（✅均值>+1%且命中≥50% 维持/加权重；⚠️均值<-1% 减权/淘汰；否则中性）。这直接服务"我信因子"——用追踪数据反哺权重决策。
- **失败复盘**：跌幅最大选股列出归因，判断是选错股还是因子失效。
- **盯盘提醒**：涨幅≥+4% 或跌幅≤-3% 标 🚨。
- **沉淀**：每天一份 `D:\AI_project\note\03_Agent_bA\00_量化架构\选股追踪\daily_YYYY-MM-DD.md` + 滚动追加 `累计分析.md`（长期归档因子表现演变）。cron `efde34969f00`（周一至五 15:45，no_agent 包装 `quant_track_cron.py` 先 `--record` 幂等记录今日选股再回填统计）。
- **教训（用户偏好）**：追踪不只是记录数字，**必须分析出"哪个因子靠谱、该调谁权重"**——用户明确否定"只追踪不分析"。任何选股追踪类任务都要带上归因分析维度。
- 注意：selection_log 的 code 列历史文件可能存成 float/丢前导0（如 2366.0 应为 002366），读取要 `str(int(float(raw))).zfill(6)` 归一化。

## 公司名显示 + 新闻情绪因子（2026-08-18，用户偏好落地）
- **用户偏好：报告/选股展示一律用公司名，不要给股票代码**（如"新致软件 (688590)"、盯盘"中国核建 -5.2%🚨"）。代码只作括号备注或内部键。
- **代码→公司名映射**：`data/build_code_name_map.py` 拉取全市场 5544 只存 `D:\\quant_data\\code_name_map.csv`（列 代码/公司）。接口用**新浪** `http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData`（node=`hs_a`，分页 num=500 翻页，返回含 code/name，编码 gbk）。daily_picks 的 `_cname(code)`、track_selection 的 `_name(code)` 都读这个 csv（`schema_overrides={\"代码\": pl.Utf8}` 防代码被当数字丢前导0）。
- **新闻/公告情绪因子（方案1：个股公告关键词情绪）**：新增 `data/news_sentiment.py`——东财 F10 公告接口 `https://np-anotice-stock.eastmoney.com/api/security/ann`（params stock_list=代码, ann_type=A, page_size=8）拉个股近期公告标题；`classify(title)` 用关键词规则分 利好/利空/中性：利好词=业绩预增/中标/回购/增持/合同/订单/分红等，利空词=业绩预减/亏损/减持/诉讼/处罚/立案/质押/退市/问询函/监管函/风险提示等；情绪分 = (利好-利空)/max(总,1) 归一化 -1..1。集成进 `daily_picks`：对 Top5 每只拉公告算情绪，`说法adj_score = score + senti*10` 微调评分并展示 `新闻情绪+0.00中性/🔴利空/🟢利好`。实测：奥精医疗(监管工作函)-1 降权 42.8→32.8，艾迪药业+0.33 加权 42→45.4。
- **东财接口 pick 坑（实测）**：①`push2.eastmoney.com/api/qt/clist/get` 偶发拒连/限流（RemoteDisconnected），新浪更稳；②同接口 fs 参数加北交所 `m:0 t:81 s:2048` 会让接口异常拒连，只保留沪深 4 市场；③单页 pz 拉 5000 被拒，要分页 pz=500 翻页；④东财 search-api 返回的是 `passportWeb`（股吧账号）不是新闻文章，不适合做个股新闻；⑤公告接口可用且含丰富情绪信号（业绩/监管/诉讼），够做情绪因子，资讯/舆情接口不稳定可不用。这些接口踩坑与新`astock-data-apis` skill 相关——如果该 skill 也要维护东财接口规范，注意上述几点。
- **新闻情绪先用公告关键词规则实现**（零 LLM 成本、快、稳定），不建议每只候选调 LLM 判情绪（慢且烧钱）。若用户要更广的资讯/舆情维度再考虑东财资讯或财联社接口。
- **把新闻因子练成入池因子（回测）需历史公告——巨潮资讯网 (cninfo) 是唯一可靠历史源**（2026-08-18 探明+采集器已建 `data/collect_announcements.py`）。完整接口记录见 `astock-data-apis` skill 的 references/cninfo-historical-announcements.md。**历史公告采集已完成**（2026-08-19）：5544 只全量、**243 万条公告**（`D:\quant_data\announcements\{code}.jsonl`，含精确日期+标题+PDF 链接，断点续跑）；207 个限流空文件用 `data/retry_announcements.py` 退避补采成功（0 失败）。**日频新闻情绪因子已构建**：`data/build_news_sentiment.py` 把公告标题按关键词（POS_PAT/NEG_PAT 复用 news_sentiment 词表）打 ±1/0，聚合为 `D:\quant_data\daily_news_sentiment\{code}.parquet`（date/code/ann_cnt/pos_cnt/neg_cnt/sentiment，sentiment=(pos-neg)/max(ann_cnt,1)）。实测信号形态：**稀疏事件型**——50 只样本 7584 日×股中仅 17.1% 非零情绪（正 563/负 735），符合"有利好/利空公告才激活"的预期，不能当连续因子用。**下一步**：把 daily_news_sentiment 并入 ic_data 或作为独立因子接 L1 体检（IC/Newey-West t/分年符号）验证是否真能入池；PIT 对齐要点：公告日期 ≤ 交易日收盘才算 T 日信号。当前形态的 `news_sentiment.py`（东财 F10 近期公告）**不落库、不走 L1-L4 验证，只是选股实时加分**。用户明确"退市股不用搜集，其余全部收集"（code_name_map 本身只含上市股，天然无退市）。

## L4 实盘模式（2026-08-17 变更：模拟盘 → 实盘手动跟踪）
用户手动下单，系统跟踪真实持仓（`D:\quant_data\live_positions.json`）：
- 管理脚本 `python code/paper/live_positions.py --status/--add 代码 成本 金额 [日期]/--sell 代码 [股数|all] [日期] [卖出价]`
- 实盘规则：最多 2 只 / 单票 ≤1万 / 止损 -5% / 止盈 +12%（与用户短线策略一致，非 paper_trading 的 10仓×2000/止损8%）
- **真实成交记录**：`--sell` 带第 4 个参数（卖出价）时自动算 pnl 写入 `D:\quant_data\live_trades.csv`（L4 SPRT 数据源；列：date/code/cost/price/shares/pnl_pct/factor）。不带卖出价则不记录。
- 5 个短线 cron 已整合：09:20 选股含量化 daily_picks 因子 Top3（与盘面候选合并，重合标⭐双信号）；10:30 优先监控真实持仓；15:30 复盘真实成交；15:05 引导用户报告买卖（格式：`买入 600354 成本12.50 金额8000` / `卖出 600354 200股`）
- L4 SPRT 数据源改为真实成交；L4_实盘验证.md 头部有模式变更说明
- 持仓脚本测试注意：--add 会真实写文件，测试后要 --sell all 清掉

## GitHub 同步
仓库 `1615323209/test`（public，main 分支）。classic token 存 `~/.git-credentials`，`git config credential.helper=store`。GFW 环境需 hosts 指向美国 IP；api.github.com 正确 IP 探测、token 脱敏规避、git reset --soft 绕 force-push 审批、hosts IP 动态失效处理详见 `references/github-gfw-access.md`。
- **hosts IP 失效极频繁**（实测一次会话内 3-4 次：113.3→114.3→112.3→116.3→113.4），curl 通 ≠ push 通。push 失败标准流程：批量测候选 IP（`curl -s --max-time 5 --resolve github.com:443:$ip https://github.com/ -o /dev/null -w "%{http_code}"`）→ 更新 hosts（备份 .bakN）→ 重试 push（5 次×间隔 8s）。测 IP 用 140.82.112/113/114/116.x 系列，多数时段有可用。

## 关键 pitfall
- 跨文件 import 用包绝对路径（`from loop.xxx` / `from backtest.xxx` / `from factors.xxx` / `from data.xxx`），不是平铺 import。
- 每个脚本顶部需 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`（指向 code/ 根，否则包 import 找不到）。
- 入口必须 `python -m loop.factor_mining_loop` 运行，不能 `python loop/factor_mining_loop.py`（后者 sys.path[0] 是 loop/，包找不到）。
- build_extra_factors.py / update_daily.py 顶层有执行代码，`import` 即触发真实构建/更新（验证 import 会真的读数据跑一遍）。
- **market_daily 由 update_daily.py 第 5 步自动增量更新**，勿再单独跑 build_market（它只读主文件会覆盖成旧数据；build_market 已删）。若需全量重建 market_daily：从 git 恢复 build_market，且必须用 `pl.scan_parquet([主文件, 增量文件])` 多文件 scan。
- **主/增量 parquet schema 可能不一致**（如 down_streak/up_streak：主文件 Int64 vs 增量 Int32）。`pl.concat([scan1, scan2])` 报 `'union'/'concat' inputs should all have the same schema`；`pl.scan_parquet([文件1, 文件2])` 多文件 scan 也可能报 `SchemaError: data type mismatch`——需显式 `cast_options=pl.ScanCastOptions(integer_cast="upcast")`。读主+增量统一写法：`pl.scan_parquet(files, cast_options=pl.ScanCastOptions(integer_cast="upcast"))`。
- **读主+增量必须去重**：factor_daily 合并会有「同日期+同股票 ×N 行」重复（实测 19662 组），症状是 daily_picks 输出整行重复。所有 `factor_files()` 多文件 scan 入口（daily_picks、build_ic_data）都要 `d.unique(subset=["日期","股票代码"], keep="last")`。
- **ic_data 重建**：`python -m data.build_ic_data`（2026-08-17 起）——从 factor_daily(主+增量) + a_stock_daily_hfq(开盘/最高/最低) + factor_extra_daily(illiq_20/vol_corr_5/vol_corr_20/skew_20/kurt_20) 重建训练集切片，fwd_* 从 ret_*.shift(-n) 同源生成（**不要用收盘重算——会踩复权跳变日假失败**），必须先 `.unique(subset=["日期","股票代码"],keep="last")` 去重主+增量、清理 收盘≤0/非有限 脏行、用 `collect(engine="streaming")` 流式防 1260万行 OOM、备份 rename 前删旧 .bak。完整故障链与修复实现在 `references/ic-data-rebuild-debug.md`。跑完需确认 L1 链路（validate_expr 新列通过、v7 因子仍过漏斗）。
- **polars 嵌套 over 全 NaN**：带 `.over('股票代码')` 的表达式再直接 `.rank().over('日期')`（或再套别的 over）结果全 NaN（如 l2_orthogonal 候选/基准 rank 化）。必须两步：先 `with_columns(expr.alias("_y0"))` 物化，再 `pl.col("_y0").rank().over("日期")`。
- **join 列名冲突**：ic_data 自带股票级 `ma_20` 列，join hs300（也有 `ma_20`）时 `pl.col("ma_20")` 取的是股票 MA20，与指数 close 错位比较 → regime 划分全错（症状：某态样本数异常少）。join 必须 `suffix="_hs"` 并引用 `ma_20_hs`。l2_regime 已修。
- **Newey-West t 经典实现 bug**：`se = sqrt(Var_NW / n)`，Var_NW = γ0 + 2·Σ(1-k/(lag+1))·γk（γk 为归一化自协方差）。漏 `sqrt(n)`（直接用标准差当标准误）会让 t 值小 √n 倍（v7 因子 t_NW 只有 0.17 而非 4.5）。`newey_west_t` 在 factor_loop_l1l2.py，已验证。
- **岭回归残差检验陷阱**：正交化"候选在基准里"时，alpha 正则收缩让残差 ≈ ε·y，spearman 对缩放不变 → 残差 IC 仍显著（假阳性）。修复：a) y 和 X 都中心化（正规方程同口径）；b) 残差范数检验 `rss/tss < 1e-3` → 判"已被池子解释"拒绝；c) 嵌套 rank 用两步物化。验证方法：候选=基准中因子应被拒、新因子应通过。
- **受限 eval/正则类表达式校验**：白名单正则必须含 `\u4e00-\u9fff`（中文列名如 股票代码），否则合法表达式全被拦。
- **`safe_compile` 现返回三元组 `(expr, err, cols)`**（AST 化二次加固后）：所有调用点用 `expr, err, _ = safe_compile(...)`，勿再用两元组解包——`ex, err = safe_compile(x)` 会 `ValueError: too many values to unpack`。改造遗漏处常报这个错（daily_picks 的 `load_active_or_fallback` 与 `_fallback_factors` 都踩过）。同理 `paper.active_factors.safe_expr` 也是三元组。
- **daily_picks 以文件方式直接跑会找不到 `paper` 包**（`from paper.active_factors` / `from loop.expr_sandbox`）：顶部必须有 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`（指向 code/），且 `Path` 必须先于该行 import（`from pathlib import Path` 提前），否则 NameError。
- **active_factors.json 里 v7 基线因子表达式必须与每日打分数据列兼容**：s3 用 `收盘`（非 `close`——后者命中幻觉列名被沙箱拦）、s6 用 `macd_dif`（factor_daily 数据无 `macd_dea` 列）、收盘价列在每日数据是 `收盘`。若把量化回测引擎的列名（close/macd_dea）抄进 active_factors，daily_picks 打分会报 ColumnNotFoundError 或沙箱拒绝。测试/初始化后确认 `D:\quant_data\active_factors.json` 的 factors expr 都是每日数据可用列。
- **active_factors.json 会被测试污染**（set_factor/retire 真实落盘递增 version）：测试后要恢复干净 v7 基线（5 因子 total_weight=1.0、retired 空），否则 version 虚高、多出残留因子影响每日打分。
- **回测引擎与 ic_data 列集不同**：`backtest_engine`/`daily_picks` 用的是 `factor_bt`/`factor_daily` 精简数据（只有 v7 原始列如 ret_5d/turn_ma5/macd_dif 等），而 `illiq_20/skew_20/vol_corr_5` 等扩展列只在 `ic_data` 里。L3 冒烟测试若用这些 ic_data-only 列（如 `-pl.col('illiq_20')`）会 `ColumnNotFoundError`——用回测数据里存在的列（`-pl.col('ret_5d')`）测。同理 daily_picks 的 active_factors 里已注明的列兼容问题。
- **polars 1.43 API 变更坑（2026-08 实测）**：①`Expr.clip(lower=1)` 报 `unexpected keyword argument 'lower'`——新版必须 `clip(lower_bound=1)`；②`d.collect_schema()` 返回 Schema 对象，字段数用 `len(d.collect_schema())` 而非 `.n_fields()`；③`collect(streaming=True)` 已弃用（DeprecationWarning，仍可用），新写法 `collect(engine="streaming")`——**流式 collect 是解决千万行级 OOM 的关键**（build_ic_data 全量 collect 1260万行×66列 OOM，改 lazy + `collect(engine="streaming")` 后物化成功）；④大表统计行数不要用 `d.select(pl.len()).collect()`（会物化全表），把 filter 下推后最后再 collect。
- **f-string 格式化 None 陷阱（2026-08-18 实测）**：`f"{x:.0f if x is not None else '暂无'}"` 是错的——格式说明符 `:.0f` 应用到整个三元表达式，None 分支仍会触发 `TypeError: unsupported format string passed to NoneType`。正确：先算好字符串 `s = f"{x:.0f}" if x is not None else "暂无"` 再插值。
- **迁移遗留：历史脚本曾硬编码 `/home/ubuntu/quant_data`（云服务器路径），已统一改为 `D:/quant_data`。
- 用户说「那个项目/不是这个项目」时先确认是量化（quant_project）还是 EA 外汇（ea profile，`D:\AI_project\note\03_Agent_bA\01_ea架构`）还是 go-stock（`D:\AI_project\code\03_Agent_bA\go-stock-dev`）。量化工作在 lofter/ba profile（宪法适用范围），会话可能分散在不同 profile 的 state.db，跨 profile 查历史需直接 sqlite3 查 `profiles/<p>/state.db` 的 sessions/messages 表。
- 量化 loop cron 已重建：job `2b9525583377`，every 2h，no_agent 脚本 `quant_loop_cron.py`（profiles/ba/scripts/ 包装脚本，subprocess 调 `D:\quant_project\code\loop\run_loop_cron.py`）。README 里旧 job id 6865cd674132（云服务器时期）已失效。
- **向量缓存 `clear()` 回归坑 + 静默坏数据诊断（2026-08-18 实测，重要）**：`vec_cache`（`loop_state/vec_cache/*.npy`，改造2.0批次3加的 L2 向量缓存）会被**测试时的 `clear()`** 一次性清空。清空后 L2 正交化/去重从空缓存拿不到向量 → **所有候选被"残差ICIR不显著(cond=0.0)"误杀**，池永远进不来新因子——但 checkpoint 里看不出异常，因为它表现为"每个候选都被拒"，而池里只剩 2 个历史因子，看起来像\"挖不出因子\"而不是\"基础设施坏了\"。
  - **诊断要点**：当自动挖掘系统**突然每个候选都被拒、且拒绝原因全是退化值**（`cond=0.0`、残差=0、t=0 这类）时，先怀疑基础设施（缓存空/列缺失/数据坏），别当成\"数据没信号\"。查 `ls loop_state/vec_cache/*.npy` 有无文件、`l2_log.csv` 拒绝原因是否清一色同一退化值。
  - **修复+防护（commit e80f411）**：a) `vec_cache._check_vec()`——向量空/全NaN/有效样本过少(<1%)/全零 → **抛错**而非静默返回坏数组；b) `l2_orthogonal`/`l2_dedup` 的 `get_vec` 调用包 try，异常时**明确拒绝并打印原因**（cond=-1 + 信息，不静默 `return False,0,0` 或放行）。验证：正常候选 t=15.49 通过、坏候选（列不存在）正确拒绝。
  - **流程铁律**：任何 `clear()`/删缓存类测试操作后，**必须先重跑主链路（smoke 或 v7 因子漏斗）确认没破坏**再继续，否则测试清理会留下隐性回归。
- **daily_picks 指数当日无数据（盘中）会 None → f-string 崩溃**：hs300.parquet 当日（如 08-18 盘中）还没更新到该交易日时，`hs_close`/`hs_ma20` 为 None。**f-string `{x:.0f if x is not None else '暂无'}` 是错的**——格式说明符 `:.0f` 会应用到整个三元表达式、且对 None 分支先求值时报 `TypeError: unsupported format string passed to NoneType`。正确写法：先算好字符串再插值 `s = f"{x:.0f}" if x is not None else "暂无今日"`。同日在场的「市场条件满足 x/3 观望」是正常判定，非 bug。
- **每日数据更新 cron 不再重建 ic_data（设计决策，勿加回）**：`quant_data_update.py` 的例行动作只有 `update_hs300 → update_daily`（采集全市场约 6 分钟），**已把 `build_ic_data` 从每日例行移除**——ic_data 是训练集切片（2021-2024 固定），只有新增因子列才需要重建（手动 `python -m data.build_ic_data`）。每天全量重建 432 万行×66 列既超时（600s 内跑不完）又 OOM。cron `script_timeout_seconds` 已全局调到 1500s 覆盖日常更新。
- **长任务必须先告知预期时长**：Alpha360 CPU 训练 ~2 小时、beam search 完整版 30-40 分钟——启动后台任务时明确说"约 X 分钟/小时，完成通知"，中途不再静默。用户等长任务时若长时间无反馈会催问（实测：2 小时训练期间用户连问"你怎么了/好了吗"）。后台跑长任务的同时应继续推进其他可并行的工作（如文档、验证脚本），并在完成通知后立即汇报结果。
- 每日数据更新 cron：job `e2bdd373e200`（周一至五 15:40），no_agent 脚本 `quant_data_update.py`（profiles/ba/scripts/），内部顺序 update_hs300.py → update_daily.py（update_daily 已含 market_daily 更新，勿加 build_market 步骤）。手动跑一次约 6-7 分钟（4919 只 10 线程采集），前台 timeout 需 >400s 或后台跑。

## L1 三大前沿方向（2026-08-17 开发进度）
方向一 LLM 合成已落地（llm_factor_synth.py）。方向三公式束搜索（formula_beam_search.py）与方向二 Alpha360（build_alpha360_tensor.py + train_alpha360.py）已开发：
- 方向三：beam search 组合 polars 算子，Reward 已改为 **L1 完整体检优先**（只按整体 |ICIR| 排序会被滚动稳定性检查全拦）；实测发现 vol 波动率类因子 |ICIR| 0.45-0.58（A股低波异象）
- **LLM 自由生成实测**：接入 G0-G4 漏斗后单批 5 个自由生成候选全被拦（G1 抽样拦 2、G2 t_NW 拦 1、G0 沙箱拦 2 个修正轮）——印证文档 8.3"LLM 自由发明表达式先验近乎为零"；漏斗+已拒绝库持续积累"挖不动主题"统计，是 C1（LLM 复现学术 anomaly）与 B4（失败样本驱动生成）改造的输入。
- **C1 升级已落地（commit 008d0fe）**：make_prompt 注入 7 条已发表 anomaly 先验（Amihud illiq_20 / 彩票偏好 skew_20 / 52周高点 / 成交量冲击 / **隔夜-日内拉锯**(开盘/最高/最低) / 低波异象 / 换手率族）+ 反冗余主题配额（pool_topics 池内因子主题，要求欠代表主题出题）。system prompt 改为复现导向（"不要自由发明，先验近乎为零"）+ 时序算子强制 `.over('股票代码')`。候选契约字段 `declared_direction`/`hypothesis` 进漏斗 → **G2 声明符号检查生效**（事前方向 vs 实测，单边检验）。实测效果：LLM 立刻生成隔夜-日内因子但连续声明方向 -1 而实测 IC=+0.053 为正 → G2 连续拦截——这是"当前 A 股 regime 隔夜信号符号与经典文献直觉相反"的市场观测，属挖掘结论非 bug。LLM 因 prompt 要求常复现隔夜/上影线，需留意已拒绝库积累确认是否该主题方向确实失效。
- 方向二：1D-CNN 从 30天×8特征 学 Alpha。**首版初验 IC≈0.11 已作废**（train_alpha360 用 2025-2026 验证集做 early stopping+选模，属验证集泄漏，L1 文档第六章第 6 条点名）。修复后（commit 0293923）：张量三段切分 design(2021-2023)/holdout(2024)/valid(2025-2026)，训练只 design、holdout 做 early stopping，valid 只推理供 L3 复核，预测输出 pred_design/holdout/valid.csv。`--pit-stats` 的 z-score 统计必须截止**设计段（<=2023-12-31）**——第一版误用 TRAIN_HI(2024-12-31) 导致 holdout 特征仍沾自身 mean/std（半泄漏）。**严格 PIT 最终结论（2026-08-17 重训完成）**：holdout(2024) IC=0.1198→**0.0471（ICIR 0.379）**，泄漏实锤——之前 0.12 里约 60% 是特征泄漏（z-score 偷看 holdout 自身统计）。严格版仍有弱正外推（IC≈0.047，统计显著但经济意义有限，对 Top3 选股贡献存疑）→ **方向二按文档降级：不追加投入，等信息轴扩维后再评估**。CPU 训练慢（~3min/epoch @31万样本），用 --sample-every 降采样；Anaconda 下 torch 需 `KMP_DUPLICATE_LIB_OK=TRUE`
- **信息轴 A1/A4（2026-08-17 验证成功）**：重建 ic_data 后（+开盘/最高/最低 + illiq/skew/kurt/vol_corr），A1 日内结构因子与 A4 扩展因子实测——gap 隔夜跳空（t_design=9.69/t_holdout=2.22）、skew_20 彩票偏好（-6.26/-3.61）、vol_corr_5 量价相关（-5.81/-5.38）**通过完整 G0-G4 漏斗**，是 loop 首批非 v7 闭包入池候选；gap20 隔夜动量与 illiq_20（Amihud）2024 年真实走弱被 G4 拦（t=1.44/0.46），属正确拦截。验证脚本参考 `_archive/test_gates_a1.py`（已拒绝库被旧测试污染时 `rm loop_state/rejected_factors.json` 后重测）。
- 关键坑：受限 eval 正则白名单必须含 `\u4e00-\u9fff`（中文列名）；Anaconda 下 torch 需 `KMP_DUPLICATE_LIB_OK=TRUE`；训练必须用 Anaconda python（系统 python 无 torch）
- 详细架构/坑/实测见 `references/factor-generation-directions.md`

## Cron / Hermes 运维经验（2026-08 实测）
- **cron script 参数只接受 `profiles/<name>/scripts/` 下的相对文件名**，绝对路径被拒（"Script path must be relative"）。解法：在 scripts/ 写包装脚本，内部 subprocess 调目标脚本绝对路径。
- **cron 任务模型漂移**：任务创建后若全局模型变更，任务会被跳过执行（`Skipped to prevent unintended spend: global inference config drifted ...`）→ 用 `cronjob action=update job_id=... model={...} provider=...` 显式 pin 模型，避免依赖全局默认。
- **cron 推送失败「用户/群已注销」**：QQ openid 变更导致 deliver 目标失效（报错 400 用户已注销）。修复：`cronjob action=update job_id=... deliver=qqbot:<新openid>`。当前有效 openid 见当前会话 User ID 或 gateway.log。
- **cron no_agent 脚本超时**（2026-08-17 实测）：脚本默认超时 **120s**，且失败报错有误导性——cron 通知显示 "provider timeout. Fallback chain was exhausted"，真实原因在 `profiles/ba/cron/output/<job_id>/` 最新 .md 里（"Script timed out after 120s"）。量化挖掘 loop（LLM 生成 + G0-G4 漏斗）需 5-10 分钟/批 → config.yaml 加 `cron.script_timeout_seconds: 600`（用 Python + ruamel.yaml 改，勿手改），脚本内 subprocess 也带 timeout（留余量 540-550s）。诊断顺序：先看 cron output 真实原因，再测 provider 连通性（`curl http://<host>/v1/models` 返回 401 是正常的——仅未带 key）。
- **⚠️ script_timeout_seconds 是全局的，但容易只给主挖掘 loop 改、漏掉其他 no_agent 脚本**（2026-08-18 实测）：加 build_ic_data 后的每日数据更新脚本（quant_data_update.py，采集全市场 6-7 分钟 + ic_data 重建）仍用默认 120s → **数据更新 cron 静默失败**，因子数据停在旧日期，daily_picks 只能选到旧交易日 → **没有新买入信号**（用户问\"为什么没实盘买卖/没让我买\"的根本原因之一）。改法：`cron.script_timeout_seconds` 一次性调到 600 覆盖所有 no_agent 脚本（不是只给具体 job 配——该配置本就全局）。**诊断\"收益/选股异常\"必须先查数据新鲜度**：`python -c "import polars as pl; print(pl.read_parquet(r'D:/quant_data/factor_daily.parquet', columns=['日期'])['日期'].max(), pl.read_parquet(r'D:/quant_data/factor_daily_incr.parquet', columns=['日期'])['日期'].max())"` 对比今天是几号；数据滞后时先跑 `quant_data_update.py` 补齐再排查别的。**数据更新是自动的（cron 15:40 周一至五）**，手动触发只用于补昨日失败缺口，不是常态操作。
- **cron 挖掘批大小调优**（2026-08-18 实测，改造2.0 已升级为预算驱动）：设了 600s 后单批**默认 5 候选仍会超 540s 子进程预算**（5×LLM 1-2 分钟 + 5×漏斗 2-4 分钟 = 20-30 分钟）。第一版解法是 `--n-cands 2`（每批固定 2 候选）；**改造2.0 3.1 已把固定候选数改为预算驱动**——`factor_mining_loop` 加 `--budget-sec 420`，`run_batch` 每候选检查剩余预算（不足单候选历史 P80 则 stop，写 `exit_reason=budget`），`run_loop_cron.py` 改 `--budget-sec 420` + 子进程超时 `budget+120`。这样单 run 永远正常结束，`killed` 只意味着真故障。**不要再退回固定 `--n-cands 2`**——预算驱动更稳（候选数随预算自适应）。
- **gateway 重启（Windows 计划任务部署）**：从 gateway 会话内部 `hermes gateway restart` 会被阻止（SIGTERM 传播会杀掉命令自身）。解法：`schtasks /Create` 一次性任务 → bat 里用 `ping 127.0.0.1 -n 31` 延迟（`timeout` 在非交互任务会失败，用 ping 代替）→ `taskkill /PID <旧PID> /F` → `schtasks /Run /TN "Hermes_Gateway_<profile>"` 拉起；bat 里 `echo ... >> log.txt` 落盘便于核对。注意 `schtasks /End` 不一定能杀进程，用 taskkill /F 更可靠。
