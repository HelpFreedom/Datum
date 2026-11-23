"""Решатель и его самопроверка — претензия №4.

Беда, которую это закрывает: числу из МКЭ верят. Оно приходит с шестью знаками
после запятой и выглядит измерением, хотя может быть завышено вдвое из-за
грубой сетки. Отличить по самому числу нельзя — значит решатель обязан
доказывать себя сам.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from datum.fem import solve as S
from datum.fem import verify as V
from datum.fem.model import Bar, FemError, Material

BAR = Bar(length=40.0, width=10.0, thickness=2.0)
PLA = Material(modulus=2000.0, poisson=0.35, name="проба")


# ------------------------------------------------------------------ постановка
@pytest.mark.parametrize("modulus,poisson", [(0.0, 0.3), (-1.0, 0.3),
                                             (2000.0, 0.5), (2000.0, -0.1),
                                             (float("inf"), 0.3)])
def test_a_nonsensical_material_is_refused(modulus, poisson):
    with pytest.raises(FemError):
        Material(modulus, poisson)


def test_poisson_at_one_half_is_refused_with_the_reason():
    """При 0.5 материал несжимаем и λ обращается в бесконечность. Молча
    получить inf в матрице жёсткости — худший из исходов."""
    with pytest.raises(FemError, match="несжимаем"):
        Material(2000.0, 0.5)


@pytest.mark.parametrize("dims", [(0.0, 1.0, 1.0), (1.0, -1.0, 1.0), (1.0, 1.0, 0.0)])
def test_a_nonsensical_bar_is_refused(dims):
    with pytest.raises(FemError):
        Bar(*dims)


def test_an_odd_number_of_divisions_is_refused():
    """Плоскости симметрии y=0 и z=0 обязаны быть плоскостями УЗЛОВ: на них
    стоят закрепления. При нечётном делении такой плоскости нет, условие
    ложится в пустоту, жёсткое смещение остаётся свободным — первая версия
    модуля так и выдала nan."""
    with pytest.raises(FemError, match="чётным"):
        S.tension(BAR, PLA, stress=1.0, divisions=(4, 3, 2))
    with pytest.raises(FemError, match="чётным"):
        S.tension(BAR, PLA, stress=1.0, divisions=(4, 2, 1))


# ------------------------------------------------------------------ точность
def test_tension_is_exact():
    """Точное решение ЛИНЕЙНО по координатам — его обязан воспроизводить любой
    элемент с точностью округления. Самая надёжная проверка машинерии."""
    stress = 5.0
    sol = S.tension(BAR, PLA, stress=stress, divisions=(4, 2, 2))
    got = float(sol.at_face(0, BAR.length, 0).mean())
    assert got == pytest.approx(stress * BAR.length / PLA.modulus, rel=1e-10)


def test_the_strain_field_is_exact_too():
    """Перемещения могут быть верны, а деформации посчитаны не так — а именно
    они идут в проверки прочности."""
    stress = 5.0
    sol = S.tension(BAR, PLA, stress=stress, divisions=(4, 2, 2))
    assert sol.peak_strain == pytest.approx(stress / PLA.modulus, rel=1e-10)


def test_pure_bending_is_exact_for_the_whole_field():
    """Точное решение КВАДРАТИЧНО, а P2 квадратики представляет точно.
    Сравнивается всё поле: одна точка могла бы совпасть случайно."""
    moment = 100.0
    sol = S.pure_bending(BAR, PLA, moment=moment, divisions=(4, 2, 2))
    loc = sol.locations(2)
    exact = -(moment / (2 * PLA.modulus * BAR.inertia)) * (
        loc[0] ** 2 + PLA.poisson * (loc[2] ** 2 - loc[1] ** 2))
    error = np.abs(sol.component(2) - exact).max() / np.abs(exact).max()
    assert error < 1e-8, f"ошибка поля {error:.2e}"


def test_the_self_check_passes_and_says_so():
    report = V.verify(PLA)
    assert report.ok, report.report()
    assert len(report.cases) == 3
    assert "воспроизводит эталоны" in report.report()


def test_the_self_check_catches_a_broken_element():
    """Саботаж: подменить P2 линейным элементом.

    Так и проверено при разработке — и результат ровно тот, что предсказывает
    теория: растяжение (решение линейно) проходит, изгиб (квадратично)
    валится на 96 %. Обе ступени лестницы нужны: поодиночке ни одна этого бы
    не поймала.
    """
    import skfem

    original = S._element

    def linear():
        return skfem.ElementVector(skfem.ElementTetP1())

    S._element = linear
    try:
        report = V.verify(PLA)
    finally:
        S._element = original

    assert not report.ok
    names = {c.name: c.ok for c in report.cases}
    assert names["растяжение"] is True, "линейное решение обязан дать и P1"
    assert names["чистый изгиб (поле)"] is False, "изгиб P1 представить не может"
    assert "верить нельзя" in report.report()


# ------------------------------------------------------------------ сходимость
def _sequence(exact: float, coefficient: float, power: float):
    """Последовательность с ИЗВЕСТНЫМ пределом: v(h) = exact − c·h^power."""

    def run(divisions):
        h = 1.0 / divisions[0]
        return (exact - coefficient * h ** power,
                S.Solution(u=None, doflocs=None, dofs=divisions[0], ms=0.0))

    return run


LADDER = ((5, 2, 2), (10, 4, 4), (20, 8, 8))


@pytest.mark.parametrize("power", [2.0, 3.0])
@pytest.mark.parametrize("coefficient", [3.0, -3.0])
def test_richardson_finds_the_known_limit(power, coefficient):
    """Знак в экстраполяции — та ещё ловушка, и я в неё попал.

    v* = v₃ + (v₃ − v₂)/(rᵖ − 1). С перепутанным знаком экстраполяция уезжала
    В СТОРОНУ, ПРОТИВОПОЛОЖНУЮ сходимости: на растущей последовательности
    выдавала число МЕНЬШЕ самого точного из посчитанных. Поэтому предел здесь
    известен заранее, и проверяются обе стороны.
    """
    c = V.converge(_sequence(10.0, coefficient, power), LADDER)
    assert c.order == pytest.approx(power, rel=1e-6)
    assert c.extrapolated == pytest.approx(10.0, abs=1e-9)


def test_the_answer_is_the_finest_mesh_and_not_the_extrapolation():
    """Экстраполяция неустойчива: на флексуре дозатора три разные лестницы дали
    24.13, 24.60 и 24.74 — разброс 2.5 %. Отдавать её за ответ значило бы
    вернуться к уверенной цифре неизвестной верности."""
    c = V.converge(_sequence(10.0, 3.0, 2.0), LADDER)
    assert c.value == c.values[-1]
    assert c.value != c.extrapolated


def test_the_band_is_between_the_computed_and_the_extrapolated():
    c = V.converge(_sequence(10.0, 3.0, 2.0), LADDER)
    lo, hi = c.band
    assert lo <= hi
    assert lo == pytest.approx(min(c.values[-1], c.extrapolated))
    assert hi == pytest.approx(max(c.values[-1], c.extrapolated))


def test_a_nonsensical_order_is_not_extrapolated():
    """Порядок оценивается, а не предполагается. Если оценка вышла
    бессмысленной — экстраполяции нет вовсе, а не подставленная теоретическая."""
    values = iter([1.0, 5.0, 1.2])       # скачет: сходимостью тут не пахнет

    def run(divisions):
        return next(values), S.Solution(u=None, doflocs=None, dofs=1, ms=0.0)

    c = V.converge(run, LADDER)
    assert c.order is None and c.extrapolated is None
    assert c.value == 1.2


def test_two_levels_give_a_change_but_no_order():
    c = V.converge(_sequence(10.0, 3.0, 2.0), LADDER[:2])
    assert c.order is None and c.extrapolated is None
    assert 0 < c.change < 1


def test_fewer_than_two_levels_is_refused():
    with pytest.raises(ValueError, match="двух сеток"):
        V.converge(_sequence(10.0, 3.0, 2.0), LADDER[:1])


# ------------------------------------------------------------------ физика
def test_a_wide_leaf_is_stiffer_than_the_beam_formula_says():
    """Находка, ради которой сверка с аналитикой и нужна.

    У ШИРОКОГО листа поперечная кривизна стеснена, и он ведёт себя как
    пластина: жёстче балки в 1/(1−ν²) раз. Замерено на лестнице сеток: при
    ширине вдвое больше толщины формула верна до 0.7 %, при восемнадцати
    занижает жёсткость на 3.6 %, при пятидесяти — на 7.3 %.

    Сетки здесь скромные — тест проверяет ТЕНДЕНЦИЮ, а не третий знак:
    широкий лист обязан отстоять от балочной формулы дальше узкого.
    """
    length, thickness = 70.0, 1.0
    material = Material(3000.0, 0.35)

    def gap(width, divisions):
        bar = Bar(length, width, thickness)
        beam = length ** 3 / (3 * material.modulus * bar.inertia)
        sol = S.tip_load(bar, material, force=1.0, divisions=divisions)
        got = -float(sol.at_face(0, length, 2).mean())
        return (beam - got) / beam

    narrow = gap(2.0, (20, 4, 2))
    wide = gap(50.0, (20, 12, 2))
    assert 0 < narrow < 0.03, f"узкий лист обязан совпадать с балкой, разрыв {narrow:.3f}"
    assert wide > narrow + 0.02, (
        f"широкий лист обязан быть заметно жёстче балочной формулы: "
        f"узкий {narrow:.3f}, широкий {wide:.3f}")


# ------------------------------------------------------------------ правила
def test_no_material_constants_live_in_the_solver():
    """То же правило, что в `checks/analysis.py`: свойства материала приходят
    аргументами. Спрятанные в коде, они становятся невидимым допущением."""
    for module in (S, V, __import__("datum.fem.model", fromlist=["x"])):
        source = inspect.getsource(module)
        for suspicious in ('PLA"', "PETG\"", "= 3000", "= 2000.0", "MATERIALS",
                           "modulus = 2", "poisson = 0.3"):
            assert suspicious not in source, (
                f"в {module.__name__} завелась константа материала: {suspicious!r}")


# ------------------------------------------------------- проверка целиком
from datum.checks.fem import _ladder, flexure_force_fem  # noqa: E402

#: Мелкая сетка нарочно: тесты проверяют ПОВЕДЕНИЕ проверки, а не третий знак.
FAST = (8, 4, 2)


def _json_of(result):
    """Доказательство с числами — ПО ВИДУ, а не по порядку.

    Порядок однажды уже сменился: рядом с json появилась картинка, и три
    теста легли на `evidence[0]`. Брать по индексу — значит закрепить порядок,
    который никто не обещал.
    """
    found = [a for a in result.evidence if a.kind == "json"]
    assert found, [a.kind for a in result.evidence]
    return found[0].data


def _leaf(**over):
    args = dict(leaves=2, length=70.0, width=18.0, thickness=1.0,
                modulus=3000.0, poisson=0.35, deflection=6.0, min_force=1.0,
                divisions=FAST, name="флексура")
    args.update(over)
    return flexure_force_fem(**args)


def test_the_ladder_keeps_the_width_divisions_even():
    """Грубые ступени получаются делением, и там легко выходит единица — а при
    нечётном делении в сетке нет плоскости симметрии, на которой стоит
    закрепление. Первая версия лестницы на этом и спотыкалась."""
    for divisions in ((20, 8, 2), (8, 4, 2), (16, 4, 2), (12, 2, 2)):
        for level in _ladder(divisions):
            assert level[1] % 2 == 0 and level[1] >= 2, level
            assert level[2] % 2 == 0
    # Отношение измельчения вдоль длины — ровно двойка: по нему оценивается
    # порядок сходимости.
    levels = _ladder((20, 8, 2))
    assert levels[1][0] / levels[0][0] == 2
    assert levels[2][0] / levels[1][0] == 2


def test_a_ladder_needs_room_to_coarsen():
    with pytest.raises(FemError, match="не меньше 8"):
        _ladder((4, 4, 2))


def test_the_answer_is_a_band_and_the_formula_is_named():
    result = _leaf()
    assert result.status in {"pass", "warn"}
    assert "…" in result.message, "ответ обязан быть вилкой, а не одним числом"
    assert "Формула даёт" in result.message
    assert "Эталоны решателя" in result.message, "число обязано приходить с доказательством"


def test_a_wide_leaf_is_reported_stiffer_than_the_formula():
    """Находка дозатора: лист шириной в 18 толщин работает как пластина."""
    result = _leaf()
    data = _json_of(result)
    assert min(data["force_band"]) > data["formula"], (
        "широкий лист обязан выйти жёстче балочной формулы")
    assert "пластина" in result.message


def test_a_narrow_leaf_does_not_get_the_plate_explanation():
    """Объяснение про пластину не должно появляться там, где его нет: у узкого
    листа формула верна, и приписывать расхождение ширине значило бы
    объяснять шум."""
    result = _leaf(width=2.0)
    assert "пластина" not in result.message


def test_a_threshold_inside_the_band_is_not_decided():
    """Самое важное поведение. Если порог попал ВНУТРЬ вилки, проверка не
    выбирает удобный конец и не молчит, а говорит, что на этой сетке ответ не
    разделяется."""
    rough = _leaf()
    lo, hi = _json_of(rough)["force_band"]
    middle = (lo + hi) / 2
    result = _leaf(min_force=middle)
    assert result.status == "warn"
    assert "не разделяется" in result.message


def test_a_force_below_the_whole_band_fails():
    result = _leaf(min_force=100.0)
    assert result.status == "fail"


def test_a_broken_solver_refuses_to_speak_about_the_part():
    """Не доказал себя на эталонах — не считает деталь. Тут это важнее, чем
    где-либо: ошибиться молча — ровно та беда, от которой весь модуль."""
    import skfem

    original = S._element
    S._element = lambda: skfem.ElementVector(skfem.ElementTetP1())
    try:
        result = _leaf()
    finally:
        S._element = original

    assert result.status == "error"
    assert "не воспроизвёл эталоны" in result.message
    assert result.value is None, "числа про деталь быть не должно вовсе"


def test_the_evidence_carries_the_whole_ladder():
    data = _json_of(_leaf())
    assert len(data["deflections"]) == 3 and len(data["dofs"]) == 3
    assert data["dofs"][0] < data["dofs"][-1]
    assert set(data["self_check"]) == {"растяжение", "деформация при растяжении",
                                       "чистый изгиб (поле)"}



# --------------------------------------------- вилка не схлопывается в точку
def test_without_extrapolation_the_band_still_has_width():
    """Раньше без экстраполяции вилка становилась (v, v) — уверенный ответ
    ровно тогда, когда неопределённость наибольшая."""
    values = iter([1.0, 1.1, 1.19])        # порядок 0.15 — вне разумного
    def run(divisions):
        return next(values), S.Solution(u=None, doflocs=None, dofs=1, ms=0.0)

    c = V.converge(run, LADDER)
    assert c.extrapolated is None
    lo, hi = c.band
    assert lo == pytest.approx(1.19) and hi > 1.19 + 0.08
    assert "не оценён" in c.band_basis


def test_a_non_monotone_ladder_gives_a_two_sided_band():
    values = iter([1.0, 5.0, 1.2])
    def run(divisions):
        return next(values), S.Solution(u=None, doflocs=None, dofs=1, ms=0.0)

    c = V.converge(run, LADDER)
    lo, hi = c.band
    assert lo < 1.2 < hi and "немонотонна" in c.band_basis


def test_two_levels_give_a_band_too():
    c = V.converge(_sequence(10.0, 3.0, 2.0), LADDER[:2])
    lo, hi = c.band
    assert hi > lo


def test_a_ladder_with_uneven_refinement_is_refused():
    """nx = 18 давал уровни 4 → 9 → 18: отношения 2.25 и 2, порядок не
    оценивался, и вилка молча теряла основание."""
    from datum.checks.fem import _ladder
    from datum.fem.model import FemError

    with pytest.raises(FemError, match="делиться на 4"):
        _ladder((18, 8, 2))
    assert _ladder((16, 8, 2))[0][0] * 4 == 16


def test_a_flexure_too_stiff_reports_a_negative_margin():
    """Провал по верхнему порогу раньше выглядел с положительным запасом: в
    результат шли нижний конец вилки и один `min_force`."""
    from datum.checks.fem import flexure_force_fem

    r = flexure_force_fem(leaves=2, length=70, width=18, thickness=1, modulus=3000,
                          poisson=0.35, deflection=6, max_force=1.0, divisions=(16, 4, 2))
    assert r.status == "fail"
    assert r.op == "in" and r.margin < 0


def test_a_zero_deflection_is_refused_not_divided_by():
    from datum.checks.fem import flexure_force_fem

    with pytest.raises(ValueError, match="прогиб"):
        flexure_force_fem(leaves=2, length=70, width=18, thickness=1, modulus=3000,
                          poisson=0.35, deflection=0)
