#!/usr/bin/env python3
"""expr_sandbox 回归测试（改造 1.4）—— 防回退唯一手段
用法: python -m tests.test_expr_sandbox
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop.expr_sandbox import safe_compile

CASES = [
    # (表达式, 期望通过 True/False, 说明)
    ("pl.col('^fwd_5d$')", False, "正则列选择器绕过 fwd 黑名单"),
    ("pl.col('ret_1d','fwd_5d')", False, "多参数引用标签列"),
    ("pl.col('ret_1d').rolling_mean(5).over('日期')", False, "时序算子按日期分组(非股票分组)"),
    ("pl.col('ret_1d').shift(n=-1).over('股票代码')", False, "关键字负 shift"),
    ("pl.col('a').rolling_mean(5) * pl.col('b').rank().over('日期')", False, "前半段裸 rolling(不在over内)"),
    ("pl.col('fwd_5d')", False, "引用标签列"),
    ("pl.col('成交量')", False, "幻觉列名"),
    ("pl.col('ret_5d')", True, "正常列"),
    ("pl.col('ret_5d').rolling_mean(5, min_samples=3).over('股票代码')", True, "正常时序+股票分组"),
    ("pl.col('ret_5d').rank().over('日期')", True, "横截面 rank+日期分组(非时序,允许)"),
    ("(-pl.col('ret_5d') * pl.col('turn_ma5'))", True, "v7 因子"),
    ("pl.col('ret_5d').shift(-5).over('股票代码')", False, "位置负 shift"),
    ("pl.col('ret_5d').over('股票代码').rolling_mean(5)", False, "rolling在over之外"),
    ("pl.col(123)", False, "col 非字符串字面量"),
]

def main():
    n_pass = 0
    for expr_str, expect, note in CASES:
        _, err, cols = safe_compile(expr_str)
        got = err is None
        status = "✅" if got == expect else "❌ MISMATCH"
        if got == expect:
            n_pass += 1
        print(f"{status} 期望{'通过' if expect else '拒绝'} 实得{'通过' if got else '拒绝'} | {note}: {expr_str}")
        if got != expect:
            print(f"      err={err}")
    print(f"\n通过 {n_pass}/{len(CASES)}")
    # 额外：验证 cols 收集正确
    _, _, cols = safe_compile("pl.col('a') + pl.col('b')")
    assert cols == ["a", "b"], f"cols 收集错误: {cols}"
    print("✅ cols 收集正确:", cols)
    return 0 if n_pass == len(CASES) else 1

if __name__ == "__main__":
    sys.exit(main())
