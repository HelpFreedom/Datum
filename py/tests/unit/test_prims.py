"""Примитивы против аналитики.

Проверяем не «не упало», а что тело оказалось ТАМ и ТАКОГО размера. Повороты в
prism_yz / prism_xz — классическое место, где путается ось, и увидеть это можно
только по габаритам.
"""

from __future__ import annotations

import math

import pytest

from datum.geom.booleans import diff, intersect, split_by_plane, union
from datum.geom.prims import (CIRC_N, box, cone_z, cyl_x, cyl_y, cyl_z, extrude,
                              prism_xy, prism_xz, prism_yz, revolve_z, rounded_slab, sphere)
from datum.geom.profile2d import rrect_poly
from datum.geom.xform import (array_linear, array_polar, frame, move, mirror,
                              place, rot_about, rotate, scale)


def bb(s):
    lo, hi = s.manifold.bounding_box()[:3], s.manifold.bounding_box()[3:]
    return lo, hi


def extents(s):
    lo, hi = bb(s)
    return tuple(round(hi[i] - lo[i], 4) for i in range(3))


# ------------------------------------------------------------------ box
def test_box_bounds_are_exact():
    s = box(1, 4, 2, 8, -3, 1)
    lo, hi = bb(s)
    assert lo == pytest.approx((1, 2, -3))
    assert hi == pytest.approx((4, 8, 1))
    assert s.manifold.volume() == pytest.approx(3 * 6 * 4)


# ------------------------------------------------------------------ призмы: ОСИ
def test_prism_xy_extrudes_along_z():
    s = prism_xy([(0, 0), (10, 0), (10, 4), (0, 4)], 2, 7)
    lo, hi = bb(s)
    assert lo == pytest.approx((0, 0, 2)), "основание профиля в XY, низ на z0"
    assert hi == pytest.approx((10, 4, 7))
    assert s.manifold.volume() == pytest.approx(10 * 4 * 5)


def test_prism_yz_extrudes_along_x():
    """Профиль задан в (y, z), выдавливание по X."""
    s = prism_yz([(0, 0), (6, 0), (6, 3), (0, 3)], -2, 5)
    lo, hi = bb(s)
    assert lo == pytest.approx((-2, 0, 0)), f"нижний угол не там: {lo}"
    assert hi == pytest.approx((5, 6, 3)), f"верхний угол не там: {hi}"
    assert s.manifold.volume() == pytest.approx(7 * 6 * 3)


def test_prism_xz_extrudes_along_y():
    """Профиль задан в (x, z), выдавливание по Y."""
    s = prism_xz([(0, 0), (8, 0), (8, 2), (0, 2)], 1, 4)
    lo, hi = bb(s)
    assert lo == pytest.approx((0, 1, 0)), f"нижний угол не там: {lo}"
    assert hi == pytest.approx((8, 4, 2)), f"верхний угол не там: {hi}"
    assert s.manifold.volume() == pytest.approx(8 * 3 * 2)


# ------------------------------------------------------------------ цилиндры
def test_cyl_z_position_and_volume():
    s = cyl_z(10.0, 0.0, 20.0, x=3.0, y=-4.0, n=256)
    lo, hi = bb(s)
    assert lo == pytest.approx((-2, -9, 0), abs=0.02)
    assert hi == pytest.approx((8, 1, 20), abs=0.02)
    assert s.manifold.volume() == pytest.approx(math.pi * 25 * 20, rel=2e-4)


@pytest.mark.parametrize("fn,axis,other", [(cyl_x, 0, (1, 2)), (cyl_y, 1, (0, 2))])
def test_cyl_x_and_y_lie_along_their_axis(fn, axis, other):
    """Цилиндр обязан быть длинным по своей оси и круглым по двум другим."""
    s = fn(8.0, 0.0, 30.0, 5.0, -2.0, 256)
    e = extents(s)
    assert e[axis] == pytest.approx(30.0, abs=0.02), f"длина не по своей оси: {e}"
    for k in other:
        assert e[k] == pytest.approx(8.0, abs=0.05), f"сечение не круглое Ø8: {e}"
    assert s.manifold.volume() == pytest.approx(math.pi * 16 * 30, rel=2e-3)


def test_cyl_x_is_centred_on_given_yz():
    s = cyl_x(8.0, 0.0, 10.0, y=5.0, z=-2.0, n=256)
    lo, hi = bb(s)
    assert (lo[1] + hi[1]) / 2 == pytest.approx(5.0, abs=0.02)
    assert (lo[2] + hi[2]) / 2 == pytest.approx(-2.0, abs=0.02)


def test_cyl_y_is_centred_on_given_xz():
    """cyl_y стоит на prism_xz, где был баг с осью — проверяем отдельно."""
    s = cyl_y(8.0, 0.0, 10.0, x=5.0, z=-2.0, n=256)
    lo, hi = bb(s)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(5.0, abs=0.02)
    assert (lo[2] + hi[2]) / 2 == pytest.approx(-2.0, abs=0.02)
    assert lo[1] == pytest.approx(0.0, abs=0.02) and hi[1] == pytest.approx(10.0, abs=0.02)


def test_prism_xz_profile_lands_upright():
    """Несимметричный профиль: ловит зеркальное отражение, которое габариты скрывают."""
    s = prism_xz([(0, 0), (10, 0), (10, 6)], 0, 2)     # треугольник, толстый край справа-сверху
    from datum.geom.measure import section_xz
    poly = section_xz(s, 1.0)
    # вершина (10,6) должна быть наверху справа, а не внизу
    xs, zs = poly.exterior.xy
    top = max(zip(xs, zs), key=lambda p: p[1])
    assert top[1] == pytest.approx(6.0, abs=0.01), f"профиль перевёрнут: верх в {top}"
    assert top[0] == pytest.approx(10.0, abs=0.01)


# ------------------------------------------------------------------ прочие примитивы
def test_sphere_and_cone():
    assert sphere(10.0, n=256).manifold.volume() == pytest.approx(4 / 3 * math.pi * 125, rel=3e-3)
    v = cone_z(10.0, 0.0, 0.0, 12.0, n=256).manifold.volume()
    assert v == pytest.approx(math.pi * 25 * 12 / 3, rel=3e-3)


def test_revolve_z_makes_a_ring():
    """Профиль (r, z) от r=8 до r=10, высотой 5 → кольцо."""
    s = revolve_z([(8, 0), (10, 0), (10, 5), (8, 5)], n=256)
    e = extents(s)
    assert e == pytest.approx((20, 20, 5), abs=0.05)
    assert s.manifold.volume() == pytest.approx(math.pi * (100 - 64) * 5, rel=3e-3)


def test_extrude_rejects_inverted_range():
    # Нулевая длина — законная пустота: фичу выключают, сведя её к нулю.
    assert extrude(rrect_poly(0, 0, 10, 10, 2), 5, 5).manifold.is_empty()
    # Перепутанные границы — отказ, а не пустое тело: `diff(куб, [cyl_z(5, 11,
    # -1, …)])` возвращал куб без отверстия, и никто об этом не говорил.
    with pytest.raises(ValueError, match="границы перепутаны"):
        extrude(rrect_poly(0, 0, 10, 10, 2), 5, 1)


def test_inverted_bounds_and_negative_sizes_are_refused_everywhere():
    from datum.geom import cone_z, cyl_x, cyl_y, cyl_z, sphere
    from datum.geom.prims import prism_xz

    for build in (lambda: cyl_z(5, 11, -1), lambda: cyl_x(5, 3, 1), lambda: cyl_y(5, 3, 1),
                  lambda: cone_z(4, 2, 9, 1), lambda: prism_xz([(0, 0), (1, 0), (0, 1)], 4, 2)):
        with pytest.raises(ValueError, match="границы перепутаны"):
            build()
    for build in (lambda: cyl_z(-5, 0, 1), lambda: sphere(-1), lambda: cone_z(-1, 2, 0, 1)):
        with pytest.raises(ValueError, match="не бывает отрицательным"):
            build()
    for build in (lambda: cyl_z(5, 3, 3), lambda: cyl_z(0, 0, 3), lambda: sphere(0),
                  lambda: cone_z(0, 0, 0, 1)):
        assert build().manifold.is_empty()
    hole = diff(box(0, 20, 0, 20, 0, 10), [cyl_z(5, -1, 11, 10, 10)])
    assert hole.manifold.volume() < 4000 - 190


def test_rounded_slab_is_smaller_than_the_sharp_one():
    g = rrect_poly(0, 0, 30, 20, 4)
    sharp = extrude(g, 0, 6)
    soft = rounded_slab(g, 0, 6, r_top=1.5, r_bot=1.5)
    assert extents(soft) == pytest.approx(extents(sharp), abs=0.01), "габариты не должны измениться"
    assert soft.manifold.volume() < sharp.manifold.volume(), "скругление обязано убрать материал"
    assert soft.manifold.volume() > sharp.manifold.volume() * 0.9, "убрало слишком много"


# ------------------------------------------------------------------ преобразования
def test_move_and_rot_about():
    s = move(box(0, 2, 0, 2, 0, 2), 10, 0, 0)
    assert bb(s)[0] == pytest.approx((10, 0, 0))
    r = rot_about(box(0, 2, 0, 2, 0, 2), "z", 90, (0, 0, 0))
    lo, hi = bb(r)
    assert lo == pytest.approx((-2, 0, 0), abs=1e-6)
    assert hi == pytest.approx((0, 2, 2), abs=1e-6)


def test_rot_about_keeps_the_centre_fixed():
    c = (5.0, 5.0, 0.0)
    s = box(4, 6, 4, 6, 0, 1)
    r = rot_about(s, "z", 37.0, c)
    lo, hi = bb(r)
    assert (lo[0] + hi[0]) / 2 == pytest.approx(5.0, abs=1e-6)
    assert (lo[1] + hi[1]) / 2 == pytest.approx(5.0, abs=1e-6)


def test_mirror_and_scale():
    m = mirror(box(1, 3, 0, 1, 0, 1), (1, 0, 0))
    assert bb(m)[0][0] == pytest.approx(-3)
    s = scale(box(0, 2, 0, 2, 0, 2), 2, 1, 0.5)
    assert extents(s) == pytest.approx((4, 2, 1))


def test_frame_and_place_apply_rotation_and_offset():
    s = place(box(0, 2, 0, 2, 0, 2), frame((10, 0, 0), rz=90))
    lo, hi = bb(s)
    assert lo == pytest.approx((8, 0, 0), abs=1e-6)
    assert hi == pytest.approx((10, 2, 2), abs=1e-6)


def test_arrays():
    a = array_linear(box(0, 1, 0, 1, 0, 1), 5, dx=3)
    assert extents(a)[0] == pytest.approx(13)
    assert a.manifold.volume() == pytest.approx(5.0)
    p = array_polar(box(4, 6, -1, 1, 0, 1), 6, axis="z", c=(0, 0, 0))
    assert p.manifold.decompose().__len__() == 6


# ------------------------------------------------------------------ булевы
def test_union_diff_intersect():
    a, b = box(0, 10, 0, 10, 0, 10), box(5, 15, 0, 10, 0, 10)
    assert union([a, b]).manifold.volume() == pytest.approx(1500)
    assert diff(a, [b]).manifold.volume() == pytest.approx(500)
    assert intersect([a, b]).manifold.volume() == pytest.approx(500)


def test_union_skips_empty_and_none():
    from datum.geom.prims import empty
    assert union([box(0, 1, 0, 1, 0, 1), None, empty()]).manifold.volume() == pytest.approx(1.0)
    assert union([]).manifold.is_empty()


def test_intersection_with_an_empty_solid_is_empty():
    """Прежде пустой вход выбрасывался, и пересечение с пустым было не пустым."""
    from datum.geom.prims import empty
    assert intersect([box(0, 1, 0, 1, 0, 1), empty()]).manifold.is_empty()
    assert intersect([box(0, 1, 0, 1, 0, 1), None]).manifold.volume() == pytest.approx(1.0)


def test_union_in_a_loop_stays_lazy():
    """`union([s, x])` не спрашивает у входов пустоту: `is_empty()` форсирует, и
    в цикле тело пересчитывалось на каждом шаге."""
    import time

    s = box(0, 1, 0, 1, 0, 1)
    pieces = [box(i * 2, i * 2 + 1, 0, 1, 0, 1) for i in range(1, 200)]
    t0 = time.perf_counter()
    for piece in pieces:
        s = union([s, piece])
    build = time.perf_counter() - t0
    t0 = time.perf_counter()
    assert s.manifold.volume() == pytest.approx(200.0)
    force = time.perf_counter() - t0
    assert build < force, f"сборка {build * 1e3:.1f} мс дороже расчёта {force * 1e3:.1f} мс"


def test_diff_with_nothing_returns_the_same_solid():
    a = box(0, 10, 0, 10, 0, 10)
    assert diff(a, []) is a


def test_split_by_plane_halves():
    top, bot = split_by_plane(box(0, 10, 0, 10, 0, 10), (0, 0, 1), 5.0)
    assert top.manifold.volume() == pytest.approx(500)
    assert bot.manifold.volume() == pytest.approx(500)


# ------------------------------------------------ скругление не шире плиты
def test_rounded_slab_refuses_radii_larger_than_the_thickness():
    """Плита 2 мм с радиусами по 5 выходила высотой 8 и втрое больше объёмом."""
    import pytest
    from shapely.geometry import box as sbox

    from datum.geom.prims import rounded_slab

    with pytest.raises(ValueError, match="больше толщины"):
        rounded_slab(sbox(0, 0, 20, 20), 0.0, 2.0, r_top=5.0, r_bot=5.0)
    half = rounded_slab(sbox(0, 0, 20, 20), 0.0, 2.0, r_top=1.0, r_bot=1.0)
    from datum.geom.measure import bbox
    lo, hi = bbox(half)
    assert lo[2] >= -1e-6 and hi[2] <= 2.0 + 1e-6
