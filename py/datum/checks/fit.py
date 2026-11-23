"""Проверки посадок и локальных препятствий.

`obstruction_thickness` — порт `check4.lip_obstacle`, появившийся после провала
v4.1. Объёмная проверка тогда прошла, а деталь всё равно не держалась: тонкий
уступ на длинной кромке «размазывался» по общему объёму и терялся. Измерять
пришлось по сечениям: на сколько деталь пройдёт до первого касания и какой
толщины препятствие, в которое она упёрлась.

`tunnel_wall` — порт одноимённой проверки: кольцо вокруг отверстия должно
целиком лежать в материале, иначе стенка туннеля прорвана.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence

from shapely import affinity
from shapely.geometry import box as sbox

from datum.checks.evidence import intersection_evidence, pair_evidence
from datum.checks.result import Artifact, CheckResult
from datum.geom.measure import inter_vol, man, min_gap, section_xy, section_xz, section_yz, vol
from datum.geom.prims import cyl_x, cyl_y, cyl_z
from datum.geom.solid import Solid

__all__ = ["clearance", "no_intersection", "obstruction_thickness", "tunnel_wall"]

_SECTION = {"xy": section_xy, "xz": section_xz, "yz": section_yz}


def _evidence_if_failed(ok: bool, make) -> tuple:
    """Картинка строится ТОЛЬКО при провале.

    На успешной проверке она никому не нужна, а стоит столько же — булеву
    операцию, три сечения и растеризацию.

    Отказ рисования не роняет саму проверку: провал важнее картинки к нему. Но
    и МОЛЧА он не проходит — вместо картинки кладётся текст с причиной. Первая
    же версия этой функции глотала всё подряд, и опечатка в импорте выглядела
    как «доказательств просто нет»; искать такое потом невесело.
    """
    if ok:
        return ()
    try:
        artifact = make()
    except Exception as e:
        return (Artifact(kind="text",
                         caption=f"доказательство не построилось: "
                                 f"{type(e).__name__}: {e}"),)
    return (artifact,) if artifact is not None else ()


def _empty_input(a: Solid, b: Solid, a_name: str, b_name: str, group: str,
                 title: str, check_id: str) -> CheckResult | None:
    """Пустое тело на входе — не повод для «прошло».

    Раньше пустое тело давало зазор «4.000 ≥ …» и нулевое пересечение, то есть
    вечный pass: деталь, которая перестала собираться в тело, выглядела
    идеально посаженной.
    """
    missing = [n for s, n in ((a, a_name), (b, b_name)) if man(s).is_empty()]
    if not missing:
        return None
    return CheckResult(
        id=check_id, title=title, group=group, status="error",
        message=f"{a_name} ↔ {b_name}: тело {', '.join(missing)} пустое — мерить нечего",
        inputs=(a_name, b_name))


def clearance(a: Solid, b: Solid, *, min_mm: float, search: float = 4.0,
              id: str = "", a_name: str = "", b_name: str = "") -> CheckResult:
    """Минимальный зазор между двумя телами."""
    t0 = time.perf_counter()
    a_name = a_name or a.name or "a"
    b_name = b_name or b.name or "b"
    if min_mm > search:
        # Зазор ищется не дальше `search`: при пороге за его пределами проверка
        # проваливалась бы всегда, сообщая «зазор 4.000 < 5.000» про зазор 6.
        raise ValueError(f"min_mm {min_mm:g} больше области поиска search {search:g}: "
                         f"такой зазор не измерить — увеличьте search")
    empty = _empty_input(a, b, a_name, b_name, "fit", "Зазор",
                         id or f"{a_name}~{b_name}.clearance")
    if empty is not None:
        return empty
    g = min_gap(a, b, search)
    ok = g >= min_mm
    check_id = id or f"{a_name}~{b_name}.clearance"
    message = (f"{a_name} ↔ {b_name}: зазор {g:.3f} мм "
               f"{'≥' if ok else '<'} {min_mm:.3f}"
               + ("" if g < search else f" (поиск ограничен {search} мм)"))
    return CheckResult(
        id=check_id, title="Зазор", group="fit",
        status="pass" if ok else "fail", message=message,
        value=g, unit="мм", op=">=", threshold=min_mm,
        inputs=(a_name, b_name),
        evidence=_evidence_if_failed(
            ok, lambda: pair_evidence(a, b, a_name=a_name, b_name=b_name,
                                      title=f"{check_id} — ПРОВАЛ", note=message)),
        duration_ms=(time.perf_counter() - t0) * 1000)


def no_intersection(a: Solid, b: Solid, *, max_vol: float = 0.05,
                    id: str = "", a_name: str = "", b_name: str = "") -> CheckResult:
    """Тела не должны пересекаться (с допуском на численный шум)."""
    t0 = time.perf_counter()
    a_name = a_name or a.name or "a"
    b_name = b_name or b.name or "b"
    empty = _empty_input(a, b, a_name, b_name, "fit", "Без пересечения",
                         id or f"{a_name}~{b_name}.no_intersection")
    if empty is not None:
        return empty
    v = inter_vol(a, b)
    ok = v <= max_vol
    check_id = id or f"{a_name}~{b_name}.no_intersection"
    message = (f"{a_name} ↔ {b_name}: пересечение {v:.3f} мм³ "
               f"{'≤' if ok else '>'} {max_vol:.3f}")
    return CheckResult(
        id=check_id, title="Без пересечения",
        group="fit", status="pass" if ok else "fail", message=message,
        value=v, unit="мм³", op="<=", threshold=max_vol,
        inputs=(a_name, b_name),
        evidence=_evidence_if_failed(
            ok, lambda: intersection_evidence(a, b, a_name=a_name, b_name=b_name,
                                              title=f"{check_id} — ПРОВАЛ",
                                              note=message)),
        duration_ms=(time.perf_counter() - t0) * 1000)


def obstruction_thickness(part: Solid, holder: Solid, *, plane: str, at: float | Sequence[float],
                          band: tuple[float, float], direction: int = 1,
                          step: float = 0.02, max_travel: float = 12.0,
                          min_thickness: float = 1.2, id: str = "",
                          part_name: str = "", holder_name: str = "") -> CheckResult:
    """Сечение + луч-марш: путь до первого касания и толщина препятствия.

    `plane` — какое сечение берём: "xz" даёт координаты (x, z), "yz" → (y, z),
    "xy" → (x, y). `band` ограничивает первую координату сечения, движение идёт
    по второй в сторону `direction`. `at` — где режем (можно список: берётся
    худший случай).

    ВАЖНО о том, что именно меряется. Оригинальный `check4.lip_obstacle` считал
    весь путь, на котором деталь остаётся в контакте. Это НЕ толщина губки: в неё
    входит ещё и собственный размер детали вдоль движения. В корпусе это не
    мешало, потому что полосу брали узкой, у самой кромки, где деталь тонкая. На
    полной детали высотой 5 мм с губкой 1.5 мм метод даёт 6.48 мм — число,
    которое легко принять за толщину губки и ошибиться в четыре раза.

    Поэтому здесь разделено:
      * `contact_span_mm` — путь в контакте (то, что считал оригинал);
      * `part_extent_mm`  — размер детали вдоль движения внутри полосы;
      * `thickness_mm`    — разность, то есть собственно толщина препятствия.
    Порог применяется к `thickness_mm`. Шаг марша тот же, 0.02 мм.
    """
    t0 = time.perf_counter()
    part_name = part_name or part.name or "деталь"
    holder_name = holder_name or holder.name or "корпус"
    if plane not in _SECTION:
        raise ValueError(f"плоскость должна быть одной из {sorted(_SECTION)}, получено {plane!r}")
    cut = _SECTION[plane]
    ats = [at] if isinstance(at, (int, float)) else list(at)

    # (толщина препятствия, свободный ход, координата сечения, размер детали, путь в контакте)
    worst: tuple[float, float | None, float, float, float] | None = None
    for a in ats:
        p_sec = cut(part, float(a))
        c_sec = cut(holder, float(a))
        if p_sec.is_empty or c_sec.is_empty:
            continue
        clip_p = sbox(band[0], -1e4, band[1], 1e4)
        clip_c = sbox(band[0] - 5.0, -1e4, band[1] + 5.0, 1e4)
        p = p_sec.intersection(clip_p)
        c = c_sec.intersection(clip_c)
        if p.is_empty:
            continue
        # Собственный размер детали вдоль движения — его надо вычесть из пути в контакте.
        pb = p.bounds
        part_extent = float(pb[3] - pb[1])
        if c.is_empty:
            # Препятствия в полосе нет вовсе — деталь ничем не удерживается.
            if worst is None or 0.0 < worst[0]:
                worst = (0.0, None, float(a), part_extent, 0.0)
            continue

        # Марш по КАЖДОЙ преграде в сечении отдельно, от первого до последнего
        # касания с ней. Первая версия останавливалась на первом выходе из
        # контакта: у детали с просветом вдоль движения (скоба, окно) первая
        # полка выходит из губки, в просвете контакта нет, потом входит вторая
        # — и из короткого пути вычитался ВЕСЬ размер детали, давая толщину
        # 0.00. Ложный провал, из-за которого правильную деталь заставили бы
        # утолщать губку.
        #
        # Отдельно по преградам — потому что иначе следующая преграда на пути
        # приклеивалась бы к толщине первой: скоба, прошедшая губку, почти
        # сразу встречает полку, и «путь в контакте» тянулся бы через обе. С
        # одной преградой контакт прерывается только просветом в самой детали,
        # а после того как деталь прошла её целиком, возобновиться не может.
        # Препятствие — первая встреченная преграда.
        first: float | None = None
        span = 0.0
        n = int(max_travel / step)
        pieces = [g for g in getattr(c, "geoms", [c]) if g.area > 0]
        for piece in pieces:
            p_first: float | None = None
            p_last: float | None = None
            pierced = False
            for k in range(1, n + 1):
                d = k * step
                if affinity.translate(p, 0, direction * d).intersection(piece).area > 1e-4:
                    p_first = d if p_first is None else p_first
                    p_last = d
                elif p_last is not None and d - p_last > part_extent + step:
                    pierced = True
                    break
            if p_first is None or (first is not None and p_first >= first):
                continue
            first = p_first
            span = (p_last + step - p_first) if pierced or p_last < max_travel - step \
                else max_travel - p_first           # не пробили за весь ход
        thickness = 0.0 if first is None else max(0.0, span - part_extent)
        if worst is None or thickness < worst[0]:
            worst = (thickness, first, float(a), part_extent, span)

    if worst is None:
        return CheckResult(
            id=id or f"{part_name}.obstruction", title="Толщина препятствия",
            # Не «пропуск», а ошибка: проверку объявили, а она ничего не
            # измерила — чаще всего опечатка в `at` или `band`. Пропуск итог не
            # красит, и объявленный замер молча выпадал из вердикта.
            group="fit", status="error",
            message=(f"{part_name}: в сечениях {plane}={ats} нечего мерить — в полосе "
                     f"замера нет ни детали, ни препятствия. Проверьте at и band "
                     f"(section покажет, где они проходят)"),
            inputs=(part_name, holder_name))

    thickness, first, a, part_extent, span = worst
    ok = thickness >= min_thickness
    where = f"на {plane}={a:g}"
    if first is None:
        msg = (f"{part_name} {where}: препятствия нет вовсе за {max_travel:g} мм хода — "
               f"деталь ничем не удерживается")
    else:
        msg = (f"{part_name} {where}: свободный ход {first:.2f} мм, затем препятствие "
               f"толщиной {thickness:.2f} мм {'≥' if ok else '<'} {min_thickness:.2f} "
               f"(путь в контакте {span:.2f} мм за вычетом размера детали {part_extent:.2f})")
    return CheckResult(
        id=id or f"{part_name}.obstruction", title="Толщина препятствия",
        group="fit", status="pass" if ok else "fail", message=msg,
        value=thickness, unit="мм", op=">=", threshold=min_thickness,
        inputs=(part_name, holder_name),
        duration_ms=(time.perf_counter() - t0) * 1000,
        evidence=(Artifact(kind="json", caption="сечение и марш",
                           data={"plane": plane, "at": a, "band": list(band),
                                 "direction": direction, "free_travel_mm": first,
                                 "contact_span_mm": span, "part_extent_mm": part_extent,
                                 "thickness_mm": thickness}),))


def tunnel_wall(part: Solid, *, axis: str, centre: tuple[float, float], d_tunnel: float,
                span: tuple[float, float], wall: float = 1.0, tol_frac: float = 0.02,
                id: str = "", name: str = "") -> CheckResult:
    """Стенка вокруг туннеля: кольцо d…d+2·wall обязано лежать в материале.

    Порт `check4.tunnel_wall`, допуск тот же — не более 2 % объёма кольца.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    a0, a1 = span
    if axis == "x":
        outer, inner = cyl_x(d_tunnel + 2 * wall, a0, a1, *centre), cyl_x(d_tunnel, a0 - 1, a1 + 1, *centre)
    elif axis == "y":
        outer, inner = cyl_y(d_tunnel + 2 * wall, a0, a1, *centre), cyl_y(d_tunnel, a0 - 1, a1 + 1, *centre)
    elif axis == "z":
        outer, inner = cyl_z(d_tunnel + 2 * wall, a0, a1, *centre), cyl_z(d_tunnel, a0 - 1, a1 + 1, *centre)
    else:
        raise ValueError(f"ось должна быть 'x', 'y' или 'z', получено {axis!r}")

    ring = outer - inner
    ring_v = vol(ring)
    lack = vol(ring - part)
    limit = tol_frac * ring_v
    ok = lack <= limit
    return CheckResult(
        id=id or f"{name}.tunnel_wall.{axis}", title="Стенка туннеля", group="fit",
        status="pass" if ok else "fail",
        message=(f"{name}: стенка туннеля Ø{d_tunnel:g} по оси {axis} — нехватка материала "
                 f"{lack:.2f} мм³ из {ring_v:.1f} "
                 f"({100 * lack / max(ring_v, 1e-9):.1f} %, порог {100 * tol_frac:.0f} %)"),
        value=lack, unit="мм³", op="<=", threshold=limit,
        inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)
