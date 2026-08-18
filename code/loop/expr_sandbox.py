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
REGEX_META = set("^$*?[]|+\\")
GROUP_KEY = "股票代码"

def is_fwd_col(name: str) -> bool:
    """label 角色列（未来收益），硬黑名单"""
    return name.startswith('fwd_')

def is_blacklisted_col(name: str) -> bool:
    """已知幻觉/禁用列（黑名单）"""
    return name in {'成交量', '成交额', '收盘价', '开盘价', '最高价', '最低价',
                    '收益率', '涨跌幅', '涨幅', 'volume', 'close', 'open'}

def _col_args(call):
    """pl.col(...) 的参数必须全是纯字面量列名，否则拒（改造 1.3）
    返回 (names, err)。含正则元字符/非字符串字面量 → 拒"""
    names = []
    for a in call.args:
        if not isinstance(a, ast.Constant) or not isinstance(a.value, str):
            return None, "pl.col 参数必须是字符串字面量"
        if REGEX_META & set(a.value):
            return None, f"禁止正则列选择器: {a.value}"
        names.append(a.value)
    if call.keywords:
        return None, "pl.col 不接受关键字参数"
    return names, None

def _has_negative_arg(call):
    """shift 的 args[0] 或 keywords['n'] 为负数（改造 1.3：关键字负 shift 也拦）"""
    for node in call.args:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value < 0:
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float)):
                return True
    for kw in call.keywords:
        if kw.arg in ("n", "offset", "periods"):
            v = kw.value
            if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) and v.value < 0:
                return True
            if isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.USub):
                if isinstance(v.operand, ast.Constant) and isinstance(v.operand.value, (int, float)):
                    return True
    return False

# ---------- AST 检查 ----------
def _check_node(node, covered, cols, err_out):
    """递归检查 AST 节点。
    covered: 当前最内层 over 的分组键集合（含 '股票代码' 则时序算子可执行；空/None=不在 over 内）
    cols: 收集到所有 pl.col 列名（供 validate_expr 消费，与校验同源）
    改造 1.3：列提取与 over 归属全部 AST 化，不再用正则"""
    if isinstance(node, ast.Expression):
        return _check_node(node.body, covered, cols, err_out)
    if isinstance(node, ast.Constant):
        if node.value is None or isinstance(node.value, (int, float, str, bool)):
            return None
        return "非常量节点"
    if isinstance(node, ast.Name):
        if node.id == 'pl':
            return None
        return f"非法变量: {node.id}"
    if isinstance(node, ast.Attribute):
        if node.attr not in ALLOWED_ATTRS:
            return f"非法算子: {node.attr}"
        return _check_node(node.value, covered, cols, err_out)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            if attr == "col":
                names, err = _col_args(node)
                if err:
                    return err
                for c in names:
                    if is_fwd_col(c):
                        return f"引用标签列(前视): {c}"
                    if is_blacklisted_col(c):
                        return f"幻觉列名: {c}"
                cols.extend(names)
            elif attr == "over":
                keys = {a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
                if not keys:
                    return "over() 必须显式给出分组列字面量"
                # 接收者子树受本次 over 分组覆盖
                inner_covered = keys if (GROUP_KEY in keys) else covered
                return _check_node(func.value, inner_covered, cols, err_out)
            elif attr in TIME_SERIES_OPS:
                # 时序算子：必须位于覆盖股票分组的 over 内（covered 含 股票代码）
                if not (isinstance(covered, set) and GROUP_KEY in covered):
                    return f"时序算子 {attr} 缺少 .over('{GROUP_KEY}')"
            if attr == "shift" and _has_negative_arg(node):
                return "禁止负数 shift（未来函数）"
        # 递归检查 func + args + keywords
        e = _check_node(func, covered, cols, err_out)
        if e:
            return e
        for a in node.args:
            e = _check_node(a, covered, cols, err_out)
            if e:
                return e
        for kw in node.keywords:
            e = _check_node(kw.value, covered, cols, err_out)
            if e:
                return e
        return None
    if isinstance(node, ast.BinOp):
        if type(node.op) not in ALLOWED_BINOPS:
            return f"非法运算符: {type(node.op).__name__}"
        e = _check_node(node.left, covered, cols, err_out)
        if e:
            return e
        return _check_node(node.right, covered, cols, err_out)
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in ALLOWED_UNOPS:
            return f"非法一元运算符: {type(node.op).__name__}"
        return _check_node(node.operand, covered, cols, err_out)
    if isinstance(node, ast.Compare):
        e = _check_node(node.left, covered, cols, err_out)
        if e:
            return e
        for c in node.comparators:
            e = _check_node(c, covered, cols, err_out)
            if e:
                return e
        return None
    return f"非法语法节点: {type(node).__name__}"

def safe_compile(expr_str: str):
    """安全编译表达式字符串。

    返回 (polars Expr, None, cols) 或 (None, 错误原因 str, cols)。
    改造 1.3：列提取/over 归属/负 shift 全部 AST 化（关闭正则绕过面）；
    cols 与 validate_expr 同源，供未注册列校验消费。"""
    if not expr_str or not isinstance(expr_str, str):
        return None, "空表达式", []
    try:
        tree = ast.parse(expr_str, mode='eval')
    except SyntaxError as e:
        return None, f"语法错误: {e.msg}", []
    cols = []
    err = _check_node(tree, None, cols, None)
    if err:
        return None, err, cols
    # 受限 eval（AST 已白名单，这里双保险）
    try:
        expr = eval(expr_str, {"__builtins__": {}}, {"pl": pl})
        return expr, None, cols
    except Exception as e:
        return None, f"表达式执行失败: {str(e)[:100]}", cols
