"""Реестр параметров — «реестр незамеренного».

Претензия из ТЗ: «Размер хвостовика, сила магнита, вылет кончика — я угадал и
зашил числами. Должен быть явный блок неизвестных с диапазонами, и проверки
должны гоняться по диапазону».

В рабочем процессе CAD это выглядело так (`phone-case-gears/work/params.py`):

    PHONE_T = 8.2   # пользователь намерил 9.0 — перемерить

Конфликт двух измерений — в комментарии. Его нельзя ни запросить, ни прогнать по
диапазону, ни превратить в красную проверку. Здесь у каждого числа есть
происхождение:

    measured — намерено, с указанием чем и по чему;
    assumed  — УГАДАНО, обязан быть диапазон; проверки гоняются по его краям;
    derived  — вычисляется формулой из других;
    standard — из каталога (крепёж, подшипники), с ссылкой;
    chosen   — конструктивное решение, обосновано словами.

Пока в проекте есть assumed-параметры, помеченные `blocking`, снимок не
считается готовым к печати.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator, Literal

import yaml

from datum.doc.expr import CONSTANTS, FUNCTIONS, ExpressionError, evaluate, referenced_names
from datum.doc.units import CANONICAL, from_canonical, to_canonical, unit_kind

__all__ = ["Provenance", "Conflict", "Param", "ParamRegistry", "ParamError", "ParamProxy"]

#: Длина параметра — не больше 100 м (см. `ParamRegistry.resolve`).
LENGTH_MAX_MM = 100_000.0

Provenance = Literal["measured", "assumed", "derived", "standard", "chosen"]
_PROVENANCES = ("measured", "assumed", "derived", "standard", "chosen")


class ParamError(ValueError):
    """Ошибка в описании параметра."""


@dataclass(frozen=True, slots=True)
class Conflict:
    """Другое измерение того же размера, не совпавшее с принятым."""
    value: float
    source: str = ""
    date: str = ""


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    provenance: Provenance
    value: float | None = None          # в канонических единицах
    unit: str = "mm"
    expr: str = ""
    range: tuple[float, float] | None = None
    tolerance: float | None = None
    source: str = ""
    instrument: str = ""
    reason: str = ""
    ref: str = ""
    note: str = ""
    measure_hint: str = ""
    validate_with: str = ""
    blocking: bool = False
    tags: tuple[str, ...] = ()
    conflicts: tuple[Conflict, ...] = ()

    # ---------------------------------------------------------------- свойства
    @property
    def is_unknown(self) -> bool:
        """Угаданное число — то, что должно попасть в развёртку по диапазону."""
        return self.provenance == "assumed"

    @property
    def display(self) -> float | None:
        """Значение в единице ввода."""
        return None if self.value is None else from_canonical(self.value, self.unit)

    def with_value(self, value: float) -> "Param":
        return replace(self, value=float(value))

    def range_points(self) -> tuple[float, ...]:
        """Точки развёртки: низ, принятое, верх — без повторов."""
        if not self.range:
            return (self.value,) if self.value is not None else ()
        lo, hi = self.range
        pts = [lo, hi] + ([self.value] if self.value is not None else [])
        out: list[float] = []
        for p in sorted(pts):
            if not out or abs(p - out[-1]) > 1e-12:
                out.append(p)
        return tuple(out)

    def as_dict(self) -> dict:
        d: dict[str, Any] = {"provenance": self.provenance, "unit": self.unit}
        if self.value is not None:
            d["value"] = round(self.display, 6)
        for k in ("expr", "source", "instrument", "reason", "ref", "note",
                  "measure_hint", "validate_with"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.range:
            d["range"] = [round(from_canonical(v, self.unit), 6) for v in self.range]
        if self.tolerance is not None:
            d["tolerance"] = round(from_canonical(self.tolerance, self.unit), 6)
        if self.blocking:
            d["blocking"] = True
        if self.tags:
            d["tags"] = list(self.tags)
        if self.conflicts:
            d["conflicts"] = [{"value": round(from_canonical(c.value, self.unit), 6),
                               "source": c.source, "date": c.date} for c in self.conflicts]
        return d


#: Поля описания параметра. Опечатка вроде `measure_hnt` раньше молча
#: пропадала: поле просто не читалось.
_FIELDS = frozenset({
    "value", "unit", "provenance", "expr", "range", "tolerance", "source",
    "instrument", "reason", "ref", "note", "measure_hint", "validate_with",
    "blocking", "tags", "conflicts",
})

#: Имена, которые параметру носить нельзя. Функции и константы формул
#: перекрывали бы параметр в выражениях (`B: pi` брал константу, а не
#: параметр `pi`); атрибуты прокси перекрывали бы его в скрипте детали
#: (`P.values` возвращал словарь всех значений).
_RESERVED = frozenset({"values", "reads", "mark_read", "reset_reads"}
                      | set(FUNCTIONS) | set(CONSTANTS))


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
        raise ParamError(f"имя параметра {name!r}: нужен идентификатор — буквы, цифры и "
                         f"подчёркивание, не с цифры и не с подчёркивания; иначе его не "
                         f"написать ни в формуле, ни как P.ИМЯ")
    if name in _RESERVED:
        raise ParamError(f"имя параметра {name!r} занято: так называется функция, "
                         f"константа формул или служебное поле. Выберите другое")
    return name


def _param_from_dict(name: str, raw: dict) -> Param:
    _check_name(name)
    if not isinstance(raw, dict):
        raise ParamError(f"{name}: описание должно быть словарём, получено {type(raw).__name__}")
    extra = sorted(set(map(str, raw)) - _FIELDS)
    if extra:
        raise ParamError(f"{name}: неизвестные поля {extra}; допустимы: {sorted(_FIELDS)}")

    prov = raw.get("provenance")
    if prov not in _PROVENANCES:
        raise ParamError(f"{name}: происхождение должно быть одним из {_PROVENANCES}, "
                         f"получено {prov!r}")
    unit = str(raw.get("unit", "mm"))
    try:
        unit_kind(unit)
    except ValueError as e:
        raise ParamError(f"{name}: {e}") from e

    def number(v: Any, what: str) -> float:
        """Число из YAML — или отказ с именем параметра и поля.

        Строку принимаем: PyYAML читает `1e-3` (без точки в мантиссе) как
        строку, и отвергать её было бы ловушкой. Логическое значение — нет:
        `value: yes` превращался в 1.0 без единого слова.
        """
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ParamError(f"{name}: {what} должно быть числом, получено {v!r}")
        try:
            x = float(v)
        except ValueError:
            raise ParamError(f"{name}: {what} должно быть числом, получено {v!r}") from None
        if x != x or x in (float("inf"), float("-inf")):
            raise ParamError(f"{name}: {what} = {v!r} — это не размер")
        return to_canonical(x, unit)

    value = None if raw.get("value") is None else number(raw["value"], "value")
    rng = raw.get("range")
    if rng is not None:
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
            raise ParamError(f"{name}: range должен быть парой [низ, верх], получено {rng!r}")
        lo, hi = number(rng[0], "низ диапазона"), number(rng[1], "верх диапазона")
        if hi < lo:
            raise ParamError(f"{name}: верх диапазона {rng[1]} меньше низа {rng[0]}")
        rng = (lo, hi)

    tolerance = None if raw.get("tolerance") is None else number(raw["tolerance"], "tolerance")
    if tolerance is not None and tolerance < 0:
        raise ParamError(f"{name}: допуск не бывает отрицательным, получено {raw['tolerance']!r}")

    raw_conflicts = raw.get("conflicts") or []
    if not isinstance(raw_conflicts, list):
        raise ParamError(f"{name}: conflicts — список измерений, получено {raw_conflicts!r}")
    conflicts = []
    for c in raw_conflicts:
        if not isinstance(c, dict) or "value" not in c:
            raise ParamError(f"{name}: каждое расхождение — словарь с value и source, "
                             f"получено {c!r}")
        conflicts.append(Conflict(value=number(c["value"], "value расхождения"),
                                  source=str(c.get("source", "")),
                                  date=str(c.get("date", ""))))

    blocking = raw.get("blocking", False)
    if not isinstance(blocking, bool):
        # `blocking: "no"` раньше давал True: непустая строка истинна.
        raise ParamError(f"{name}: blocking — да или нет (true/false), получено {blocking!r}")
    if blocking and prov != "assumed":
        raise ParamError(f"{name}: blocking имеет смысл только у угаданного (assumed): "
                         f"измеренное или выбранное печати не мешает")

    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        # `tags: abc` раньше становился ('a', 'b', 'c').
        raise ParamError(f"{name}: tags — список, получено {tags!r}")

    expr = raw.get("expr", "")
    if expr and not isinstance(expr, str):
        raise ParamError(f"{name}: expr — строка с формулой, получено {expr!r}")

    p = Param(
        name=name, provenance=prov, value=value, unit=unit,
        expr=str(expr or ""), range=rng, tolerance=tolerance,
        source=str(raw.get("source", "")), instrument=str(raw.get("instrument", "")),
        reason=str(raw.get("reason", "")), ref=str(raw.get("ref", "")),
        note=str(raw.get("note", "")), measure_hint=str(raw.get("measure_hint", "")),
        validate_with=str(raw.get("validate_with", "")),
        blocking=blocking, tags=tuple(str(t) for t in tags), conflicts=tuple(conflicts))
    _validate(p)
    if p.expr:
        try:
            referenced_names(p.expr)            # формула разбирается сразу, с именем
        except ExpressionError as e:
            raise ParamError(f"{name}: {e}") from e
    return p


def _validate(p: Param) -> None:
    """Правила, нарушение которых — ошибка схемы, а не предупреждение."""
    if p.provenance == "assumed":
        if p.range is None:
            raise ParamError(
                f"{p.name}: угаданный параметр обязан иметь диапазон. Смысл в том и "
                f"состоит: проверки гоняются по его краям, и сразу видно, какие из них "
                f"зависят от этого неизвестного")
        if p.value is None:
            raise ParamError(f"{p.name}: угаданному параметру нужно принятое значение")
        lo, hi = p.range
        if not (lo - 1e-12 <= p.value <= hi + 1e-12):
            raise ParamError(f"{p.name}: принятое значение {p.display:g} вне диапазона "
                             f"[{from_canonical(lo, p.unit):g}, {from_canonical(hi, p.unit):g}]")
    elif p.provenance == "derived":
        if not p.expr:
            raise ParamError(f"{p.name}: производный параметр обязан иметь формулу (expr)")
        if p.value is not None:
            raise ParamError(f"{p.name}: у производного параметра не может быть своего "
                             f"значения — оно вычисляется формулой")
    else:
        if p.value is None:
            raise ParamError(f"{p.name}: параметру нужно значение")
        if p.expr:
            raise ParamError(f"{p.name}: формула допустима только у производных")


class ParamProxy:
    """Доступ к параметрам с записью обращений.

    Именно отсюда берётся `depends_on` у деталей и проверок: не из деклараций,
    которые разъезжаются с кодом, а из того, что код реально прочитал.
    """

    __slots__ = ("_values", "_read")

    def __init__(self, values: dict[str, float]):
        object.__setattr__(self, "_values", dict(values))
        object.__setattr__(self, "_read", set())

    def __getattr__(self, name: str) -> float:
        values = object.__getattribute__(self, "_values")
        if name not in values:
            raise AttributeError(
                f"параметра {name!r} нет в реестре; известны: {sorted(values)[:12]}…")
        object.__getattribute__(self, "_read").add(name)
        return values[name]

    def __getitem__(self, name: str) -> float:
        return getattr(self, name)

    def __setattr__(self, name: str, value: Any) -> None:       # pragma: no cover
        raise AttributeError("параметры менять из скрипта детали нельзя — "
                             "их источник это реестр проекта")

    def __contains__(self, name: str) -> bool:
        return name in object.__getattribute__(self, "_values")

    @property
    def values(self) -> dict[str, float]:
        """Копия всех значений — для вычисления выражений без записи обращений."""
        return dict(object.__getattribute__(self, "_values"))

    @property
    def reads(self) -> frozenset[str]:
        return frozenset(object.__getattribute__(self, "_read"))

    def mark_read(self, names) -> None:
        """Отметить обращения, случившиеся не через этот прокси.

        Нужно при повторном использовании ранее собранной детали: сама сборка не
        повторяется, но её зависимости обязаны попасть в текущий учёт, иначе
        проверка окажется «ни от чего не зависящей».
        """
        object.__getattribute__(self, "_read").update(names)

    def reset_reads(self) -> None:
        object.__getattribute__(self, "_read").clear()


class _StrictLoader(yaml.SafeLoader):
    """Безопасный загрузчик, отвергающий повторный ключ.

    Обычный `safe_load` при двух `WALL:` молча берёт последний — и параметр,
    который человек правит в начале файла, оказывается не тем, что читает
    сборка.
    """


def _no_duplicates(loader: _StrictLoader, node, deep: bool = False):
    seen: dict = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ParamError(f"параметр {key!r} описан дважды (строки "
                             f"{seen[key] + 1} и {key_node.start_mark.line + 1}): "
                             f"YAML молча взял бы последний")
        seen[key] = key_node.start_mark.line
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


@dataclass
class ParamRegistry:
    """Все параметры проекта вместе с разрешением производных."""

    params: dict[str, Param] = field(default_factory=dict)

    # ---------------------------------------------------------------- загрузка
    @staticmethod
    def from_dict(raw: dict) -> "ParamRegistry":
        reg = ParamRegistry({name: _param_from_dict(name, body)
                             for name, body in (raw or {}).items()})
        reg.resolve()
        return reg

    @staticmethod
    def load(path: str | Path) -> "ParamRegistry":
        path = Path(path)
        return ParamRegistry.loads(path.read_text(encoding="utf-8"), where=path.name)

    @staticmethod
    def loads(text: str, *, where: str = "params/base.yaml") -> "ParamRegistry":
        try:
            data = yaml.load(text, Loader=_StrictLoader) or {}
        except yaml.YAMLError as e:
            raise ParamError(f"{where}: YAML не разобран: {e}") from e
        if not isinstance(data, dict):
            raise ParamError(f"{where}: ожидался словарь «имя: описание», "
                             f"получено {type(data).__name__}")
        return ParamRegistry.from_dict(data)

    def with_defaults(self, raw: dict[str, dict]) -> "ParamRegistry":
        """Добавить параметры, которых в реестре нет (каталог материала).

        Объявленное проектом перебивает умолчание: свой замер модуля важнее
        паспорта производителя.
        """
        extra = {name: _param_from_dict(name, body) for name, body in raw.items()
                 if name not in self.params}
        if not extra:
            return self
        reg = ParamRegistry({**self.params, **extra})
        reg.resolve()
        return reg

    def save(self, path: str | Path) -> None:
        body = {n: p.as_dict() for n, p in sorted(self.params.items())}
        Path(path).write_text(
            yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8")

    # ---------------------------------------------------------------- доступ
    def __contains__(self, name: str) -> bool:
        return name in self.params

    def __getitem__(self, name: str) -> Param:
        return self.params[name]

    def __iter__(self) -> Iterator[Param]:
        return iter(self.params.values())

    def __len__(self) -> int:
        return len(self.params)

    @property
    def unknowns(self) -> tuple[Param, ...]:
        """Угаданные параметры — те, что стоит измерить."""
        return tuple(p for p in self.params.values() if p.is_unknown)

    @property
    def blocking_unknowns(self) -> tuple[Param, ...]:
        return tuple(p for p in self.unknowns if p.blocking)

    @property
    def conflicted(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params.values() if p.conflicts)

    # ---------------------------------------------------------------- значения
    def resolve(self, overrides: dict[str, float] | None = None) -> dict[str, float]:
        """Значения всех параметров с учётом формул и подстановок.

        `overrides` — значения в КАНОНИЧЕСКИХ единицах (так работает развёртка
        по диапазонам).
        """
        overrides = dict(overrides or {})
        unknown = set(overrides) - set(self.params)
        if unknown:
            raise ParamError(f"подстановка для неизвестных параметров: {sorted(unknown)}")

        values: dict[str, float] = {}
        pending: dict[str, Param] = {}
        for name, p in self.params.items():
            if name in overrides:
                values[name] = float(overrides[name])
            elif p.provenance == "derived":
                pending[name] = p
            else:
                values[name] = float(p.value)      # проверено в _validate

        # Разрешаем формулы, пока получается; что не разрешилось — цикл или опечатка.
        while pending:
            progressed = False
            for name in list(pending):
                deps = referenced_names(pending[name].expr)
                if deps <= set(values):
                    try:
                        values[name] = evaluate(pending[name].expr, values)
                    except ExpressionError as e:
                        raise ParamError(f"{name}: {e}") from e
                    del pending[name]
                    progressed = True
            if not progressed:
                stuck = sorted(pending)
                missing = {n: sorted(referenced_names(pending[n].expr) - set(values))
                           for n in stuck}
                raise ParamError(
                    f"формулы не разрешились: {stuck}. Либо кольцевая зависимость, "
                    f"либо опечатка в имени. Не хватает: {missing}")
        # Последний рубеж для ВСЕХ путей: файл, param_set, подстановки снимка и
        # развёртки, формулы. Подстановка CASE_H=1e308 в снимке проходила мимо
        # правил реестра, корпус собирался высотой 1e308, и в историю версий
        # ложился объём nan. Длина дальше 100 м — не размер печатной детали.
        for name, v in values.items():
            if not math.isfinite(v):
                raise ParamError(f"{name} = {v!r} — не число")
            p = self.params[name]
            if unit_kind(p.unit) == "length" and abs(v) > LENGTH_MAX_MM:
                raise ParamError(f"{name} = {v:g} мм — по модулю больше {LENGTH_MAX_MM / 1000:g} м: это "
                                 f"не размер печатной детали, а ошибка в числе или единицах")
        return values

    def proxy(self, overrides: dict[str, float] | None = None) -> ParamProxy:
        return ParamProxy(self.resolve(overrides))

    def depends_on(self, name: str) -> frozenset[str]:
        """Транзитивные зависимости производного параметра."""
        seen: set[str] = set()
        stack = [name]
        while stack:
            cur = stack.pop()
            p = self.params.get(cur)
            if p is None or p.provenance != "derived":
                continue
            for dep in referenced_names(p.expr):
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        return frozenset(seen)

    # ---------------------------------------------------------------- отчёт
    def summary(self) -> dict:
        by_prov: dict[str, int] = {}
        for p in self.params.values():
            by_prov[p.provenance] = by_prov.get(p.provenance, 0) + 1
        return {
            "total": len(self.params),
            "by_provenance": by_prov,
            "unknowns": [p.name for p in self.unknowns],
            "blocking_unknowns": [p.name for p in self.blocking_unknowns],
            "conflicted": [p.name for p in self.conflicted],
            "print_ready": not self.blocking_unknowns,
        }

    def report(self) -> str:
        lines = []
        s = self.summary()
        lines.append(f"Параметров: {s['total']} " +
                     ", ".join(f"{k}: {v}" for k, v in sorted(s["by_provenance"].items())))
        if self.unknowns:
            lines.append("Незамеренное (проверки гоняются по диапазонам):")
            for p in self.unknowns:
                lo, hi = (from_canonical(v, p.unit) for v in p.range)
                mark = " ← мешает печати" if p.blocking else ""
                hint = f"; {p.measure_hint}" if p.measure_hint else ""
                lines.append(f"  {p.name} = {p.display:g} {p.unit} "
                             f"[{lo:g}…{hi:g}]{mark}{hint}")
        if self.conflicted:
            lines.append("Расхождения в измерениях:")
            for p in self.conflicted:
                for c in p.conflicts:
                    lines.append(f"  {p.name}: принято {p.display:g} {p.unit}, "
                                 f"но {from_canonical(c.value, p.unit):g} по источнику "
                                 f"«{c.source}»")
        return "\n".join(lines)
