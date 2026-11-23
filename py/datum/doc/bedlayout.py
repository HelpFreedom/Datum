"""Расстановка новых деталей на столе — когда модель приходит несколькими объектами.

«ОРГАНИЧНО» здесь значит по порядку:

1. КАК В ФАЙЛЕ, если это возможно. Объекты, расставленные в слайсере, стоят
   относительно друг друга так, как их поставил автор: группа только
   переезжает на стол. Возможно — это когда в плане они не перекрываются и
   вместе влезают на стол. Объекты, сложенные в одну точку (экспорт сборки,
   STL-набор от разных деталей), так не годятся.
2. Иначе — РЯДАМИ с зазором, крупные первыми. Ширина ряда — около стороны
   квадрата той же площади, а не весь стол: группа выходит компактной, а не
   полосой через стол.
3. Группа — в ЦЕНТР стола, а если там уже стоят детали — в ближайшее свободное
   место: поиск по расширяющимся кольцам с шагом зазора. Не нашлось — в
   центр, и ответ честно говорит «не поместилось».

Только план (XY): детали стоят на столе, по высоте их никто не складывает.
Прямоугольник — (x0, y0, x1, y1), мм; координаты стола — от его угла.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["Arrangement", "arrange", "GAP"]

Rect = tuple[float, float, float, float]

#: Зазор между деталями на столе, мм: сопло проходит между ними, не цепляя
#: соседнюю, и человек видит, где кончается одна и начинается другая.
GAP = 5.0


@dataclass(frozen=True)
class Arrangement:
    """Центры габаритов в плане, в координатах стола, — по порядку объектов."""

    centres: list[tuple[float, float]]
    kept_file_layout: bool
    fits: bool
    note: str


def _overlap(a: Rect, b: Rect, pad: float = 0.0) -> bool:
    return (a[0] < b[2] + pad and b[0] < a[2] + pad
            and a[1] < b[3] + pad and b[1] < a[3] + pad)


def _rows(sizes: list[tuple[float, float]], width: float, gap: float) -> list[tuple[float, float]]:
    """Рядами: левые нижние углы, крупные (по глубине) первыми."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    corners: list[tuple[float, float]] = [(0.0, 0.0)] * len(sizes)
    x = y = row = 0.0
    for i in order:
        w, d = sizes[i]
        if x > 0 and x + w > width:
            x, y, row = 0.0, y + row + gap, 0.0
        corners[i] = (x, y)
        x += w + gap
        row = max(row, d)
    return corners


def arrange(footprints: Sequence[Rect], bed: tuple[float, float],
            occupied: Sequence[Rect] = (), gap: float = GAP) -> Arrangement:
    """Куда поставить объекты: центры их габаритов на столе.

    footprints — габариты объектов в плане в координатах ФАЙЛА (по ним видно,
    как автор расставил их относительно друг друга); occupied — габариты уже
    стоящих на столе деталей.
    """
    if not footprints:
        return Arrangement([], False, True, "")
    bx, by = float(bed[0]), float(bed[1])
    sizes = [(r[2] - r[0], r[3] - r[1]) for r in footprints]

    # 1. Как в файле — если в плане не перекрываются и влезают на стол.
    rects = list(footprints)
    lo_x, lo_y = min(r[0] for r in rects), min(r[1] for r in rects)
    hi_x, hi_y = max(r[2] for r in rects), max(r[3] for r in rects)
    apart = all(not _overlap(rects[i], rects[j], -1e-6)
                for i in range(len(rects)) for j in range(i + 1, len(rects)))
    kept = apart and hi_x - lo_x <= bx and hi_y - lo_y <= by
    if kept:
        group = (hi_x - lo_x, hi_y - lo_y)
        rel = [((r[0] + r[2]) / 2 - (lo_x + hi_x) / 2, (r[1] + r[3]) / 2 - (lo_y + hi_y) / 2)
               for r in rects]
    else:
        # 2. Рядами: ширина ряда — около стороны квадрата той же площади.
        area = sum((w + gap) * (d + gap) for w, d in sizes)
        width = min(bx, max(max(w for w, _ in sizes), math.sqrt(area) * 1.25))
        corners = _rows(sizes, width, gap)
        gx = max(c[0] + s[0] for c, s in zip(corners, sizes))
        gy = max(c[1] + s[1] for c, s in zip(corners, sizes))
        group = (gx, gy)
        rel = [(c[0] + s[0] / 2 - gx / 2, c[1] + s[1] / 2 - gy / 2)
               for c, s in zip(corners, sizes)]

    # 3. Группа — в центр стола или в ближайшее свободное место.
    half = (group[0] / 2, group[1] / 2)

    def free(cx: float, cy: float) -> bool:
        if cx - half[0] < 0 or cy - half[1] < 0 or cx + half[0] > bx or cy + half[1] > by:
            return False
        box = (cx - half[0], cy - half[1], cx + half[0], cy + half[1])
        return not any(_overlap(box, o, gap) for o in occupied)

    centre = (bx / 2, by / 2)
    spot = centre if free(*centre) else None
    radius = gap
    while spot is None and radius <= math.hypot(bx, by) / 2:
        steps = max(8, math.ceil(2 * math.pi * radius / gap))
        for k in range(steps):
            angle = 2 * math.pi * k / steps
            candidate = (centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle))
            if free(*candidate):
                spot = candidate
                break
        radius += gap
    fits = spot is not None
    spot = spot or centre

    notes = []
    if kept and len(footprints) > 1:
        notes.append("расстановка из файла")
    elif len(footprints) > 1:
        notes.append(f"рядами с зазором {gap:g} мм")
    if spot != centre:
        notes.append("центр стола занят — рядом")
    if not fits:
        notes.append(f"НЕ ПОМЕСТИЛОСЬ: группа {group[0]:.0f}×{group[1]:.0f} мм на столе "
                     f"{bx:.0f}×{by:.0f} мм" + (" рядом с другими деталями" if occupied else ""))
    return Arrangement(centres=[(spot[0] + dx, spot[1] + dy) for dx, dy in rel],
                       kept_file_layout=kept, fits=fits, note="; ".join(notes))
