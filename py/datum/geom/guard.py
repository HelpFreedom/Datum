"""Страж CSG — автоматическая замена ручных clean()/watertight() из корпуса.

КОНСТРУКЦИЯ ОБОСНОВАНА ЗАМЕРОМ, а не интуицией.

Напрашивается проверять тело после каждой булевой операции. Так делать нельзя:
manifold3d ленив, и любой запрос свойства форсирует расчёт всей накопленной
цепочки. Измеренные накладные расходы пооперационной проверки:

    20 операций   лениво 3.1 мс → со стражем 12.7 мс   (×4.1)
    60 операций   лениво 7.9 мс → со стражем 49.7 мс   (×6.3)
   168 операций   лениво 26.5 мс → со стражем 251.4 мс (×9.5)

«Дешёвого» запроса не существует: status() стоит столько же, сколько volume().

Поэтому схема такая:

  * КОНТРОЛЬНАЯ ТОЧКА — там, где расчёт нужен в любом случае (возврат из детали,
    экспорт, снимок, прогон проверок). На уже посчитанном теле разбор стоит
    0.0035 мс, decompose() — 0.010 мс, то есть практически ноль.
  * ПОВТОР ПРИ ОТКАЗЕ — журнал хранит ленивые промежуточные результаты; при
    отказе они форсируются по порядку, и находится ПЕРВАЯ операция, на которой
    инварианты сломались. Платим ×9 только когда уже сломано.

Работает это потому, что жёсткая ошибка доживает до конца цепочки: замер
показал, что NonFiniteVertex и NotManifold переживают последующие операции
(см. tests/unit/test_topology.py::test_error_propagates_through_chain).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal

from datum.geom.solid import OpRecord, Solid
from datum.geom.topology import SLIVER_MM3, TopologyReport, analyze

__all__ = ["GuardMode", "Expect", "GuardError", "checkpoint", "locate_failure",
           "describe_places"]


class GuardMode(str, Enum):
    """Режим стража.

    Было ещё два, `full` и `paranoid`, и ни один ничего не делал: страж
    ветвился только на `off`, а проект с `guard = "paranoid"` молча получал
    обычную контрольную точку. Убраны, а не доделаны, потому что добавить им
    нечего: жёсткая ошибка ядра доживает до контрольной точки, и виновную
    операцию находит бисекция (`locate_failure`); тонкие стенки — работа
    проверки `min_wall`, а не стража.
    """

    OFF = "off"
    CHECKPOINT = "checkpoint"


@dataclass(frozen=True, slots=True)
class Expect:
    """Чего мы ждём от тела в контрольной точке."""

    bodies: int | None = 1
    """Сколько самостоятельных тел. None — не проверять."""

    genus: int | None = None
    """Ожидаемый род поверхности. None — не проверять (сквозные отверстия это норма)."""

    allow_sealed_voids: bool = False
    """Замкнутые внутренние полости. По умолчанию это дефект печати."""

    allow_empty: bool = False

    min_volume_mm3: float = 1e-6
    sliver_mm3: float = SLIVER_MM3

    max_slivers: int = 0
    """Сколько «лезвий» терпим молча. Сверх этого — отказ."""


class GuardError(Exception):
    """Отказ стража. Несёт всё нужное для красной карточки в интерфейсе."""

    def __init__(self, *, reason: str, message: str, report: TopologyReport | None = None,
                 solid_name: str = "", culprit: OpRecord | None = None,
                 value: float | int | None = None, threshold: float | int | None = None):
        self.reason = reason
        self.message = message
        self.report = report
        self.solid_name = solid_name
        self.culprit = culprit
        self.value = value
        self.threshold = threshold
        where = f" [{solid_name}]" if solid_name else ""
        if culprit is not None:
            where += f" на операции #{culprit.index} {culprit.describe()}"
        super().__init__(f"{message}{where}")

    def as_dict(self) -> dict:
        return {
            "reason": self.reason,
            "message": self.message,
            "solid": self.solid_name,
            "value": self.value,
            "threshold": self.threshold,
            "culprit_index": self.culprit.index if self.culprit else None,
            "culprit_op": self.culprit.describe() if self.culprit else None,
            "topology": self.report.as_dict() if self.report else None,
        }


def _violations(rep: TopologyReport, expect: Expect) -> list[tuple[str, str, object, object]]:
    """Список нарушений: (код, сообщение, значение, порог). Пустой — всё хорошо."""
    out: list[tuple[str, str, object, object]] = []

    if rep.status != "NoError":
        out.append(("manifold_error",
                    f"ядро вернуло ошибку: {rep.status}", rep.status, "NoError"))
        return out                      # остальное бессмысленно

    if rep.is_empty:
        if not expect.allow_empty:
            out.append(("empty", "тело пустое — операция срезала всё", 0, ">0"))
        return out

    if rep.volume < expect.min_volume_mm3:
        out.append(("degenerate_volume",
                    f"объём {rep.volume:.6g} мм³ ниже порога вырожденности",
                    rep.volume, expect.min_volume_mm3))

    if expect.bodies is not None and rep.bodies != expect.bodies:
        out.append(("body_count",
                    f"тел {rep.bodies}, ожидалось {expect.bodies}"
                    + (" — деталь распалась на куски" if rep.bodies > expect.bodies
                       else " — тел меньше ожидаемого"),
                    rep.bodies, expect.bodies))

    if rep.sealed_voids and not expect.allow_sealed_voids:
        out.append(("sealed_void",
                    f"запаянных внутренних полостей: {rep.sealed_voids} "
                    f"({describe_places(rep.void_volumes, rep.void_places)}) — такую полость "
                    f"нечем заполнить и неоткуда выкачать, печать даст брак"
                    + _section_hint(rep.void_places),
                    rep.sealed_voids, 0))

    if expect.genus is not None and rep.genus != expect.genus:
        out.append(("genus", f"род поверхности {rep.genus}, ожидался {expect.genus}",
                    rep.genus, expect.genus))

    if rep.slivers > expect.max_slivers:
        out.append(("slivers",
                    f"«лезвий» (компонент тоньше {expect.sliver_mm3} мм³): {rep.slivers} "
                    f"({describe_places(rep.sliver_volumes, rep.sliver_places, digits=2)}) — "
                    f"мусор почти совпадающих граней",
                    rep.slivers, expect.max_slivers))

    return out


#: Сколько полостей или лезвий перечислять поимённо. Остальные — числом.
LISTED = 3


def describe_places(volumes, places, *, digits: int = 3) -> str:
    """«6.47 мм³ у (x, y, z), габарит …; 0.66 мм³ у …; и ещё 2».

    Место — в координатах того тела, которое проверялось. Прежде отказ
    называл только объёмы, и каждую из пяти полостей подставки агент искал
    отдельной серией сечений.
    """
    items = []
    for i, v in enumerate(volumes[:LISTED]):
        where = f" {places[i].describe()}" if i < len(places) else ""
        items.append(f"{abs(v):.{digits}g} мм³{where}")
    rest = len(volumes) - LISTED
    return "; ".join(items) + (f"; и ещё {rest}" if rest > 0 else "")


def _section_hint(places) -> str:
    """Чем посмотреть первую полость: срез поперёк её самого тонкого размера.

    Так полость в срезе выходит самой крупной: плоский пузырь, разрезанный
    вдоль, был бы на картинке чертой.
    """
    if not places:
        return ""
    p = places[0]
    axis = min(range(3), key=lambda k: p.size[k])
    plane = {0: "yz", 1: "xz", 2: "xy"}[axis]
    return f". Посмотреть: section(plane={plane}, at={p.centre[axis]:.2f})"


def locate_failure(solid: Solid, expect: Expect) -> OpRecord | None:
    """Найти операцию, на которой сломались инварианты.

    Форсирует промежуточные результаты — это дорого (×4…×9.5), потому и
    вызывается только с пути отказа. Жёсткая ошибка ядра не «чинится»
    дальнейшими операциями — она доживает до каждого ПОТОМКА, а не до каждой
    следующей записи журнала: журнал булевой над двумя телами сложен из обеих
    ветвей. Поэтому путь — от результата к родителям (`OpRecord.parents`):
    вдоль цепочки с одним родителем бисекция, на слиянии — каждая ветвь.
    Виновник — сломанная запись с целыми входами. Номер у неё — место в
    журнале этого тела.
    """
    if not solid.journal:
        return None
    verdict: dict[int, bool] = {}

    def broken(rec: OpRecord) -> bool:
        if id(rec) not in verdict:
            man = rec.result
            # На промежуточных шагах число тел и полости — дело житейское;
            # ищем только то, что необратимо: ошибку ядра и схлопывание в пустоту.
            # Разбор на компоненты здесь не нужен — только статус и пустота.
            verdict[id(rec)] = man is not None and (
                str(man.status()).removeprefix("Error.") != "NoError"
                or (man.is_empty() and not expect.allow_empty))
        return verdict[id(rec)]

    node = solid.journal[-1]
    if not broken(node):
        return None                      # жёсткой ошибки нет, отказ мягкий
    while True:
        chain = [node]                   # от новых к старым, у каждой один родитель
        while len(chain[-1].parents) == 1:
            chain.append(chain[-1].parents[0])
        if broken(chain[-1]):
            node = chain[-1]
        else:
            lo, hi = 0, len(chain) - 1   # lo сломан, hi цел
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if broken(chain[mid]):
                    lo = mid
                else:
                    hi = mid
            node = chain[lo]
            break                        # родитель node цел — виновник найден
        bad = [p for p in node.parents if broken(p)]
        if not bad:
            break
        node = bad[0]
    for pos, rec in enumerate(solid.journal):
        if rec is node:
            return replace(node, index=pos)
    return node                          # запись старше обрезанного журнала


def checkpoint(solid: Solid, expect: Expect | None = None, *,
               mode: GuardMode | Literal["off", "checkpoint"] = GuardMode.CHECKPOINT,
               ) -> TopologyReport:
    """Проверить тело в контрольной точке.

    Возвращает разбор топологии. При нарушении бросает GuardError, предварительно
    отыскав виновную операцию.
    """
    mode = GuardMode(mode)
    expect = expect or Expect()
    rep = analyze(solid.manifold, sliver_mm3=expect.sliver_mm3)
    if mode is GuardMode.OFF:
        return rep

    problems = _violations(rep, expect)
    if not problems:
        return rep

    reason, message, value, threshold = problems[0]
    culprit = None
    if reason in ("manifold_error", "empty"):
        culprit = locate_failure(solid, expect)

    if len(problems) > 1:
        message += "; также: " + "; ".join(p[1] for p in problems[1:])

    raise GuardError(reason=reason, message=message, report=rep,
                     solid_name=solid.name, culprit=culprit,
                     value=value, threshold=threshold)
