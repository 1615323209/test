#!/usr/bin/env python3
"""扩展因子计算模块 — 流动性与微观结构 / 高阶矩类
独立于 factors.py（旧45因子），供 build_extra_factors.py 和 update_daily.py 复用。
输入原始日K（日期/收盘/成交量/成交额/股票代码），输出 5 个新因子。

新因子清单（2026-08-16 依据 FACTOR_LIBRARY.md 评估建议）：
- illiq_20   : Amihud 非流动性，20日均值 log1p(|ret_1d|/成交额*1e10)
               值越大流动性越差，暴跌时是负向信号
- vol_corr_5 : 5日量价相关性 corr(ret_1d, 成交量pct_change)
               正=量价齐升延续趋势，负=量价背离预示反转
- vol_corr_20: 20日量价相关性（同上，长窗口）
- skew_20    : 20日收益偏度（彩票偏好，高偏度→已被炒作→未来收益低）
- kurt_20    : 20日超额峰度（极端风险度量）
"""
import polars as pl

def calc_extra_factors(df):
    """输入原始日K → 输出追加5个扩展因子的 DataFrame（含原始列）"""
    df = df.with_columns(pl.col('日期').cast(pl.Date))
    df = df.sort(['股票代码', '日期'])
    df = df.with_columns([
        pl.col('收盘').pct_change().over('股票代码').fill_nan(None).clip(-1, 1).alias('ret_1d'),
        pl.col('成交量').pct_change().over('股票代码').fill_nan(None).clip(-1, 1).alias('vol_chg'),
    ])
    # --- 1. Amihud 非流动性（20日均值，log 压缩极端值）---
    # clip(0,1e6) 防成交额为0时 inf；log1p 后最大约 13.8
    illiq_d = (pl.col('ret_1d').abs() / (pl.col('成交额') + 1e-9) * 1e10).clip(0, 1e6)
    df = df.with_columns([
        illiq_d.rolling_mean(20, min_samples=20).over('股票代码').log1p().alias('illiq_20'),
    ])
    # --- 2. 量价相关性 corr(ret_1d, vol_chg, window) ---
    # 用矩实现: corr = (E[xy]-E[x]E[y]) / (σx·σy)，rolling_mean 即可，避免逐窗循环
    for n, suffix in [(5, '5'), (20, '20')]:
        x, y = pl.col('ret_1d'), pl.col('vol_chg')
        exy = (x * y).rolling_mean(n, min_samples=n).over('股票代码')
        ex  = x.rolling_mean(n, min_samples=n).over('股票代码')
        ey  = y.rolling_mean(n, min_samples=n).over('股票代码')
        sx  = x.rolling_std(n, min_samples=n).over('股票代码')
        sy  = y.rolling_std(n, min_samples=n).over('股票代码')
        corr = (exy - ex * ey) / (sx * sy + 1e-12)
        df = df.with_columns(corr.alias(f'vol_corr_{suffix}'))
    # --- 3/4. 20日收益偏度 & 超额峰度（矩展开，x=ret_1d, μ=E[x]）---
    x = pl.col('ret_1d')
    m1 = x.rolling_mean(20, min_samples=20).over('股票代码')
    m2 = (x * x).rolling_mean(20, min_samples=20).over('股票代码')
    m3 = (x * x * x).rolling_mean(20, min_samples=20).over('股票代码')
    m4 = (x * x * x * x).rolling_mean(20, min_samples=20).over('股票代码')
    sd = x.rolling_std(20, min_samples=20).over('股票代码')
    skew = (m3 - 3*m1*m2 + 2*m1.pow(3)) / (sd.pow(3) + 1e-12)
    kurt = (m4 - 4*m1*m3 + 6*m1.pow(2)*m2 - 3*m1.pow(4)) / (sd.pow(4) + 1e-12) - 3.0
    df = df.with_columns([skew.alias('skew_20'), kurt.alias('kurt_20')])
    return df.drop(['ret_1d', 'vol_chg'])

EXTRA_FACTOR_COLS = ['illiq_20', 'vol_corr_5', 'vol_corr_20', 'skew_20', 'kurt_20']

if __name__ == '__main__':
    import sys
    # 小规模自测：读少量股票验证
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    codes = sorted(pl.scan_parquet('D:/quant_data/a_stock_daily_hfq.parquet')
                   .select('股票代码').unique().collect()['股票代码'].to_list())[:n]
    df = pl.scan_parquet('D:/quant_data/a_stock_daily_hfq.parquet') \
           .filter(pl.col('股票代码').is_in(codes)).collect()
    out = calc_extra_factors(df)
    print(f"输入 {len(df):,} 行 → 输出 {len(out):,} 行, 列: {out.columns}")
    for c in EXTRA_FACTOR_COLS:
        s = out[c]
        print(f"  {c:12} 非空率={s.is_not_null().mean()*100:5.1f}%  "
              f"均值={s.mean():+.4f} 标准差={s.std():.4f} 范围=[{s.min():.3f}, {s.max():.3f}]")
    out.write_parquet('/tmp/extra_test.parquet')
    print("已写 /tmp/extra_test.parquet")
