"""Короткий расчёт на Python внутри проекта — операцией, а не через Bash.

Зачем. Разбор сессий агента (аудит 2026-11-21): 150…300 вызовов Bash на сессию
— это свой Python: `Project.open(...)` (41 раз за сессию), сечения на
нескольких высотах, зазор между буквами надписи, «сколько граней смотрит вниз».
Каждый такой вызов начинался с поиска окружения и открытия проекта, шёл мимо
перил и пределов воркера, а его код — это выход модели, самая дорогая часть
ответа.

Операция даёт то же самое, но внутри: проект уже открыт, детали берутся из
кеша сборки (ровно те, что в окне и в проверках), расчёт идёт в изолированном
процессе с пределами памяти и времени и с перилами проекта. Новых прав она не
даёт: агент и так исполняет код проекта (скрипты деталей, `checks/custom.py`).

Это инструмент ИССЛЕДОВАНИЯ, а не проверки: то, что должно держаться всегда,
объявляют проверкой в `checks/custom.py` — её прогонит каждый `check_run`.
"""

from __future__ import annotations

import contextlib
import io
import traceback
from typing import Annotated, Any

from datum.tools.ops import Project, _err, _guard, open_project
from datum.tools.registry import Arg, op

__all__ = ["script_run", "MAX_OUTPUT"]

#: Сколько знаков вывода возвращается (хвост длиннее — обрезается с пометкой).
MAX_OUTPUT = 8000
#: Сколько знаков кода принимается: это расчёт на экран, а не программа.
MAX_CODE = 12000


@op("script_run", cli_name="script", group="замеры", isolate=True,
    title="Короткий расчёт на Python в проекте",
    summary=("Выполнить короткий фрагмент Python внутри проекта и вернуть то, что он "
             "напечатал: свой замер, которого нет среди операций (сечения на нескольких "
             "высотах, зазор между элементами, счёт граней). Готово в пространстве имён: "
             "P — параметры (P.WALL), parts(\"имя\") — тело детали из сборки (то же, что "
             "в окне), geom — datum.geom, measure — datum.geom.measure, np — numpy, "
             "project — открытый проект. Печатайте print(). Проект не меняйте: параметры "
             "— param_set, детали — файлами. То, что должно проверяться всегда, — "
             "проверкой в checks/custom.py, а не этим."))
async def script_run(
    project: Project,
    code: Annotated[str, Arg(help="фрагмент Python; результат — то, что напечатано")],
) -> dict[str, Any]:
    def run():
        if not code.strip():
            return _err("пустой фрагмент")
        if len(code) > MAX_CODE:
            return _err(f"фрагмент длиннее {MAX_CODE} знаков ({len(code)}): это расчёт на "
                        f"экран; большое положите в lib/ проекта и зовите оттуда")
        import numpy as np

        import datum.geom as geom
        from datum.geom import measure

        pr = open_project(project)
        proxy = pr.params.proxy()

        def parts(name: str, **motion):
            return pr._solid(name, proxy, **motion)

        space: dict[str, Any] = {"P": proxy, "parts": parts, "geom": geom, "measure": measure,
                                 "np": np, "project": pr, "__name__": "__datum_script__"}
        out = io.StringIO()
        failed = ""
        try:
            compiled = compile(code, "<script_run>", "exec")
            with pr._context(), contextlib.redirect_stdout(out):
                exec(compiled, space)                      # noqa: S102 — в этом и суть
        except BaseException as e:                         # noqa: BLE001 — отчёт, не падение
            if isinstance(e, (KeyboardInterrupt, SystemExit, MemoryError)):
                raise
            # Трассировка — только по строкам самого фрагмента: внутренности
            # Datum агенту не помогут, а место в его коде — помогут.
            frames = [f for f in traceback.extract_tb(e.__traceback__)
                      if f.filename == "<script_run>"]
            where = f", строка {frames[-1].lineno}" if frames else ""
            if isinstance(e, SyntaxError) and e.lineno:
                where = f", строка {e.lineno}"
            from datum.build.hints import with_hint

            failed = with_hint(f"{type(e).__name__}{where}: {e}", e)
        text = out.getvalue()
        cut = ""
        if len(text) > MAX_OUTPUT:
            cut = (f"\n… вывод обрезан: показано {MAX_OUTPUT} знаков из {len(text)} — "
                   f"печатайте итог, а не весь массив")
            text = text[:MAX_OUTPUT]
        reads = sorted(proxy.reads)
        if failed:
            return {"error": failed, "output": text + cut,
                    "report": (text + cut + "\n" if text else "") + f"ОШИБКА: {failed}"}
        return {"output": text + cut, "params_read": reads,
                "report": (text + cut) if text.strip() else
                "(фрагмент ничего не напечатал — выводите результат через print)"}

    return _guard(run)
