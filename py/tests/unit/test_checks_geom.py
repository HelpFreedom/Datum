"""Проверки топологии, посадок и печатности — против аналитики.

Каждый порог и каждая величина сверяются с числом, посчитанным руками, а не
«лишь бы не упало».
"""

from __future__ import annotations

import math

import pytest

from datum.checks.fit import clearance, no_intersection, obstruction_thickness, tunnel_wall
from datum.checks.printing import bbox_fits, min_wall, overhang, under_part_violation
from datum.checks.result import CheckRun
from datum.checks.topology import no_sealed_void, no_slivers, single_body, topology_checks
from datum.geom import box, cyl_z, diff, extrude, move, rrect_poly, union


# ------------------------------------------------------------------ топология
def test_single_body_and_voids():
    solid = box(0, 10, 0, 10, 0, 10).renamed("деталь")
    assert single_body(solid).status == "pass"
    assert no_sealed_void(solid).status == "pass"

    split = union([box(0, 10, 0, 10, 0, 10), box(30, 40, 0, 10, 0, 10)]).renamed("деталь")
    r = single_body(split)
    assert r.status == "fail" and "РАСПАЛАСЬ" in r.message and r.value == 2

    hollow = (box(0, 10, 0, 10, 0, 10) - box(3, 7, 3, 7, 3, 7).manifold).renamed("деталь")
    v = no_sealed_void(hollow)
    assert v.status == "fail" and "нечем заполнить" in v.message


def test_slivers_are_reported():
    s = union([box(0, 10, 0, 10, 0, 10), box(40, 40.02, 0, 0.02, 0, 0.02)]).renamed("деталь")
    assert no_slivers(s).status == "fail"


def test_topology_checks_bundle():
    res = topology_checks(box(0, 10, 0, 10, 0, 10).renamed("к"))
    assert {r.id for r in res} == {"к.single_body", "к.no_sealed_void", "к.no_slivers"}
    assert CheckRun(tuple(res)).ok


# ------------------------------------------------------------------ посадки
def test_clearance_measures_the_real_gap():
    a = box(0, 10, 0, 10, 0, 10).renamed("a")
    b = box(12.5, 20, 0, 10, 0, 10).renamed("b")
    r = clearance(a, b, min_mm=2.0, search=6.0)
    assert r.status == "pass" and r.value == pytest.approx(2.5, abs=1e-6)
    assert clearance(a, b, min_mm=3.0, search=6.0).status == "fail"


def test_no_intersection():
    a = box(0, 10, 0, 10, 0, 10).renamed("a")
    assert no_intersection(a, box(20, 30, 0, 10, 0, 10).renamed("b")).status == "pass"
    r = no_intersection(a, box(8, 18, 0, 10, 0, 10).renamed("b"))
    assert r.status == "fail" and r.value == pytest.approx(200.0)


def test_obstruction_thickness_measures_travel_and_lip():
    """Губка толщиной 1.5 мм над деталью, свободный ход 0.4 мм.

    Деталь: z 0…5, x 0…20. Корпус: губка при z 5.4…6.9 над x 0…20.
    Двигаем деталь вверх: до 0.4 мм свободно, затем 1.5 мм препятствия.
    """
    part = box(0, 20, 0, 10, 0, 5).renamed("деталь")
    holder = box(0, 20, 0, 10, 5.4, 6.9).renamed("корпус")
    r = obstruction_thickness(part, holder, plane="xz", at=5.0, band=(0, 20),
                              direction=1, min_thickness=1.2)
    d = r.evidence[0].data
    assert d["free_travel_mm"] == pytest.approx(0.4, abs=0.03), d
    assert d["part_extent_mm"] == pytest.approx(5.0, abs=0.01), d
    # Путь в контакте = губка + высота детали; именно это считал оригинал.
    assert d["contact_span_mm"] == pytest.approx(6.5, abs=0.05), d
    # А толщина губки — разность, и порог применяется к ней.
    assert d["thickness_mm"] == pytest.approx(1.5, abs=0.05), d
    assert r.status == "pass"


def test_obstruction_thickness_flags_a_thin_lip():
    part = box(0, 20, 0, 10, 0, 5).renamed("деталь")
    holder = box(0, 20, 0, 10, 5.4, 5.8).renamed("корпус")      # губка всего 0.4 мм
    r = obstruction_thickness(part, holder, plane="xz", at=5.0, band=(0, 20),
                              direction=1, min_thickness=1.2)
    assert r.status == "fail"
    assert r.value == pytest.approx(0.4, abs=0.05), r.message


def test_obstruction_thickness_reports_absence_of_any_lip():
    part = box(0, 20, 0, 10, 0, 5).renamed("деталь")
    holder = box(40, 50, 0, 10, 0, 10).renamed("корпус")        # в стороне
    r = obstruction_thickness(part, holder, plane="xz", at=5.0, band=(0, 20),
                              direction=1, min_thickness=1.2, max_travel=6.0)
    assert r.status == "fail" and "препятствия нет вовсе" in r.message


def test_tunnel_wall_pass_and_fail():
    """Туннель Ø6 вдоль Z в бруске 20×20×30: стенка от края туннеля до края 7 мм."""
    part = (box(-10, 10, -10, 10, 0, 30) - cyl_z(6.0, -1, 31, 0, 0, 128).manifold)
    part = part.renamed("деталь")
    # просим стенку 1 мм — есть с запасом
    assert tunnel_wall(part, axis="z", centre=(0, 0), d_tunnel=6.0,
                       span=(1, 29), wall=1.0).status == "pass"
    # просим 8 мм — кольцо вылезет за брусок
    r = tunnel_wall(part, axis="z", centre=(0, 0), d_tunnel=6.0, span=(1, 29), wall=8.0)
    assert r.status == "fail" and r.value > 0


# ------------------------------------------------------------------ печатность
def test_under_part_violation_catches_material_below():
    moving = box(5, 15, 5, 15, 5, 8).renamed("шторка")
    clean = box(0, 20, 0, 20, 0, 2).renamed("корпус")           # под деталью пусто? нет — есть плита
    # Плита 0..2 целиком под деталью и внутри её контура → нарушение.
    bad = under_part_violation(clean, moving, erode=0.4, max_vol=0.5)
    assert bad.status == "fail" and bad.value > 50

    # А если корпус только по периметру, под деталью пусто.
    ring = diff(box(0, 20, 0, 20, 0, 2), [box(4, 16, 4, 16, -1, 3)]).renamed("корпус")
    good = under_part_violation(ring, moving, erode=0.4, max_vol=0.5)
    assert good.status == "pass", good.message


def test_under_part_violation_respects_exclusions():
    moving = box(5, 15, 5, 15, 5, 8).renamed("шторка")
    axle = box(9, 11, 4, 16, 0, 2).renamed("ось")
    case = union([diff(box(0, 20, 0, 20, 0, 2), [box(4, 16, 4, 16, -1, 3)]), axle]).renamed("корпус")
    assert under_part_violation(case, moving, erode=0.4, max_vol=0.5).status == "fail"
    assert under_part_violation(case, moving, erode=0.4, max_vol=0.5,
                                exclude=[axle]).status == "pass"


def test_min_wall_finds_a_thin_rib():
    thick = box(0, 20, 0, 20, 0, 5).renamed("плита")
    assert min_wall(thick, wall=1.6).status == "pass"

    # Оболочка со стенкой 1 мм: при требовании 1.6 мм сечение размыкается целиком.
    shell = (box(0, 30, 0, 30, 0, 10) - box(1, 29, 1, 29, -1, 11).manifold).renamed("оболочка")
    r = min_wall(shell, wall=1.6)
    assert r.status == "fail", r.message
    assert r.value > 5.0, f"крупное тонкое место должно быть замечено: {r.value} мм²"
    # Та же оболочка при требовании 0.8 мм проходит.
    assert min_wall(shell, wall=0.8).status == "pass"



def test_min_wall_cuts_the_part_in_its_print_layers():
    """Тонкое — в плоскостях СЛОЁВ печати, как на карте печатности. Лист 0.8 мм,
    построенный стоймя, но печатаемый плашмя (`print_up="+X"`), в слое — целый
    лист 30×30, а не полоска 0.8: прежде проверка резала как построено и
    браковала то, что печатается без единой тонкой стенки."""
    sheet = box(0, 0.8, 0, 30, 0, 30).renamed("лист")
    assert min_wall(sheet, wall=1.6, samples=6).status == "fail"            # стоймя
    flat = min_wall(sheet, wall=1.6, samples=6, up="+X")
    assert flat.status == "pass", flat.message
    # И наоборот: плита, печатаемая на ребре, тонка в слоях, хотя построена плашмя.
    plate = box(0, 30, 0, 30, 0, 0.8).renamed("плита")
    assert min_wall(plate, wall=1.6, samples=6).status == "pass"
    edge = min_wall(plate, wall=1.6, samples=6, up="+Y")
    assert edge.status == "fail" and "слои печати, вверх +Y" in edge.message


@pytest.mark.parametrize("wall", [0.4, 1.2, 1.6, 2.0, 3.0])
def test_min_wall_accepts_a_wall_exactly_as_thick_as_required(wall):
    """Равенство — не нарушение. Раньше исход решало округление: полоса 1.6 при
    требовании 1.6 проходила, а 2.0 при 2.0 давала «40 мм² тонкого»."""
    exact = box(0, wall, 0, 20, 0, 5).renamed("полоса")
    assert min_wall(exact, wall=wall, samples=5).status == "pass"
    thinner = box(0, wall - 0.05, 0, 20, 0, 5).renamed("полоса")
    r = min_wall(thinner, wall=wall, samples=5)
    assert r.status == "fail", r.message
    # Место — по нормали к сечению: срез XY стоит на высоте z, а не «xy=».
    assert "в сечении XY на z=" in r.message

def test_min_wall_ignores_thin_edges_but_catches_a_wall():
    """Различение калибровано на реальных деталях phone-case-gears v4.

    Там кончики зубьев шестерни давали куски потери по 0.51 мм², фаски шторки —
    по 0.14, а настоящее тонкое место корпуса — 130 мм². По доле площади все
    выглядели одинаково подозрительно, по крупнейшему куску — различаются.
    """
    from datum.geom import array_polar, cyl_z

    # Диск Ø30 с 24 тонкими зубцами по ободу: каждый зубец тоньше 1.6 мм,
    # но это законная мелкая деталь, а не тонкая стенка.
    disc = cyl_z(30.0, 0, 4, 0, 0, 128)
    tooth = box(14.0, 17.0, -0.45, 0.45, 0, 4)
    toothed = union([disc, array_polar(tooth, 24, axis="z", c=(0, 0, 0))]).renamed("шестерня")
    r = min_wall(toothed, wall=1.6, samples=8)
    assert r.status == "pass", r.message
    assert r.value < 5.0, f"зубцы не должны считаться тонкой стенкой: {r.value:.2f} мм²"
    assert r.evidence[0].data["pieces"] >= 12, "но сами зубцы должны быть замечены"

    # А настоящая тонкая стенка на том же пороге — ловится.
    thin_shell = (box(0, 40, 0, 40, 0, 6) - box(1, 39, 1, 39, -1, 7).manifold).renamed("коробка")
    r2 = min_wall(thin_shell, wall=1.6, samples=8)
    assert r2.status == "fail" and r2.value > 20.0, r2.message


def test_overhang_ignores_the_face_on_the_bed():
    cube = box(0, 10, 0, 10, 0, 10).renamed("куб")
    r = overhang(cube, limit_deg=45.0)
    assert r.status == "pass"
    assert r.value == pytest.approx(0.0, abs=1e-9), \
        "у куба нависаний нет: низ лежит на столе"


def test_overhang_counts_a_ceiling():
    """Полка, висящая в воздухе: её нижняя грань — настоящее нависание."""
    part = union([box(0, 2, 0, 10, 0, 20),          # стойка
                  box(0, 20, 0, 10, 18, 20)]).renamed("полка")
    r = overhang(part, limit_deg=45.0, max_frac=0.05)
    assert r.status == "warn" and r.value > 0.05
    assert r.severity == "warn"


def test_bbox_fits_bed():
    assert bbox_fits(box(0, 100, 0, 100, 0, 100).renamed("д")).status == "pass"
    big = box(0, 300, 0, 100, 0, 100).renamed("д")
    assert bbox_fits(big).status == "fail"
    # узкая, но длинная — влезает поворотом
    long_ = box(0, 250, 0, 20, 0, 20).renamed("д")
    assert bbox_fits(long_).status == "pass"


# ------------------------------------------- деталь с просветом вдоль движения
@pytest.mark.parametrize("shape", ["брусок", "скоба", "окно"])
@pytest.mark.parametrize("second", [None, 11.0, 13.0])
def test_the_obstacle_thickness_does_not_depend_on_gaps_in_the_part(shape, second):
    """Губка 1.5 мм. У скобы и детали с окном есть просвет вдоль движения:
    первая версия останавливалась на нём и давала толщину 0.00 — ложный
    провал. Вторая преграда дальше на пути к толщине первой не относится."""
    from datum.checks.fit import obstruction_thickness
    from datum.geom import box, diff, union

    parts = {
        "брусок": box(0, 4, -2, 2, 0, 4),
        "скоба": union([box(0, 4, -2, 2, 0, 1), box(0, 4, -2, 2, 3, 4),
                        box(0, 0.5, -2, 2, 0, 4)]),
        "окно": diff(box(0, 4, -2, 2, 0, 4), [box(1.5, 2.5, -3, 3, 1, 3)]),
    }
    holder = box(-10, 10, -10, 10, 6, 7.5)
    if second is not None:
        holder = union([holder, box(-10, 10, -10, 10, second, second + 1)])
    r = obstruction_thickness(parts[shape], holder, plane="xz", at=0.0,
                              band=(1.0, 3.0), min_thickness=1.2)
    assert r.status == "pass"
    assert r.value == pytest.approx(1.5, abs=0.03)


# ------------------------------------------- число и статус не противоречат
def test_bbox_fits_with_rotation_has_a_non_negative_margin():
    """Плита 250×100 на столе 200×256 помещается с поворотом: статус pass, а
    запас раньше считался по прямой укладке и выходил −26 %."""
    from datum.checks.printing import bbox_fits
    from datum.geom import box

    r = bbox_fits(box(0, 250, 0, 100, 0, 5), bed=(200, 256, 256), margin=0)
    assert r.status == "pass" and "поворотом" in r.message
    assert r.value <= 1.0 and r.margin >= 0


def test_a_jammed_part_reports_the_jam_not_the_hold():
    """Заклинившая деталь с большим упором получала запас +51 при провале и
    уезжала в хвост «худшее первым»."""
    from datum.checks.motion import escape
    from datum.geom import box

    part = box(0, 10, 0, 10, 0, 10)
    holder = box(-5, 15, -5, 15, 9.9, 20)         # упирается сразу, зазора нет
    r = escape(part, holder, ["+Z"], gap=0.3)[0]
    assert r.status == "fail"
    assert r.op == "<=" and r.margin < 0


def test_escape_refuses_a_zero_hold():
    from datum.checks.motion import escape
    from datum.geom import box

    with pytest.raises(ValueError, match="hold_vol"):
        escape(box(0, 1, 0, 1, 0, 1), box(2, 3, 2, 3, 2, 3), ["+Z"], gap=0.3, hold_vol=0)


def test_clearance_refuses_a_threshold_beyond_its_search():
    from datum.checks.fit import clearance
    from datum.geom import box

    with pytest.raises(ValueError, match="search"):
        clearance(box(0, 1, 0, 1, 0, 1), box(7, 8, 0, 1, 0, 1), min_mm=5, search=4)


def test_an_empty_body_is_an_error_not_a_perfect_fit():
    from datum.checks.fit import clearance, no_intersection
    from datum.geom import box, diff

    a = box(0, 1, 0, 1, 0, 1)
    gone = diff(box(0, 1, 0, 1, 0, 1), [box(-1, 2, -1, 2, -1, 2)])
    assert clearance(a, gone, min_mm=0.2).status == "error"
    assert no_intersection(a, gone).status == "error"


# ------------------------------------------------------------- тонкий поясок
def _tube_with_groove(groove_h: float):
    """Труба Ø40 со стенкой 3 мм и наружным пазом на высоте 50: остаётся 0.6 мм."""
    from datum.geom import cyl_z, diff

    tube = diff(cyl_z(40, 0, 100), [cyl_z(34, -1, 101)])
    ring = diff(cyl_z(50, 50 - groove_h / 2, 50 + groove_h / 2), [cyl_z(35.2, 0, 100)])
    return diff(tube, [ring]).renamed("труба")


def test_min_wall_finds_a_thin_band_between_its_old_slices():
    """Найдено обзором: 24 среза на 100 мм проскакивали паз высотой 3 мм,
    оставлявший стенку 0.6 мм («0.00 мм² тонкого»); карта печатности рядом
    показывала его красным. Теперь режется посередине между высотами вершин."""
    from datum.checks.printing import min_wall

    r = min_wall(_tube_with_groove(3.0))
    assert r.status == "fail" and r.value > 40, r.message


def test_a_steep_rim_at_the_top_is_an_edge_not_a_wall():
    """Верх крутого борта тонок лишь на долю миллиметра по высоте (как у
    `examples/spool`: 0.47 мм) — кромка, а не стенка: не считается, но
    называется в сообщении. Тот же контур, вытянутый на 2 мм по высоте, —
    стенка, и ловится."""
    from shapely.geometry import Polygon

    from datum.checks.printing import min_wall
    from datum.geom import extrude, rotate

    def wall(top: float):
        # Стенка 3 мм, сверху сходящаяся в острие за `top` мм высоты (вдоль Y).
        profile = Polygon([(0, 0), (3, 0), (3, 10), (1.5, 10 + top), (0, 10)])
        return rotate(extrude(profile, 0, 40), rx=90).renamed("борт")

    sharp = min_wall(wall(0.5), max_lost_area=5.0)
    assert sharp.status == "pass" and "кромкой" in sharp.message, sharp.message
    tall = min_wall(wall(6.0), max_lost_area=5.0)
    assert tall.status == "fail", tall.message


def test_under_part_sees_a_flange_between_the_old_slices():
    """Найдено обзором: ручка — вал Ø6 (z 2…14) с фланцем Ø16 на z 4…5 над
    полкой корпуса (z 0…3.9, отверстие Ø7). Три прежних среза (низ, середина,
    верх) фланец не задевали: «0.00 мм³». Теперь — область под деталью
    послойно: полка под сжатым на 0.4 мм фланцем целиком,
    π/4·(15.2² − 7²)·3.9 ≈ 557.6 мм³ (окружности — многоугольники)."""
    import math

    from datum.geom import box, cyl_z, union

    knob = union([cyl_z(6, 2, 14), cyl_z(16, 4, 5)]).renamed("ручка")
    housing = (box(-15, 15, -15, 15, 0, 3.9) - cyl_z(7, -1, 5)).renamed("корпус")
    r = under_part_violation(housing, knob)
    expected = math.pi / 4 * (15.2 ** 2 - 7 ** 2) * 3.9
    assert r.status == "fail" and r.value == pytest.approx(expected, rel=0.01), r.message
    # Корпус целиком в стороне — ничего.
    aside = box(30, 40, 30, 40, 0, 3.9).renamed("корпус")
    assert under_part_violation(aside, knob).status == "pass"


@pytest.mark.parametrize("up", ["-Z", "+X", "-X", "+Y", "-Y"])
def test_min_wall_names_the_place_in_the_frame_section_uses(up):
    """Найдено обзором: у детали с print_up="-Z" тонкое место называлось
    «z=-17.77» (поза печати), а `section` там ничего не находил. Теперь рядом
    — готовый срез в координатах детали, и он попадает в тонкое."""
    import re

    from datum.geom.measure import section_xy, section_xz, section_yz

    r = min_wall(_tube_with_groove(3.0), up=up)
    found = re.search(r"section (xy|xz|yz) (-?[\d.]+)", r.message)
    assert found, r.message
    plane, at = found.group(1), float(found.group(2))
    cut = {"xy": section_xy, "xz": section_xz, "yz": section_yz}[plane](
        _tube_with_groove(3.0), at)
    assert cut.area > 1.0, (plane, at)
    assert abs(abs(at) - 50.0) < 1.6 if plane == "xy" else True
