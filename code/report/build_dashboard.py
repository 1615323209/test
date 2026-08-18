#!/usr/bin/env python3
"""改造2.0 5.1：build_dashboard.py 生成自包含单文件 loop_state/dashboard.html
零外部依赖/零CDN/内联JSON数据/SVG图表（漏斗矩形+趋势polyline）/只读（可溯源run_log.jsonl）
六个视图：今日漏斗 / 因子池 / 时间线 / 成本 / 门禁健康 / 实盘验证
"""
import json, sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
STATE = Path(r"D:\quant_data\loop_state")
LOG = STATE / "run_log.jsonl"

def _load_events():
    if not LOG.exists():
        return []
    out = []
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out

def _gather():
    evs = _load_events()
    # 今日漏斗（run_end 聚合）
    runs = [e for e in evs if e.get("stage") == "run_end"]
    today_runs = [e for e in runs if e.get("ts", "").startswith(datetime.now().strftime("%Y-%m-%d"))]
    # 因子池（checkpoint）
    pool = []
    try:
        ck = json.loads((STATE / "checkpoint.json").read_text(encoding="utf-8"))
        pool = ck.get("pool", [])
    except Exception:
        pass
    # active_factors
    af = {}
    try:
        from paper.active_factors import load_data
        af = load_data()
    except Exception:
        pass
    # 门禁健康
    try:
        dash = json.loads((STATE / "dashboard.json").read_text(encoding="utf-8"))
    except Exception:
        dash = {}
    import csv as _csv
    trades = []
    tp = Path(r"D:\quant_data\live_trades.csv")
    if tp.exists():
        try:
            trades = list(_csv.DictReader(open(tp, encoding="utf-8")))
        except Exception:
            trades = []
    return {"evs": evs, "today_runs": today_runs, "pool": pool, "af": af, "dash": dash,
            "trades": trades}  # 改造2.0补：实盘成交（L4 视图）

def build_dashboard_html():
    data = _gather()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 内联数据（前端所有数字都源自这里，可溯源）
    payload = {
        "generated_at": now,
        "today_runs": len(data["today_runs"]),
        "latest_health": data["dash"].get("health_score"),
        "pool_size": data["dash"].get("pool_size"),
        "enabled": data["dash"].get("enabled"),
        "avg_half_life": data["dash"].get("avg_half_life"),
        "pool": [{ "name": p.get("name"), "status": p.get("status"),
                   "icir": (p.get("ic_metrics") or {}).get("icir"),
                   "half_life": p.get("half_life") } for p in data["pool"]],
        "active_factors": [{"name": f.get("name"), "status": f.get("status"),
                            "weight": f.get("weight"), "t_nw_design": f.get("t_nw_design")}
                           for f in data["af"].get("factors", [])],
        "run_timeline": [{"ts": r.get("ts"), "exit": r.get("exit_reason"),
                          "dur": r.get("duration_sec"), "enabled": r.get("enabled"),
                          "pool": r.get("pool")} for r in data["today_runs"]],
        "reject": _gate_dist(),
        # 改造2.0补：实盘验证视图（从 live_trades 归因聚合每因子成交样本/实盘均值/偏差）
        "live_validation": _live_validation(data["trades"], data["af"]),
    }
    html = _template(payload)
    (STATE / "dashboard.html").write_text(html, encoding="utf-8")
    return str(STATE / "dashboard.html")


def _live_validation(trades, af):
    """实盘验证：按因子归因聚合 live_trades（factor 列 | 分隔），算每因子成交样本/实盘均值/偏差"""
    if not trades:
        return {"n_trades": 0, "factors": [], "has_data": False}
    from collections import defaultdict
    # 每因子样本
    per_factor = defaultdict(list)
    n_total = 0
    for t in trades:
        f_col = (t.get("factor") or "").strip()
        if not f_col or f_col == "manual":
            continue  # 手工加仓无归因，跳过
        try:
            pnl = float(t.get("pnl_pct", 0)) * 100.0  # 小数→百分比
        except (TypeError, ValueError):
            continue
        for fname in [x.strip() for x in f_col.split("|") if x.strip()]:
            per_factor[fname].append(pnl)
            n_total += 1
    # active_factors 里的预期（l4_expected）
    expected_map = {}
    for f in af.get("factors", []):
        expected_map[f.get("name")] = f.get("l4_expected", 0.0)
    import statistics as _stat
    factors = []
    for fname, pnls in per_factor.items():
        n = len(pnls)
        realized = round(sum(pnls) / n, 2) if n else 0.0
        exp = expected_map.get(fname, 0.0)
        denom = max(abs(exp), 2.0)
        dev = round((realized - exp) / denom * 100, 1) if denom else 0.0
        factors.append({"factor": fname, "n": n, "realized_pct": realized,
                        "expected_pct": exp, "deviation_pct": dev})
    return {"n_trades": n_total, "factors": factors, "has_data": True}

def _gate_dist():
    """l1_log 的 gate 分布（死因）"""
    out = {}
    try:
        import csv
        lp = STATE / "l1_log.csv"
        if lp.exists():
            for r in csv.DictReader(open(lp, encoding="utf-8")):
                g = r.get("gate", "")
                if g and g not in ("g0-g4", "?"):
                    out[f"G{g}"] = out.get(f"G{g}", 0) + 1
    except Exception:
        pass
    return out

def _live_table(lv):
    """实盘验证表格行（改造2.0补：无数据时给提示行）"""
    if not lv or not lv.get("has_data"):
        return "<tr><td colspan=5 style='color:#999'>暂无归因成交数据</td></tr>"
    rows = ""
    for x in lv.get("factors", []):
        dev = x.get("deviation_pct")
        badge = "✅" if dev is not None and abs(dev) <= 50 else "⚠️"
        rows += (f"<tr><td>{x['factor']}</td><td>{x['n']}</td>"
                 f"<td>{x['realized_pct']}</td><td>{x['expected_pct']}</td>"
                 f"<td>{badge} {x['deviation_pct']}</td></tr>")
    return rows or "<tr><td colspan=5 style='color:#999'>无</td></tr>"

def _template(p):
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>因子挖掘 Dashboard</title>
<style>
body{{font-family:'Segoe UI',sans-serif;margin:0;padding:16px;background:#f5f6fa;color:#222}}
h1{{font-size:20px}} h2{{font-size:15px;border-bottom:2px solid #ddd;padding-bottom:4px;margin-bottom:8px}}
.card{{background:#fff;border-radius:8px;padding:14px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}
.metric{{font-size:28px;font-weight:bold;color:#1664ff}} .label{{color:#888;font-size:12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{text-align:left;padding:4px 8px;border-bottom:1px solid #eee}}
.badge{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;margin-right:4px}}
.pin{{background:#e8f0fe;color:#1a56db}} .启用{{background:#e6f4ea;color:#188038}}
.灰度{{background:#fef7e0;color:#b26a00}} .观察{{background:#fff4e5;color:#cc6a00}}
.档案{{background:#f3f3f3;color:#666}} .回滚{{background:#fdecea;color:#d93025}} .候选{{background:#e8f0fe;color:#1a56db}}
.note{{color:#888;font-size:12px;margin-top:6px}}
</style></head><body>
<h1>📊 因子挖掘 Dashboard</h1>
<div class="note">生成于 {p['generated_at']}｜今日 {p['today_runs']} 个 run｜数据源 run_log.jsonl（每个数字可溯源）</div>
<div class="grid">
  <div class="card"><div class="label">健康分</div><div class="metric">{p['latest_health'] or '-'}</div></div>
  <div class="card"><div class="label">池大小</div><div class="metric">{p['pool_size'] or 0}</div></div>
  <div class="card"><div class="label">启用因子</div><div class="metric">{p['enabled'] or 0}</div></div>
  <div class="card"><div class="label">平均半衰期</div><div class="metric">{p['avg_half_life'] if p['avg_half_life'] else '-'}</div></div>
</div>

<div class="card"><h2>🎯 今日漏斗（死因分布）</h2>
{_funnel_svg(p['reject'])}
{_funnel_table(p['reject'])}
<div class="note">G2 拒绝占比连续两天 >80% → 生成器质量问题，考虑切信息轴 A1/A4 而非调阈值。</div>
</div>

<div class="card"><h2>🧬 因子池</h2>
<table><tr><th>因子</th><th>状态</th><th>ICIR</th><th>半衰期</th></tr>
{''.join(f"<tr><td>{x['name']}</td><td><span class='badge {x['status']}'>{x['status']}</span></td><td>{x['icir'] if x['icir'] is not None else '-'}</td><td>{x['half_life'] if x['half_life'] else '-'}月</td></tr>" for x in p['pool']) or '<tr><td colspan=4>池为空</td></tr>'}
</table></div>

<div class="card"><h2>⚙️ 实盘因子（active_factors）</h2>
<table><tr><th>因子</th><th>状态</th><th>权重</th><th>t设计</th></tr>
{''.join(f"<tr><td>{x['name']}</td><td><span class='badge {x['status']}'>{x['status']}</span></td><td>{x['weight']}</td><td>{x['t_nw_design']}</td></tr>" for x in p['active_factors']) or '<tr><td colspan=4>无激活因子</td></tr>'}
</table></div>

<div class="card"><h2>🎤 实盘验证（L4·真实成交）</h2>
<div class="note">成交总数 {p['live_validation']['n_trades']} 笔（归因因子）｜数据源 live_trades.csv</div>
<table><tr><th>因子</th><th>成交样本</th><th>实盘均值%</th><th>预期%</th><th>偏差%</th></tr>
{_live_table(p['live_validation'])}
</table>
<div class="note">{'暂无实盘成交——有真实买卖（--from-pick 归因）后此处显示每因子 SPRT 进度与偏差。' if not p['live_validation']['has_data'] else '偏差 = (实盘-预期)/max(|预期|,2%)；同因子共享归因样本需打折看待。'}</div>
</div>

<div class="card"><h2>🕐 Run 时间线（今日）</h2>
{_timeline(p['run_timeline'])}
</div>
<div class="note">前端只读；唯一可写路径 control.json 且需二次确认。页面挂了不影响 loop。</div>
</body></html>"""

def _funnel_svg(reject):
    # 5 级漏斗（G0-G4）矩形 + 文字
    levels = [("G0", reject.get("G0", 0)), ("G1", reject.get("G1", 0)),
              ("G2", reject.get("G2", 0)), ("G3", reject.get("G3", 0)),
              ("G4", reject.get("G4", 0))]
    maxv = max([v for _, v in levels] or [1])
    w = 300; h = 30; pad = 10
    rects = []
    for i, (name, v) in enumerate(levels):
        y = i * (h + pad)
        rw = int(w * (1 - i * 0.12))
        x = (w - rw) // 2
        rects.append(f'<rect x="{x}" y="{y}" width="{rw}" height="{h}" rx="4" fill="#e8f0fe" stroke="#1664ff"/>'
                     f'<text x="{w//2}" y="{y+h//2+4}" text-anchor="middle" font-size="12">{name} 拒{v}</text>')
    return f'<svg width="{w}" height="{len(levels)*h + (len(levels)-1)*pad}" viewBox="0 0 {w} {len(levels)*h+(len(levels)-1)*pad}" style="background:#fafafa;border-radius:6px">{"".join(rects)}</svg>'

def _funnel_table(reject):
    if not reject:
        return "<div class='note'>暂无拦截数据</div>"
    rows = "".join(f"<tr><td>{g}</td><td>{v}</td></tr>" for g, v in sorted(reject.items(), key=lambda x: -x[1]))
    return f"<table><tr><th>门</th><th>拒绝数</th></tr>{rows}</table>"

def _timeline(runs):
    if not runs:
        return "<div class='note'>今日暂无 run</div>"
    rows = "".join(f"<tr><td>{r['ts'][11:16]}</td><td>{r['exit']}</td><td>{r['dur']}s</td>"
                   f"<td>启用{r['enabled']}/池{r['pool']}</td></tr>" for r in runs)
    return f"<table><tr><th>时间</th><th>退出</th><th>耗时</th><th>产出</th></tr>{rows}</table>"

if __name__ == "__main__":
    pth = build_dashboard_html()
    print("dashboard.html 已生成:", pth)
