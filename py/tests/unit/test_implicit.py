"""Органика: неявные поверхности и гладкие сопряжения — претензия №8.

Булевы операции над мешами дают форму, но не дают ПЕРЕХОДА. Здесь форма
задаётся функцией расстояния, и сопряжение становится арифметикой над
функциями, а не операцией над гранями.

Все проверки ниже — против ТОЧНО ИЗВЕСТНЫХ чисел: объём шара, объём, снятый
скруглением ребра, расстояние до граней коробки. Иначе «выглядит гладко» так и
осталось бы единственным доводом.
"""

from __future__ import annotations

import math

import pytest

from datum.geom import checkpoint, man, section_xy, vol
from datum.geom.implicit import (MeshQuality, blend_intersect, blend_union,
                                 box_sdf, cyl_z_sdf, diff_sdf, intersect_sdf,
                                 invert_sdf, mesh_sdf, offset_sdf, plane_sdf,
                                 shell_sdf, sphere_sdf, union_sdf)


# ------------------------------------------------------------------ примитивы
def test_the_box_field_is_a_true_distance():
    """Пересечение шести полупространств дало бы верный уровень нуля и НЕВЕРНОЕ
    расстояние у рёбер — а именно оно и нужно сопряжениям."""
    f = box_sdf(0, 10, 0, 10, 0, 10)
    assert f(5, 5, 5) == pytest.approx(5.0)          # центр
    assert f(5, 5, 12) == pytest.approx(-2.0)        # над гранью
    # Снаружи угла расстояние ЕВКЛИДОВО, а не по ближайшей грани.
    assert f(13, 14, 5) == pytest.approx(-5.0)       # √(3² + 4²)
    assert f(-3, -4, 5) == pytest.approx(-5.0)


def test_the_sphere_volume_is_right():
    solid = mesh_sdf(sphere_sdf(24.0), (-14, -14, -14, 14, 14, 14), 0.5)
    assert vol(solid) == pytest.approx(4 / 3 * math.pi * 12 ** 3, rel=2e-3)


def test_the_cylinder_section_is_a_circle():
    solid = mesh_sdf(cyl_z_sdf(20.0, 0.0, 30.0), (-12, -12, -2, 12, 12, 32), 0.4)
    assert section_xy(solid, 15.0).area == pytest.approx(math.pi * 100, rel=2e-3)


def test_a_nonsensical_primitive_is_refused():
    for bad in (lambda: sphere_sdf(0.0), lambda: cyl_z_sdf(10, 5, 5),
                lambda: box_sdf(0, 0, 0, 1, 0, 1), lambda: plane_sdf((0, 0, 0), 1)):
        with pytest.raises(ValueError):
            bad()


# ------------------------------------------------------------------ сопряжение
def test_the_blend_radius_means_exactly_what_it_says():
    """Скругление вертикального ребра бруска радиусом r убирает ровно
    r²·(1 − π/4)·h материала. Число известно заранее, и проверяется именно оно,
    а не «на вид гладко»."""
    r, side, height, edge = 3.0, 20.0, 20.0, 0.25
    bounds = (-1, -1, -1, side + 1, side + 1, height + 1)
    walls = intersect_sdf(plane_sdf((1, 0, 0), side), plane_sdf((0, 1, 0), side),
                          plane_sdf((0, 0, -1), 0.0), plane_sdf((0, 0, 1), height))
    sharp = mesh_sdf(intersect_sdf(plane_sdf((-1, 0, 0), 0.0),
                                   plane_sdf((0, -1, 0), 0.0), walls), bounds, edge)
    rounded = mesh_sdf(intersect_sdf(
        blend_intersect(plane_sdf((-1, 0, 0), 0.0), plane_sdf((0, -1, 0), 0.0), r),
        walls), bounds, edge)
    cut = vol(sharp) - vol(rounded)
    assert cut == pytest.approx(r * r * (1 - math.pi / 4) * height, rel=0.01)


def test_a_zero_radius_blend_is_a_plain_boolean():
    a, b = sphere_sdf(20.0), sphere_sdf(20.0, x=12.0)
    for x, y, z in ((0, 0, 0), (6, 0, 0), (12, 3, 4), (30, 0, 0)):
        assert blend_union(a, b, 0.0)(x, y, z) == pytest.approx(union_sdf(a, b)(x, y, z))
        assert (blend_intersect(a, b, 0.0)(x, y, z)
                == pytest.approx(intersect_sdf(a, b)(x, y, z)))


def test_the_blend_adds_material_and_the_plain_union_does_not():
    """Выпуклое сопряжение ДОБАВЛЯЕТ материал в пазуху стыка — этого меш-CSG
    не даёт вовсе."""
    a, b = cyl_z_sdf(20.0, 0.0, 40.0), sphere_sdf(30.0, z=40.0)
    bounds = (-20, -20, -2, 20, 20, 58)
    plain = mesh_sdf(union_sdf(a, b), bounds, 0.5)
    blended = mesh_sdf(blend_union(a, b, 6.0), bounds, 0.5)
    assert vol(blended) > vol(plain)


@pytest.mark.parametrize("radius", [-0.1, -5.0])
def test_a_negative_radius_is_refused(radius):
    a, b = sphere_sdf(10.0), sphere_sdf(10.0, x=5.0)
    with pytest.raises(ValueError, match="радиус"):
        blend_union(a, b, radius)
    with pytest.raises(ValueError, match="радиус"):
        blend_intersect(a, b, radius)


# ------------------------------------------------------------------ сочетания
def test_offset_grows_and_shrinks_by_the_asked_amount():
    """Ради этого и нужна ТОЧНАЯ функция расстояния: по оценке снизу сдвиг
    исказил бы форму."""
    for delta in (2.0, -3.0):
        solid = mesh_sdf(offset_sdf(sphere_sdf(24.0), delta),
                         (-20, -20, -20, 20, 20, 20), 0.4)
        assert vol(solid) == pytest.approx(4 / 3 * math.pi * (12 + delta) ** 3, rel=3e-3)


def test_the_shell_has_the_asked_thickness():
    thickness = 2.0
    solid = mesh_sdf(shell_sdf(sphere_sdf(24.0), thickness),
                     (-16, -16, -16, 16, 16, 16), 0.25)
    outer, inner = 12 + thickness / 2, 12 - thickness / 2
    want = 4 / 3 * math.pi * (outer ** 3 - inner ** 3)
    assert vol(solid) == pytest.approx(want, rel=0.02)


def test_difference_removes_material():
    hole = cyl_z_sdf(8.0, -20.0, 20.0)
    solid = mesh_sdf(diff_sdf(box_sdf(-10, 10, -10, 10, -5, 5), hole),
                     (-12, -12, -7, 12, 12, 7), 0.25)
    assert vol(solid) == pytest.approx(20 * 20 * 10 - math.pi * 16 * 10, rel=0.01)


def test_invert_swaps_inside_and_outside():
    f = sphere_sdf(10.0)
    assert f(0, 0, 0) > 0 > invert_sdf(f)(0, 0, 0)


def test_empty_combinations_are_refused():
    for bad in (union_sdf, intersect_sdf):
        with pytest.raises(ValueError):
            bad()


# ------------------------------------------------------------------ качество
def test_the_quality_is_measured_not_promised():
    solid, quality = mesh_sdf(sphere_sdf(24.0), (-14, -14, -14, 14, 14, 14), 0.5,
                              quality=True)
    assert isinstance(quality, MeshQuality)
    # Вдвое более грубый меш обязан отличаться — иначе мера ничего не мерит.
    assert 0 < quality.change < 0.05
    assert "сдвиг" in quality.line()


@pytest.mark.parametrize("bad", [
    dict(bounds=(0, 0, 0, 1, 1)),                 # не шесть чисел
    dict(edge=0.0),
    dict(bounds=(0, 0, 0, 0, 1, 1)),              # границы не возрастают
])
def test_nonsensical_mesh_arguments_are_refused(bad):
    args = dict(bounds=(-1, -1, -1, 1, 1, 1), edge=0.5)
    args.update(bad)
    with pytest.raises(ValueError):
        mesh_sdf(sphere_sdf(1.0), args["bounds"], args["edge"])


# ------------------------------------------------------- в общем конвейере
def test_an_organic_body_goes_through_the_rest_of_datum():
    """Органика обязана жить наравне со своими: страж, проверки, сечения,
    выгрузка. Иначе это отдельный мирок, а не часть инструмента."""
    from datum.checks import printing, topology
    from datum.io.glb import solid_to_glb

    body = mesh_sdf(blend_union(cyl_z_sdf(20.0, 0.0, 40.0), sphere_sdf(30.0, z=40.0), 6.0),
                    (-20, -20, -2, 20, 20, 58), 0.6, name="рукоять")
    checkpoint(body)                                  # страж не возражает
    assert man(body).genus() == 0
    assert topology.single_body(body, name="рукоять").status == "pass"
    assert topology.no_sealed_void(body, name="рукоять").status == "pass"
    assert printing.bbox_fits(body, bed=(256, 256, 256), name="рукоять").status == "pass"
    # Сечение ниже сопряжения — ровно цилиндр.
    assert section_xy(body, 10.0).area == pytest.approx(math.pi * 100, rel=5e-3)
    assert len(solid_to_glb(body)) > 1000


def test_an_organic_part_builds_inside_a_project(tmp_path):
    """Перила песочницы не должны мешать органике.

    Скрипт детали исполняется в отдельном процессе с ограниченным импортом, а
    `mesh_sdf` зовёт ядро и часы. Проверяется именно этот путь: в библиотеке
    всё могло работать, а в детали — отказать.
    """
    from datum.doc.project import Project

    root = tmp_path / "органика"
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "checks").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "СТЕРЖЕНЬ: {value: 20, unit: mm, provenance: chosen, reason: 'диаметр'}\n"
        "ГОЛОВКА:  {value: 30, unit: mm, provenance: chosen, reason: 'диаметр'}\n"
        "СОПРЯЖЕНИЕ: {value: 6, unit: mm, provenance: chosen, reason: 'радиус'}\n",
        encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\n"
        "from datum.geom import blend_union, cyl_z_sdf, sphere_sdf, mesh_sdf\n\n"
        "@part('рукоять')\n"
        "def build():\n"
        "    return mesh_sdf(\n"
        "        blend_union(cyl_z_sdf(P.СТЕРЖЕНЬ, 0, 40),\n"
        "                    sphere_sdf(P.ГОЛОВКА, z=40), P.СОПРЯЖЕНИЕ),\n"
        "        (-20, -20, -2, 20, 20, 58), 1.0)\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(
        '- id: рукоять.целостность\n  use: single_body\n  args: {part: "@рукоять"}\n',
        encoding="utf-8")

    project = Project.open(root)
    built = project.build()["рукоять"]
    assert built.ok, built.line()
    assert built.topology.bodies == 1
    # Зависимости собрались сами: все три параметра прочитаны при сборке.
    assert {"СТЕРЖЕНЬ", "ГОЛОВКА", "СОПРЯЖЕНИЕ"} <= set(built.depends_on)
    assert project.run_checks().results[0].status == "pass"


# ------------------------------------------------ тело тоньше шага
def test_a_body_thinner_than_the_step_is_refused_not_returned_broken():
    """Оболочка 0.3 мм при шаге 1 мм рассыпалась на 68 кусков с объёмом 72
    вместо 543 — и молча уходила в сборку."""
    with pytest.raises(ValueError, match="рассыпалось"):
        mesh_sdf(shell_sdf(sphere_sdf(24.0), 0.3), (-24, -24, -24, 24, 24, 24), 1.0)


def test_a_thick_enough_shell_is_one_body():
    s = mesh_sdf(shell_sdf(sphere_sdf(24.0), 2.0), (-24, -24, -24, 24, 24, 24), 1.0)
    assert s.manifold.volume() > 3000


def test_several_bodies_on_purpose_are_allowed_when_said_so():
    two = union_sdf(sphere_sdf(6.0, x=-10), sphere_sdf(6.0, x=10))
    with pytest.raises(ValueError, match="рассыпалось"):
        mesh_sdf(two, (-20, -8, -8, 20, 8, 8), 0.5)
    s = mesh_sdf(two, (-20, -8, -8, 20, 8, 8), 0.5, allow_split=True)
    assert len(s.manifold.decompose()) == 2


def test_an_empty_level_set_is_refused():
    with pytest.raises(ValueError, match="пустое"):
        mesh_sdf(sphere_sdf(4.0, x=100), (-5, -5, -5, 5, 5, 5), 0.5)
