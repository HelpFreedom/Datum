"""Измерения и сечения.

Все проверки — на несимметричном теле. На симметричном ошибка ориентации осей
не видна: габариты сходятся, а сечение при этом перевёрнуто. Именно так у меня
и проскочила ошибка в section_xz при первом написании.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import Point

from datum.geom.booleans import union
from datum.geom.prims import box, cyl_z, sphere
from datum.geom.measure import (area, bbox, centre, com, contains, extents,
                                inter_vol, min_gap, occupancy, occupancy_ascii,
                                ray_probe, section_xy, section_xz, section_yz,
                                thickness_at, vol)


@pytest.fixture(scope="module")
def flag():
    """Плита 10×4×2 со штырём 2×1×4 у дальнего верхнего угла.

    Несимметрично по всем трём осям — любое зеркалирование или перестановка осей
    сразу видна.
    """
    return union([box(0, 10, 0, 4, 0, 2), box(8, 10, 3, 4, 2, 6)])


# ------------------------------------------------------------------ скаляры
def test_scalars(flag):
    assert vol(flag) == pytest.approx(10 * 4 * 2 + 2 * 1 * 4)
    assert bbox(flag) == (pytest.approx((0, 0, 0)), pytest.approx((10, 4, 6)))
    assert extents(flag) == pytest.approx((10, 4, 6))
    assert centre(flag) == pytest.approx((5, 2, 3))
    assert area(flag) > 0


def test_com_is_pulled_towards_the_spike(flag):
    cx, cy, cz = com(flag)
    assert cx > 5.0, "центр масс должен сместиться к штырю по X"
    assert cy > 2.0, "и по Y"
    assert cz > 1.0, "и вверх"


def test_inter_vol_and_min_gap():
    a, b = box(0, 10, 0, 10, 0, 10), box(8, 18, 0, 10, 0, 10)
    assert inter_vol(a, b) == pytest.approx(200)
    far = box(20, 30, 0, 10, 0, 10)
    assert min_gap(a, far, search=20.0) == pytest.approx(10.0, abs=1e-6)


# ------------------------------------------------------------------ сечения
def test_section_xy(flag):
    p = section_xy(flag, 4.0)
    assert p.bounds == pytest.approx((8, 3, 10, 4))
    assert p.area == pytest.approx(2.0)


def test_section_xz_is_upright_and_not_mirrored(flag):
    """Координаты результата — (x, z), без смены знака."""
    p = section_xz(flag, 3.5)
    assert p.bounds == pytest.approx((0, 0, 10, 6))
    assert p.area == pytest.approx(10 * 2 + 2 * 4)
    assert p.contains(Point(9, 5)), "штырь должен быть справа сверху"
    assert not p.contains(Point(1, 5)), "слева сверху материала нет"
    assert p.contains(Point(1, 1)), "плита внизу"


def test_section_yz_is_upright_and_not_swapped(flag):
    """Координаты результата — (y, z), именно в этом порядке."""
    p = section_yz(flag, 9.0)
    assert p.bounds == pytest.approx((0, 0, 4, 6))
    assert p.area == pytest.approx(4 * 2 + 1 * 4)
    assert p.contains(Point(3.5, 5)), "штырь при большом y"
    assert not p.contains(Point(1.0, 5)), "при малом y вверху пусто"
    assert p.contains(Point(1.0, 1)), "плита внизу"


def test_section_of_a_hollow_part_keeps_the_hole():
    """Вложенный контур обязан стать дырой, а не залиться."""
    part = box(0, 20, 0, 20, 0, 10) - cyl_z(8.0, -1, 11, 10, 10, 128).manifold
    p = section_xy(part, 5.0)
    assert len(p.interiors) == 1, "сечение трубы должно иметь одну дыру"
    assert p.area == pytest.approx(400 - math.pi * 16, rel=1e-3)


# ------------------------------------------------------------------ лучи
def test_ray_probe_measures_distance_to_surface():
    b = box(0, 10, 0, 10, 0, 10)
    assert ray_probe(b, (5, 5, 20), (0, 0, -1)) == pytest.approx(10.0, abs=1e-6)
    assert ray_probe(b, (5, 5, 20), (0, 0, 1)) is None, "вверх лучу не во что упереться"


def test_thickness_at_measures_wall():
    """Стенка трубы: внешний Ø20, внутренний Ø14 → стенка 3 мм."""
    pipe = cyl_z(20.0, 0, 10, 0, 0, 256) - cyl_z(14.0, -1, 11, 0, 0, 256).manifold
    t = thickness_at(pipe, (10.0, 0.0, 5.0), (1, 0, 0))
    assert t == pytest.approx(3.0, abs=0.05)


# ------------------------------------------------------------------ карта занятости
def test_occupancy_matches_geometry(flag):
    mask, us, vs = occupancy(flag, "XZ", 3.5, (0, 10), (0, 6), step=0.5)
    assert mask.shape == (len(vs), len(us))
    ui = int(np.argmin(np.abs(us - 9.0)))
    vi = int(np.argmin(np.abs(vs - 5.0)))
    assert mask[vi, ui], "штырь должен попасть в растр"
    ui0 = int(np.argmin(np.abs(us - 1.0)))
    assert not mask[vi, ui0], "слева сверху пусто"


def test_occupancy_ascii_is_readable(flag):
    txt = occupancy_ascii(flag, "XZ", 3.5, (0, 10), (0, 6), step=1.0)
    lines = txt.splitlines()
    assert lines[0].startswith("XZ при 3.5")
    assert "#" in txt and "." in txt
    # Точки ровно на границе тела внутренними не считаются, поэтому при шаге 1 мм
    # на высоте z=5 (штырь занимает x от 8 до 10) занят ровно столбец x=9:
    # x=8 и x=10 — грани.
    row_z5 = next(l for l in lines if l.lstrip().startswith("5.0 |"))
    body = row_z5.split("|", 1)[1]
    assert body == "." * 9 + "#" + ".", f"карта на z=5 не та: {body!r}"

    # А на высоте плиты (z=1) занято всё, кроме крайних столбцов-граней.
    row_z1 = next(l for l in lines if l.lstrip().startswith("1.0 |"))
    assert row_z1.split("|", 1)[1] == "." + "#" * 9 + ".", row_z1


def test_contains_agrees_with_volume():
    b = sphere(10.0, n=128)
    pts = (np.random.default_rng(0).random((4000, 3)) - 0.5) * 12.0
    frac = contains(b, pts).mean()
    cube_v = 12.0 ** 3
    assert frac * cube_v == pytest.approx(4 / 3 * math.pi * 125, rel=0.05)


# --------------------------------------------------------------- зонд толщины
class TestProbeOperation:
    """Зонд `measure_probe`: то, чем окно отвечает на вопрос «сколько здесь мм».

    Толщина обязана считаться ПО КАНОНИЧЕСКОЙ ГЕОМЕТРИИ, а не по мешу в окне: у
    браузера на руках только GLB, а число, которое человек запишет в параметр,
    должно совпасть с тем, что увидит проверка `min_wall`.
    """

    @staticmethod
    def run(project, **kwargs):
        import asyncio

        from datum.tools.ops import measure_probe

        return asyncio.run(measure_probe(project=str(project), **kwargs))

    def test_floor_thickness_matches_the_parameter(self, latch):
        """Луч сверху вниз в пол корпуса обязан дать ровно FLOOR = 5 × сопло."""
        out = self.run(latch, part="корпус", point=[30, 20, 40], direction=[0, 0, -1])
        assert out["hit"]["distance_mm"] == pytest.approx(38.0, abs=0.01)
        assert out["thickness_mm"] == pytest.approx(2.0, abs=0.01)

    def test_side_wall_thickness(self, latch):
        out = self.run(latch, part="корпус", point=[-10, 20, 10], direction=[1, 0, 0])
        assert out["hit"]["distance_mm"] == pytest.approx(10.0, abs=0.01)
        assert out["thickness_mm"] == pytest.approx(2.0, abs=0.01)

    def test_a_ray_into_nothing_is_not_an_error(self, latch):
        """Человек промахнулся мимо детали. Это не повод отвечать отказом."""
        out = self.run(latch, part="корпус", point=[-50, -50, 100],
                       direction=[0, 0, 1], max_dist=10)
        assert out["hit"] is None and "не встретил" in out["report"]

    def test_bad_vectors_are_refused_clearly(self, latch):
        out = self.run(latch, part="корпус", point=[1, 2], direction=[0, 0, -1])
        assert "тремя числами" in (out.get("error") or "")


def test_cone_finds_the_local_minimum_on_a_slope():
    """Зачем конус вообще нужен — измерено, а не заявлено.

    На плоской стенке косой луч длиннее осевого, и минимумом остаётся осевой:
    конус не портит очевидный случай. Смысл он имеет там, где тонкое место
    лежит не по нормали, — у клина, у схода стенки на нет. Ровно такие места и
    ищет проверка `min_wall`.
    """
    import numpy as np

    from datum.geom.measure import ray_hit, ray_probe
    from datum.geom.prims import box
    from datum.geom.xform import move, rotate

    wedge = box(0, 40, 0, 40, 0, 10) - move(
        rotate(box(-60, 60, -60, 60, 0, 60), 0, -18, 0), dz=4.0)

    def thickness(point, normal, half_angle: float) -> float:
        n = np.asarray(normal, float)
        n = n / np.linalg.norm(n)
        helper = np.eye(3)[int(np.argmin(np.abs(n)))]
        u = np.cross(n, helper)
        u = u / np.linalg.norm(u)
        v = np.cross(n, u)
        best = ray_probe(wedge, point, -n, max_dist=60)
        for k in range(8) if half_angle > 0 else ():
            phi = 2 * np.pi * k / 8
            off = np.tan(np.radians(half_angle)) * (np.cos(phi) * u + np.sin(phi) * v)
            value = ray_probe(wedge, point, -n + off, max_dist=60)
            if value is not None and (best is None or value < best):
                best = value
        assert best is not None
        return best

    # Наклонная грань: конус обязан найти тоньше.
    hit = ray_hit(wedge, (10, 20, 40), (0, 0, -1), max_dist=60)
    assert hit is not None
    _, point, normal = hit
    assert abs(normal[0]) > 0.2, "проба негодна: грань оказалась горизонтальной"
    assert thickness(point, normal, 6.0) < thickness(point, normal, 0.0) - 1e-6

    # Плоская вершина: конус не меняет ничего.
    hit = ray_hit(wedge, (30, 20, 40), (0, 0, -1), max_dist=60)
    assert hit is not None
    _, point, normal = hit
    assert thickness(point, normal, 6.0) == pytest.approx(
        thickness(point, normal, 0.0), abs=1e-9)


# ------------------------------------------------------------ контуры среза
class TestSectionContours:
    """Контуры нужны окну, чтобы залить крышку среза.

    Требование к ним одно и жёсткое: показанный срез обязан быть ТЕМ ЖЕ, что
    измеренный. Поэтому точки не упрощаются, а отображение координат совпадает
    с тем, что выверено на несимметричном теле выше.
    """

    @staticmethod
    def run(project, **kwargs):
        import asyncio

        from datum.tools.ops import section

        return asyncio.run(section(project=str(project), **kwargs))

    def test_contours_are_off_by_default(self, latch):
        """Агенту координаты не нужны: ему нужны числа. Иначе ответ инструмента
        превращается в простыню из тысяч точек."""
        out = self.run(latch, part="корпус", plane="xz", at=20)
        assert "contours" not in out
        assert out["area_mm2"] > 0

    def test_contour_area_matches_the_measured_area(self, latch):
        """Главная проверка: площадь, посчитанная по возвращённым точкам,
        совпадает с площадью, которую операция объявила. Разойдись они — окно
        залило бы не тот срез, который показывают числа."""
        from shapely.geometry import Polygon

        out = self.run(latch, part="корпус", plane="xz", at=20, contours=True)
        total = 0.0
        for piece in out["contours"]:
            total += Polygon(piece["shell"], piece["holes"]).area
        assert total == pytest.approx(out["area_mm2"], abs=1e-3)

    def test_the_case_section_is_a_u_shape(self, latch):
        """Число не из воздуха: 60×20 минус карман 56×18 плюс две губки.

        Губка теперь не прямоугольная: сверху у неё заходная фаска LEAD_IN=1.2,
        без которой крышка не проходила бы вовсе (см. `insertion_force`).
        Значит губка — прямоугольник 0.8×(3−1.2) плюс треугольник 0.8×1.2/2.
        """
        out = self.run(latch, part="корпус", plane="xz", at=20, contours=True)
        lip = 0.8 * (3 - 1.2) + 0.8 * 1.2 / 2
        assert out["area_mm2"] == pytest.approx(60 * 20 - 56 * 18 + 2 * lip, abs=0.02)
        assert out["pieces"] == 1 and out["holes"] == 0

    def test_holes_are_reported_separately(self):
        """Сквозное отверстие обязано приехать отдельным кольцом, а не швом в
        наружном: иначе крышка среза закрыла бы дыру."""
        from datum.geom.measure import section_xy
        from datum.geom.prims import box, cyl_z

        solid = box(0, 20, 0, 20, 0, 10) - cyl_z(6, -1, 11, x=10, y=10)
        poly = section_xy(solid, 5)
        assert len(poly.interiors) == 1
        assert poly.area == pytest.approx(400 - math.pi * 9, rel=0.01)

    def test_rings_are_not_closed_twice(self, latch):
        """Последняя точка кольца у shapely совпадает с первой. Отдавать её
        значило бы заставить окно рисовать нулевой отрезок."""
        out = self.run(latch, part="корпус", plane="xz", at=20, contours=True)
        for piece in out["contours"]:
            assert piece["shell"][0] != piece["shell"][-1]

    def test_an_empty_section_is_not_an_error(self, latch):
        """Плоскость увели за деталь. Это обычное дело при перетаскивании."""
        out = self.run(latch, part="корпус", plane="xy", at=500, contours=True)
        assert out["empty"] is True and "contours" not in out
