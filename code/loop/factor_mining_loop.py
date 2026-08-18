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

def run_batch(ck, api_key, n_cands=5, smoke=False, verbose=True, ck_mgr=None, budget_sec=None, n_batch=1):
    """L2 批次：生成 n_cands 个候选 → L1 → L2 → 入池（每候选处理完即保存检查点）
    改造2.0 3.1：budget_sec 预算驱动——每候选检查剩余预算，不足单候选历史 P80 耗时则 stop（exit_reason=budget）"""
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
    exit_reason = "done"
    # 改造2.0 3.1：预算驱动——单候选历史 P80 耗时（默认 240s，后续由 run_summary 校准）
    est_per_cand = ck.get("cand_p80_sec", 240)
    t_run = time.time()
    for idx in range(n_cands):
        # 预算检查：不足再跑一个候选的预估耗时 → 停止并标记 budget
        if budget_sec and (time.time() - t_run) + est_per_cand > budget_sec:
            print(f"  [预算] 剩余预算不足单候选 P80({est_per_cand}s)，停止取新候选")
            exit_reason = "budget"
            break
        if verbose:
            print(f"  [L1] batch{batch_id} 候选{idx}...")
        cand = l1_refine(batch_id, idx, api_key, ddict, max_rounds=3, smoke=smoke, pool_topics=pool_topics, n_batch=n_batch)
        if cand is None:
            append_csv(STATE_DIR / "l1_log.csv", {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "batch": batch_id, "idx": idx, "status": "L1失败"})
            # 改造 3.4：三轮修正全失败的候选也消耗窥视次数（llm 调用过，不能白计）
            ck["peek_spent"] = ck.get("peek_spent", 0) + 3
            if ck_mgr: ck_mgr.save(ck)
            continue
        gms = cand.get("gate_ms") or {}
        append_csv(STATE_DIR / "l1_log.csv", {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "batch": batch_id, "idx": idx, "name": cand["name"],
                   "icir": cand["ic_metrics"].get("icir"),
                   "t_nw_design": cand.get("t_nw_design"), "t_nw_holdout": cand.get("t_nw_holdout"),
                   "icir_tradable": cand.get("icir_tradable"),
                   "n_peek": cand.get("n_peek"), "role": cand.get("role", "score"),
                   "gate": cand.get("gate_hit", "?"), "status": "L1通过",
                   "ms_g0": gms.get("g0"), "ms_g1": gms.get("g1"),
                   "ms_g2": gms.get("g2"), "ms_g3": gms.get("g3"), "ms_g4": gms.get("g4"),
                   "g1_fail_open": 1 if cand.get("g1_fail_open") else 0})
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
        # IC 序列外置由漏斗负责（factor_loop_gates._save_ic_series，改造 3.2），主控不再空转
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
    ck["last_exit_reason"] = exit_reason  # 改造2.0 3.1：预算驱动标记
    return added

def run_l3(ck, verbose=True):
    """L3 回测评估：候选池 → 训练/验证 → 启用/回滚"""
    pool = ck["pool"]
    candidates = [p for p in pool if p["status"] == "候选"]
    if not candidates:
        if verbose:
            print("  [L3] 无候选因子，跳过")
        return 0
    # 改造 3.4：N 口径改 Σn_peek（含三轮修正全失败的 peek_spent）
    # l3_evaluate 内部 = cumulative_tested + n_peek；这里维护累计基线
    N = ck.get("cumulative_tested", 0) + ck.get("peek_spent", 0)
    enabled_names = [p["name"] for p in pool if p["status"] in ("启用", "实盘确认")]
    # 已启用因子注入（改造2.0 2.1：用 active_factors.json 单一真相源，替代从 pool 现场拼；
    # 含灰度权重系数 0.5×target——新因子不满权重上实盘）
    extra = {}
    try:
        from paper.active_factors import load_data
        af = load_data()
        for f in af.get("factors", []):
            st = f.get("status", "")
            if st in ("启用", "实盘确认", "pin"):
                extra[f["name"]] = (f"({f['expr']}).rank().over('日期')", f.get("weight", 0.02))
            elif st == "灰度":
                # 灰度：0.5×weight_target
                w = f.get("weight_target", f.get("weight", 0.02))
                extra[f["name"]] = (f"({f['expr']}).rank().over('日期')", 0.5 * w)
    except Exception:
        # 回退：从 pool 拼（active_factors 不可用时保留旧行为）
        for p in pool:
            if p["status"] in ("启用", "实盘确认"):
                w = p.get("weight", 0.05)
                extra[p["name"]] = (f"({p['expr']}).rank().over('日期')", w)
    n_enabled = 0
    for cand in candidates:
        peek = cand.get("n_peek", 1)
        status, report = l3_evaluate(cand, N, extra_factors=extra, verbose=verbose)
        N += peek  # 改造 3.4：按 n_peek 累加（原来 N+=1 低估修正轮窥视）
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
            # 改造2.0缺陷2+2.3：l4_expected 必须由 L3 写入（否则 μ1=μ0=0 → SPRT 恒观察）
            # 正常启用：预期单笔收益 = 该因子注入后 train 回测总收益 / 笔数
            # 降级启用（验证集<20笔）：写 0.0 保守口径
            if report.get("valid_degraded"):
                cand["l4_expected"] = 0.0
            else:
                t_ret = train_m_per_cand(report.get("train"))
                cand["l4_expected"] = t_ret
            n_enabled += 1
            # 改造2.0 2.1灰度规则：新启用因子以 0.5×target + 灰度 进 active_factors
            try:
                from paper.active_factors import set_factor
                wt = cand.get("weight", 0.04) or 0.04
                set_factor(cand["name"],
                           expr=cand["expr"], weight=0.5 * wt, weight_target=wt,
                           status="灰度", origin=f"loop_b{ck.get('batch_id')}",
                           since=time.strftime("%Y-%m-%d"),
                           t_nw_design=cand.get("t_nw_design"), t_nw_holdout=cand.get("t_nw_holdout"),
                           icir_tradable=cand.get("icir_tradable"),
                           half_life=cand.get("half_life"), l4_expected=cand.get("l4_expected"),
                           ic_series_path=cand.get("ic_series_path"))
            except Exception as e:
                print(f"  [active_factors] 写入失败: {e}")
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

def train_m_per_cand(train_m):
    """改造2.0缺陷2：单笔预期收益 = train 回测总收益 / 笔数（百分比）"""
    if not train_m:
        return 0.0
    n = train_m.get("n_trades", 1) or 1
    return round(float(train_m.get("total_ret_pct", 0.0)) / n, 3)

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
    """工程保障门禁有效性自检（工程保障.md 第六章，改造 C23 三类告警齐备）：
    1. 恒 100% 通过（假门禁） 2. 关键指标恒空 3. 兜底触发率>10%（G1 fail_open）"""
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
        if len(rows) < 20:
            continue
        ok_n = sum(1 for r in rows if r.get("status") == ok_status)
        rate = ok_n / len(rows)
        # 属性1：恒 100% 通过
        if rate == 1.0:
            alerts.append(f"{name} 通过率恒 100%（{len(rows)}次从未拒绝）→ 假门禁风险")
        elif verbose:
            print(f"[audit] {name} 通过率 {rate:.0%} ({ok_n}/{len(rows)})")
        # 属性2：关键指标恒空（L1 的 t_nw_design 全空 → 门禁未真正计算）
        if name == "L1":
            tnw = [r for r in rows if r.get("t_nw_design") not in (None, "")]
            if not tnw and len(rows) >= 20:
                alerts.append("L1 t_nw_design 恒空 → 主周期门从未真正计算")
        # 属性3：兜底触发率（L1 的 g1_fail_open）
        if name == "L1":
            fo = sum(1 for r in rows if str(r.get("g1_fail_open", "0")) in ("1", "1.0"))
            if rows and fo / len(rows) > 0.1:
                alerts.append(f"L1 G1 fail_open 触发率 {fo/len(rows):.0%}>10% → 抽样兜底过高")
    if alerts:
        print("[audit] ⚠️ " + " | ".join(alerts))
    return alerts


def _make_push_card(run_id, ck, dash, exit_reason, alerts, duration_sec=0, verbose=True):
    """改造2.0 4.2：生成 push_card.md（cron 直接 cat 的最终推送内容）
    含：漏斗摘要 / 入池 / 启用 / 池状态 / 告警 / 成本。空转折叠一行。"""
    from report.run_reporter import write_push_card, read_events
    import datetime as _dt
    # 本 run 的 gate 事件聚合（从 l1_log.csv：每候选一行的 gate 命中/拒绝；run_log 无逐候选 gate 行）
    g_rej = {}
    gen_n = 0
    try:
        import csv as _csv
        lp = STATE_DIR / "l1_log.csv"
        if lp.exists():
            for r in _csv.DictReader(open(lp, encoding="utf-8")):
                gate = r.get("gate", "")
                # batch 匹配本 run（l1_log 有 batch 字段，但 run_id 无直接关联；退化：聚合本 run 期间新增）
                if gate and gate != "g0-g4" and gate != "?":
                    g_rej[f"g{gate}"] = g_rej.get(f"g{gate}", 0) + 1
                if r.get("status") == "L1通过":
                    gen_n += 1
    except Exception:
        pass
    # 空转判断：无新增池、无启用、无告警
    delta_pool = dash["pool_size"]
    n_enabled = dash["enabled"]
    is_idle = (delta_pool <= 0 and n_enabled <= 0 and not alerts)
    pool = ck.get("pool", [])
    n_gray = sum(1 for p in pool if p.get("status") == "灰度")
    n_watch = sum(1 for p in pool if p.get("status") == "观察")
    n_cls = sum(1 for p in pool if p.get("status") == "候选")
    n_arch = sum(1 for p in pool if p.get("status") == "档案")
    time_hdr = _dt.datetime.now().strftime("%H:%M")
    if is_idle:
        # 空转 run：折叠成一行（感知不疲劳）
        line = f"【{time_hdr}】{run_id} 空转：生成 {gen_n} 全部止于漏斗（最佳 t_NW 见 dashboard）｜健康 {dash['health_score']}"
        body = line
    else:
        head = f"【因子挖掘 {time_hdr}】{run_id} · {'预算内正常结束' if exit_reason == 'done' else '预算上限'} · {duration_sec}s"
        # 漏斗摘要
        gline = "生成 {gen_n} → " + " → ".join(f"g{g.replace('g','')}拒{v}" for g, v in g_rej.items()) if g_rej else f"生成 {gen_n} → 全过/无拦截记录"
        lines = [head, gline]
        # 新增启用
        if n_enabled > 0:
            lines.append(f"L3 启用 {n_enabled} 个因子 → active_factors（灰度 0.5×权重）")
        lines.append(f"池：{len(pool)} 个（启用 {n_enabled} / 灰度 {n_gray} / 观察 {n_watch} / 档案 {n_arch}）｜健康分 {dash['health_score']}")
        body = "\n".join(lines)
        if alerts:
            body += "\n⚠️ " + " | ".join(alerts)
        body += "\n详情：dashboard.html"
    write_push_card(run_id, body.split("\n"))
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=1, help="跑几批（每批5候选）")
    ap.add_argument("--n-cands", type=int, default=5, help="每批候选数（cron 用小值控制在超时内）")
    ap.add_argument("--budget-sec", type=int, default=None, help="改造2.0 3.1：总预算秒数（默认无；cron 用 420）")
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
    # 改造2.0 4.1：run 级事件流（run_start）
    from report.run_reporter import log_event, new_run_id, write_summary, write_push_card, read_events
    run_id = new_run_id()
    log_event("run_start", run_id, batch=args.batch, n_cands=args.n_cands, budget=args.budget_sec)
    t_run_start = time.time()
    try:
        # 数据健康门禁
        dh = DataHealth()
        health = dh.scan()
        if not health.get("ok", False):
            print(f"[gate] 数据健康不通过: {health.get('reason', health)}")
            log_event("alert", run_id, kind="data_health", msg=str(health.get("reason")))
            write_push_card(run_id, ["⚠️ 数据健康不通过，loop 跳过", f"原因: {health.get('reason')}"])
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
                added = run_batch(ck, api_key, n_cands=args.n_cands, smoke=args.smoke,
                                  ck_mgr=ck_mgr, budget_sec=args.budget_sec)
                print(f"[L2] 批 {ck['batch_id']}: 新增 {added} 个候选")
                if added > 0:
                    log_event("l2", run_id, batch=ck["batch_id"], added=added)
                # 批完成后触发 L3（候选≥1 且非 smoke 时）
                if not args.smoke and added > 0:
                    n = run_l3(ck)
                    print(f"[L3] 启用 {n} 个因子")
                    if n > 0:
                        log_event("l3", run_id, enabled=n)
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
        alerts = gate_audit()  # 门禁有效性自检（工程保障.md）
        dur = int(time.time() - t_run_start)
        exit_reason = ck.get("last_exit_reason", "done")
        # 改造2.0 4.2：生成 run_summary + push_card（loop 自己产出结论，cron 只 cat）
        log_event("run_end", run_id, exit_reason=exit_reason, duration_sec=dur,
                  pool=len(ck["pool"]), enabled=dash["enabled"],
                  health=dash["health_score"], alerts=alerts)
        write_summary(run_id, {
            "id": run_id, "duration_sec": dur, "exit_reason": exit_reason,
            "pool_size": len(ck["pool"]), "enabled": dash["enabled"],
            "health_score": dash["health_score"], "alerts": alerts,
            "n_effective": ck.get("cumulative_tested", 0) + ck.get("peek_spent", 0),
        })
        _make_push_card(run_id, ck, dash, exit_reason, alerts, duration_sec=dur)
        print("[done] loop 完成")
    except Exception as e:
        import traceback
        traceback.print_exc()
        log_event("alert", run_id, kind="exception", msg=str(e))
    finally:
        lock.release()

if __name__ == "__main__":
    main()
