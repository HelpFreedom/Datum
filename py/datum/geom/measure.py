"""Измерения по геометрии.

ВНИМАНИЕ: всё здесь ФОРСИРУЕТ ленивый расчёт manifold3d. В скриптах деталей
измерения ставятся осознанно, а не в цикле построения.

Сюда же собраны «протезы зрения», которыми пользовался агент в рабочем процессе
CAD: сечения (`section_*`), карта занятости (`occupancy`) и лучевой зонд
(`ray_probe`) — последний заменяет ручной шаг точкой вдоль направления, который
в `check_asm13.py` был написан циклом `while d < 4.0: d += 0.05`.
"""

from __future__ import annotations

import numpy as np
from manifold3d import Manifold
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from datum.geom.solid import Solid

__all__ = [
    "man", "vol", "area", "bbox", "extents", "centre", "com",
    "inter_vol", "min_gap", "section_xy", "section_xz", "section_yz",
    "ray_hit", "ray_probe", "thickness_at", "occupancy", "occupancy_ascii",
]


def man(x: Solid | Manifold) -> Manifold:
    """Manifold тела (или сам Manifold) — для прямых вызовов manifold3d."""
    return x.manifold if isinstance(x, Solid) else x


# ------------------------------------------------------------------ скаляры
def vol(x: Solid | Manifold) -> float:
    """Объём, мм³."""
    m = man(x)
    return 0.0 if m.is_empty() else float(m.volume())


def area(x: Solid | Manifold) -> float:
    """Площадь поверхности, мм²."""
    m = man(x)
    return 0.0 if m.is_empty() else float(m.surface_area())


def bbox(x: Solid | Manifold) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Габарит: ((xmin, ymin, zmin), (xmax, ymax, zmax))."""
    b = man(x).bounding_box()
    return (tuple(b[:3]), tuple(b[3:]))


def extents(x: Solid | Manifold) -> tuple[float, float, float]:
    """Размеры габарита по осям (dx, dy, dz)."""
    lo, hi = bbox(x)
    return tuple(hi[i] - lo[i] for i in range(3))


def centre(x: Solid | Manifold) -> tuple[float, float, float]:
    """Центр габаритного ящика (не центр масс)."""
    lo, hi = bbox(x)
    return tuple((lo[i] + hi[i]) / 2 for i in range(3))


def com(x: Solid | Manifold) -> tuple[float, float, float]:
    """Центр масс при однородной плотности — через trimesh."""
    from datum.geom.convert import to_trimesh

    return tuple(float(v) for v in to_trimesh(man(x)).center_mass)


def inter_vol(a: Solid | Manifold, b: Solid | Manifold) -> float:
    """Объём пересечения. Основной измеритель интерференции во всём корпусе."""
    return vol(man(a) ^ man(b))


def min_gap(a: Solid | Manifold, b: Solid | Manifold, search: float = 2.0) -> float:
    """Минимальный зазор. search ограничивает поиск — без него медленно."""
    return float(man(a).min_gap(man(b), search))


# ------------------------------------------------------------------ сечения
def _polys_from_cross_section(cs_obj) -> Polygon | MultiPolygon:
    """Контуры CrossSection → shapely с правильной вложенностью.

    Собираем через symmetric_difference (порт из `mf.py`): вложенные контуры так
    правильно превращаются в дыры, а простое объединение их бы залило.
    """
    polys = [Polygon(p) for p in cs_obj.to_polygons() if len(p) >= 3]
    if not polys:
        return Polygon()
    g = Polygon()
    for p in polys:
        g = g.symmetric_difference(p.buffer(0))
    return g


def section_xy(x: Solid | Manifold, z: float) -> Polygon | MultiPolygon:
    """Сечение горизонтальной плоскостью на высоте z."""
    return _polys_from_cross_section(man(x).slice(z))


def _section_rotated(x: Solid | Manifold, rot: tuple[float, float, float],
                     height: float) -> Polygon | MultiPolygon:
    return _polys_from_cross_section(man(x).rotate(rot).slice(height))


def section_xz(x: Solid | Manifold, y: float) -> Polygon | MultiPolygon:
    """Сечение вертикальной плоскостью y = const. Координаты результата — (x, z).

    `slice(h)` режет по мировому z и отдаёт сечение в (x, y), поэтому тело надо
    развернуть. Выкладка (её легко переврать, поэтому она записана):
        rotate((-90, 0, 0)):  (x, y, z) -> (x, z, -y)
    после поворота z' = -y, значит режем на -y, а координаты сечения (x', y')
    это ровно (x, z) — без смены знака.
    """
    return _polys_from_cross_section(man(x).rotate((-90, 0, 0)).slice(-y))


def section_yz(x: Solid | Manifold, xv: float) -> Polygon | MultiPolygon:
    """Сечение вертикальной плоскостью x = const. Координаты результата — (y, z).

    Одним поворотом нужную перестановку осей не получить, нужны два:
        rotate((0, -90, 0)):  (x, y, z)  -> (-z,  y, x)
        rotate((0, 0, -90)):  (a, b, c)  -> ( b, -a, c)
    в сумме (x, y, z) -> (y, z, x): z' = x, режем на xv, сечение (y, z).
    """
    return _polys_from_cross_section(
        man(x).rotate((0, -90, 0)).rotate((0, 0, -90)).slice(xv))


# ------------------------------------------------------------------ лучи и точки
def section_moments(geom) -> tuple[float, float, float, float, float, float]:
    """Площадь, центр и моменты инерции плоского сечения относительно центра.

    Возвращает (A, cu, cv, Iuu, Ivv, Iuv), где Iuu = ∫(u−cu)² dA,
    Ivv = ∫(v−cv)² dA, Iuv = ∫(u−cu)(v−cv) dA; u, v — координаты сечения.
    Считается точно по формулам Грина для многоугольника: внешний контур
    против часовой стрелки, дыры — по часовой, и они вычитаются сами.
    """
    from shapely.geometry.polygon import orient

    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    a = su = sv = suu = svv = suv = 0.0
    for poly in parts:
        if poly.is_empty:
            continue
        poly = orient(poly, 1.0)
        for ring in [poly.exterior, *poly.interiors]:
            xy = np.asarray(ring.coords, dtype=float)
            x0, y0 = xy[:-1, 0], xy[:-1, 1]
            x1, y1 = xy[1:, 0], xy[1:, 1]
            c = x0 * y1 - x1 * y0
            a += c.sum() / 2
            su += ((x0 + x1) * c).sum() / 6
            sv += ((y0 + y1) * c).sum() / 6
            suu += ((x0 * x0 + x0 * x1 + x1 * x1) * c).sum() / 12
            svv += ((y0 * y0 + y0 * y1 + y1 * y1) * c).sum() / 12
            suv += ((x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * c).sum() / 24
    if a <= 0:
        raise ValueError("сечение пустое — моменты инерции не определены")
    cu, cv = su / a, sv / a
    return a, cu, cv, suu - a * cu * cu, svv - a * cv * cv, suv - a * cu * cv


def contains(x: Solid | Manifold, pts: np.ndarray) -> np.ndarray:
    """Точки внутри тела. Через trimesh: у manifold3d нет массового теста."""
    from datum.geom.convert import to_trimesh

    pts = np.asarray(pts, dtype=float).reshape(-1, 3)
    return to_trimesh(man(x)).contains(pts)


def ray_hit(x: Solid | Manifold, origin, direction, max_dist: float = 50.0,
            skip_eps: float = 1e-6):
    """Первое попадание луча: (расстояние_мм, точка, нормаль) или None.

    ВАЖНО про API manifold3d: `RayHit.distance` — это ДОЛЯ вдоль отрезка луча
    (0…1), а не миллиметры. Проверено: при длине луча 20/50/100 мм до стенки на
    10 мм значения выходят 0.5/0.2/0.1. Поэтому расстояние считается по
    `position`, а не по `distance` — так оно верно при любой семантике.

    skip_eps отбрасывает попадание в точке старта: зонд толщины стартует прямо
    на поверхности, и без этого он бы упёрся сам в себя.
    """
    o = np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
    # Сперва к наибольшей компоненте, потом к единице: длина (1e308, 1e308,
    # 1e308) переполнялась в бесконечность, и луч «не встречал поверхности».
    scale = float(np.max(np.abs(d))) if d.size else 0.0
    if scale == 0 or not np.isfinite(scale):
        raise ValueError("направление не может быть нулевым")
    d = d / scale
    d = d / float(np.linalg.norm(d))
    hits = man(x).ray_cast(tuple(o), tuple(o + d * max_dist))
    best = None
    for h in hits:
        dist = float(np.linalg.norm(np.asarray(h.position, dtype=float) - o))
        if dist <= skip_eps:
            continue
        if best is None or dist < best[0]:
            best = (dist, tuple(float(v) for v in h.position),
                    tuple(float(v) for v in h.normal))
    return best


def ray_probe(x: Solid | Manifold, origin, direction, max_dist: float = 50.0) -> float | None:
    """Расстояние в мм от точки до первой поверхности вдоль направления.

    Замена ручного шага точкой из `check_asm13.py`, где это было написано циклом
    `while d < 4.0: d += 0.05`. None — луч ничего не встретил.
    """
    hit = ray_hit(x, origin, direction, max_dist)
    return None if hit is None else hit[0]


def thickness_at(x: Solid | Manifold, point, normal, max_dist: float = 50.0) -> float | None:
    """Толщина стенки в точке: луч внутрь тела вдоль −normal до выхода наружу."""
    inward = -np.asarray(normal, dtype=float)
    return ray_probe(x, point, inward, max_dist)


# ------------------------------------------------------------------ карта занятости
def occupancy(x: Solid | Manifold, plane: str, const: float,
              u_range: tuple[float, float], v_range: tuple[float, float],
              step: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Растр «внутри/снаружи» на сетке. Возвращает (маска, оси u, оси v).

    Порт `ascii.py::amap` из рабочего процесса CAD — там это было единственным
    способом «увидеть» деталь. Здесь оно остаётся как быстрая числовая проверка
    (и как вход для ASCII-вывода агенту), а людям есть вьюпорт.
    """
    us = np.arange(u_range[0], u_range[1] + 1e-9, step)
    vs = np.arange(v_range[0], v_range[1] + 1e-9, step)
    U, V = np.meshgrid(us, vs)
    flat = np.full((U.size, 3), float(const))
    plane = plane.upper()
    if plane == "XY":
        flat[:, 0], flat[:, 1] = U.ravel(), V.ravel()
    elif plane == "XZ":
        flat[:, 0], flat[:, 2] = U.ravel(), V.ravel()
    elif plane == "YZ":
        flat[:, 1], flat[:, 2] = U.ravel(), V.ravel()
    else:                                         # pragma: no cover
        raise ValueError(f"плоскость должна быть XY, XZ или YZ, получено {plane!r}")
    mask = contains(x, flat).reshape(V.shape)
    return mask, us, vs


def occupancy_ascii(x: Solid | Manifold, plane: str, const: float,
                    u_range: tuple[float, float], v_range: tuple[float, float],
                    step: float = 1.0, fill: str = "#", void: str = ".") -> str:
    """Карта занятости текстом — компактное «зрение» для агента."""
    mask, us, vs = occupancy(x, plane, const, u_range, v_range, step)
    lines = [f"{plane.upper()} при {const:g}, шаг {step:g} мм, "
             f"u∈[{us[0]:g}, {us[-1]:g}], v∈[{vs[0]:g}, {vs[-1]:g}]"]
    for i in range(len(vs) - 1, -1, -1):          # сверху вниз
        lines.append(f"{vs[i]:7.1f} |" + "".join(fill if c else void for c in mask[i]))
    return "\n".join(lines)
