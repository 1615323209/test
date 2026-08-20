# 改造1.0 代码复核批次A实录（2026-08-18）

外部人工复核文档「改造1.0.md」对第一轮架构同步后的代码做了实测复核，发现 24 项遗留问题，
含 5 项"代码写了但从未生效"（靠读代码复核发现的成本远高于一条断言）。分四类：
P0 沙箱可绕过 / P0 运行时崩溃 / P1 静默失效与口径半改 / P1 role打通 / P2 效率。
本文件记录批次 A（P0：泄漏+崩溃）的修复，及批次 B 尚未施工的清单。

## 工作流：外部复核文档驱动
- 用户会定期发人工写的「改造N.M.md」复核文档（状态标记沿用 ✅/⚠️/🔧P0-P2，**新增 🧪=已实测复现**）。
- 与架构 v2 不同：这是已有实现的"复核"，不是整篇替换。逐项对，施工后可回写文档缺陷表状态，
  但**文档新增了"实测验证"列**——只有跑过用例的才标 ✅（本批有 5 项是"代码写了但从未生效"）。
- 施工顺序：批次 A（P0 泄漏+崩溃）先做一次提交 → 批次 B（P1 口径）→ 批次 C（P2 效率）。
- **修复后立刻跑回归用例/崩溃复现，不要只靠读代码**。

## P0-1.3：expr_sandbox AST 二次加固（关闭正则绕过面）
第一版 `safe_compile` 用 AST 做算子白名单，但**列提取与 over 归属仍是正则**：
`safe_compile` 里 `re.findall(r"pl\.col\(['\"]([^'\"]+)['\"]\)", expr_str)` 与 `validate_expr` 各跑一遍。
polars 把首尾 `^...$` 的字符串当正则列选择器 → `pl.col('^fwd_5d$')` 绕过黑名单直取 fwd_5d；
多参数 `pl.col('a','b')` 因正则要求引号后紧跟 `)` 完全不匹配 → 零校验。

**正确的 AST 设计（关键）**：
1. `pl.col` 只收字符串字面量：`_col_args(call)` 遍历 args，非 `ast.Constant str` 拒，含任一正则元字符
   `REGEX_META = set("^$*?[]|+\\")` 拒（顺带挡 `pl.col(变量)` 动态构造），禁关键字参数。
   同时在这里做 fwd/黑名单检查（列校验与 AST 同源）。
2. **over 覆盖传播不能用全局 bool**——要用**分组键集合**：`covered` 是"当前最内层 over 的分组键集合"。
   `over` 分支算 `inner_covered = keys if (GROUP_KEY in keys) else covered` 向下传；
   时序算子校验 `not (isinstance(covered, set) and GROUP_KEY in covered)` 才拦。
   这样 `.rank().over('日期')`（横截面 rank，非时序）合法，而 `rolling_mean(5).over('日期')` 被拦——
   旧版用全局 `has_over` bool 会让 `.over('日期')` 给时序算子"抵账"（缺陷 3）。
3. **负 shift 查 AST 而非正则**：`shift(-5)`（positional）与 `shift(n=-1)`（keyword）都要拦，见 `_has_negative_arg`。
4. `safe_compile` 返回三元组 `(expr, err, cols)`；`validate_expr` 消费 `cols` 做未注册列检查，
   **不再自跑正则**（两道检查看到同一组列名，消除绕过面）。调用点解包要同步改 3 元。

**回归测试** `tests/test_expr_sandbox.py`（14 用例，`python tests/test_expr_sandbox.py` 直接可跑）：
5 条绕过 + 3 条正常全固化成用例（正则选择器/多参数/日期 over/关键字负shift/roll在over外 应 REJECT；
正常列/时序+over股票/rank+over日期/v7因子 应 PASS）。这是唯一防回退手段，跟修复一起提交。

## P0-2.x：运行时崩溃（改前 L3/L4 从未被真正跑通过）
- **2.1 calc_weights KeyError**：池内因子的 ICIR 在 `p["ic_metrics"]["icir"]`，顶层没有 `p["icir"]`
  → `p.get("icir_tradable") or p["icir"]` 短路失效时 KeyError，run_l3 整个崩。
  修：统一取数辅助 `_icir(p) = p.get("icir_tradable") or (p.get("ic_metrics") or {}).get("icir") or 0.0`；
  tradable 为空记 `p["icir_fallback"]="full_domain"`（口径降级要可见，不静默）。
- **2.2 update_dashboard TypeError**：`p.get("half_life", 12)` 键存在但值为 None（L2 现在故意写 half_life=None）
  → 对 None 求均值崩。修：`hls=[h for p if (h:=p.get("half_life")) is not None]`，空时 avg_hl=None、
  健康分半衰期项 0 分，dashboard 加 `half_life_coverage`。同处 `p.get("icir",0)` 也要走 `_icir`。
- **2.3 l4_evaluate 字符串算术**：`csv.DictReader` 的 `pnl_pct` 是字符串，`np.std/np.mean` 崩。
  修：入口 `float()` 清洗 + 记 `bad_rows`；sigma 优先级明确（不要靠 Python 运算符优先级 `sigma_prior or x if...` 碰巧成立）。
- **2.4 两个未定义名被裸 except 吞**：`hashlib`/`date` 都没 import → 内容寻址缓存从未写一个字节、
  `eval_ic` 恒返回 None。这两处 P0 都是被 `except Exception: pass` 藏住的。修：补 import + 裸 except 收敛
  （改为 `except Exception as e: print(...)`）+ 缓存 `response` 改全文（截断无法重放）。

## 静默失效类（批次 B 已施工，本文件记录教训）
- **3.1 l4 min_n 算完未用**：`min_n` 算好但判定写死 `if n<5`。修：`if n < min_n: return "观察"`，min_n 写进报告。
- **3.2 IC 序列外置空转**：漏斗构造 l1_metrics 时已 `if k != "_ic_series"` 过滤掉，主控再 pop 永远 None
  → ic_series/*.parquet 一个都不生成。职责应归漏斗（落盘后只放 ic_series_path 进 cand）。
- **3.4 N 累加器仍 N+=1**：`l3_evaluate` 内部用 `cumulative_tested + n_peek` 对，但外层累加器写 `N += 1`；
  且 l1_refine 返回 None 的候选（三轮全失败）的窥视次数也一次没计。修：`N += cand.get("n_peek",1)`，
  失败候选也返回轻量 `{n_peek:rnd}` 由 run_batch 累进 `ck["peek_spent"]`。

## 批次 B 其余施工实录（2026-08-18 完成，commit 94e5733）
- **3.3 漏斗表退化为两态**：`gates` 建好后全是 None，`t0` 未用 → l1_log 的 `gate` 字段恒字符串 "g0-g4"。
  修：漏斗内每门记录 `gates[g]={"pass","why","ms"}` + `cand["gate_hit"]`（最后命中的门）+ `cand["gate_ms"]`；
  主控 l1_log 展平 `ms_g0..ms_g4`/`gate`（3.7 门禁有效性自检的前置——没有逐门数据只能看总通过率）。
  实现方式：漏斗内嵌 `_gate(name, ok, why)` 闭包计时器（`t_gate["_t0"]=time.time()` 起），每门通过/失败都记录。
- **3.5 G1 fail-open 计数**：G1 抽样算不出（rolling 因子抽样下 ICC 不连续）opened fail-open 放行 G2，
  但要标记计数进 gate_audit 兜底触发率。修：`g1_sample` 算不出返回 `True, "g1_fail_open"`（哨兵字符串），
  漏斗 G1 段判 `why=="g1_fail_open"` → `cand["g1_fail_open"]=True`，后续 gate_audit 统计兜底触发率用。
- **3.6 G2 补三项可交易性**（文档 G2 要求但代码没有）：补 `每日有效截面≥500`（脏活：因子值非空样本数
  中位数）+ `总覆盖率≥60%` + `可交易域一致性`（`|IC_可交易 − IC_全域|/|IC_全域| < 30%`，超标标
  `untradable_alpha`）。tradable_icir 现在偏重 / 缺 → 但 G2 判定仍走全域（可交易域一致性检查在补时用 filter）。
  **docstring 撒谎比没注释贵**：补齐前记得删 docstring 里没实现的项。Top-K/风格中性 AC 大（需新分组统计），
  文档已标注"下一轮"，本轮只补前三项。
- **3.7 embargo purge 落地**（原来只在注释里，段末样本 fwd_20d 标签跨进 2024 → G4 不是干净留出）：
  `load_design_df()` 段末按 `fwd_*` 最长周期(20日) purge：`cutoff = df["日期"].max() - timedelta(days=40)`
  （≈20 交易日含周末），print 实际 purge 行数。`load_holdout_df()` 段首 purge 对称。需要 `from datetime import timedelta`。
  实测设计段 purge 14.3 万行（>2023-11-19）。注意 purge 后 gap 因子 t_design 从 9.69→9.28（略降，合理）。
  **build_ic_data.py**：重算 fwd_* 前显式 `d.sort(["股票代码","日期"])`（增量合并改顺序不会静默错位），
  并加自检 `fwd_5d ≈ ret_5d.shift(-5)`（同股票分组，max_diff>1e-6 抛 RuntimeError）。
- **4.1 train_alpha360 推理乱序**：`train_dl` 是 `shuffle=True`，拿它推理再和原序 `ty`/`dates` 配对算
  train IC → 训练 IC 是随机配对噪声、导出预测错位。修：另建 `train_eval_dl = DataLoader(..., shuffle=False)`
  专供评估与导出，训练仍用 shuffle。修完的验收信号：train IC 应显著高于 dl_val IC（当前两者都像噪声）。
- **4.2 DL 与 2024 留出冲突**（2024 既是 G4 一次性留出又是每 epoch 选模基准 → DL 因子 G4 不独立）：
  人工定调走方案 1——**DL 单独切段**：张量构建改四段 `dl_train(2021-01~2023-06)/dl_val(2023-07~12)/holdout(2024)/valid(2025-26)`；
  train_alpha360 训练用 dl_train、early stopping 选模用 dl_val，2024(holdout)+2025-26(valid) 只推理。
  代价只是训练样本少半年，而 G4 独立性是防选择偏差最后一道。文件命名从 design 改为 dl_train/dl_val。
- **4.3 role/label_spec 硬编码 score**（L1 无条件写 role="score"，下游 L2/L3/L4 的 role 分流全是死代码）：
  从 LLM 输出读 `role`（白名单 score/exit/timing，非法降级 score 记日志）、`label_spec`（kind 白名单
  fwd_ret/triple_barrier/max_dd，horizon）。**非 score 角色跳过 IC 类门**（G2/G3/G4 只对 fwd_5d 有意义），
  仍过 G0（沙箱/列校验），返回时标记 `archived_only` → L2 已实现的档案登记分支。这样 LLM 声明 exit/timing
  才能透传到下游，role 分流不再是死代码。

## 批次 C 施工实录（2026-08-18 完成，commit e18db51）—— 效率与一致性
- **C21 LLM 单 prompt 来源**：`llm_factor_synth.main()` 原本是另一套旧的"自由发明" prompt + 旧 `eval_ic`，
  与 `l1_refine` 的 C1 复现 prompt 并存（双套易漂移）。修：`main()` 直接循环调 `l1_refine`，
  删除旧 system prompt 与旧 eval_ic 路径；`llm_factor_synth` 只保留被复用的 `load_deepseek_key`/`llm_chat`/`build_dict`。
  注意循环内 `from loop.factor_loop_l1l2 import l1_refine` 局部 import 不会循环死锁（反向引用只在 llm_chat）。
- **C22 L3 回测 4 次→2 次**：`l3_evaluate` 原跑 train/valid/2021-2023/2024 四次全量回测（成本翻倍，
  且宪法红线"单次全量回测>10min 需先说明"）。修：`run_backtest` 加 `return_by_year=True` 返回
  `year_ret:{年份:该年卖出平仓总收益pct}`（groupby 卖出年 pnl 聚合）；`l3_evaluate` train 那次带
  `return_by_year=True`，从 `train_m.year_ret` 拆 `seg_design=Σ(y≤2023)` 与 `seg_2024=yr.get(2024)`，
  删除独立的 seg_design/seg_2024 两次回测。分段披露语义不变。
- **C23 gate_audit 三类告警齐备**（工程保障要求的三类里原来只实现了"恒 100% 通过"）：
  补 `关键指标恒空率`（如 L1 的 `t_nw_design` 全空 → 主周期门从未真正计算）+ `兜底触发率`（
  G1 fail_open >10%）。依赖 3.3 逐门落盘 + 3.5 的 `g1_fail_open` 标记进 l1_log（新增 `g1_fail_open` 列）。
- **C24 G2/G3 主周期去重**（原缺陷 13）：`g3_full` 忽略传入 main，`l1_ic_metrics` 内部把主周期全量 IC
  又算一遍。修：`g2_main` 把多周期结果 `res` 缓存进 `main["_res_cache"]`；`g3_full(expr, main=main)` 读
  缓存传给 `l1_ic_metrics(expr, df=df, res=res_cache)`（新增可选 res 参数）；G3 返回前 `main2.pop("_res_cache")`
  不落盘。单候选全量 IC 从 2 次降为 1 次。漏斗入口 G3 要 `g3_full(expr, main=main)` 传 g2 的 main。

## 通用教训
- "代码写了但从未生效"比"没写"更危险：裸 `except: pass`、算完不用的变量、双重过滤后 main 再 pop——
  靠读代码复核找到它们，比跑断言便宜。修复时先写一条会失败的最小复现，再改。
- 仲裁规则：复核文档若指出"阈值代码与文档分叉，但代码理由成立"（如 G1 用 ICIR 而非 t_NW、
  G4 系数 0.2×），人工定调后**改文档服从代码校准值**，不要在批次 A/B 顺手把代码改回文档旧值。
- 批次 A/B 施工时反复踩的坑：`safe_compile` 解包改三元组要全量 grep 调用点、嵌套 over 全 NaN、
  join 列名冲突、polars 主/增量 schema 不一致（cast_options upcast）——都已在 SKILL.md 正文 pitfall 段，改代码时对照着看。
