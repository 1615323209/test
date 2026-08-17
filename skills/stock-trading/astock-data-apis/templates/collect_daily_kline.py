#!/usr/bin/env python3
"""A股15年日K批量采集 — 东方财富源 (push2.eastmoney.com) 模板
⚠️ 仅适用于家庭宽带/Windows桌面环境！云服务器（腾讯云/阿里云）IP会被封禁。
云服务器请用 templates/collect_daily_kline_tx.py（腾讯源 hfq）。
过滤：排除ST/*ST、北交所、上市不满250个交易日。
输出：前复权日K，Parquet 格式，支持断点续传。
"""
import akshare as ak
import pandas as pd
from pathlib import Path
import time
import sys
from datetime import datetime

DATA_DIR = Path.home() / "quant_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = "20100101"  # 15年前
END_DATE = datetime.now().strftime("%Y%m%d")
MIN_TRADING_DAYS = 250      # 上市至少1年
BATCH_SIZE = 50             # 每50只打印一次进度
SLEEP_BETWEEN = 0.3         # 请求间隔（秒）
MAX_RETRIES = 3

OUTPUT_FILE = DATA_DIR / "a_stock_daily_qfq.parquet"
PROGRESS_FILE = DATA_DIR / ".collect_progress.txt"

print(f"=== A股日K数据采集 ===")
print(f"时间范围: {START_DATE} ~ {END_DATE}")
print(f"输出: {OUTPUT_FILE}")
print(f"过滤: 排除ST/*ST，上市 < {MIN_TRADING_DAYS} 交易日")

# 1. 获取全A股列表
print("\n[1/3] 获取股票列表...")
stock_list = ak.stock_zh_a_spot_em()
print(f"  原始列表: {len(stock_list)} 只")

# 2. 过滤
stocks = stock_list[~stock_list['名称'].str.contains('ST', na=False)].copy()
print(f"  排除ST后: {len(stocks)} 只")

stocks = stocks[~stocks['代码'].str.startswith(('83', '87'))].copy()
print(f"  排除北交所后: {len(stocks)} 只")

# 恢复进度
done_codes = set()
if PROGRESS_FILE.exists():
    done_codes = set(PROGRESS_FILE.read_text().strip().split("\n"))
    print(f"  恢复进度: {len(done_codes)} 只已完成")

todo = [c for c in stocks['代码'].tolist() if c not in done_codes]
print(f"  待采集: {len(todo)} 只")

# 3. 批量采集
print(f"\n[2/3] 开始采集（间隔 {SLEEP_BETWEEN}s，最多重试 {MAX_RETRIES} 次）...")
all_data = []

if OUTPUT_FILE.exists():
    all_data.append(pd.read_parquet(OUTPUT_FILE))
    print(f"  已加载 {len(all_data[0])} 条历史数据")

success = 0
failed = 0
too_short = 0

for i, code in enumerate(todo):
    df = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period='daily',
                start_date=START_DATE,
                end_date=END_DATE,
                adjust='qfq'
            )
            break
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
            else:
                print(f"  [{i+1}/{len(todo)}] {code} 失败: {e}")
                failed += 1
                continue

    if df is None or df.empty:
        failed += 1
        continue

    if len(df) < MIN_TRADING_DAYS:
        too_short += 1
        done_codes.add(code)
        continue

    all_data.append(df)
    done_codes.add(code)
    success += 1

    if (i + 1) % BATCH_SIZE == 0:
        print(f"  [{i+1}/{len(todo)}] 成功 {success}, 不足 {too_short}, 失败 {failed}")

    if (i + 1) % 100 == 0:
        PROGRESS_FILE.write_text("\n".join(sorted(done_codes)))
        combined = pd.concat(all_data, ignore_index=True)
        combined.to_parquet(OUTPUT_FILE, index=False)
        print(f"  [checkpoint] 已保存 {len(combined)} 条")

    time.sleep(SLEEP_BETWEEN)

# 最终保存
print(f"\n[3/3] 最终合并保存...")
combined = pd.concat(all_data, ignore_index=True)
combined.to_parquet(OUTPUT_FILE, index=False)
PROGRESS_FILE.write_text("\n".join(sorted(done_codes)))

print(f"\n=== 完成 ===")
print(f"成功: {success} 只, 数据不足: {too_short} 只, 失败: {failed} 只")
print(f"总记录: {len(combined)} 条, 股票数: {combined['股票代码'].nunique()}")
print(f"日期范围: {combined['日期'].min()} ~ {combined['日期'].max()}")
print(f"文件: {OUTPUT_FILE} ({OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB)")
