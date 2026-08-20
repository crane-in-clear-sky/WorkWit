"""计算器：仅支持基础四则运算的安全计算器，用于需要数值计算的场景。"""
import ast
import operator

META = {
    "name": "calculator", "display_name": "计算器", "category": "utility",
    "description": "计算基础数学表达式（四则运算、幂、取模），用于需要数值计算的场景。",
    "params": {"type": "object",
               "properties": {"expression": {"type": "string", "description": "如 (12+8)*3"}},
               "required": ["expression"]},
    "backend_type": "builtin", "handler": "calculator",
    "trigger_words": "计算,算,数值,数学,公式,多少",
    "skip_skill": 1,
}


def run(ctx, expression):
    allowed = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
               ast.USub: operator.neg, ast.UAdd: operator.pos}
    try:
        node = ast.parse(expression, mode="eval").body

        def ev(n):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return n.value
            if isinstance(n, ast.BinOp) and type(n.op) in allowed:
                return allowed[type(n.op)](ev(n.left), ev(n.right))
            if isinstance(n, ast.UnaryOp) and type(n.op) in allowed:
                return allowed[type(n.op)](ev(n.operand))
            raise ValueError("仅支持基础四则运算")

        return f"{ev(node)}"
    except Exception as e:
        return f"计算失败：{e}"
