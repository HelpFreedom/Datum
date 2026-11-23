"""Опыт печати: урок с признаком позы напоминает о себе на сборке будущих деталей.

«Datum запомнит это для будущих проверок?» — вопрос владельца про шестерни
Gear Cube, вышедшие вершиной вниз с оплывшими рёбрами.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from datum.doc import journal, lessons
from datum.geom import box, cyl_z
from datum.geom.xform import rotate
from datum.tools import ops, ops_lessons


@pytest.fixture(autouse=True)
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("DATUM_DATA_HOME", str(tmp_path / "xdg" / "datum"))
    ops.clear_project_cache()
    yield tmp_path / "xdg" / "datum" / "lessons.json"
    ops.clear_project_cache()


def _onto_vertex(cube):
    # Z на 45°, затем наклон на arctan(√2) ≈ 54.74° вокруг X: вершина смотрит вниз.
    return rotate(rotate(cube, rz=45), rx=math.degrees(math.atan(math.sqrt(2))))


# ------------------------------------------------------------------ признаки
def test_a_cube_on_its_vertex_on_its_edge_and_on_its_face():
    cube = box(-10, 10, -10, 10, -10, 10)
    on_vertex = _onto_vertex(cube)
    on_edge = rotate(cube, rx=45)
    assert lessons.sign_holds("vertex_down", None, on_vertex)[0]
    assert not lessons.sign_holds("vertex_down", None, on_edge)[0]
    assert not lessons.sign_holds("vertex_down", None, cube)[0]
    assert lessons.sign_holds("edge_down", None, on_edge)[0]
    assert not lessons.sign_holds("edge_down", None, on_vertex)[0]
    assert not lessons.sign_holds("edge_down", None, cube)[0]


def test_a_thin_plate_standing_on_its_edge_is_not_an_edge_down_solid():
    """Пластина на торце — узкая опора, но не «на ребре»: выше она не шире."""
    plate = box(0, 40, 0, 1.2, 0, 30)
    assert not lessons.sign_holds("edge_down", None, plate)[0]
    assert lessons.sign_holds("tall_thin", None, plate)[0]


def test_footprint_and_slenderness_have_thresholds():
    pin = cyl_z(6, 0, 60)
    assert lessons.sign_holds("small_footprint", None, pin)[0]
    assert not lessons.sign_holds("small_footprint", {"max_mm2": "20"}, pin)[0]
    assert lessons.sign_holds("tall_thin", {"ratio": 8}, pin)[0]
    assert not lessons.sign_holds("tall_thin", {"ratio": 12}, pin)[0]
    assert "мм²" in lessons.sign_holds("small_footprint", None, pin)[1]


@pytest.mark.parametrize("sign, args, words", [
    ("corner", None, "признака 'corner' нет"),
    ("tall_thin", {"max_mm2": 5}, "нет аргумента"),
    ("tall_thin", {"ratio": "много"}, "нужно число"),
    ("tall_thin", {"ratio": -1}, "положительное"),
    ("tall_thin", {"ratio": float("nan")}, "положительное"),
])
def test_a_sign_that_is_not_one_is_refused_in_words(sign, args, words):
    with pytest.raises(lessons.LessonError, match=words):
        lessons.sign_holds(sign, args, box(0, 1, 0, 1, 0, 1))


# ---------------------------------------------------------------------- база
def test_lessons_accumulate_and_survive_a_broken_record(base):
    lessons.add(project="а", part="п", outcome="плохо", text="  рёбра\nоплыли  ", material="PETG")
    lessons.add(project="б", part="", outcome="хорошо", text="чисто")
    got = lessons.read()
    assert [x.text for x in got] == ["рёбра оплыли", "чисто"] and got[0].id != got[1].id
    assert "[плохо] а, «п» (PETG): рёбра оплыли" in got[0].line()
    base.write_text(base.read_text("utf-8").replace('"outcome"', '"outcome_x"', 1), "utf-8")
    assert [x.text for x in lessons.read()] == ["чисто"], "испорченная запись — мимо, не отказ"
    base.write_text("не json", "utf-8")
    assert lessons.read() == []


@pytest.mark.parametrize("kw, words", [
    (dict(outcome="так себе", text="x"), "итог"),
    (dict(outcome="плохо", text="   "), "пустой урок"),
    (dict(outcome="плохо", text="я" * 700), "длиннее"),
    (dict(outcome="плохо", text="x", sign="нет"), "признака"),
])
def test_what_is_not_a_lesson_is_refused(kw, words):
    with pytest.raises(lessons.LessonError, match=words):
        lessons.add(project="а", part="п", **kw)


def test_only_bad_lessons_with_a_holding_sign_remind():
    lessons.add(project="куб", part="шестерня", outcome="плохо", text="рёбра оплыли",
                sign="vertex_down")
    lessons.add(project="куб", part="шестерня", outcome="хорошо", text="гранью вниз чисто",
                sign="vertex_down")
    lessons.add(project="куб", part="ось", outcome="плохо", text="оторвало", sign="edge_down")
    lessons.add(project="куб", part="", outcome="плохо", text="без признака")
    hits = lessons.matching(_onto_vertex(box(-10, 10, -10, 10, -10, 10)))
    assert [x.text for x, _ in hits] == ["рёбра оплыли"]
    assert lessons.matching(box(0, 30, 0, 30, 0, 10)) == []


# ------------------------------------------------------------------ операции
def _run(coro):
    return asyncio.run(coro)


def test_print_log_writes_the_journal_and_the_common_base(cube, base):
    res = _run(ops_lessons.print_log(str(cube), "хорошо", "стакан вышел чисто", "кубик"))
    assert "error" not in res, res
    assert [e.kind for e in journal.read(cube)] == ["печать"]
    assert "кубик: [хорошо] стакан вышел чисто" in journal.read(cube)[0].text
    saved = lessons.read()
    assert saved[0].part == "кубик" and saved[0].material == "PLA" and saved[0].print_up == "+Z"
    listed = _run(ops_lessons.lessons_list(str(cube)))
    assert "стакан вышел чисто" in listed["report"]
    assert not _run(ops_lessons.lessons_list(str(cube), "PETG"))["lessons"]


def test_a_sign_must_hold_on_the_very_part_of_the_lesson(cube, base):
    """Иначе урок напоминал бы о себе не там."""
    res = _run(ops_lessons.print_log(str(cube), "плохо", "оплыло", "кубик", "vertex_down"))
    assert "НЕ выполняется на самой детали" in res["error"]
    assert lessons.read() == [] and journal.read(cube) == []
    assert "назовите деталь" in _run(
        ops_lessons.print_log(str(cube), "плохо", "оплыло", "", "vertex_down"))["error"]
    assert "нет детали" in _run(ops_lessons.print_log(str(cube), "плохо", "x", "крышка"))["error"]


def test_building_a_part_with_the_sign_reminds_of_the_lesson(cube, base):
    # Стакан 20×20 — первый слой 400 мм²; урок про опору меньше 500.
    res = _run(ops_lessons.print_log(str(cube), "плохо", "отлип на третьем слое", "кубик",
                                     "small_footprint", {"max_mm2": "500"}))
    assert "error" not in res, res
    ops.clear_project_cache()
    built = _run(ops.part_build(str(cube)))
    assert built["ok"] and len(built["lessons"]) == 1
    assert "опыт печати — кубик: первый слой меньше порога" in built["report"]
    assert "отлип на третьем слое" in built["report"]
    # Напоминание — не проверка: вердикт проверок прежний.
    ops.clear_project_cache()
    assert _run(ops.check_run(str(cube)))["ok"]


def test_the_brief_names_the_common_experience(cube, base):
    from datum.doc import brief

    assert "ОПЫТ ПЕЧАТИ" not in brief.compose(cube)
    lessons.add(project="куб", part="шестерня", outcome="плохо", text="рёбра оплыли")
    text = brief.compose(cube)
    assert "ОПЫТ ПЕЧАТИ (общая база" in text and "рёбра оплыли" in text
