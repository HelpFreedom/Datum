"""Проверка, ради которой этот проект стоило портировать целиком.

В NOTES оригинала записана ошибка, найденная уже после сборки:

    «Скругления люлька/перегородка убраны (CRADLE_FILLET = 0): с ними линия
     дна отсека была 24 мм вместо 26, катушка садилась бы на скругления.»

То есть отсек, объявленный двадцатишестимиллиметровым, на дне сужался до
двадцати четырёх — и катушка вставала не на дно, а на скругления. Поймано это
было глазами и линейкой, задним числом.

Здесь это проверка: просвет отсека меряется НА НЕСКОЛЬКИХ ВЫСОТАХ, от самого
дна люльки до верха, и всюду обязан быть не меньше объявленного. Сужение
на миллиметр у каждой перегородки она видит как 24.000 — проверено тестом.
"""

from datum.doc.project import check
from datum.checks.result import CheckResult
from datum.geom.measure import section_xy


def _gaps_at(solid, z, half_length, width=1.0):
    """Просветы вдоль Y на высоте z, в узкой полосе у оси X.

    Полоса нужна, чтобы мерить ДНО отсека, а не просвет у стенок: у люльки
    сечение дугой, и ближе к бортам просвет всегда шире.
    """
    from shapely.geometry import box as strip

    section = section_xy(solid, z)
    if section.is_empty:
        return []
    clipped = section.intersection(
        strip(-width, -half_length - 1, width, half_length + 1))
    if clipped.is_empty:
        return []
    pieces = sorted(getattr(clipped, "geoms", [clipped]), key=lambda g: g.bounds[1])
    return [round(b.bounds[1] - a.bounds[3], 4)
            for a, b in zip(pieces, pieces[1:])]


@check("подставка.отсек_не_сужается", title="Отсек не сужается ко дну",
       group="fit")
def slot_width(P, parts):
    solid = parts("подставка")
    half_length = P.LENGTH / 2
    bottom = P.CRADLE_CZ - P.CRADLE_R          # самая низкая точка люльки
    want = P.SLOT_L
    tolerance = 0.05

    worst = None
    for step in range(9):
        z = bottom + 0.25 + step * (P.HEIGHT - bottom - 1.0) / 8
        for gap in _gaps_at(solid, z, half_length):
            if worst is None or gap < worst[0]:
                worst = (gap, z)

    if worst is None:
        return CheckResult(
            id="подставка.отсек_не_сужается", title="Отсек не сужается ко дну",
            group="fit", status="error",
            message="просветов отсеков не нашлось — деталь не та, что ожидалась",
            inputs=("подставка",))

    narrowest, at_z = worst
    ok = narrowest >= want - tolerance
    return CheckResult(
        id="подставка.отсек_не_сужается", title="Отсек не сужается ко дну",
        group="fit", status="pass" if ok else "fail",
        message=(f"самый узкий просвет отсека {narrowest:.3f} мм на высоте "
                 f"{at_z:.2f} {'≥' if ok else '<'} объявленных {want:g}"
                 + ("" if ok else ". Катушка сядет не на дно, а на сужение —"
                                  " ровно то, на чём споткнулся оригинал")),
        value=narrowest, unit="мм", op=">=", threshold=want - tolerance,
        inputs=("подставка",), severity="blocking")
