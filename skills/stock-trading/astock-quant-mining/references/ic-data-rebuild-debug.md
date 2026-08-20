# ic_data 重建 / 数据链路完整故障链（2026-08-18 实测）

`build_ic_data.py`（`python -m data.build_ic_data`）重建 ic_data 时踩过一整串连锁故障。
根因 + 修复 + 验证，供未来重建/数据更新排查复用。

## 故障链总览（一条接一条，全修好才跑通）

1. **全量 collect OOM**：`ic_data` 1260 万行 × 66 列，`pl.scan_parquet(...).collect()` + 多次 join/with_columns
   在内存被占用的机器（hermes + 浏览器占 ~11GB/15.8GB）直接 `memory allocation failed`。
2. **fwd_* 自检失败**：最初自检断言 `fwd_1d == ret_1d.shift(-1)` 老失败（max diff 0.2、5.37）。
3. **数据源脏值**：部分退市/停牌股（600595、600076）收盘价被采集成 ≤0 或 ±inf，
   导致 ret_1d 算出 -inf/+inf/-5.6 等荒谬值，污染一切 ret/fwd 统计。
4. **主+增量合并重复**：`factor_daily.parquet` + `factor_daily_incr.parquet` 合并后
   出现 19662 组「同日期+同股票代码 ×N 行」重复 → daily_picks 选股结果整行重复。
5. **备份 rename WinError 183**：目标 `.bak` 已存在（上次失败残留）时 `OUT.rename(bak)` 报错。

## 逐条修复

### 1. 内存：lazy 链 + 流式 collect
```python
d = pl.scan_parquet(files, cast_options=pl.ScanCastOptions(integer_cast="upcast"))
d = d.join(raw_scan, on=["日期","股票代码"], how="left")   # 全部 lazy
d = d.filter(...).with_columns([...]).filter(切片)           # 全 auto-downsample 到 train 段
d = d.collect(engine="streaming")                            # ← 唯一一次物化用流式
```
- polars 1.43：`collect(streaming=True)` 已弃用（DeprecationWarning）→ 用 `collect(engine="streaming")`。
- 更老版本无 streaming，可 try/except 退化到 `collect()`。
- `collect_schema()` 返回 Schema 对象，字段数用 `len(d.collect_schema())`（不是 `.n_fields()`）。
- **避免在 lazy 链中途做 `pl.len()` / `count()` collect 统计清理行数**——那会触发整表物化（照样 OOM）。
  让 filter 在 lazy 图上、随切片一次 collect，自然下推。

### 2. fwd_* 必须与 ret_* 同源生成（不要用收盘重算）
```python
d = d.with_columns([
    pl.col("ret_1d").shift(-1).over("股票代码").alias("fwd_1d"),
    pl.col("ret_5d").shift(-5).over("股票代码").alias("fwd_5d"),
    pl.col("ret_10d").shift(-10).over("股票代码").alias("fwd_10d"),
    pl.col("ret_20d").shift(-20).over("股票代码").alias("fwd_20d"),
])
```
- 已验证：现有正确 ic_data 里 `fwd_n == ret_n.shift(-n)` 成立（max diff ~1e-15）。
- `close.shift(-n)/close-1`（几何收益）与 `ret_n` 在**除权除息复权跳变日**不一致（如
  688167 从 356→复权调整后下一日，`fwd_calc=0` 但 `rlead=0.199`）→ 自检假失败。
- 同理 `ret_5d` 是 `ret_1d.rolling_sum(5)`（算术和），`fwd_5d` 若用 close 几何收益，
  两者数学上本就不等——**永远不要拿 fwd_5d 与 ret_5d.shift(-5) 直接比**。

### 3. 清理脏值（收盘 ≤0 或非有限）
```python
d = d.filter(pl.col("收盘").is_finite() & (pl.col("收盘") > 0))
```
- A 股真实股票收盘价恒 >0；退市/停牌被采成负价或 0 的是脏值，直接清。
- 自检（fwd vs ret）也要用 `.is_finite()` 过滤两侧，不要只 `is_not_null()`（inf 会通过）。

### 4. 主+增量合并去重（所有读多文件的入口都要）
```python
d = d.unique(subset=["日期","股票代码"], keep="last")
```
- `daily_picks.py` 和 `build_ic_data.py` 读 factor_daily 主+增量都加了；凡 `factor_files()` 多文件 scan 都该去重。
- 症状：daily_picks 输出里同一只股票连续出现 2-4 次（如 688030×2、003007×4）。
- `keep="last"`：增量可能是更新的完整行，保留后写的。

### 5. 备份 rename 前删旧 bak
```python
if bak.exists():
    bak.unlink()   # 先删旧备份，避免 WinError 183 目标已存在
OUT.rename(bak)
```

## 验证方法（重建后必须做）
```python
import polars as pl
d = pl.read_parquet("D:/quant_data/ic_data.parquet")
dup = d.group_by(["日期","股票代码"]).len().filter(pl.col("len")>1)
assert len(dup) == 0   # 无重复
for c in ["fwd_1d","fwd_5d","fwd_10d","fwd_20d","开盘","最高","最低"]:
    assert d[c].count()/len(d) > 0.99   # 关键列非空率
# daily_picks 去重验证：选出 5 只互不相同的股票（不再有整行重复）
```

## 排查「数据/选股异常」的检查顺序
当量化系统"没信号 / 没买入建议 / 收益异常"且数据看似正常时，先按序查基础设施：
1. 数据新鲜度：`factor_daily(.incr).parquet` 最新日期 vs 今天是几号（这是"没实盘买卖"最常被忽略的根因）
2. vec_cache 是否被清空 / 为空（`ls loop_state/vec_cache/*.npy`）——清空 → L2 全部误杀
3. 主+增量是否重复（去重后看 daily_picks 是否正常）
4. 脏值（收盘 ≤0 / ret ±inf）是否混入
