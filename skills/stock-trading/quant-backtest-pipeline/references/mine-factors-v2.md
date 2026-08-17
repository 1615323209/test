# 因子挖掘 v3（mine_factors_v2.py）实战记录

2026-08-16 实测。脚本：`/home/ubuntu/quant_data/mine_factors_v2.py`，数据：ic_data.parquet（1257万行 × 42列，含 fwd_5d）。

## 要解决的问题

阶段2（mine_factors.py）产出 2340 个两两组合候选，FDR 校正后 2234 个通过 q<0.05——几乎全过，因为组合因子共享基础因子、IC 序列高度相关。FDR 对相关检验形同虚设，2340 候选实际只有 4-5 个独立主题。

## 四项改造

1. **去相关预筛**（组合前）：40 基础因子按 |ICIR| 降序贪心选择，与已选集合最大 |pearson corr| < 0.7 才保留。
   - 相关矩阵用 `df.sample(n=300_000, seed=42).select(BASE).drop_nulls()` 抽样算（30 万行足够，快）
   - ⚠️ 键判断坑：`corr_map` 只存 (a,b) 且 a<b（字典序），取时若 `(a,f) not in corr_map` 要用 `corr_map[(f,a)]`，**必须用 `in` 判断而不是 `a < f`**（实测踩过 KeyError）
2. **4 种运算**：x(乘) / d(差) / p(和) / r(除 `ca/(cb+1e-12)`)
3. **分段稳定性**：IC 序列按半年分段（`(year-2010)*2 + (month>6)`），段内 IC 均值同号比例 >=60% 且最近 2 段同号才保留
4. **精算层**（初筛 Top30）：换手暴露（top10% 因子值股票 turn_ratio 均值 ÷ 全市场）+ quintile 单调性 + `score = |ICIR|/(turn_exp+0.5)`

## 实测结果（去相关后 18 个独立基础因子）

```
limit_down(-0.645) vol_ratio_20(-0.385) turn_ma5(-0.377) vol_change_5d(-0.376)
ret_20d(-0.359) ret_5d(-0.341) macd_dif(-0.328) up_streak(-0.320) atr_14(-0.310)
ret_10d(-0.299) atr_ratio(-0.286) price_pos_60(-0.279) macd_hist(-0.205)
ma5_ma20_cross(-0.175) limit_up(+0.140) ret_1d(-0.097) down_streak(+0.096) ma5_ma20_dead(+0.025)
```
（括号内为 fwd_5d ICIR；18 个 = 反转类主力 + 标记类，其余 22 个因相关 >0.7 被剔除）

- 候选：C(18,2)×4 = 612
- 耗时：基础 IC 210s + 相关矩阵 103s + 612 候选 ~2500s（单候选 ~4s）+ 精算层，全量约 47 分钟
- 输出：mined_factors_v2.csv（全部候选）+ mined_factors_v2_fine.csv（精算层）

## ⚠️ 坑 1：NaN 过滤导致"初筛 0 通过"假象（白跑 47 分钟）

`compute_ic_full` 里 `ic.filter(pl.col('ic').is_not_null())` **滤不掉 NaN**（polars 中 NaN ≠ null）→ `v.mean()/v.std()` 传播 NaN → icir 全 NaN → 初筛 `|icir|>=0.25` 对 NaN 全 False → "612 候选 0 通过"。

修复：
```python
ic = ic.filter(pl.col('ic').is_not_null() & pl.col('ic').is_finite())
```
或沿用 `ic['ic'].fill_nan(None).drop_nulls()`。

**诊断信号**：CSV 里 ic_mean/icir 列为空、ic_pos_pct/seg_ok_ratio 正常 → 就是 NaN 泄漏。结果异常（全 0 通过、全 NaN）先查 NaN 过滤，再怀疑策略。

## ⚠️ 坑 2：前台管道跑长任务超时假象

冒烟测试 `python3 mine_factors_v2.py 2>&1 | grep ... | tail` 前台跑 600s 被杀，但输出文件其实 350s 就写完了——grep/tail 管道缓冲看不到实时输出，timeout 误杀。**长任务/冒烟测试一律后台跑 + 直接检查输出文件**，不要前台管道等。

## ⚠️ 坑 3：会话中断后后台进程存活

会话中断（received signal 1）不会杀已启动的后台进程。恢复会话时 `ps aux | grep <脚本名>` 检查——上次的冒烟测试可能还在跑甚至已完成（输出文件已生成）。先检查再决定是否重跑，不要盲目重启。

## 冒烟验证输出示例（修复后，limit_down 组合全部通过初筛）

```
limit_down_p_turn_ma5    IC=-0.0696  ICIR=-0.382  同号段=100% last2=True
limit_down_r_turn_ma5    IC=-0.0291  ICIR=-0.650  同号段=100% last2=True
limit_down_x_turn_ma5    IC=-0.0294  ICIR=-0.627  同号段=100% last2=True
limit_down_x_vol_ratio_20 IC=-0.0279 ICIR=-0.606  同号段=100% last2=True
limit_down_d_turn_ma5    IC=+0.0638  ICIR=+0.359  同号段=100% last2=True
```

## 结果解读方向

v3 挖掘后的候选筛选标准比阶段2 严（分段稳定性 + 换手暴露惩罚），通过初筛的因子直接可用于 v7 打分权重更新或新策略构建。若初筛通过数仍过多，收紧 SEG_OK_MIN（0.6→0.7）或 ICIR_MIN（0.25→0.3）。
