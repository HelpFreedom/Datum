"""Контур предмета в плане — для ложемента под реальную вещь.

Мышь в ящике, телефон в подставке, пульт в гнезде: карман должен повторять
предмет, а не его габаритный прямоугольник. Чаще всего у предмета есть скан
или чужая модель; из неё нужен КОНТУР В ПЛАНЕ нижней части — той, которую
обнимет карман.

ТОЧНАЯ ТЕНЬ, А НЕ СЛОИ. Напрашивается объединить сечения через миллиметр —
так и делают, — но тогда выступ между двумя сечениями пропадает, и карман
выходит теснее предмета ровно там, где это хуже всего. Здесь слой предмета
от `z0` до `z1` вырезается целиком и проецируется на XY (`Manifold.project`):
это тень, у которой шага нет вовсе.

ВЫРАВНИВАНИЕ — по наименьшему описанному прямоугольнику тени, а не по главной
оси: та тянется к выступам и к местам, где скан подробнее (см. `align_long_axis`).

Скан обычно неточен по масштабу. `fit_extents` подгоняет его к размерам,
намеренным штангенциркулем, — сами размеры при этом объявляются параметрами
`measured` с источником, а коэффициенты видны в отчёте.
"""

from __future__ import annotations

import math

import numpy as np
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon

from datum.geom.measure import _polys_from_cross_section, bbox, man
from datum.geom.solid import Solid

__all__ = ["align_long_axis", "fit_extents", "footprint", "min_square_angle"]


def align_long_axis(solid: Solid) -> tuple[Solid, float]:
    """Повернуть вокруг Z так, чтобы длинная сторона предмета легла вдоль X.

    Потом — центр габарита в начало координат по XY и низ на Z=0. Возвращает
    тело и угол поворота в градусах (чтобы его можно было записать).

    Критерий — НАИМЕНЬШИЙ ОПИСАННЫЙ ПРЯМОУГОЛЬНИК тени предмета, а не главная
    ось. Замер на скане телефона из корпуса: он лежал ровно по осям, а главная
    ось площади поверхности ушла на 1.7° из-за выступа камеры — и карман под
    него вырос на 2…5 мм. Для кармана важен именно описанный прямоугольник.
    """
    from datum.geom.xform import move, rotate

    m = man(solid)
    if m.is_empty():
        raise ValueError("пустое тело — выравнивать нечего")
    rect = _polys_from_cross_section(m.project()).minimum_rotated_rectangle
    corners = list(rect.exterior.coords)[:4]
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(2)]
    (ax, ay), (bx, by) = max(edges, key=lambda e: math.dist(*e))
    angle = -math.degrees(math.atan2(by - ay, bx - ax))
    # Сторона без направления: ±180° — одно и то же. Берём ближайший к нулю
    # угол, чтобы почти выровненный предмет не переворачивался задом наперёд.
    angle = (angle + 90.0) % 180.0 - 90.0
    if abs(angle) < 1e-9:
        angle = 0.0
    turned = rotate(solid, rz=angle) if angle else solid
    lo, hi = bbox(turned)
    return move(turned, dx=-(lo[0] + hi[0]) / 2, dy=-(lo[1] + hi[1]) / 2, dz=-lo[2]), angle


def fit_extents(solid: Solid, size: tuple[float, float, float]) -> tuple[Solid, tuple[float, float, float]]:
    """Растянуть по осям до намеренного габарита. Возвращает тело и коэффициенты.

    Коэффициент дальше 5 % от единицы — повод остановиться: скан так не врёт,
    скорее перепутаны оси или единицы. Решает человек, поэтому это не отказ, а
    число в ответе.
    """
    from datum.geom.xform import scale

    lo, hi = bbox(solid)
    ext = [h - l for l, h in zip(lo, hi)]
    if any(e <= 0 for e in ext) or any(s <= 0 for s in size):
        raise ValueError(f"габарит {ext} или цель {size} не положительны")
    k = tuple(float(s) / e for s, e in zip(size, ext))
    return scale(solid, *k), k


#: Зазор меньше — ноль (см. `footprint`).
CLEARANCE_EPS_MM = 1e-9


def footprint(solid: Solid, z0: float | None = None, z1: float | None = None, *,
              clearance: float = 0.0, simplify: float = 0.0,
              keep_holes: bool = False) -> Polygon | MultiPolygon:
    """Тень слоя предмета от `z0` до `z1` на плоскость XY.

    Без `z0`/`z1` — от низа до верха. `clearance` раздувает контур на зазор
    посадки (скругляя внешние углы — так и печатается карман). `simplify` —
    допуск упрощения в мм: у скана тысячи точек, и карману они не нужны.

    Дыры в тени по умолчанию ЗАЛИВАЮТСЯ: карману нужен внешний контур, а
    остров посреди ложемента почти всегда не замысел, а свойство модели —
    у скана телефона из корпуса это вырез под камеру (модель оказалась
    чехлом). `keep_holes=True` оставляет их.
    """
    from manifold3d import Manifold

    m = man(solid)
    lo, hi = bbox(m)
    z0 = lo[2] if z0 is None else z0
    z1 = hi[2] if z1 is None else z1
    if z1 <= z0:
        raise ValueError(f"слой пуст: z от {z0:g} до {z1:g}")
    pad = 1.0
    # Слой режется по габариту предмета: тень от этого не меняется, а кубик
    # высотой 1e308 вырождал пересечение — тень выходила пустой, а габарит nan.
    c0, c1 = max(z0, lo[2] - pad), min(z1, hi[2] + pad)
    slab = Manifold() if c1 <= c0 else (
        m ^ Manifold.cube((hi[0] - lo[0] + 2 * pad, hi[1] - lo[1] + 2 * pad, c1 - c0))
        .translate((lo[0] - pad, lo[1] - pad, c0)))
    if slab.is_empty():
        raise ValueError(f"в слое z {z0:g}…{z1:g} предмета нет (он от {lo[2]:g} до {hi[2]:g})")
    shadow = _polys_from_cross_section(slab.project())
    if not keep_holes:
        shadow = _fill_holes(shadow)
    # Меньше нанометра — это ноль: GEOS при раздувании на 1e-200 возвращает
    # ПУСТОЙ контур (замерено), и карман выходил нулевым.
    if clearance > CLEARANCE_EPS_MM:
        before = shadow
        shadow = shadow.buffer(clearance, join_style=1)
        if shadow.is_empty and not before.is_empty:          # pragma: no cover — страж
            raise ValueError(f"раздувание на {clearance:g} мм дало пустой контур")
    if simplify > 0:
        shadow = shadow.simplify(simplify, preserve_topology=True)
    return shadow


def _fill_holes(geom):
    from shapely.ops import unary_union

    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return unary_union([Polygon(p.exterior) for p in parts if not p.is_empty])


def holes_of(geom) -> int:
    """Сколько дыр в контуре — чтобы отчёт мог сказать, сколько залито."""
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return sum(len(p.interiors) for p in parts)


def min_square_angle(geom, resolution_deg: float = 0.05) -> tuple[float, float]:
    """Поворот в плане, при котором описанный квадрат наименьший.

    Нужен, чтобы вещь легла в ячейку сетки поменьше: мышь по диагонали влезает
    в 3×3, а прямо — только в 4×3. Возвращает угол (градусы) и сторону квадрата.
    Грубый перебор через градус, потом уточнение вокруг лучшего.
    """
    hull = geom.convex_hull

    def side(a: float) -> float:
        x0, y0, x1, y1 = affinity.rotate(hull, a, origin="centroid").bounds
        return max(x1 - x0, y1 - y0)

    best = min(np.arange(0.0, 90.0, 1.0), key=side)
    step = 1.0
    while step > resolution_deg:
        step /= 4
        best = min(np.arange(best - 4 * step, best + 4 * step + 1e-12, step), key=side)
    best = float(best) % 90.0
    return best, side(best)
