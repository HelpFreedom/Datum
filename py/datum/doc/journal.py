"""Журнал решений проекта — `JOURNAL.md` в корне.

Зачем. Разбор сессий агента (аудит 2026-11-21): новая сессия ничего не знает о
прежних решениях и восстанавливает их, перечитывая старые журналы разговоров
(46 таких походов в одной сессии) и `NOTES.md` на сотни строк. Поэтому сессии
не начинали заново и вели до упора окна — а цена хода растёт с контекстом.

Журнал — короткая память проекта: одна строка на решение, отказ от варианта,
замер, итог печати. Он лежит В ПРОЕКТЕ (а не в `.datum/`): уходит в архив,
виден в git и правится человеком. `NOTES.md` остаётся для длинного
рассуждения; сюда — вывод одной строкой и, если нужно, ссылка на место в
заметках.

Формат строки:  `- 2026-11-22 14:05 [решение] текст`
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FILE", "KINDS", "Entry", "add", "read", "JournalError"]

FILE = "JOURNAL.md"
KINDS = ("решение", "отказ", "замер", "печать", "вопрос", "этап")
MAX_TEXT = 600

_HEAD = ("# Журнал решений\n\n"
         "Одна строка на решение, отказ от варианта, замер или итог печати — чтобы новая\n"
         "сессия знала, на чём остановились. Длинное рассуждение — в NOTES.md.\n\n")
_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))? \[([^\]]+)\] (.+)$")


class JournalError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Entry:
    date: str
    time: str
    kind: str
    text: str

    def line(self) -> str:
        when = f"{self.date} {self.time}".strip()
        return f"- {when} [{self.kind}] {self.text}"


def read(root: str | Path) -> list[Entry]:
    """Записи журнала по порядку. Нет файла — пусто; чужие строки пропускаются."""
    try:
        text = (Path(root) / FILE).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for raw in text.splitlines():
        m = _LINE.match(raw.rstrip())
        if m:
            out.append(Entry(m.group(1), m.group(2) or "", m.group(3), m.group(4)))
    return out


def add(root: str | Path, kind: str, text: str, *, now: dt.datetime | None = None) -> Entry:
    """Дописать запись. Текст — одной строкой: переводы строк сворачиваются."""
    if kind not in KINDS:
        raise JournalError(f"род записи {kind!r}: один из {', '.join(KINDS)}")
    flat = " ".join(str(text).split())
    if not flat:
        raise JournalError("пустая запись")
    if len(flat) > MAX_TEXT:
        raise JournalError(f"запись длиннее {MAX_TEXT} знаков ({len(flat)}): в журнал — "
                           f"вывод одной-двумя фразами, подробности — в NOTES.md")
    moment = now or dt.datetime.now()
    entry = Entry(moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M"), kind, flat)
    path = Path(root) / FILE
    try:
        before = path.read_text(encoding="utf-8")
    except OSError:
        before = _HEAD
    if before and not before.endswith("\n"):
        before += "\n"
    from datum.doc.params_edit import write_atomically

    write_atomically(path, before + entry.line() + "\n")
    return entry
