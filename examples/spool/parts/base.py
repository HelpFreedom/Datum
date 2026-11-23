"""Подставка под кабельные катушки: корпус с люльками и рифлением.

ПОЧЕМУ ЭТА ДЕТАЛЬ ЗДЕСЬ. В прежнем рабочем процессе она собиралась ДВУМЯ
конвейерами, и это записано в NOTES оригинала: «мешер OCCT на 168 канавках ×
фаска/скругления даёт трещины (164 открытых ребра; мельче допуск → 4500).
Поэтому корпус без канавок экспортируется в STL, а канавки вырезаются на меше
через manifold3d». То есть форма для просмотра и форма для печати строились
РАЗНЫМИ путями и совпадали только на честном слове.

Здесь путь один. Ядро многообразно по построению, и 168 канавок вычитаются
тем же булевым, что и всё остальное.

ТРИ МЕСТА, ГДЕ ПРИШЛОСЬ ПОДУМАТЬ.

Сопряжение R12 между плоским верхом и уклоном строится ДУГОЙ в профиле, а не
операцией скругления: в плоском профиле это точная дуга, а не аппроксимация
поверхности. Скругления рёбер как операции у меша нет вовсе, и это честнее —
в B-rep она есть и именно на ней OCCT спотыкался.

Фаска 45° по низу — ВЫПУКЛАЯ ОБОЛОЧКА двух контуров: плана и его смещения
внутрь на глубину фаски. Это даёт ТОЧНЫЙ скос, потому что смещение
скруглённого прямоугольника внутрь меняет и стороны, и радиус линейно, а
выпуклая оболочка двух выпуклых сечений интерполирует их линейно. Лестницей из
слоёв вышло бы ступеньками.

Рифление раскладывается по периметру ПЛАНА, а не по прямым сторонам: на
скруглённых углах шаг обязан сохраняться, иначе на стыке прямой и дуги
получится сбой рисунка. Число канавок берётся кратным четырём — ради симметрии.
"""

import math

import numpy as np

from datum.build import P, part
from datum.geom import (diff, hull, intersect, prism_xy, prism_xz, rrect_poly,
                        union, cyl_y)


def _fillet_arc(a, b, c, radius, segments=28):
    """Дуга сопряжения радиуса `radius` в вершине b между отрезками a-b и b-c.

    Считается точно: центр лежит на биссектрисе на расстоянии r/sin(θ/2), точки
    касания — на сторонах на расстоянии r/tan(θ/2) от вершины.
    """
    b = np.asarray(b, dtype=float)
    u = np.asarray(a, dtype=float) - b
    v = np.asarray(c, dtype=float) - b
    u /= np.linalg.norm(u)
    v /= np.linalg.norm(v)
    half = math.acos(max(-1.0, min(1.0, float(u @ v)))) / 2
    bisector = u + v
    bisector /= np.linalg.norm(bisector)
    centre = b + bisector * (radius / math.sin(half))
    start = b + u * (radius / math.tan(half))
    end = b + v * (radius / math.tan(half))
    a0 = math.atan2(*(start - centre)[::-1])
    a1 = math.atan2(*(end - centre)[::-1])
    sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
    return [tuple(centre + radius * np.array([math.cos(a0 + sweep * i / segments),
                                              math.sin(a0 + sweep * i / segments)]))
            for i in range(segments + 1)]


def _groove_cutters(plan, pitch_target, top_w, bottom_w, depth, height):
    """Трапециевидные резцы рифления по всему периметру плана.

    Боковые грани продлены НАРУЖУ на `out` с тем же наклоном: иначе резец не
    дотянулся бы до поверхности и канавка вышла бы мельче заявленной.
    """
    ring = plan.exterior
    perimeter = ring.length
    count = int(round(perimeter / pitch_target / 4)) * 4
    pitch = perimeter / count
    half_top, half_bottom, out = top_w / 2, bottom_w / 2, 0.6
    slope = (half_top - half_bottom) / depth
    if 2 * (half_top + slope * out) >= pitch:
        raise ValueError(
            f"резцы рифления перекрываются: ширина {2 * (half_top + slope * out):.2f} "
            f"при шаге {pitch:.2f} мм. Возьмите шаг крупнее или канавку уже")

    cutters = []
    for i in range(count):
        at = (i + 0.5) / count * perimeter
        point = np.asarray(ring.interpolate(at).coords[0])
        ahead = np.asarray(ring.interpolate((at + 0.05) % perimeter).coords[0])
        tangent = ahead - point
        tangent /= np.linalg.norm(tangent)
        normal = np.array([tangent[1], -tangent[0]])
        if normal @ point < 0:                      # наружу от центра
            normal = -normal

        def at_point(along, across):
            return tuple(point + along * tangent + across * normal)

        cutters.append(prism_xy(
            [at_point(-(half_top + slope * out), out),
             at_point(half_top + slope * out, out),
             at_point(half_bottom, -depth),
             at_point(-half_bottom, -depth)],
            -1.0, height + 2))
    return cutters, count, pitch


@part("подставка", color="#7a8b99", print_up="+Z")
def build():
    half_w, half_l = P.WIDTH / 2, P.LENGTH / 2

    # Клиновой профиль в XZ: плоский верх, дуга сопряжения, уклон к передней
    # кромке. Вытягивается вдоль Y с запасом — план обрежет его по длине.
    arc = _fillet_arc((-half_w, P.HEIGHT), (P.TOP_FLAT_X, P.HEIGHT),
                      (half_w, P.FRONT_H), P.TOP_R)
    profile = [(-half_w, 0.0), (half_w, 0.0), (half_w, P.FRONT_H),
               *reversed(arc), (-half_w, P.HEIGHT)]
    wedge = prism_xz(profile, -half_l - 1, half_l + 1)

    # План со скруглёнными углами и фаской 45° по низу.
    chamfer = P.BOTTOM_CHAMFER
    plan = rrect_poly(-half_w, -half_l, half_w, half_l, P.CORNER_R)
    inset = rrect_poly(-half_w + chamfer, -half_l + chamfer,
                       half_w - chamfer, half_l - chamfer, P.CORNER_R - chamfer)
    skirt = hull(union([prism_xy(inset, 0.0, 0.02),
                        prism_xy(plan, chamfer - 0.02, chamfer)]))
    column = prism_xy(plan, chamfer, P.HEIGHT + 1)
    body = intersect([wedge, union([skirt, column])])

    # Люльки отсеков.
    slots = int(P.N_SLOTS)
    first = -half_l + P.END_T + P.SLOT_L / 2
    body = diff(body, [
        cyl_y(2 * P.CRADLE_R,
              first + i * (P.SLOT_L + P.DIV_T) - P.SLOT_L / 2,
              first + i * (P.SLOT_L + P.DIV_T) + P.SLOT_L / 2,
              x=0.0, z=P.CRADLE_CZ, n=256)
        for i in range(slots)])

    cutters, _, _ = _groove_cutters(plan, P.GROOVE_PITCH, P.GROOVE_TOP_W,
                                    P.GROOVE_BOT_W, P.GROOVE_DEPTH, P.HEIGHT)
    return diff(body, cutters)
