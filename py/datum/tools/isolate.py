"""Запуск операции в отдельном процессе.

Изолируется ОПЕРАЦИЯ ЦЕЛИКОМ, а не сборка отдельной детали. Так выходит и проще,
и надёжнее: дочерний процесс делает всю работу, сам пишет в кеш и возвращает
готовый словарь — тот самый, которым операции и обмениваются. Геометрию через
границу процессов передавать не приходится вовсе.

Дочерний процесс не пользуется унаследованными соединениями sqlite. В пробе
наследование сработало, но документация sqlite это прямо запрещает: состояние
блокировок и разделяемая память WAL после fork принадлежат обоим процессам
сразу, и при одновременной записи это кончается порчей базы. Поэтому потомок
открывает проект заново — несколько миллисекунд против редкой, но неразбираемой
беды.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable

from datum.build.worker import BuildWorker, WorkerLimits, limits_for, wait_or_cancel
from datum.tools.registry import OpSpec

__all__ = ["run_isolated", "isolated_callable", "DEFAULT_LIMITS"]

DEFAULT_LIMITS = WorkerLimits()


def _job(fn: Callable, kwargs: dict):
    def run() -> tuple[dict, bytes]:
        from datum.tools.ops import clear_project_cache

        clear_project_cache()          # см. примечание о sqlite в заголовке модуля
        result = asyncio.run(fn(**kwargs))
        return ({"status": "ok", "result": result}, b"")

    return run


def run_isolated(fn: Callable, kwargs: dict, *,
                 limits: WorkerLimits | None = None,
                 headroom_mb: int | None = None) -> dict:
    """Выполнить операцию в отдельном процессе и вернуть её словарь."""
    limits = limits_for(kwargs, limits or DEFAULT_LIMITS, headroom_mb=headroom_mb)
    outcome = wait_or_cancel(BuildWorker(_job(fn, kwargs), limits=limits).start())
    if outcome.ok:
        result = outcome.payload.get("result")
        if isinstance(result, dict):
            return result
        return {"error": "операция вернула не словарь",      # pragma: no cover
                "report": "ОШИБКА: операция вернула не словарь"}

    message = outcome.message or f"процесс сборки завершился со статусом {outcome.status}"
    return {"error": message, "report": f"ОШИБКА: {message}",
            "worker_status": outcome.status, "worker_ms": round(outcome.ms, 1)}


def isolated_callable(spec: OpSpec, *, limits: WorkerLimits | None = None) -> Callable:
    """Обёртка операции, сохраняющая её сигнатуру.

    Сигнатура важна: из неё выводятся и схема инструмента MCP, и разбор
    командной строки. `functools.wraps` проставляет `__wrapped__`, и
    `inspect.signature` идёт по нему — поэтому обёртка снаружи неотличима.
    """

    @functools.wraps(spec.fn)
    async def wrapper(**kwargs: Any) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: run_isolated(spec.fn, kwargs, limits=limits,
                                       headroom_mb=spec.memory_headroom_mb))

    return wrapper
