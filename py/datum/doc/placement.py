"""Положение деталей на столе — `placement.yaml`.

Деталь перетащили или повернули в окне — это правка ПРОЕКТА, а не вида: сервер
собирает деталь на новом месте, и проверки видят то же, что экран (решение
владельца). Скрипт детали при этом не трогается: положение лежит отдельно и
накладывается поверх того, что скрипт вернул.

    кубик:
      move: [12.5, -4, 0]      # сдвиг, мм
      rotate: [0, 0, 45]       # углы, градусы: сперва X, потом Y, потом Z

Поворот — вокруг ЦЕНТРА ГАБАРИТА детали, какой её вернул скрипт, а не вокруг
начала координат: иначе поворот детали в углу стола уводил бы её за край. Порядок
углов — как у `Manifold.rotate` (замерено: X, затем Y, затем Z; матрица
Rz·Ry·Rx), и окно считает поворот в том же порядке.

Положение — жёсткое движение: объём, тела и полости от него не меняются. Поэтому
кеш хранит деталь, какой её вернул скрипт, а положение накладывается при каждой
сборке — это доли миллисекунды, и ключ кеша не дробится по положениям.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = ["Placement", "PlacementError", "FILE", "load", "save", "update", "apply", "centre_of"]

FILE = "placement.yaml"

HEADER = """\
# Положение деталей на столе. Пишет окно (перетаскивание, поворот) и операция
# part_place; править руками тоже можно.
#   move:   [x, y, z]  — сдвиг, мм
#   rotate: [x, y, z]  — углы, градусы: сперва X, потом Y, потом Z, вокруг
#                        центра габарита детали, какой её вернул скрипт
"""


class PlacementError(ValueError):
    """placement.yaml описан неверно."""


@dataclass(frozen=True, slots=True)
class Placement:
    move: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotate: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def identity(self) -> bool:
        return not any(self.move) and not any(self.rotate)

    def as_dict(self) -> dict:
        return {"move": list(self.move), "rotate": list(self.rotate)}


#: Пределы положения. Дальше 10 м — это не место на столе, а опечатка или
#: ошибка расчёта: сдвиг 1e308 принимался, и крышка latch схлопывалась по X в
#: ноль (ширина тоньше шага float), а сборка отвечала «OK». На 10 м шаг float32
#: в GLB окна — 0.001 мм, форма ещё точна. Угол — до десяти оборотов: окно
#: пишет углы в (−180, 180], а больший — уже не поворот, а мусор во входе.
MOVE_LIMIT_MM = 10_000.0
ANGLE_LIMIT_DEG = 3600.0


def _triple(value, where: str) -> tuple[float, float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in value)):
        raise PlacementError(f"{FILE}: {where} — {value!r}, а нужны три конечных числа")
    out = (float(value[0]), float(value[1]), float(value[2]))
    is_angle = where.endswith("rotate")
    limit = ANGLE_LIMIT_DEG if is_angle else MOVE_LIMIT_MM
    worst = max(out, key=abs)
    if abs(worst) > limit:
        what = (f"угол {worst:g}° — больше десяти оборотов ({limit:g}°); углы пишутся "
                f"в пределах −180…180") if is_angle else (
                f"сдвиг {worst:g} мм — дальше {limit / 1000:g} м: это не место на столе, "
                f"и форма так далеко от начала уже не держится в точности чисел")
        raise PlacementError(f"{FILE}: {where} — {what}")
    return out


def parse(raw) -> dict[str, Placement]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PlacementError(f"{FILE}: ожидалась таблица «деталь → положение»")
    out: dict[str, Placement] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise PlacementError(f"{FILE}: {name}: ожидались move и/или rotate")
        unknown = set(entry) - {"move", "rotate"}
        if unknown:
            raise PlacementError(f"{FILE}: {name}: неизвестные ключи {sorted(unknown)} — "
                                 f"допустимы move и rotate")
        out[str(name)] = Placement(
            move=_triple(entry.get("move", [0, 0, 0]), f"{name}.move"),
            rotate=_triple(entry.get("rotate", [0, 0, 0]), f"{name}.rotate"))
    return out


def load(root: str | Path) -> dict[str, Placement]:
    path = Path(root) / FILE
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PlacementError(f"{FILE} не разобран: {e}") from e
    return parse(raw)


def _clean(v: float, digits: int) -> float | int:
    """Число для файла: без хвостов float и без «-0.0»."""
    r = round(v, digits)
    if r == 0:
        return 0
    return int(r) if r == int(r) else r


def save(root: str | Path, placements: dict[str, Placement]) -> None:
    """Записать положения. Тождественные не пишутся; пусто — файл удаляется."""
    path = Path(root) / FILE
    keep = {n: p for n, p in sorted(placements.items()) if not p.identity}
    if not keep:
        path.unlink(missing_ok=True)
        return
    body = {}
    for name, p in keep.items():
        entry = {}
        if any(p.move):
            entry["move"] = [_clean(v, 4) for v in p.move]
        if any(p.rotate):
            entry["rotate"] = [_clean(v, 3) for v in p.rotate]
        body[name] = entry
    text = HEADER + "\n" + yaml.safe_dump(body, allow_unicode=True, sort_keys=True,
                                          default_flow_style=None)
    from datum.doc.params_edit import write_atomically

    # Свой временный файл у каждого писателя: общий `placement.yaml.tmp` двух
    # одновременных правок затирал друг друга.
    write_atomically(path, text)


def update(root: str | Path, change) -> dict[str, Placement]:
    """Прочитать положения, изменить `change(текущие) → новые` и записать —
    под ИСКЛЮЧИТЕЛЬНОЙ блокировкой проекта.

    Прежде каждая операция брала положения из открытого проекта, меняла свою
    деталь и писала файл целиком: две одновременные правки (окно тащит одну
    деталь, агент ставит другую) обе отвечали «готово», а в файле оставалась
    одна (найдено обзором). Блокировка — `fcntl.flock` на `.datum/
    placement.lock`, и читается файл ЗАНОВО под ней, а не из кеша проекта.
    """
    import fcntl

    lock_path = Path(root) / ".datum" / "placement.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = load(root)
            new = change(dict(current))
            save(root, new)
            return new
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def centre_of(solid) -> tuple[float, float, float]:
    """Центр габарита — точка, вокруг которой поворачивается деталь."""
    from datum.geom.measure import bbox

    (x0, y0, z0), (x1, y1, z1) = bbox(solid)
    return ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)


def apply(solid, placement: Placement | None, centre=None):
    """Поставить деталь: повернуть вокруг центра габарита, затем сдвинуть.

    `centre` — вокруг чего вращать, если не вокруг своего габарита: зона
    детали (`@part(zones=…)`) обязана повернуться вокруг центра ДЕТАЛИ, иначе
    при повороте она уехала бы с язычка.
    """
    if placement is None or placement.identity:
        return solid
    c = centre_of(solid) if centre is None else centre
    m = placement.move
    man = (solid.manifold.translate((-c[0], -c[1], -c[2]))
           .rotate(placement.rotate)
           .translate((c[0] + m[0], c[1] + m[1], c[2] + m[2])))
    return solid.derive(man, "placement", move=list(m), rotate=list(placement.rotate))
