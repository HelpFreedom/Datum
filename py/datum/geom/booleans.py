"""Булевы операции. Пакетные формы из `mf.py` — они заметно быстрее цепочки.

Страж здесь НЕ вызывается: он работает в контрольных точках (см. guard.py).
Замер показал, что проверка после каждой операции стоит ×4…×9.5, потому что
форсирует ленивый расчёт manifold3d.
"""

from __future__ import annotations

from collections.abc import Iterable

from manifold3d import Manifold, OpType

from datum.geom.solid import Solid, merge_journals

__all__ = ["union", "diff", "intersect", "hull", "compose", "split_by_plane"]


def _live(parts: Iterable[Solid | Manifold | None]) -> list[Manifold]:
    """Входы без None. Пустоту здесь НЕ спрашивают: `is_empty()` форсирует
    ленивую цепочку (замер: 25 мс на 168 вычитаниях, после него расчёт уже
    сделан), и `union([s, x])` в цикле считал `s` на каждом шаге. Объединению и
    вычитанию пустой вход ничего не меняет, а пересечению — обязан: прежде
    пустой вход выбрасывался, и пересечение с пустым выходило не пустым."""
    return [p.manifold if isinstance(p, Solid) else p for p in parts if p is not None]


def _name_of(parts) -> str:
    for p in parts:
        if isinstance(p, Solid) and p.name:
            return p.name
    return ""


def _journals_of(parts) -> tuple:
    """Журнал результата из ВСЕХ входов и записи-родители (`merge_journals`)."""
    return merge_journals(*(p.journal for p in parts if isinstance(p, Solid)))


def union(parts, *, name: str = "") -> Solid:
    """Объединение. None пропускаются, пустые тела ничего не добавляют."""
    parts = list(parts)
    live = _live(parts)
    if not live:
        return Solid.wrap(Manifold(), name=name or _name_of(parts), op="union")
    man = live[0] if len(live) == 1 else Manifold.batch_boolean(live, OpType.Add)
    journal, parents = _journals_of(parts)
    return Solid.wrap(man, name=name or _name_of(parts), op="union",
                      kwargs={"n": len(live)}, journal=journal, parents=parents)


def diff(a: Solid, parts, *, name: str = "") -> Solid:
    """Вычитание списка тел из a одной пакетной операцией."""
    parts = list(parts)
    live = _live(parts)
    if not live:
        return a
    cutter = live[0] if len(live) == 1 else Manifold.batch_boolean(live, OpType.Add)
    journal, parents = _journals_of([a, *parts])
    return Solid.wrap(a.manifold - cutter, name=name or a.name, op="diff",
                      kwargs={"n": len(live)}, inputs=(a.name,) if a.name else (),
                      journal=journal, parents=parents)


def intersect(parts, *, name: str = "") -> Solid:
    """Пересечение. None пропускаются; пустое тело среди входов — пустой ответ."""
    parts = list(parts)
    live = _live(parts)
    if not live:
        return Solid.wrap(Manifold(), name=name, op="intersect")
    man = live[0] if len(live) == 1 else Manifold.batch_boolean(live, OpType.Intersect)
    journal, parents = _journals_of(parts)
    return Solid.wrap(man, name=name or _name_of(parts), op="intersect",
                      kwargs={"n": len(live)}, journal=journal, parents=parents)


def hull(s: Solid) -> Solid:
    """Выпуклая оболочка тела."""
    return s.derive(s.manifold.hull(), "hull")


def compose(parts, *, name: str = "") -> Solid:
    """Сложить несколько тел в одно БЕЗ булевой операции.

    Быстро, но тела не сшиваются: годится для сцены и экспорта сборки, не для
    геометрии детали.
    """
    live = _live(parts)
    return Solid.wrap(Manifold.compose(live), name=name, op="compose",
                      kwargs={"n": len(live)})


def split_by_plane(s: Solid, normal: tuple[float, float, float],
                   offset: float) -> tuple[Solid, Solid]:
    """Разрезать тело плоскостью. Возвращает (над плоскостью, под плоскостью)."""
    a, b = s.manifold.split_by_plane(normal, offset)
    return (s.derive(a, "split_by_plane", side="+", normal=tuple(normal), offset=offset),
            s.derive(b, "split_by_plane", side="-", normal=tuple(normal), offset=offset))
