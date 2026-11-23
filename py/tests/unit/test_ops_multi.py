"""Несколько видов и срезов одним вызовом — те же числа, что по одному."""

from __future__ import annotations

import asyncio

import pytest

from datum.tools import ops, ops_multi


@pytest.fixture(autouse=True)
def fresh():
    ops.clear_project_cache()
    yield
    ops.clear_project_cache()


def _run(coro):
    return asyncio.run(coro)


def test_views_of_several_parts_come_in_one_answer(latch):
    res = _run(ops_multi.part_views(str(latch), ["корпус", "крышка", "корпус"]))
    assert list(res["views"]) == ["корпус", "крышка"], "повтор имени — один раз"
    for name, view in res["views"].items():
        assert (latch / view["image"]["path"]).is_file() and "report" not in view
        assert f"{name}: вид с четырёх сторон" in res["report"]
    # Картинки — те же, что у одиночного вызова: один растеризатор.
    one = _run(ops.part_view(str(latch), "крышка"))
    assert one["image"]["path"] == res["views"]["крышка"]["image"]["path"]


def test_an_unknown_part_does_not_hide_the_others(latch):
    res = _run(ops_multi.part_views(str(latch), ["крышка", "нет_такой"]))
    assert list(res["views"]) == ["крышка"] and "нет_такой" in res["failed"]
    assert "НЕ НАРИСОВАНА" in res["report"]
    assert "error" in _run(ops_multi.part_views(str(latch), ["нет_такой"]))


def test_more_than_an_answer_can_carry_is_refused(latch):
    assert "до 4" in _run(ops_multi.part_views(str(latch), list("абвгд")))["error"]
    assert "до 4" in _run(ops_multi.sections(str(latch), "корпус", "xy", [1, 2, 3, 4, 5]))["error"]
    assert "ни одной" in _run(ops_multi.sections(str(latch), "корпус", "xy", []))["error"]
    assert "ни одной" in _run(ops_multi.part_views(str(latch), []))["error"]


def test_sections_at_several_heights_equal_the_single_ones(latch):
    res = _run(ops_multi.sections(str(latch), "корпус", "xy", [1.0, 10.0, 99.0]))
    assert [s["at"] for s in res["sections"]] == [1.0, 10.0, 99.0]
    assert res["sections"][2]["empty"] is True
    for got in res["sections"][:2]:
        one = _run(ops.section(str(latch), "корпус", "xy", got["at"], False, False))
        assert got["area_mm2"] == one["area_mm2"] and got["pieces"] == one["pieces"]
        assert (latch / got["image"]["path"]).is_file()
    assert res["report"].count("сечение xy") == 3
    assert "error" in _run(ops_multi.sections(str(latch), "нет", "xy", [1.0]))


def test_one_job_cuts_the_whole_scene(latch):
    res = _run(ops_multi.section_scene(str(latch), "xy", 15.0))
    assert set(res["parts"]) == {"корпус", "крышка"} and not res["failed"]
    for name, cut in res["parts"].items():
        one = _run(ops.section(str(latch), name, "xy", 15.0, True, False))
        assert cut["contours"] == one["contours"], "те же контуры, что у одиночного среза"
    only = _run(ops_multi.section_scene(str(latch), "xz", 20.0, ["крышка"]))
    assert list(only["parts"]) == ["крышка"]


def test_a_part_that_cannot_be_cut_does_not_blank_the_rest(latch):
    res = _run(ops_multi.section_scene(str(latch), "xy", 15.0, ["корпус", "призрак"]))
    assert list(res["parts"]) == ["корпус"] and "призрак" in res["failed"]


def test_the_window_cut_is_a_service_call():
    from datum.server.jobs import SERVICE_OPS

    assert "section_scene" in SERVICE_OPS
