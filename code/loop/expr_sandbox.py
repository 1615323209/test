#!/usr/bin/env python3
"""安全 polars 表达式沙箱（AST 白名单）—— L1/L2 泄漏与注入防线

对应 L1 文档第六章（安全与泄漏防线）：
- 第 2 条：AST 白名单沙箱取代裸 eval（只允许 Name(pl) / 算子白名单内的
  Attribute/Call / Constant / BinOp / UnaryOp）
- 第 1 条：fwd_* 硬黑名单（feature registry 的 label 角色列，任何引用直接拒）
- 第 3 条：时序算子（rolling_*/shift/diff）强制 `.over('股票代码')`，
  禁止负数 shift（显式未来函数）

用法:
    from loop.expr_sandbox import safe_compile
    expr, err = safe_compile(expr_str)   # expr 为 polars Expr 或 None
"""
import ast
import re
import polars as pl

# ---------- 算子白名单（单一来源，同时用于校验与 prompt 生成） ----------
# polars Expr 方法白名单
ALLOWED_ATTRS = {
    'col', 'lit', 'when', 'then', 'otherwise',
    # 时序算子（必须带 .over('股票代码')）
    'rolling_mean', 'rolling_std', 'rolling_min', 'rolling_max', 'rolling_sum',
    'rolling_quantile', 'rolling_skew', 'shift', 'diff',
    # 横截面/变换
    'rank', 'over', 'alias', 'abs', 'log', 'log10', 'sqrt', 'clip', 'fill_null',
    'cast', 'round', 'sign', 'pow',
}
# 时序算子集合（强制 over 分组）
TIME_SERIES_OPS = {
    'rolling_mean', 'rolling_std', 'rolling_min', 'rolling_max', 'rolling_sum',
    'rolling_quantile', 'rolling_skew', 'shift', 'diff',
}
# 二元运算白名单
ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
# 一元运算白名单
ALLOWED_UNOPS = (ast.USub, ast.UAdd)

# ---------- 列校验 ----------
def is_fwd_col(name: str) -> bool:
    """label 角色列（未来收益），硬黑名单"""
    return name.startswith('fwd_')

def is_blacklisted_col(name: str) -> bool:
    """已知幻觉/禁用列（黑名单）"""
    return name in {'成交量', '成交额', '收盘价', '开盘价', '最高价', '最低价',
                    '收益率', '涨跌幅', '涨幅', 'volume', 'close', 'open'}

# ---------- AST 检查 ----------
def _check_node(node, state):
    """递归检查 AST 节点。state: {"has_over": bool, "has_timeseries": bool}"""
    if isinstance(node, ast.Expression):
        return _check_node(node.body, state)
    if isinstance(node, ast.Constant):
        # 只允许数字、字符串、None、bool
        if node.value is None or isinstance(node.value, (int, float, str, bool)):
            return None
        return "非常量节点"
    if isinstance(node, ast.Name):
        # 只允许 pl（模块引用）
        if node.id == 'pl':
            return None
        return f"非法变量: {node.id}"
    if isinstance(node, ast.Attribute):
        # 属性访问，attr 必须在白名单（value 递归检查）
        if node.attr not in ALLOWED_ATTRS:
            return f"非法算子: {node.attr}"
        if node.attr in TIME_SERIES_OPS:
            state["has_timeseries"] = True
        err = _check_node(node.value, state)
        return err
    if isinstance(node, ast.Call):
        # 函数调用：func 递归检查（必须是白名单属性），args 逐个检查
        err = _check_node(node.func, state)
        if err:
            return err
        for a in node.args:
            err = _check_node(a, state)
            if err:
                return err
        for kw in node.keywords:
            err = _check_node(kw.value, state)
            if err:
                return err
        # 如果调用的是 over，标记已有分组
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'over':
            state["has_over"] = True
        return None
    if isinstance(node, ast.BinOp):
        if type(node.op) not in ALLOWED_BINOPS:
            return f"非法运算符: {type(node.op).__name__}"
        err = _check_node(node.left, state)
        if err:
            return err
        return _check_node(node.right, state)
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in ALLOWED_UNOPS:
            return f"非法一元运算符: {type(node.op).__name__}"
        return _check_node(node.operand, state)
    if isinstance(node, ast.Compare):
        for c in node.comparators:
            err = _check_node(c, state)
            if err:
                return err
        err = _check_node(node.left, state)
        return err
    return f"非法语法节点: {type(node).__name__}"

def safe_compile(expr_str: str):
    """安全编译表达式字符串。

    返回 (polars Expr, None) 或 (None, 错误原因 str)。
    检查项：AST 白名单 / fwd_* 黑名单 / 幻觉列黑名单 / 时序算子必须 over
    """
    if not expr_str or not isinstance(expr_str, str):
        return None, "空表达式"
    try:
        tree = ast.parse(expr_str, mode='eval')
    except SyntaxError as e:
        return None, f"语法错误: {e.msg}"
    state = {"has_over": False, "has_timeseries": False}
    err = _check_node(tree, state)
    if err:
        return None, err
    # 列名检查（fwd_* 硬黑名单 + 幻觉黑名单）
    cols = re.findall(r"pl\.col\(['\"]([^'\"]+)['\"]\)", expr_str)
    for c in cols:
        if is_fwd_col(c):
            return None, f"引用标签列(前视): {c}"
        if is_blacklisted_col(c):
            return None, f"幻觉列名: {c}"
    # 时序算子强制 over
    if state["has_timeseries"] and not state["has_over"]:
        return None, "时序算子(rolling_*/shift/diff)必须带 .over('股票代码')"
    # 禁止负数 shift（未来函数）
    if re.search(r"shift\(\s*-", expr_str):
        return None, "禁止负数 shift（未来函数）"
    # 受限 eval（AST 已白名单，这里双保险）
    try:
        expr = eval(expr_str, {"__builtins__": {}}, {"pl": pl})
        return expr, None
    except Exception as e:
        return None, f"表达式执行失败: {str(e)[:100]}"
