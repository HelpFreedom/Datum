"""Объявление механизма в скрипте проекта — `@mechanism("рука")`, как `@part`.

Функция механизма зовётся при симуляции с параметрами прогона (`P.…`) и
возвращает `datum.sim.Mechanism`. Регистрация глобальная и снимается после
загрузки проекта — так же, как у деталей.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

__all__ = ["MECHANISMS", "MechanismDef", "mechanism", "clear_mechanisms"]


@dataclass(frozen=True)
class MechanismDef:
    name: str
    fn: Callable
    doc: str = ""


MECHANISMS: dict[str, MechanismDef] = {}


def mechanism(name: str) -> Callable[[Callable], Callable]:
    """Объявить механизм. Функция без аргументов возвращает `Mechanism`.

    Что объявлять и когда — справочник навыка `references/simulation.md`."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("у механизма должно быть имя: @mechanism(\"рука\")")

    def wrap(fn: Callable) -> Callable:
        if name in MECHANISMS and MECHANISMS[name].fn is not fn:
            raise ValueError(f"механизм {name!r} объявлен дважды")
        MECHANISMS[name] = MechanismDef(name=name, fn=fn, doc=(fn.__doc__ or "").strip())
        return fn

    return wrap


def clear_mechanisms() -> None:
    MECHANISMS.clear()
