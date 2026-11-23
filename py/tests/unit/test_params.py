"""Реестр параметров: происхождение, формулы, единицы, развёртка."""

from __future__ import annotations

import math

import pytest

from datum.doc.params import ParamError, ParamRegistry
from datum.doc.units import from_canonical

BASE = {
    "PHONE_T": {"value": 8.2, "unit": "mm", "provenance": "measured",
                "source": "сечение input/redmi.stl при Y=80",
                "instrument": "model-section", "tolerance": 0.15,
                "conflicts": [{"value": 9.0, "source": "штангенциркуль", "date": "2026-09-02"}]},
    "CL_SLIDE": {"value": 0.25, "unit": "mm", "provenance": "assumed",
                 "range": [0.15, 0.35], "reason": "на этом принтере не проверена",
                 "measure_hint": "напечатать купон", "blocking": True,
                 "validate_with": "test_coupon"},
    "NOZZLE": {"value": 0.4, "unit": "mm", "provenance": "standard", "ref": "сопло CC2"},
    "WALL_MIN": {"provenance": "derived", "expr": "4 * NOZZLE", "unit": "mm"},
    "SHELL_T": {"value": 2.0, "unit": "mm", "provenance": "chosen",
                "reason": "жёсткость спинки"},
    "Z_TOP": {"provenance": "derived", "expr": "SHELL_T + PHONE_T + CL_SLIDE", "unit": "mm"},
}


@pytest.fixture
def reg():
    return ParamRegistry.from_dict(BASE)


# ------------------------------------------------------------------ основы
def test_values_and_derived(reg):
    v = reg.resolve()
    assert v["PHONE_T"] == pytest.approx(8.2)
    assert v["WALL_MIN"] == pytest.approx(1.6), "4 × 0.4"
    assert v["Z_TOP"] == pytest.approx(2.0 + 8.2 + 0.25)


def test_unknowns_and_print_readiness(reg):
    assert [p.name for p in reg.unknowns] == ["CL_SLIDE"]
    assert [p.name for p in reg.blocking_unknowns] == ["CL_SLIDE"]
    assert reg.summary()["print_ready"] is False


def test_conflicts_are_first_class(reg):
    """То, что в корпусе жило комментарием «пользователь намерил 9.0»."""
    assert [p.name for p in reg.conflicted] == ["PHONE_T"]
    c = reg["PHONE_T"].conflicts[0]
    assert c.value == pytest.approx(9.0) and c.source == "штангенциркуль"
    assert "расхождения" in reg.report().lower()


def test_depends_on_is_transitive(reg):
    assert reg.depends_on("Z_TOP") == frozenset({"SHELL_T", "PHONE_T", "CL_SLIDE"})
    assert reg.depends_on("WALL_MIN") == frozenset({"NOZZLE"})
    assert reg.depends_on("NOZZLE") == frozenset()


# ------------------------------------------------------------------ проверки схемы
def test_assumed_without_range_is_rejected():
    with pytest.raises(ParamError, match="обязан иметь диапазон"):
        ParamRegistry.from_dict({"X": {"value": 1.0, "provenance": "assumed"}})


def test_assumed_value_outside_range_is_rejected():
    with pytest.raises(ParamError, match="вне диапазона"):
        ParamRegistry.from_dict({"X": {"value": 5.0, "provenance": "assumed",
                                       "range": [1.0, 2.0]}})


def test_derived_without_expression_is_rejected():
    with pytest.raises(ParamError, match="обязан иметь формулу"):
        ParamRegistry.from_dict({"X": {"provenance": "derived"}})


def test_derived_with_its_own_value_is_rejected():
    with pytest.raises(ParamError, match="не может быть своего"):
        ParamRegistry.from_dict({"X": {"provenance": "derived", "expr": "1+1", "value": 3}})


def test_unknown_provenance_is_rejected():
    with pytest.raises(ParamError, match="происхождение"):
        ParamRegistry.from_dict({"X": {"value": 1, "provenance": "примерно"}})


def test_inverted_range_is_rejected():
    with pytest.raises(ParamError, match="меньше низа"):
        ParamRegistry.from_dict({"X": {"value": 1, "provenance": "assumed", "range": [2, 1]}})


# ------------------------------------------------------------------ формулы
def test_cycle_is_detected():
    with pytest.raises(ParamError, match="кольцевая|не разрешились"):
        ParamRegistry.from_dict({
            "A": {"provenance": "derived", "expr": "B + 1"},
            "B": {"provenance": "derived", "expr": "A + 1"}})


def test_typo_in_formula_is_reported_with_the_missing_name():
    with pytest.raises(ParamError) as e:
        ParamRegistry.from_dict({
            "A": {"value": 1, "provenance": "chosen"},
            "B": {"provenance": "derived", "expr": "A + NOZZEL"}})
    assert "NOZZEL" in str(e.value)


def test_dangerous_expression_is_rejected():
    with pytest.raises(Exception, match="запрещ"):
        ParamRegistry.from_dict({
            "A": {"provenance": "derived", "expr": "__import__('os').listdir('.')"}})


# ------------------------------------------------------------------ единицы
def test_units_are_converted_on_entry_and_display():
    r = ParamRegistry.from_dict({
        "GAP": {"value": 0.01, "unit": "in", "provenance": "chosen", "reason": "—"}})
    assert r.resolve()["GAP"] == pytest.approx(0.254), "хранится в мм"
    assert r["GAP"].display == pytest.approx(0.01), "показывается в дюймах"


def test_angle_units():
    r = ParamRegistry.from_dict({
        "TILT": {"value": 0.3490658504, "unit": "rad", "provenance": "chosen", "reason": "—"}})
    assert r.resolve()["TILT"] == pytest.approx(20.0, abs=1e-6)


def test_unknown_unit_is_rejected():
    with pytest.raises(ParamError, match="неизвестная единица"):
        ParamRegistry.from_dict({"X": {"value": 1, "unit": "попугай", "provenance": "chosen"}})


# ------------------------------------------------------------------ развёртка
def test_range_points_cover_both_ends_and_the_chosen_value(reg):
    pts = reg["CL_SLIDE"].range_points()
    assert pts == pytest.approx((0.15, 0.25, 0.35))


def test_overrides_flow_into_derived(reg):
    v = reg.resolve({"CL_SLIDE": 0.35})
    assert v["Z_TOP"] == pytest.approx(2.0 + 8.2 + 0.35), "формула обязана увидеть подстановку"


def test_override_of_unknown_name_is_rejected(reg):
    with pytest.raises(ParamError, match="неизвестных"):
        reg.resolve({"НЕТУ": 1.0})


# ------------------------------------------------------------------ прокси
def test_proxy_records_reads(reg):
    P = reg.proxy()
    _ = P.PHONE_T + P.WALL_MIN
    assert P.reads == {"PHONE_T", "WALL_MIN"}, "зависимости берутся из фактических чтений"
    P.reset_reads()
    assert P.reads == frozenset()


def test_proxy_is_read_only(reg):
    P = reg.proxy()
    with pytest.raises(AttributeError, match="менять из скрипта"):
        P.PHONE_T = 9.0


def test_proxy_reports_unknown_name_helpfully(reg):
    P = reg.proxy()
    with pytest.raises(AttributeError, match="нет в реестре"):
        _ = P.PHONE_TT


# ------------------------------------------------------------------ файл
def test_save_and_load_roundtrip(reg, tmp_path):
    f = tmp_path / "params.yaml"
    reg.save(f)
    again = ParamRegistry.load(f)
    assert set(again.params) == set(reg.params)
    a, b = again.resolve(), reg.resolve()
    for k in b:
        assert a[k] == pytest.approx(b[k]), k
    p = again["CL_SLIDE"]
    assert p.blocking and p.range == pytest.approx((0.15, 0.35))
    assert again["PHONE_T"].conflicts[0].value == pytest.approx(9.0)


# ------------------------------------------------ формула не вешает процесс
@pytest.mark.parametrize("expr", ["9**9**9", "10**10**10", "2**(2**100)"])
def test_a_huge_power_is_refused_at_once(expr):
    """Целые у Python неограниченной длины: `9**9**9` считался минутами и не
    отвечал на сигналы. Одна строка в чужом params/base.yaml вешала открытие
    проекта."""
    import time

    from datum.doc.expr import ExpressionError, evaluate

    t0 = time.perf_counter()
    with pytest.raises(ExpressionError):
        evaluate(expr, {})
    assert time.perf_counter() - t0 < 0.5


@pytest.mark.parametrize("expr,why", [("(-8)**(1/3)", "комплексное"), ("1e308*10", "inf")])
def test_a_non_real_result_is_refused(expr, why):
    from datum.doc.expr import ExpressionError, evaluate

    with pytest.raises(ExpressionError, match=why):
        evaluate(expr, {})


def test_round_with_digits_still_works():
    from datum.doc.expr import evaluate

    assert evaluate("round(3.14159, 2)", {}) == pytest.approx(3.14)
    assert evaluate("round(X)", {"X": 2.6}) == 3.0


# ------------------------------------------------ кривой params/base.yaml
def _one(body: dict, name: str = "P1"):
    return ParamRegistry.from_dict({name: body})


@pytest.mark.parametrize("body,word", [
    ({"value": "abc", "unit": "mm", "provenance": "chosen", "reason": "-"}, "value"),
    ({"value": [1, 2], "unit": "mm", "provenance": "chosen", "reason": "-"}, "value"),
    ({"value": True, "unit": "mm", "provenance": "chosen", "reason": "-"}, "value"),
    ({"value": float("nan"), "unit": "mm", "provenance": "chosen", "reason": "-"}, "не размер"),
    ({"value": 1, "unit": "mm", "provenance": "assumed", "range": [0, 2],
      "blocking": "no", "reason": "-"}, "blocking"),
    ({"value": 1, "unit": "mm", "provenance": "chosen", "blocking": True, "reason": "-"},
     "только у угаданного"),
    ({"value": 1, "unit": "mm", "provenance": "chosen", "tags": "abc", "reason": "-"}, "tags"),
    ({"value": 1, "unit": "mm", "provenance": "chosen", "tolerance": -5, "reason": "-"},
     "допуск"),
    ({"value": 1, "unit": "mm", "provenance": "chosen", "conflicts": [{"source": "x"}],
      "reason": "-"}, "расхождение"),
    ({"value": 1, "unit": "mm", "provenance": "chosen", "measure_hnt": "x", "reason": "-"},
     "measure_hnt"),
    ({"unit": "mm", "provenance": "derived", "expr": "B +"}, "P1"),
])
def test_a_broken_parameter_names_itself(body, word):
    """Раньше: сырое «could not convert string to float: 'abc'» без имени
    параметра, а `blocking: "no"` молча давал True."""
    with pytest.raises(ParamError) as info:
        _one(body)
    assert "P1" in str(info.value) and word in str(info.value)


def test_a_numeric_string_is_accepted():
    """PyYAML читает `1e-3` без точки как строку — отвергать её было бы ловушкой."""
    reg = _one({"value": "1e-3", "unit": "mm", "provenance": "chosen", "reason": "-"})
    assert reg["P1"].value == pytest.approx(1e-3)


@pytest.mark.parametrize("name", ["pi", "sqrt", "values", "reads", "_x", "a b", "1x"])
def test_a_reserved_or_unwritable_name_is_refused(name):
    """`pi` перекрывался константой формул, `values` — служебным полем прокси
    (P.values отдавал словарь), а `a b` не написать ни в формуле, ни как P.имя."""
    with pytest.raises(ParamError, match="имя параметра"):
        _one({"value": 1, "unit": "mm", "provenance": "chosen", "reason": "-"}, name)


def test_a_parameter_described_twice_in_the_file_is_refused(tmp_path):
    """Обычный YAML молча берёт последний из двух одноимённых ключей."""
    f = tmp_path / "base.yaml"
    f.write_text("W: {value: 1, unit: mm, provenance: chosen, reason: a}\n"
                 "H: {value: 2, unit: mm, provenance: chosen, reason: b}\n"
                 "W: {value: 3, unit: mm, provenance: chosen, reason: c}\n", encoding="utf-8")
    with pytest.raises(ParamError, match="описан дважды"):
        ParamRegistry.load(f)
