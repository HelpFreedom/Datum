"""Положение деталей (placement.yaml) и корзина проекта.

Перетаскивание в окне — правка проекта: деталь пересобирается на новом месте,
и проверки обязаны видеть её там же, где экран. Удаление — в корзину, откуда
деталь возвращается вместе с положением.
"""

from __future__ import annotations

import asyncio

import pytest

from datum.doc import placement as P
from datum.doc.project import Project, ProjectError
from datum.geom import box
from datum.geom.measure import bbox, vol
from datum.tools.ops import (clear_project_cache, part_delete, part_place,
                             part_restore)


def run(op, **kwargs):
    clear_project_cache()          # как в изолированном процессе: проект открывается заново
    return asyncio.run(op(**kwargs))


# ---------------------------------------------------------------- геометрия
def test_rotation_is_about_the_centre_then_the_move():
    solid = box(0, 20, 0, 10, 0, 4)
    placed = P.apply(solid, P.Placement(move=(5, 0, 0), rotate=(0, 0, 90)))
    (x0, y0, z0), (x1, y1, z1) = bbox(placed)
    # Поворот вокруг центра (10, 5, 2): 20×10 становится 10×20, центр на месте,
    # потом сдвиг на +5 по X.
    assert (x1 - x0, y1 - y0) == pytest.approx((10, 20), abs=1e-4)
    assert ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) == pytest.approx((15, 5, 2), abs=1e-4)
    assert vol(placed) == pytest.approx(vol(solid))      # жёсткое движение


def test_angles_go_x_then_y_then_z():
    """Порядок — как у Manifold.rotate и как считает окно (Euler «ZYX»)."""
    rod = box(0, 10, -0.5, 0.5, -0.5, 0.5)                # вдоль +X, центр (5,0,0)
    placed = P.apply(rod, P.Placement(rotate=(90, 90, 0)))
    (x0, y0, z0), (x1, y1, z1) = bbox(placed)
    # X на 90° стержень вдоль X не меняет; Y на 90° кладёт его вдоль −Z.
    assert (x1 - x0, y1 - y0, z1 - z0) == pytest.approx((1, 1, 10), abs=1e-4)


def test_saved_numbers_are_clean(tmp_path):
    P.save(tmp_path, {"а": P.Placement(move=(1.00000001, -0.0, 2.5), rotate=(0, 0, 45.00049))})
    text = (tmp_path / P.FILE).read_text(encoding="utf-8")
    # Три знака у углов: 45.00049 — это ровно 45, и в файл так и пишется.
    assert "move: [1, 0, 2.5]" in text and "rotate: [0, 0, 45]" in text
    assert P.load(tmp_path)["а"].move == (1.0, 0.0, 2.5)
    P.save(tmp_path, {"а": P.Placement()})                 # на месте — файла нет
    assert not (tmp_path / P.FILE).exists()


# ------------------------------------------------------------------- проект
def test_the_project_builds_and_checks_the_part_where_it_stands(cube):
    (cube / P.FILE).write_text("кубик: {move: [0, 0, 300]}\n", encoding="utf-8")
    project = Project.open(cube)
    lo, hi = bbox(project.build(["кубик"])["кубик"].solid)
    assert lo[2] == pytest.approx(300)
    assert project.placement_of("кубик")["centre"] == pytest.approx([128, 128, 10])
    # Проверки берут деталь своим путём (`_solid`), не через build — и
    # положение обязано быть там тоже, иначе проверено одно, а на экране другое.
    checked = project._solid("кубик", project.params.proxy())
    assert bbox(checked)[0][2] == pytest.approx(300)


def test_moving_does_not_poison_the_cache(cube):
    """В кеше — деталь, какой её вернул скрипт; положение накладывается сверху."""
    first = bbox(Project.open(cube).build(["кубик"])["кубик"].solid)
    (cube / P.FILE).write_text("кубик: {move: [7, 0, 0]}\n", encoding="utf-8")
    moved = Project.open(cube).build(["кубик"])["кубик"]
    assert moved.cached
    assert bbox(moved.solid)[0][0] == pytest.approx(first[0][0] + 7)
    (cube / P.FILE).unlink()
    back = Project.open(cube).build(["кубик"])["кубик"]
    assert bbox(back.solid)[0][0] == pytest.approx(first[0][0])


@pytest.mark.parametrize("text, hint", [
    ("нет_такой: {move: [1, 2, 3]}\n", "нет таких деталей"),
    ("кубик: {move: [1, 2]}\n", "три конечных числа"),
    ("кубик: {move: [1, 2, .nan]}\n", "три конечных числа"),
    ("кубик: {scale: [2, 2, 2]}\n", "неизвестные ключи"),
    ("кубик: {move: [1.0e+308, 0, 0]}\n", "не место на столе"),
    ("кубик: {rotate: [0, 0, 1.0e+9]}\n", "больше десяти оборотов"),
])
def test_a_wrong_placement_file_is_refused_when_opening(cube, text, hint):
    (cube / P.FILE).write_text(text, encoding="utf-8")
    with pytest.raises(ProjectError, match=hint):
        Project.open(cube)


def test_placement_changes_reach_the_window():
    from pathlib import Path

    from datum.server.watch import relevant

    root = Path("/проект")
    assert relevant(root, root / "placement.yaml")


# ----------------------------------------------------------------- операции
def test_an_absurd_place_is_refused_and_the_file_is_untouched(cube):
    """Сдвиг 1e308 прежде принимался: деталь схлопывалась по X (ширина тоньше
    шага float), а сборка отвечала «OK»."""
    assert not run(part_place, project=str(cube), part="кубик", move=[5, 0, 0]).get("error")
    before = (cube / P.FILE).read_text(encoding="utf-8")
    for move, rotate in (([1e308, 0, 0], None), ([0, -10_001, 0], None),
                         (None, [0, 4000, 0])):
        out = run(part_place, project=str(cube), part="кубик", move=move, rotate=rotate)
        assert out.get("error"), (move, rotate)
    assert (cube / P.FILE).read_text(encoding="utf-8") == before


def test_far_but_sane_places_still_work(cube):
    """Предел — не стол: деталь можно отставить и за край (предупреждение
    скажет), и повернуть на несколько оборотов."""
    out = run(part_place, project=str(cube), part="кубик", move=[9_999, -500, 3],
              rotate=[720, -3600, 45])
    assert not out.get("error"), out.get("error")
    assert P.load(cube)["кубик"].move == (9999.0, -500.0, 3.0)


def test_place_writes_only_what_is_asked(cube):
    out = run(part_place, project=str(cube), part="кубик", move=[10, 0, 0])
    assert not out.get("error"), out.get("error")
    out = run(part_place, project=str(cube), part="кубик", rotate=[0, 0, 30])
    assert out["placement"] == {"move": [10.0, 0.0, 0.0], "rotate": [0.0, 0.0, 30.0]}
    assert "нет детали" in run(part_place, project=str(cube), part="нет", move=[1, 1, 1])["error"]


def test_delete_goes_to_the_trash_and_comes_back_with_its_place(cube):
    run(part_place, project=str(cube), part="кубик", move=[3, 4, 0])
    checks_before = (cube / "checks" / "checks.yaml").read_text(encoding="utf-8")
    plan = run(part_delete, project=str(cube), part="кубик", dry_run=True)
    assert plan["file"] == "parts/cube.py" and plan["parts"] == ["кубик"]
    assert set(plan["checks"]) == {"кубик.целостность", "кубик.без_пузырей",
                                   "кубик.стенка", "кубик.на_столе"}
    assert (cube / "parts" / "cube.py").is_file()          # предпросмотр ничего не трогает

    done = run(part_delete, project=str(cube), part="кубик")
    assert not done.get("error"), done.get("error")
    assert not (cube / "parts" / "cube.py").exists()
    assert not (cube / P.FILE).exists()                     # положение ушло вместе с ней
    project = Project.open(cube)
    assert "кубик" not in project.parts
    # Деталь удалена ЦЕЛИКОМ: проверки на неё не остались краснеть ошибкой, а
    # шапка файла с комментарием — на месте.
    assert project.check_decls == []
    assert (cube / "checks" / "checks.yaml").read_text(encoding="utf-8").startswith("# Проверки")

    back = run(part_restore, project=str(cube))
    assert not back.get("error"), back.get("error")
    project = Project.open(cube)
    assert "кубик" in project.parts
    assert project.placements["кубик"].move == (3.0, 4.0, 0.0)
    assert (cube / "checks" / "checks.yaml").read_text(encoding="utf-8") == checks_before
    assert "пуста" in run(part_restore, project=str(cube))["error"]


def _import_as_part(root, name="модель"):
    import trimesh

    from datum.tools.ops import import_model, import_to_part

    src = root.parent / f"{name}.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(src)
    assert not run(import_model, project=str(root), path=str(src), name=name).get("error")
    out = run(import_to_part, project=str(root), name=name)
    assert not out.get("error"), out.get("error")


def test_the_model_goes_with_its_only_part_and_comes_back(cube):
    """Иначе модель, которую больше никто не берёт, встала бы на стол образцом —
    «повтором» только что удалённой детали."""
    from datum.build.imported import imported_names

    _import_as_part(cube)
    plan = run(part_delete, project=str(cube), part="модель", dry_run=True)
    assert plan["models"] == ["модель"]
    run(part_delete, project=str(cube), part="модель")
    assert imported_names(cube) == []
    back = run(part_restore, project=str(cube))
    assert back["models"] == ["модель"] and imported_names(cube) == ["модель"]
    assert "модель" in Project.open(cube).parts


def test_a_model_another_file_uses_stays(cube):
    from datum.build.imported import imported_names

    _import_as_part(cube)
    from datum.build.imported import part_source

    (cube / "parts" / "copy.py").write_text(part_source("модель", "копия"), encoding="utf-8")
    assert "копия" in Project.open(cube).parts
    out = run(part_delete, project=str(cube), part="модель")
    assert not out.get("error") and out["models"] == []
    assert imported_names(cube) == ["модель"]
    assert "копия" in Project.open(cube).parts


def test_an_unclear_checks_file_is_left_alone(cube):
    """Кусок текста не отделяется — файл не трогается, а проверка краснеет."""
    path = cube / "checks" / "checks.yaml"
    path.write_text('[{id: кубик.целостность, use: single_body, args: {part: "@кубик"}}]\n',
                    encoding="utf-8")
    out = run(part_delete, project=str(cube), part="кубик")
    assert not out.get("error") and out["checks_kept"]
    assert "кубик.целостность" in path.read_text(encoding="utf-8")


def test_restore_never_overwrites(cube):
    run(part_delete, project=str(cube), part="кубик")
    (cube / "parts" / "cube.py").write_text("# новое\n", encoding="utf-8")
    out = run(part_restore, project=str(cube))
    assert "не перезаписывается" in out["error"]
    assert (cube / "parts" / "cube.py").read_text(encoding="utf-8") == "# новое\n"


def test_a_part_of_the_assembly_order_is_not_deleted(latch):
    out = run(part_delete, project=str(latch), part="крышка")
    assert out.get("blocked") and "порядке сборки" in out["error"]
    assert (latch / "parts" / "lid.py").is_file()


def test_simultaneous_moves_of_different_parts_all_survive(latch):
    """Найдено обзором: две одновременные правки отвечали «готово», а в файле
    оставалась одна — каждая писала свой снимок всего placement.yaml. Теперь
    правка — под блокировкой проекта и от свежего файла."""
    import subprocess
    import sys

    code = ("import asyncio, sys\n"
            "from datum.tools.ops import part_place\n"
            "for i in range(20):\n"
            "    out = asyncio.run(part_place(project=sys.argv[1], part=sys.argv[2],"
            " move=[float(i + 1), 0.0, float(sys.argv[3])]))\n"
            "    assert not out.get('error'), out\n")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(latch), name, z])
             for name, z in (("корпус", "0"), ("крышка", "1"))]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    placed = P.load(latch)
    assert placed["корпус"].move == (20.0, 0.0, 0.0)
    assert placed["крышка"].move == (20.0, 0.0, 1.0)
