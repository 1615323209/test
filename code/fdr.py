#!/usr/bin/env python3
"""FDR 校正（Benjamini-Hochberg）— 挖掘因子统计检验
从 ICIR 近似 t 统计量 → p 值 → BH 校正 q<0.05
"""
import pandas as pd
import numpy as np
from scipy import stats

df = pd.read_csv('/home/ubuntu/quant_data/mined_factors.csv')
print(f"候选因子: {len(df)}")

# t 统计量 = ICIR × sqrt(days)，p = 2×(1-Φ(|t|))
df['t_stat'] = df['icir'] * np.sqrt(df['days'])
df['p_value'] = 2 * (1 - stats.norm.cdf(np.abs(df['t_stat'])))

# BH 校正
df_sorted = df.sort_values('p_value').reset_index(drop=True)
N = len(df_sorted)
df_sorted['rank'] = np.arange(1, N+1)
df_sorted['q_value'] = df_sorted['p_value'] * N / df_sorted['rank']
df_sorted['q_value'] = df_sorted['q_value'].clip(upper=1.0)

# 找到 q<0.05 的最大 rank（BH 步骤）
passed = df_sorted[df_sorted['q_value'] < 0.05]
k = len(passed)
significant = df_sorted.head(k) if k > 0 else pd.DataFrame()

print(f"\nBH 阈值: q < 0.05")
print(f"通过 FDR 的因子数: {k}")

# 同时保留 IC 强度筛选（实际可用性）
usable = df_sorted[(df_sorted['q_value'] < 0.05) & (df_sorted['ic_mean'].abs() > 0.02)]
print(f"通过 FDR 且 |IC|>0.02 的因子数: {len(usable)}")

# 输出 Top 20
print("\n=== 通过 FDR 的 Top 20 因子 ===")
cols = ['expr','ic_mean','icir','t_stat','p_value','q_value']
print(usable[cols].head(20).to_string(index=False))

# 保存
usable.to_csv('/home/ubuntu/quant_data/fdr_passed.csv', index=False)
print(f"\n保存: fdr_passed.csv ({len(usable)}个)")
