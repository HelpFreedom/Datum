"""Топологические проверки — то, что в корпусе делалось руками после каждой сборки.

Там это выглядело так (`export13.py`, `build_v13.py` и ещё десяток файлов):

    parts = [p for p in mesh.split() if p.volume > 1.0]
    assert len(parts) == 1
    print(extents, watertight, volume)

Здесь то же самое становится проверками с порогами и объяснением.
"""

from __future__ import annotations

import time

from datum.checks.result import Artifact, CheckResult
from datum.geom.guard import Expect, describe_places
from datum.geom.solid import Solid
from datum.geom.topology import SLIVER_MM3, analyze

__all__ = ["no_floating_body", "topology_checks", "single_body", "no_sealed_void", "no_slivers"]


def _mk(id_: str, title: str, status: str, message: str, **kw) -> CheckResult:
    return CheckResult(id=id_, title=title, group="topology", status=status,
                       message=message, **kw)


def single_body(part: Solid, *, expected: int = 1, name: str = "") -> CheckResult:
    """Деталь — одно тело: не распалась на куски после булевых операций.

    Распавшаяся деталь выглядит целой во вьюпорте, а на столе печатается
    отдельными островами. Число тел считается по компонентам с ПОЛОЖИТЕЛЬНЫМ
    объёмом — запаянная полость телом не считается, её ловит `no_sealed_void`.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    rep = analyze(part.manifold)
    ok = rep.bodies == expected
    if ok:
        msg = f"{name}: тел {rep.bodies}, как и задумано"
    elif rep.bodies > expected:
        vols = ", ".join(f"{v:.1f}" for v in rep.body_volumes[:5])
        msg = (f"{name}: РАСПАЛАСЬ на {rep.bodies} тел (объёмы {vols} мм³) — "
               f"ожидалось {expected}")
    else:
        msg = f"{name}: тел всего {rep.bodies}, ожидалось {expected}"
    return _mk(f"{name}.single_body", "Число тел", "pass" if ok else "fail", msg,
               value=rep.bodies, unit="шт", op="==", threshold=expected,
               inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)


def no_sealed_void(part: Solid, *, name: str = "") -> CheckResult:
    """Запаянная внутренняя полость — брак печати: её нечем заполнить.

    Признак — компонента с отрицательным объёмом (см. datum.geom.topology).
    Старый рабочий процесс такие полости не ловил вовсе.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    rep = analyze(part.manifold)
    ok = rep.sealed_voids == 0
    msg = (f"{name}: запаянных полостей нет" if ok else
           f"{name}: ЗАПАЯННЫХ ПОЛОСТЕЙ {rep.sealed_voids} "
           f"({describe_places(rep.void_volumes, rep.void_places, digits=3)}) — "
           f"такую полость нечем заполнить и неоткуда выкачать")
    evidence = () if ok else _void_picture(part, rep, name, msg)
    return _mk(f"{name}.no_sealed_void", "Запаянные полости", "pass" if ok else "fail",
               msg, value=rep.sealed_voids, unit="шт", op="==", threshold=0,
               inputs=(name,), evidence=evidence,
               duration_ms=(time.perf_counter() - t0) * 1000)


def _void_picture(part: Solid, rep, name: str, note: str) -> tuple:
    """Срез через самую крупную полость, сама она — красным.

    Срез идёт поперёк самого тонкого её размера: так полость в нём крупнее
    всего. Красным закрашены ДЫРЫ того же сечения, лежащие в габарите
    полости, — то есть ровно посчитанное, а не нарисованный поверх
    прямоугольник. Отказ рисования провала не отменяет: вместо картинки —
    текст с причиной.
    """
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union

    from datum.checks.evidence import AXIS_TO_PLANE, COLOUR_A, COLOUR_BAD, _render, section_of

    if not rep.void_places:
        return ()
    try:
        place = rep.void_places[0]
        axis = min(range(3), key=lambda k: place.size[k])
        plane, at = AXIS_TO_PLANE[axis], float(place.centre[axis])
        u, v = [k for k in range(3) if k != axis]       # оси сечения — как у section_*
        pad = 1e-3
        window = box(place.lo[u] - pad, place.lo[v] - pad, place.hi[u] + pad, place.hi[v] + pad)
        cut = section_of(part, plane, at)
        polys = getattr(cut, "geoms", [cut])
        holes = [Polygon(ring) for poly in polys for ring in getattr(poly, "interiors", ())]
        inside = [h for h in holes if window.contains(h.representative_point())]
        artifact = _render(
            [(cut, name, COLOUR_A),
             (unary_union(inside) if inside else None, "полость", COLOUR_BAD)],
            plane, at, f"{name} — запаянная полость", note)
    except Exception as e:
        return (Artifact(kind="text",
                         caption=f"доказательство не построилось: {type(e).__name__}: {e}"),)
    return (artifact,) if artifact is not None else ()


def no_slivers(part: Solid, *, sliver_mm3: float = SLIVER_MM3, max_count: int = 0,
               name: str = "") -> CheckResult:
    """«Лезвия» — мусор почти совпадающих граней в булевых операциях."""
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    rep = analyze(part.manifold, sliver_mm3=sliver_mm3)
    ok = rep.slivers <= max_count
    msg = (f"{name}: лезвий нет" if ok else
           f"{name}: лезвий {rep.slivers} (тоньше {sliver_mm3} мм³: "
           f"{describe_places(rep.sliver_volumes, rep.sliver_places, digits=2)}) — "
           f"мусор почти совпадающих граней, в слайсере даст артефакты")
    return _mk(f"{name}.no_slivers", "Лезвия", "pass" if ok else "fail", msg,
               value=rep.slivers, unit="шт", op="<=", threshold=max_count,
               inputs=(name,), duration_ms=(time.perf_counter() - t0) * 1000)


def topology_checks(part: Solid, *, expect: Expect | None = None,
                    name: str = "") -> list[CheckResult]:
    """Весь топологический набор разом."""
    expect = expect or Expect()
    out = []
    if expect.bodies is not None:
        out.append(single_body(part, expected=expect.bodies, name=name))
    if not expect.allow_sealed_voids:
        out.append(no_sealed_void(part, name=name))
    out.append(no_slivers(part, sliver_mm3=expect.sliver_mm3,
                          max_count=expect.max_slivers, name=name))
    return out


#: Сколько тел разбирает `no_floating_body`: дальше попарный замер зазоров
#: стоит минуты, а ответа «висит» на таком числе тел всё равно не прочесть.
FLOATING_MAX_BODIES = 200


def no_floating_body(part: Solid, *, gap: float = 0.3, name: str = "") -> CheckResult:
    """Ни одно тело составной детали не висит в воздухе.

    Найдено человеком, а не проверкой («вон камень в воздухе висит, я на него
    смотрю» — показная сборка Gear Cube): тело, которое ни на что не опирается.
    У показной сборки это ошибка модели (узел стоит не там), у печатаемой
    вместе группы — то, что принтер положит в пустоту.

    Тела — компоненты с положительным объёмом. Тело ОПЁРТО, если от него можно
    дойти до основания по касаниям: два тела касаются, когда зазор между ними
    не больше `gap` (печатный зазор посадки — тоже опора: вал в отверстии
    лежит на стенке), а основание — тела, достающие до нижней точки детали.
    """
    t0 = time.perf_counter()
    name = name or part.name or "деталь"
    bodies = [b for b in part.manifold.decompose() if float(b.volume()) > SLIVER_MM3]
    common = dict(inputs=(name,), unit="шт", op="==", threshold=0)

    def done(status: str, message: str, value: float = 0.0) -> CheckResult:
        return _mk(f"{name}.no_floating_body", "Тела без опоры", status, message,
                   value=value, duration_ms=(time.perf_counter() - t0) * 1000, **common)

    if len(bodies) <= 1:
        return done("pass", f"{name}: тело одно — висеть нечему")
    if len(bodies) > FLOATING_MAX_BODIES:
        return done("skip", f"{name}: тел {len(bodies)} — больше {FLOATING_MAX_BODIES}, "
                            f"опора каждого не разбирается")
    boxes = [tuple(float(v) for v in b.bounding_box()) for b in bodies]
    z_min = min(bx[2] for bx in boxes)
    grounded = {i for i, bx in enumerate(boxes) if bx[2] - z_min <= gap}

    def near(i: int, j: int) -> bool:
        a, b = boxes[i], boxes[j]
        # Габариты дальше зазора — тела заведомо не касаются: точный замер
        # зазора (он дорог) нужен только соседям.
        if any(a[k] - b[k + 3] > gap or b[k] - a[k + 3] > gap for k in range(3)):
            return False
        return float(bodies[i].min_gap(bodies[j], gap * 2 + 1e-6)) <= gap + 1e-9

    frontier = list(grounded)
    pending = set(range(len(bodies))) - grounded
    while frontier and pending:
        i = frontier.pop()
        for j in [j for j in pending if near(i, j)]:
            pending.discard(j)
            grounded.add(j)
            frontier.append(j)
    if not pending:
        return done("pass", f"{name}: все {len(bodies)} тел опёрты — на основание или друг "
                            f"на друга (зазор до {gap:g} мм считается касанием)")
    hanging = sorted(pending, key=lambda i: -float(bodies[i].volume()))
    told = []
    for i in hanging[:4]:
        x0, y0, z0, x1, y1, z1 = boxes[i]
        told.append(f"{float(bodies[i].volume()):.1f} мм³ у ({(x0 + x1) / 2:.1f}, "
                    f"{(y0 + y1) / 2:.1f}, {(z0 + z1) / 2:.1f}), низ на z = {z0:.2f}")
    return done("fail",
                f"{name}: ВИСИТ В ВОЗДУХЕ тел: {len(hanging)} из {len(bodies)} — ни на "
                f"основание, ни на другое тело не опираются (зазор больше {gap:g} мм): "
                + "; ".join(told) + (" …" if len(hanging) > 4 else "")
                + ". Посмотрите section или part_view в этом месте: узел стоит не там "
                  "или опора под ним не построена. Если между телами стоит покупное, "
                  "которого нет в модели (подшипник, винт), — добавьте его макет или "
                  "объявите проверку со своим gap",
                value=float(len(hanging)))
