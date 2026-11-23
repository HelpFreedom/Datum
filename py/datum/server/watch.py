"""Наблюдение за файлами проекта.

Проект правят не только через окно: агент пишет `parts/*.py` своими `Edit`,
человек открывает `params/base.yaml` в редакторе. До этого наблюдателя окно
узнавало о таких правках, только когда человек сам жал «Собрать», — а обещано
было, что правка видна сразу, кто бы её ни сделал.

Сервер следит за тем, что ОПРЕДЕЛЯЕТ документ, и рассылает `doc.changed`. Что
с этим делать, решает окно: перечитать сводку, пересобрать, перепрогнать.
Сам сервер ничего не пересобирает — иначе правка из окна и правка из
редактора шли бы разными дорогами.

Не смотрим на то, что сборка пишет сама (`.datum/`, `output/`, `snapshots/`):
иначе каждая пересборка будила бы следующую.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from datum.server.events import EventHub

__all__ = ["relevant", "watch_project"]

#: Каталоги, чьи файлы описывают документ, — и какие файлы в них важны.
_WATCHED_DIRS = {"params": (".yaml", ".yml"), "parts": (".py",), "lib": (".py",),
                 "checks": (".py", ".yaml", ".yml"),
                 # Шрифты проекта: надписи деталей — их контуры (`geom/text.py`).
                 "fonts": (".ttf", ".otf", ".TTF", ".OTF")}
_WATCHED_FILES = {"datum.toml", "assembly.yaml", "placement.yaml"}


def relevant(root: Path, path: str | Path) -> bool:
    """Меняет ли правка этого файла документ проекта."""
    try:
        rel = Path(path).resolve().relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    if not parts or any(p.startswith(".") or p == "__pycache__" for p in parts):
        return False                             # .datum/, редакторские .swp и кеши
    if len(parts) == 1:
        return rel.name in _WATCHED_FILES
    top = parts[0]
    if top in _WATCHED_DIRS:
        return rel.suffix in _WATCHED_DIRS[top]
    # Чужая модель сменилась, когда переписана её опись: импорт кладёт её
    # последней (см. `import_model`).
    return top == "imported" and rel.name == "meta.json"


async def watch_project(root: Path, hub: EventHub, *, debounce_ms: int = 300,
                        stop: asyncio.Event | None = None) -> None:
    """Слать `doc.changed` на каждую пачку правок. Работает до `stop`."""
    from watchfiles import awatch

    root = Path(root).resolve()
    async for changes in awatch(root, watch_filter=lambda _c, p: relevant(root, p),
                                debounce=debounce_ms, stop_event=stop,
                                recursive=True):
        paths = sorted({str(Path(p).resolve().relative_to(root)) for _c, p in changes})
        if paths:
            hub.publish("doc.changed", paths=paths)
