"""Деталь — в центр стола, а не в угол.

Тело удобно строить от нуля: `box(0, W, 0, D, 0, H)`. Но ноль координат — это
УГОЛ стола, и деталь, построенная так и оставленная, печаталась бы у края
(владелец заметил это у агента, проектирующего с нуля). Поэтому последняя
строка скрипта ставит деталь на стол:

    from datum.build import centre_on_bed, part

    @part("крышка", print_up="+Z")
    def build():
        body = box(0, P.W, 0, P.D, 0, P.H) - ...
        return centre_on_bed(body)

Отдельные детали ставятся вокруг центра сдвигом `dx`, `dy` — так они не
налезают друг на друга. Детали УЗЛА, стоящие друг на друге (крышка в корпусе),
строят в общих координатах вокруг нуля — `box(-W / 2, W / 2, -D / 2, D / 2, …)` —
и сдвигают на одно и то же: `cx, cy = bed_centre()`, `move(тело, dx=cx, dy=cy)`.
Поставить каждую `centre_on_bed` по её собственному габариту значило бы
разъединить узел.

`part_build` предупреждает, если детали сдвинуты к краю стола или выходят за
него (`bed_warnings`).
"""

from __future__ import annotations

import math

__all__ = ["centre_on_bed", "bed_centre", "bed_warnings"]


def bed_centre() -> tuple[float, float]:
    """Центр стола в плане, мм — общий сдвиг для деталей узла, построенного вокруг нуля."""
    from datum.build.context import bed

    bx, by, _ = bed()
    return bx / 2, by / 2


def centre_on_bed(solid, dx: float = 0.0, dy: float = 0.0, *, drop: bool = True):
    """Поставить тело в центр стола (+ сдвиг dx, dy) и опустить на стол.

    drop — низ на стол (z = 0); False — по высоте не трогать.
    """
    from datum.geom.measure import bbox
    from datum.geom.xform import move

    (x0, y0, z0), (x1, y1, _) = bbox(solid)
    cx, cy = bed_centre()
    return move(solid, dx=cx + dx - (x0 + x1) / 2, dy=cy + dy - (y0 + y1) / 2,
                dz=-z0 if drop else 0.0)


def bed_warnings(boxes: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]],
                 bed: tuple[float, float, float], *, whole: bool) -> list[str]:
    """Что сказать о месте деталей на столе: за краем и (по всем деталям) у края.

    boxes — габариты деталей, как стоят; whole — собраны ли ВСЕ детали проекта
    (тогда судится и группа целиком: её центр дальше четверти меньшей стороны
    стола от центра — «у края»).
    """
    bx, by = float(bed[0]), float(bed[1])
    out = []
    for name, ((x0, y0, _), (x1, y1, _)) in boxes.items():
        if x0 < -0.01 or y0 < -0.01 or x1 > bx + 0.01 or y1 > by + 0.01:
            out.append(f"{name}: выходит за стол ({x0:.0f}…{x1:.0f} × {y0:.0f}…{y1:.0f} мм "
                       f"при столе {bx:.0f}×{by:.0f})")
    if whole and boxes:
        lo_x = min(b[0][0] for b in boxes.values())
        lo_y = min(b[0][1] for b in boxes.values())
        hi_x = max(b[1][0] for b in boxes.values())
        hi_y = max(b[1][1] for b in boxes.values())
        cx, cy = (lo_x + hi_x) / 2, (lo_y + hi_y) / 2
        if math.hypot(cx - bx / 2, cy - by / 2) > min(bx, by) / 4:
            what = "деталь" if len(boxes) == 1 else "детали"
            out.append(f"{what} у края стола: центр ({cx:.0f}, {cy:.0f}) мм, а центр стола "
                       f"({bx / 2:.0f}, {by / 2:.0f}) — поставьте в центр: "
                       f"return centre_on_bed(тело) в скрипте детали")
    return out
