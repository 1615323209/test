#!/usr/bin/env python3
"""量化因子挖掘四层 loop 总控 —— 宪法执行引擎

用法:
    python factor_mining_loop.py --batch 2          # 跑 2 批（每批 5 候选）
    python factor_mining_loop.py --smoke            # 1 批 1 候选快速验证
    python factor_mining_loop.py --l4-only          # 只跑 L4 实盘验证评估
    python factor_mining_loop.py --status           # 查看当前状态

流程（宪法第二章）:
    数据健康门禁 → 事件处理 → L1 生成 → L2 筛选 → 入池
    → 触发 L3 回测 → 判定启用/回滚 → 更新权重 → dashboard
"""
import argparse, json, os, sys, time
from pathlib import Path

# stdout 无缓冲（cron/超时场景可见实时输出）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

DATA_DIR = Path(r"D:\quant_data")
STATE_DIR = DATA_DIR / "loop_state"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loop.factor_loop_infra import Checkpoint, RunLock, EventBus, DataHealth, append_csv
from loop.factor_loop_l1l2 import load_train_df, load_full_ic_cols, l1_refine, l2_pipeline, validate_expr
from loop import factor_loop_l1l2 as L1L2
from loop.factor_loop_l3l4 import l3_evaluate, calc_weights, update_dashboard, BASELINE
from loop.llm_factor_synth import load_deepseek_key, build_dict

def run_batch(ck, api_key, n_cands=5, smoke=False, verbose=True, ck_mgr=None):
    """L2 批次：生成 n_cands 个候选 → L1 → L2 → 入池（每候选处理完即保存检查点）"""
    if smoke:
        n_cands = 1
    batch_id = ck.get("batch_id", 0) + 1
    pool = ck.get("pool", [])
    pool_exprs = [{"expr": p["expr"], "expr_hash": p.get("expr_hash"), "name": p["name"]} for p in pool]
    # 主题配额（L1 文档第七章反冗余）：注入池内因子主题，要求从欠代表主题出题
    pool_topics = "\n".join(
        f"- {p['name']}: {str(p.get('hypothesis') or p.get('logic') or '')[:60]}"
        for p in pool[-10:]) or "（池为空，自由选择主题）"
    ddict = build_dict()
    added = 0
    for idx in range(n_cands):
        if verbose:
            print(f"  [L1] batch{batch_id} 候选{idx}...")
        cand = l1_refine(batch_id, idx, api_key, ddict, max_rounds=3, smoke=smoke, pool_topics=pool_topics)
        if cand is None:
            append_csv(STATE_DIR / "l1_log.csv", {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "batch": batch_id, "idx": idx, "status": "L1失败"})
            if ck_mgr: ck_mgr.save(ck)
            continue
        append_csv(STATE_DIR / "l1_log.csv", {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "batch": batch_id, "idx": idx, "name": cand["name"],
                   "icir": cand["ic_metrics"].get("icir"),
                   "t_nw_design": cand.get("t_nw_design"), "t_nw_holdout": cand.get("t_nw_holdout"),
                   "icir_tradable": cand.get("icir_tradable"),
                   "n_peek": cand.get("n_peek"), "role": cand.get("role", "score"),
                   "gate": "g0-g4", "status": "L1通过"})
        ok, why, cand2 = l2_pipeline(cand, pool_exprs, api_key)
        if not ok:
            append_csv(STATE_DIR / "l2_log.csv", {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "batch": batch_id, "name": cand["name"], "status": "拒绝", "reason": why})
            if ck_mgr: ck_mgr.save(ck)
            continue
        cand2["status"] = "候选"
        cand2["added_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if cand2.get("archived_only"):
            cand2["status"] = "档案"  # role != score：只登记档案，不进打分池、不触发 L3（L2 文档第七章）
        # IC 序列外置（L1 文档缺陷 14）：不随 checkpoint 膨胀，落盘 ic_series/{hash}.parquet
        ic_series = (cand2.get("ic_metrics") or {}).pop("_ic_series", None)
        if ic_series:
            try:
                import polars as _pl
                sdir = Path(r"D:\quant_data\loop_state\ic_series")
                sdir.mkdir(parents=True, exist_ok=True)
                _pl.DataFrame({"ic": list(ic_series)}).write_parquet(sdir / f"{cand2['expr_hash']}.parquet")
                cand2["ic_series_path"] = f"loop_state/ic_series/{cand2['expr_hash']}.parquet"
            except Exception:
                pass
        pool.append(cand2)
        pool_exprs.append({"expr": cand2["expr"], "expr_hash": cand2["expr_hash"], "name": cand2["name"]})
        added += 1
        append_csv(STATE_DIR / "l2_log.csv", {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "batch": batch_id, "name": cand2["name"], "status": "入池",
                   "icir": cand2["ic_metrics"].get("icir"),
                   "half_life": cand2.get("half_life"), "reason": ""})
        if ck_mgr: ck_mgr.save(ck)
    ck["batch_id"] = batch_id
    ck["pool"] = pool
    return added

def run_l3(ck, verbose=True):
    """L3 回测评估：候选池 → 训练/验证 → 启用/回滚"""
    pool = ck["pool"]
    candidates = [p for p in pool if p["status"] == "候选"]
    if not candidates:
        if verbose:
            print("  [L3] 无候选因子，跳过")
        return 0
    N = ck.get("cumulative_tested", 0)
    enabled_names = [p["name"] for p in pool if p["status"] in ("启用", "实盘确认")]
    # 已启用因子注入（保持它们在打分中）
    extra = {}
    for p in pool:
        if p["status"] in ("启用", "实盘确认"):
            w = p.get("weight", 0.05)
            extra[p["name"]] = (f"({p['expr']}).rank().over('日期')", w)
    n_enabled = 0
    for cand in candidates:
        status, report = l3_evaluate(cand, N, extra_factors=extra, verbose=verbose)
        N += 1
        report["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        append_csv(STATE_DIR / "backtest_history.csv", {
            "ts": report["ts"], "name": cand["name"], "N": N,
            "threshold": report.get("threshold"), "status": status,
            "train_gain": report.get("train_gain"), "valid_gain": report.get("valid_gain"),
            "valid_n": report.get("valid", {}).get("n_trades"),
            "train_ret": report.get("train", {}).get("total_ret_pct"),
            "valid_ret": report.get("valid", {}).get("total_ret_pct"),
            "reason": report.get("reason", ""),
        })
        if status == "启用":
            cand["status"] = "启用"
            cand["degraded_enabled"] = report.get("valid_degraded", False)
            if report.get("valid_degraded"):
                cand["l4_expected"] = 0.0
            n_enabled += 1
        else:
            cand["status"] = "回滚"
            cand["rollback_reason"] = report.get("reason", "")
    # 重新计算所有启用因子权重（总权重约束 + 迭代剔除）
    enabled = [p for p in pool if p["status"] in ("启用", "实盘确认")]
    weights = calc_weights(enabled)
    for p in pool:
        if p["name"] in weights:
            p["weight"] = weights[p["name"]]
            p["status"] = "启用"  # 从候选转启用后保持
    ck["cumulative_tested"] = N
    return n_enabled

def run_l4(ck, paper_file=None, verbose=True):
    """L4 实盘验证评估（真实成交驱动；L4 文档缺陷 3 修复：不再默认读模拟盘 paper_trades.csv）"""
    pool = ck["pool"]
    trades, src = [], "none"
    live_trades = DATA_DIR / "live_trades.csv"
    if paper_file:
        import csv
        with open(paper_file, newline="", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        src = str(paper_file)
    elif live_trades.exists():
        import csv
        with open(live_trades, newline="", encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
        src = "live_trades.csv(真实成交)"
    else:
        # 无成交记录：读真实持仓（仅登记状态，不做 SPRT 判定）
        lp = DATA_DIR / "live_positions.json"
        if lp.exists():
            try:
                pos = json.loads(lp.read_text(encoding="utf-8"))
                src = f"live_positions.json({len(pos)}只持仓, 暂无成交记录)"
            except Exception:
                pos = []
                src = "live_positions.json(读取失败)"
    enabled = [p for p in pool if p["status"] in ("启用", "实盘确认", "观察")]
    from factor_loop_l3l4 import l4_evaluate
    for p in enabled:
        ft = [t for t in trades if t.get("factor") == p["name"]]
        if not ft:
            # 无该因子成交记录：登记观察，不判定
            rep = {"n": 0, "reason": f"无真实成交记录 (数据源: {src})"}
        else:
            status, rep = l4_evaluate(p, ft, verbose=verbose)
        rep["name"] = p["name"]
        rep["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rep["data_src"] = src
        append_csv(STATE_DIR / "l4_log.csv", {**rep, "ts": rep["ts"]})
        if rep.get("n", 0) == 0:
            continue
        if status == "实盘确认":
            p["status"] = "实盘确认"
        elif status == "回滚":
            p["status"] = "回滚"
            p["rollback_reason"] = "L4实盘回滚"
        else:
            p["status"] = "观察"
    return len([p for p in pool if p["status"] == "实盘确认"])

def gate_audit(verbose=True):
    """工程保障门禁有效性自检（工程保障.md 第六章）：
    统计 L1/L2 通过率，出现"恒 100% 从未拒绝"即告警（假门禁比没有门禁更危险）"""
    import csv as _csv
    alerts = []
    for name, fname, ok_status in [("L1", "l1_log.csv", "L1通过"), ("L2", "l2_log.csv", "入池")]:
        p = STATE_DIR / fname
        if not p.exists():
            continue
        try:
            rows = list(_csv.DictReader(open(p, encoding="utf-8")))
        except Exception:
            continue
        if len(rows) >= 20:
            ok_n = sum(1 for r in rows if r.get("status") == ok_status)
            rate = ok_n / len(rows)
            if rate == 1.0:
                alerts.append(f"{name} 通过率恒 100%（{len(rows)}次从未拒绝）→ 假门禁风险")
            elif verbose:
                print(f"[audit] {name} 通过率 {rate:.0%} ({ok_n}/{len(rows)})")
    if alerts:
        print("[audit] ⚠️ " + " | ".join(alerts))
    return alerts

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=1, help="跑几批（每批5候选）")
    ap.add_argument("--n-cands", type=int, default=5, help="每批候选数（cron 用小值控制在超时内）")
    ap.add_argument("--smoke", action="store_true", help="快速验证：1批1候选")
    ap.add_argument("--l4-only", action="store_true", help="只跑 L4")
    ap.add_argument("--status", action="store_true", help="查看状态")
    args = ap.parse_args()

    ck_mgr = Checkpoint()
    ck = ck_mgr.load()
    if args.status:
        print(f"状态: batch={ck.get('batch_id')} 池={len(ck.get('pool',[]))} "
              f"累计测试N={ck.get('cumulative_tested')} API成本={ck.get('api_cost')}")
        for p in ck["pool"]:
            print(f"  {p['name']:30} {p['status']:6} ICIR={p['ic_metrics'].get('icir'):+.3f} "
                  f"权重={p.get('weight','—')} 半衰期={p.get('half_life')}月")
        return

    lock = RunLock()
    if not lock.acquire():
        return
    try:
        # 数据健康门禁
        dh = DataHealth()
        health = dh.scan()
        if not health.get("ok", False):
            print(f"[gate] 数据健康不通过: {health.get('reason', health)}")
            return
        print(f"[gate] 数据健康 OK ({health['n_dates']} 交易日)")
        bus = EventBus()
        # 事件处理
        pending = bus.drain_pending()
        for ev in pending:
            print(f"[event] 处理: {ev.get('event')}")
        api_key = load_deepseek_key()
        if args.l4_only:
            run_l4(ck)
        else:
            for b in range(args.batch):
                added = run_batch(ck, api_key, n_cands=args.n_cands, smoke=args.smoke, ck_mgr=ck_mgr)
                print(f"[L2] 批 {ck['batch_id']}: 新增 {added} 个候选")
                # 批完成后触发 L3（候选≥1 且非 smoke 时）
                if not args.smoke and added > 0:
                    n = run_l3(ck)
                    print(f"[L3] 启用 {n} 个因子")
                    ck_mgr.save(ck)
            if args.smoke:
                # smoke 模式也走一次 L3 验证链路
                run_l3(ck)
        # 保存检查点 + dashboard
        ck_mgr.save(ck)
        hist = []
        hl = STATE_DIR / "backtest_history.csv"
        if hl.exists():
            import csv
            with open(hl, newline="", encoding="utf-8") as f:
                hist = list(csv.DictReader(f))
        l4 = []
        ll = STATE_DIR / "l4_log.csv"
        if ll.exists():
            import csv
            with open(ll, newline="", encoding="utf-8") as f:
                l4 = list(csv.DictReader(f))
        dash = update_dashboard(ck["pool"], hist, l4)
        print(f"[dash] 健康分={dash['health_score']} 池={dash['pool_size']} 启用={dash['enabled']}")
        gate_audit()  # 门禁有效性自检（工程保障.md）
        print("[done] loop 完成")
    finally:
        lock.release()

if __name__ == "__main__":
    main()
