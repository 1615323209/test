#!/usr/bin/env python3
"""扩展因子库全量构建 — 分批防 OOM（同 build_factors_pl 模式）
输出: factor_extra_daily.parquet（日期/股票代码 + 5个扩展因子）
不动旧 factor_daily.parquet（3.3GB），消费方按需 join。
"""
import polars as pl
import pyarrow.parquet as pq
from pathlib import Path
import gc, shutil, sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # code/ 根（跨目录 import）

INPUT = Path("D:/quant_data/factor_daily.parquet")  # 主库（含原始列）
INPUT_INCR = Path("D:/quant_data/factor_daily_incr.parquet")  # 增量（多文件 scan）
INPUT_COLS = ['日期', '收盘', '成交量', '成交额', '股票代码']
OUTPUT = Path("D:/quant_data/factor_extra_daily.parquet")
TMPDIR = Path("D:/quant_data/extra_tmp")
CHUNK = 500

from factors.extra_factors import calc_extra_factors, EXTRA_FACTOR_COLS

def _scan():
    files = [INPUT]
    if INPUT_INCR.exists():
        files.append(INPUT_INCR)
    return pl.scan_parquet(files)

TMPDIR.mkdir(exist_ok=True)
for f in TMPDIR.glob("batch_*.parquet"):
    f.unlink()

print("=== 扩展因子库构建（分批，多文件 scan）===")
codes = sorted(_scan().select('股票代码').unique().collect()['股票代码'].to_list())
total = len(codes)
print(f"股票: {total} 只")

for i in range(0, total, CHUNK):
    batch_codes = codes[i:i+CHUNK]
    bn = i//CHUNK + 1
    df = _scan().select(INPUT_COLS).filter(pl.col('股票代码').is_in(batch_codes)).collect()
    out = calc_extra_factors(df)[['日期', '股票代码'] + EXTRA_FACTOR_COLS]
    f = TMPDIR / f"batch_{bn:04d}.parquet"
    out.write_parquet(f, compression='zstd')
    if bn % 2 == 0:
        print(f"  [{i+len(batch_codes)}/{total}] batch_{bn:04d} OK ({len(out):,}行)")
    del df, out; gc.collect()

print("合并...")
writer = None
for f in sorted(TMPDIR.glob("batch_*.parquet")):
    t = pq.read_table(f)
    if writer is None:
        writer = pq.ParquetWriter(OUTPUT, t.schema)
    writer.write_table(t)
writer.close()
shutil.rmtree(TMPDIR)
print(f"=== 完成: {OUTPUT} {OUTPUT.stat().st_size/1024/1024:.0f}MB ===")
