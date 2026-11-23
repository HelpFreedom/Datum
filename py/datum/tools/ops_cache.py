"""Кеш сборок: состояние и чистка.

Часть операций Datum (`tools/ops.py` — их общий вход: оттуда строятся
MCP-инструменты и команды CLI). Соглашение то же: операция возвращает
словарь, `report` — текст для человека, об ошибке — ключ `error`.
"""

from __future__ import annotations

from typing import Annotated, Any

from datum.tools.ops_base import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op


# ------------------------------------------------------------------ кеш
@op("cache_stats", cli_name="cache", group="система",
    isolate=True,
    title="Состояние кеша сборок",
    summary=("Сколько места занимает кеш проекта и сколько в нём записей. "
             "Кеш лежит в .datum/cache и в git не попадает."))
async def cache_stats(project: Project) -> dict[str, Any]:
    def run():
        pr = open_project(project)
        st = pr.cache.stats()
        return {**st,
                "report": (f"Кеш проекта {pr.name}: объектов {st['objects']}, "
                           f"сборок {st['builds']}, занято {st['mb']} МБ "
                           f"из бюджета {st['budget_mb']} МБ")}

    return _guard(run)


@op("cache_gc", cli_name="cache-gc", group="система", danger="destructive",
    isolate=True,
    title="Ужать кеш",
    summary=("Выбросить из кеша самые давно не использованные объекты, пока он не "
             "уложится в бюджет. Геометрия пересоберётся при следующем обращении, "
             "так что потери данных нет — только времени."))
async def cache_gc(
    project: Project,
    budget_mb: Annotated[int, Arg(help="во что уложиться, МБ; 0 — взять из datum.toml")] = 0,
) -> dict[str, Any]:
    def run():
        # −1 проходил как бюджет «меньше нуля» и выбрасывал ВЕСЬ кеш, тогда как
        # 0 значит «как в datum.toml». Опасная операция не угадывает знак.
        if budget_mb < 0:
            return _err(f"budget_mb {budget_mb} — бюджет не бывает отрицательным; "
                        f"0 — взять из datum.toml")
        pr = open_project(project)
        res = pr.cache.gc(budget_mb or None)
        return {**res,
                "report": (f"Выброшено объектов: {res['removed']}, "
                           f"освобождено {res['freed_mb']} МБ. "
                           f"Осталось {res['mb']} МБ в {res['objects']} объектах")}

    return _guard(run)
