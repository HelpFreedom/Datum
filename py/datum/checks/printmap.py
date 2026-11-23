"""Карта печатности: где нависания, мосты и тонкие стенки — цветом по граням.

ЗАЧЕМ. `support_volume` говорит «41 мм³», но не где: ступени и «зубья» на
подставке под мышь агент искал сечениями, хотя на картинке они видны с
первого взгляда. Здесь — те же вопросы, что у проверок печатности, ответом
по каждой грани.

ТЕ ЖЕ ФУНКЦИИ, ЧТО У ПРОВЕРОК, а не похожие: нависание — `overhang_faces`
(та же маска, что считает площадь `overhang`), мост — `bridge_cells` (ячейки,
по которым `bridge_span` меряет пролёт), тонкое — `thin_regions` (то, что
теряет размыкание `min_wall`). Картинка, расходящаяся с числом, убеждала бы
сильнее числа и врала бы.

ПОЗА — ПЕЧАТИ. Деталь подаётся уже поставленной (`on_bed`), ось вверх — +Z.
Тонкое ищется в плоскостях СЛОЁВ печати: так стенка и печатается — и так же
режет деталь проверка `min_wall` (в позе `print_up`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from datum.geom.solid import Solid

__all__ = ["PrintMap", "print_map", "colors", "CLASSES", "PLAIN"]

#: Класс грани → (подпись, цвет). Цвета — те, что обещаны в плане: красный,
#: жёлтый, синий; ноль — обычная поверхность, цветом детали.
CLASSES = {1: ("нависание", "#e5534b"), 2: ("мост", "#e3b341"), 3: ("тонкая стенка", "#539bf5")}

#: Обычная поверхность на карте — нейтральным серым, а не цветом детали: деталь
#: цвета #c9a96e читалась сплошным «мостом» (#e3b341) при «мостов нет» в
#: подписи (найдено обзором).
PLAIN = "#9aa4b2"


@dataclass(frozen=True)
class PrintMap:
    verts: np.ndarray
    faces: np.ndarray
    labels: np.ndarray                  # класс каждой грани (CLASSES; 0 — обычная)
    overhang_mm2: float                 # нависает круче предела (вместе с мостами)
    bridge_mm2: float                   # из них — мосты
    bridge_span_mm: float               # самый длинный мост, как у bridge_span
    thin_mm2: float                     # площадь граней тонких стенок
    thin_piece_mm2: float               # крупнейший тонкий кусок сечения
    thin_at: float | None               # высота слоя с ним
    notes: tuple[str, ...] = ()

    def summary(self) -> str:
        parts = [f"нависания {self.overhang_mm2:.0f} мм²"]
        if self.bridge_mm2:
            parts.append(f"из них мосты {self.bridge_mm2:.0f} мм² (пролёт до "
                         f"{self.bridge_span_mm:.1f} мм)")
        parts.append(f"тонкие стенки {self.thin_mm2:.0f} мм²"
                     + (f" (крупнейший кусок {self.thin_piece_mm2:.1f} мм² на "
                        f"z={self.thin_at:.2f})" if self.thin_at is not None else ""))
        return ", ".join(parts)


def print_map(solid: Solid, *, limit_deg: float = 45.0, bed_eps: float = 0.2,
              wall: float = 1.6, thin_min_area: float = 1.0, step: float | None = None,
              layer: float = 0.2, exclude_overhang=(), exclude_thin=()) -> PrintMap:
    """Классы граней детали, стоящей на столе (+Z вверх).

    `thin_min_area` — тонкие куски сечения меньше этого не красятся: кончики
    зубьев и фаски теряют при размыкании доли мм² — норма (см. `min_wall`).
    `step` — шаг растра мостов (по умолчанию как у `export`: растёт с
    деталью). `layer` — шаг сечений тонкого: по слоям печати, но не больше
    150 сечений на деталь. `exclude_overhang`, `exclude_thin` — зоны детали
    (уже в позе), исключённые соответствующими проверками: там нарочно, и
    карта, как и проверка, их не красит, а называет.
    """
    from shapely import contains_xy
    from shapely.ops import unary_union

    from datum.checks.dfam import DEFAULT_STEP, _longest_bridge, bridge_cells, overhanging
    from datum.checks.printing import overhang_faces, thin_regions
    from datum.geom.convert import vertices_faces
    from datum.geom.voxel import voxelize

    verts, faces = vertices_faces(solid.manifold)
    if not len(faces):
        raise ValueError("пустое тело — карты нет")
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    cross = np.cross(b - a, c - a)
    area = np.linalg.norm(cross, axis=1) / 2
    normals = cross / np.maximum(2 * area, 1e-30)[:, None]
    centre = (a + b + c) / 3
    up = np.array([0.0, 0.0, 1.0])
    labels = np.zeros(len(faces), dtype=np.int8)
    notes: list[str] = []

    # Нависания — маска проверки `overhang`, без её зон.
    hang = overhang_faces(normals, centre, up, limit_deg, bed_eps)
    if exclude_overhang and hang.any():
        from datum.geom.measure import contains

        idx = np.nonzero(hang)[0]
        hit = np.zeros(len(idx), dtype=bool)
        for zone in exclude_overhang:
            hit |= contains(zone, centre[idx])
        hang[idx[hit]] = False
        notes.append(f"без зон {', '.join(z.name or '?' for z in exclude_overhang)}: "
                     f"в них нависаний {area[idx[hit]].sum():.1f} мм²")
    labels[hang] = 1

    # Мосты — ячейки, по которым `bridge_span` меряет пролёт.
    lo, hi = verts.min(0), verts.max(0)
    step = step or max(DEFAULT_STEP, float(np.max(hi - lo)) / 400)
    span = 0.0
    if limit_deg >= 45.0 and hang.any():
        grid = voxelize(solid, step)
        hanging = overhanging(grid, limit_deg)
        span, _, _ = _longest_bridge(grid, hanging)
        cells = bridge_cells(grid, hanging)
        if cells.any():
            from scipy import ndimage

            # На ячейку вокруг: грань лежит на границе ячейки, а не в ней.
            cells = ndimage.binary_dilation(cells, iterations=1)
            idx = np.nonzero(hang)[0]
            p = centre[idx] + up * (grid.step / 2)        # точка внутри материала
            o = np.asarray(grid.origin)
            ix = np.floor((p[:, 0] - o[0]) / grid.step).astype(int)
            iy = np.floor((p[:, 1] - o[1]) / grid.step).astype(int)
            iz = np.floor((p[:, 2] - o[2]) / grid.step).astype(int)
            nz, nx, ny = cells.shape
            inside = (iz >= 0) & (iz < nz) & (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
            hit = np.zeros(len(idx), dtype=bool)
            hit[inside] = cells[iz[inside], ix[inside], iy[inside]]
            labels[idx[hit]] = 2
    elif limit_deg < 45.0:
        notes.append("мосты на растре строже 45° не различаются — не показаны")

    # Тонкие стенки — то, что теряет размыкание `min_wall`, в слоях печати.
    height = float(hi[2] - lo[2])
    spacing = max(layer, height / 150)
    heights = np.arange(lo[2] + spacing / 2, hi[2], spacing)
    side = np.abs(normals[:, 2]) < 0.7
    thin_piece, thin_at = 0.0, None
    if exclude_thin:
        notes.append(f"тонкое в зонах {', '.join(zn.name or '?' for zn in exclude_thin)} "
                     f"не показано")
    for z, _, lost in thin_regions(solid, wall, "xy", heights):
        if exclude_thin and not lost.is_empty:
            from datum.geom.measure import section_xy

            cut = unary_union([section_xy(zone, z) for zone in exclude_thin])
            if not cut.is_empty:
                lost = lost.difference(cut)
        pieces = [g for g in getattr(lost, "geoms", [lost]) if g.area >= thin_min_area]
        if not pieces:
            continue
        biggest = max(g.area for g in pieces)
        if biggest > thin_piece:
            thin_piece, thin_at = biggest, z
        band = side & (np.abs(centre[:, 2] - z) <= spacing / 2) & (labels == 0)
        if not band.any():
            continue
        region = unary_union(pieces).buffer(0.05)
        idx = np.nonzero(band)[0]
        labels[idx[contains_xy(region, centre[idx, 0], centre[idx, 1])]] = 3

    return PrintMap(
        verts=verts, faces=faces, labels=labels,
        overhang_mm2=float(area[labels == 1].sum() + area[labels == 2].sum()),
        bridge_mm2=float(area[labels == 2].sum()), bridge_span_mm=span,
        thin_mm2=float(area[labels == 3].sum()), thin_piece_mm2=thin_piece,
        thin_at=thin_at, notes=tuple(notes))


def colors(pm: PrintMap, base: str) -> np.ndarray:
    """Цвет каждой грани: класс — своим цветом, обычная — цветом детали."""
    def rgb(h: str) -> list[int]:
        h = h.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]

    table = np.array([rgb(base)] + [rgb(CLASSES[k][1]) for k in sorted(CLASSES)], float)
    return table[pm.labels]
