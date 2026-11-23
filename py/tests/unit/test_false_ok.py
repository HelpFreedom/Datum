"""Ложные «OK», найденные аудитом 2026-11-21: каждый воспроизведён и закрыт.

Общее у всех: проверка отвечала «прошло» там, где ответа у неё не было —
препятствие оказывалось между позами, знак формулы переворачивался, деталь
вообще не касалась того, что проверялось.
"""

from __future__ import annotations

import asyncio

import pytest

from datum.checks import machine
from datum.checks.assembly import snap_insertion
from datum.checks.contact import insertion_force
from datum.checks.fit import obstruction_thickness
from datum.checks.motion import travel_sweep
from datum.checks.printing import min_wall
from datum.checks.result import CheckResult, CheckRun
from datum.checks.sim import STRESS_MISMATCH_WARN, sim_stress_verdict
from datum.geom import box, diff, move, rotate, union


def _one(result):
    return result[0] if isinstance(result, list) else result


# ---------------------------------------------------------------- min_wall
def test_min_wall_sees_a_thin_wall_next_to_a_bigger_edge():
    """Крупнейший тонкий кусок сечения — наклонный лист — признавался кромкой,
    и сечение пропускалось целиком вместе со стенкой 0.3 мм рядом."""
    plate = box(0, 60, 0, 60, 0, 3)
    wall = box(10, 50, 10, 10.3, 3, 9)
    sheet = move(rotate(box(0, 40, 0, 0.3, 0, 8), rx=30), dx=10, dy=40, dz=2.5)
    assert _one(min_wall(union([plate, wall]), wall=0.8)).status == "fail"
    assert _one(min_wall(union([plate, wall, sheet]), wall=0.8)).status == "fail"
    # Наклонная тонкая стенка — тоже стенка, а не «кромка».
    assert _one(min_wall(union([plate, sheet]), wall=0.8)).status == "fail"
    assert _one(min_wall(plate, wall=0.8)).status == "pass"


# ----------------------------------------------------------- insertion_force
_SPRING = dict(direction="-Z", distance=35, modulus=2400, part_name="пробка",
               spring_length=10, spring_width=6, spring_thickness=1, poisson=0.35,
               friction=0.3)


@pytest.mark.parametrize("ridge_h", [0.2, 0.4, 3.0])
def test_insertion_force_does_not_miss_a_short_ridge(ridge_h):
    """Семь сечений на 30 мм перекрытия стояли через 5 мм: гребень 0.2 мм с
    натягом 0.2 оказывался между ними — «проходит свободно, усилия не
    требуется» при известном пересечении."""
    socket = diff(box(-10, 10, -10, 10, 0, 32), [box(-5, 5, -5, 5, 2, 33)])
    plug = union([box(-4.9, 4.9, -4.9, 4.9, 2, 32),
                  box(4.9, 5.1, -3, 3, 16.9, 16.9 + ridge_h)])
    res = _one(insertion_force(plug, [socket], **_SPRING))
    assert res.status != "pass" and "отжать 0.100 мм" in res.message, res.message
    free = _one(insertion_force(box(-4.7, 4.7, -4.7, 4.7, 2, 32), [socket], **_SPRING))
    assert free.status == "pass" and "проходит свободно" in free.message


# ----------------------------------------------------------------- travel
@pytest.mark.parametrize("turns_deg", [360, 720, 1080, 2880])
def test_travel_sweep_is_not_fooled_by_whole_turns(turns_deg):
    """Хорда поворота на целое число оборотов — ноль: ход 0…2880° с начальной
    сеткой через 360° выглядел неподвижным и давал «максимум 0.000 мм³»."""
    lever = box(20, 60, -2, 2, 0, 4)
    stop = rotate(box(40, 50, -2, 2, 0, 4), rz=135)
    res = travel_sweep(lambda u: rotate(lever, rz=u), [stop], u_range=(0, turns_deg))
    assert res.status == "fail" and res.value > 100, res.message


# ------------------------------------------------------------------- винт
def test_screw_that_jams_is_not_enough_torque():
    """ψ+φ' ≥ 90°: тангенс отрицателен, «нужно −1.78 Н·м, есть 0.01 — хватает»."""
    out = machine.screw_drive(d=6, d2=5, pitch=20, starts=4, force=100, friction=0.3,
                              max_torque=10)
    assert [r.status for r in out] == ["fail"]
    assert "ЗАКЛИНИВАНИЕ" in out[0].message and "хватает" not in out[0].message
    # Обычный винт считается как прежде.
    usual = machine.screw_drive(d=8, pitch=1.25, force=100, friction=0.15, max_torque=500)
    assert all(r.status == "pass" for r in usual) and "КПД" in usual[0].message


# ---------------------------------------------------------------- защёлка
def test_snap_that_presses_nothing_is_not_a_snap():
    body = diff(box(0, 40, 0, 40, 0, 20), [box(2, 38, 2, 38, 2, 21)])
    lid = box(5, 35, 5, 35, 14, 17)                 # проходит, ничего не задевая
    res = _one(snap_insertion(lid, [body], direction="-Z", max_press_vol=50,
                              part_name="крышка"))
    assert res.status == "fail" and "НЕ ЗАЩЁЛКА" in res.message


# ----------------------------------------------------------------- пропуск
def test_skips_are_named_next_to_the_verdict():
    run = CheckRun((CheckResult(id="a", title="a", group="g", status="pass", message="a"),
                    CheckResult(id="b", title="b", group="g", status="skip", message="b")))
    assert run.as_dict()["n_skip"] == 1
    assert "пропущено без замера (1): b" in run.report()
    clean = CheckRun((CheckResult(id="a", title="a", group="g", status="pass", message="a"),))
    assert "пропущено" not in clean.report()


def test_obstruction_thickness_that_measured_nothing_is_an_error():
    part = box(0, 10, 0, 10, 0, 10)
    holder = box(20, 30, 0, 10, 0, 10)
    res = _one(obstruction_thickness(part, holder, direction=1, plane="xy", at=[500.0],
                                     band=(0.0, 10.0), min_thickness=1.0))
    assert res.status == "error" and "нечего мерить" in res.message


# ------------------------------------------------------ самопроверки МКЭ звена
class _Rep:
    link, pieces, t, value_mpa, basis = "плечо", ("плечо",), 0.5, 2.0, "больший пик двух сеток"
    peak_at, max_displacement_mm, notes = (1.0, 2.0, 3.0), 0.1, ()
    imbalance = (1e-9, 1e-9)
    mujoco_mismatch = 0.0


def _rep(**kw):
    rep = _Rep()
    for k, v in kw.items():
        setattr(rep, k, v)
    return rep


def test_stress_verdict_judges_its_own_self_checks():
    fine = sim_stress_verdict(_rep(), who="рука", strength_z=20.0)
    assert fine.status == "pass"
    lost = sim_stress_verdict(_rep(imbalance=(0.4, 1e-9)), who="рука", strength_z=20.0)
    assert lost.status == "error" and "ВЕРИТЬ НЕЛЬЗЯ" in lost.message
    off = sim_stress_verdict(_rep(mujoco_mismatch=STRESS_MISMATCH_WARN * 2), who="рука",
                             strength_z=20.0)
    assert off.status == "warn" and "расходится с динамикой MuJoCo на 20 %" in off.message
    small = sim_stress_verdict(_rep(mujoco_mismatch=0.03), who="рука", strength_z=20.0)
    assert small.status == "pass" and "расхождение нагрузки 3.0 %" in small.message
    # Не выдержит — красное, с какой бы оговоркой ни считалось.
    weak = sim_stress_verdict(_rep(mujoco_mismatch=0.5), who="рука", strength_z=1.0)
    assert weak.status == "fail"


# ------------------------------------------------------------ код возврата
def test_fem_verify_failure_is_a_failing_exit_code():
    import datum.tools.ops  # noqa: F401 — объявление операций
    from datum.tools.registry import OPS

    code = OPS["fem_verify"].exit_code
    assert code is not None
    assert code({"ok": False, "report": "…"}) == 1
    assert code({"error": "x"}) == 1
    assert code({"ok": True}) == 0
    assert asyncio.iscoroutinefunction(OPS["fem_verify"].fn)


# --------------------------------------------------- скругление съедает выступ
def test_corner_rounding_refuses_to_eat_a_rib_or_fill_a_slot():
    """Рёбрышко 10×1 при r = 1 исчезало из контура молча (потеряно 0.4 %
    площади, кусков столько же), хотя докстринг обещал отказ."""
    from shapely.geometry import Polygon
    from shapely.geometry import box as rect
    from shapely.ops import unary_union

    from datum.geom.features import chamfer_corners, round_corners

    plate = rect(0, 0, 50, 50)
    rib = unary_union([plate, rect(50, 24.5, 60, 25.5)])
    slot = plate.difference(rect(40, 24.5, 50, 25.5))
    with pytest.raises(ValueError, match="съеден выступ"):
        round_corners(rib, 1.0)
    with pytest.raises(ValueError, match="залит паз"):
        round_corners(slot, 1.0)
    with pytest.raises(ValueError, match="съеден выступ"):
        chamfer_corners(rib, 1.0)
    # Законное остаётся законным: радиус меньше полуширины ребра, острый угол,
    # уже скруглённый профиль большим радиусом, обычная плита.
    assert round_corners(rib, 0.3).area > plate.area + 9.5
    assert round_corners(Polygon([(0, 0), (60, 0), (0, 12)]), 2.0).area > 300
    assert round_corners(round_corners(plate, 2.0), 5.0).area < plate.area
    assert chamfer_corners(plate, 3.0).area == pytest.approx(2500 - 4 * 4.5)


# ------------------------------------------------- сверка выгрузки без trimesh
def test_closed_mesh_test_matches_trimesh():
    import numpy as np
    import trimesh

    from datum.geom.convert import vertices_faces
    from datum.io.export import _closed

    v, f = vertices_faces(diff(box(0, 20, 0, 20, 0, 10), [box(5, 15, 5, 15, -1, 11)]).manifold)
    assert _closed(f) and trimesh.Trimesh(v, f, process=False).is_watertight
    assert not _closed(f[:-1]), "без одной грани — дыра"
    flipped = f.copy()
    flipped[0] = flipped[0][::-1]
    assert not _closed(flipped), "вывернутая грань — не ориентирована"
    assert not _closed(np.zeros((0, 3), int))


# ------------------------------------------------------------ висит в воздухе
def test_a_body_hanging_in_the_air_is_found():
    """«Вон камень в воздухе висит, я на него смотрю» — нашёл человек, а не
    проверка: тело показной сборки ни на что не опиралось."""
    from datum.checks.topology import no_floating_body
    from datum.geom import compose, cyl_z

    base = box(0, 40, 0, 40, 0, 5)
    assert no_floating_body(base, name="плита").status == "pass"
    stacked = compose([base, cyl_z(8, 5.1, 30, 20, 20), box(14, 26, 14, 26, 30.2, 34)])
    assert no_floating_body(stacked, name="сборка").status == "pass", "зазор посадки — опора"
    hanging = compose([base, box(10, 20, 10, 20, 5.2, 15), box(25, 30, 25, 30, 20, 26)])
    res = no_floating_body(hanging, name="сборка")
    assert res.status == "fail" and res.value == 1
    assert "ВИСИТ В ВОЗДУХЕ тел: 1 из 3" in res.message and "z = 20.00" in res.message
    # Зазор — аргумент: что считать касанием, решает тот, кто объявляет.
    assert no_floating_body(hanging, name="сборка", gap=9.0).status == "pass"


def test_floating_bodies_are_a_baseline_warning_for_declared_multi_body_parts(cube):
    from datum.doc.project import Project

    (cube / "parts" / "show.py").write_text(
        "from datum.build import part\n"
        "from datum.geom import Expect, box, compose\n\n\n"
        "@part('витрина', printed=False, expect=Expect(bodies=None))\n"
        "def show():\n"
        "    return compose([box(100, 140, 100, 140, 0, 5), box(110, 120, 110, 120, 30, 40)])\n",
        encoding="utf-8")
    pr = Project.open(cube)
    ids = {d["id"]: d for d in pr.baseline_decls()}
    assert "витрина.без_висящего" in ids and ids["витрина.без_висящего"]["severity"] == "warn"
    assert "кубик.без_висящего" not in ids, "детали из одного тела проверка не нужна"
    run = pr.run_checks(only=["витрина.без_висящего"])
    assert [r.status for r in run.results] == ["warn"] and "ВИСИТ" in run.results[0].message


# ------------------------------------------------------- прочность между слоями
def test_section_modulus_matches_the_textbook():
    import math

    from shapely import affinity
    from shapely.geometry import Point
    from shapely.geometry import box as rect

    from datum.checks.layers import section_bending

    for angle in (0, 37, 90, 123):
        area, w = section_bending(affinity.rotate(rect(0, 0, 30, 4), angle))
        assert area == pytest.approx(120) and w == pytest.approx(30 * 4 * 4 / 6)
    _, w = section_bending(rect(0, 0, 30, 30).difference(rect(5, 5, 25, 25)))
    assert w == pytest.approx((30 ** 4 - 20 ** 4) / 12 / 15)
    _, w = section_bending(Point(3, 7).buffer(5, quad_segs=64))
    assert w == pytest.approx(math.pi * 10 ** 3 / 32, rel=1e-3)


def test_layer_strength_finds_the_layer_eaten_by_a_nut_pocket():
    """Ядро Gear Cube ломалось в руках там, где гнездо под гайку съедает
    сечение ноги; печатность при этом была в порядке."""
    import math

    from datum.checks.layers import layer_strength
    from datum.geom import cyl_z

    post = union([box(-20, 20, -20, 20, 0, 4), cyl_z(10, 4, 54)])
    whole = layer_strength(post, force=20, strength_z=30, name="столб")
    assert whole.status == "pass"
    # У корня столба меньший кусок — плита: плечо до её угла, √(20² + 20² + h²).
    # Чем выше слой, тем длиннее меньший кусок-плита, но не длиннее самого
    # столба: плечо между 28 мм (угол плиты) и половиной высоты.
    w = math.pi * 1000 / 32
    assert 20 * 28 / w < whole.value < 20 * 40 / w
    pocket = diff(post, [box(-6, 6, -2.5, 2.5, 20, 24)])
    weak = layer_strength(pocket, force=20, strength_z=30, name="столб")
    assert weak.status == "fail" and "СЛОМАЕТСЯ ПО СЛОЮ" in weak.message
    import re

    at = float(re.search(r"на высоте ([\d.]+) мм над столом", weak.message).group(1))
    assert 20.0 < at < 24.0, "слабейший слой — в гнезде"
    assert weak.value > 5 * whole.value
    # Сужение у самого стола — не шейка: меньший кусок там в миллиметр, и
    # плеча у него нет (первая версия показывала на первый слой ядра).
    tip = union([cyl_z(3, 0, 0.6), cyl_z(30, 0.6, 40)])
    low = layer_strength(tip, force=20, strength_z=30, name="с ножкой")
    assert low.status == "pass" and "на высоте 0." not in low.message
    with pytest.raises(ValueError, match="force"):
        layer_strength(post, force=0, strength_z=30)


def test_layer_strength_takes_the_pose_and_the_material_from_the_project(cube):
    from datum.doc.project import Project

    checks = cube / "checks" / "checks.yaml"
    checks.write_text(checks.read_text("utf-8")
                      + '\n- id: кубик.в_руках\n  use: layer_strength\n'
                        '  args: {part: "@кубик", force: 30.0}\n', encoding="utf-8")
    run = Project.open(cube).run_checks(only=["кубик.в_руках"])
    (res,) = run.results
    assert res.status == "pass" and "MAT_TENSILE_Z" in res.depends_on
    assert "вверх +Z" in res.message
