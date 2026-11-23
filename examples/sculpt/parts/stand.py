"""Подставка под шар — скульптура полем.

Порядок — тот, что проверен печатью (см. `datum.geom.field`):
  1. форма: лофт сечений от основания к шейке и чаше — гладкий сам;
  2. `limit_overhang` по позе печати — до узора, иначе он зальёт ячейки;
  3. `redistance` — после сопряжений поле уже не расстояние;
  4. узор с затуханием у кромок и на нависании;
  5. гнездо — вычитанием, последним: его стенка гладкая.
"""

import math

from shapely.geometry import Point

from datum.build import P, draft, part
from datum.geom import field as F


def _rounded_square(w: float, r: float):
    """Квадрат со стороной w и скруглёнными углами радиуса r, центр в нуле."""
    return Point(0, 0).buffer(w / 2 - r, cap_style=3).buffer(r, quad_segs=24)


@part("подставка", print_up="+Z", color="#c9b48a")
def build():
    h = 1.2 if draft() else P.FIELD_H
    ball_r = P.BALL_D / 2
    top = P.HEIGHT

    # Гнездо: шар утоплен на долю диаметра, центр над кромкой. Чаша — от
    # отверстия гнезда плюс стенка: уже — и шар срезал бы кромку в лезвие.
    seat_r = ball_r + P.SEAT_GAP
    centre_z = top + ball_r - P.SEAT_DEPTH * P.BALL_D
    opening = math.sqrt(max(seat_r ** 2 - (centre_z - top) ** 2, 0.0))
    cup_r = opening + P.RIM_WALL
    half = max(P.BASE_W / 2, cup_r) + 1.0
    g = F.Grid.around((-half, -half, 0), (half, half, top), h)

    # 1. Силуэт: основание → шейка → чаша. Сечения — круги и скруглённый квадрат.
    neck_z = top * 0.45
    body = F.loft(g, [
        (_rounded_square(P.BASE_W, 10.0), 0.0),
        (Point(0, 0).buffer(P.BASE_W * 0.36, quad_segs=48), 4.0),
        (Point(0, 0).buffer(P.NECK_D / 2, quad_segs=48), neck_z),
        (Point(0, 0).buffer(cup_r, quad_segs=48), top),
    ])

    # 2–3. Печатность по построению и снова расстояние.
    body = F.limit_overhang(body, 45, up="+Z")
    body = F.redistance(body)

    # 4. Узор: гаснет у основания и у кромки чаши, на нависании — сам.
    X, Y, Z = g.mesh()
    fade_bottom = F.Field(g, (0 * X + 0 * Y + (Z - 6.0) / 4.0).clip(0, 1).astype("float32"))
    fade_top = F.Field(g, (0 * X + 0 * Y + (top - 7.0 - Z) / 4.0).clip(0, 1).astype("float32"))
    pattern = F.diagrid(P.PATTERN_PERIOD, angle=60, rib=0.22, axis_xy=(0.0, 0.0, P.NECK_D / 2 + 4))
    body = F.surface_pattern(body, pattern, P.PATTERN_DEPTH, fade=(fade_bottom, fade_top))

    # 5. Гнездо под шар с зазором.
    body = F.diff(body, F.sphere(g, 2 * seat_r, 0, 0, centre_z))

    return F.mesh(body, name="подставка", simplify=0.01)
