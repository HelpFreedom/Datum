"""Квазистатический контакт — претензия №3.

Полный контактный решатель этому инструменту не по средствам: он требует
нелинейных итераций по контакту двух сеток. Жёсткость пружины проверка берёт
у призматического бруса на структурированной сетке — у неё есть лестница
сеток и вилка (произвольное тело МКЭ считает `fem_solve`, но без лестницы).
Инженерный вопрос про защёлку ставится корректно и без контактного решателя,
тремя звеньями:

  1. НАСКОЛЬКО отжать — геометрия. Меряется, а не объявляется.
  2. КАКАЯ сила на это нужна — МКЭ (или формула), с вилкой.
  3. ВО ЧТО она превращается на скосе — клин с трением.

Первое звено и было недостающим. `snap_insertion` меряет ОБЪЁМ продавливания,
а сила зависит от РАССТОЯНИЯ, на которое отжат упругий элемент. Объём в
расстояние не переводится: он зависит ещё и от площади контакта.

КАК МЕРЯЕТСЯ РАССТОЯНИЕ. Деталь ведётся по пути от посадки наружу, и в каждом
положении берутся сечения, ПЕРПЕНДИКУЛЯРНЫЕ вставке. В сечении ищется
наименьшая ужимка контура, при которой он расходится с материалом, — это и
есть требуемое отжатие. Способ проверен на конусе с известным наклоном:
восстановленный tan(α) совпал точно (0.1000, 0.2500, 0.5000 при заданных тех
же), а на `examples/latch` ужимка вышла ровно 0.5000 мм, то есть LIP_D − GAP,
где оба числа заданы человеком независимо.

НАКЛОН ЗАХОДА — ПРОИЗВОДНАЯ ТОГО ЖЕ. Если δ растёт по пути постепенно, деталь
заходит по скосу, и tan(α) = dδ/ds. Если δ возникает скачком — скоса нет,
деталь упирается в прямую ступеньку, и усилие вставки НЕ ОПРЕДЕЛЕНО: жёсткое
тело прямую ступеньку не пройдёт вовсе. Настоящие детали проходят, потому что
кромки скруглены и материал мнётся, — но это уже не та задача, которую решает
геометрия, и делать вид, что число есть, нельзя.

ЧЕГО ЗДЕСЬ НЕТ. Какой именно элемент прогибается — губка корпуса, консоль
защёлки, сама деталь — геометрия не знает: это зависит от соотношения
жёсткостей. Поэтому упругий элемент ОБЪЯВЛЯЕТСЯ, как и способ постановки в
`assembly.yaml`. Угадывать его было бы третьей за проект попыткой распознать
замысел по форме; две предыдущие не пережили замера.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from datum.checks.result import Artifact, CheckResult
from datum.geom.measure import bbox, inter_vol, man, section_xy, section_xz, section_yz, vol
from datum.geom.solid import Solid

__all__ = ["interference_profile", "InterferenceProfile", "insertion_force"]

#: Какое сечение перпендикулярно какой оси вставки.
_PERPENDICULAR = {0: section_yz, 1: section_xz, 2: section_xy}

#: Сколько шагов деления пополам при поиске ужимки. 28 шагов на отрезке в
#: несколько миллиметров — это точность далеко за пределами печати.
_BISECTION = 28


@dataclass(frozen=True, slots=True)
class InterferenceProfile:
    """Насколько нужно отжать упругий элемент в каждой точке пути."""

    #: Положения на пути от посадки, мм.
    path: tuple[float, ...] = ()
    #: Требуемое отжатие в каждом положении, мм.
    depth: tuple[float, ...] = ()
    #: Уперлось ли деление пополам в верхнюю границу хоть раз.
    saturated: bool = False
    #: Верхняя граница поиска, мм.
    limit: float = 0.0

    @property
    def peak(self) -> float:
        return max(self.depth) if self.depth else 0.0

    #: Порог, ниже которого отжатие считается отсутствующим, мм.
    TOL = 1e-6

    def _engagement(self) -> tuple[int, int] | None:
        """Индексы (первое касание, достижение пика) ПО ХОДУ ВСТАВКИ.

        Путь хранится ОТ ПОСАДКИ НАРУЖУ, а вставляют навстречу — от дальнего
        конца к посадке. Первая версия сканировала путь в порядке хранения и
        отвечала про выход детали, а не про вход: у `latch` это давало
        «касание с 0.5 мм», хотя деталь встречает губку на 6.3 мм.
        """
        n = len(self.path)
        engage = None
        for i in range(n - 1, -1, -1):
            if self.depth[i] > self.TOL:
                engage = i
                break
        if engage is None:
            return None
        # Точка, где деталь ЕЩЁ свободна, — соседняя снаружи, если она есть.
        touch = min(engage + 1, n - 1)
        peak = self.peak
        reach = engage
        for i in range(engage, -1, -1):
            if self.depth[i] >= peak - 1e-9:
                reach = i
                break
        return touch, reach

    @property
    def step(self) -> float:
        """Шаг развёртки. Скос короче него не разрешается — и об этом говорят."""
        return (self.path[1] - self.path[0]) if len(self.path) > 1 else 0.0

    @property
    def rise_samples(self) -> int:
        """За сколько шагов отжатие нарастает от нуля до пика. Ноль — скачком."""
        found = self._engagement()
        if found is None:
            return 0
        touch, reach = found
        return max(0, touch - reach - 1)

    @property
    def engaged_from(self) -> float | None:
        """На каком расстоянии от посадки деталь встречает препятствие."""
        found = self._engagement()
        return self.path[found[0]] if found else None

    def ramp(self) -> float | None:
        """Наклон захода tan(α) = dδ/ds на участке нарастания.

        Возвращает None, когда нарастания нет: отжатие возникло скачком между
        двумя соседними точками пути. Это не «очень крутой скос», а ОТСУТСТВИЕ
        скоса, и подменять одно другим нельзя — число вышло бы зависящим от
        шага развёртки, а не от детали.

        Способ проверен на конусе с известным наклоном: восстановленный tan(α)
        совпал с заданным до третьего знака при 0.1, 0.25 и 0.5.
        """
        peak = self.peak
        if peak <= self.TOL:
            return None
        found = self._engagement()
        if found is None:
            return None
        touch, reach = found
        # ПИК НА ПЕРВОМ ЖЕ КАСАНИИ — значит скос короче шага развёртки, а
        # скорее всего его нет вовсе. Поделить пик на один шаг и назвать это
        # наклоном — соблазн, в который я уже попал: на `latch` так выходило
        # tan(α) = 2.0, число про шаг сетки, а не про деталь. Честный ответ —
        # «не разрешено», а увеличить `samples` человек может сам.
        if touch - reach <= 1:
            return None
        return self._slope(reach, touch)

    def _slope(self, lo: int, hi: int) -> float | None:
        """Наклон по участку нарастания — НАИМЕНЬШИМИ КВАДРАТАМИ.

        Делить пик на пролёт между крайними точками нельзя: обе лежат ЗА
        границами нарастания, каждая примерно на полшага, и пролёт выходит
        завышенным, а наклон заниженным. Замерено на `latch`, где скос задан
        параметрами и равен 0.667: по концам выходило 0.556 — ошибка 17 % при
        шаге 0.1 мм. По внутренним точкам смещения нет.
        """
        inner = [(self.path[i], self.depth[i]) for i in range(min(lo, hi), max(lo, hi) + 1)
                 if self.TOL < self.depth[i] < self.peak - self.TOL]
        if len(inner) < 2:
            return None
        xs = np.array([p[0] for p in inner])
        ys = np.array([p[1] for p in inner])
        slope = float(np.polyfit(xs, ys, 1)[0])
        return abs(slope) if abs(slope) > self.TOL else None

    def release_ramp(self) -> float | None:
        """Наклон со стороны ПОСАДКИ — то, через что деталь выходит обратно.

        У защёлки это угол зацепа: пологий скос здесь означает, что деталь
        выдернется, прямая ступенька — что она держит. Мерится тем же способом,
        но от посадки наружу: это буквально та же кромка, пройденная в другую
        сторону.
        """
        peak = self.peak
        if peak <= self.TOL or not self.path:
            return None
        engage = next((i for i, d in enumerate(self.depth) if d > self.TOL), None)
        if engage is None:
            return None
        touch = max(engage - 1, 0)
        reach = engage
        for i in range(engage, len(self.depth)):
            if self.depth[i] >= peak - 1e-9:
                reach = i
                break
        if reach - touch <= 1:
            return None
        return self._slope(touch, reach)


def _sections(solid: Solid, axis: int, coordinate: float):
    return _PERPENDICULAR[axis](solid, coordinate)


def _squeeze(part_section, obstacle_section, limit: float) -> tuple[float, bool]:
    """Наименьшая ужимка контура, при которой он расходится с материалом."""
    if part_section.is_empty or obstacle_section.is_empty:
        return 0.0, False
    if not part_section.intersects(obstacle_section):
        return 0.0, False
    lo, hi = 0.0, limit
    shrunk = part_section.buffer(-limit)
    if not shrunk.is_empty and shrunk.intersects(obstacle_section):
        # Даже ужатая до предела деталь всё ещё в материале: ответ БОЛЬШЕ
        # предела, и выдавать сам предел за ответ нельзя — это выглядело бы
        # измерением, а было бы упором в границу поиска.
        return limit, True
    for _ in range(_BISECTION):
        mid = (lo + hi) / 2
        shrunk = part_section.buffer(-mid)
        if shrunk.is_empty or not shrunk.intersects(obstacle_section):
            hi = mid
        else:
            lo = mid
    # Чем кончилась бисекция: сечение ВЫШЛО из материала или ИСЧЕЗЛО в нём?
    # У тонкой детали, целиком утопленной в препятствие, контур пропадает
    # раньше, чем расходится с материалом, и глубина исчезновения — это
    # половина толщины детали, а не отжатие. Раньше она выдавалась за ответ:
    # лепесток в 1 мм, перекрытый ребром насквозь, получал «отжать 0.5 мм» и
    # вилку силы, выглядевшую точным измерением. Это тот же упор в границу, что
    # и выше, только с другой стороны.
    if part_section.buffer(-hi).is_empty and part_section.buffer(-lo).intersects(
            obstacle_section):
        return hi, True
    return hi, False


def interference_profile(part: Solid, obstacles: Solid | Iterable[Solid], *,
                         direction: str, distance: float = 20.0,
                         from_mm: float = 0.0, samples: int = 25,
                         slices: int = 7, limit: float | None = None
                         ) -> InterferenceProfile:
    """Профиль отжатия вдоль пути вставки.

    `direction` — направление ВСТАВКИ, как и везде в проверках собираемости;
    деталь при этом уводится встречно, от посадки наружу.
    """
    from datum.checks.motion import _shifted
    from datum.checks.motion import direction as parse_direction

    others = [obstacles] if isinstance(obstacles, Solid) else list(obstacles)
    if not others:
        raise ValueError("не с чем мерить контакт: список препятствий пуст")
    if samples < 2:
        raise ValueError(f"нужно не меньше двух точек пути, получено {samples}")
    if slices < 1:
        raise ValueError(f"нужно не меньше одного сечения, получено {slices}")

    away = -parse_direction(direction)
    axis = int(np.argmax(np.abs(away)))
    if not math.isclose(float(np.abs(away).max()), 1.0, rel_tol=1e-9):
        raise ValueError("контакт меряется только вдоль осей координат: "
                         "сечение перпендикулярно вставке иначе не построить")

    if limit is None:
        # Граница поиска — половина наименьшей поперечной стороны детали:
        # ужать больше значит не оставить от сечения ничего.
        lo, hi = bbox(part)
        across = [hi[i] - lo[i] for i in range(3) if i != axis]
        limit = max(0.2, min(across) / 2)

    path, depth = [], []
    saturated = False
    for s in np.linspace(from_mm, distance, samples):
        moved = _shifted(part, away, float(s))
        worst = 0.0
        # Сечения строятся только там, где тела вообще пересекаются: булева
        # операция здесь дешевле десятка сечений с делением пополам.
        if any(inter_vol(moved, other) > 0 for other in others):
            lo_m, hi_m = bbox(moved)
            for other in others:
                lo_o, hi_o = bbox(other)
                start = max(lo_m[axis], lo_o[axis])
                stop = min(hi_m[axis], hi_o[axis])
                if stop <= start:
                    continue
                # Где именно тела пересекаются вдоль вставки: сечения обязаны
                # попасть ТУДА. Семь сечений на всё перекрытие у длинной
                # пробки стоят через 5 мм, и короткий гребень (0.2 мм с
                # натягом) оказывался между ними — «на пути ничего не
                # отжимается, усилия не требуется» при известном пересечении
                # (аудит 2026-11-21).
                common = man(moved) ^ man(other)
                inside: list[float] = []
                if vol(common) > 0:
                    lo_c, hi_c = bbox(common)
                    c0, c1 = float(lo_c[axis]), float(hi_c[axis])
                    if c1 > c0:
                        pad = min(0.01, (c1 - c0) / 4)
                        inside = [c0 + pad, (c0 + c1) / 2, c1 - pad]
                # Отступ от краёв перекрытия — АБСОЛЮТНЫЙ, а не доля высоты.
                # Худшее отжатие обычно приходится ровно на кромку детали, и
                # отступ в 2 % высоты её промахивал: на `latch` наклон выходил
                # 0.659 вместо 0.672. Доля отступа зависела бы от того, как
                # глубоко деталь вошла, то есть от шага развёртки, а не от
                # детали.
                span = stop - start
                eps = min(0.01, span / 4)
                levels = sorted({*np.linspace(start + eps, stop - eps, slices).tolist(),
                                 *(t for t in inside if start < t < stop)})
                for t in levels:
                    value, hit = _squeeze(_sections(moved, axis, float(t)),
                                          _sections(other, axis, float(t)), limit)
                    saturated = saturated or hit
                    worst = max(worst, value)
        path.append(float(s))
        depth.append(float(worst))

    return InterferenceProfile(path=tuple(path), depth=tuple(depth),
                               saturated=saturated, limit=float(limit))


#: Каким упругим элементом отжимается деталь: консоль (защемлена с одного
#: конца) или направляемая балка (оба конца сохраняют угол).
SPRING_MODES = ("cantilever", "guided")



def _stiffness_band(length: float, width: float, thickness: float,
                    modulus: float, poisson: float, mode: str,
                    divisions) -> tuple[float, float, object, object]:
    """Жёсткость упругого элемента — вилкой, через МКЭ с самопроверкой."""
    from datum.checks.fem import _ladder
    from datum.fem.model import Bar, Material
    from datum.fem.solve import guided_deflection, tip_load
    from datum.fem.verify import converge, verify

    material = Material(modulus, poisson)
    bar = Bar(length, width, thickness)
    check = verify(material)
    if not check.ok:
        return 0.0, 0.0, None, check

    solver = guided_deflection if mode == "guided" else tip_load

    def run(level):
        solution = solver(bar, material, force=1.0, divisions=level)
        return -float(solution.at_face(0, length, 2).mean()), solution

    conv = converge(run, _ladder(divisions))
    lo_d, hi_d = conv.band
    # Жёсткость обратна прогибу: концы вилки меняются местами.
    return 1.0 / hi_d, 1.0 / lo_d, conv, check


def insertion_force(part: Solid, obstacles: Solid | Iterable[Solid], *,
                    direction: str, spring_length: float, spring_width: float,
                    spring_thickness: float, modulus: float,
                    poisson: float, friction: float, mode: str = "cantilever",
                    distance: float = 20.0, samples: int = 41,
                    max_force: float = float("inf"),
                    divisions: tuple[int, int, int] = (16, 4, 2),
                    id: str = "", part_name: str = "",
                    depends_on: tuple[str, ...] = ()) -> CheckResult:
    """С какой силой деталь заходит на место.

    Три звена, каждое со своим источником:

      δ — насколько отжать упругий элемент. ИЗМЕРЯЕТСЯ по геометрии.
      P — какая сила на это нужна. Считается МКЭ, вилкой, с самопроверкой.
      W — во что она превращается на скосе: W = P·(μ + tanα)/(1 − μ·tanα).

    `poisson` и `friction` обязательны, как и `modulus`: это свойства
    материала и пары трения, а не константы. Трение пластика по пластику —
    0.2…0.4 в зависимости от материала, слоёв и того, обкаталась ли кромка;
    спрятанное умолчанием 0.3, оно не попадало в развёртку по незамеренному.
    Объявляйте их параметрами с происхождением.

    Упругий элемент ОБЪЯВЛЯЕТСЯ (`spring_*`): какая именно часть прогибается —
    губка корпуса, консоль защёлки или сама деталь — зависит от соотношения
    жёсткостей, и геометрия этого не знает.

    Если скоса нет (отжатие возникает скачком), сила вставки НЕ ОПРЕДЕЛЕНА:
    жёсткое тело прямую ступеньку не пройдёт. Настоящие детали проходят, потому
    что кромки скруглены и материал мнётся, — но это другая задача, и выдавать
    за неё число нельзя.
    """
    t0 = time.perf_counter()
    part_name = part_name or part.name or "деталь"
    check_id = id or f"{part_name}.усилие_вставки"
    common = dict(id=check_id, title="Усилие вставки", group="contact",
                  inputs=(part_name,), depends_on=depends_on)

    if not (0.0 <= friction < 5.0):
        raise ValueError(f"коэффициент трения должен лежать в [0, 5), получено {friction}")
    if mode not in SPRING_MODES:
        # Опечатка «cantilevr» раньше молча считалась консолью.
        raise ValueError(f"mode {mode!r}: ожидается одно из {', '.join(SPRING_MODES)}")

    profile = interference_profile(part, obstacles, direction=direction,
                                   distance=distance, samples=samples)

    if profile.peak <= profile.TOL:
        return CheckResult(**common, status="pass",
                           message=(f"{part_name}: на пути вставки {direction} ничего "
                                    f"не отжимается — деталь проходит свободно, "
                                    f"усилия не требуется"),
                           value=0.0, unit="Н", op="<=", threshold=max_force,
                           duration_ms=(time.perf_counter() - t0) * 1000)

    if profile.saturated:
        return CheckResult(**common, status="error",
                           message=(f"{part_name}: отжатие не измеряется — деталь уходит "
                                    f"в препятствие глубже, чем её сечение можно ужать "
                                    f"(граница поиска {profile.limit:.2f} мм). Это не "
                                    f"посадка с натягом, а затор. Смотрите `assembly_path`"),
                           duration_ms=(time.perf_counter() - t0) * 1000)

    if profile.depth and profile.depth[-1] > profile.TOL:
        # На дальнем конце пути деталь всё ещё в материале: либо путь короче,
        # чем нужно, чтобы выйти на свободу, либо это затор. Раньше такой
        # профиль получал «встречает на {distance} мм» и «скоса нет» — то есть
        # рассказ о фаске там, где беда в другом.
        return CheckResult(**common, status="error",
                           message=(f"{part_name}: и в {distance:g} мм от посадки деталь "
                                    f"всё ещё отжата на {profile.depth[-1]:.3f} мм — "
                                    f"свободного начала пути нет. Увеличьте distance "
                                    f"или смотрите `assembly_path`: это может быть затор"),
                           duration_ms=(time.perf_counter() - t0) * 1000)

    k_lo, k_hi, conv, self_check = _stiffness_band(
        spring_length, spring_width, spring_thickness, modulus, poisson, mode, divisions)
    if conv is None:
        return CheckResult(**common, status="error",
                           message=(f"{part_name}: решатель не воспроизвёл эталоны, "
                                    f"числам верить нельзя.\n{self_check.report()}"),
                           duration_ms=(time.perf_counter() - t0) * 1000)

    p_lo, p_hi = k_lo * profile.peak, k_hi * profile.peak
    tan_alpha = profile.ramp()
    hold = profile.release_ramp()
    hold_text = (f"выходит по скосу tan(α)={hold:.3f}" if hold is not None
                 else "со стороны посадки — прямая кромка, деталь держит")

    head = (f"{part_name}: отжать {profile.peak:.3f} мм (встречает на "
            f"{profile.engaged_from:.2f} мм от посадки), сила пружины "
            f"{p_lo:.2f}…{p_hi:.2f} Н")

    if tan_alpha is None:
        return CheckResult(**common, status="warn",
                           message=(f"{head}. СКОСА НЕТ: отжатие возникает скачком, "
                                    f"короче шага развёртки {profile.step:.3f} мм. "
                                    f"Жёсткое тело прямую ступеньку не пройдёт, и "
                                    f"усилие вставки не определено — нужна заходная "
                                    f"фаска. {hold_text}"),
                           value=p_hi, unit="Н", op="<=", threshold=max_force,
                           evidence=_profile_artifact(profile, conv, self_check,
                                                      (p_lo, p_hi), None,
                                                      part_name, check_id),
                           duration_ms=(time.perf_counter() - t0) * 1000)

    if friction * tan_alpha >= 1.0:
        return CheckResult(**common, status="fail",
                           message=(f"{head}. САМОТОРМОЖЕНИЕ: μ·tan(α) = "
                                    f"{friction * tan_alpha:.2f} ≥ 1 при скосе "
                                    f"tan(α)={tan_alpha:.3f} и трении μ={friction:g}. "
                                    f"Сколько ни дави, деталь не пойдёт — нужен "
                                    f"положе скос или меньше трение. {hold_text}"),
                           value=float("inf"), unit="Н", op="<=", threshold=max_force,
                           evidence=_profile_artifact(profile, conv, self_check,
                                                      (p_lo, p_hi), tan_alpha,
                                                      part_name, check_id),
                           duration_ms=(time.perf_counter() - t0) * 1000)

    factor = (friction + tan_alpha) / (1 - friction * tan_alpha)
    w_lo, w_hi = p_lo * factor, p_hi * factor
    below = w_hi <= max_force
    status = "pass" if below else ("warn" if w_lo <= max_force else "fail")
    verdict = {"pass": "в допуске",
               "warn": "порог попал ВНУТРЬ вилки — ответ не разделяется",
               "fail": "больше допустимого"}[status]

    return CheckResult(**common, status=status,
                       message=(f"{head}, скос tan(α)={tan_alpha:.3f} за "
                                f"{profile.rise_samples} шагов, трение μ={friction:g} → "
                                f"усилие вставки {w_lo:.2f}…{w_hi:.2f} Н, {verdict} "
                                f"(допуск ≤ {max_force:g}). {hold_text}. "
                                f"Эталоны решателя: "
                                + ", ".join(f"{c.name} {c.error:.0e}" for c in self_check.cases)),
                       value=w_hi, unit="Н", op="<=", threshold=max_force,
                       evidence=_profile_artifact(profile, conv, self_check,
                                                  (p_lo, p_hi), tan_alpha,
                                                  part_name, check_id),
                       duration_ms=(time.perf_counter() - t0) * 1000)


def _profile_picture(profile, tan_alpha, part_name: str, check_id: str) -> tuple:
    """Профиль отжатия картинкой.

    Здесь картинка рисуется ВСЕГДА, а не только при провале, и это не отступ от
    правила, а его же применение. Правило запрещает платить БУЛЕВЫМИ за
    доказательство к успешной проверке; тут развёртка уже пройдена, и остаётся
    одна растеризация — 30 мс на 9 КБ. А смысла в ней не меньше, чем при
    провале: по кривой сразу видно, есть скос или отжатие возникает ступенькой,
    и где деталь встречает препятствие. Из текста это читается куда хуже.
    """
    try:
        from datum.viz.chart import Mark, Series, render_chart

        marks = [Mark(label=f"пик {profile.peak:.3f} мм", y=profile.peak)]
        if profile.engaged_from is not None:
            marks.append(Mark(label="встречает", x=profile.engaged_from))
        note = (f"скос tan(α)={tan_alpha:.3f} за {profile.rise_samples} шагов"
                if tan_alpha is not None else
                f"скоса нет: отжатие возникает короче шага {profile.step:.3f} мм")
        png = render_chart(
            [Series(profile.path, profile.depth, label="отжатие δ", dots=True)],
            title=f"{check_id} — профиль отжатия",
            note=f"{part_name}: {note}",
            x_label="от посадки, мм", y_label="δ, мм", marks=marks)
    except Exception as e:
        return (Artifact(kind="text",
                         caption=f"график не построился: {type(e).__name__}: {e}"),)
    return (Artifact(kind="png", caption=f"{part_name}: профиль отжатия вдоль пути",
                     data=png),)


def _profile_artifact(profile, conv, self_check, spring, tan_alpha,
                      part_name: str = "", check_id: str = "") -> tuple:
    return (Artifact(kind="json", caption="профиль отжатия и сходимость",
                     data={"path": list(profile.path), "depth": list(profile.depth),
                           "peak_mm": profile.peak, "step_mm": profile.step,
                           "rise_samples": profile.rise_samples,
                           "ramp_tan": tan_alpha,
                           "release_ramp_tan": profile.release_ramp(),
                           "spring_force_band": list(spring),
                           "deflections": list(conv.values) if conv else [],
                           "dofs": list(conv.dofs) if conv else [],
                           "self_check": {c.name: c.error for c in self_check.cases}}),
            *_profile_picture(profile, tan_alpha, part_name, check_id))
