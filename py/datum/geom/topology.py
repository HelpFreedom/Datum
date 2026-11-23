"""Топологический разбор тела — основа стража CSG.

Измерено на manifold3d 3.5.3 (см. tests/unit/test_topology.py):

    случай                    decompose()  объёмы компонент      genus
    одно сплошное тело             1       [+1000.0]               0
    два разделённых тела           2       [+1000.0, +1000.0]     −1
    ЗАМКНУТАЯ ВНУТРЕННЯЯ ПОЛОСТЬ   2       [+1000.0,   −64.0]     −1
    сквозное отверстие             1       [ +874.5]              +1

Отсюда: `genus` сам по себе непригоден как признак — у «двух тел» и у «полости»
он одинаковый. Различает их ЗНАК ОБЪЁМА компоненты. Отрицательная компонента —
это запаянная внутренняя полость: реальный дефект печати (пузырь, который нечем
заполнить и неоткуда выкачать).

Инвариант «полость даёт отрицательный объём» вытекает из ориентации оболочки, но
в API manifold3d не задокументирован, поэтому версия пакета пинуется, а тест
воспроизводит всю таблицу целиком.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import trimesh
from manifold3d import Manifold

from datum.geom.convert import from_trimesh

__all__ = ["TopologyReport", "Place", "analyze", "topology_report", "SLIVER_MM3"]

#: Компоненты меньше этого объёма считаются «лезвиями» — мусором булевых операций.
SLIVER_MM3 = 0.01


@dataclass(frozen=True)
class Place:
    """Где лежит компонента: габарит в координатах тела, мм."""

    lo: tuple[float, float, float]
    hi: tuple[float, float, float]

    @property
    def centre(self) -> tuple[float, float, float]:
        return tuple((a + b) / 2 for a, b in zip(self.lo, self.hi))   # type: ignore[return-value]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(b - a for a, b in zip(self.lo, self.hi))         # type: ignore[return-value]

    def describe(self) -> str:
        """«у (12.3, 45.6, 7.8), габарит 1.2×0.8×0.5 мм»."""
        c, d = self.centre, self.size
        return (f"у ({c[0]:.2f}, {c[1]:.2f}, {c[2]:.2f}), "
                f"габарит {d[0]:.2f}×{d[1]:.2f}×{d[2]:.2f} мм")


@dataclass(frozen=True)
class TopologyReport:
    """Разбор тела на составляющие. Объёмы в мм³."""

    status: str
    is_empty: bool
    volume: float
    genus: int
    triangles: int
    body_volumes: tuple[float, ...] = field(default_factory=tuple)
    void_volumes: tuple[float, ...] = field(default_factory=tuple)
    sliver_volumes: tuple[float, ...] = field(default_factory=tuple)
    # Места полостей и лезвий — в порядке их объёмов выше. В словарь (и в кеш)
    # НЕ идут: координаты — в системе тела, которое разбирали, а кеш хранит
    # деталь без положения, и место из него оказалось бы не там, где деталь
    # стоит. Нужны они сообщению стража, которое строится сразу.
    void_places: tuple[Place, ...] = field(default_factory=tuple, compare=False)
    sliver_places: tuple[Place, ...] = field(default_factory=tuple, compare=False)

    @property
    def bodies(self) -> int:
        """Число самостоятельных тел (без учёта лезвий и полостей)."""
        return len(self.body_volumes)

    @property
    def sealed_voids(self) -> int:
        """Число запаянных внутренних полостей."""
        return len(self.void_volumes)

    @property
    def slivers(self) -> int:
        return len(self.sliver_volumes)

    @property
    def ok(self) -> bool:
        return self.status == "NoError" and not self.is_empty

    @staticmethod
    def from_dict(d: dict) -> "TopologyReport":
        return TopologyReport(
            status=d.get("status", "NoError"), is_empty=bool(d.get("is_empty", False)),
            volume=float(d.get("volume_mm3", 0.0)), genus=int(d.get("genus", 0)),
            triangles=int(d.get("triangles", 0)),
            body_volumes=tuple(d.get("body_volumes_mm3", ())),
            void_volumes=tuple(d.get("void_volumes_mm3", ())),
            sliver_volumes=tuple() if not d.get("slivers") else tuple([0.0] * int(d["slivers"])))

    def as_dict(self, *, round_to: int | None = 4) -> dict:
        """Словарь с разбором. round_to=None — без округления.

        Округление нужно для показа человеку, но хранить так нельзя: кеш
        возвращает сохранённые метрики обратно в отчёт, и округлённый объём
        начинает отличаться от посчитанного. Разница ничтожна, зато порождает
        вопрос «почему число поменялось» на ровном месте.
        """
        rnd = (lambda v: v) if round_to is None else (lambda v: round(v, round_to))
        return {
            "status": self.status,
            "is_empty": self.is_empty,
            "volume_mm3": rnd(self.volume),
            "genus": self.genus,
            "triangles": self.triangles,
            "bodies": self.bodies,
            "sealed_voids": self.sealed_voids,
            "slivers": self.slivers,
            "body_volumes_mm3": [rnd(v) for v in self.body_volumes],
            "void_volumes_mm3": [rnd(v) for v in self.void_volumes],
        }


def analyze(man: Manifold, *, sliver_mm3: float = SLIVER_MM3) -> TopologyReport:
    """Разобрать Manifold.

    Внимание: этот вызов ФОРСИРУЕТ расчёт ленивой цепочки manifold3d. Звать его
    следует в контрольных точках, где расчёт нужен в любом случае, а не после
    каждой булевой операции — замер показал накладные ×4…×9.5 (см. план, §3.1).
    """
    status = str(man.status()).removeprefix("Error.")
    if man.is_empty():
        return TopologyReport(status=status, is_empty=True, volume=0.0, genus=0, triangles=0)

    bodies: list[float] = []
    voids: list[tuple[float, Place]] = []
    slivers: list[tuple[float, Place]] = []
    for part in man.decompose():
        v = float(part.volume())
        # Крошечная компонента — численный мусор, КАКОГО БЫ ЗНАКА ОНА НИ БЫЛА.
        # На реальном корпусе phone-case-gears v4 встретились «полости» объёмом
        # ровно -0.00000000 мм³: считать их запаянными пузырями — ложная тревога.
        if abs(v) < sliver_mm3:
            slivers.append((abs(v), _place(part)))
        elif v < 0.0:
            voids.append((v, _place(part)))  # оболочка вывернута наружу → замкнутая полость
        else:
            bodies.append(v)
    voids.sort(key=lambda t: t[0])
    slivers.sort(key=lambda t: -t[0])

    return TopologyReport(
        status=status,
        is_empty=False,
        volume=float(man.volume()),
        genus=int(man.genus()),
        triangles=int(man.num_tri()),
        body_volumes=tuple(sorted(bodies, reverse=True)),
        void_volumes=tuple(v for v, _ in voids),
        sliver_volumes=tuple(v for v, _ in slivers),
        void_places=tuple(p for _, p in voids),
        sliver_places=tuple(p for _, p in slivers),
    )


def _place(part: Manifold) -> Place:
    x0, y0, z0, x1, y1, z1 = (float(v) for v in part.bounding_box())
    return Place((x0, y0, z0), (x1, y1, z1))


def topology_report(obj: Manifold | trimesh.Trimesh, *, sliver_mm3: float = SLIVER_MM3) -> dict:
    """Удобная обёртка: принимает и Manifold, и Trimesh, возвращает словарь."""
    man = obj if isinstance(obj, Manifold) else from_trimesh(obj)
    return analyze(man, sliver_mm3=sliver_mm3).as_dict()
