"""Solid — тело в Datum.

Неизменяемая обёртка над manifold3d.Manifold с именем и журналом операций.

ГЛАВНОЕ ПРАВИЛО ЭТОГО МОДУЛЯ: **не трогать свойства Manifold**. manifold3d
ленив — построение выражения на 168 операций стоит 1.9 мс, а первый же запрос
`volume()`/`status()`/`num_tri()` форсирует расчёт всей цепочки. Замер показал,
что форсирование после каждой булевой обходится в ×4.1 / ×6.3 / ×9.5 на
20 / 60 / 168 операций. Поэтому ни конструктор, ни операторы, ни запись в журнал
не запрашивают у Manifold ничего. Расчёт происходит только в контрольных точках
(см. datum.geom.guard).
"""

from __future__ import annotations

import hashlib
import weakref
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from manifold3d import Manifold

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np
    import trimesh

__all__ = ["Solid", "OpRecord", "Journal", "merge_journals"]


@dataclass(frozen=True, slots=True, weakref_slot=True)
class OpRecord:
    """Одна операция в истории тела.

    Хранит ЛЕНИВЫЙ результат: форсирование происходит только при разборе отказа.
    `parents` — записи, давшие входные тела (у булевой двух тел — обе ветви).
    По ним `guard.locate_failure` идёт от результата к виновнику: журнал,
    сложенный из двух ветвей, — уже не цепочка, и бисекция по одному порядку
    записей прошла бы мимо отказа в короткой ветви.

    Ссылки на родителей СЛАБЫЕ: сильные держали бы всю историю тела, и предел
    `MAX_JOURNAL` перестал бы ограничивать память — цикл на сто тысяч операций
    не отпустил бы ни одной записи. Родитель, выпавший из всех журналов, для
    поиска — начало истории, как и прежде у обрезанного журнала.
    """

    index: int
    op: str
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()
    inputs: tuple[str, ...] = ()          # имена/метки входных тел
    result: Manifold | None = field(default=None, repr=False, compare=False)
    parent_refs: tuple["weakref.ref[OpRecord]", ...] = field(default=(), repr=False,
                                                             compare=False)

    @property
    def parents(self) -> tuple["OpRecord", ...]:
        """Живые записи-родители (выпавшие из всех журналов — пропущены)."""
        return tuple(r for r in (ref() for ref in self.parent_refs) if r is not None)

    def describe(self) -> str:
        parts = [repr(a) for a in self.args]
        parts += [f"{k}={v!r}" for k, v in self.kwargs]
        return f"{self.op}({', '.join(parts)})"


#: История операций тела. Кортеж — чтобы Solid оставался неизменяемым.
Journal = tuple[OpRecord, ...]

#: Дальше этой длины журнал обрезается спереди: хвост важнее начала при разборе
#: отказа, а держать миллион ленивых узлов в памяти незачем.
MAX_JOURNAL = 4096


def merge_journals(*journals: Journal) -> tuple[Journal, tuple[OpRecord, ...]]:
    """Журнал тела из нескольких входов и записи-родители новой операции.

    Записи общих предков (одна и та же запись в обеих ветвях) не повторяются:
    `b = move(a)`, затем `a - b` — история `a` в журнале одна. Порядок
    топологический: каждая запись стоит после своих родителей.
    """
    journals = tuple(j for j in journals if j)
    if not journals:
        return (), ()
    parents = tuple(j[-1] for j in journals)
    if len(journals) == 1:
        return journals[0], parents
    merged = list(journals[0])
    seen = {id(r) for r in merged}
    for j in journals[1:]:
        for r in j:
            if id(r) not in seen:
                seen.add(id(r))
                merged.append(r)
    return tuple(merged), parents


@dataclass(frozen=True, slots=True)
class Solid:
    """Тело. Неизменяемое: любая операция возвращает новое."""

    manifold: Manifold
    name: str = ""
    journal: Journal = ()

    # ------------------------------------------------------------------ создание
    @staticmethod
    def wrap(man: Manifold, *, name: str = "", op: str = "wrap",
             args: tuple = (), kwargs: dict | None = None,
             inputs: tuple[str, ...] = (), journal: Journal = (),
             parents: tuple[OpRecord, ...] | None = None) -> "Solid":
        """Обернуть Manifold, дописав запись в журнал. Ничего не форсирует.

        parents — входы операции; по умолчанию последняя запись `journal`
        (операция над одним телом)."""
        if parents is None:
            parents = (journal[-1],) if journal else ()
        rec = OpRecord(
            index=len(journal),
            op=op,
            args=args,
            kwargs=tuple(sorted((kwargs or {}).items())),
            inputs=inputs,
            result=man,
            parent_refs=tuple(weakref.ref(p) for p in parents),
        )
        new_journal = (*journal, rec)
        if len(new_journal) > MAX_JOURNAL:
            new_journal = new_journal[-MAX_JOURNAL:]
        return Solid(manifold=man, name=name, journal=new_journal)

    def renamed(self, name: str) -> "Solid":
        return replace(self, name=name)

    def derive(self, man: Manifold, op: str, **kwargs) -> "Solid":
        """Новое тело из этого — с дописанной записью в журнал. Ничего не форсирует."""
        return Solid.wrap(man, name=self.name, op=op, kwargs=kwargs,
                          inputs=(self.name,) if self.name else (), journal=self.journal)

    # ------------------------------------------------------------------ булевы
    def _combine(self, other: "Solid | Manifold", op: str, man: Manifold) -> "Solid":
        other_name = other.name if isinstance(other, Solid) else ""
        other_journal = other.journal if isinstance(other, Solid) else ()
        # Журнал — из обеих ветвей: отказ ядра в любой из них виден при разборе.
        merged, parents = merge_journals(self.journal, other_journal)
        return Solid.wrap(man, name=self.name, op=op,
                          inputs=(self.name or "a", other_name or "b"),
                          journal=merged, parents=parents)

    @staticmethod
    def _man(x: "Solid | Manifold") -> Manifold:
        return x.manifold if isinstance(x, Solid) else x

    def __add__(self, other: "Solid | Manifold") -> "Solid":
        return self._combine(other, "union", self.manifold + self._man(other))

    def __sub__(self, other: "Solid | Manifold") -> "Solid":
        return self._combine(other, "difference", self.manifold - self._man(other))

    def __xor__(self, other: "Solid | Manifold") -> "Solid":
        return self._combine(other, "intersection", self.manifold ^ self._man(other))

    # ------------------------------------------------------------------ прочее
    def __repr__(self) -> str:          # без запроса свойств — иначе форсируем расчёт
        tag = f" {self.name!r}" if self.name else ""
        return f"<Solid{tag} ops={len(self.journal)}>"

    # ------------------------------------------- всё ниже ФОРСИРУЕТ расчёт
    def digest(self) -> str:
        """Отпечаток ГЕОМЕТРИИ. Форсирует расчёт — звать в контрольных точках.

        Наивный вариант (хешировать буферы как есть) оказался негодным: после
        круга через сериализацию `Manifold(Mesh(...))` перестраивает список
        граней — вершины совпадают побайтово, а из 132 элементов граней
        различаются 122. Отпечаток тогда описывает не форму, а внутренний
        порядок, и деталь, поднятая из кеша, выглядит изменившейся.

        Поэтому приводим к каноническому виду:
          1. вершины сортируем лексикографически и перенумеровываем грани;
          2. внутри грани делаем циклический сдвиг так, чтобы наименьший индекс
             стоял первым — намотка при этом сохраняется, а произвол выбора
             начальной вершины исчезает;
          3. сам список граней сортируем.

        Стоит O(n log n) и считается редко: при снимке версии и при записи в кеш.
        """
        import numpy as np

        from datum.geom.convert import vertices_faces

        verts, faces = vertices_faces(self.manifold)
        v32 = np.asarray(verts, dtype=np.float32)

        h = hashlib.sha256()
        if len(v32) == 0 or len(faces) == 0:
            h.update(b"empty")
            return h.hexdigest()

        order = np.lexsort((v32[:, 2], v32[:, 1], v32[:, 0]))
        rank = np.empty(len(v32), dtype=np.int64)
        rank[order] = np.arange(len(v32), dtype=np.int64)
        f = rank[np.asarray(faces, dtype=np.int64)]

        # Циклический сдвиг: наименьший индекс — первым, порядок обхода цел.
        start = f.argmin(axis=1)
        rolled = np.concatenate([f, f[:, :2]], axis=1)
        idx = start[:, None] + np.arange(3)[None, :]
        f = np.take_along_axis(rolled, idx, axis=1)
        f = f[np.lexsort((f[:, 2], f[:, 1], f[:, 0]))]

        h.update(np.ascontiguousarray(v32[order]).tobytes())
        h.update(np.ascontiguousarray(f, dtype=np.uint32).tobytes())
        return h.hexdigest()

    def to_trimesh(self) -> "trimesh.Trimesh":
        from datum.geom.convert import to_trimesh

        return to_trimesh(self.manifold)
