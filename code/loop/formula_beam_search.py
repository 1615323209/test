#!/usr/bin/env python3
"""方向三：公式束搜索（beam search）—— L1 因子生成引擎

从算子库组合 polars 表达式，Reward = fwd_5d ICIR，beam search 保留 Top-K。
产物与 LLM 因子同格式（polars 表达式字符串），走同一 L1 体检管线。

用法:
  python -m loop.formula_beam_search --smoke      # 小规模验证（~1分钟）
  python -m loop.formula_beam_search --top-k 30   # 完整跑（~20-30分钟，后台）
输出: D:/quant_data/loop_state/beam_results.json
"""
import sys, os, re, json, argparse, hashlib
from pathlib import Path
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop.factor_loop_l1l2 import calc_multi_ic, l1_ic_metrics, load_train_df, validate_expr, MAIN_HORIZON, HORIZONS

STATE = Path(r"D:\quant_data\loop_state")
OUT = STATE / "beam_results.json"

# ============ 算子/模板库（金融逻辑合理，polars 时序+横截面） ============
BASE_COLS = ['收盘', '成交量', '成交额', 'turnover', 'ret_5d', 'vol_ratio',
             'turn_ratio', 'ma5_dist', 'ma20_dist', 'vol_change_5d']
PARAMS = [5, 10, 20, 60]

def t_price_vs_ma(c, n):      # 价格偏离 n 日均线
    return f"(pl.col('{c}') / pl.col('{c}').rolling_mean({n}).over('股票代码') - 1)"
def t_volatility(c, n):       # n 日波动率（std/mean）
    return f"(pl.col('{c}').rolling_std({n}).over('股票代码') / (pl.col('{c}').rolling_mean({n}).over('股票代码') + 1e-9))"
def t_momentum(c, n):         # n 日动量
    return f"(pl.col('{c}') / pl.col('{c}').shift({n}) - 1).over('股票代码')"
def t_zscore(c, n):           # n 日 z-score 标准化
    return f"((pl.col('{c}') - pl.col('{c}').rolling_mean({n}).over('股票代码')) / (pl.col('{c}').rolling_std({n}).over('股票代码') + 1e-9))"
def t_cs_rank(c):             # 横截面排名（每日全市场）
    return f"pl.col('{c}').rank().over('日期')"
def t_vol_ratio_change(c, n, m):  # 量能短/长均线比
    return f"(pl.col('{c}').rolling_mean({n}).over('股票代码') / (pl.col('{c}').rolling_mean({m}).over('股票代码') + 1e-9))"

TEMPLATES = [t_price_vs_ma, t_volatility, t_momentum, t_zscore]
EXPR_CHARS = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff\.\(\)\[\]'\" ,\+\-\*/%<>=!&|]+")

def build_layer1(smoke=False):
    """层1：基础模板 × 列 × 参数 全枚举"""
    cols = BASE_COLS[:6] if smoke else BASE_COLS
    params = [5, 20] if smoke else PARAMS
    cands = []
    for c in cols:
        for n in params:
            for tpl in TEMPLATES:
                cands.append(tpl(c, n))
        cands.append(t_cs_rank(c))
    for c in (['成交量', '成交额'] if not smoke else ['成交量']):
        for n in [5, 10]:
            for m in [20, 60]:
                cands.append(t_vol_ratio_change(c, n, m))
    return list(dict.fromkeys(cands))  # 保序去重

def expand_beam(beam_exprs, top_k):
    """对 Top 子集扩展：横截面 rank / 与基础列比率 / 二次时序平滑（控制候选量）"""
    new = set()
    for e in beam_exprs[: min(10, len(beam_exprs))]:
        new.add(f"({e}).rank().over('日期')")
        new.add(f"(-({e})).rank().over('日期')")
        for c in BASE_COLS[:6]:
            new.add(f"({e}) / (pl.col('{c}') + 1e-9)")
        for n in [5, 20]:
            new.add(f"({e}).rolling_mean({n}).over('股票代码')")
    return list(new)

def score_expr(expr_str):
    """单候选打分：列名校验 → 受限 eval → L1 完整体检（含滚动稳定性）。
    返回 dict 或 None。l1_ok=True 的候选优先进入 beam。"""
    try:
        ok, why = validate_expr(expr_str)
        if not ok:
            return None
        if not EXPR_CHARS.fullmatch(expr_str):
            return None
        expr = eval(expr_str, {"__builtins__": {}}, {"pl": pl})
        l1_ok, main, l1_why = l1_ic_metrics(expr)
        if not main:
            return None
        return {
            "expr": expr_str,
            "icir": main["icir"], "ic_mean": main["ic_mean"],
            "ic_pos_pct": main["ic_pos_pct"],
            "ic_all": {k: (v["icir"] if v else None) for k, v in
                       (calc_multi_ic(expr) or {}).items()},
            "l1_ok": l1_ok, "l1_why": l1_why,
            "hash": hashlib.md5(expr_str.encode()).hexdigest()[:12],
        }
    except Exception:
        return None

def score_all(cands, top_k, verbose=True):
    """批量打分：过 L1 的优先，其次 |ICIR|。返回 Top-K"""
    scored = []
    for i, e in enumerate(cands):
        r = score_expr(e)
        if r:
            scored.append(r)
        if verbose and (i + 1) % 50 == 0:
            print(f"  已打分 {i+1}/{len(cands)}，有效 {len(scored)}，过L1 {sum(1 for x in scored if x['l1_ok'])}")
    scored.sort(key=lambda x: (x["l1_ok"], abs(x["icir"])))
    return scored[:top_k]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=30, help="beam 宽度")
    ap.add_argument("--top-n", type=int, default=10, help="最终输出数量")
    ap.add_argument("--depth", type=int, default=2, help="扩展深度")
    ap.add_argument("--smoke", action="store_true", help="小规模验证（depth 强制 1）")
    args = ap.parse_args()
    if args.smoke:
        args.depth = min(args.depth, 1)

    print(f"=== 公式束搜索 (top_k={args.top_k}, depth={args.depth}, smoke={args.smoke}) ===")
    print("[1/3] 构建层1候选...")
    layer1 = build_layer1(args.smoke)
    print(f"  层1候选数: {len(layer1)}")

    print("[2/3] 层1打分（Reward=fwd_5d ICIR）...")
    beam = score_all(layer1, args.top_k)
    print(f"  层1 Top-{args.top_k} 完成，最佳 |ICIR|={abs(beam[0]['icir']) if beam else 0:.3f}")

    if args.depth >= 2 and beam:
        print("[3/3] beam 扩展...")
        expanded = expand_beam([b["expr"] for b in beam], args.top_k)
        print(f"  扩展候选数: {len(expanded)}")
        beam = score_all(expanded, args.top_k)
        print(f"  扩展后 Top-{args.top_k} 完成，最佳 |ICIR|={abs(beam[0]['icir']) if beam else 0:.3f}")

    # 输出
    STATE.mkdir(exist_ok=True)
    results = [{"rank": i + 1, **b} for i, b in enumerate(beam[:args.top_n])]
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== Top {len(results)} 公式（已存 {OUT}） ===")
    for r in results:
        h = r["ic_all"].get(MAIN_HORIZON)
        mark = "✅过L1" if r["l1_ok"] else f"❌{r['l1_why'][:30]}"
        print(f"  #{r['rank']} |ICIR|={abs(r['icir']):.3f} IC={r['ic_mean']:.4f} 同号段={r['ic_pos_pct']:.0f}% {mark}")
        print(f"      {r['expr']}")

if __name__ == "__main__":
    main()
