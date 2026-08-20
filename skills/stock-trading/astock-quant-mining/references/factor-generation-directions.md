# L1 三大前沿方向开发经验（2026-08-17）

L1 文档第八章定义三大因子生成方向：方向一 LLM 合成（已落地 `llm_factor_synth.py`）、方向二 深度学习表示学习（Alpha360）、方向三 公式束搜索（beam search）。本次开发了方向二、三，产物都走同一 L1 体检管线（`l1_ic_metrics`：|ICIR|≥0.25 + 次周期同号 + 衰减<50% + 滚动60日ICIR min>0 + Rank/Normal 同号）。

## 方向三：formula_beam_search.py（loop/）

**思路**：beam search 组合 polars 时序算子生成公式，Reward=fwd_5d ICIR。产物是 **polars 表达式字符串**（与 LLM 因子同格式），`eval` 后直接复用 `calc_multi_ic` / `l1_ic_metrics`。

用法：
```
python -m loop.formula_beam_search --smoke    # depth 强制 1，~5 分钟
python -m loop.formula_beam_search --top-k 30 --depth 2   # 完整 ~30-40 分钟，后台跑
```
输出 `D:/quant_data/loop_state/beam_results.json`（Top 公式 + L1 状态）。

算子模板（金融逻辑合理）：价格/均线乖离、波动率(std/mean)、n日动量、z-score、横截面 rank（`rank().over('日期')`）、量能短长均线比。时序算子写法：`pl.col(c).rolling_mean(n).over('股票代码')`（over 分组后 rolling 在组内）。

**坑 1：受限 eval 的正则白名单必须含中文字符**。列名是中文（`'股票代码'` 等），正则 `[A-Za-z0-9_...]+` 会把所有含中文列名的表达式拦成"无效 0 个"。必须加 `\u4e00-\u9fff`。症状：58 候选全部返回 None、最佳 |ICIR|=0。

**坑 2（最重要）：Reward 不能只看整体 |ICIR|**。按整体 |ICIR| 排序选出的因子（0.45-0.58）全被 L1 滚动稳定性检查拦下（滚动 60 日 ICIR min 有负时段，如 -0.013 ~ -1.5）——整体均值高但时段不稳，L1 拦截是正确行为。改法：`score_expr` 内跑完整 `l1_ic_metrics`，排序 `key=lambda x: (x["l1_ok"], abs(x["icir"]))`，过 L1 的候选优先进 beam。

**坑 3：扩展层候选爆炸**。depth=2 时 Top-30 × 16 种扩展 = 480 候选 × ~5s ≈ 40 分钟。只扩展 Top-10、精简扩展方式（rank / 与基础列 ratio / rolling_mean 平滑），约 100 候选。

实测：vol_ratio/turn_ratio 波动率类因子 |ICIR| 0.45-0.58（A股低波异象，负 IC）；`vol_change_5d` 的 60 日波动率/vol_ratio 达 0.577 + 同号段 73%，但滚动 min=-0.013 微负仍被 L1 拒——接近通过，是继续优化的方向（如参数微调、组合变换）。

## 方向二：Alpha360（build_alpha360_tensor.py + train_alpha360.py，loop/）

**思路**：1D-CNN 从 30 天×8 特征学 Alpha，预测值作因子列喂 v7 打分增量。walk-forward：train 2021-2024 / val 2025-2026（val 只看）。

张量构建（build_alpha360_tensor.py）：
- 特征 8 个：close/open/high/low/volume/amount/turnover + ret_1d，按股票 z-score：`pl.col(c).sub(pl.col(c).mean().over('股票代码')).truediv(pl.col(c).std().over('股票代码').add(1e-9))`
- 标签 fwd_5d：`(pl.col('收盘').shift(-5)/pl.col('收盘') - 1).over('股票代码')`
- 滑窗用 `numpy.lib.stride_tricks.sliding_window_view`（向量化，不用逐行循环）
- **坑**：fwd_5d 在数据末尾缺失（null→NaN），滑窗时 `if not np.isfinite(y[j]): continue`；停牌股特征 NaN（std=0）用 `np.nan_to_num(x, nan=0.0)` 中性化。不处理则 val_y mean=nan、x 有 NaN。
- 样本量：全量约 470 万/年太大；`--sample-every 5` → 85 万（~2min），`--sample-every 10` → 43 万（~1min）。CPU 训练用降采样版先验证。
- 日期需从 2020-12 起读（为 2021 提供 30 天窗口）。

训练（train_alpha360.py）：
- 模型：Conv1d(8→32,k=5,p=2)→ReLU→Dropout(0.2)→Conv1d(32→64,k=3,p=1)→ReLU→Dropout→Conv1d(64→64)→AdaptiveAvgPool1d→Linear(64→1)；Adam lr=1e-3 weight_decay=1e-4；MSE；early stop patience=4。
- **坑：Anaconda 下 torch+numpy OpenMP 冲突**（`OMP Error #15: libiomp5md.dll already initialized`）→ 脚本顶部 import torch 前 `os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")`。
- **坑：torch 只在 Anaconda python**（`D:\02_download\APP\Anaconda\python.exe`），系统 python 无 torch——训练必须用 Anaconda python。
- 性能：CPU 16 线程 85 万样本 ≈ 428s/epoch；43 万 ≈ 210s/epoch。快速验证用 3 epochs 确认链路，完整用 12 epochs 后台跑。
- IC 评估：按日期分组 Spearman（`groupby('日期').apply(lambda g: g['p'].corr(g['y'], method='spearman'))`），报 IC 均值 + ICIR。
- 实测：ep01 验证集外推 IC=+0.107, ICIR=+0.65（walk-forward 有效）；train IC≈0（回归任务早期正常）。
- 输出：`alpha360_model.pt` + `pred_train.csv` / `pred_val.csv`（日期×代码×预测，集成时作为因子列喂 L1 体检）。

## 集成路径

方向二/三产出的因子（表达式或预测序列）→ `l1_ic_metrics` 体检 → 合格进 L2（去重/正交化）→ L3 回测。黑箱因子（深度学习）额外强制查 quintile 单调性 |mono|≥0.3（L1 文档通用门槛）。
