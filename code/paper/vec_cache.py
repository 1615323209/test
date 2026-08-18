#!/usr/bin/env python3
"""因子向量缓存（改造2.0 3.3）：让 L2 成本与池大小脱钩

存表达式在设计段上的逐日横截面 rank 向量（float32），键 = expr_hash（语义归一化，与已拒绝库同一套）。
池内因子向量只在入池时算一次，此后候选的去重/正交全是纯内存 numpy，不再碰 parquet。
容量配额按 LRU 淘汰（默认 40MB）。
"""
import os, shutil
from pathlib import Path
import polars as pl
import numpy as np

CACHE_DIR = Path(r"D:\quant_data\loop_state\vec_cache")
QUOTA_MB = 40  # 超配额按 LRU 淘汰（atime 近似用 mtime）

def _path(h):
    return CACHE_DIR / f"{h}.npy"

def get_vec(expr, df=None, days=None, force=False):
    """取表达式的逐日 rank 向量（shape [n_day] 的 dict? 不——返回完整 float32 向量）。
    设计：存 (index0..n-1) 原样 rank 展平。为便去重/正交，直接存设计段全样本 rank 列。
    df: 计算源（默认 load_design_df）。days: 需要对齐的日期列表（缓存按整个设计段算，用值即可）
    """
    from loop.factor_loop_l1l2 import expr_hash, load_design_df
    h = expr_hash(expr)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(h)
    if p.exists() and not force:
        try:
            return np.load(p)
        except Exception:
            pass
    # 计算：设计段全样本 rank（按日期分组）
    from loop.expr_sandbox import safe_compile
    df = df if df is not None else load_design_df()
    if isinstance(expr, str):
        ex, err, _ = safe_compile(expr)
        if ex is None:
            raise ValueError(f"向量缓存表达式沙箱拒绝: {err}")
        expr = ex
    vec = df.with_columns(expr.rank().over("日期").alias("_v"))["_v"].to_numpy().astype(np.float32)
    np.save(p, vec)
    _evict()
    return vec

def get_indices(df=None):
    """设计段行索引（与向量对齐）"""
    from loop.factor_loop_l1l2 import load_design_df
    df = df if df is not None else load_design_df()
    return None  # 向量本身就是全样本 rank，去重/正交直接用同一份 df 的行序

def _evict():
    """超配额 LRU 淘汰（按 mtime 近似）"""
    try:
        total = sum(f.stat().st_size for f in CACHE_DIR.glob("*.npy"))
        if total <= QUOTA_MB * 1024 * 1024:
            return
        files = sorted(CACHE_DIR.glob("*.npy"), key=lambda f: f.stat().st_mtime)
        for f in files:
            total -= f.stat().st_size
            try:
                f.unlink()
            except OSError:
                pass
            if total <= QUOTA_MB * 1024 * 1024:
                break
    except Exception:
        pass

def clear():
    """清空缓存（测试用）"""
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR, ignore_errors=True)
