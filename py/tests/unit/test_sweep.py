"""Карман по направлению вставки (`geom.sweep.insertion_pocket`)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from datum.checks.assembly import assembly_path
from datum.geom import box, cyl_z, diff, insertion_pocket, rotate, sphere, union
from datum.geom.measure import bbox, inter_vol, vol


def _undercut():
    """Широкий низ, узкая шейка, шар сверху и наклон: поднутрения со всех сторон."""
    return rotate(union([cyl_z(30, 0, 6), cyl_z(12, 6, 30), sphere(18, 0, 0, 30)]), ry=15)


def test_a_sphere_pocket_is_a_tube_with_a_round_floor():
    pocket = insertion_pocket(sphere(20), "-Z", layer=0.25)
    # Нижняя полусфера плюс цилиндр до верха и на высоту предмета дальше.
    exact = 2 / 3 * math.pi * 1000 + math.pi * 100 * (10 + 20)
    assert vol(pocket) == pytest.approx(exact, rel=3e-3)
    assert vol(pocket) >= exact * 0.999, "карман не теснее точного"


def test_the_pocket_lets_the_part_in_where_its_own_shape_does_not():
    part = _undercut()
    holder = box(-30, 30, -30, 30, -5, 28)
    naive = diff(holder, [part])
    assert assembly_path(part, [naive], dirs=["-Z"], distance=60).status == "fail"
    pocketed = diff(holder, [insertion_pocket(part, "-Z", clearance=0.3)])
    assert inter_vol(part, pocketed) == 0.0
    assert assembly_path(part, [pocketed], dirs=["-Z"], distance=60).status == "pass"


def test_any_direction_is_accepted():
    """Мышь входит под углом к своей оси — направление вектором."""
    part = _undercut()
    a = math.radians(25)
    d = (0.0, math.sin(a), -math.cos(a))
    pocket = insertion_pocket(part, d, clearance=0.3)
    holder = diff(box(-40, 40, -40, 40, -5, 25), [pocket])
    assert inter_vol(part, holder) == 0.0
    lo, hi = bbox(pocket)
    assert hi[1] < -1 or lo[1] < -10, "карман уходит против вставки, к -Y и вверх"


def test_clearance_grows_the_pocket_on_every_side():
    tight = insertion_pocket(box(-5, 5, -5, 5, 0, 10), "-Z")
    loose = insertion_pocket(box(-5, 5, -5, 5, 0, 10), "-Z", clearance=0.5)
    (lo_t, hi_t), (lo_l, hi_l) = bbox(tight), bbox(loose)
    assert np.allclose(np.subtract(lo_t, lo_l), 0.5, atol=1e-6)
    assert np.allclose(np.subtract(hi_l[:2], hi_t[:2]), 0.5, atol=1e-6)


def test_wrong_arguments_are_refused():
    with pytest.raises(ValueError, match="нулевое"):
        insertion_pocket(sphere(10), (0, 0, 0))
    with pytest.raises(ValueError, match="слоя"):
        insertion_pocket(sphere(10), "-Z", layer=0)
