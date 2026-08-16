#!/usr/bin/env python3
"""A股15年日K批量采集 — 腾讯源 后复权(hfq)
后复权无负数问题。过滤：排除ST/北交所/上市<250天。
输出 Parquet，断点续传。
"""
import akshare as ak
import pandas as pd
from pathlib import Path
import time
from datetime import datetime

DATA_DIR = Path("/home/ubuntu/quant_data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

START_DATE = "20100101"
END_DATE = datetime.now().strftime("%Y%m%d")
MIN_TRADING_DAYS = 250
SLEEP = 0.3
MAX_RETRIES = 3
ADJUST = "hfq"  # 后复权

OUTPUT = DATA_DIR / "a_stock_daily_hfq.parquet"
PROGRESS = DATA_DIR / ".collect_progress_hfq.txt"

print(f"=== A股日K采集（腾讯源 后复权）===")
print(f"时间: {START_DATE} ~ {END_DATE}")

# 1. 股票列表
print("[1/3] 获取股票列表...")
spots = ak.stock_zh_a_spot_tx()
print(f"  原始: {len(spots)} 只")

stocks = spots[~spots['name'].str.contains('ST', na=False)].copy()
print(f"  排ST: {len(stocks)} 只")
stocks = stocks[~stocks['code'].str.startswith('bj')].copy()
print(f"  排北交所: {len(stocks)} 只")

codes = stocks['code'].str[2:].tolist()

# 进度恢复
done = set()
if PROGRESS.exists():
    done = set(PROGRESS.read_text().strip().split("\n"))
    print(f"  已恢复: {len(done)} 只")

todo = [c for c in codes if c not in done]
print(f"  待采集: {len(todo)} 只")

# 2. 采集
print(f"\n[2/3] 开始采集...")
all_data = []
if OUTPUT.exists():
    all_data.append(pd.read_parquet(OUTPUT))
    print(f"  加载已有 {len(all_data[0])} 条")

ok = fail = short = 0

for i, code in enumerate(todo):
    df = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = ak.stock_zh_a_hist_tx(symbol=code, start_date=START_DATE,
                                       end_date=END_DATE, adjust=ADJUST)
            break
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)
            else:
                print(f"  [{i+1}/{len(todo)}] {code} ❌ {e}")
                fail += 1

    if df is None or df.empty:
        fail += 1
        continue
    if len(df) < MIN_TRADING_DAYS:
        short += 1; done.add(code); continue

    df = df.rename(columns={
        'date': '日期', 'open': '开盘', 'close': '收盘',
        'high': '最高', 'low': '最低', 'volume': '成交量',
        'amount': '成交额'
    })
    df['股票代码'] = code
    all_data.append(df)
    done.add(code)
    ok += 1

    if (i + 1) % 50 == 0:
        print(f"  [{i+1}/{len(todo)}] ✅{ok} ⏭{short} ❌{fail}")

    if (i + 1) % 100 == 0:
        PROGRESS.write_text("\n".join(sorted(done)))
        pd.concat(all_data, ignore_index=True).to_parquet(OUTPUT, index=False)
        print(f"  💾 已保存")

    time.sleep(SLEEP)

# 3. 保存
print(f"\n[3/3] 合并保存...")
combined = pd.concat(all_data, ignore_index=True)
combined.to_parquet(OUTPUT, index=False)
PROGRESS.write_text("\n".join(sorted(done)))

size_mb = OUTPUT.stat().st_size / 1024 / 1024
print(f"\n=== 完成 ===")
print(f"✅{ok} ⏭{short} ❌{fail}")
print(f"记录: {len(combined)} 条 | 股票: {combined['股票代码'].nunique()} 只")
print(f"日期: {combined['日期'].min()} ~ {combined['日期'].max()}")
print(f"文件: {OUTPUT} ({size_mb:.1f} MB)")
