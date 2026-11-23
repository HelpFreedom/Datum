"""Плоские профили: shapely → manifold CrossSection.

Порт `cs()` и `rrect_poly()` из `phone-case-gears/work/lib/mf.py`. Две тонкости
оттуда, которые легко потерять и потом долго искать:

  * ориентация колец: внешний контур против часовой, дыры по часовой, и
    `FillRule.EvenOdd` — иначе дыры не прорежутся;
  * контуры короче трёх точек и площадью меньше 1e-6 отбрасываются, иначе
    вырожденные кольца ломают триангуляцию.
"""

from __future__ import annotations

import math

import numpy as np
from manifold3d import CrossSection, FillRule
from shapely.geometry import Polygon
from shapely.geometry import box as _sbox

__all__ = ["rings", "cs", "rrect_poly", "slot_poly", "ring_poly", "teardrop_poly",
           "MIN_RING_AREA"]

MIN_RING_AREA = 1e-6


def rings(geom) -> list[list[tuple[float, float]]]:
    """Разложить shapely (Multi)Polygon на кольца с правильной ориентацией."""
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    out: list[list[tuple[float, float]]] = []
    for p in polys:
        if p.is_empty or p.area < MIN_RING_AREA:
            continue
        ext = list(p.exterior.coords)[:-1]
        if len(ext) < 3:
            continue
        if Polygon(ext).exterior.is_ccw is False:
            ext = ext[::-1]                      # внешний контур — против часовой
        out.append(ext)
        for h in p.interiors:
            hole = list(h.coords)[:-1]
            if len(hole) < 3:
                continue
            if Polygon(hole).exterior.is_ccw:
                hole = hole[::-1]                # дыра — по часовой
            out.append(hole)
    return out


def cs(geom) -> CrossSection:
    """shapely (Multi)Polygon → CrossSection. Дыры по чётности контуров."""
    if isinstance(geom, CrossSection):
        return geom
    rr = rings(geom)
    if not rr:
        return CrossSection()
    return CrossSection([np.asarray(r, dtype=np.float64) for r in rr], FillRule.EvenOdd)


def rrect_poly(x0: float, y0: float, x1: float, y1: float, r: float, *, quad_segs: int = 24):
    """Скруглённый прямоугольник в плане."""
    r = min(r, (x1 - x0) / 2 - 0.01, (y1 - y0) / 2 - 0.01)
    if r <= 0:
        return _sbox(x0, y0, x1, y1)
    return _sbox(x0 + r, y0 + r, x1 - r, y1 - r).buffer(r, quad_segs=quad_segs)


def slot_poly(x0: float, y0: float, x1: float, y1: float, width: float, *, quad_segs: int = 24):
    """Паз: отрезок, «раздутый» на половину ширины (скруглённые торцы)."""
    from shapely.geometry import LineString

    return LineString([(x0, y0), (x1, y1)]).buffer(width / 2, quad_segs=quad_segs)


def ring_poly(d_out: float, d_in: float, *, cx: float = 0.0, cy: float = 0.0, n: int = 72):
    """Кольцо (шайба) в плане."""
    from shapely.geometry import Point

    outer = Point(cx, cy).buffer(d_out / 2, quad_segs=max(4, n // 4))
    if d_in <= 0:
        return outer
    return outer.difference(Point(cx, cy).buffer(d_in / 2, quad_segs=max(4, n // 4)))


def teardrop_poly(d: float, *, cx: float = 0.0, cy: float = 0.0, nose: str = "+y",
                  n: int = 72):
    """Капля: окружность с носиком под 45°, печатаемая без поддержек лёжа.

    Горизонтальное круглое отверстие печатается со сводом, который висит над
    пустотой. Капля заменяет свод двумя скатами по 45° — пластик ложится на
    предыдущий слой. `nose` — куда смотрит носик в плоскости профиля; при
    печати это направление обязано уходить ВВЕРХ, иначе капля бесполезна.

    Порт `teardrop_pts` из `toothpaste-dispenser/work/lib/geom.py`, где она
    стоит на каждом горизонтальном отверстии. Носик — вершина квадрата,
    описанного вокруг окружности: r·√2 от центра.
    """
    dirs = {"+y": (0.0, 1.0), "-y": (0.0, -1.0), "+x": (1.0, 0.0), "-x": (-1.0, 0.0)}
    if nose not in dirs:
        raise ValueError(f"nose={nose!r}: ожидается одно из {sorted(dirs)}")
    from shapely.geometry import Point

    r = d / 2
    ux, uy = dirs[nose]
    circle = Point(cx, cy).buffer(r, quad_segs=max(4, n // 4))
    # Квадрат со стороной r: центр, две точки касания под ±45° к оси носика и
    # сам носик. Скаты касаются окружности, поэтому объединение гладкое.
    k = r / math.sqrt(2)
    tip = (cx + ux * r * math.sqrt(2), cy + uy * r * math.sqrt(2))
    left = (cx + ux * k - uy * k, cy + uy * k + ux * k)
    right = (cx + ux * k + uy * k, cy + uy * k - ux * k)
    return circle.union(Polygon([tip, left, (cx, cy), right]))
