"""Модель сборки: в каком порядке детали становятся на места.

Претензия №1 закрыта проверкой `assembly_path`, но одного знания ей не хватало,
и это знание жило в голове. В корпусе phone-case-gears проверка планки звучала
так: «планка надевается сбоку ПРИ ВСТАВЛЕННОМ ТЕЛЕФОНЕ». Телефон там не потому,
что он интересен, — а потому, что он входит РАНЬШЕ и становится препятствием.

Порядок сборки — это и есть то знание. Записанный однажды, он избавляет от
перечисления препятствий в каждой проверке руками (где их легко забыть, и
проверка станет зелёной по недосмотру) и даёт разнесённому виду его расстановку.

    # assembly.yaml
    order:                  # каждая следующая входит в уже собранное
      - case
      - phone_not_print
      - topbar

    parts:                  # уточнения, все необязательные
      phone_not_print: {insert: "-Y", distance: 180}   # вдвигается сверху
      topbar:           {insert: "-X", distance: 95, tolerance: 3.0}  # сбоку справа

    explode: {gap: 25}

ДВА СПОСОБА ПОСТАВИТЬ ДЕТАЛЬ, и путать их нельзя.

`method: rigid` (по умолчанию) — деталь вдвигается как есть, ничего не задевая.
Проверяется `assembly_path`.

`method: snap` — защёлка: деталь шире проёма и входит с прогибом. Прямолинейного
пути без задевания у неё НЕТ, и объявлять её несобираемой было бы враньём.
Проверяется `snap_insertion`: сколько материала приходится продавить и выходит
ли деталь в конце на свободу. Защёлке обязательны ОДНО направление `insert` (по
какому именно она заводится, геометрия не знает — это замысел) и `snap_vol` —
сколько продавить задумано.

    parts:
      крышка: {method: snap, insert: "-Z", snap_vol: 300}   # вдавливается сверху

`insert` — направление ВСТАВКИ: куда движется деталь, становясь на место. Не
направление снятия, хотя путь тот же и проверка ведёт деталь именно от посадки
наружу. На этом легко ошибиться: корпусный цикл `move(phone, dy=1..175)` уводит
телефон ВВЕРХ, а NOTES говорит «вдвигается сверху» — то есть вставка идёт вниз,
в −Y.

ПЕРВАЯ ДЕТАЛЬ НЕ ПРОВЕРЯЕТСЯ. Её никуда не вставляют: это основание, с которого
сборка начинается. Проверять её было бы не просто бессмысленно — не с чем.

ШАРНИРЫ ОБЪЯВЛЯЮТСЯ, А НЕ РИСУЮТСЯ ФУНКЦИЕЙ ХОДА. Подвижное звено собранного
изделия — крышка на петле, ползун в пазу — описывается движением, и проверка
хода (`joint_sweep`) выводится сама, как собираемость из порядка:

    joints:
      # петля вдоль Y по кромке x = 0, крышка лежит в +X и открывается вверх
      крышка: {kind: revolute, axis: "-Y", origin: [0, 0, 22], range: [0, 110]}
      ползун: {kind: prismatic, axis: "+X", range: [0, 25]}

`revolute` — поворот в градусах вокруг `axis` через `origin` по правилу правой
руки, `prismatic` — сдвиг в мм вдоль `axis`. Координаты — собранной сцены,
положение 0 — как деталь построена. Препятствия — все ДРУГИЕ детали порядка
(звено ходит в собранном изделии); `against` перечисляет их явно, если вместе
со звеном едет ещё что-то. Шаг развёртки — по пути самой быстрой точки звена
(`resolution`, мм), так что длинная крышка не проскочит упор между отсчётами.

ДЕТАЛИ ВНЕ ПОРЯДКА НЕ МОЛЧАТ. Если в проекте есть деталь, не упомянутая в
`order`, она перечисляется в сводке. Молчаливый пропуск здесь — та самая беда,
от которой инструмент и заводился: проверок стало бы меньше, а отчёт выглядел
бы так же зелено.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["Assembly", "Step", "Joint", "AssemblyError"]

#: Направления, которые понимает `assembly_path`. Список продублирован здесь
#: НАРОЧНО: разбор assembly.yaml не должен тянуть за собой ядро геометрии,
#: иначе открытие проекта начнёт зависеть от manifold3d. Совпадение с
#: `checks.motion.DIRECTIONS` закреплено тестом.
DIRECTION_KEYS = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")

#: Сколько миллиметров развёртывать путь, если в файле не сказано иное.
DEFAULT_DISTANCE = 60.0

#: Допустимое задевание по умолчанию — то же, что у самой проверки.
DEFAULT_TOLERANCE = 0.05

#: Зазор разнесённого вида по умолчанию, мм.
DEFAULT_EXPLODE_GAP = 25.0

#: Способы постановки детали.
METHODS = ("rigid", "snap")

#: Ключи уточнений, общие и особые для каждого способа.
COMMON_KEYS = frozenset({"insert", "distance", "method"})
RIGID_KEYS = frozenset({"tolerance"})
SNAP_KEYS = frozenset({"snap_vol"})

#: Ключи верхнего уровня. Незнакомый отвергается: `joint:` вместо `joints:`
#: иначе молча не значил бы ничего, и подвижное звено осталось бы непроверенным.
TOP_KEYS = ("order", "parts", "joints", "explode")

#: Виды шарниров (совпадение с `checks.motion.JOINT_KINDS` закреплено тестом).
JOINT_KINDS = ("revolute", "prismatic")
JOINT_KEYS = frozenset({"kind", "axis", "origin", "range", "against", "tolerance",
                        "resolution"})

#: Шаг развёртки шарнира по пути самой быстрой точки звена, мм.
DEFAULT_JOINT_RESOLUTION = 0.25


class AssemblyError(RuntimeError):
    """Испорченный assembly.yaml."""


@dataclass(frozen=True, slots=True)
class Step:
    """Одна деталь в порядке сборки."""

    part: str
    #: Направления вставки. Пусто — пробовать все шесть.
    insert: tuple[str, ...] = ()
    distance: float = DEFAULT_DISTANCE
    tolerance: float = DEFAULT_TOLERANCE
    #: "rigid" — вдвигается как есть; "snap" — заводится с прогибом.
    method: str = "rigid"
    #: Для защёлки: сколько материала продавить задумано, мм³.
    snap_vol: float = 0.0


@dataclass(frozen=True, slots=True)
class Joint:
    """Подвижное звено собранного изделия."""

    part: str
    kind: str
    axis: tuple[float, float, float] | str
    limits: tuple[float, float]
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: Препятствия поимённо. Пусто — все другие детали порядка.
    against: tuple[str, ...] = ()
    tolerance: float = DEFAULT_TOLERANCE
    resolution: float = DEFAULT_JOINT_RESOLUTION

    @property
    def check_id(self) -> str:
        return f"{self.part}.{'шарнир' if self.kind == 'revolute' else 'ход'}"


@dataclass(frozen=True, slots=True)
class Assembly:
    order: tuple[str, ...] = ()
    steps: dict[str, Step] = field(default_factory=dict)
    explode_gap: float = DEFAULT_EXPLODE_GAP
    joints: dict[str, Joint] = field(default_factory=dict)

    # ------------------------------------------------------------------ чтение
    @staticmethod
    def load(path: Path) -> "Assembly":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise AssemblyError(f"{path.name}: {e}") from e
        if not isinstance(raw, dict):
            raise AssemblyError(f"{path.name}: ожидался словарь с ключом order")
        return Assembly.from_dict(raw, where=path.name)

    @staticmethod
    def from_dict(raw: dict[str, Any], *, where: str = "assembly.yaml") -> "Assembly":
        strange = sorted(set(raw) - set(TOP_KEYS))
        if strange:
            raise AssemblyError(
                f"{where}: непонятные ключи {strange}; известны {', '.join(TOP_KEYS)}"
                + _similar(strange, TOP_KEYS))
        order_raw = raw.get("order", [])
        if not isinstance(order_raw, list) or not all(isinstance(n, str) for n in order_raw):
            raise AssemblyError(f"{where}: order должен быть списком имён деталей")
        order = tuple(order_raw)

        seen: set[str] = set()
        for name in order:
            if name in seen:
                raise AssemblyError(
                    f"{where}: деталь {name!r} в порядке сборки дважды. "
                    f"Вставить её дважды нельзя, а если это две разные детали — "
                    f"им нужны разные имена")
            seen.add(name)

        parts_raw = raw.get("parts", {}) or {}
        if not isinstance(parts_raw, dict):
            raise AssemblyError(f"{where}: parts должен быть отображением имя → уточнения")
        unknown = sorted(set(parts_raw) - seen)
        if unknown:
            raise AssemblyError(
                f"{where}: уточнения заданы для деталей вне порядка сборки: "
                f"{unknown}. Добавьте их в order или уберите уточнения — "
                f"иначе они молча ни на что не влияют")

        steps = {name: _step(name, parts_raw.get(name) or {}, where) for name in order}

        explode = raw.get("explode", {}) or {}
        if not isinstance(explode, dict):
            raise AssemblyError(f"{where}: explode должен быть словарём")
        gap = _positive(explode.get("gap", DEFAULT_EXPLODE_GAP), "explode.gap", where)

        joints_raw = raw.get("joints", {}) or {}
        if not isinstance(joints_raw, dict):
            raise AssemblyError(f"{where}: joints должен быть отображением имя детали → шарнир")
        joints = {name: _joint(name, spec, order, where) for name, spec in joints_raw.items()}

        return Assembly(order=order, steps=steps, explode_gap=gap, joints=joints)

    # --------------------------------------------------------------- свойства
    @property
    def empty(self) -> bool:
        return not self.order

    def unlisted(self, parts: list[str] | set[str]) -> list[str]:
        """Детали проекта, не попавшие в порядок сборки.

        Возвращается, а не замалчивается: непроверенная деталь должна быть
        видна, иначе зелёный отчёт означает «не проверяли», а выглядит как
        «всё хорошо».
        """
        return sorted(set(parts) - set(self.order))

    def missing(self, parts: list[str] | set[str]) -> list[str]:
        """Имена из порядка, которых в проекте нет."""
        return [n for n in self.order if n not in set(parts)]

    # ------------------------------------------------------------- проверки
    def check_decls(self) -> list[dict]:
        """Описания проверок собираемости — по одной на деталь, кроме первой.

        Препятствия каждой — ВСЕ предыдущие детали порядка. Именно это
        делалось в корпусе руками: планка проверялась против корпуса вместе с
        уже вставленным телефоном.
        """
        decls: list[dict] = []
        for i, name in enumerate(self.order[1:], start=1):
            step = self.steps[name]
            obstacles = [f"@{p}" for p in self.order[:i]]
            if step.method == "snap":
                decls.append({
                    "id": f"{name}.защёлка",
                    "use": "snap_insertion",
                    "severity": "blocking",
                    "args": {
                        "part": f"@{name}",
                        "obstacles": obstacles,
                        "direction": step.insert[0],
                        "max_press_vol": step.snap_vol,
                        "distance": step.distance,
                    },
                })
                continue
            args: dict[str, Any] = {
                "part": f"@{name}",
                "obstacles": obstacles,
                "distance": step.distance,
                "max_inter_vol": step.tolerance,
            }
            if step.insert:
                args["dirs"] = list(step.insert)
            decls.append({
                "id": f"{name}.собираемость",
                "use": "assembly_path",
                "severity": "blocking",
                "args": args,
            })
        for name, j in self.joints.items():
            others = j.against or tuple(p for p in self.order if p != name)
            decls.append({
                "id": j.check_id,
                "use": "joint_sweep",
                "severity": "blocking",
                "args": {
                    "part": f"@{name}",
                    "others": [f"@{p}" for p in others],
                    "kind": j.kind,
                    "axis": j.axis if isinstance(j.axis, str) else list(j.axis),
                    "origin": list(j.origin),
                    "limits": list(j.limits),
                    "max_inter_vol": j.tolerance,
                    "resolution": j.resolution,
                },
            })
        return decls


# ------------------------------------------------------------------ разбор
def _step(name: str, raw: Any, where: str) -> Step:
    if not isinstance(raw, dict):
        raise AssemblyError(f"{where}: уточнения для {name!r} должны быть словарём")

    method = raw.get("method", "rigid")
    if method not in METHODS:
        raise AssemblyError(
            f"{where}: у детали {name!r} неизвестный method {method!r}; "
            f"известны {', '.join(METHODS)}")

    # Ключи ЧУЖОГО способа отвергаются, а не игнорируются: `tolerance` у
    # защёлки выглядел бы как заданный порог, а на деле не влиял бы ни на что.
    allowed = COMMON_KEYS | (SNAP_KEYS if method == "snap" else RIGID_KEYS)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        чужие = sorted(set(unknown) & (RIGID_KEYS | SNAP_KEYS))
        подсказка = (f"; у способа {method!r} ключ(и) {чужие} не применяются, "
                     f"порог задаётся "
                     + ("snap_vol" if method == "snap" else "tolerance")) if чужие else ""
        raise AssemblyError(
            f"{where}: у детали {name!r} непонятные ключи {unknown}; "
            f"известны {', '.join(sorted(allowed))}{подсказка}")

    insert_raw = raw.get("insert")
    if insert_raw is None:
        insert: tuple[str, ...] = ()
    else:
        keys = [insert_raw] if isinstance(insert_raw, str) else list(insert_raw)
        insert = tuple(_direction(k, name, where) for k in keys)
        if not insert:
            raise AssemblyError(
                f"{where}: у детали {name!r} пустой insert. Уберите ключ — "
                f"тогда проверятся все шесть направлений")

    snap_vol = 0.0
    if method == "snap":
        # Направление защёлки геометрия не знает — это замысел. Угадывать его
        # «где продавить меньше» нельзя: у крышки latch вбок продавливается
        # меньше, чем вверх, только вбок она пошла бы СКВОЗЬ СТЕНКУ.
        if len(insert) != 1:
            raise AssemblyError(
                f"{where}: защёлке {name!r} нужно ровно одно направление insert "
                f"(получено {len(insert)}). Куда она заводится, геометрия не "
                f"знает — это замысел")
        if "snap_vol" not in raw:
            raise AssemblyError(
                f"{where}: защёлке {name!r} нужен snap_vol — сколько материала "
                f"продавить задумано, мм³. Умолчания у него нет: это зависит от "
                f"замысла, и подставить число значило бы решить за вас, "
                f"насколько тугой должна быть защёлка")
        snap_vol = _positive(raw["snap_vol"], f"{name}.snap_vol", where)

    return Step(
        part=name,
        insert=insert,
        distance=_positive(raw.get("distance", DEFAULT_DISTANCE), f"{name}.distance", where),
        tolerance=_positive(raw.get("tolerance", DEFAULT_TOLERANCE), f"{name}.tolerance", where),
        method=method,
        snap_vol=snap_vol,
    )


def _joint(name: Any, raw: Any, order: tuple[str, ...], where: str) -> Joint:
    if name not in order:
        raise AssemblyError(
            f"{where}: шарнир у детали {name!r}, которой нет в order. Звено ходит в "
            f"СОБРАННОМ изделии — добавьте деталь в порядок сборки"
            + _similar([str(name)], order))
    if not isinstance(raw, dict):
        raise AssemblyError(f"{where}: шарнир {name!r} должен быть словарём "
                            f"(kind, axis, range, …)")
    unknown = sorted(set(raw) - JOINT_KEYS)
    if unknown:
        hint = ""
        if {"limits", "travel", "angles"} & set(unknown):
            hint = "; пределы хода задаются ключом range: [от, до]"
        elif {"parent", "obstacles", "others"} & set(unknown):
            hint = "; препятствия перечисляются ключом against"
        raise AssemblyError(f"{where}: у шарнира {name!r} непонятные ключи {unknown}; "
                            f"известны {', '.join(sorted(JOINT_KEYS))}{hint}")
    for key in ("kind", "axis", "range"):
        if key not in raw:
            raise AssemblyError(f"{where}: шарниру {name!r} нужен {key}. Умолчания нет: "
                                f"куда и насколько ходит звено, знает замысел, а не геометрия")
    kind = raw["kind"]
    if kind not in JOINT_KINDS:
        hint = {"hinge": "revolute", "rotary": "revolute", "slider": "prismatic",
                "linear": "prismatic"}.get(str(kind).lower())
        raise AssemblyError(
            f"{where}: у шарнира {name!r} неизвестный kind {kind!r}; известны "
            f"{', '.join(JOINT_KINDS)}" + (f" — вероятно, {hint}" if hint else ""))
    axis = _vector_or_direction(raw["axis"], f"{name}.axis", where)
    if "origin" in raw:
        if kind == "prismatic":
            raise AssemblyError(f"{where}: у ползуна {name!r} origin ни на что не влияет — "
                                f"сдвиг от точки оси не зависит. Уберите ключ")
        origin = _vector(raw["origin"], f"{name}.origin", where)
    elif kind == "revolute":
        raise AssemblyError(f"{where}: шарниру {name!r} нужен origin — точка на оси "
                            f"поворота [x, y, z], мм. Без неё ось прошла бы через ноль "
                            f"сцены, а не через петлю")
    else:
        origin = (0.0, 0.0, 0.0)
    rng = raw["range"]
    if (not isinstance(rng, list) or len(rng) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rng)):
        raise AssemblyError(f"{where}: {name}.range — пара чисел [от, до], получено {rng!r}")
    lo, hi = float(rng[0]), float(rng[1])
    if not hi > lo:
        raise AssemblyError(f"{where}: {name}.range {rng}: конец хода должен быть больше "
                            f"начала. Обратное направление — ось с другим знаком")
    if kind == "revolute" and hi - lo > 360:
        raise AssemblyError(f"{where}: {name}.range {rng}: больше полного оборота, "
                            f"а у жёсткого поворота дальше 360° нового нет")
    against_raw = raw.get("against", [])
    if isinstance(against_raw, str):
        against_raw = [against_raw]
    if not isinstance(against_raw, list) or not all(isinstance(v, str) for v in against_raw):
        raise AssemblyError(f"{where}: {name}.against — список имён деталей")
    for other in against_raw:
        if other == name:
            raise AssemblyError(f"{where}: {name}.against: деталь не мешает сама себе")
        if other not in order:
            raise AssemblyError(f"{where}: {name}.against: детали {other!r} нет в order"
                                + _similar([other], order))
    if "against" in raw and not against_raw:
        raise AssemblyError(f"{where}: {name}.against пуст — проверять было бы не с чем. "
                            f"Уберите ключ, тогда препятствия — все другие детали")
    return Joint(
        part=name, kind=kind, axis=axis, limits=(lo, hi), origin=origin,
        against=tuple(against_raw),
        tolerance=_positive(raw.get("tolerance", DEFAULT_TOLERANCE), f"{name}.tolerance", where),
        resolution=_positive(raw.get("resolution", DEFAULT_JOINT_RESOLUTION),
                             f"{name}.resolution", where),
    )


def _vector(value: Any, field_name: str, where: str) -> tuple[float, float, float]:
    if (not isinstance(value, list) or len(value) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise AssemblyError(f"{where}: {field_name} — три числа [x, y, z], получено {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))


def _vector_or_direction(value: Any, field_name: str, where: str):
    if isinstance(value, str):
        k = value.strip().upper()
        if k not in DIRECTION_KEYS:
            raise AssemblyError(f"{where}: {field_name} {value!r}: ось — одно из "
                                f"{', '.join(DIRECTION_KEYS)} или вектор [x, y, z]")
        return k
    v = _vector(value, field_name, where)
    if not any(v):
        raise AssemblyError(f"{where}: {field_name}: ось не может быть нулевым вектором")
    return v


def _similar(names: list[str], known) -> str:
    import difflib

    tips = []
    for n in names:
        close = difflib.get_close_matches(n, list(known), n=1, cutoff=0.6)
        if close:
            tips.append(f"{n!r} → {close[0]!r}")
    return f" (может быть, {', '.join(tips)}?)" if tips else ""


def _direction(key: Any, name: str, where: str) -> str:
    if not isinstance(key, str):
        raise AssemblyError(f"{where}: направление у {name!r} должно быть строкой, "
                            f"получено {key!r}")
    k = key.strip().upper()
    if k not in DIRECTION_KEYS:
        raise AssemblyError(
            f"{where}: у детали {name!r} неизвестное направление {key!r}; "
            f"известны {', '.join(DIRECTION_KEYS)}")
    return k


def _positive(value: Any, field_name: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssemblyError(f"{where}: {field_name} должно быть числом, получено {value!r}")
    if value <= 0:
        raise AssemblyError(f"{where}: {field_name} должно быть больше нуля, получено {value}")
    return float(value)
