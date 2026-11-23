"""Gridfinity: размеры по спецификации и сопряжения, проверенные числом.

Константы сверены с gridfinity-rebuilt-openscad (MIT). Здесь проверяется
не «похоже ли», а три вещи, которые решают, встанет ли ящик: сечения
основания на нужных высотах, посадка в плиту и посадка ящика на ящик.
"""

from __future__ import annotations

import pytest

from datum.geom.guard import Expect, checkpoint
from datum.geom.measure import bbox, inter_vol, section_xy
from datum.geom.xform import move
from datum.std.gridfinity import (LIP_HEIGHT, PLATE_HEIGHT, Bin, GridfinityError,
                                  baseplate)


def _width(solid, z):
    x0, _, x1, _ = section_xy(solid, z).bounds
    return x1 - x0


def test_the_base_profile_is_the_spec_one():
    """0.8 под 45°, 1.8 вертикально, 2.15 под 45° — от 35.6 до 41.5 мм."""
    one = Bin(1, 1, 3).solid()
    assert _width(one, 0.001) == pytest.approx(35.6, abs=0.01)
    assert _width(one, 0.4) == pytest.approx(36.4, abs=0.01)
    assert _width(one, 1.5) == pytest.approx(37.2, abs=0.01)
    assert _width(one, 3.0) == pytest.approx(38.0, abs=0.01)
    assert _width(one, 6.0) == pytest.approx(41.5, abs=0.01)


def test_sizes_follow_the_grid():
    b = Bin(3, 2, 6)
    lo, hi = bbox(b.solid())
    assert (hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx((125.5, 83.5))
    assert hi[2] - lo[2] == pytest.approx(42 + LIP_HEIGHT)
    assert (b.height, b.total_height) == (42.0, 42.0 + LIP_HEIGHT)
    assert Bin(1, 1, 2, lip=False).total_height == 14.0


def test_a_bin_stacks_on_a_bin():
    """Верхний ящик садится фасками основания на губку нижнего.

    Обе фаски под 45°, у основания 0.8, у губки 0.7 при зазоре 0.25 на
    сторону — поэтому посадка на 0.35 мм ниже номинала, как у эталона. Не
    доходит до неё — висит на губке; проваливается глубже — губка не держит.
    """
    lower, upper = Bin(2, 1, 3).solid(), Bin(2, 1, 3).solid()
    h = 21.0
    assert inter_vol(move(upper, dz=h - 0.3), lower) == 0.0
    assert inter_vol(move(upper, dz=h - 0.45), lower) > 0.1


def test_a_bin_sits_in_the_baseplate():
    plate = baseplate(2, 1)
    lo, hi = bbox(plate)
    assert (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) == pytest.approx((84, 42, PLATE_HEIGHT))
    b = Bin(2, 1, 3).solid()
    assert inter_vol(move(b, dz=0.26), plate) == 0.0
    assert inter_vol(move(b, dz=0.1), plate) > 0.1


def test_magnet_and_screw_holes_are_on_the_26mm_square():
    b = Bin(2, 1, 3, magnets=True, screws=True).solid()
    near_bottom = section_xy(b, 1.0)
    holes = [h for p in getattr(near_bottom, "geoms", [near_bottom]) for h in p.interiors]
    assert len(holes) == 8
    xs = sorted(round(sum(c[0] for c in h.coords[:-1]) / (len(h.coords) - 1), 2) for h in holes)
    assert xs[0] == pytest.approx(-21 - 13, abs=0.05) and xs[-1] == pytest.approx(21 + 13, abs=0.05)
    # Выше магнита остаётся только винт ⌀3.
    screw = section_xy(b, 4.0)
    radii = [(h.bounds[2] - h.bounds[0]) / 2 for p in getattr(screw, "geoms", [screw])
             for h in p.interiors]
    assert radii and all(r == pytest.approx(1.5, abs=0.02) for r in radii)


@pytest.mark.parametrize("make", [
    lambda: Bin(2, 1, 3),
    lambda: Bin(3, 2, 6, magnets=True).compartments(3, 2),
    lambda: Bin(2, 2, 4, screws=True).pocket(60, 50, depth=15, corner_r=5),
    lambda: Bin(1, 1, 2, lip=False),
    lambda: Bin(2, 1, 5).round_pocket(22, depth=10, center=(10, 0)),
])
def test_every_variant_is_one_clean_body(make):
    report = checkpoint(make().solid(), Expect())
    assert report.bodies == 1 and not report.void_volumes and not report.sliver_volumes


def test_a_pocket_goes_down_from_the_nominal_top():
    b = Bin(2, 1, 4).pocket(50, 20, depth=10)
    s = b.solid()
    assert section_xy(s, 28 - 5).area < section_xy(s, 28 - 12).area   # в кармане меньше материала
    assert section_xy(s, 28 - 12).area == pytest.approx(83.5 * 41.5, rel=0.02)


def test_the_hollow_bin_has_a_supported_lip():
    """Под губкой полого ящика — опорная фаска 45° до стенки: губка не висит."""
    b = Bin(1, 1, 3).solid()
    ring = [section_xy(b, z).area for z in (18.0, 19.3, 20.5)]
    assert ring[0] < ring[1] < ring[2]


@pytest.mark.parametrize("bad, words", [
    (lambda: Bin(1, 1, 1), "не выше основания"),
    (lambda: Bin(0, 1, 3), "целое"),
    (lambda: Bin(1, 1, 3, wall=0.5), "стенка"),
    (lambda: Bin(1, 1, 3).pocket(40, 30), "выходит за стенку"),
    (lambda: Bin(1, 1, 3).pocket(20, 20, depth=30), "глубина"),
    (lambda: Bin(2, 1, 3).compartments(2).pocket(10, 10), "отсеки"),
    (lambda: Bin(2, 1, 3).pocket(10, 10).compartments(2), "карманы"),
    (lambda: Bin(1, 1, 3).compartments(20), "уже 5 мм"),
])
def test_bad_parameters_are_named(bad, words):
    with pytest.raises(GridfinityError, match=words):
        bad()


def test_a_scan_outline_becomes_a_pocket():
    """Контур предмета из `footprint` — прямо в карман."""
    from datum.geom import box, footprint

    outline = footprint(box(-30, 30, -15, 15, 0, 10), clearance=0.3)
    b = Bin(2, 1, 4).shape_pocket(outline, depth=12)
    ring = section_xy(b.solid(), 20)          # на 8 мм ниже верха — в кармане
    assert len(ring.interiors) == 1
    x0, y0, x1, y1 = ring.interiors[0].bounds
    assert (x1 - x0, y1 - y0) == pytest.approx((60.6, 30.6), abs=0.05)


def test_the_example_project_passes_its_checks(tmp_path):
    """Пример в репозитории — не картинка: он обязан проходить свои проверки."""
    import shutil
    from pathlib import Path

    from datum.doc.project import Project

    src = Path(__file__).resolve().parents[3] / "examples" / "gridfinity"
    dst = tmp_path / "gridfinity"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".datum", "output"))
    run = Project.open(dst).run_checks()
    assert run.ok, run.report()
