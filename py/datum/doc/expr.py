"""Безопасное вычисление выражений для производных параметров.

Производный параметр задаётся формулой: `Z_TOP: FLOOR_T + MECH_H + PLATE_T`.
Соблазн написать `eval()` велик, но проект Datum по замыслу открывает чужие
файлы, а формула в чужом проекте — это чужой код. Поэтому выражение разбирается
в дерево и пропускается через белый список узлов: ни обращения к атрибутам, ни
индексации, ни вызовов чего попало.
"""

from __future__ import annotations

import ast
import math
from typing import Any

__all__ = ["evaluate", "referenced_names", "ExpressionError", "FUNCTIONS", "CONSTANTS"]


class ExpressionError(ValueError):
    """Выражение отвергнуто или не вычислилось."""


def _round(x: float, ndigits: float = 0) -> float:
    """`round` с числом знаков: в выражении все числа вещественные, а `round`
    ждёт целое число знаков."""
    if float(ndigits) != int(ndigits):
        raise ExpressionError(f"round: число знаков {ndigits} должно быть целым")
    return round(x, int(ndigits))


FUNCTIONS: dict[str, Any] = {
    "min": min, "max": max, "abs": abs, "round": _round,
    "sqrt": math.sqrt, "hypot": math.hypot,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "radians": math.radians, "degrees": math.degrees,
    "floor": math.floor, "ceil": math.ceil, "log": math.log, "exp": math.exp,
}

CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}

_ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load,
    ast.BinOp, ast.UnaryOp, ast.Call, ast.IfExp, ast.Compare,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.BoolOp, ast.And, ast.Or, ast.Not,
)


def _parse(expr: str) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"не разобрать выражение {expr!r}: {e.msg}") from e

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"в выражении {expr!r} запрещён элемент {type(node).__name__} — "
                f"формула может содержать только арифметику, сравнения и вызовы из "
                f"списка {sorted(FUNCTIONS)}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                name = getattr(node.func, "id", type(node.func).__name__)
                raise ExpressionError(f"в выражении {expr!r} запрещён вызов {name!r}")
            if node.keywords:
                raise ExpressionError(f"в выражении {expr!r} именованные аргументы не поддерживаются")
    return tree


def referenced_names(expr: str) -> set[str]:
    """Имена параметров, от которых зависит выражение (без функций и констант)."""
    tree = _parse(expr)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in FUNCTIONS and node.id not in CONSTANTS:
            out.add(node.id)
    return out


def _real(value: Any, expr: str) -> Any:
    """Каждое промежуточное число — вещественное.

    ЦЕЛЫЕ ЗДЕСЬ ОПАСНЫ. У Python они неограниченной длины, и `9**9**9` в
    формуле производного параметра считался минутами, не отвечая ни на какой
    сигнал: длинная арифметика идёт в C. Одна строка в чужом params/base.yaml
    вешала открытие проекта. В вещественных та же степень мгновенно
    переполняется, и это называется ошибкой формулы.

    Логические значения остаются как есть — они нужны сравнениям и `and/or`.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, complex):
        raise ExpressionError(f"в выражении {expr!r} вышло комплексное число "
                              f"(например, дробная степень отрицательного)")
    return float(value)


def evaluate(expr: str, values: dict[str, float]) -> float:
    """Вычислить выражение при данных значениях параметров."""
    tree = _parse(expr)
    env: dict[str, Any] = {**CONSTANTS, **FUNCTIONS, **values}

    def ev(node: ast.AST) -> Any:
        return _real(ev_raw(node), expr) if not isinstance(node, ast.Expression) \
            else ev_raw(node)

    def ev_raw(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)):
                return node.value
            raise ExpressionError(f"в выражении допустимы только числа, встречено {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise ExpressionError(f"в выражении {expr!r} неизвестное имя {node.id!r}")
            if node.id in FUNCTIONS and node.id not in values:
                raise ExpressionError(f"в выражении {expr!r} функция {node.id!r} без вызова")
            return env[node.id]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else (+v if isinstance(node.op, ast.UAdd) else (not v))
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            op = node.op
            if isinstance(op, ast.Add): return a + b
            if isinstance(op, ast.Sub): return a - b
            if isinstance(op, ast.Mult): return a * b
            if isinstance(op, ast.Div):
                if b == 0:
                    raise ExpressionError(f"в выражении {expr!r} деление на ноль")
                return a / b
            if isinstance(op, ast.FloorDiv):
                if b == 0:
                    raise ExpressionError(f"в выражении {expr!r} деление на ноль")
                return a // b
            if isinstance(op, ast.Mod): return a % b
            if isinstance(op, ast.Pow): return a ** b
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                ok = (left < right if isinstance(op, ast.Lt) else
                      left <= right if isinstance(op, ast.LtE) else
                      left > right if isinstance(op, ast.Gt) else
                      left >= right if isinstance(op, ast.GtE) else
                      left == right if isinstance(op, ast.Eq) else left != right)
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.IfExp):
            return ev(node.body) if ev(node.test) else ev(node.orelse)
        if isinstance(node, ast.Call):
            return FUNCTIONS[node.func.id](*[ev(a) for a in node.args])
        raise ExpressionError(f"необработанный элемент {type(node).__name__}")

    try:
        result = float(ev(tree))
    except ExpressionError:
        raise
    except OverflowError as e:
        raise ExpressionError(f"выражение {expr!r} переполнилось: {e}") from e
    except Exception as e:
        raise ExpressionError(f"выражение {expr!r} не вычислилось: "
                              f"{type(e).__name__}: {e}") from e
    if not math.isfinite(result):
        raise ExpressionError(f"выражение {expr!r} дало {result} — это не размер")
    return result
