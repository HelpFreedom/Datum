"""Операция, выполняемая РАБОТАЮЩИМ сервером, а не своим процессом.

Зачем. У Datum одна дверь для агента — MCP по stdio. Но если эта дверь считает
всё у себя, то правка, сделанная агентом, до открытого окна не доходит: окно
узнаёт о геометрии по событиям сервера, а событий не было. Требование «всё, что
делает ИИ, видно в реальном времени» тогда не выполняется.

Поэтому, когда сервер поднят, MCP становится ТОНКИМ КЛИЕНТОМ: он пересылает
вызов на `/api/ops/<имя>` и возвращает ответ как есть. Дальше работает обычная
дорога — задание, отдельный процесс, события в окно. Человек и агент правят
один и тот же живой документ.

Если сервера нет (агент запущен в обычном терминале без окна), дверь работает
по-старому, в своём процессе. Набор инструментов при этом одинаков до буквы:
схемы выводятся из тех же сигнатур, меняется только исполнитель.

Токен НЕ передаётся через окружение: в /proc/<pid>/environ его видно любому
процессу пользователя. В окружении лежит лишь путь к файлу рукопожатия с
правами 0600.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from datum.process import process_alive
from datum.tools.registry import OpSpec

__all__ = ["Endpoint", "endpoint_from_env", "remote_callable", "routed_callable",
           "endpoint_alive", "find_endpoint", "HANDSHAKE_ENV"]

#: Переменная окружения с путём к файлу рукопожатия.
HANDSHAKE_ENV = "DATUM_HANDSHAKE"

#: Сборка может идти минутами; обрывать её по таймауту клиента бессмысленно —
#: отменой ведает сервер. Больше предела воркера (`WorkerLimits.wall_seconds`,
#: 1200 с) с запасом на очередь: ответ обязан прийти от сервера, с причиной.
TIMEOUT_S = 1800.0


#: Аргументы операций, которые суть пути к файлам (у каждой — свой смысл, но
#: всегда путь): тест сверяет список с реестром.
_PATH_ARGS = ("path", "dest", "into", "out")


class Endpoint:
    __slots__ = ("port", "token", "project", "pid")

    def __init__(self, port: int, token: str, project: str = "", pid: int = 0) -> None:
        self.port = port
        self.token = token
        self.project = project
        self.pid = pid

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def endpoint_from_env() -> Endpoint | None:
    """Рукопожатие из окружения. Нет файла — значит сервера нет, и это не беда."""
    path = os.environ.get(HANDSHAKE_ENV)
    if not path:
        return None
    try:
        from datum.server.handshake import read_handshake

        data = read_handshake(path, remove=False)
        return Endpoint(data.port, data.token, data.project, data.pid)
    except (OSError, ValueError, KeyError):
        return None


def _post(endpoint: Endpoint, name: str, payload: dict) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{endpoint.base}/api/ops/{name}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Datum-Token": endpoint.token,
            "X-Datum-Client": "1",
            # Чтобы в ленте было видно, что это сделал агент, а не человек.
            "X-Datum-Actor": "agent",
        },
    )
    # Прокси здесь недопустим: адрес петлевой, а в окружении прокси может быть
    # выставлен для самого агента. ProxyHandler({}) отключает его явно.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=TIMEOUT_S) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        return {"error": f"сервер отказал ({e.code}): {detail}",
                "report": f"ОШИБКА: сервер отказал ({e.code})"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"error": f"сервер Datum недоступен: {e}",
                "report": "ОШИБКА: сервер Datum недоступен"}
    except Exception as e:                         # pragma: no cover
        # Обещание этой функции — вернуть словарь, а не бросить. Один из
        # способов её уронить: не-ASCII в токене (заголовки HTTP — latin-1).
        # Настоящий токен всегда ASCII, но обещание не должно зависеть от
        # чужой дисциплины.
        return {"error": f"запрос к серверу не удался: {type(e).__name__}: {e}",
                "report": "ОШИБКА: запрос к серверу не удался"}

    result = body.get("result")
    if not isinstance(result, dict):               # pragma: no cover
        return {"error": "сервер вернул не словарь",
                "report": "ОШИБКА: сервер вернул не словарь"}
    return result


def endpoint_alive(endpoint: Endpoint) -> bool:
    """Сервер за этой точкой жив и открыт на том самом проекте.

    Рукопожатие могло протухнуть: окно закрыли, сервер умер, а переменная
    окружения у агента осталась. Раньше после этого мёртвыми становились ВСЕ
    инструменты, включая те, которым сервер вовсе не нужен.
    """
    if endpoint.pid and not process_alive(endpoint.pid):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        probe = urllib.request.Request(f"{endpoint.base}/healthz",
                                       headers={"X-Datum-Token": endpoint.token})
        with opener.open(probe, timeout=1.0) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    if endpoint.project and os.path.realpath(str(data.get("project", ""))) \
            != os.path.realpath(endpoint.project):
        return False
    return bool(data.get("ok"))


def find_endpoint(project: str) -> Endpoint | None:
    """Живой сервер, открытый на этом проекте, — по его объявлению."""
    from datum.server.handshake import announced

    for h in announced(project):
        candidate = Endpoint(h.port, h.token, h.project, h.pid)
        if endpoint_alive(candidate):
            return candidate
    return None


def routed_callable(spec: OpSpec, pinned: Endpoint | None = None) -> Callable:
    """Операция для MCP: на сервер, если он жив и открыт на этом проекте, иначе — у себя.

    Решение принимается НА КАЖДЫЙ ВЫЗОВ, а не при старте:
      * агент из окна получает `pinned` — сервер окна; пока он жив, всё идёт туда;
      * агент в обычном терминале находит сервер по объявлению (см.
        `server.handshake.announce`) — и его правки видны в открытом окне;
      * сервера нет или он умер — считаем у себя, как раньше, а не отвечаем
        «сервер недоступен» на всё подряд.
    Операции без проекта (состояние, каталог, самопроверка решателя, диагностика
    отдельного файла) всегда считаются у себя: окну они не интересны.
    """
    from datum.tools.isolate import isolated_callable

    local = isolated_callable(spec) if spec.isolate else spec.fn
    try:
        takes_project = "project" in inspect.signature(spec.fn).parameters
    except (TypeError, ValueError):                # pragma: no cover
        takes_project = False
    remote = remote_callable(spec, pinned) if pinned is not None else None
    spec_args = {a.name for a in spec.args}

    from datum.tools.agent_reply import fit_for_agent, for_agent

    @functools.wraps(spec.fn)
    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        # Отказ в аргументах (лишнее имя, не тот тип) приходит сюда готовым
        # текстом — см. `mcp_adapter._strict`: операция не вызывается вовсе.
        bad = kwargs.get("__datum_arg_error__")
        if bad:
            from mcp.server.mcpserver.exceptions import ToolError

            raise ToolError(bad)
        # Ответ — агенту: не влезающий в его предел сокращается служебным
        # (`agent_reply`); окно и CLI получают ответ целиком, другим путём.
        return fit_for_agent(for_agent(spec.name, await call(**kwargs), kwargs))

    async def call(**kwargs: Any) -> dict[str, Any]:
        # Относительный путь к файлу агент пишет от СВОЕГО каталога (каталога
        # проекта), а сервер окна запущен из каталога Datum: `import_model(
        # path="scan.stl")` при открытом окне отвечал «нет такого файла», а
        # `project_save(dest="../копия")` клал архив рядом с репозиторием
        # (аудит 2026-11-21). Разрешаем здесь, на стороне агента.
        for key in _PATH_ARGS:
            value = kwargs.get(key)
            if isinstance(value, str) and value and key in spec_args:
                kwargs = {**kwargs, key: os.path.abspath(os.path.expanduser(value))}
        if not takes_project:
            return await local(**kwargs)
        project = kwargs.get("project") or (pinned.project if pinned else "")
        if project:
            kwargs = {**kwargs, "project": os.path.abspath(str(project))}
        target = None
        if pinned is not None and (not project or os.path.realpath(kwargs["project"])
                                   == os.path.realpath(pinned.project or kwargs["project"])):
            if await asyncio.to_thread(endpoint_alive, pinned):
                target = pinned
        if target is None and project:
            target = await asyncio.to_thread(find_endpoint, kwargs["project"])
        if target is not None:
            payload = {k: v for k, v in kwargs.items() if v is not None}
            return await asyncio.to_thread(_post, target, spec.name, payload)
        if not project:
            return {"error": "не указан проект", "report": "ОШИБКА: не указан проект"}
        return await local(**kwargs)

    if remote is not None and getattr(remote, "__signature__", None) is not None:
        wrapper.__signature__ = remote.__signature__   # type: ignore[attr-defined]
    return wrapper


def remote_callable(spec: OpSpec, endpoint: Endpoint) -> Callable:
    """Обёртка операции, пересылающая вызов на сервер.

    `functools.wraps` обязателен: из `__wrapped__` берётся сигнатура, а из неё —
    схема инструмента MCP. Без этого агент увидел бы инструмент без аргументов.
    """

    @functools.wraps(spec.fn)
    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        if not kwargs.get("project") and endpoint.project:
            kwargs = {**kwargs, "project": endpoint.project}
        elif kwargs.get("project"):
            # Относительный путь агент пишет от СВОЕГО каталога (это каталог
            # проекта), а сервер разрешал бы его от своего. Разрешаем здесь.
            kwargs = {**kwargs, "project": os.path.abspath(str(kwargs["project"]))}
        payload = {k: v for k, v in kwargs.items() if v is not None}
        return await asyncio.to_thread(_post, endpoint, spec.name, payload)

    # Проект перестаёт быть обязательным: сервер открыт ровно на ОДНОМ, и
    # заставлять агента повторять путь — лишний повод ошибиться. Умолчания в
    # обёртке мало: слой MCP проверяет аргументы по СХЕМЕ, а схема выводится из
    # сигнатуры, и до тела вызов бы не дошёл. Поэтому подменяется сигнатура.
    #
    # `inspect.signature` прекращает разворачивать цепочку `__wrapped__` на
    # объекте с собственным `__signature__`, поэтому подмена видна снаружи.
    if endpoint.project:
        # eval_str обязателен: в модуле операций стоит `from __future__ import
        # annotations`, и без него аннотации приезжают СТРОКАМИ. Схему по ним
        # не построить — имя `Project` в модуле обёртки неизвестно, и pydantic
        # падает на «is not fully defined».
        try:
            signature = inspect.signature(spec.fn, eval_str=True)
        except Exception:                          # pragma: no cover
            signature = None
        if signature is not None and "project" in signature.parameters:
            # Все параметры становятся ТОЛЬКО ИМЕНОВАННЫМИ. Иначе умолчание у
            # первого аргумента — «non-default argument follows default
            # argument», потому что за `project` идут обязательные. MCP и так
            # передаёт всё по именам, а порядок в схеме сохраняется.
            parameters = [
                param.replace(
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=endpoint.project if name == "project" else param.default,
                )
                for name, param in signature.parameters.items()
            ]
            wrapper.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]

    return wrapper
