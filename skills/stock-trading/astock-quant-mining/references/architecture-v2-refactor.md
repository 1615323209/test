# 架构优化版改造实录（2026-08-17）

四层loop架构细则（L1-L4 + 工程保障）整体替换为架构优化版后，按文档缺陷清单同步代码。
commit 链：f8db954（P0 泄漏+安全）→ 3b7e666（L2 口径）→ 508e31c（L3/L4 口径）。

## 文档来源工作流（用户通过 QQ 发文档）
用户用 Obsidian 链接/QQ 文件直发新文档（缓存于 `profiles/ba/cache/documents/doc_*.md`）。
**用户偏好：直接发文件，不要反复搜索 Obsidian vault/文件位置**（obsidian:// 链接的 vault 名可能与实际不符；用户说"你别搜了，我直接发你了"）。
替换流程：cp 到 note 版（`D:\AI_project\note\03_Agent_bA\00_量化架构\四层loop架构细则\`）
→ cp 到 docs 版（`D:\quant_project\docs\四层loop架构细则\`）→ git commit + push（GFW 重试）。
文档带状态标记约定：✅已实现 / ⚠️文档承诺代码无 / 🔧P0正确性 / 🔧P1口径 / 🔧P2效率。
修复顺序按文档"实施阶段"表：先 P0（泄漏与安全），再 P1（口径），P2 功能级留后。

## P0 泄漏防线实现
- `loop/expr_sandbox.py`：`safe_compile(expr_str) -> (Expr|None, err)`。AST 白名单：
  - 只允许 `Name(pl)`、`Constant`（数字/字符串/bool/None）、白名单内 `Attribute/Call`、`BinOp/UnaryOp`
  - fwd_* 列硬黑名单（label 角色）；幻觉列黑名单（成交量/close/open 等）
  - 时序算子（rolling_*/shift/diff）强制 `.over('股票代码')`；禁 `shift(-n)`（未来函数）
  - `validate_expr` 改为 safe_compile + 未注册列默认拒（NON_PRICE_COLS 仍空集——当前全是量价列）
- `llm_factor_synth.build_dict`：排除全部 `fwd_*`（旧版只排 fwd_5d，fwd_1d/10d/20d 泄漏进数据字典喂 LLM）
- `llm_factor_synth.eval_ic`：ic_data 过滤 `2021-01-01 ~ 2024-12-31`（旧版全量含验证集）
- 验证样例：`pl.col('fwd_1d')` 必须被拦；`pl.col('收盘').rolling_mean(5)`（无 over）必须被拦

## l2_regime 修复（P0，最危险的"假门禁"）
旧实现 `_cand` 从未物化 → `select(["_cand",...])` 恒抛 ColumnNotFound → `except: return True, {}`
→ 任何因子都能过 regime 关，且日志 regime={} 无异常。修复：
1. `d = d.with_columns(expr.alias("_cand"))` 物化
2. **join 加 `suffix="_hs"`**：ic_data 自带股票级 `ma_20`，与 hs300 的 `ma_20` 冲突
   （症状：hs300 close=3814 与股票 ma_20=2527 错位比较，熊态样本 0.07%）
3. 三态：牛 close≥ma_20_hs；熊 close<ma_20_hs 且涨停家数<中位数；震荡其余
4. 判定口径设计段（load_design_df），每态>100 天，三态 IC 方向一致且 |ICIR|≥0.15
5. 异常改为 `return False, {"error": ...}`（假门禁比没有门禁更危险）

## L1 体检重写（P1）
`l1_ic_metrics`（factor_loop_l1l2.py）：
- 口径：设计段 2021-2023（load_design_df），防 L1 反复窥视验证集
- G2：`newey_west_t(ic_series, lag=10) ≥ 3.0`
- G3：次周期同号 / 衰减<50% / **分年符号一致**（year_sign_check，任一年反向且|t|≥2 拒）/
  半年段一致（seg_ok_ratio≥60% + last2_ok）/ **quintile 单调≥0.3**（quintile_mono，从 L2 上移）/
  Rank vs Normal
- 辅助函数：`newey_west_t` / `year_sign_check` / `seg_ok_check` / `quintile_mono`
- 验收：v7 五因子全过（t_NW 4.2~10.1，mono 0.44~0.90）；beam search 0.577 因子新口径通过
  （旧"滚动60日ICIR min"规则下被拒，正是文档 C3 预言的重新判定场景）

## l2_orthogonal 重写（P1，含两个隐蔽 bug）
- **嵌套 over 全 NaN**：`expr.rank().over('日期')`（expr 内部带 over）→ 全 NaN。
  两步修复：`with_columns(expr.alias("_y0")).with_columns(pl.col("_y0").rank().over("日期"))`
- **正规方程口径**：X 中心化后 y 必须也中心化（`y_c = y_m - y_m.mean()`），否则残差不收敛
- **岭回归残差 ∝ y**：alpha 正则让"候选在基准里"时残差 ≈ ε·y，spearman 对缩放不变 → 假阳性。
  修复：残差范数检验 `rss/tss < 1e-3` → "已被池子解释"拒绝（候选=基准因子时 cond 正常但 rss/tss≈0）
- 基准统一 rank 形式（与 L3 注入 `(expr).rank().over('日期')` 一致）
- 残差 IC：残差列 → 逐日横截面 Spearman → newey_west_t ≥ 2.0
- 验证：候选=反转*换手（基准内）→ 拒；候选=MACD（基准内）→ 拒；候选=波动率（新）→ 过

## L3/L4 口径（P1）
- `calc_weights`：`p.get("icir_tradable") or p["icir"]`；`short_lived or half_life_unknown` → 封顶 0.04
- `l3_evaluate`：`N = cumulative_tested + cand.get("n_peek", 1)`；报告加 `seg` 分段
  （design_2021_2023 / holdout_2024 / valid_2025_2026 + note 注明 2024 已被 L1 消费）
- `l4_evaluate`：`short_lived = factor.get("short_lived") or factor.get("half_life_unknown")`
- `run_l4`（factor_mining_loop.py）：数据源优先级 live_trades.csv > live_positions.json，
  不再默认 paper_trades.csv；无成交记录时登记"无真实成交记录"不判定
- `live_positions.py --sell`：第 4 参数卖出价 → record_trade 写 live_trades.csv（pnl 计算）

## 测试数据清理纪律
live_positions.py 的 --add/--sell 会真实写 D:\quant_data 下的 json/csv。验证后必须清理
（--sell all 或 rm live_trades.csv），否则假记录污染 L4 SPRT 数据源。

## G0-G4 分级漏斗（commit 0293923，P2→核心入口）
`loop/factor_loop_gates.py`，入口 `l1_gate_pipeline(cand)`：
- G0 静态（毫秒）：validate_expr（AST 沙箱+fwd 黑名单）+ 已拒绝库比对（expr_hash）
- G1 抽样（秒）：设计段随机 15% 交易日算主周期 |t_NW|，<1.5 直接杀（门槛半值）
- G2 主周期（10s）：全量 |t_NW|≥3.0 + `declared_direction` 声明符号一致
- G3 完整（min）：直接复用 l1_ic_metrics（次周期/衰减/分年/分段/quintile/Rank）
- G4 留出确认：2024 段符号一致（硬性）+ |t_NW_2024| ≥ max(0.3×|t_NW_design|, 1.5)
- 失败写已拒绝库 `loop_state/rejected_factors.json`（expr_hash → reason[:100]），G0 命中即复用拒绝原因（修正常数重跑场景省算力）
- **G4 系数校准**：0.5× 对设计段强因子过度惩罚（v7 s3 t_design=10 需 2024 t≥5，实际 3.13 已很强）→ 校准为 0.3×，v7 回归 4/5 过线（s2 2024 t=1.23 真实走弱被拦，属正常）。**校准依据 = 文档验收原则"老因子应仍过线"**。
- 测试注意：漏斗失败会写已拒绝库，用错误 declared_direction 测过一次后，同 hash 候选后续全被 G0 拦（清理 rm rejected_factors.json）

## role 分流（commit 2c4c53d）
- `l2_pipeline`：`cand.get("role", "score")`；role != "score" 只做 l2_dedup + 档案登记（`archived_only=True`），跳过正交化/regime/半衰期（这些是打分因子概念）
- `run_batch`：archived_only 候选 status="档案"，不入打分池、不触发 L3
- L1 生成端当前无 role 字段（默认 score）；exit/timing 通路等 L3 评估形态就绪

## 涨跌停按板块判定（factors.py，L1 文档缺陷 16）
旧：统一 `ret_1d > ±0.095`（创业板/科创板 20% 错判）。新：
`th_up = when(代码 starts_with('30')|'68').then(0.195).otherwise(0.095)`（留余量防边界）。
**生效条件**：需全量重建 factor_daily.parquet（增量 update_daily 只算新交易日，历史 limit_up/down 仍是旧值）。ST 的 5% 需股票名标记列，当前数据缺失暂用近似。

## Alpha360 泄漏修复（commit 0293923）
首版缺陷（L1 文档第六章第 6 条）：train_alpha360 用 2025-2026（宪法验证集）做 early stopping 与选模，val IC≈0.107 不是干净外推；train IC≈0 / val IC=0.15 的组合即泄漏症状（正常监督学习不可能）。
修复：
- build_alpha360_tensor：三段切分输出 design_x/holdout_x/valid_x（含 meta.json）
- train_alpha360：design 训练 / holdout 做 early stopping / valid 只推理（`pred_design/holdout/valid.csv`）
- `--pit-stats`：z-score 只用训练期(≤2024-12-31)统计，防全样本统计泄漏进验证期特征
- 验证泄漏的快速手段：模型输出 5 分桶 fwd_5d 均值应单调；去掉预测值 top/bottom 10% 后 IC 应基本保持；季度 IC 分段看是否全年稳定（2026Q3 样本仅 25 天，0.378 高 IC 是小样本波动）

## GFW hosts 轮换（本会话 5 次）
github.com 的 hosts IP 当日多次失效（113.3 → 114.3 → 112.3 → 116.3 → 113.4），每次 push 前/失败后要：
1. 批量测：`curl -s --max-time 5 --resolve github.com:443:$ip https://github.com/ -o /dev/null -w "%{http_code}"`（返回 200 才可用）
2. 更新 hosts（python 脚本 replace 三段：github.com/api.github.com/codeload.github.com），备份 hosts.bakN
3. `git push` 重试循环（5 次 × 间隔 8s，GFW 间歇性干扰时重试即可成功）
注意：curl 通（200）不代表 git push 一定能过（SNI 干扰对长连接更狠），push 失败先重试再换 IP。
