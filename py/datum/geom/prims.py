"""Примитивы. Порт `mf.py`: имена и порядок аргументов сохранены дословно,
чтобы перенос скриптов из рабочего процесса CAD был механическим.

Соглашение `mf.py`: тела задаются ГРАНИЦАМИ (x0, x1, …), а не размером и
положением. Это оказалось удобнее при проектировании посадок, где важны именно
координаты стенок.

Ничто здесь не форсирует расчёт manifold3d.
"""

from __future__ import annotations

import math

from manifold3d import CrossSection, Manifold

from datum.geom.profile2d import cs
from datum.geom.solid import Solid

__all__ = [
    "CIRC_N", "empty", "box", "extrude", "prism_xy", "prism_xz", "prism_yz",
    "cyl_x", "cyl_y", "cyl_z", "cone_z", "sphere", "revolve_z", "rounded_slab",
]

#: Сегментов на полный оборот по умолчанию. 72 — из `mf.py`: для деталей 3D-печати
#: хорда при Ø20 получается 0.87 мм, что заметно тоньше сопла 0.4 мм.
CIRC_N = 72

#: Перекрытие ступеней в `rounded_slab`. Из `mf.py`: плёнки тоньше точности
#: float32 (а STL хранит float32) дают вырожденные грани и разрывы сетки.
STEP_OVERLAP = 0.02


def empty(name: str = "") -> Solid:
    """Пустое тело — нейтральный элемент объединения."""
    return Solid.wrap(Manifold(), name=name, op="empty")


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, *,
        name: str = "") -> Solid:
    """Параллелепипед по границам."""
    return Solid.wrap(
        Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0)),
        name=name, op="box", args=(x0, x1, y0, y1, z0, z1))



def _span(op: str, lo_name: str, lo: float, hi_name: str, hi: float, **sizes: float) -> bool:
    """Есть ли что строить: False — законная пустота (нулевая длина или диаметр).

    Перепутанные границы и отрицательный диаметр — отказ. Прежде они давали
    пустое тело молча: `diff(куб, [cyl_z(5, 11, -1, …)])` возвращал куб без
    отверстия, и страж молчал — тело одно (аудит 2026-11-21). `box` при том же
    отказывал всегда. Нулевая длина остаётся пустотой: фича, сведённая
    параметром к нулю, — законный способ её выключить.
    """
    if hi < lo:
        raise ValueError(f"{op}: {hi_name} = {hi:g} меньше {lo_name} = {lo:g} — границы "
                         f"перепутаны. Тело без длины задают равными границами")
    for key, value in sizes.items():
        if value < 0:
            raise ValueError(f"{op}: {key} = {value:g} — размер не бывает отрицательным")
    return hi > lo and all(v > 0 for v in sizes.values())


def extrude(geom, z0: float, z1: float, *, name: str = "",
            twist_deg: float = 0.0, scale_top: tuple[float, float] = (1.0, 1.0)) -> Solid:
    """Профиль в плоскости XY, выдавленный от z0 до z1."""
    c: CrossSection = cs(geom)
    if not _span("extrude", "z0", z0, "z1", z1) or c.is_empty():
        return empty(name)
    man = c.extrude(z1 - z0, 0, twist_deg, scale_top).translate((0, 0, z0))
    return Solid.wrap(man, name=name, op="extrude", args=(z0, z1))


def _prism(pts, a0: float, a1: float, plane: str, name: str) -> Solid:
    """Профиль в плоскости `plane`, выдавленный по третьей оси от a0 до a1."""
    from shapely.geometry import Polygon

    if not _span(f"prism_{plane}", "начало", a0, "конец", a1):
        return empty(name)
    c = cs(Polygon(pts).buffer(0))
    if c.is_empty():
        return empty(name)
    man = c.extrude(a1 - a0)                      # выдавлено по +Z в локальных осях
    # Профиль лежит в плоскости XY сечения и выдавлен по +Z на (a1 - a0).
    # Дальше его надо развернуть так, чтобы координаты профиля легли на нужные
    # мировые оси БЕЗ смены знака — здесь легко ошибиться, поэтому выкладки:
    #   rotate((90,0,0)):            (x, y, z) -> (x, -z,  y)
    #   rotate((0,90,0)):            (x, y, z) -> (z,  y, -x)
    if plane == "xy":
        man = man.translate((0, 0, a0))
    elif plane == "yz":
        # (py, pz, [0,h]) --rot(0,90,0)--> ([0,h], pz, -py)
        #                 --rot(90,0,0)--> ([0,h], py, pz)      сдвиг по X на a0
        man = man.rotate((0, 90, 0)).rotate((90, 0, 0)).translate((a0, 0, 0))
    elif plane == "xz":
        # (px, pz, [0,h]) --rot(90,0,0)--> (px, [-h,0], pz)     сдвиг по Y на a1
        man = man.rotate((90, 0, 0)).translate((0, a1, 0))
    else:                                         # pragma: no cover
        raise ValueError(f"неизвестная плоскость: {plane}")
    return Solid.wrap(man, name=name, op=f"prism_{plane}", args=(a0, a1))


def prism_xy(pts, z0: float, z1: float, *, name: str = "") -> Solid:
    """Профиль [(x, y)], выдавленный по Z от z0 до z1."""
    return _prism(pts, z0, z1, "xy", name)


def prism_yz(pts, x0: float, x1: float, *, name: str = "") -> Solid:
    """Профиль [(y, z)], выдавленный по X от x0 до x1."""
    return _prism(pts, x0, x1, "yz", name)


def prism_xz(pts, y0: float, y1: float, *, name: str = "") -> Solid:
    """Профиль [(x, z)], выдавленный по Y от y0 до y1."""
    return _prism(pts, y0, y1, "xz", name)


def cyl_z(d: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0,
          n: int = CIRC_N, *, name: str = "") -> Solid:
    """Цилиндр вдоль Z: диаметр d, от z0 до z1, центр в (x, y)."""
    if not _span("cyl_z", "z0", z0, "z1", z1, d=d):
        return empty(name)
    man = Manifold.cylinder(z1 - z0, d / 2, d / 2, n).translate((x, y, z0))
    return Solid.wrap(man, name=name, op="cyl_z", args=(d, z0, z1, x, y))


def _circle_pts(d: float, u: float, v: float, n: int) -> list[tuple[float, float]]:
    r = d / 2
    return [(u + r * math.cos(2 * math.pi * i / n), v + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]


def cyl_x(d: float, x0: float, x1: float, y: float = 0.0, z: float = 0.0,
          n: int = CIRC_N, *, name: str = "") -> Solid:
    """Цилиндр вдоль X: диаметр d, от x0 до x1, ось через (y, z).

    Через призму: цилиндр manifold всегда по Z."""
    if not _span("cyl_x", "x0", x0, "x1", x1, d=d):
        return empty(name)
    s = _prism(_circle_pts(d, y, z, n), x0, x1, "yz", name)
    return s.derive(s.manifold, "cyl_x", d=d, x0=x0, x1=x1, y=y, z=z)


def cyl_y(d: float, y0: float, y1: float, x: float = 0.0, z: float = 0.0,
          n: int = CIRC_N, *, name: str = "") -> Solid:
    """Цилиндр вдоль Y: диаметр d, от y0 до y1, ось через (x, z)."""
    if not _span("cyl_y", "y0", y0, "y1", y1, d=d):
        return empty(name)
    s = _prism(_circle_pts(d, x, z, n), y0, y1, "xz", name)
    return s.derive(s.manifold, "cyl_y", d=d, y0=y0, y1=y1, x=x, z=z)


def cone_z(d0: float, d1: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0,
           n: int = CIRC_N, *, name: str = "") -> Solid:
    """Усечённый конус вдоль Z: диаметр d0 снизу, d1 сверху."""
    if not _span("cone_z", "z0", z0, "z1", z1):
        return empty(name)
    if d0 < 0 or d1 < 0:
        raise ValueError(f"cone_z: диаметры {d0:g} и {d1:g} — размер не бывает отрицательным")
    if d0 == 0 and d1 == 0:
        return empty(name)
    man = Manifold.cylinder(z1 - z0, d0 / 2, d1 / 2, n).translate((x, y, z0))
    return Solid.wrap(man, name=name, op="cone_z", args=(d0, d1, z0, z1, x, y))


def sphere(d: float, x: float = 0.0, y: float = 0.0, z: float = 0.0,
           n: int = CIRC_N, *, name: str = "") -> Solid:
    """Шар диаметром d с центром в (x, y, z)."""
    if d < 0:
        raise ValueError(f"sphere: d = {d:g} — размер не бывает отрицательным")
    if d == 0:
        return empty(name)
    return Solid.wrap(Manifold.sphere(d / 2, n).translate((x, y, z)),
                      name=name, op="sphere", args=(d, x, y, z))


def revolve_z(pts_rz, cx: float = 0.0, cy: float = 0.0, n: int = CIRC_N, *,
              degrees: float = 360.0, name: str = "") -> Solid:
    """Тело вращения вокруг вертикальной оси через (cx, cy). Профиль [(r, z)], r ≥ 0."""
    from shapely.geometry import Polygon

    c = cs(Polygon(pts_rz).buffer(0))
    if c.is_empty():
        return empty(name)
    man = c.revolve(n, degrees).translate((cx, cy, 0))
    return Solid.wrap(man, name=name, op="revolve_z", args=(cx, cy), kwargs={"degrees": degrees})


def rounded_slab(geom, z0: float, z1: float, r_top: float = 0.0, r_bot: float = 0.0,
                 steps: int = 6, *, name: str = "") -> Solid:
    """Выдавливание профиля со скруглением верхней и нижней кромки.

    Настоящего fillet в manifold3d нет, поэтому скругление набирается стопкой
    смещённых профилей. При печати ступени 0.1–0.2 мм неотличимы от скругления.
    Порт из `mf.py` вместе с перекрытием ступеней STEP_OVERLAP.
    """
    from datum.geom.booleans import union

    if not z1 > z0:
        raise ValueError(f"rounded_slab: z1 {z1:g} должен быть больше z0 {z0:g}")
    if r_top < 0 or r_bot < 0:
        raise ValueError(f"rounded_slab: радиусы не бывают отрицательными "
                         f"(r_top {r_top:g}, r_bot {r_bot:g})")
    if r_top + r_bot > (z1 - z0) + 1e-9:
        # Иначе середина уходит «внутрь себя», и блоки скругления выдавливаются
        # ЗА пределы [z0, z1]: плита 2 мм с радиусами по 5 выходила высотой 8 и
        # втрое больше по объёму — без единого слова.
        raise ValueError(
            f"rounded_slab: r_top + r_bot = {r_top + r_bot:g} больше толщины "
            f"{z1 - z0:g} мм. Полное скругление кромки — это радиусы по половине "
            f"толщины, {(z1 - z0) / 2:g}")

    parts: list[Solid] = []
    zc0, zc1 = z0 + r_bot, z1 - r_top
    if zc1 > zc0:
        parts.append(extrude(geom, zc0, zc1))

    for r, sign in ((r_top, 1), (r_bot, -1)):
        if r <= 0:
            continue
        for k in range(steps):
            a0, a1 = k / steps, (k + 1) / steps
            inset = r * (1 - math.cos(math.asin(min(1.0, a1))))
            g = geom.buffer(-inset, join_style=1) if inset > 1e-6 else geom
            if g.is_empty:
                continue
            if sign > 0:
                parts.append(extrude(g, zc1 + r * a0 - STEP_OVERLAP, zc1 + r * a1))
            else:
                parts.append(extrude(g, zc0 - r * a1, zc0 - r * a0 + STEP_OVERLAP))

    out = union(parts)
    return out.renamed(name) if name else out
