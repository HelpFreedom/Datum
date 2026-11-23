"""Проверки, считаемые МКЭ, — и они приходят со своим доказательством.

Претензия №4. Числу из МКЭ верят: оно приходит с шестью знаками после запятой
и выглядит измерением, хотя может быть завышено вдвое из-за грубой сетки.
Отличить по самому числу нельзя — значит числу обязаны сопутствовать два
свидетельства, и оба здесь строятся на каждый прогон:

  САМОПРОВЕРКА — решатель воспроизводит задачи с точным ответом (растяжение,
  чистый изгиб) с точностью округления. Не воспроизвёл — проверка не считает
  деталь вовсе, а отказывает: считать ей нечем.

  СХОДИМОСТЬ — ответ берётся не с одной сетки, а с лестницы, и сообщается
  ВИЛКОЙ. Согласованный МКЭ по перемещениям жёстче точного, поэтому вилка
  односторонняя: от посчитанного до экстраполяции.

И третье, ради чего всё и затевалось: ответ сверяется с АНАЛИТИЧЕСКОЙ
формулой. Два независимых способа, у каждого свои допущения. Совпали — доверия
больше, чем к любому поодиночке. Разошлись — расхождение само по себе находка,
и обычно виновато допущение формулы, а не решатель.

Так и вышло при первом же прогоне на дозаторе. Формула `flexure_force` считает
лист БАЛКОЙ, а лист шириной в 18 толщин ведёт себя как ПЛАСТИНА: поперечная
кривизна стеснена, и он жёстче в 1/(1−ν²) раз. Замерено МКЭ на лестнице
ширин: при двух толщинах формула верна до 1.5 %, при восемнадцати занижает
жёсткость на 4.7 %, при пятидесяти — на 7 с лишним.
"""

from __future__ import annotations

import math

import time

from datum.checks.result import Artifact, CheckResult
from datum.fem.model import Bar, FemError, Material
from datum.fem.solve import Divisions, guided_deflection
from datum.fem.verify import converge, verify

__all__ = ["flexure_force_fem"]

#: Самая мелкая сетка по умолчанию. Замерено: на флексуре дозатора лестница до
#: (20,8,2) стоит 1.1 с и даёт вилку 2.5…4.3 %, а вчетверо более мелкая — 8.9 с
#: и 3.2…3.6 %. Дешёвая вилка СОДЕРЖИТ дорогую, то есть не врёт, а лишь шире.
DEFAULT_DIVISIONS: Divisions = (20, 8, 2)

#: С какой ширины лист перестаёт быть балкой. Не порог природы, а граница
#: внимания: ниже неё расхождение с формулой тонет в погрешности сетки.
WIDE_LEAF = 8.0


def _even(value: int) -> int:
    """Ближайшее чётное не меньше двух.

    Число делений по ширине ОБЯЗАНО быть чётным: на плоскости симметрии y=0
    стоит закрепление, и при нечётном делении такой плоскости в сетке нет.
    Грубые ступени лестницы получаются делением, и там легко выходит
    единица — первая версия на этом и спотыкалась.
    """
    value = max(2, value)
    return value if value % 2 == 0 else value + 1


def _ladder(divisions: Divisions) -> tuple[Divisions, ...]:
    """Три сетки с измельчением вдвое ВДОЛЬ ДЛИНЫ.

    Толщина не дробится: сквозь неё и двух элементов P2 хватает — чистый изгиб
    на такой сетке воспроизводится точно, — а дробление даёт вытянутые
    элементы и портит сходимость. Замерено: дробление толщины до восьми
    делений дало порядок сходимости 1.17 против 1.5 без него, и вчетверо
    дороже.

    Отношение измельчения вдоль длины обязано быть ровно двойкой: по нему
    оценивается порядок сходимости.
    """
    nx, ny, nz = divisions
    if nx < 8:
        raise FemError(f"для лестницы сеток nx должно быть не меньше 8, получено {nx}")
    if nx % 4:
        # Иначе уровни выходят, скажем, 4 → 9 → 18: отношения 2.25 и 2, порядок
        # сходимости не оценивается, и вилка теряет основание.
        raise FemError(f"nx = {nx}: для лестницы сеток nx должно делиться на 4 "
                       f"(ближайшие: {nx - nx % 4}, {nx - nx % 4 + 4})")
    return tuple((max(2, nx // f), _even(ny // f), nz) for f in (4, 2, 1))


def flexure_force_fem(*, leaves: int, length: float, width: float, thickness: float,
                      modulus: float, deflection: float, poisson: float,
                      min_force: float = 0.0, max_force: float = float("inf"),
                      divisions: Divisions = DEFAULT_DIVISIONS,
                      name: str = "флексура", id: str = "",
                      depends_on: tuple[str, ...] = ()) -> CheckResult:
    """Сила пружины из листов — решённая, а не посчитанная по формуле.

    Аргументы те же, что у `checks.analysis.flexure_force`, чтобы сверять было
    чем. Добавлен только `poisson`: формуле он не нужен, а трёхмерной задаче
    нужен — и именно через него входит поправка на ширину листа.
    """
    t0 = time.perf_counter()
    check_id = id or f"{name}.сила_мкэ"
    if not deflection > 0:
        # Сила считается как жёсткость × прогиб и сверяется с формулой делением
        # на неё: при нуле выходило ZeroDivisionError, а не ответ.
        raise ValueError(f"прогиб {deflection:g} мм: нужен положительный")
    if leaves < 1:
        raise ValueError(f"листов {leaves}: нужен хотя бы один")
    material = Material(modulus, poisson)
    bar = Bar(length, width, thickness)

    # 1. Решатель обязан сперва доказать себя. Не доказал — считать нечем.
    self_check = verify(material)
    if not self_check.ok:
        return CheckResult(
            id=check_id, title="Сила флексуры (МКЭ)", group="fem", status="error",
            message=(f"{name}: решатель не воспроизвёл эталоны, числам про "
                     f"деталь верить нельзя.\n{self_check.report()}"),
            inputs=(name,), depends_on=depends_on,
            duration_ms=(time.perf_counter() - t0) * 1000)

    # 2. Прогиб под единичной силой — на лестнице сеток.
    def run(level: Divisions):
        solution = guided_deflection(bar, material, force=1.0, divisions=level)
        return -float(solution.at_face(0, length, 2).mean()), solution

    conv = converge(run, _ladder(divisions))

    # Жёсткость обратна прогибу, поэтому концы вилки МЕНЯЮТСЯ МЕСТАМИ: самый
    # мелкий расчёт даёт наибольшую жёсткость, экстраполяция — наименьшую.
    lo_d, hi_d = conv.band
    k_hi = leaves / lo_d
    k_lo = leaves / hi_d
    f_lo, f_hi = k_lo * deflection, k_hi * deflection

    # 3. Сверка с формулой.
    inertia = bar.inertia
    k_formula = leaves * 12 * modulus * inertia / length ** 3
    f_formula = k_formula * deflection
    gap_lo = 100 * (f_lo - f_formula) / f_formula
    gap_hi = 100 * (f_hi - f_formula) / f_formula

    # Вердикт по ВСЕЙ вилке. Если порог попал внутрь неё — проверка не молчит
    # и не выбирает удобный конец, а честно говорит, что на этой сетке ответ
    # не разделяется.
    below = f_lo < min_force
    above = f_hi > max_force
    if (below and f_hi < min_force) or (above and f_lo > max_force):
        status = "fail"
    elif below or above:
        status = "warn"
    else:
        status = "pass"

    reason = ""
    if width / thickness >= WIDE_LEAF and gap_lo > 0:
        reason = (f" Лист широкий ({width / thickness:.0f} толщин): поперечная "
                  f"кривизна стеснена, и он работает как пластина — формула "
                  f"балки занижает жёсткость.")

    verdict = {"pass": "в допуске", "warn": "порог попал ВНУТРЬ вилки — "
                                            "на этой сетке ответ не разделяется",
               "fail": "вне допуска"}[status]
    message = (
        f"{name}: сила {f_lo:.3f}…{f_hi:.3f} Н при прогибе {deflection:g} мм — "
        f"{verdict} (допуск {min_force:g}…{max_force:g}). "
        f"Формула даёт {f_formula:.3f} Н, расхождение {gap_lo:+.1f}…{gap_hi:+.1f} %."
        f"{reason} "
        f"Прогиб листа под 1 Н, мм, по сеткам: {conv.line()}. "
        f"Эталоны решателя: "
        + ", ".join(f"{c.name} {c.error:.0e}" for c in self_check.cases))

    # Вердикт выносится по ВСЕЙ вилке и по обоим порогам — значит и число в
    # результате обязано быть тем концом вилки, что ближе к нарушению, а порог —
    # диапазоном. Раньше отдавались нижний конец и один `min_force`: провал по
    # верхнему порогу выглядел с положительным запасом и уезжал в хвост списка.
    value = f_hi if (above or (not below and max_force - f_hi < f_lo - min_force)) else f_lo
    threshold = (min_force, None if math.isinf(max_force) else max_force)
    return CheckResult(
        id=check_id, title="Сила флексуры (МКЭ)", group="fem", status=status,
        message=message, value=value, unit="Н", op="in", threshold=threshold,
        inputs=(name,), depends_on=depends_on,
        evidence=(*_convergence_picture(conv, f_formula, (f_lo, f_hi), leaves,
                                        deflection, name, check_id),
                  Artifact(kind="json", caption="сходимость и самопроверка",
                           data={"force_band": [f_lo, f_hi],
                                 "formula": f_formula,
                                 "stiffness_band": [k_lo, k_hi],
                                 "deflections": list(conv.values),
                                 "dofs": list(conv.dofs),
                                 "order": conv.order,
                                 "self_check": {c.name: c.error
                                                for c in self_check.cases}})),
        duration_ms=(time.perf_counter() - t0) * 1000)


def _convergence_picture(conv, formula: float, band: tuple[float, float],
                         leaves: int, deflection: float,
                         name: str, check_id: str) -> tuple:
    """Лестница сеток картинкой: видно, СОШЁЛСЯ ли ответ.

    По числам «5.44 → 5.90 → 6.07» приходится считать в уме, замедляется ли
    приращение. На картинке это видно сразу — как и то, где лежит ответ
    формулы относительно вилки.

    Ось степеней свободы логарифмическая: они растут в разы, и на равномерной
    три точки сбиваются в угол.
    """
    try:
        from datum.viz.chart import Band, Mark, Series, render_chart

        forces = [leaves / value * deflection for value in conv.values]
        lo, hi = band
        extra = []
        if conv.extrapolated is not None:
            extra = [Series([conv.dofs[-1]], [leaves / conv.extrapolated * deflection],
                            label="экстраполяция", colour=(224, 175, 104), dots=True)]
        png = render_chart(
            [Series(conv.dofs, forces, label="по сеткам", dots=True), *extra],
            title=f"{check_id} — сходимость по сеткам",
            note=(f"{name}: ответ {lo:.3f}…{hi:.3f} Н, формула {formula:.3f} Н; "
                  f"шевелится на {conv.change * 100:.2f} %"),
            x_label="степеней свободы", y_label="сила, Н", x_log=True,
            marks=[Mark(label=f"формула {formula:.3f}", y=formula,
                        colour=(158, 206, 106))],
            bands=[Band(lo, hi, "вилка ответа")])
    except Exception as e:
        return (Artifact(kind="text",
                         caption=f"график не построился: {type(e).__name__}: {e}"),)
    return (Artifact(kind="png", caption=f"{name}: сходимость по сеткам", data=png),)
