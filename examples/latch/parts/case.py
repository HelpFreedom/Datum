"""Корпус: коробка с карманом и удерживающей губкой по краю проёма."""

from datum.build import P, part
from datum.geom import box, diff, hull, union


@part("корпус", color="#8a93a5", print_up="+Z")
def build():
    """Коробка со стенками WALL, карманом и губкой глубиной LIP_D."""
    lip_z = P.CASE_H - P.LIP_H
    outer = box(0, P.CASE_W, 0, P.CASE_D, 0, P.CASE_H)
    pocket = box(P.WALL, P.CASE_W - P.WALL,
                 P.WALL, P.CASE_D - P.WALL,
                 P.FLOOR, lip_z)
    # Проём сверху уже кармана на LIP_D с каждой стороны — это и есть губка.
    mouth = box(P.WALL + P.LIP_D, P.CASE_W - P.WALL - P.LIP_D,
                P.WALL + P.LIP_D, P.CASE_D - P.WALL - P.LIP_D,
                lip_z, P.CASE_H)
    # ЗАХОДНАЯ ФАСКА. Без неё губка — прямая ступенька, а жёсткое тело прямую
    # ступеньку не проходит: усилие вставки не определено, сколько ни дави.
    # Объёмная проверка продавливания этого не видела вовсе — она мерит объём,
    # а не путь, по которому крышка съезжает.
    lead_z = P.CASE_H - P.LEAD_IN
    # Выпуклая оболочка двух тонких рамок на разной высоте и есть усечённая
    # пирамида — скос от кромки проёма до стенки кармана.
    lead = hull(union([
        box(P.WALL + P.LIP_D, P.CASE_W - P.WALL - P.LIP_D,
            P.WALL + P.LIP_D, P.CASE_D - P.WALL - P.LIP_D,
            lead_z, lead_z + 0.01),
        box(P.WALL, P.CASE_W - P.WALL,
            P.WALL, P.CASE_D - P.WALL,
            P.CASE_H - 0.01, P.CASE_H),
    ]))
    return diff(outer, [pocket, mouth, lead])
