"""Собираемость — претензия №1.

Беда, которую ловит эта проверка, обиднее прочих: каждая деталь по отдельности
хороша, зацепы и зазоры зелёные, а вместе конструкция не собирается — деталь
некуда вставить, путь перекрыт.
"""

from __future__ import annotations

import numpy as np
import pytest

from datum.checks.assembly import AssemblyProbe, assembly_path
from datum.checks.motion import direction
from datum.geom.prims import box


@pytest.fixture
def open_pocket():
    """Карман, открытый только сверху."""
    return (box(0, 60, 0, 40, 0, 20) - box(5, 55, 5, 35, 2, 21)).renamed("корпус")


@pytest.fixture
def insert():
    return box(5.3, 54.7, 5.3, 34.7, 2.3, 12).renamed("вкладыш")


def test_it_finds_the_way_in(open_pocket, insert):
    result = assembly_path(insert, open_pocket, part_name="вкладыш", distance=30)
    assert result.status == "pass"
    # Карман открыт СВЕРХУ, значит вкладыш опускают в него: вставка по −Z.
    assert "сдвигом -Z" in result.message


def test_it_names_the_direction_it_found(open_pocket, insert):
    """Ответ «собирается» без направления бесполезен: собирать-то как?"""
    result = assembly_path(insert, open_pocket, part_name="вкладыш",
                           dirs=["-X", "-Z"], distance=30)
    assert result.status == "pass" and "сдвигом -Z" in result.message


def test_the_named_direction_is_the_one_that_goes_IN(open_pocket, insert):
    """Договорённость о знаке — физикой, а не строкой.

    Прежняя версия печатала направление СНЯТИЯ под словом «ставится»: все числа
    были верны, перевёрнут был только смысл, и ни один тест этого не замечал —
    они сверяли строку со строкой. Поэтому здесь проверяется само движение:
    сдвиг ПО названному направлению обязан загонять деталь в материал (глубже
    посадки), а встречный — выводить на свободу.
    """
    from datum.checks.motion import _shifted
    from datum.checks.motion import direction as parse
    from datum.geom.measure import inter_vol

    result = assembly_path(insert, open_pocket, part_name="вкладыш", distance=30)
    assert result.status == "pass"
    key = result.message.split("сдвигом ")[1].split(":")[0]

    внутрь = inter_vol(_shifted(insert, parse(key), 5.0), open_pocket)
    наружу = inter_vol(_shifted(insert, -parse(key), 5.0), open_pocket)
    assert внутрь > 1.0, (
        f"сдвиг по названному направлению {key} обязан упираться в дно кармана, "
        f"а даёт {внутрь:.3f} мм³ — значит названо направление снятия")
    assert наружу < 0.05, (
        f"встречный сдвиг обязан выводить деталь наружу, а даёт {наружу:.3f} мм³")


def test_a_side_slot_is_found_too():
    """«Надвигается сбоку» — второй способ сборки из корпуса, наравне с «входит
    сверху». Паз сквозной по X, сверху закрыт козырьком."""
    frame = (box(0, 60, 0, 40, 0, 24) - box(-1, 61, 5, 35, 2, 14)).renamed("корпус")
    bar = box(1, 59, 5.3, 34.7, 2.3, 13.7).renamed("планка")
    result = assembly_path(bar, frame, part_name="планка", distance=80)
    assert result.status == "pass"
    assert "+X" in result.message or "-X" in result.message


def test_an_enclosed_cavity_is_refused(open_pocket, insert):
    lid = box(0, 60, 0, 40, 20, 24)
    closed = (open_pocket + lid).renamed("корпус")
    result = assembly_path(insert, closed, part_name="вкладыш", distance=60)
    assert result.status == "fail"
    assert "НЕ СОБИРАЕТСЯ" in result.message


def test_the_failure_names_where_it_jams(open_pocket, insert):
    lid = box(0, 60, 0, 40, 20, 24)
    result = assembly_path(insert, (open_pocket + lid).renamed("корпус"),
                           part_name="вкладыш", distance=60)
    # Все шесть направлений перечислены с числами: разбирать надо по ним.
    for key in ("+Z", "-Z", "+X", "-X", "+Y", "-Y"):
        assert key in result.message
    assert "мм от посадки" in result.message


def test_the_failure_brings_a_picture(open_pocket, insert):
    result = assembly_path(insert, (open_pocket + box(0, 60, 0, 40, 20, 24)).renamed("к"),
                           part_name="вкладыш", distance=60)
    assert [a for a in result.evidence if a.kind == "png"]


def test_the_seat_itself_is_not_part_of_the_path():
    """Ключевая тонкость, взятая из корпуса: цикл там начинался с dy=1, а не с
    нуля. В посадке натяг ЗАДУМАН — защёлка обязана упираться, иначе она не
    держит. Включи посадку в развёртку — и любая работающая защёлка
    объявлялась бы несобираемой.
    """
    # Направляющая с сужением у самого входа: деталь садится с натягом.
    rail = (box(0, 40, 0, 30, 0, 20)
            - box(5, 45, 5, 25, 2, 12)
            + box(5, 6, 5, 25, 2, 12)).renamed("корпус")   # уступ в посадке
    part = box(5.5, 39, 5.3, 24.7, 2.3, 11.7).renamed("деталь")

    # Паз открыт со стороны +X, значит деталь задвигают в −X.
    seated = assembly_path(part, rail, part_name="деталь", dirs=["-X"],
                           distance=40, from_mm=0.0, samples=20)
    away = assembly_path(part, rail, part_name="деталь", dirs=["-X"],
                         distance=40, from_mm=1.5, samples=20)
    assert seated.value > away.value, (
        "натяг в посадке обязан попадать в развёртку только при from_mm=0")


def test_obstacles_can_be_several():
    """Порядок сборки важен: деталь, вставленная первой, закрывает дорогу
    следующей. В корпусе планка проверялась и против корпуса, и против уже
    вставленного телефона."""
    frame = (box(0, 60, 0, 40, 0, 24) - box(-1, 61, 5, 35, 2, 14)).renamed("корпус")
    bar = box(1, 59, 5.3, 34.7, 2.3, 13.7).renamed("планка")
    blocker = box(58, 70, 0, 40, 0, 24).renamed("пробка")

    assert assembly_path(bar, frame, part_name="планка", distance=80).status == "pass"
    both = assembly_path(bar, [frame, blocker], part_name="планка", distance=80)
    assert both.status == "fail", "пробка обязана закрыть дорогу"


def test_an_empty_obstacle_list_is_refused():
    with pytest.raises(ValueError, match="пуст"):
        assembly_path(box(0, 1, 0, 1, 0, 1), [], part_name="деталь")


def test_a_path_shorter_than_its_start_is_refused():
    with pytest.raises(ValueError, match="путь пуст"):
        assembly_path(box(0, 1, 0, 1, 0, 1), box(0, 2, 0, 2, 0, 2),
                      from_mm=10.0, distance=5.0)


def test_the_sweep_costs_a_grid_by_resolution_plus_refinement(open_pocket, insert):
    """Сетка с шагом не крупнее PATH_RESOLUTION плюс бисекция вокруг худшего —
    и ничего сверх. (Прежде шаг брали от протяжённости детали, и узлов было
    меньше, — но так проскакивала деталь из тонких стенок: см. ниже.)"""
    from datum.checks.assembly import PATH_RESOLUTION, _worst_along

    # AssemblyProbe — примитив нижнего уровня: ему даётся направление, в котором
    # деталь УВОДЯТ от посадки, то есть встречное вставке.
    probe = AssemblyProbe(insert, [open_pocket], direction("+Z"))
    _worst_along(probe, 0.5, 30.0, samples=25, refine=6)
    grid = int(np.ceil((30.0 - 0.5) / PATH_RESOLUTION)) + 1
    assert probe.calls <= grid + 6 * 2 + 2, f"вызовов {probe.calls}"


def test_a_part_of_thin_walls_does_not_step_through_a_thin_shelf():
    """Найдено обзором: две пластины 0.8 мм в 20 мм друг от друга (рамка,
    ящик) и полка 0.4 мм на пути. Шаг «от протяжённости детали» (1.65 мм)
    перешагивал полку — «задевание 0.000 мм³» при настоящем перекрытии."""
    from datum.checks.assembly import assembly_path
    from datum.geom import box, union

    plates = union([box(0, 20, 0, 20, 0, 0.8), box(0, 20, 0, 20, 20, 20.8),
                    box(0, 2, 0, 2, 0, 20.8)]).renamed("рамка")
    shelf = box(4, 16, 4, 16, 26.25, 26.65).renamed("полка")
    r = assembly_path(plates, [shelf], dirs=("-Z",), from_mm=0.5, distance=40.0)
    # Пластина накрывает полку 12×12 на всю её толщину 0.4: 12·12·0.4 = 57.6 мм³.
    assert r.status == "fail" and r.value == pytest.approx(12 * 12 * 0.4, rel=1e-3), r.message


# ---------------------------------------------------------- декларативный путь
def _slot_project(root, checks_yaml: str):
    """Проект из трёх деталей: штырь между двумя стенками.

    Штырь выходит вверх и вниз, вбок — никуда: стенки держат. Препятствий
    ДВА, и объявлены они списком — это и проверяется.
    """
    (root / "parts").mkdir(parents=True)
    (root / "params").mkdir(parents=True)
    (root / "checks").mkdir(parents=True)
    (root / "params" / "base.yaml").write_text(
        "ЗАЗОР: {value: 0.1, unit: mm, provenance: chosen, reason: 'скользящая'}\n",
        encoding="utf-8")
    (root / "parts" / "p.py").write_text(
        "from datum.build import P, part\n"
        "from datum.geom import box\n\n"
        "@part('штырь')\n"
        "def штырь():\n"
        "    return box(-1 + P.ЗАЗОР, 1 - P.ЗАЗОР, 0, 10, 0, 10)\n\n"
        "@part('стенка_левая')\n"
        "def левая():\n"
        "    return box(-6, -1, 0, 10, 0, 10)\n\n"
        "@part('стенка_правая')\n"
        "def правая():\n"
        "    return box(1, 6, 0, 10, 0, 10)\n",
        encoding="utf-8")
    (root / "checks" / "checks.yaml").write_text(checks_yaml, encoding="utf-8")
    (root / "datum.toml").write_text("[checks]\nbaseline = false\n", encoding="utf-8")   # только объявленное
    return root


def test_the_check_can_be_declared_in_checks_yaml(tmp_path):
    """Проверка из каталога обязана объявляться в проекте.

    Тонкость здесь в аргументе `obstacles`: он объявлен деталью, но приходит
    СПИСКОМ деталей. Разбор ссылок обязан пройти вглубь списка — иначе внутрь
    проверки уезжает PartRef и падает там невнятно.
    """
    from datum.doc.project import Project

    root = _slot_project(tmp_path / "паз",
                         '- id: штырь.сборка\n'
                         '  use: assembly_path\n'
                         '  args:\n'
                         '    part: "@штырь"\n'
                         '    obstacles: ["@стенка_левая", "@стенка_правая"]\n'
                         '    distance: 20\n')
    run = Project.open(root).run_checks()
    assert len(run.results) == 1
    r = run.results[0]
    assert r.status == "pass", r.message
    assert r.id == "штырь.сборка"
    assert "сдвигом +Z" in r.message or "сдвигом -Z" in r.message


def test_a_declared_obstacle_list_is_really_two_obstacles(tmp_path):
    """Обе стенки обязаны участвовать, а не только первая.

    Саботаж: разрешаем штырю выходить только вбок. С ОБЕИМИ стенками пути
    нет; если бы список молча терялся до одной, проверка бы прошла.
    """
    from datum.doc.project import Project

    root = _slot_project(tmp_path / "бок",
                         '- id: штырь.вбок\n'
                         '  use: assembly_path\n'
                         '  args:\n'
                         '    part: "@штырь"\n'
                         '    obstacles: ["@стенка_левая", "@стенка_правая"]\n'
                         '    dirs: ["+X", "-X"]\n'
                         '    distance: 20\n')
    r = Project.open(root).run_checks().results[0]
    assert r.status == "fail", r.message
    # Обе стороны заперты — значит обе стенки посчитаны.
    assert "+X" in r.message and "-X" in r.message


def test_the_check_records_which_params_it_depends_on(tmp_path):
    """Зазор штыря читается при сборке — он обязан попасть в depends_on."""
    from datum.doc.project import Project

    root = _slot_project(tmp_path / "связи",
                         '- id: штырь.сборка\n'
                         '  use: assembly_path\n'
                         '  args:\n'
                         '    part: "@штырь"\n'
                         '    obstacles: ["@стенка_левая", "@стенка_правая"]\n'
                         '    distance: 20\n')
    r = Project.open(root).run_checks().results[0]
    assert "ЗАЗОР" in r.depends_on


def test_a_thin_plate_does_not_slip_through_a_rib_between_nodes():
    """Пластина 0.3 мм, вставляемая плашмя сквозь ребро 0.2 мм, на пути 30 мм.

    Контакт длится 0.5 мм пути, а шаг сетки из 25 узлов — 1.2 мм: прежняя
    развёртка его проскакивала. Теперь шаг выводится из толщины детали вдоль
    пути, и ребро находится.
    """
    from datum.checks.assembly import AssemblyProbe, _worst_along

    plate = box(0, 10, 0, 10, 0, 0.3)
    rib = box(-5, 15, 4.9, 5.1, 13.3, 13.5)   # контакт при d 13.0…13.5, узлы 12.79 и 14.02
    probe = AssemblyProbe(plate, [rib], direction("+Z"))
    where, worst, _, _ = _worst_along(probe, 0.5, 30.0, samples=25, refine=6)
    assert worst > 0.0, "ребро проскочило между узлами"
    assert 13.0 < where < 13.5


# ------------------------------------------------ защёлка на настоящей геометрии
@pytest.fixture(scope="module")
def latch_parts():
    """Крышка и корпус из примера `latch` — единственная защёлка корпуса."""
    import pathlib

    from datum.doc.project import Project

    root = pathlib.Path(__file__).resolve().parents[3] / "examples" / "latch"
    built = Project.open(root).build(use_cache=False)
    return built["крышка"].solid, built["корпус"].solid


def test_the_latch_lid_snaps_in_from_above(latch_parts):
    from datum.checks.assembly import snap_insertion

    lid, case = latch_parts
    r = snap_insertion(lid, case, direction="-Z", max_press_vol=320, distance=20)
    assert r.status == "pass", r.message
    assert 100 < r.value < 320
    assert "защёлкивается" in r.message


def test_a_snap_tighter_than_intended_fails(latch_parts):
    from datum.checks.assembly import snap_insertion

    lid, case = latch_parts
    r = snap_insertion(lid, case, direction="-Z", max_press_vol=50, distance=20)
    assert r.status == "fail" and r.value > 50


def test_sideways_the_lid_does_not_come_free_it_jams(latch_parts):
    """Вбок продавливается даже меньше, чем сверху, — только идти пришлось бы
    сквозь стенку корпуса, и на свободу деталь не выходит. Это затор, а не
    защёлка, и объём тут не судья."""
    from datum.checks.assembly import snap_insertion

    lid, case = latch_parts
    r = snap_insertion(lid, case, direction="+X", max_press_vol=1000, distance=20)
    assert r.status == "fail" and "затор" in r.message


def test_a_path_too_short_to_come_free_is_a_jam(latch_parts):
    from datum.checks.assembly import snap_insertion

    lid, case = latch_parts
    r = snap_insertion(lid, case, direction="-Z", max_press_vol=1000, distance=2.0)
    assert r.status == "fail" and "затор" in r.message
