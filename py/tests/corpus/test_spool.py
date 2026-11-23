"""Порт подставки под катушки: числа обязаны совпасть с записанными в корпусе.

Проект взят потому, что на нём у прежнего процесса РАСКАЛЫВАЛСЯ конвейер:
мешер OCCT на 168 канавках давал 164 открытых ребра, и печатный меш резался
отдельным путём. Здесь путь один, и это проверяется замкнутостью.

Числа ниже переписаны из `<корпус>/projects/spool-base/NOTES.md` — они
сняты с исходного меша сечениями, а не придуманы.
"""

from __future__ import annotations

import pathlib

import pytest

from datum.doc.project import Project

ROOT = pathlib.Path(__file__).resolve().parents[3]
SPOOL = ROOT / "examples" / "spool"


@pytest.fixture(scope="module")
def built():
    project = Project.open(SPOOL)
    result = project.build()["подставка"]
    assert result.ok, result.line()
    return project, result


def test_the_whole_thing_is_one_watertight_body(built):
    """То, ради чего порт и делался. У оригинала печатная форма получалась
    ВТОРЫМ путём, потому что первый не мешился без трещин."""
    _, result = built
    assert result.topology.bodies == 1
    assert result.topology.genus == 0


def test_the_overall_size_matches_the_original(built):
    from datum.geom.measure import bbox

    project, result = built
    params = project.params.proxy()
    lo, hi = bbox(result.solid)
    assert hi[0] - lo[0] == pytest.approx(params["WIDTH"], abs=0.01)
    assert hi[1] - lo[1] == pytest.approx(params["LENGTH"], abs=0.01)
    assert hi[2] - lo[2] == pytest.approx(params["HEIGHT"], abs=0.01)
    # 2·5 + 6·26 + 5·3 = 181, как в NOTES оригинала.
    assert params["LENGTH"] == pytest.approx(181.0)


def test_the_partitions_and_end_walls_are_where_they_should_be(built):
    """NOTES: «Отсеки 33.0, перегородки 3.0, торцы 5.0». Отсеки у нас 26."""
    from shapely.geometry import box as strip

    from datum.geom.measure import section_xy

    project, result = built
    params = project.params.proxy()
    half = params["LENGTH"] / 2
    section = section_xy(result.solid, params["CRADLE_CZ"] - params["CRADLE_R"] + 0.3)
    clipped = section.intersection(strip(-1, -half - 1, 1, half + 1))
    pieces = sorted(getattr(clipped, "geoms", [clipped]), key=lambda g: g.bounds[1])
    widths = [g.bounds[3] - g.bounds[1] for g in pieces]
    assert len(widths) == int(params["N_SLOTS"]) + 1
    assert widths[0] == pytest.approx(params["END_T"], abs=0.01)
    assert widths[-1] == pytest.approx(params["END_T"], abs=0.01)
    for middle in widths[1:-1]:
        assert middle == pytest.approx(params["DIV_T"], abs=0.01)


def test_the_knurling_is_168_grooves_of_the_right_section(built):
    """NOTES: «Рифление: 168 канавок по периметру, шаг 3.031». Число канавок
    проверяется ПЛОЩАДЬЮ, снятой сечением: 168 трапеций по 1.5 мм²."""
    from datum.geom.measure import section_xy
    from datum.geom.profile2d import rrect_poly

    project, result = built
    params = project.params.proxy()
    half_w, half_l = params["WIDTH"] / 2, params["LENGTH"] / 2
    plan = rrect_poly(-half_w, -half_l, half_w, half_l, params["CORNER_R"])

    # Высота выбрана выше фаски по низу и ниже дна люльки: там сечение — это
    # ровно план минус рифление, без посторонней геометрии.
    z = (params["BOTTOM_CHAMFER"]
         + (params["CRADLE_CZ"] - params["CRADLE_R"] - params["BOTTOM_CHAMFER"]) / 2)
    cut = plan.area - section_xy(result.solid, z).area
    one = (params["GROOVE_TOP_W"] + params["GROOVE_BOT_W"]) / 2 * params["GROOVE_DEPTH"]
    assert cut / one == pytest.approx(168, rel=0.01)


def test_the_slot_does_not_narrow_towards_its_floor(built):
    """Ошибка оригинала, ставшая проверкой.

    NOTES: «с ними линия дна отсека была 24 мм вместо 26, катушка садилась бы
    на скругления». Здесь просвет обязан быть 26 на всех высотах.
    """
    project, _ = built
    run = project.run_checks()
    slot = [r for r in run.results if r.id == "подставка.отсек_не_сужается"]
    assert slot, "проверка просвета отсека не объявлена"
    assert slot[0].status == "pass", slot[0].message
    assert slot[0].value == pytest.approx(project.params.proxy()["SLOT_L"], abs=0.01)


def test_every_check_of_the_port_passes(built):
    project, _ = built
    run = project.run_checks()
    assert run.ok, "\n".join(r.message for r in run.results if r.status != "pass")
    assert len(run.results) >= 7


def test_the_slot_check_sees_the_originals_narrowing(built):
    """NOTES: «верните скругление — и она покажет 24.000».

    Скругления в порте нет, поэтому сужение делается прямо: по миллиметру
    материала у каждой перегородки на дне одного отсека — ровно те 26 → 24, о
    которых писал оригинал. Проверка обязана это увидеть; иначе её «26.000»
    ничего не стоило бы.
    """
    import importlib.util
    from types import SimpleNamespace

    from datum.geom import box, union

    spec = importlib.util.spec_from_file_location("spool_custom", SPOOL / "checks" / "custom.py")
    custom = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(custom)

    project, result = built
    P = SimpleNamespace(**project.params.resolve())
    bottom = P.CRADLE_CZ - P.CRADLE_R
    solid = result.solid
    from datum.geom.measure import section_xy
    from shapely.geometry import box as strip

    pieces = sorted(section_xy(solid, bottom + 0.25).intersection(
        strip(-1, -P.LENGTH, 1, P.LENGTH)).geoms, key=lambda g: g.bounds[1])
    left, right = pieces[1].bounds[3], pieces[2].bounds[1]      # второй отсек
    assert right - left == pytest.approx(P.SLOT_L, abs=0.01)
    fillets = [box(-P.WIDTH, P.WIDTH, left - 0.5, left + 1.0, bottom - 1, bottom + 1),
               box(-P.WIDTH, P.WIDTH, right - 1.0, right + 0.5, bottom - 1, bottom + 1)]
    narrowed = union([solid, *fillets])

    verdict = custom.slot_width(P, lambda name: narrowed)
    assert verdict.status == "fail", verdict.message
    assert verdict.value == pytest.approx(24.0, abs=0.01)
