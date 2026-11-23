"""Порядок сборки: `assembly.yaml` и выведенные из него проверки.

Знание, которое этот файл хранит, в корпусе жило в голове: «планка надевается
сбоку ПРИ ВСТАВЛЕННОМ ТЕЛЕФОНЕ». Телефон там не потому, что интересен, а
потому, что входит раньше и становится препятствием.
"""

from __future__ import annotations

import pytest

from datum.doc.assembly import (DEFAULT_DISTANCE, DEFAULT_TOLERANCE, Assembly,
                                AssemblyError)


def build(**raw) -> Assembly:
    return Assembly.from_dict(raw)


# ------------------------------------------------------------------ вывод проверок
def test_each_part_is_blocked_by_everything_before_it():
    a = build(order=["корпус", "телефон", "планка"])
    decls = a.check_decls()
    assert [d["id"] for d in decls] == ["телефон.собираемость", "планка.собираемость"]
    assert decls[0]["args"]["obstacles"] == ["@корпус"]
    assert decls[1]["args"]["obstacles"] == ["@корпус", "@телефон"], \
        "планка обязана проверяться против УЖЕ ВСТАВЛЕННОГО телефона"


def test_the_first_part_is_not_checked():
    """Основание никуда не вставляют — проверять его не с чем."""
    a = build(order=["корпус"])
    assert a.check_decls() == []


def test_refinements_reach_the_check():
    a = build(order=["корпус", "планка"],
              parts={"планка": {"insert": "+X", "distance": 95, "tolerance": 3.0}})
    args = a.check_decls()[0]["args"]
    assert args["dirs"] == ["+X"]
    assert args["distance"] == 95.0
    assert args["max_inter_vol"] == 3.0


def test_without_refinements_all_six_directions_are_tried():
    a = build(order=["корпус", "крышка"])
    args = a.check_decls()[0]["args"]
    assert "dirs" not in args, "без уточнения проверка сама переберёт все шесть"
    assert args["distance"] == DEFAULT_DISTANCE
    assert args["max_inter_vol"] == DEFAULT_TOLERANCE


def test_several_directions_may_be_named():
    a = build(order=["к", "д"], parts={"д": {"insert": ["+Z", "-Z"]}})
    assert a.check_decls()[0]["args"]["dirs"] == ["+Z", "-Z"]


def test_direction_case_and_spaces_do_not_matter():
    a = build(order=["к", "д"], parts={"д": {"insert": " +z "}})
    assert a.check_decls()[0]["args"]["dirs"] == ["+Z"]


# ------------------------------------------------------------------ что не молчит
def test_a_part_outside_the_order_is_named():
    """Непроверенная деталь обязана быть видна: иначе зелёный отчёт означает
    «не проверяли», а выглядит как «всё хорошо»."""
    a = build(order=["корпус", "крышка"])
    assert a.unlisted(["корпус", "крышка", "кольцо", "штифт"]) == ["кольцо", "штифт"]
    assert a.unlisted(["корпус", "крышка"]) == []


def test_a_name_that_is_not_a_part_is_reported():
    a = build(order=["корпус", "опечатка"])
    assert a.missing(["корпус", "крышка"]) == ["опечатка"]


# ------------------------------------------------------------------ отказы
def test_a_part_cannot_be_inserted_twice():
    with pytest.raises(AssemblyError, match="дважды"):
        build(order=["корпус", "крышка", "крышка"])


def test_refinements_for_a_part_outside_the_order_are_refused():
    """Молча они ни на что не влияли бы — а человек считал бы, что задал допуск."""
    with pytest.raises(AssemblyError, match="вне порядка"):
        build(order=["корпус"], parts={"крышка": {"insert": "+Z"}})


def test_an_unknown_direction_is_refused():
    with pytest.raises(AssemblyError, match="неизвестное направление"):
        build(order=["к", "д"], parts={"д": {"insert": "+Q"}})


def test_an_unknown_key_is_refused():
    """Опечатка в ключе иначе просто игнорировалась бы."""
    with pytest.raises(AssemblyError, match="непонятные ключи"):
        build(order=["к", "д"], parts={"д": {"tolerence": 1.0}})


def test_an_empty_insert_is_refused():
    with pytest.raises(AssemblyError, match="пустой insert"):
        build(order=["к", "д"], parts={"д": {"insert": []}})


@pytest.mark.parametrize("bad", [0, -1, "много", True])
def test_distance_must_be_a_positive_number(bad):
    with pytest.raises(AssemblyError):
        build(order=["к", "д"], parts={"д": {"distance": bad}})


def test_order_must_be_a_list_of_names():
    with pytest.raises(AssemblyError, match="order"):
        build(order={"корпус": 1})


# ------------------------------------------------------------------ согласие с ядром
def test_the_direction_list_matches_the_one_the_check_understands():
    """Список направлений здесь продублирован нарочно — чтобы разбор
    assembly.yaml не тянул ядро геометрии. Дубль обязан совпадать."""
    from datum.checks.motion import DIRECTIONS

    from datum.doc.assembly import DIRECTION_KEYS

    assert set(DIRECTION_KEYS) == set(DIRECTIONS)
