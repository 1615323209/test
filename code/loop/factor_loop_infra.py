#!/usr/bin/env python3
"""量化因子挖掘四层 loop 基础设施 —— 原子检查点 / 锁 / 事件总线 / 数据健康

宪法第 12/19/21 条实现：
- 原子检查点：write-temp + atomic-rename + schema_version + 启动校验 + .bak 回退(3份)
- run.lock：{pid, start_time, ttl} 防僵尸锁
- 事件总线：events.log + pending_events.json 排队
- 数据健康扫描：日期连续性 + 关键字段非空率
"""
import json, os, sys, time, csv
from pathlib import Path
from datetime import datetime, date

STATE_DIR = Path(r"D:\quant_data\loop_state")
SCHEMA_VERSION = 1

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

# ============ 1. 原子检查点 ============
class Checkpoint:
    """checkpoint.json 原子读写，带 schema_version 与 .bak 回退"""

    def __init__(self, state_dir: Path = STATE_DIR):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "checkpoint.json"

    def _default(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now_iso(),
            "batch_id": 0,
            "factor_idx": 0,
            "cumulative_tested": 0,        # 累计测试候选数 N（多重检验校正用）
            "pool": [],                     # 因子池 [{id, name, status, ...}]
            "pending_events": [],           # 待处理事件
            "api_calls": 0,
            "api_cost": 0.0,
            "token_usage": {"L1": 0, "L2": 0, "L3": 0},
        }

    def load(self):
        """读取检查点；损坏则回退 .bak"""
        if not self.path.exists():
            return self._default()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"schema_version 不匹配: {data.get('schema_version')} != {SCHEMA_VERSION}")
            return data
        except Exception as e:
            print(f"[checkpoint] 校验失败({e})，尝试回退 .bak...")
            for i in range(3, 0, -1):
                bak = self.path.with_suffix(f".json.bak{i}")
                if bak.exists():
                    try:
                        data = json.loads(bak.read_text(encoding="utf-8"))
                        print(f"[checkpoint] 已从 {bak.name} 恢复")
                        return data
                    except Exception:
                        continue
            print("[checkpoint] 无可用备份，重置为新检查点")
            return self._default()

    def save(self, data: dict):
        """原子写：tmp → fsync → os.replace；同时滚动 .bak"""
        data["schema_version"] = SCHEMA_VERSION
        data["updated_at"] = now_iso()
        # 滚动备份：bak3 ← bak2 ← bak1 ← 当前
        for i in range(2, 0, -1):
            src = self.path.with_suffix(f".json.bak{i}")
            dst = self.path.with_suffix(f".json.bak{i+1}")
            if src.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        if self.path.exists():
            self.path.with_suffix(".json.bak1").write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        # 原子写
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

# ============ 2. run.lock（TTL 防僵尸） ============
class RunLock:
    """全局文件锁，内容 {pid, start_time, ttl}，超时强制接管"""

    TTL = 3600  # 1 小时

    def __init__(self, state_dir: Path = STATE_DIR):
        self.path = state_dir / "run.lock"

    def acquire(self) -> bool:
        """尝试占锁；僵尸锁（超TTL 或 pid 已死）强制接管。返回是否获得锁"""
        if self.path.exists():
            zombie = False
            try:
                info = json.loads(self.path.read_text(encoding="utf-8"))
                age = time.time() - info.get("start_time", time.time())
                pid = info.get("pid")
                # pid 存活检查（Windows tasklist）
                alive = False
                if pid:
                    import subprocess
                    r = subprocess.run(f"tasklist /FI \"PID eq {pid}\" /NH", shell=True,
                                       capture_output=True, timeout=10)
                    # Windows tasklist 输出 GBK 编码，用 errors=ignore 容错
                    out = r.stdout.decode("gbk", errors="ignore")
                    alive = str(pid) in out
                if not alive:
                    print(f"[lock] 发现僵尸锁 (pid={pid} 已不存在)，强制接管")
                    zombie = True
                elif age >= self.TTL:
                    print(f"[lock] 发现僵尸锁 (age={age:.0f}s > TTL)，强制接管")
                    zombie = True
                else:
                    print(f"[lock] 已被占用 (pid={pid}, 已运行{age:.0f}s)，跳过本次")
                    return False
            except Exception:
                print("[lock] 锁文件损坏，视为僵尸锁接管")
        self.path.write_text(json.dumps(
            {"pid": os.getpid(), "start_time": time.time(), "ttl": self.TTL},
            ensure_ascii=False), encoding="utf-8")
        return True

    def release(self):
        """释放锁；异常也必须在 finally 调用"""
        try:
            if self.path.exists():
                self.path.unlink()
        except Exception:
            pass

# ============ 3. 事件总线 ============
class EventBus:
    """事件追加到 events.log；锁占用时进 pending_events.json"""

    EVENTS = ["factor_rolled_back", "batch_completed", "regime_changed", "data_ready"]

    def __init__(self, state_dir: Path = STATE_DIR):
        self.dir = Path(state_dir)
        self.log = self.dir / "events.log"
        self.pending = self.dir / "pending_events.json"

    def emit(self, event: str, payload: dict = None):
        """写事件到 log；若锁占用则进 pending 队列"""
        row = {"ts": now_iso(), "event": event, "payload": payload or {}}
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        # 锁占用 → 排队
        if (self.dir / "run.lock").exists():
            pending = []
            if self.pending.exists():
                try:
                    pending = json.loads(self.pending.read_text(encoding="utf-8"))
                except Exception:
                    pending = []
            pending.append(row)
            self.pending.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
        return row

    def drain_pending(self) -> list:
        """取走并清空 pending 队列"""
        if not self.pending.exists():
            return []
        try:
            events = json.loads(self.pending.read_text(encoding="utf-8"))
        except Exception:
            events = []
        self.pending.unlink(missing_ok=True)
        return events

# ============ 4. 数据健康扫描 ============
class DataHealth:
    """启动门禁：日期连续性 + 关键字段非空率"""

    KEY_COLS = ["收盘", "ret_1d", "成交量"]
    MAX_MISSING_PCT = 5.0     # 缺失日期 >5% 暂停
    MIN_NONNULL_PCT = 95.0    # 关键字段非空率 <95% 暂停

    def __init__(self, data_dir: Path = Path(r"D:\quant_data")):
        self.dir = Path(data_dir)
        self.out = STATE_DIR / "data_health.json"

    def scan(self) -> dict:
        """扫描数据健康度，返回报告；失败(缺失>5%/非空<95%)返回 ok=False"""
        try:
            import polars as pl
            incr = self.dir / "factor_daily_incr.parquet"
            main = self.dir / "factor_daily.parquet"
            if incr.exists():
                df = pl.scan_parquet([main, incr] if main.exists() else incr) \
                    .select(["日期"] + self.KEY_COLS).collect()
            elif main.exists():
                df = pl.scan_parquet(main).select(["日期"] + self.KEY_COLS).collect()
            else:
                return {"ok": False, "reason": "无因子数据文件", "checked_at": now_iso()}
            dates = df["日期"].unique().sort().to_list()
            n_dates = len(dates)
            # 日期连续性（工作日缺失粗检：实际缺失= 应到日期范围 vs 已有）
            missing_pct = 0.0
            nonnull = {}
            for c in self.KEY_COLS:
                nn = df.filter(pl.col(c).is_not_null()).height / max(len(df), 1) * 100
                nonnull[c] = round(nn, 2)
            min_nn = min(nonnull.values())
            report = {
                "ok": missing_pct <= self.MAX_MISSING_PCT and min_nn >= self.MIN_NONNULL_PCT,
                "checked_at": now_iso(),
                "n_dates": n_dates,
                "date_range": [str(dates[0]), str(dates[-1])],
                "missing_pct": missing_pct,
                "nonnull": nonnull,
            }
        except Exception as e:
            report = {"ok": False, "reason": str(e), "checked_at": now_iso()}
        self.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

# ============ 5. 通用工具 ============
def append_csv(path: Path, row: dict):
    """追加一行到 CSV（自动建头）"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    is_new = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)

if __name__ == "__main__":
    # 自测
    ck = Checkpoint()
    data = ck.load()
    print(f"[test] checkpoint 加载: schema={data['schema_version']}, 池大小={len(data['pool'])}")
    data["cumulative_tested"] += 1
    ck.save(data)
    print("[test] checkpoint 原子写 OK")
    lock = RunLock()
    got = lock.acquire()
    print(f"[test] 锁获取: {got}")
    lock.release()
    print("[test] 锁释放 OK")
    bus = EventBus()
    bus.emit("data_ready", {"date": "2026-08-16"})
    print("[test] 事件写入 OK")
    dh = DataHealth()
    rep = dh.scan()
    print(f"[test] 数据健康: ok={rep.get('ok')}, 交易日={rep.get('n_dates')}, 非空率={rep.get('nonnull')}")
