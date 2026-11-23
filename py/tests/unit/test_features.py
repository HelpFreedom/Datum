"""Элементы для печати: кромки, стакан, отверстия, резьба, накатка, размещение.

Против точных ответов: объём фаски и скругления — интегралом площади
смещённого контура; винт и гайка обязаны сходиться без пересечения на всём
обороте по нитке и расходиться против неё.
"""

from __future__ import annotations

import math

import pytest
from scipy.integrate import quad
from shapely.geometry import Point
from shapely.geometry import box as sbox

from datum.geom import (align, anchor, box, centre_on, chamfer_corners, cup, diff, hole_z,
                        knurl, move, rotate, round_corners, slab, stack, thread)
from datum.geom.measure import bbox, inter_vol, min_gap, vol
from datum.geom.topology import analyze

W, D, H = 40.0, 20.0, 10.0


def _rect_area(t: float) -> float:
    return (W - 2 * t) * (D - 2 * t)


def _closed(solid) -> bool:
    rep = analyze(solid.manifold)
    return rep.status == "NoError" and rep.bodies >= 1


# ---------------------------------------------------------------- кромки
def test_a_bottom_chamfer_is_exactly_45_degrees():
    c = 1.0
    s = slab(sbox(0, 0, W, D), 0, H, chamfer_bot=c)
    exact = quad(lambda h: _rect_area(c - h), 0, c)[0] + _rect_area(0) * (H - c)
    assert _closed(s) and vol(s) == pytest.approx(exact, rel=1e-6)
    lo, hi = bbox(s)
    assert lo == pytest.approx((0, 0, 0)) and hi == pytest.approx((W, D, H))


def test_a_top_fillet_follows_the_arc_within_the_chord():
    r = 3.0
    s = slab(sbox(0, 0, W, D), 0, H, fillet_top=r)
    exact = quad(lambda h: _rect_area(r - math.sqrt(r * r - (r - h) ** 2)), 0, r)[0] \
        + _rect_area(0) * (H - r)
    assert _closed(s)
    # Дуга из хорд лежит внутри точной: объём меньше, но на доли процента.
    assert exact * (1 - 2e-3) < vol(s) < exact


def test_no_terrace_faces_down_on_a_chamfered_bottom():
    """Ни одной горизонтальной грани, глядящей вниз, кроме самого дна: ступенек
    нет, и проверка нависаний не сочтёт фаску нависанием."""
    import numpy as np

    from datum.geom.convert import vertices_faces

    s = slab(sbox(0, 0, 30, 20), 0, 6, chamfer_bot=0.6)
    v, f = vertices_faces(s.manifold)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    down = n[:, 2] < -0.99
    assert np.allclose(tri[down][:, :, 2], 0.0), "вниз смотрит только дно"
    steep = (n[:, 2] < -0.1) & ~down
    assert np.allclose(n[steep][:, 2], -math.sqrt(0.5), atol=1e-4), "фаска — ровно 45°"


def test_a_profile_with_a_hole_and_rounded_corners_keeps_one_body_with_a_through_hole():
    g = round_corners(sbox(0, 0, 40, 30).difference(Point(20, 15).buffer(6, quad_segs=32)), 4,
                      concave=False)
    s = slab(g, 0, 8, chamfer_bot=0.5, fillet_top=2.0)
    rep = analyze(s.manifold)
    assert rep.status == "NoError" and rep.bodies == 1 and rep.genus == 1


@pytest.mark.parametrize("kw, words", [
    ({"fillet_top": 5.0}, "схлопнулось"),            # радиус угла 4 < кромки 5
    ({"chamfer_bot": 3, "chamfer_top": 6}, "выше самой плиты"),
    ({"chamfer_top": 1, "fillet_top": 1}, "либо фаска, либо скругление"),
    ({"chamfer_bot": -1}, "отрицательным"),
])
def test_impossible_edges_are_refused_with_a_reason(kw, words):
    g = round_corners(sbox(0, 0, 40, 30), 4, concave=False)
    with pytest.raises(ValueError, match=words):
        slab(g, 0, 8, **kw)


def test_corners_in_plan():
    sq = sbox(0, 0, 20, 10)
    assert round_corners(sq, 3).area == pytest.approx(200 - 4 * (9 - math.pi * 9 / 4), rel=1e-3)
    assert chamfer_corners(sq, 2).area == pytest.approx(200 - 4 * 2)
    with pytest.raises(ValueError, match="узкого места"):
        round_corners(sbox(0, 0, 20, 2), 3)


# ---------------------------------------------------------------- стакан, отверстия
def test_cup_walls_and_floor_are_exact():
    c = cup(sbox(0, 0, 30, 20), 0, 15, 1.6, 1.2)
    assert _closed(c)
    assert vol(c) == pytest.approx(30 * 20 * 15 - (30 - 3.2) * (20 - 3.2) * (15 - 1.2), rel=1e-9)
    with pytest.raises(ValueError, match="не оставляют места"):
        cup(sbox(0, 0, 3, 3), 0, 5, 2, 1)


@pytest.mark.parametrize("kw", [{}, {"cbore": (6.5, 3.4)}, {"csk": 6.3}, {"csk": (6.3, 82)}])
def test_screw_holes_go_through_and_leave_one_body(kw):
    plate = diff(box(0, 20, 0, 20, 0, 6), [hole_z(3.4, 0, 6, 10, 10, **kw)])
    rep = analyze(plate.manifold)
    assert rep.status == "NoError" and rep.bodies == 1 and rep.genus == 1
    assert vol(plate) < 20 * 20 * 6 - math.pi * 1.7 ** 2 * 6 * 0.99


def test_a_countersink_wider_than_the_hole_only():
    with pytest.raises(ValueError, match="шире отверстия"):
        hole_z(3, 0, 6, csk=(3.0, 90))


# ---------------------------------------------------------------- резьба
@pytest.mark.parametrize("left", [False, True])
def test_screw_and_nut_mate_along_the_thread_and_jam_against_it(left):
    d, p = 8.0, 1.25
    screw = thread(d, p, 0, 12, clearance=0.3, left=left)
    nut = diff(box(-8, 8, -8, 8, 1, 9), [thread(d, p, 1, 9, clearance=0.3, internal=True, left=left)])
    assert _closed(screw) and analyze(nut.manifold).bodies == 1
    assert inter_vol(screw, nut) == pytest.approx(0, abs=1e-6)
    assert min_gap(screw, nut, 1.0) == pytest.approx(0.15 * math.sin(math.radians(60)), abs=0.02)
    s = -1 if left else 1
    # Правая: поворот против часовой (вид сверху) и подъём на шаг за оборот.
    for a in range(0, 360, 30):
        moved = move(rotate(screw, rz=s * a), 0, 0, p * a / 360)
        assert inter_vol(moved, nut) == pytest.approx(0, abs=1e-6), a
    jammed = move(rotate(screw, rz=-s * 90), 0, 0, p * 90 / 360)
    assert inter_vol(jammed, nut) > 1.0


def test_a_thread_needs_its_clearance_named():
    with pytest.raises(TypeError):
        thread(8, 1.25, 0, 10)                      # зазора нет — не угадываем
    with pytest.raises(ValueError, match="от 0 до шага"):
        thread(8, 1.25, 0, 10, clearance=2)


def test_a_knurl_is_one_closed_body_within_its_diameter():
    k = knurl(20, 0, 15, pitch=1.5)
    rep = analyze(k.manifold)
    assert rep.status == "NoError" and rep.bodies == 1
    lo, hi = bbox(k)
    assert hi[0] <= 10 + 1e-6 and hi[2] == pytest.approx(15)


# ---------------------------------------------------------------- размещение
def test_anchors_name_points_of_the_bounding_box():
    a = box(0, 10, 0, 20, 0, 5)
    assert anchor(a) == pytest.approx((5, 10, 2.5))
    assert anchor(a, "+Z") == pytest.approx((5, 10, 5))
    assert anchor(a, "+X-Y+Z") == pytest.approx((10, 0, 5))
    assert anchor(a, " -x +y ") == pytest.approx((0, 20, 2.5))
    with pytest.raises(ValueError, match="точка габарита"):
        anchor(a, "вверх")
    with pytest.raises(ValueError, match="дважды"):
        anchor(a, "+X-X")


def test_stack_puts_one_part_against_another_with_a_gap():
    base, part = box(0, 10, 0, 20, 0, 5), box(0, 4, 0, 4, 0, 2)
    on_top = stack(part, base, "+Z")
    assert anchor(on_top, "-Z") == pytest.approx(anchor(base, "+Z"))
    left = stack(part, base, "-X", gap=0.3)
    assert anchor(left, "+X")[0] == pytest.approx(-0.3)
    assert inter_vol(left, base) == pytest.approx(0)
    assert anchor(centre_on(part, base), "") == pytest.approx((5, 10, 1))
    assert anchor(align(part, "+X+Y", (100, 50, 7)), "+X+Y") == pytest.approx((100, 50, 7))
    with pytest.raises(ValueError, match="одна ось"):
        stack(part, base, "+X+Z")


def test_a_45_degree_chamfer_is_not_an_overhang_but_46_is():
    """Фаска РОВНО 45° — на пределе, не круче: float32 вершин не делает её нависанием."""
    from datum.checks.printing import overhang
    from datum.geom import hull

    s = slab(round_corners(sbox(0, 0, 70, 45), 6, concave=False), 0, 3, chamfer_bot=2.0)
    assert overhang(s, up="+Z").value == pytest.approx(0)
    # Скос 46° от вертикали — уже нависание (полоса шириной 2·tg46° на высоте 2).
    w = 2 * math.tan(math.radians(46))
    steep = hull(box(w, 70 - w, w, 45 - w, 0, 0.001) + box(0, 70, 0, 45, 2, 3).manifold)
    assert overhang(steep, up="+Z").value > 0.05


def test_a_chamfer_round_rounded_corners_far_from_the_origin_is_not_an_overhang():
    """Короткие рёбра скруглённого угла далеко от нуля: float32 вершин качает угол
    грани на ~10⁻⁵ — и это не должно делать фаску 45° нависанием (замерено в демо)."""
    from datum.checks.printing import overhang

    s = move(slab(round_corners(sbox(0, 0, 70, 45), 6, concave=False), 0, 30, chamfer_bot=0.5),
             90, 105, 0)
    assert overhang(s, up="+Z").value == pytest.approx(0)
