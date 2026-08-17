#!/usr/bin/env python3
"""A股因子库构建 — 分批filter读取版（适用于2-4GB低内存服务器）
输入：a_stock_daily_hfq.parquet
输出：factor_daily.parquet
策略：每批50只从parquet filter读取，独立batch文件，最后pyarrow合并。
      不加载全量 → 不OOM。支持断点续传。
"""
import pandas as pd
import numpy as np
from pathlib import Path
import gc, shutil
import pyarrow as pa
import pyarrow.parquet as pq
import warnings
warnings.filterwarnings('ignore')

INPUT = Path("/home/ubuntu/quant_data/a_stock_daily_hfq.parquet")
TMPDIR = Path("/home/ubuntu/quant_data/factor_tmp")
OUTPUT = Path("/home/ubuntu/quant_data/factor_daily.parquet")
PROGRESS = Path("/home/ubuntu/quant_data/.factor_progress.txt")
BATCH = 50  # 每批股票数，调小节省内存

TMPDIR.mkdir(exist_ok=True)
tmp_files = sorted(TMPDIR.glob("batch_*.parquet"))
batch_id = len(tmp_files) + 1
print(f"已有 {len(tmp_files)} 个batch，从 batch_{batch_id:04d} 继续")

codes = sorted(pd.read_parquet(INPUT, columns=['股票代码'])['股票代码'].unique().tolist())
print(f"股票: {len(codes)} 只")

done = set()
if PROGRESS.exists():
    done = set(PROGRESS.read_text().strip().split("\n"))
todo = [c for c in codes if c not in done]
print(f"已恢复: {len(done)}, 待处理: {len(todo)}")

def compute(sub):
    """对单只股票计算所有因子（~35个）"""
    sub = sub.sort_values('日期').copy()
    sub['ret_1d'] = sub['收盘'].pct_change()
    sub['ret_5d'] = sub['收盘'].pct_change(5)
    sub['ret_10d'] = sub['收盘'].pct_change(10)
    sub['ret_20d'] = sub['收盘'].pct_change(20)
    sub['vol_5d'] = sub['ret_1d'].rolling(5).std()*np.sqrt(252)
    sub['vol_10d'] = sub['ret_1d'].rolling(10).std()*np.sqrt(252)
    sub['vol_20d'] = sub['ret_1d'].rolling(20).std()*np.sqrt(252)
    h,l,c = sub['最高'],sub['最低'],sub['收盘'].shift(1)
    tr = pd.concat([h-l,abs(h-c),abs(l-c)],axis=1).max(axis=1)
    sub['atr_14'] = tr.rolling(14).mean()
    sub['atr_ratio'] = sub['atr_14']/sub['收盘']
    for w in [5,10,20,60]:
        sub[f'ma_{w}'] = sub['收盘'].rolling(w).mean()
    sub['ma5_dist'] = (sub['收盘']-sub['ma_5'])/sub['ma_5']
    sub['ma20_dist'] = (sub['收盘']-sub['ma_20'])/sub['ma_20']
    sub['ma5_ma20_cross'] = ((sub['ma_5']>sub['ma_20'])&(sub['ma_5'].shift(1)<=sub['ma_20'].shift(1))).astype(int)
    sub['ma5_ma20_dead'] = ((sub['ma_5']<sub['ma_20'])&(sub['ma_5'].shift(1)>=sub['ma_20'].shift(1))).astype(int)
    vol=sub['成交量']
    sub['vol_ma5']=vol.rolling(5).mean(); sub['vol_ma20']=vol.rolling(20).mean()
    sub['vol_ratio']=vol/sub['vol_ma5']; sub['vol_ratio_20']=vol/sub['vol_ma20']
    sub['vol_change_5d']=vol.pct_change(5)
    if 'turnover' in sub.columns:
        sub['turn_ma5']=sub['turnover'].rolling(5).mean()
        sub['turn_ma20']=sub['turnover'].rolling(20).mean()
        sub['turn_ratio']=sub['turnover']/sub['turn_ma5']
    sub['high_20d']=sub['最高'].rolling(20).max(); sub['low_20d']=sub['最低'].rolling(20).min()
    sub['price_pos_20']=(sub['收盘']-sub['low_20d'])/(sub['high_20d']-sub['low_20d']+1e-10)
    sub['high_60d']=sub['最高'].rolling(60).max(); sub['low_60d']=sub['最低'].rolling(60).min()
    sub['price_pos_60']=(sub['收盘']-sub['low_60d'])/(sub['high_60d']-sub['low_60d']+1e-10)
    e12=sub['收盘'].ewm(span=12,adjust=False).mean(); e26=sub['收盘'].ewm(span=26,adjust=False).mean()
    sub['macd_dif']=e12-e26; sub['macd_dea']=sub['macd_dif'].ewm(span=9,adjust=False).mean()
    sub['macd_hist']=2*(sub['macd_dif']-sub['macd_dea'])
    d=sub['收盘'].diff(); g=d.clip(lower=0); l=d.clip(upper=0).abs()
    sub['rsi_14']=100-100/(1+g.rolling(14).mean()/(l.rolling(14).mean()+1e-10))
    sub['bb_mid']=sub['收盘'].rolling(20).mean(); s=sub['收盘'].rolling(20).std()
    sub['bb_upper']=sub['bb_mid']+2*s; sub['bb_lower']=sub['bb_mid']-2*s
    sub['bb_width']=(sub['bb_upper']-sub['bb_lower'])/sub['bb_mid']
    sub['bb_pos']=(sub['收盘']-sub['bb_lower'])/(sub['bb_upper']-sub['bb_lower']+1e-10)
    sub['limit_up']=(sub['ret_1d']>0.095).astype(int); sub['limit_down']=(sub['ret_1d']<-0.095).astype(int)
    sub['is_suspended']=(sub['成交量']==0).astype(int)
    u=(sub['ret_1d']>0).astype(int); d2=(sub['ret_1d']<0).astype(int)
    sub['up_streak']=u.groupby((u==0).cumsum()).cumsum()
    sub['down_streak']=d2.groupby((d2==0).cumsum()).cumsum()
    return sub

# 阶段1：逐批计算保存独立文件
ok = 0
for i in range(0, len(todo), BATCH):
    batch_codes = todo[i:i+BATCH]
    df_batch = pd.read_parquet(INPUT, filters=[('股票代码','in',batch_codes)])
    if df_batch.empty:
        continue
    df_batch['日期'] = pd.to_datetime(df_batch['日期'])
    results = []
    for code in batch_codes:
        sub = df_batch[df_batch['股票代码']==code]
        if len(sub) < 20:
            continue
        try:
            results.append(compute(sub))
            done.add(code); ok += 1
        except:
            pass
    if results:
        new_data = pd.concat(results, ignore_index=True)
        tmp_file = TMPDIR / f"batch_{batch_id:04d}.parquet"
        new_data.to_parquet(tmp_file, index=False)
        batch_id += 1
        PROGRESS.write_text("\n".join(sorted(done)))
        print(f"  [{ok}/{len(todo)}] → {tmp_file.name}")
    del df_batch, results, new_data; gc.collect()

print(f"\n阶段1完成: {ok} 只")

# 阶段2：合并所有batch为最终文件
print("阶段2: 合并...")
tmp_files = sorted(TMPDIR.glob("batch_*.parquet"))
schema = None
writer = None
total_rows = 0
for f in tmp_files:
    table = pq.read_table(f)
    if writer is None:
        schema = table.schema
        writer = pq.ParquetWriter(OUTPUT, schema)
    writer.write_table(table)
    total_rows += len(table)
writer.close()

print(f"合并完成: {OUTPUT} ({total_rows:,}行)")
shutil.rmtree(TMPDIR)
PROGRESS.unlink(missing_ok=True)
