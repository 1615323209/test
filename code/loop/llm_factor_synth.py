#!/usr/bin/env python3
"""LLM 因子合成（方向一：LLM-based Synthesis）

Prompt → LLM 生成 Polars 因子公式 → 自动算 IC → 反馈修正（≤3轮）

用法:
    python llm_factor_synth.py                # 生成 5 个因子, 3 轮修正
    python llm_factor_synth.py --n 3 --rounds 2
    python llm_factor_synth.py --smoke        # 快速验证: 1 个因子 1 轮

依赖: polars, pandas, numpy, requests, pyyaml
数据: D:\\quant_data\\ic_data.parquet (schema 自动生成数据字典)
"""
import os, sys, json, re, time, argparse
from pathlib import Path
import polars as pl
import pandas as pd
import numpy as np
import requests

# ---------- 配置 ----------
IC_DATA = Path(os.environ.get("QUANT_DATA", r"D:\quant_data")) / "ic_data.parquet"
OUT_DIR = Path(os.environ.get("QUANT_DATA", r"D:\quant_data"))
HORIZON = "fwd_5d"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
REASONING = True  # reasoning_effort=high

# ---------- 1. DeepSeek 配置（从 Hermes config 读取真实 key） ----------
def load_deepseek_key():
    """从 Hermes config.yaml 的 custom_providers 中取 deepseek 官方 key"""
    import yaml
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "config.yaml",
        Path(os.environ.get("APPDATA", "")) / "Local" / "hermes" / "config.yaml",
        Path.home() / ".hermes" / "config.yaml",
    ]
    for cfg_path in candidates:
        if not cfg_path.exists():
            continue
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for cp in cfg.get("custom_providers", []) or []:
            if isinstance(cp, dict) and cp.get("name") == "deepseek" and "api.deepseek.com" in str(cp.get("base_url", "")):
                return cp["api_key"]
        # model.api_key 兜底
        m = cfg.get("model", {})
        if m.get("api_key"):
            return m["api_key"]
    raise SystemExit("找不到 DeepSeek API key（config.yaml custom_providers.deepseek.api_key）")

# ---------- 2. 数据字典 ----------
def build_dict():
    s = pl.scan_parquet(IC_DATA).collect_schema()
    desc = {
        "日期": "交易日", "股票代码": "股票代码",
        "收盘": "当日收盘价",
        "ret_1d": "当日收益", "ret_5d": "5日累计收益", "ret_10d": "10日累计收益", "ret_20d": "20日累计收益",
        "vol_5d": "5日年化波动率", "vol_10d": "10日年化波动率", "vol_20d": "20日年化波动率",
        "ma_5": "5日均线", "ma_10": "10日均线", "ma_20": "20日均线", "ma_60": "60日均线",
        "ma5_dist": "收盘偏离MA5", "ma20_dist": "收盘偏离MA20",
        "ma5_ma20_cross": "MA5金叉MA20(0/1)", "ma5_ma20_dead": "MA5死叉MA20(0/1)",
        "vol_ratio": "量比(5日基准)", "vol_ratio_20": "量比(20日基准)", "vol_change_5d": "5日量能变化",
        "turn_ma5": "5日平均换手率", "turn_ma20": "20日平均换手率", "turn_ratio": "换手率比",
        "atr_14": "14日ATR", "atr_ratio": "ATR相对收盘比",
        "high_20d": "20日最高", "low_20d": "20日最低", "high_60d": "60日最高", "low_60d": "60日最低",
        "price_pos_20": "20日价格位置(0~1)", "price_pos_60": "60日价格位置(0~1)",
        "macd_dif": "MACD快慢差", "macd_dea": "MACD信号线", "macd_hist": "MACD柱",
        "rsi_14": "14日RSI", "bb_width": "布林带宽度", "bb_pos": "布林带位置(0~1)",
        "limit_up": "涨停标记(0/1)", "limit_down": "跌停标记(0/1)", "is_suspended": "停牌标记(0/1)",
        "up_streak": "连涨天数", "down_streak": "连跌天数",
        # 原始价格列（L1 文档 8.1-A1 前置，2026-08-17 起并入 ic_data）
        "开盘": "当日开盘价（隔夜跳空/日内收益分解用）",
        "最高": "当日最高价（上影线/振幅结构用）",
        "最低": "当日最低价（下影线用）",
        # 扩展因子（L1 文档 8.1-A4，已发表 anomaly）
        "illiq_20": "20日非流动性(Amihud, 越大流动性越差)",
        "vol_corr_5": "5日量价相关",
        "vol_corr_20": "20日量价相关",
        "skew_20": "20日收益偏度(彩票偏好代理)",
        "kurt_20": "20日收益峰度",
        HORIZON: "未来5日收益（预测目标）",
    }
    lines = [f"- {c}: {desc.get(c, 'Float32 因子值')}" for c in s.names()
             if c not in ("日期", "股票代码") and not c.startswith("fwd_")]
    return "\n".join(lines)

# ---------- 3. LLM 调用 ----------
def llm_chat(system, user, api_key, temperature=0.7, seed=None):
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": 8000,
    }
    if seed is not None:
        body["seed"] = seed  # L1 文档第六章第 5 条：请求体带 seed（不再只写进 prompt 文本）
    if REASONING:
        body["reasoning_effort"] = "high"
    r = requests.post(API_URL, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=120)
    r.raise_for_status()
    resp_text = r.json()["choices"][0]["message"]["content"]
    # 内容寻址缓存（L1 文档第六章第 5 条：复现 = 重放缓存，provenance 供审计追溯）
    try:
        cache_path = Path(r"D:\quant_data\loop_state\llm_cache.json")
        ph = hashlib.md5(f"{system}|{user}".encode()).hexdigest()[:16]
        cache = {}
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                cache = {}
        cache[ph] = {"prompt_hash": ph, "model": MODEL, "temperature": temperature,
                     "seed": seed, "response": resp_text[:500],
                     "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return resp_text

# ---------- 4. 解析 LLM 输出（JSON 数组，容忍 markdown 代码块） ----------
def parse_factors(text):
    if not text:
        return []
    # 去掉 ```json ... ``` 代码块标记
    t = re.sub(r"```(?:json)?", "", text)
    # 直接尝试解析
    try:
        d = json.loads(t)
        if isinstance(d, list):
            return d
    except json.JSONDecodeError:
        pass
    # 提取第一个 [ ... ] 平衡括号块
    start = t.find("[")
    if start == -1:
        return []
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "[":
            depth += 1
        elif t[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    d = json.loads(t[start:i + 1])
                    return d if isinstance(d, list) else []
                except json.JSONDecodeError:
                    return []
    return []

# ---------- 5. IC 计算（复用 v2 管道：横截面 spearman + 半年分段） ----------
def eval_ic(expr_str):
    """返回 (dict) 或 None；expr_str 是 polars Expr 代码字符串"""
    try:
        # 受限 eval：只允许 polars Expr 构建（中文列名需放行）
        if not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fff\.\(\)\[\]'\" ,\+\-\*/%<>=!&|]+", expr_str):
            return None
        expr = eval(expr_str, {"__builtins__": {}}, {"pl": pl})
    except Exception:
        return None
    try:
        # 训练集切片（2021-2024），禁止验证集参与（L1 文档第六章第 7 条）
        cols = pl.scan_parquet(IC_DATA).collect_schema().names()
        d = (pl.scan_parquet(IC_DATA)
             .filter((pl.col("日期") >= date(2021, 1, 1)) & (pl.col("日期") <= date(2024, 12, 31)))
             .select(["日期", HORIZON] + [c for c in cols if c not in ("日期", HORIZON)])
             .collect())
        d = d.with_columns(expr.alias("_cand"))
        ic = (d.select(["日期", "_cand", HORIZON])
              .group_by("日期")
              .agg(pl.corr(pl.col("_cand"), pl.col(HORIZON), method="spearman").alias("ic"))
              .sort("日期"))
        ic = ic.filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite())
        if len(ic) < 200:
            return None
        v = ic["ic"]
        m, s = v.mean(), v.std()
        if s is None or s == 0 or m is None:
            return None
        icir = m / s
        ic2 = ic.with_columns(((pl.col("日期").dt.year() - 2010) * 2
                               + (pl.col("日期").dt.month() > 6)).alias("seg"))
        seg = ic2.group_by("seg").agg(pl.col("ic").mean().alias("seg_ic")).sort("seg")
        seg_ics = seg["seg_ic"].to_list()
        sign = 1 if m > 0 else -1
        seg_ok = sum(1 for x in seg_ics if x * sign > 0) / len(seg_ics)
        last2_ok = all(x * sign > 0 for x in seg_ics[-2:])
        return {"ic_mean": round(float(m), 4), "icir": round(float(icir), 4),
                "ic_pos_pct": round(float((v > 0).mean()) * 100, 1), "days": len(v),
                "seg_ok_ratio": round(seg_ok, 3), "last2_ok": last2_ok}
    except Exception:
        return None

# ---------- 6. 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5, help="每轮生成因子数")
    ap.add_argument("--rounds", type=int, default=3, help="自我修正轮数上限")
    ap.add_argument("--smoke", action="store_true", help="快速验证: 1 因子 1 轮")
    args = ap.parse_args()

    api_key = load_deepseek_key()
    ddict = build_dict()

    system = """你是 A 股量化因子研究员，精通 Polars 表达式。你的任务是发明有金融逻辑的因子。
规则：
1. 只能基于给定列组合出表达式（列名必须来自数据字典）
2. 预测目标 fwd_5d（未来5日收益），因子应是横截面排序信号（数值越大越看多/看空都行，我们会取绝对值）
3. 表达式必须是合法的 polars Expr 代码，例如：pl.col('ret_5d') * pl.col('turn_ma5') 或 pl.col('vol_ratio').rolling_mean(5, min_samples=3) 或 pl.col('close_momentum')——但列名只能用数据字典里的
4. 支持的操作符和函数：+ - * /、pl.col、.rolling_mean/.rolling_std/.rolling_max/.rolling_min（min_samples 必须给）、.rank().over('日期')、.shift(1).over('股票代码')、pl.corr 等
5. 不要用未定义的列，不要用 python 内置函数，不要 import
6. 每个因子必须有金融逻辑（反转/动量/量价背离/主力行为/事件惯性等），且与常见的 ma/vol/turn 因子不同
7. 输出严格 JSON 数组（不要多余文字），每项：{"name": "英文名", "logic": "金融逻辑", "expr": "polars表达式"}

数据字典："""

    user = f"请生成 1 个预测 {HORIZON} 的因子。\n\n数据字典（可用列）:\n{ddict}"

    results = []
    for gen in range(args.n):
        name, logic, expr_str = None, None, None
        for rnd in range(1, args.rounds + 1):
            if rnd == 1:
                u = user
            else:
                u = (f"上一轮你的因子 {name} 检验结果：\n"
                     f"IC均值={res.get('ic_mean')}, ICIR={res.get('icir')}, "
                     f"IC>0占比={res.get('ic_pos_pct')}%, 同号段比例={res.get('seg_ok_ratio')}, "
                     f"最近2段同号={res.get('last2_ok')}\n"
                     f"（{logic}）\n"
                     f"请分析这个因子为什么表现如此，修改公式以优化 |ICIR|，输出新的 JSON 数组（仍含 1 个因子）")
            try:
                out = llm_chat(system, u, api_key)
                factors = parse_factors(out)
                if not factors:
                    print(f"  [gen{gen} rnd{rnd}] LLM 输出无法解析，跳过")
                    break
                f = factors[0]
                name, logic, expr_str = f.get("name"), f.get("logic"), f.get("expr")
                res = eval_ic(expr_str)
                if res is None:
                    print(f"  [gen{gen} rnd{rnd}] 表达式执行失败: {expr_str}")
                    if rnd == args.rounds:
                        break
                    continue
                print(f"  [gen{gen} rnd{rnd}] {name}: IC={res['ic_mean']:+.4f} ICIR={res['icir']:+.3f} "
                      f"同号段={res['seg_ok_ratio']:.0%} last2={res['last2_ok']} <- {logic}")
                if rnd == args.rounds:
                    break
            except Exception as e:
                print(f"  [gen{gen} rnd{rnd}] API 错误: {e}")
                time.sleep(2)
        if name and res:
            results.append({"name": name, "logic": logic, "expr": expr_str, **res})

    df = pd.DataFrame(results)
    if len(df):
        out_csv = OUT_DIR / "llm_factors.csv"
        df.to_csv(out_csv, index=False)
        print(f"\n=== 完成: {len(df)} 个因子 -> {out_csv} ===")
        print(df[["name", "ic_mean", "icir", "seg_ok_ratio", "last2_ok"]].to_string(index=False))
    else:
        print("\n无有效因子产出")

if __name__ == "__main__":
    main()
