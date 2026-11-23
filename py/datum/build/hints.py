"""Подсказки к ошибкам: чем заменить то, чего в Datum нет.

Агент пишет скрипт детали по памяти о ДРУГИХ библиотеках — OpenSCAD, CadQuery,
build123d — и первым делом зовёт `cylinder`, `translate`, `fillet`. Python
отвечает «cannot import name 'cylinder'», и дальше агент ищет замену вслепую
(перечитывает исходники, пробует варианты). Здесь к такой ошибке дописывается
ответ: какое имя Datum делает то же самое и с какой подписью.

Подсказка — ДОБАВКА к сообщению, а не его замена: исходный текст Python
остаётся, чтобы причину было видно как есть. Если подсказать нечего, ничего не
дописывается — догадка хуже молчания.

Чужие названия перечислены руками (`ALIASES`): похожесть строк их не найдёт
(`cylinder` на `cyl_z` не похож), а близкие опечатки (`centre_onn`, `slabb`)
ловит `difflib`. Каждое наше имя в таблице сверяет тест — подсказка на
несуществующую функцию была бы хуже отсутствия подсказки.
"""

from __future__ import annotations

import difflib
import enum
import inspect
import re
from collections.abc import Callable

__all__ = ["ALIASES", "CHECK_ALIASES", "hint", "with_hint", "names_hint", "compact_signature",
           "known_functions"]

#: Чужое имя → (наши имена, как ими сделать то же самое).
ALIASES: dict[str, tuple[tuple[str, ...], str]] = {
    "cylinder": (("cyl_z", "cyl_x", "cyl_y"),
                 "цилиндр — cyl_z(d, z0, z1, x, y) и cyl_x / cyl_y: d — ДИАМЕТР, "
                 "а вдоль оси — от и до, а не высота"),
    "cube": (("box",), "параллелепипед — box(x0, x1, y0, y1, z0, z1): границы, а не размер"),
    "cuboid": (("box",), "параллелепипед — box(x0, x1, y0, y1, z0, z1): границы, а не размер"),
    "translate": (("move",), "перенос — move(тело, dx, dy, dz)"),
    "translated": (("move",), "перенос — move(тело, dx, dy, dz)"),
    "difference": (("diff",), "вычитание — diff(a, [b, c]) или a - b"),
    "subtract": (("diff",), "вычитание — diff(a, [b, c]) или a - b"),
    "cut": (("diff",), "вычитание — diff(a, [b, c]) или a - b"),
    "intersection": (("intersect",), "пересечение — intersect([a, b]) или a ^ b"),
    "fuse": (("union",), "объединение — union([a, b]) или a + b"),
    "combine": (("union",), "объединение — union([a, b]) или a + b"),
    "linear_extrude": (("extrude", "slab"),
                       "выдавливание — extrude(профиль, z0, z1); с фаской или "
                       "скруглением кромок — slab"),
    "rotate_extrude": (("revolve_z",), "тело вращения — revolve_z([(r, z), …], cx, cy)"),
    "revolve": (("revolve_z",), "тело вращения — revolve_z([(r, z), …], cx, cy)"),
    "fillet": (("slab", "round_corners"),
               "скругление: кромок плиты — slab(профиль, z0, z1, fillet_top=r), "
               "вертикальных углов — round_corners(профиль, r)"),
    "chamfer": (("slab", "chamfer_corners"),
                "фаска: кромок плиты — slab(профиль, z0, z1, chamfer_bot=c), "
                "вертикальных углов — chamfer_corners(профиль, c)"),
    "shell": (("cup",), "стакан со стенками и дном — cup(профиль, z0, z1, wall, floor)"),
    "hollow": (("cup",), "стакан со стенками и дном — cup(профиль, z0, z1, wall, floor)"),
    "hole": (("hole_z",), "отверстие — резец hole_z(d, z0, z1, x, y), вычитается diff"),
    "counterbore": (("hole_z",), "отверстие с цековкой — hole_z(d, z0, z1, x, y, cbore=(D, глубина))"),
    "countersink": (("hole_z",), "отверстие с зенковкой — hole_z(d, z0, z1, x, y, csk=D)"),
    "text": (("text_z", "text_poly"),
             "надпись — text_z(строка, size, z0, z1, x, y, font=…); профилем — text_poly"),
    "text3d": (("text_z",), "надпись — text_z(строка, size, z0, z1, x, y, font=…)"),
    "emboss": (("text_z",), "выпуклая надпись — union с text_z(…) на поверхности"),
    "engrave": (("text_z",), "гравировка — diff(тело, [text_z(…)]) ниже поверхности"),
    "screw": (("thread",), "резьба — thread(d, pitch, z0, z1, clearance=…)"),
    "bolt": (("thread",), "резьба — thread(d, pitch, z0, z1, clearance=…)"),
    "helix": (("thread",), "резьба — thread(d, pitch, z0, z1, clearance=…)"),
    "knurling": (("knurl",), "накатка — knurl(d, z0, z1, x, y, pitch=…)"),
    "rounded_box": (("slab", "round_corners"),
                    "скруглённая коробка — slab(round_corners(профиль, r), z0, z1, fillet_top=…)"),
    "polygon": (("prism_xy", "extrude"),
                "многоугольник — prism_xy([(x, y), …], z0, z1) или shapely Polygon и extrude"),
    "linear_pattern": (("array_linear",), "ряд копий — array_linear(тело, n, dx, dy, dz)"),
    "circular_pattern": (("array_polar",), "копии по кругу — array_polar(тело, n, axis='z', c=…)"),
    "polar_array": (("array_polar",), "копии по кругу — array_polar(тело, n, axis='z', c=…)"),
    "center": (("centre_on", "centre_on_bed"),
               "поставить по центру — centre_on(тело, база), на стол — centre_on_bed(тело)"),
    "center_on": (("centre_on",), "по центру другого тела — centre_on(тело, база)"),
    "translate_to": (("align",), "точку габарита — в точку: align(тело, '+X-Y', (x, y, z))"),
    "place_on": (("stack",), "поставить на другое тело — stack(тело, база, '+Z', gap=…)"),
    "minkowski": (("insertion_pocket",),
                  "общей суммы Минковского нет; карман под предмет по вставке — "
                  "insertion_pocket(предмет, insert)"),
    "convex_hull": (("hull",), "выпуклая оболочка — hull(тело)"),
    "offset": (("cup",), "смещение профиля — shapely: профиль.buffer(d, join_style='mitre'); "
                         "стенки по контуру — cup"),
}

#: Чужие названия проверок → проверки каталога.
CHECK_ALIASES: dict[str, str] = {
    "collision": "no_intersection (в одном положении) или joint_sweep / travel_sweep (по ходу)",
    "interference": "no_intersection (в одном положении) или joint_sweep / travel_sweep (по ходу)",
    "intersection": "no_intersection",
    "gap": "clearance",
    "wall_thickness": "min_wall",
    "thin_wall": "min_wall",
    "supports": "support_volume",
    "support": "support_volume",
    "bridge": "bridge_span",
    "bed_adhesion": "bed_contact",
    "fits_bed": "bbox_fits",
    "hinge": "joint_sweep — а проще объявить шарнир в assembly.yaml (joints:), проверка выведется сама",
    "joint": "joint_sweep — а проще объявить шарнир в assembly.yaml (joints:), проверка выведется сама",
    "insertion": "assembly_path (вдвигается) или snap_insertion (защёлка)",
    "assembly": "assembly_path — а проще объявить порядок в assembly.yaml, проверки выведутся сами",
    "snap": "snap_insertion",
    "retention": "escape",
    "holds": "escape",
}

#: Операторы, которых у Solid нет, → какие есть.
_OPERATORS = {
    "&": "пересечение — a ^ b или intersect([a, b])",
    "|": "объединение — a + b или union([a, b])",
    "*": "умножения тел нет: перенос — move, масштаб — scale",
}

_IMPORT = re.compile(r"cannot import name '([^']+)' from '([\w.]+)'")
_NO_MODULE = re.compile(r"No module named '(datum[\w.]*)'")
#: Модули, которые скрипт детали импортирует.
_SCRIPT_MODULES = ("datum.geom", "datum.build", "datum.std.gridfinity")
_MODULE_ATTR = re.compile(r"module '([\w.]+)' has no attribute '([^']+)'")
_OBJECT_ATTR = re.compile(r"'(\w+)' object has no attribute '([^']+)'")
_KWARG = re.compile(r"(\w+)\(\) got an unexpected keyword argument '([^']+)'")
_ARGS = re.compile(r"(\w+)\(\) (missing \d+ required|takes \d+|takes from)")
_OPERAND = re.compile(r"unsupported operand type\(s\) for (\S+): '(\w+)' and '(\w+)'")


def compact_signature(fn: Callable, name: str | None = None) -> str:
    """Подпись без аннотаций: `box(x0, x1, y0, y1, z0, z1, *, name='')`."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return f"{name or getattr(fn, '__name__', '?')}(…)"
    parts: list[str] = []
    star = False
    for p in sig.parameters.values():
        if p.kind is p.KEYWORD_ONLY and not star:
            parts.append("*")
            star = True
        if p.kind is p.VAR_POSITIONAL:
            parts.append(f"*{p.name}")
            star = True
            continue
        if p.kind is p.VAR_KEYWORD:
            parts.append(f"**{p.name}")
            continue
        if p.default is p.empty:
            parts.append(p.name)
        else:
            d = p.default
            parts.append(f"{p.name}={(d.value if isinstance(d, enum.Enum) else d)!r}")
    return f"{name or fn.__name__}({', '.join(parts)})"


def known_functions() -> dict[str, Callable]:
    """Имена, которые скрипт детали и проверки зовут чаще всего: datum.geom,
    datum.build и каталог проверок (по имени в checks.yaml)."""
    import datum.build as build
    import datum.geom as geom
    from datum.checks.library import LIBRARY

    out: dict[str, Callable] = {}
    for module in (geom, build):
        for n in module.__all__:
            obj = getattr(module, n, None)
            if callable(obj) and not inspect.isclass(obj):
                out[n] = obj
    for n, entry in LIBRARY.items():
        out.setdefault(n, entry.fn)
        out.setdefault(entry.fn.__name__, entry.fn)
    return out


def _public(module_name: str) -> list[str]:
    import importlib

    try:
        module = importlib.import_module(module_name)
    except Exception:                                   # pragma: no cover
        return []
    names = getattr(module, "__all__", None)
    return list(names) if names else [n for n in dir(module) if not n.startswith("_")]


def _replacement(name: str, known: list[str], module: str = "") -> str:
    """Что сказать про отсутствующее имя: чужое → наше, опечатка → близкое."""
    if name in ALIASES:
        _, how = ALIASES[name]
        return how
    lower = name.lower()
    if lower in ALIASES:
        return ALIASES[lower][1]
    close = difflib.get_close_matches(name, known, n=3, cutoff=0.6)
    if close:
        fns = known_functions()
        shown = [compact_signature(fns[c], c) if c in fns else c for c in close]
        return "может быть, " + " или ".join(shown)
    if module:
        return f"имена {module} — references/api.md навыка"
    return ""


def _signature_hint(fn_name: str, bad_kw: str | None = None) -> str:
    fns = known_functions()
    fn = fns.get(fn_name)
    if fn is None:
        return ""
    sig = compact_signature(fn, fn_name)
    if bad_kw:
        params = list(inspect.signature(fn).parameters)
        close = difflib.get_close_matches(bad_kw, params, n=1, cutoff=0.5)
        if close:
            return f"может быть, {close[0]}=…; подпись: {sig}"
    return f"подпись: {sig}"


def hint(err: BaseException) -> str:
    """Подсказка к ошибке или пустая строка."""
    text = str(err)
    if isinstance(err, ImportError):
        m = _IMPORT.search(text)
        if m and m.group(2).startswith("datum"):
            return _replacement(m.group(1), _public(m.group(2)), m.group(2))
        m = _NO_MODULE.search(text)
        if m:
            close = difflib.get_close_matches(m.group(1), _SCRIPT_MODULES, n=1, cutoff=0.6)
            return (f"модуль — {close[0]}" if close else
                    f"модули для скрипта детали: {', '.join(_SCRIPT_MODULES)}")
        return ""
    if isinstance(err, NameError):
        name = getattr(err, "name", None) or ""
        for module in ("datum.geom", "datum.build"):
            if name in _public(module):
                return f"добавьте импорт: from {module} import {name}"
        return _replacement(name, _public("datum.geom") + _public("datum.build")) if name else ""
    if isinstance(err, AttributeError):
        m = _MODULE_ATTR.search(text)
        if m and m.group(1).startswith("datum"):
            return _replacement(m.group(2), _public(m.group(1)), m.group(1))
        m = _OBJECT_ATTR.search(text)
        if m and m.group(1) == "Solid":
            from datum.geom.solid import Solid

            attrs = [a for a in dir(Solid) if not a.startswith("_")]
            close = difflib.get_close_matches(m.group(2), attrs, n=2, cutoff=0.6)
            if close:
                return "у Solid есть " + ", ".join(close)
            how = _replacement(m.group(2), _public("datum.geom"))
            return (f"это функция, а не метод: {how}" if how else
                    "методы Solid — references/api.md навыка")
        return ""
    if isinstance(err, TypeError):
        m = _KWARG.search(text)
        if m:
            return _signature_hint(m.group(1), m.group(2))
        m = _ARGS.search(text)
        if m:
            return _signature_hint(m.group(1))
        m = _OPERAND.search(text)
        if m and "Solid" in (m.group(2), m.group(3)):
            return _OPERATORS.get(m.group(1), "")
    return ""


def names_hint(missing: list[str], known: list[str] | set[str], *,
               aliases: dict[str, str] | None = None) -> str:
    """Подсказка к неизвестным именам (деталей, проверок): опечатка, список
    одной строкой через запятую, чужое название."""
    known = sorted(known)
    tips: list[str] = []
    for name in missing:
        pieces = [p.strip() for p in re.split(r"[,;]", name) if p.strip()]
        if len(pieces) > 1 and all(p in known for p in pieces):
            tips.append(f"{name!r} — это несколько имён одной строкой: передавайте их "
                        f"по одному (в CLI — повтором флага, в вызове — списком)")
            continue
        if aliases and name.lower() in aliases:
            tips.append(f"{name!r} → {aliases[name.lower()]}")
            continue
        close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        if close:
            tips.append(f"{name!r} → {close[0]!r}")
    return "; ".join(tips)


def with_hint(message: str, err: BaseException) -> str:
    """Сообщение с подсказкой, если она есть."""
    try:
        h = hint(err)
    except Exception:                                   # pragma: no cover
        h = ""                       # подсказка не должна ломать само сообщение
    return f"{message}. Подсказка: {h}" if h else message
