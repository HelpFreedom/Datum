"""Контур предмета в плане: тень, выравнивание, раскладка по сетке."""

from __future__ import annotations

import json
import math

import pytest

from datum.cli import main as cli
from datum.geom import box, cyl_z, diff, move, rotate, union
from datum.geom.footprint import align_long_axis, fit_extents, footprint, min_square_angle
from datum.geom.measure import bbox


def test_the_shadow_keeps_a_ledge_that_slices_would_miss():
    """Выступ толщиной 0.2 мм на высоте 10.2 — между сечениями через миллиметр
    он пропал бы, и карман вышел бы теснее предмета. Тень его видит."""
    thing = union([box(0, 10, 0, 10, 0, 20), box(-5, 15, 4, 6, 10.2, 10.4)])
    assert footprint(thing, 0, 19).bounds == pytest.approx((-5, 0, 15, 10))
    assert footprint(thing, 0, 10).bounds == pytest.approx((0, 0, 10, 10))


@pytest.mark.parametrize("turn", [0, 30, -60, 89, 90, 135])
def test_align_puts_the_long_side_along_x(turn):
    bar = rotate(move(box(0, 100, 0, 40, 0, 30), dx=5, dy=-3), rz=turn)
    aligned, angle = align_long_axis(bar)
    lo, hi = bbox(aligned)
    assert (hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx((100, 40), abs=1e-6)
    assert lo[2] == pytest.approx(0) and (lo[0] + hi[0]) == pytest.approx(0, abs=1e-6)
    assert -90 <= angle < 90


def test_align_is_not_pulled_by_a_bump():
    """Главная ось площади ушла бы к выступу; описанный прямоугольник — нет.
    Так было на скане телефона: камера сдвигала ось на 1.7°, и карман рос."""
    phone = union([box(0, 160, 0, 75, 0, 8), box(120, 150, 45, 70, 8, 10)])
    aligned, angle = align_long_axis(rotate(phone, rz=12))
    lo, hi = bbox(aligned)
    assert angle == pytest.approx(-12, abs=1e-6)
    assert (hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx((160, 75), abs=1e-6)


def test_holes_are_filled_unless_asked():
    frame = diff(box(0, 40, 0, 30, 0, 5), [box(10, 30, 10, 20, -1, 6)])
    assert footprint(frame).area == pytest.approx(1200)
    assert footprint(frame, keep_holes=True).area == pytest.approx(1000)


def test_clearance_grows_each_side():
    shape = footprint(box(0, 50, 0, 20, 0, 5), clearance=0.3)
    x0, y0, x1, y1 = shape.bounds
    assert (x1 - x0, y1 - y0) == pytest.approx((50.6, 20.6), abs=1e-6)


def test_min_square_turns_a_long_thing_diagonal():
    """Брусок 100×40: прямо ему нужен квадрат 100, по диагонали — 140/√2."""
    angle, side = min_square_angle(footprint(box(0, 100, 0, 40, 0, 1)))
    assert angle == pytest.approx(45.0, abs=0.1)
    assert side == pytest.approx(140 / math.sqrt(2), rel=1e-4)
    # Квадрату поворачиваться незачем.
    assert min_square_angle(footprint(box(0, 30, 0, 30, 0, 1)))[1] == pytest.approx(30)


def test_fit_extents_reports_its_factors():
    solid, k = fit_extents(box(0, 10, 0, 20, 0, 5), (10.5, 19.0, 5.0))
    assert k == pytest.approx((1.05, 0.95, 1.0))
    lo, hi = bbox(solid)
    assert (hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx((10.5, 19.0))


def _project_with_scan(tmp_path):
    """Проект с «отсканированной» чужой моделью и деталью-ложементом по ней."""
    import trimesh

    from datum.geom.convert import to_trimesh

    root = tmp_path / "ложемент"
    assert cli(["init", str(root)]) == 0
    scan = tmp_path / "scan.stl"
    thing = rotate(union([box(0, 80, 0, 30, 0, 12), cyl_z(10, 12, 16, x=60, y=15)]), rz=20)
    to_trimesh(thing.manifold).export(scan)
    assert trimesh.load(scan).is_watertight
    assert cli(["import", str(root), str(scan), "--name", "thing"]) == 0
    (root / "parts" / "cradle.py").write_text('''\
from datum.build import imported, part
from datum.geom import *


@part("ложемент", print_up="+Z")
def build():
    contour = footprint(align_long_axis(imported("thing"))[0], z1=6.0, clearance=0.3)
    return diff(box(-50, 50, -25, 25, 0, 8), [extrude(contour, 3, 9)])
''', encoding="utf-8")
    with (root / "checks" / "checks.yaml").open("a", encoding="utf-8") as f:
        f.write('\n- id: ложемент.целостность\n  use: single_body\n  args: {part: "@ложемент"}\n')
    return root


def test_a_part_cut_from_a_scan_passes_its_checks(tmp_path, capsys):
    """Деталь по чужой модели обязана проходить и проверки, а не только сборку.

    Путь проверок собирал детали без контекста проекта, и `imported("…")` в
    скрипте падал там, где `build` проходил."""
    root = _project_with_scan(tmp_path)
    capsys.readouterr()
    assert cli(["check", str(root)]) == 0, capsys.readouterr()
    assert "ложемент: тел 1" in capsys.readouterr().out


def test_the_footprint_op_hands_over_a_working_snippet(tmp_path, capsys):
    root = _project_with_scan(tmp_path)
    capsys.readouterr()
    assert cli(["footprint", str(root), "--model", "thing", "--align", "--z-to", "6",
                "--clearance", "0.3", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    # Скан прошёл через STL, а там координаты во float32 — отсюда 1e-6°.
    assert d["aligned_deg"] == pytest.approx(-20, abs=1e-4)
    assert d["size_mm"] == pytest.approx([80.6, 30.6], abs=1e-3)
    assert 'footprint(align_long_axis(imported("thing"))[0], z1=6.0, clearance=0.3' in d["snippet"]
    assert (root / d["image"]["path"]).read_bytes()[:4] == b"\x89PNG"
    # Одно из двух: деталь или модель.
    assert cli(["footprint", str(root)]) == 3
