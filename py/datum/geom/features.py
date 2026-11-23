"""Элементы для печати: кромки, стаканы, отверстия под винт, резьба, накатка.

Словарь подсказан разбором sand3d (там это глаголы поверх Blender), но всё
здесь построено на manifold3d и ТОЧНО для печати — ни одной ступеньки там, где
её не напечатает принтер.

КРОМКИ ВЫДАВЛЕННОГО ПРОФИЛЯ (`slab`). Фаска и скругление нижней и верхней
кромки строятся своей сеткой: контуры профиля смещаются внутрь со «скосом» в
углах (рёбра остаются параллельны исходным, число вершин то же), и соседние
уровни соединяются ПЛОСКИМИ гранями. Фаска — ровно 45°, скругление — дуга из
отрезков с прогибом хорды не больше `CHORD_MM`. Ступенек, как у
`rounded_slab`, нет: у нижней кромки каждая ступенька — горизонтальная грань,
глядящая вниз, и проверка нависаний сочла бы нависанием всю полосу фаски.
Смещение, которое контур не выдерживает (фаска шире скругления угла, узкая
перемычка), — отказ с подсказкой, а не тихо вывернутая сетка.

УГЛЫ В ПЛАНЕ (`round_corners`, `chamfer_corners`) — точно, плоскими
операциями shapely: вертикальные рёбра выдавленного профиля.

Всё возвращает `Solid`; резцы (отверстие, внутренняя резьба) вычитаются
обычным `diff`.
"""

from __future__ import annotations

import math

import numpy as np
from manifold3d import CrossSection, Manifold, Mesh, triangulate
from shapely.geometry import MultiPolygon, Polygon

from datum.geom.prims import CIRC_N, STEP_OVERLAP, cone_z, cyl_z, empty, extrude
from datum.geom.profile2d import MIN_RING_AREA
from datum.geom.solid import Solid

__all__ = ["round_corners", "chamfer_corners", "slab", "cup", "hole_z",
           "thread", "knurl", "CHORD_MM"]

#: Наибольший прогиб хорды на скруглении кромки, мм. Сопло 0.4 его не увидит.
CHORD_MM = 0.02


def _polys(geom) -> list[Polygon]:
    if isinstance(geom, CrossSection):
        geom = MultiPolygon([Polygon(p) for p in geom.to_polygons()]).buffer(0)
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return [p for p in polys if not p.is_empty and p.area >= MIN_RING_AREA]


# ---------------------------------------------------------------- углы в плане
def round_corners(geom, r: float, *, convex: bool = True, concave: bool = True,
                  quad_segs: int = 16):
    """Скруглить углы плоского профиля радиусом r — вертикальные рёбра выдавленного.

    Выпуклые углы скругляются размыканием (внутрь и обратно), вогнутые —
    замыканием (наружу и обратно). ВНИМАНИЕ: размыкание убирает и всё, что
    уже 2r (тонкие перемычки), замыкание — заливает щели уже 2r; такой профиль
    — отказ, а не тихая потеря детали.
    """
    if r < 0:
        raise ValueError(f"round_corners: радиус не бывает отрицательным ({r:g})")
    if r == 0:
        return geom
    out = geom
    if convex:
        out = out.buffer(-r, join_style=1, quad_segs=quad_segs).buffer(r, join_style=1,
                                                                      quad_segs=quad_segs)
    if concave:
        out = out.buffer(r, join_style=1, quad_segs=quad_segs).buffer(-r, join_style=1,
                                                                      quad_segs=quad_segs)
    lost = abs(out.area - geom.area) / max(geom.area, 1e-9)
    if out.is_empty or len(_polys(out)) != len(_polys(geom)) or lost > 0.5:
        raise ValueError(f"round_corners: радиус {r:g} мм больше половины самого узкого места "
                         f"профиля — скругление съело бы его часть; уменьшите радиус")
    # У прямого угла скругление уносит материал не дальше r·(√2 − 1) от нового
    # контура; с запасом на хорды дуги — 0.45·r.
    eaten = _eaten_feature(geom, out, 0.45 * r)
    if eaten:
        raise ValueError(f"round_corners: радиус {r:g} мм — {eaten}: он уже двух радиусов. "
                         f"Уменьшите радиус или скругляйте до того, как добавлен этот элемент")
    return out


def _eaten_feature(before, after, reach: float) -> str:
    """Что скругление или фаска унесли СВЕРХ углов — словами, или пустая строка.

    Размыкание убирает всё, что уже двух радиусов: рёбрышко 10×1 при r = 1
    исчезало из контура молча, хотя докстринг обещал отказ, — число кусков то
    же, площади потеряно 0.4 % (аудит 2026-11-21). Унесённый элемент отличают
    от срезанного угла два признака разом: он уходит от нового контура дальше,
    чем угол (`reach`), и несёт больше одной вершины прежнего контура (у угла
    вершина одна; у ребра — оба его торцевых угла). Одного расстояния мало:
    острый угол скругляется далеко от вершины, и это законно.
    """
    from shapely.geometry import Point

    for what, a, b in (("съеден выступ", before, after), ("залит паз", after, before)):
        gone = a.difference(b)
        for piece in _all_polys(gone):
            if piece.area < 1e-9:
                continue
            ring = np.asarray(piece.exterior.coords)[:-1]
            far = max(b.distance(Point(x, y)) for x, y in ring)
            if far <= reach:
                continue
            probe = piece.buffer(1e-6)
            corners = sum(1 for poly in _all_polys(before)
                          for rg in (poly.exterior, *poly.interiors)
                          for x, y in list(rg.coords)[:-1] if probe.covers(Point(x, y)))
            if corners > 1:
                x0, y0, x1, y1 = piece.bounds
                return (f"{what} {x1 - x0:.2f}×{y1 - y0:.2f} мм у точки "
                        f"({(x0 + x1) / 2:.1f}, {(y0 + y1) / 2:.1f})")
    return ""


def _all_polys(geom) -> list[Polygon]:
    polys = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return [p for p in polys if isinstance(p, Polygon) and not p.is_empty]


def chamfer_corners(geom, d: float):
    """Срезать выпуклые углы плоского профиля фаской: у прямого угла катет d."""
    if d < 0:
        raise ValueError(f"chamfer_corners: катет не бывает отрицательным ({d:g})")
    if d == 0:
        return geom
    out = geom.buffer(-d, join_style=2).buffer(d, join_style=3)
    if out.is_empty or len(_polys(out)) != len(_polys(geom)):
        raise ValueError(f"chamfer_corners: фаска {d:g} мм шире половины самого узкого места "
                         f"профиля; уменьшите её")
    # У прямого угла фаска уносит материал не дальше d/√2 от нового контура.
    eaten = _eaten_feature(geom, out, 0.75 * d)
    if eaten:
        raise ValueError(f"chamfer_corners: фаска {d:g} мм — {eaten}: он уже двух катетов. "
                         f"Уменьшите фаску или снимайте её до того, как добавлен этот элемент")
    return out


# ---------------------------------------------------------------- кромки плиты
def _clean(ring: np.ndarray) -> np.ndarray:
    """Без повторов соседних точек: у нулевого ребра нет нормали."""
    keep = np.linalg.norm(ring - np.roll(ring, 1, axis=0), axis=1) > 1e-9
    return ring[keep]


def _inset(ring: np.ndarray, d: float) -> np.ndarray:
    """Кольцо, смещённое на d в сторону материала, со скосом в углах.

    Материал — СЛЕВА по ходу кольца (внешний контур против часовой, дыры по
    часовой). Вершина уходит по биссектрисе на d / cos(половины угла поворота):
    рёбра остаются параллельны исходным, вершин столько же.
    """
    if d == 0:
        return ring.copy()
    prev, nxt = np.roll(ring, 1, axis=0), np.roll(ring, -1, axis=0)
    e1, e2 = ring - prev, nxt - ring
    e1 /= np.linalg.norm(e1, axis=1)[:, None]
    e2 /= np.linalg.norm(e2, axis=1)[:, None]
    n1 = np.stack([-e1[:, 1], e1[:, 0]], axis=1)
    n2 = np.stack([-e2[:, 1], e2[:, 0]], axis=1)
    denom = 1.0 + (n1 * n2).sum(axis=1)
    if np.any(denom < 1e-6):
        raise ValueError("контур разворачивается назад (угол около 360°) — кромку не построить")
    return ring + d * (n1 + n2) / denom[:, None]


def _oriented(poly: Polygon) -> list[np.ndarray]:
    """Кольца многоугольника: внешнее против часовой, дыры по часовой, без повторов."""
    ext = np.asarray(poly.exterior.coords)[:-1]
    if not Polygon(ext).exterior.is_ccw:
        ext = ext[::-1]
    out = [_clean(ext)]
    for hole in poly.interiors:
        h = np.asarray(hole.coords)[:-1]
        if Polygon(h).exterior.is_ccw:
            h = h[::-1]
        out.append(_clean(h))
    return out


def _check_inset(rings: list[np.ndarray], moved: list[np.ndarray], d: float, what: str) -> None:
    """Смещённый контур обязан остаться тем же контуром: рёбра в ту же сторону,
    многоугольник правильный. Иначе — понятный отказ."""
    for ring, new in zip(rings, moved):
        a = np.roll(ring, -1, axis=0) - ring
        b = np.roll(new, -1, axis=0) - new
        if np.any((a * b).sum(axis=1) <= 0):
            raise ValueError(
                f"{what} {d:g} мм больше, чем позволяет профиль: ребро контура схлопнулось "
                f"(узкая перемычка, мелкий выступ или скругление угла радиусом меньше {d:g} "
                f"мм). Уменьшите {what} или скруглите углы радиусом не меньше {d:g}")
    if not Polygon(moved[0], moved[1:]).is_valid:
        raise ValueError(f"{what} {d:g} мм больше, чем позволяет профиль: смещённый контур "
                         f"пересёк сам себя или дыру. Уменьшите {what}")


def _arc_steps(r: float) -> int:
    if r <= 0:
        return 0
    step = 2 * math.acos(max(-1.0, 1 - CHORD_MM / r))
    return max(3, math.ceil((math.pi / 2) / step))


def _levels(z0: float, z1: float, chamfer_bot: float, chamfer_top: float,
            fillet_bot: float, fillet_top: float) -> list[tuple[float, float]]:
    """Уровни (смещение внутрь, высота) снизу вверх."""
    if chamfer_bot and fillet_bot or chamfer_top and fillet_top:
        raise ValueError("slab: у одной кромки либо фаска, либо скругление, не то и другое")
    bottom: list[tuple[float, float]] = [(0.0, z0)]
    if chamfer_bot:
        bottom = [(chamfer_bot, z0), (0.0, z0 + chamfer_bot)]
    elif fillet_bot:
        k = _arc_steps(fillet_bot)
        bottom = [(fillet_bot * (1 - math.sin(a)), z0 + fillet_bot * (1 - math.cos(a)))
                  for a in np.linspace(0, math.pi / 2, k + 1)]
    top: list[tuple[float, float]] = [(0.0, z1)]
    if chamfer_top:
        top = [(0.0, z1 - chamfer_top), (chamfer_top, z1)]
    elif fillet_top:
        k = _arc_steps(fillet_top)
        top = [(fillet_top * (1 - math.sin(a)), z1 - fillet_top * (1 - math.cos(a)))
               for a in np.linspace(math.pi / 2, 0, k + 1)]
    if bottom[-1][1] > top[0][1] + 1e-9:
        raise ValueError(f"slab: кромки ({bottom[-1][1] - z0:g} снизу и {z1 - top[0][1]:g} сверху) "
                         f"выше самой плиты {z1 - z0:g} мм")
    if abs(bottom[-1][1] - top[0][1]) < 1e-9:
        top = top[1:]                     # кромки сошлись: общий уровень — один
    return bottom + top


def slab(geom, z0: float, z1: float, *, chamfer_bot: float = 0.0, chamfer_top: float = 0.0,
         fillet_bot: float = 0.0, fillet_top: float = 0.0, name: str = "") -> Solid:
    """Плоский профиль, выдавленный от z0 до z1, с фаской или скруглением кромок.

    Фаска — 45°, катет `chamfer_*` по горизонтали и вертикали. Нижняя фаска
    0.3…0.6 мм — против «слоновьей ноги» первого слоя. Скругление —
    `fillet_*` радиусом, дуга по `CHORD_MM`. Углы в плане скругляют до этого
    `round_corners`; радиус угла не меньше кромки, иначе контур схлопнется
    (тогда — отказ с подсказкой). Без кромок — то же, что `extrude`.
    """
    for label, v in (("chamfer_bot", chamfer_bot), ("chamfer_top", chamfer_top),
                     ("fillet_bot", fillet_bot), ("fillet_top", fillet_top)):
        if v < 0:
            raise ValueError(f"slab: {label} не бывает отрицательным ({v:g})")
    if not z1 > z0:
        raise ValueError(f"slab: z1 {z1:g} должен быть больше z0 {z0:g}")
    if not (chamfer_bot or chamfer_top or fillet_bot or fillet_top):
        return extrude(geom, z0, z1, name=name)
    levels = _levels(z0, z1, chamfer_bot, chamfer_top, fillet_bot, fillet_top)
    deepest = max(d for d, _ in levels)
    what = "фаска" if (chamfer_bot or chamfer_top) else "скругление кромки"

    verts: list[np.ndarray] = []
    tris: list[np.ndarray] = []
    count = 0
    for poly in _polys(geom):
        rings = _oriented(poly)
        _check_inset(rings, [_inset(r, deepest) for r in rings], deepest, what)
        for d, _ in levels:
            if 0 < d < deepest:
                _check_inset(rings, [_inset(r, d) for r in rings], d, what)
        # Вершины: кольцо за кольцом, уровень за уровнем.
        base: list[list[int]] = []          # [кольцо][уровень] → индекс первой вершины
        for ring in rings:
            per_level = []
            for d, z in levels:
                pts = _inset(ring, d)
                verts.append(np.c_[pts, np.full(len(pts), z)])
                per_level.append(count)
                count += len(pts)
            base.append(per_level)
        # Боковые грани: материал слева по ходу кольца — наружу смотрит правая сторона.
        for ring, per_level in zip(rings, base):
            n = len(ring)
            i = np.arange(n)
            j = (i + 1) % n
            for k in range(len(levels) - 1):
                a, b = per_level[k] + i, per_level[k] + j
                c, d_ = per_level[k + 1] + j, per_level[k + 1] + i
                tris.append(np.stack([a, b, c], axis=1))
                tris.append(np.stack([a, c, d_], axis=1))
        # Торцы: триангуляция колец нижнего и верхнего уровня (CCW — нормаль вверх).
        for level, flip in ((0, True), (len(levels) - 1, False)):
            loops = [_inset(r, levels[level][0]) for r in rings]
            local = triangulate([np.asarray(p, dtype=np.float64) for p in loops])
            index = np.concatenate([np.arange(len(p)) + base[ri][level]
                                    for ri, p in enumerate(loops)])
            mapped = index[local]
            tris.append(mapped[:, ::-1] if flip else mapped)
    if not verts:
        return empty(name)
    mesh = Mesh(vert_properties=np.ascontiguousarray(np.concatenate(verts), dtype=np.float32),
                tri_verts=np.ascontiguousarray(np.concatenate(tris), dtype=np.uint32))
    man = Manifold(mesh)
    status = str(man.status()).removeprefix("Error.")
    if status != "NoError":
        raise ValueError(f"slab: сетка не замкнулась ({status}) — пришлите профиль в отчёт об ошибке")
    return Solid.wrap(man, name=name, op="slab",
                      kwargs={"z0": z0, "z1": z1, "chamfer_bot": chamfer_bot,
                              "chamfer_top": chamfer_top, "fillet_bot": fillet_bot,
                              "fillet_top": fillet_top})


# ---------------------------------------------------------------- стакан
def cup(geom, z0: float, z1: float, wall: float, floor: float, *, name: str = "") -> Solid:
    """Открытый сверху стакан по профилю: стенки `wall` по контуру, дно `floor`.

    Стенка ровно `wall` по нормали к контуру; во вогнутых углах — толще, не
    тоньше. Внутренность, которую стенка не вмещает, — отказ, а не тело без
    полости.
    """
    if wall <= 0 or floor <= 0:
        raise ValueError(f"cup: стенка и дно положительны (wall {wall:g}, floor {floor:g})")
    if floor >= z1 - z0:
        raise ValueError(f"cup: дно {floor:g} не меньше высоты {z1 - z0:g}")
    inner = geom.buffer(-wall, join_style=2)
    if inner.is_empty:
        raise ValueError(f"cup: стенки {wall:g} мм не оставляют места внутри профиля")
    outer = extrude(geom, z0, z1)
    pocket = extrude(inner, z0 + floor, z1 + STEP_OVERLAP)
    out = outer - pocket.manifold
    return out.renamed(name) if name else out


# ---------------------------------------------------------------- отверстие под винт
def hole_z(d: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0, *,
           cbore: tuple[float, float] | None = None,
           csk: float | tuple[float, float] | None = None, n: int = CIRC_N,
           name: str = "") -> Solid:
    """РЕЗЕЦ вертикального отверстия диаметром d от z0 до z1 (вычитается `diff`).

    `cbore=(D, глубина)` — цековка под головку сверху (у z1), `csk=D` или
    `(D, угол)` — зенковка под потайную головку (угол 90° по умолчанию, у z1).
    Резец выходит за z0 и z1 на `STEP_OVERLAP`: без этого на торцах остаются
    плёнки толщиной в округление. Горизонтальное отверстие печатают каплей —
    `teardrop_poly`.
    """
    if d <= 0 or not z1 > z0:
        raise ValueError(f"hole_z: диаметр положительный и z1 > z0 (d {d:g}, z {z0:g}…{z1:g})")
    if cbore and csk:
        raise ValueError("hole_z: либо цековка, либо зенковка")
    parts = [cyl_z(d, z0 - STEP_OVERLAP, z1 + STEP_OVERLAP, x, y, n=n)]
    if cbore:
        big, depth = cbore
        if big <= d or not 0 < depth < z1 - z0:
            raise ValueError(f"hole_z: цековка (D {big:g}, глубина {depth:g}) — шире отверстия "
                             f"и мельче его длины {z1 - z0:g}")
        parts.append(cyl_z(big, z1 - depth, z1 + STEP_OVERLAP, x, y, n=n))
    if csk:
        big, angle = (csk, 90.0) if isinstance(csk, (int, float)) else csk
        if big <= d or not 0 < angle < 180:
            raise ValueError(f"hole_z: зенковка (D {big:g}, угол {angle:g}°) — шире отверстия")
        fall = (big - d) / 2 / math.tan(math.radians(angle) / 2)
        if fall >= z1 - z0:
            raise ValueError(f"hole_z: зенковка глубже ({fall:.2f} мм) самого отверстия")
        parts.append(cone_z(d, big, z1 - fall, z1, x, y, n=n))
        parts.append(cyl_z(big, z1 - STEP_OVERLAP / 2, z1 + STEP_OVERLAP, x, y, n=n))
    from datum.geom.booleans import union

    out = union(parts)
    return out.renamed(name) if name else out


# ---------------------------------------------------------------- резьба
def _iso_section(d: float, pitch: float, grow: float, n: int, left: bool) -> np.ndarray:
    """Сечение винта метрической резьбы (ISO 68-1, основной профиль) плоскостью z = 0.

    Радиус по углу θ — профиль по осевой координате s = θ/2π·P: впадина P/4 на
    малом диаметре, бок 60°, вершина P/8 на большом. `grow` — сдвиг всего
    профиля по радиусу (зазор: у винта минус, у резца гайки плюс).
    """
    depth = 5 / 8 * (math.sqrt(3) / 2) * pitch          # 0.5413 P
    r_major = d / 2 + grow
    r_minor = r_major - depth
    theta = np.linspace(0, 2 * math.pi, n, endpoint=False)
    s = (theta / (2 * math.pi)) % 1.0
    if left:
        s = 1.0 - s
    root, flank, crest = 0.25, 0.3125, 0.125
    t = np.empty_like(s)
    t[s < root] = 0.0
    up = (s >= root) & (s < root + flank)
    t[up] = (s[up] - root) / flank
    top = (s >= root + flank) & (s < root + flank + crest)
    t[top] = 1.0
    down = s >= root + flank + crest
    t[down] = 1.0 - (s[down] - root - flank - crest) / flank
    r = r_minor + depth * t
    return np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)


def thread(d: float, pitch: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0, *,
           clearance: float, internal: bool = False, left: bool = False,
           chamfer: bool = True, n: int = 90, name: str = "") -> Solid:
    """Метрическая резьба d×pitch от z0 до z1: винт или РЕЗЕЦ гайки.

    Винт (`internal=False`) — сам стержень с резьбой. Резец (`internal=True`)
    вычитается из детали `diff` и оставляет в ней гайку. `clearance` —
    ОБЯЗАТЕЛЬНЫЙ зазор по диаметру под печать (у винта профиль сжат на
    clearance/2, у резца расширен): у каждой пары принтер–пластик он свой —
    берите параметр проекта (assumed с диапазоном, развёртка покажет).

    Построена точно: сечение резьбы выдавлено с закруткой 360° на шаг —
    настоящая винтовая поверхность, хорда не больше сотых. `chamfer` срезает
    концы конусом 45° (у винта) или раскрывает (у резца): острый заход нитки
    отламывается и не находит гайку.
    """
    if d <= 0 or pitch <= 0 or not z1 > z0:
        raise ValueError(f"thread: d, pitch положительны и z1 > z0 (d {d:g}, P {pitch:g})")
    if clearance < 0 or clearance > pitch:
        raise ValueError(f"thread: зазор {clearance:g} мм — от 0 до шага {pitch:g}")
    grow = clearance / 2 if internal else -clearance / 2
    lo, hi = (z0 - pitch, z1 + pitch) if internal else (z0, z1)
    length = hi - lo
    section = _iso_section(d, pitch, grow, n, left)
    turns = length / pitch
    sign = -1 if left else 1
    twist = 360.0 * turns * sign
    divisions = max(1, math.ceil(turns * 36))
    # Фаза нитки — от АБСОЛЮТНОЙ высоты: сечение на высоте lo повёрнуто на
    # 360°·lo/P. Тогда винт и гайка, построенные в одних координатах, сходятся
    # при любых z0 — у резца гайки начало ниже на шаг, и без этого нитки
    # расходились по фазе (замерено: 3.6 мм³ пересечения в посадке).
    start = CrossSection([section]).rotate(360.0 * lo / pitch * sign)
    man = start.extrude(length, divisions, twist).translate((0, 0, lo))
    out = Solid.wrap(man, op="thread", kwargs={"d": d, "pitch": pitch, "internal": internal})
    depth = 5 / 8 * (math.sqrt(3) / 2) * pitch
    r_major = d / 2 + grow
    if internal:
        out = out ^ cyl_z(2 * r_major + 2, z0 - STEP_OVERLAP, z1 + STEP_OVERLAP).manifold
        if chamfer:
            # Раззенковка 45° на обоих торцах: от малого диаметра резьбы до чуть
            # больше большого — заход винта в гайку.
            from datum.geom.booleans import union

            out = union([out,
                         cone_z(2 * (r_major - depth), 2 * (r_major + 0.1), z1 - depth - 0.1,
                                z1 + STEP_OVERLAP),
                         cone_z(2 * (r_major + 0.1), 2 * (r_major - depth), z0 - STEP_OVERLAP,
                                z0 + depth + 0.1)])
    elif chamfer:
        # Конусы 45° с обоих концов: от малого диаметра на торце наружу.
        cut = (cone_z(2 * (r_major - depth), 2 * (r_major + length), z0, z0 + length + depth)
               ^ cone_z(2 * (r_major + length), 2 * (r_major - depth), z1 - length - depth,
                        z1).manifold)
        out = out ^ cut.manifold
    if (x, y) != (0.0, 0.0):
        from datum.geom.xform import move

        out = move(out, x, y, 0.0)
    return out.renamed(name) if name else out


# ---------------------------------------------------------------- накатка
def knurl(d: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0, *,
          pitch: float = 1.5, depth: float | None = None, angle: float = 30.0,
          name: str = "") -> Solid:
    """Ручка с ромбической накаткой: диаметр d по вершинам, от z0 до z1.

    Две нарезки под ±`angle` к оси пересекаются — ромбы с шагом `pitch` по
    окружности. Глубина по умолчанию — половина шага. Пересечение двух
    выдавливаний с закруткой: точные винтовые грани, без сетки поля.
    """
    if d <= 0 or not z1 > z0 or pitch <= 0 or pitch >= d:
        raise ValueError(f"knurl: d > шага > 0 и z1 > z0 (d {d:g}, шаг {pitch:g})")
    depth = pitch / 2 if depth is None else depth
    teeth = max(6, round(math.pi * d / pitch))
    n = teeth * 8
    theta = np.linspace(0, 2 * math.pi, n, endpoint=False)
    saw = np.abs(((theta * teeth / (2 * math.pi)) % 1.0) * 2 - 1)      # 1 на вершине, 0 во впадине
    r = d / 2 - depth * (1 - saw)
    section = CrossSection([np.stack([r * np.cos(theta), r * np.sin(theta)], axis=1)])
    length = z1 - z0
    twist = math.degrees(length * math.tan(math.radians(angle)) / (d / 2))
    divisions = max(1, math.ceil(abs(twist) / 4))
    a = section.extrude(length, divisions, twist)
    b = section.extrude(length, divisions, -twist)
    man = (a ^ b).translate((x, y, z0))
    out = Solid.wrap(man, op="knurl", kwargs={"d": d, "pitch": pitch, "angle": angle})
    return out.renamed(name) if name else out
