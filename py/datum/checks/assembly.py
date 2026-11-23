"""Собираемость: можно ли это вообще собрать.

Претензия №1 из технического задания. Беда, которую она ловит, обиднее прочих:
каждая деталь по отдельности хороша, проверки зацепов и зазоров зелёные, а
вместе конструкция не собирается — детали некуда вставить, потому что путь
перекрыт другой деталью или собственным корпусом.

Порт `check4.check_body` из phone-case-gears, где это делалось так:

    worst = max(inter_vol(move(phone, dy=dy), case) for dy in range(1, 176, 2))
    gate(worst <= TOL, "телефон вдвигается сверху без планки")

Три вещи из этого кода стоит назвать явно, потому что каждая неочевидна.

ПУТЬ НЕ ВКЛЮЧАЕТ ПОСАДОЧНОЕ МЕСТО. Цикл начинается с dy=1, а не с нуля, и это
не описка. В посадке натяг ЗАДУМАН: защёлка должна упираться, иначе она не
держит. Включи посадку в развёртку — и любая работающая защёлка объявлялась бы
несобираемой.

СДВИГ, А НЕ ПОВОРОТ. Здесь проверяются только прямолинейные вставки: «входит
сверху», «надвигается сбоку». Этого хватило корпусу и хватает подавляющему
большинству печатных узлов. Вставку с поворотом (деталь заводится наискось и
доворачивается) проверка не найдёт и честно об этом молчит, а не делает вид.

ПРЕПЯТСТВИЕ — НЕ ТОЛЬКО КОРПУС. В корпусе планка проверялась и против корпуса,
и против уже вставленного телефона: порядок сборки важен, и деталь, входящая
первой, может закрыть дорогу следующей.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence

import numpy as np

from datum.checks.result import Artifact, CheckResult
from datum.geom.measure import inter_vol
from datum.geom.solid import Solid

__all__ = ["assembly_path", "snap_insertion", "AssemblyProbe"]


class AssemblyProbe:
    """Развёртка одной вставки: сколько мешает на каждом шаге пути.

    `away` — направление, в котором деталь УВОДИТСЯ ОТ ПОСАДКИ, то есть
    встречное вставке. Параметр назван так нарочно: пока он звался
    `direction`, было слишком легко подставить сюда направление вставки и
    получить верные числа с перевёрнутым смыслом.
    """

    def __init__(self, part: Solid, obstacles: Sequence[Solid], away: np.ndarray):
        from datum.checks.motion import _shifted

        self._part = part
        self._obstacles = list(obstacles)
        self._direction = away
        self._shift = _shifted
        self.calls = 0

    def at(self, distance: float) -> float:
        """Наибольшее пересечение с препятствиями при сдвиге на `distance`."""
        self.calls += 1
        moved = self._shift(self._part, self._direction, distance)
        return max((inter_vol(moved, other) for other in self._obstacles), default=0.0)


#: Шаг пути проверок собираемости, мм — как у `motion.travel_sweep`.
PATH_RESOLUTION = 0.25
#: Больше узлов не берём: 4000 узлов по 0.25 мм — путь в метр.
PATH_MAX_SAMPLES = 4000


def _worst_along(probe: AssemblyProbe, low: float, high: float,
                 samples: int, refine: int,
                 resolution: float = PATH_RESOLUTION) -> tuple[float, float, float, float]:
    """Худшее место пути: сетка, затем бисекция вокруг худшего узла.

    ШАГ СЕТКИ — НЕ КРУПНЕЕ `resolution` (0.25 мм, как у `travel_sweep`).
    Бисекция уточняет только то, что сетка уже нашла: если деталь проходит
    сквозь препятствие между двумя узлами и на обоих пересечения нет, уточнять
    нечего. Прежде шаг брался от ПРОТЯЖЁННОСТИ детали вдоль пути («контакт
    длится не меньше неё») — это верно только для выпуклой детали. Деталь из
    двух пластин 0.8 мм в 20 мм друг от друга касается полки 0.4 мм лишь на
    1.2 мм пути, а шаг был 1.65: «задевание 0.000 мм³» при настоящих 128
    (найдено обзором, `tests/unit/test_assembly.py`). Теперь пропустить можно
    только то, что вместе с элементом детали тоньше `resolution`.
    """
    if not resolution > 0:
        raise ValueError(f"resolution {resolution}: нужен положительный шаг, мм")
    needed = int(np.ceil((high - low) / resolution)) + 1
    samples = max(samples, min(needed, PATH_MAX_SAMPLES))
    grid = np.linspace(low, high, max(2, samples))
    values = [probe.at(float(d)) for d in grid]
    index = int(np.argmax(values))
    best_d, best_v = float(grid[index]), float(values[index])

    step = (high - low) / max(1, samples - 1)
    lo, hi = max(low, best_d - step), min(high, best_d + step)
    for _ in range(refine):
        for candidate in ((best_d + lo) / 2, (best_d + hi) / 2):
            value = probe.at(candidate)
            if value > best_v:
                best_d, best_v = candidate, value
        lo, hi = max(low, best_d - (hi - lo) / 4), min(high, best_d + (hi - lo) / 4)

    # Доля пути, на которой деталь вообще чего-то касается. Ради неё грубая
    # сетка и сохраняется: одно только худшее место не отличает ЗАТОР от
    # ТРЕНИЯ, а это разные беды (см. `_verdict`).
    touching = sum(1 for v in values if v > 1e-6) / len(values)
    # Задевание В КОНЦЕ пути. Им отличается ЗАЩЁЛКА от ЗАТОРА: у защёлки деталь
    # продавливает препятствие и выходит на свободу, у затора — упирается
    # насовсем. Узел сетки ровно в `high` есть всегда: linspace включает конец.
    return best_d, best_v, touching, float(values[-1])


def _hint(touching: float, clearing: list[str], distance: float) -> str:
    """Факты, которые стоит знать, прежде чем править геометрию.

    ЗДЕСЬ НЕ РАСПОЗНАЮТ МЕХАНИЗМ. Дважды пробовал и дважды получил по рукам от
    замера — стоит записать оба раза, чтобы никто не начал в третий.

    Попытка первая: отличать трение от затора по доле пути с касанием. Замер:
    0.52 у задуманного натяга, 0.68 у затора, 0.84 у неверного направления —
    не разделяется.

    Попытка вторая: отличать ЗАЩЁЛКУ от затора по тому, обнуляется ли задевание
    к концу пути. На `examples/latch` вышло красиво, а на запертой полости —
    ровно та же картина: кубик, сдвинутый за скорлупу, тоже «выходит на
    свободу», только СКВОЗЬ неё. Геометрически «продавил губку» и «прошёл
    сквозь стенку» — одно и то же событие. Различает жёсткость, а её тут нет.

    Вывод, который дороже обеих попыток: распознавать механизм по геометрии —
    проигрышная игра, потому что механизмов не перечислить. Замысел ОБЪЯВЛЯЕТСЯ
    (`assembly.yaml`: `method: snap`), а проверка верифицирует объявленное.
    Поэтому ниже — только факты и названные прочтения, без вердикта.

    Здесь СООБЩАЕТСЯ ФАКТ, а не ставится диагноз, и это решение по замеру.
    Сначала казалось, что «трение по всей дороге» отличается от «затора» долей
    пути с касанием: у планки phone-case-gears v4, которую поджимают пружинящие
    балки, касание держится от посадки до 80 мм. Померили на трёх случаях:

        планка v4, натяг балок задуман  — 52 % пути,   1.3 мм³
        паз, закрытый с торца (затор)   — 68 % пути, 167.6 мм³
        планка не в ту сторону          — 84 % пути, 169.9 мм³

    Доли не разделяются. Разделяет ВЕЛИЧИНА — два порядка, — но и по ней порог
    был бы подгонкой под три примера. Поэтому проверка называет долю и
    напоминает про обе возможности, а выбирает человек.

    Знание из корпуса, ради которого напоминание и существует: оригинальная
    проверка гоняла планку с `springs=False`, попросту снимая балки. Без этого
    «1.3 мм³» выглядит заторам, а это натяг, ради которого деталь и сделана.
    """
    parts: list[str] = []

    # Факт: по этим направлениям задевание к концу пути обращается в ноль, то
    # есть препятствие проходится НАСКВОЗЬ. Что это значит — геометрия не
    # решает: так выглядит и защёлка (материал прогибается), и запертая полость
    # (материал пришлось бы разрушить). Названы оба прочтения, выбирает человек.
    if clearing:
        parts.append(
            f"  Факт: по {', '.join(clearing)} задевание к {distance:g} мм "
            f"обращается в ноль — препятствие проходится насквозь. Защёлка это "
            f"или запертая полость, геометрия не различает: различает "
            f"жёсткость. Если защёлка задумана — объявите её в assembly.yaml "
            f"(`method: snap`), и мерить будет `snap_insertion`.")

    if touching > 0:
        parts.append(
            f"  Касание держится на {touching * 100:.0f} % пути. Если деталь "
            f"поджимается пружинящими элементами, такой натяг задуман: поднимите "
            f"max_inter_vol или проверяйте деталь без них. Если же это всплеск "
            f"посреди свободного пути — помеха настоящая.")
    return "".join(parts)


def assembly_path(part: Solid, obstacles: Solid | Iterable[Solid], *,
                  dirs: Iterable[str] = ("+Z", "-Z", "+X", "-X", "+Y", "-Y"),
                  distance: float = 40.0, from_mm: float = 0.5,
                  samples: int = 25, refine: int = 6,
                  max_inter_vol: float = 0.05, id: str = "",
                  part_name: str = "", depends_on: tuple[str, ...] = ()) -> CheckResult:
    """Найти, каким прямолинейным сдвигом деталь ставится на место.

    `dirs` — направления ВСТАВКИ: куда движется деталь, становясь на место.
    Щуп внутри ведёт её ВСТРЕЧНО, от посадки наружу: путь один и тот же,
    пройденный в обе стороны, и для жёсткой прямолинейной вставки это
    равносильно. Называется он по вставке, потому что человек собирает, а не
    разбирает.

    Разница не косметическая, и я на ней уже ошибся. Корпусный цикл
    `move(phone, dy=1..175)` уводит телефон ВВЕРХ (в том проекте верх — это
    +Y: планка, названная в NOTES «Верх», стоит на Y 164…170). NOTES при этом
    говорит «телефон вдвигается сверху», то есть вставка идёт в −Y. Первая
    версия проверки печатала «ставится сдвигом +Y» — числа верные, смысл
    перевёрнутый. То же на `examples/latch`: крышка сидит на Z 13.7…16.7 под
    губкой, начинающейся с 17, и вдавливается сверху вниз, по −Z.

    Путь считается свободным, если пересечение с препятствиями нигде не
    превышает `max_inter_vol`. Само посадочное место в путь НЕ входит —
    развёртка обрывается за `from_mm` до него, потому что там натяг задуман.

    Результат один, а не по одному на направление: вопрос «собирается ли»
    имеет один ответ. Если собирается — названо направление; если нет — названо
    лучшее из испробованных и место, где оно упирается.
    """
    from datum.checks.motion import direction as parse_direction

    t0 = time.perf_counter()
    part_name = part_name or part.name or "деталь"
    others = [obstacles] if isinstance(obstacles, Solid) else list(obstacles)
    if not others:
        raise ValueError("не с чем проверять сборку: список препятствий пуст")
    if from_mm >= distance:
        raise ValueError(f"путь пуст: from_mm={from_mm} не меньше distance={distance}")

    check_id = id or f"{part_name}.собираемость"
    tried: list[tuple[str, float, float, int]] = []

    for key in dirs:
        # Ключ — направление ВСТАВКИ; уводим встречно, от посадки наружу.
        probe = AssemblyProbe(part, others, -parse_direction(key))
        where, worst, touching, tail = _worst_along(probe, from_mm, distance,
                                                    samples, refine)
        tried.append((str(key), worst, where, touching, tail))
        if worst <= max_inter_vol:
            names = ", ".join(o.name or "препятствие" for o in others)
            return CheckResult(
                id=check_id, title="Собираемость", group="assembly",
                status="pass",
                message=(f"{part_name} ставится сдвигом {key}: путь {from_mm:g}…"
                         f"{distance:g} мм свободен (наибольшее задевание "
                         f"{worst:.3f} мм³ при допустимых {max_inter_vol:g}); "
                         f"препятствия: {names}"),
                value=worst, unit="мм³", op="<=", threshold=max_inter_vol,
                inputs=(part_name, *(o.name for o in others if o.name)),
                depends_on=depends_on,
                duration_ms=(time.perf_counter() - t0) * 1000)

    # Ни одно направление не подошло: называем лучшее и место, где упирается.
    best_key, best_worst, best_where, best_touch, best_tail = min(
        tried, key=lambda row: row[1])
    listing = "; ".join(f"{k}: {v:.2f} мм³ на {d:.1f} мм" for k, v, d, _, _ in tried)
    # Направления, по которым деталь всё же ВЫХОДИТ НА СВОБОДУ к концу пути.
    clearing = [k for k, _, _, _, t in tried if t <= max(max_inter_vol, 1e-6)]
    message = (f"{part_name} НЕ СОБИРАЕТСЯ ни одним из {len(tried)} направлений. "
               f"Ближе всех {best_key}: наибольшее задевание "
               f"на {best_where:.2f} мм от посадки, {best_worst:.2f} мм³ "
               f"при допустимых {max_inter_vol:g}. Все: {listing}"
               + _hint(best_touch, clearing, distance))

    return CheckResult(
        id=check_id, title="Собираемость", group="assembly",
        status="fail", message=message,
        value=best_worst, unit="мм³", op="<=", threshold=max_inter_vol,
        inputs=(part_name, *(o.name for o in others if o.name)),
        depends_on=depends_on,
        evidence=_jam_picture(part, others, best_key, best_where,
                              part_name, check_id, message),
        duration_ms=(time.perf_counter() - t0) * 1000)


def _jam_picture(part, others, key, where, part_name, check_id, message) -> tuple:
    """Где именно упирается — картинкой.

    Показывается лучшее из направлений в том месте, где оно заклинило: у
    остальных заклинивает раньше и сильнее, и разбирать надо именно это.

    Из препятствий берётся ТО, ОБО ЧТО ДЕТАЛЬ И УПЁРЛАСЬ, а не самое крупное:
    мешать может мелкая бобышка рядом с большим корпусом, и нарисовать корпус
    значило бы показать картинку, на которой всё в порядке.
    """
    from datum.checks.evidence import escape_evidence
    from datum.checks.motion import _shifted
    from datum.checks.motion import direction as parse_direction

    # `key` — вставка; картинка строится на пути щупа, то есть встречном.
    vector = -parse_direction(key)
    moved = _shifted(part, vector, where)
    culprit = max(others, key=lambda o: float(inter_vol(moved, o)))
    try:
        artifact = escape_evidence(
            part, culprit, vector, where,
            part_name=part_name, holder_name=culprit.name or "препятствие",
            title=f"{check_id} — НЕ СОБИРАЕТСЯ",
            note=message)
    except Exception as e:
        return (Artifact(kind="text",
                         caption=f"доказательство не построилось: "
                                 f"{type(e).__name__}: {e}"),)
    return (artifact,) if artifact is not None else ()


def snap_insertion(part: Solid, obstacles: Solid | Iterable[Solid], *,
                   direction: str, max_press_vol: float,
                   distance: float = 40.0, from_mm: float = 0.5,
                   clear_at: float | None = None,
                   samples: int = 25, refine: int = 6,
                   id: str = "", part_name: str = "",
                   depends_on: tuple[str, ...] = ()) -> CheckResult:
    """Защёлка: сколько материала приходится продавить, чтобы деталь встала.

    `assembly_path` спрашивает «есть ли прямолинейный путь БЕЗ задевания» и для
    защёлки честно отвечает «нет»: крышка шире проёма, она входит с прогибом.
    Объявлять её из-за этого несобираемой — враньё в другую сторону.

    Здесь задаётся вопрос, на который геометрия ответить МОЖЕТ: по названному
    направлению деталь обязана (а) в конце пути выйти на свободу и (б) по дороге
    продавить не больше, чем задумано.

    `direction` — направление ВСТАВКИ (у крышки `latch` это −Z, её вдавливают
    сверху вниз). Щуп внутри ведёт деталь встречно, от посадки наружу; замер
    по этому пути:

        0.5 мм →  18 мм³     ← крышка входит в губку
        3.0 мм → 242 мм³     ← продавливает её целиком
       10.0 мм →   0 мм³     ← вышла, губка её держит

    Ноль в конце и отличает ЗАЩЁЛКУ от ЗАТОРА: у затора задевание не кончается.

    НАПРАВЛЕНИЕ НЕ УГАДЫВАЕТСЯ, а задаётся. Взять «то, где продавить меньше»
    было бы соблазнительно и неверно: у той же крышки вбок продавливается
    212 мм³ против 242 вниз, только вбок она пошла бы СКВОЗЬ СТЕНКУ. Геометрия
    тут не судья — судьёй была бы жёсткость, а это отдельная работа.

    `max_press_vol` обязателен и умолчания не имеет. Разумного умолчания у него
    нет: продавленный объём зависит от замысла, и подставить сюда число значило
    бы решить за человека, насколько тугой должна быть его защёлка.
    """
    from datum.checks.motion import direction as parse_direction

    t0 = time.perf_counter()
    part_name = part_name or part.name or "деталь"
    others = [obstacles] if isinstance(obstacles, Solid) else list(obstacles)
    if not others:
        raise ValueError("не с чем проверять защёлку: список препятствий пуст")
    if from_mm >= distance:
        raise ValueError(f"путь пуст: from_mm={from_mm} не меньше distance={distance}")
    if max_press_vol <= 0:
        raise ValueError("max_press_vol должен быть больше нуля: "
                         "защёлка без продавливания — это не защёлка")

    check_id = id or f"{part_name}.защёлка"
    where_free = distance if clear_at is None else float(clear_at)
    if not (from_mm < where_free <= distance):
        raise ValueError(f"clear_at={where_free} обязан лежать внутри пути "
                         f"({from_mm}…{distance}]")

    # `direction` — направление ВСТАВКИ; щуп уводит деталь встречно.
    probe = AssemblyProbe(part, others, -parse_direction(direction))
    at_free = probe.at(where_free)
    peak_where, peak, _, _ = _worst_along(probe, from_mm, where_free, samples, refine)
    names = ", ".join(o.name or "препятствие" for o in others)
    common = dict(id=check_id, title="Защёлка", group="assembly",
                  unit="мм³", op="<=",
                  inputs=(part_name, *(o.name for o in others if o.name)),
                  depends_on=depends_on)

    # Не вышла на свободу — значит это не защёлка, а затор, и продавленный
    # объём мерить бессмысленно: деталь туда попросту не попадает.
    if at_free > max(0.05, max_press_vol * 0.01):
        message = (f"{part_name} НЕ ЗАЩЁЛКИВАЕТСЯ сдвигом {direction}: на "
                   f"{where_free:g} мм от посадки задевание всё ещё "
                   f"{at_free:.2f} мм³ — деталь не выходит на свободу, это "
                   f"затор, а не натяг. Препятствия: {names}")
        return CheckResult(**common, status="fail", message=message,
                           value=at_free, threshold=max_press_vol,
                           evidence=_jam_picture(part, others, direction, where_free,
                                                 part_name, check_id, message),
                           duration_ms=(time.perf_counter() - t0) * 1000)

    # Ничего не продавлено — значит и защёлки нет: губка не касается того, за
    # что должна цепляться. Прежде это было зелёное «защёлкивается: продавливает
    # 0.00 мм³ ≤ 50» (аудит 2026-11-21), хотя сама проверка отвергает порог
    # ≤ 0 словами «защёлка без продавливания — это не защёлка».
    if peak <= 1e-6:
        message = (f"{part_name} НЕ ЗАЩЁЛКА: по всему пути сдвига {direction} деталь "
                   f"ничего не продавливает (0.00 мм³) — губка не касается препятствий "
                   f"({names}). Либо губки нет или она не достаёт (посмотрите section), "
                   f"либо постановка свободная — тогда объявите method: rigid")
        return CheckResult(**common, status="fail", message=message,
                           value=peak, threshold=max_press_vol,
                           duration_ms=(time.perf_counter() - t0) * 1000)

    ok = peak <= max_press_vol
    message = (f"{part_name} защёлкивается сдвигом {direction}"
               + ("" if ok else " СЛИШКОМ ТУГО")
               + f": продавливает {peak:.2f} мм³ {'≤' if ok else '>'} "
                 f"{max_press_vol:g} (наибольшее на {peak_where:.2f} мм от "
                 f"посадки, дальше {where_free:g} мм — свободно). "
                 f"Препятствия: {names}")
    return CheckResult(**common, status="pass" if ok else "fail", message=message,
                       value=peak, threshold=max_press_vol,
                       evidence=_jam_picture(part, others, direction, peak_where,
                                             part_name, check_id, message) if not ok else (),
                       duration_ms=(time.perf_counter() - t0) * 1000)
