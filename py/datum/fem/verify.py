"""Самопроверка решателя — претензия №4.

Беда, которую это закрывает: числу из МКЭ верят. Оно приходит с шестью
знаками после запятой, на красивой картинке, и выглядит измерением — хотя
может быть завышено вдвое из-за грубой сетки или не иметь смысла вовсе из-за
перепутанного закрепления. Отличить одно от другого по самому числу нельзя.

Поэтому решатель здесь обязан доказывать себя, и доказывать ЖЁСТКО.

ДВА ЭТАЛОНА С ТОЧНЫМ ОТВЕТОМ. Не «похоже на правду», а совпадение до
округления — потому что у обеих задач трёхмерное решение представимо
элементом ТОЧНО:

  одноосное растяжение — решение линейно по координатам;
  чистый изгиб моментом — решение квадратично, а P2 квадратики представляет.

Отсюда пороги не подгоняются: замерено 1e-14 у растяжения и 1e-11…1e-9 у
изгиба, а разрешено 1e-9 и 1e-6. Всё, что хуже, — не «чуть менее точно», а
сломано. Для сравнения, линейные тетраэдры на том же изгибе ошибаются на
66…96 %, и такой порог их не пропустит никогда.

ЧЕГО ЭТАЛОНАМИ НЕ ПРОВЕРИШЬ. Консоль с силой на конце сравнивать с
P·L³/(3EI) и ждать совпадения НЕЛЬЗЯ: замерено −2.5 %, и разрыв не уходит с
измельчением. Причина в постановке, а не в сетке — защемлённая трёхмерная
грань жёстче балочной заделки, а формула пренебрегает сдвигом. Для таких
величин ответ даёт не эталон, а СХОДИМОСТЬ: считаем на лестнице сеток и
сообщаем, насколько ответ ещё шевелится. Если шевелится сильно — так и
сказано, а не выдано красивое число.

ЗАМЕР ПРО РЕШАТЕЛЬ, чтобы не пробовали вслепую. Многосеточный метод (pyamg)
на этих задачах не окупается: 934 мс против 124 у прямого на 5 тыс. степеней
свободы, вровень на 35 тыс. Мало того, без явно переданных жёстких движений
тела он не сходится вовсе и молча возвращает ответ, ошибочный на 29…87 %.
Перестановки MMD вместо COLAMD тоже не помогли. Прямой решатель с умолчанием
и есть лучшее, что здесь есть; практический предел — 17 тыс. степеней свободы
за 1.7 с.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from datum.fem.model import Bar, FemError, Material
from datum.fem.solve import Divisions, Solution, pure_bending, tension

__all__ = ["Verification", "CaseResult", "verify", "patch_test",
           "converge", "Convergence"]

#: Брус для эталонов. Тонкий и длинный нарочно: на таком линейные элементы
#: запираются особенно заметно, и подмена элемента не пройдёт незамеченной.
REFERENCE_BAR = Bar(length=40.0, width=10.0, thickness=2.0)

#: Пороги. Это уровень округления, а не «достаточная точность»: см. заголовок.
TOL_TENSION = 1e-9
TOL_BENDING = 1e-6


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    error: float
    tolerance: float
    dofs: int
    ms: float
    note: str = ""

    @property
    def ok(self) -> bool:
        return math.isfinite(self.error) and self.error <= self.tolerance

    def line(self) -> str:
        mark = "OK  " if self.ok else "ПЛОХО"
        return (f"  {mark} {self.name:22} ошибка {self.error:.2e} "
                f"{'≤' if self.ok else '>'} {self.tolerance:.0e}  "
                f"({self.dofs} степ. свободы, {self.ms:.0f} мс)"
                + (f" — {self.note}" if self.note else ""))


@dataclass(frozen=True, slots=True)
class Verification:
    cases: tuple[CaseResult, ...] = ()
    material: str = ""

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.cases)

    def report(self) -> str:
        head = ("Самопроверка решателя"
                + (f" на материале {self.material}" if self.material else ""))
        tail = ("ИТОГ: решатель воспроизводит эталоны с точностью округления"
                if self.ok else
                "ИТОГ: ЭТАЛОНЫ НЕ ВОСПРОИЗВЕДЕНЫ — числам про настоящую деталь верить нельзя")
        return "\n".join([head, *(c.line() for c in self.cases), tail])


def verify(material: Material, *, bar: Bar | None = None,
           divisions: Divisions = (4, 2, 2)) -> Verification:
    """Прогнать эталоны. Дёшево: две задачи по полсотни миллисекунд."""
    bar = bar or REFERENCE_BAR
    cases: list[CaseResult] = []

    # --- растяжение: удлинение обязано равняться σ·L/E, деформация — σ/E
    stress = 5.0
    sol = tension(bar, material, stress=stress, divisions=divisions)
    got = float(sol.at_face(0, bar.length, 0).mean())
    want = stress * bar.length / material.modulus
    cases.append(CaseResult("растяжение", abs(got - want) / abs(want),
                            TOL_TENSION, sol.dofs, sol.ms,
                            note=f"удлинение {got:.8f} мм"))

    # Деформация — отдельная проверка: перемещения могут быть верны, а поле
    # деформаций посчитано не так, и именно оно идёт в проверки прочности.
    want_strain = stress / material.modulus
    cases.append(CaseResult("деформация при растяжении",
                            abs(sol.peak_strain - want_strain) / want_strain,
                            TOL_TENSION, sol.dofs, 0.0,
                            note=f"ε = {sol.peak_strain:.8f}"))

    # --- чистый изгиб: сравнивается ВСЁ поле прогиба, а не одна точка.
    # Одна точка могла бы совпасть случайно; поле целиком — нет.
    moment = 100.0
    sol = pure_bending(bar, material, moment=moment, divisions=divisions)
    loc = sol.locations(2)
    exact = -(moment / (2 * material.modulus * bar.inertia)) * (
        loc[0] ** 2 + material.poisson * (loc[2] ** 2 - loc[1] ** 2))
    scale = float(np.abs(exact).max())
    error = float(np.abs(sol.component(2) - exact).max() / scale)
    # Прогиб НА ОСИ, а не наибольший по телу: последний включает слагаемое
    # ν(z²−y²) с краёв сечения и отличается от балочного M·L²/(2EI) на доли
    # процента. Подписать его «прогибом на конце» значило бы соврать на ровном
    # месте — в модуле, весь смысл которого в том, чтобы не врать.
    tip = moment * bar.length ** 2 / (2 * material.modulus * bar.inertia)
    cases.append(CaseResult("чистый изгиб (поле)", error, TOL_BENDING,
                            sol.dofs, sol.ms,
                            note=f"прогиб на оси {tip:.6f} мм"))

    return Verification(cases=tuple(cases), material=material.name)


# ------------------------------------------------------------------ сходимость
@dataclass(frozen=True, slots=True)
class Convergence:
    """Как величина ведёт себя при измельчении сетки."""

    values: tuple[float, ...] = ()
    dofs: tuple[int, ...] = ()
    ms: tuple[float, ...] = ()
    levels: tuple[Divisions, ...] = ()
    #: Наблюдённый порядок сходимости. None — оценить не удалось.
    order: float | None = None
    extrapolated: float | None = None

    @property
    def value(self) -> float:
        """Посчитанное на самой мелкой сетке — и только оно.

        Экстраполяцию сюда НЕ подставляем, хотя соблазн велик. Замерено на
        флексуре дозатора: три разные лестницы сеток дали экстраполяции
        24.13, 24.60 и 24.74 — разброс в 2.5 %, потому что оценка порядка
        сходимости сама неустойчива. Отдавать такое число за ответ значит
        вернуться к тому, от чего весь модуль: к уверенной цифре неизвестной
        верности. Экстраполяция остаётся отдельным полем и входит в ВИЛКУ.
        """
        return self.values[-1]

    @property
    def ratio(self) -> float | None:
        """Отношение измельчения между двумя самыми мелкими сетками."""
        if len(self.levels) < 2 or not self.levels[-2][0]:
            return None
        return self.levels[-1][0] / self.levels[-2][0]

    @property
    def monotone(self) -> bool:
        """Последние разности одного знака — ответ идёт в одну сторону."""
        if len(self.values) < 3:
            return True
        d1 = self.values[-2] - self.values[-3]
        d2 = self.values[-1] - self.values[-2]
        return d1 * d2 >= 0

    @property
    def band_basis(self) -> str:
        """На чём держится дальняя граница вилки — словами, для отчёта."""
        if len(self.values) < 2:
            return "одна сетка — вилки нет"
        if not self.monotone:
            return "сходимость немонотонна — вилка в обе стороны на последнее изменение"
        if self.extrapolated is not None:
            return "до экстраполяции"
        return "порядок не оценён — остаток взят как при порядке 1"

    @property
    def band(self) -> tuple[float, float]:
        """Вилка, в которой лежит ответ.

        Согласованный МКЭ по перемещениям всегда ЖЁСТЧЕ точного: энергия
        минимизируется по подпространству, поэтому посчитанное перемещение —
        оценка снизу, и истина лежит за ним ПО ХОДУ сходимости. Поэтому вилка —
        от самого мелкого расчёта до экстраполяции, а не «плюс-минус».

        Проверено на листе флексуры дозатора (70×18×1): лестница по умолчанию
        даёт вилку 6.069…6.165, а мелкие сетки сходятся к 6.141 → 6.145 (до
        250 тыс. степеней свободы; с вдвое более мелким делением толщины —
        ещё на 0.002 выше). Истина внутри, экстраполяция перелетает с запасом.

        НЕ СХЛОПЫВАЕТСЯ В ТОЧКУ. Раньше без экстраполяции вилка становилась
        (v, v) — ровно тогда, когда неопределённость наибольшая: сходимость
        немонотонна, порядок вне разумного, сетки измельчены неравномерно.
        Проверка выдавала уверенный вердикт, а «порог внутри вилки» становился
        недостижим. Теперь без экстраполяции остаток берётся как при первом
        порядке — d/(r − 1), не меньше последнего изменения при r = 2, — а при
        немонотонной сходимости вилка идёт в обе стороны.
        """
        last = self.values[-1]
        if len(self.values) < 2:
            return (last, last)
        d = last - self.values[-2]
        if not self.monotone:
            return (last - abs(d), last + abs(d))
        if self.extrapolated is not None:
            far = self.extrapolated
        else:
            r = self.ratio if self.ratio and self.ratio > 1 else 2.0
            far = last + d / (r - 1)
        lo, hi = sorted((last, far))
        return (lo, hi)

    @property
    def change(self) -> float:
        """Относительное изменение между двумя самыми мелкими сетками.

        Это и есть честная мера остаточной неопределённости: без допущения о
        порядке сходимости, просто «насколько ответ ещё шевелится».
        """
        if len(self.values) < 2:
            return float("inf")
        last = self.values[-1]
        return abs(self.values[-1] - self.values[-2]) / max(abs(last), 1e-30)

    def line(self) -> str:
        parts = [f"{v:.6g} ({n} степ. свободы)" for v, n in zip(self.values, self.dofs)]
        tail = f", шевелится на {self.change * 100:.2f} %"
        if self.order is not None and self.extrapolated is not None:
            tail += (f"; порядок {self.order:.2f}, экстраполяция "
                     f"{self.extrapolated:.6g}")
        tail += f"; вилка: {self.band_basis}"
        return " → ".join(parts) + tail


def converge(run: Callable[[Divisions], tuple[float, Solution]],
             levels: tuple[Divisions, ...]) -> Convergence:
    """Прогнать величину на лестнице сеток.

    `run(divisions)` обязан вернуть пару (величина, решение): решение нужно
    ради числа степеней свободы и времени — без них лестница не читается.

    Порядок сходимости НЕ ПРЕДПОЛАГАЕТСЯ, а оценивается по трём уровням:

        p = ln((v₁−v₂)/(v₂−v₃)) / ln(r)

    и только если оценка вышла осмысленной (0.5 ≤ p ≤ 6) — экстраполяция.
    Подставить теоретический порядок было бы соблазнительно и неверно: он
    верен для гладкого решения на равномерно измельчаемой сетке, а стоит
    появиться особенности в углу заделки — и экстраполяция уедет в сторону,
    выдав уверенное неверное число. Ровно то, от чего вся эта затея.
    """
    if len(levels) < 2:
        raise ValueError("для оценки сходимости нужно не меньше двух сеток")

    values, dofs, times = [], [], []
    for level in levels:
        value, solution = run(level)
        values.append(float(value))
        dofs.append(solution.dofs)
        times.append(solution.ms)

    order = extrapolated = None
    if len(values) >= 3:
        d1, d2 = values[-3] - values[-2], values[-2] - values[-1]
        # Отношение измельчения берётся по числу делений вдоль длины: сетки
        # измельчаются равномерно, иначе оценка порядка не имеет смысла.
        r = levels[-2][0] / levels[-3][0]
        ratio = levels[-1][0] / levels[-2][0]
        if abs(d2) > 1e-14 and abs(d1) > 1e-14 and math.isclose(r, ratio, rel_tol=1e-6):
            quotient = d1 / d2
            if quotient > 0:
                p = math.log(quotient) / math.log(r)
                if 0.5 <= p <= 6.0:
                    order = p
                    # ЗНАК. v* = v₃ + (v₃ − v₂)/(rᵖ − 1), а d2 = v₂ − v₃,
                    # поэтому здесь МИНУС. С плюсом экстраполяция уезжала в
                    # сторону, противоположную сходимости: на растущей
                    # последовательности выдавала число МЕНЬШЕ самого точного
                    # из посчитанных. Заперто тестом на последовательности с
                    # известным пределом.
                    extrapolated = values[-1] - d2 / (r ** p - 1)

    return Convergence(values=tuple(values), dofs=tuple(dofs), ms=tuple(times),
                       levels=tuple(levels), order=order, extrapolated=extrapolated)


# ------------------------------------------------------- эталон для любой сетки
#: Деформация и кривизна для пробных полей. Абсолютная величина в относительной
#: ошибке сокращается; числа взяты малыми, чтобы остаться в линейной области.
PATCH_STRAIN = 1e-3
PATCH_CURVATURE = 1e-4

#: Порог теста заплаты. Замерено: P2 даёт 1e-15 на структурированной сетке и
#: 1.3e-15 на сетке fTetWild, P1 на неструктурированной — 1.3e-3.
TOL_PATCH = 1e-9


def _linear_field(p: np.ndarray, poisson: float) -> np.ndarray:
    """Однородное растяжение. Поле ЛИНЕЙНО и является точным решением."""
    e = PATCH_STRAIN
    return np.stack([e * p[0], -poisson * e * p[1], -poisson * e * p[2]])


def _quadratic_field(p: np.ndarray, poisson: float) -> np.ndarray:
    """Чистый изгиб. Поле КВАДРАТИЧНО и тоже точное решение — во всём
    пространстве, а не только в брусе, поэтому годится для любого тела."""
    k = PATCH_CURVATURE
    return np.stack([-k * p[0] * p[2], poisson * k * p[1] * p[2],
                     k / 2 * (p[0] ** 2 + poisson * (p[2] ** 2 - p[1] ** 2))])


def _patch_system(mesh, material: Material, element=None, stiffness=None):
    """Базис, матрица и закрепление теста заплаты — ОДНИ на оба поля: граница та же,
    меняются только значения на ней. Матрицу можно передать готовой (та же сетка и
    материал, что у задачи): сборка P2 — секунды, и дважды её делать незачем."""
    from skfem import Basis

    from datum.fem.solve import _element, _stiffness

    basis = Basis(mesh, element() if element else _element())
    K = stiffness if stiffness is not None else _stiffness(basis, material)
    fixed = np.unique(basis.get_dofs().flatten())
    inner = np.setdiff1d(np.arange(basis.N), fixed)
    if len(inner) == 0:
        raise FemError("в сетке нет ни одного внутреннего узла: "
                       "тест заплаты на ней ничего не проверяет")
    return basis, K, fixed, inner


def _patch(mesh, material: Material, field, element=None, *, system=None,
           factor=None) -> tuple[float, int, float]:
    """Задать точное решение на ВСЕЙ границе и посмотреть, что вышло внутри."""
    import time

    from datum.fem.solve import spd_factor

    t0 = time.perf_counter()
    basis, K, fixed, inner = system or _patch_system(mesh, material, element)
    component = np.arange(basis.N) % 3
    exact = field(basis.doflocs, material.poisson)[component, np.arange(basis.N)]
    # Внутренняя система: K_II·u_I = −K_IB·u_B (то же, что condense, без копий K).
    rhs = -(K[inner][:, fixed] @ exact[fixed])
    solve = factor or spd_factor(K[inner][:, inner])
    u_inner = solve(rhs)
    error = float(np.abs(u_inner - exact[inner]).max() / np.abs(exact).max())
    return error, len(inner), (time.perf_counter() - t0) * 1000


def patch_test(mesh, material: Material, *, element=None, stiffness=None) -> Verification:
    """Тест заплаты: точное решение на границе обязано воспроизвестись внутри.

    Эталоны из `verify()` держатся на плоскостях симметрии и годятся только
    для структурированной сетки бруса. У сетки произвольного тела таких
    плоскостей нет вовсе — у бруска 40×10×2, разбитого fTetWild, на плоскость
    y=0 попало пять узлов из двухсот. Тест заплаты этого не требует: он
    защемляет ВСЮ границу точным решением и смотрит, что получилось внутри.

    ОСТОРОЖНО С ПРАВИЛЬНОЙ СЕТКОЙ. На структурированной сетке квадратичное поле
    воспроизводит и ЛИНЕЙНЫЙ элемент — до 2e-15, хотя представить квадратику он
    не может. Это не чудо, а симметрия сетки, которая ему льстит. На сетке
    fTetWild тот же P1 даёт 1.3e-3, то есть проваливается, как и положено.
    Отсюда правило: элемент проверяют на НЕПРАВИЛЬНОЙ сетке.
    """
    from datum.fem.solve import spd_factor

    system = _patch_system(mesh, material, element, stiffness)
    _, K, _, inner_dofs = system
    factor = spd_factor(K[inner_dofs][:, inner_dofs])      # одна на оба поля
    cases = []
    for name, field, tol in (("заплата: линейное поле", _linear_field, TOL_PATCH),
                             ("заплата: квадратичное поле", _quadratic_field, TOL_PATCH)):
        error, inner, ms = _patch(mesh, material, field, element, system=system, factor=factor)
        cases.append(CaseResult(name, error, tol, inner, ms,
                                note=f"внутренних степеней свободы {inner}"))
    return Verification(cases=tuple(cases), material=material.name)
