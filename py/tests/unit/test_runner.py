"""Исполнитель проверок и развёртка по незамеренному.

Главный тест здесь — тот, ради которого затевался реестр: по трём угаданным
числам отчёт обязан сказать, какое из них решает дело, а какие мерить незачем.
"""

from __future__ import annotations

import pytest

from datum.checks.motion import escape
from datum.checks.result import CheckResult
from datum.checks.runner import CheckSpec, Runner, run_checks, sweep_assumed
from datum.doc.params import ParamRegistry
from datum.geom import box, diff

PARAMS = {
    # Глубина губки — угадана. От неё напрямую зависит, удержится ли деталь.
    "LIP_D": {"value": 0.8, "unit": "mm", "provenance": "assumed",
              "range": [0.3, 1.5], "reason": "по фотографии, не мерено",
              "measure_hint": "штангенциркулем по торцу", "blocking": True},
    # Сила магнита — угадана, но входит только в проверку с огромным запасом.
    "MAGNET_F": {"value": 8.0, "unit": "N", "provenance": "assumed",
                 "range": [4.0, 12.0], "reason": "из описания продавца"},
    # Толщина наклейки — угадана и не используется нигде.
    "STICKER_T": {"value": 0.1, "unit": "mm", "provenance": "assumed",
                  "range": [0.05, 0.3], "reason": "неизвестна"},
    "GAP": {"value": 0.3, "unit": "mm", "provenance": "chosen", "reason": "скользящая"},
    "HOLD_MIN": {"value": 1.0, "unit": "mm", "provenance": "chosen", "reason": "порог зацепа"},
}


def holder(lip_d: float):
    """Карман 5…35 × 5…15 × 2…10 с губкой глубиной lip_d."""
    return diff(box(0, 40, 0, 20, 0, 14),
                [box(5, 35, 5, 15, 2, 10),
                 box(5 + lip_d, 35 - lip_d, 5, 15, 10, 14)]).renamed("корпус")


def part(gap: float):
    return box(5 + gap, 35 - gap, 5 + gap, 15 - gap, 2 + gap, 10 - gap).renamed("шторка")


def check_retention(P) -> CheckResult:
    """Читает LIP_D, GAP, HOLD_MIN — и ничего больше."""
    return escape(part(P.GAP), holder(P.LIP_D), ["+Z"],
                  gap=P.GAP, hold_vol=P.HOLD_MIN, id_prefix="удержание")[0]


def check_magnet(P) -> CheckResult:
    """Читает MAGNET_F. Запас десятикратный при любом значении диапазона."""
    need = 0.5
    return CheckResult(
        id="магнит.усилие", title="Магнит держит", group="analytic",
        status="pass" if P.MAGNET_F >= need else "fail",
        message=f"сила магнита {P.MAGNET_F:.1f} Н ≥ {need} Н",
        value=P.MAGNET_F, unit="Н", op=">=", threshold=need)


@pytest.fixture
def runner():
    reg = ParamRegistry.from_dict(PARAMS)
    return (Runner(reg)
            .add("удержание", check_retention, group="motion")
            .add("магнит", check_magnet, group="analytic"))


# ------------------------------------------------------------------ прогон
def test_baseline_passes(runner):
    run = runner.run()
    assert run.ok, run.report()


def test_dependencies_come_from_actual_reads(runner):
    """Зависимости не объявлены руками — они записаны прокси при чтении."""
    run = runner.run()
    hold = next(r for r in run.results if r.id.startswith("удержание"))
    assert set(hold.depends_on) == {"LIP_D", "GAP", "HOLD_MIN"}
    magnet = next(r for r in run.results if r.id.startswith("магнит"))
    assert set(magnet.depends_on) == {"MAGNET_F"}


def test_a_failing_check_does_not_kill_the_run():
    reg = ParamRegistry.from_dict(PARAMS)
    def boom(P):
        raise RuntimeError("что-то пошло не так")
    run = run_checks([CheckSpec(id="взрыв", fn=boom),
                      CheckSpec(id="магнит", fn=check_magnet)], reg)
    assert len(run.results) == 2
    assert next(r for r in run.results if r.id == "взрыв").status == "error"
    assert next(r for r in run.results if r.id.startswith("магнит")).status == "pass"


# ------------------------------------------------------------------ чувствительность
def test_sweep_names_the_one_number_worth_measuring(runner):
    s = runner.sweep()
    assert s.critical_params == ("LIP_D",), (
        f"решает дело только глубина губки, получено {s.critical_params}")
    assert set(s.harmless_params) == {"MAGNET_F", "STICKER_T"}


def test_sweep_shows_where_the_verdict_flips(runner):
    s = runner.sweep()
    rows = [r for r in s.for_param("LIP_D") if r.flips]
    assert rows, "при губке 0.3 мм деталь обязана перестать держаться"
    statuses = {v: st for v, st, _ in rows[0].samples}
    assert statuses[0.3] == "fail", statuses
    assert statuses[1.5] == "pass", statuses


def test_unused_unknown_is_not_even_run(runner):
    """Параметр, который никто не читает, не должен порождать прогонов."""
    s = runner.sweep()
    assert s.for_param("STICKER_T") == ()
    assert "STICKER_T" in s.harmless_params


def test_report_says_what_to_measure(runner):
    txt = runner.sweep().report()
    assert "ИЗМЕРИТЬ В ПЕРВУЮ ОЧЕРЕДЬ" in txt
    assert "LIP_D" in txt
    assert "штангенциркулем по торцу" in txt
    assert "Нечувствительно" in txt
    assert "MAGNET_F" in txt and "STICKER_T" in txt


def test_sweep_can_be_limited_to_named_params(runner):
    s = runner.sweep(params=["MAGNET_F"])
    assert s.checked_params == ("MAGNET_F",)
    assert s.critical_params == ()


def test_sensitivity_serialises(runner):
    d = runner.sweep().as_dict()
    assert d["critical"] == ["LIP_D"]
    assert set(d["harmless"]) == {"MAGNET_F", "STICKER_T"}
    assert all({"param", "check", "flips", "samples"} <= set(r) for r in d["rows"])


# ------------------------------------------------ ответ проверки не теряется
def _reg():
    return ParamRegistry.from_dict(
        {"W": {"value": 10, "unit": "mm", "provenance": "chosen", "reason": "-"}})


def test_a_check_that_forgot_return_is_an_error_not_a_silent_pass():
    """Забытый return в ветви провала раньше давал пустой список и «ИТОГ: OK»."""
    def forgot(p):
        if p["W"] > 5:
            pass

    run = run_checks([CheckSpec(id="x", fn=forgot)], _reg())
    assert len(run.results) == 1
    assert run.results[0].status == "error" and "return" in run.results[0].message
    assert not run.ok


def test_a_generator_check_works_instead_of_killing_the_run():
    def gen(p):
        yield CheckResult(id="g.a", title="a", status="pass", message="ok")
        yield CheckResult(id="g.b", title="b", status="fail", message="плохо")

    def fine(p):
        return CheckResult(id="f", title="f", status="pass", message="ok")

    run = run_checks([CheckSpec(id="g", fn=gen), CheckSpec(id="f", fn=fine)], _reg())
    assert [r.id for r in run.results] == ["g.a", "g.b", "f"]


def test_a_wrong_answer_type_is_an_error_of_that_check_only():
    def wrong(p):
        return {"status": "pass"}

    def fine(p):
        return CheckResult(id="f", title="f", status="pass", message="ok")

    run = run_checks([CheckSpec(id="w", fn=wrong), CheckSpec(id="f", fn=fine)], _reg())
    assert run.results[0].status == "error" and "dict" in run.results[0].message
    assert run.results[1].status == "pass"


def test_an_empty_answer_is_visible_as_skipped():
    run = run_checks([CheckSpec(id="e", fn=lambda p: [])], _reg())
    assert [r.status for r in run.results] == ["skip"]


def test_an_empty_run_is_not_ok():
    """Нечего проверять — не «ИТОГ: OK»: зелёная проверка пустоты хуже красной."""
    from datum.checks.result import CheckRun

    run = CheckRun(())
    assert not run.ok and run.exit_code() == 1
    assert "НЕЧЕГО ПРОВЕРЯТЬ" in run.report()


def test_an_unknown_whose_value_is_zero_is_still_swept():
    """`p.value or v` при значении 0 выкидывал все точки диапазона, и параметр,
    решающий вердикт, объявлялся нечувствительным."""
    reg = ParamRegistry.from_dict({
        "LIP": {"value": 0.5, "unit": "mm", "provenance": "chosen", "reason": "-"},
        "OFFSET": {"value": 0.0, "unit": "mm", "provenance": "assumed",
                   "range": [-0.3, 0.3], "reason": "несоосность не мерили"},
    })

    def hold(p):
        v = p["LIP"] + p["OFFSET"]
        return CheckResult(id="зацеп", title="зацеп", status="pass" if v >= 0.4 else "fail",
                           message=f"{v:.2f}", value=v, op=">=", threshold=0.4)

    s = sweep_assumed([CheckSpec(id="зацеп", fn=hold)], reg)
    assert s.critical_params == ("OFFSET",)
    assert "OFFSET" not in s.harmless_params


def test_two_unknowns_that_only_matter_together_are_found():
    """зацеп = губка − зазор: каждый край по отдельности проходит, угол
    (тонкая губка, широкий зазор) — провал. По одному оба выходили
    «нечувствительными»."""
    reg = ParamRegistry.from_dict({
        "LIP": {"value": 0.8, "unit": "mm", "provenance": "assumed",
                "range": [0.6, 1.0], "reason": "-"},
        "GAP": {"value": 0.3, "unit": "mm", "provenance": "assumed",
                "range": [0.2, 0.5], "reason": "-"},
    })

    def hold(p):
        v = p["LIP"] - p["GAP"]
        return CheckResult(id="зацеп", title="зацеп", status="pass" if v >= 0.2 else "fail",
                           message=f"{v:.2f}", value=v, op=">=", threshold=0.2)

    s = sweep_assumed([CheckSpec(id="зацеп", fn=hold)], reg)
    assert not any(r.flips for r in s.rows), "по одному вердикт не меняется"
    assert set(s.critical_params) == {"LIP", "GAP"}
    assert s.harmless_params == ()
    assert "СОВМЕСТНО" in s.report()
