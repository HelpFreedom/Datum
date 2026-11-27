"""Локальный сервер Datum.

Один сервер — один проект: так задумано, и оболочка поднимает его на каждый
открытый проект. Маршрутов немного, потому что всё содержательное делают
операции из реестра — те же самые, что доступны в командной строке и агенту.

Разделение по цене: мелкое ходит по WebSocket событиями, крупное (меши,
картинки) отдаётся обычным запросом по отпечатку содержимого и кешируется
браузером навсегда.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from datum.server.events import EventHub
from datum.server.jobs import JobManager
from datum.server.security import ServerSecurity, actor_of, host_is_allowed
from datum.server.ui import MEDIA as UI_MEDIA
from datum.server.ui import NOT_BUILT, asset_path, index_html, ui_dir
from datum.tools.registry import OPS, ArgumentsError, validate_args

__all__ = ["create_app", "ServerState", "OBJECT_NAME"]

#: Имя объекта — только отпечаток и известное расширение. Никаких путей:
#: так обход каталога невозможен в принципе, а не «проверен».
OBJECT_NAME = re.compile(r"^[0-9a-f]{64}\.(glb|png|gif|json|mesh|stl|3mf|step)$")

_MEDIA = {"glb": "model/gltf-binary", "png": "image/png", "gif": "image/gif",
          "json": "application/json",
          "mesh": "application/octet-stream", "stl": "model/stl",
          "3mf": "model/3mf", "step": "application/step"}


# В Windows проверены эти встроенные операции. Задания со скриптами проекта
# ждут исполнителя с полным набором ограничений ресурсов.
_WINDOWS_OPS = frozenset({"datum_status", "check_list", "fem_verify", "param_set"})


def _supported(spec) -> bool:
    return os.name != "nt" or (not spec.isolate and spec.name in _WINDOWS_OPS)


class ServerState:
    def __init__(self, project: str | Path, *, security: ServerSecurity | None = None,
                 ui: str | Path | None = None):
        self.project = Path(project).resolve()
        self.security = security or ServerSecurity()
        self.hub = EventHub()
        self.jobs = JobManager(self.hub)
        self.ui = ui_dir(ui)
        #: Что сделать при остановке приложения. Нужно тому, что должно
        #: исчезнуть вместе с сервером, — объявлению для агентов: после SIGTERM
        #: uvicorn заново поднимает сигнал, и до `finally` вокруг run дело не
        #: доходит.
        self.on_shutdown: list = []

    @property
    def objects_dir(self) -> Path:
        return self.project / ".datum" / "cache" / "objects"


def create_app(project: str | Path, *, security: ServerSecurity | None = None,
               ui: str | Path | None = None) -> FastAPI:
    state = ServerState(project, security=security, ui=ui)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Наблюдатель за файлами проекта: правка из редактора или от агента
        # видна окну без «Собрать» (см. `server.watch`).
        from datum.server.watch import watch_project

        stop = asyncio.Event()
        watcher = asyncio.create_task(watch_project(state.project, state.hub, stop=stop))
        try:
            yield
        finally:
            stop.set()
            try:
                await asyncio.wait_for(watcher, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
                watcher.cancel()
            except Exception:                      # pragma: no cover
                pass
        # Сперва будим подписчиков: обработчик WebSocket спит на очереди, и
        # пока он спит, uvicorn ждёт закрытия соединения и не останавливается.
        state.hub.close()
        # Затем снимаем незавершённые задания: иначе их процессы переживут
        # сервер и останутся сиротами.
        await state.jobs.shutdown()
        for callback in state.on_shutdown:
            try:
                callback()
            except Exception:                      # pragma: no cover
                pass

    app = FastAPI(title="Datum", version="0.1.0", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.datum = state

    # ------------------------------------------------------------ охрана
    @app.middleware("http")
    async def guard(request: Request, call_next):
        ok, why = state.security.check(request.method, request.url.path,
                                       request.headers)
        if not ok:
            return JSONResponse({"error": why}, status_code=403)
        response = await call_next(request)
        # Ни одного заголовка Access-Control-Allow-*: именно их отсутствие
        # заставляет правило одного источника работать за нас.
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # ------------------------------------------------------------ жизнь
    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        # Живость открыта без токена, а ПУТЬ ПРОЕКТА — нет: его незачем знать
        # тому, кто не читал рукопожатие (другой пользователь машины мог
        # спросить любой порт). Свой клиент (MCP) шлёт токен и сверяет проект.
        from datum.server.security import TOKEN_HEADER

        if state.security.token_ok(request.headers.get(TOKEN_HEADER, "")):
            return {"ok": True, "project": str(state.project), "seq": state.hub.seq}
        return {"ok": True}

    # ------------------------------------------------------------ окно
    # Бандл отдаётся БЕЗ токена: к переходу браузера на страницу заголовок не
    # приложишь. Секретов в нём нет — токен окно получает из preload Electron.
    @app.get("/")
    @app.get("/index.html")
    async def index() -> Response:
        path = index_html(state.ui)
        if path is None:
            return Response(NOT_BUILT.replace("__DIR__", str(state.ui)),
                            media_type="text/html; charset=utf-8",
                            status_code=200)
        return Response(path.read_bytes(), media_type="text/html; charset=utf-8",
                        # Страницу кешировать нельзя: в ней имена файлов бандла,
                        # и после пересборки окно обязано увидеть новые.
                        headers={"Cache-Control": "no-store"})

    @app.get("/assets/{name}")
    async def asset(name: str) -> Response:
        path = asset_path(state.ui, name)
        if path is None:
            return JSONResponse({"error": "нет такого файла окна"}, status_code=404)
        return Response(
            path.read_bytes(),
            media_type=UI_MEDIA.get(path.suffix, "application/octet-stream"),
            # Vite вписывает отпечаток в имя файла, поэтому навсегда.
            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        path = state.ui / "favicon.ico"
        if not path.is_file():
            return Response(status_code=204)
        return Response(path.read_bytes(), media_type="image/vnd.microsoft.icon")

    # ------------------------------------------------------------ операции
    @app.get("/api/ops")
    async def list_ops() -> dict:
        return {"ops": [{"name": s.name, "title": s.title, "group": s.group,
                         "danger": s.danger, "isolate": s.isolate,
                         "description": s.description,
                         "args": [{"name": a.name, "required": a.required,
                                   "help": a.help} for a in s.args]}
                        for s in sorted(OPS.values(), key=lambda x: x.name) if _supported(s)]}

    def checked(spec, args: dict) -> dict | JSONResponse:
        """Аргументы — по сигнатуре операции, проект — только свой.

        Сервер открыт на ОДНОМ проекте, и события его операций окно принимает
        за правду о своём документе. Раньше путь к другому проекту в теле
        запроса собирал ЧУЖОЙ проект и разослал бы окну его геометрию.
        """
        if not _supported(spec):
            return JSONResponse({"error": f"операция {spec.name} пока не поддержана сервером Windows"},
                                status_code=501)
        args = dict(args)
        if "project" in inspect.signature(spec.fn).parameters:
            given = args.get("project")
            if not given:
                args["project"] = str(state.project)
            elif Path(str(given)).resolve() != state.project:
                return JSONResponse(
                    {"error": f"сервер открыт на проекте {state.project}, а запрошен "
                              f"{given}. Другой проект — другой сервер"},
                    status_code=400)
            else:
                args["project"] = str(state.project)
        try:
            return validate_args(spec, args)
        except ArgumentsError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    def not_for(spec, request: Request) -> JSONResponse | None:
        """Операция, которой нет в MCP, — не для агента, и через сервер тоже.

        Так `window_report` — «что видит человек» — пишет только окно. Метка
        исполнителя — заголовок, который ставит сам клиент: это перила против
        ошибки агента, а не граница безопасности (граница — токен и петля).
        """
        if not spec.mcp and actor_of(request.headers) == "agent":
            return JSONResponse({"error": f"операция {spec.name!r} — только для окна"},
                                status_code=403)
        return None

    @app.post("/api/ops/{name}")
    async def run_op(name: str, request: Request) -> Any:
        spec = OPS.get(name)
        if spec is None:
            return JSONResponse({"error": f"нет операции {name!r}"}, status_code=404)
        args = await _json_body(request)
        if isinstance(args, JSONResponse):
            return args
        args = checked(spec, args)
        if isinstance(args, JSONResponse):
            return args
        refused = not_for(spec, request)
        if refused:
            return refused
        job = state.jobs.submit(spec, args, actor=actor_of(request.headers))
        # Ждём дольше, чем живёт воркер: иначе ответ «ещё идёт» приходил бы
        # раньше, чем сборка могла бы кончиться или быть убитой с причиной.
        await _await_job(job, timeout=state.jobs.limits.wall_seconds + 60.0)
        return {"job": job.id, "status": job.status, "actor": job.actor,
                "result": job.result}

    # ------------------------------------------------------------ задания
    @app.post("/api/jobs")
    async def submit_job(request: Request) -> Any:
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        name = body.get("op")
        spec = OPS.get(name or "")
        if spec is None:
            return JSONResponse({"error": f"нет операции {name!r}"}, status_code=404)
        raw = body.get("args") or {}
        if not isinstance(raw, dict):
            return JSONResponse({"error": "args должен быть объектом"}, status_code=400)
        args = checked(spec, raw)
        if isinstance(args, JSONResponse):
            return args
        refused = not_for(spec, request)
        if refused:
            return refused
        job = state.jobs.submit(spec, args, actor=actor_of(request.headers))
        return {"job": job.id, "status": job.status}

    @app.get("/api/jobs")
    async def list_jobs(brief: bool = False) -> dict:
        return {"jobs": state.jobs.list(brief=brief)}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> Any:
        job = state.jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "нет такого задания"}, status_code=404)
        return job.as_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> Any:
        job = state.jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "нет такого задания"}, status_code=404)
        cancelled = state.jobs.cancel(job_id)
        return {"job": job_id, "cancelled": cancelled, "status": job.status}

    # ------------------------------------------------------------ объекты
    @app.get("/obj/{name}")
    async def artifact(name: str) -> Any:
        if not OBJECT_NAME.match(name):
            return JSONResponse(
                {"error": "имя объекта — это отпечаток и известное расширение"},
                status_code=400)
        digest, ext = name.split(".", 1)
        path = state.objects_dir / digest[:2] / name
        if not path.is_file():
            return JSONResponse({"error": "нет такого объекта"}, status_code=404)
        return Response(
            content=path.read_bytes(),
            media_type=_MEDIA.get(ext, "application/octet-stream"),
            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # ------------------------------------------------------------ события
    @app.websocket("/ws")
    async def events(ws: WebSocket) -> None:
        # Средний слой охраны — HTTP: до рукопожатия WebSocket он не доходит,
        # и Host здесь проверяется сам, тем же правилом (перепривязка DNS).
        if not host_is_allowed(ws.headers.get("host")):
            await ws.close(code=4403, reason="заголовок Host не локальный")
            return
        if not state.security.origin_ok(ws.headers.get("origin")):
            await ws.close(code=4403, reason="источник не локальный")
            return
        await ws.accept()

        # Токен приходит первым кадром, а не в строке запроса: строки запроса
        # попадают в журналы и историю, а токен — это ключ от сервера.
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
        except (asyncio.TimeoutError, Exception):
            await ws.close(code=4401, reason="не дождались токена")
            return
        # Первый кадр обязан быть объектом: `[]` или строка давали
        # необработанное исключение вместо закрытия с кодом.
        if not isinstance(first, dict) or not state.security.token_ok(
                str(first.get("token", ""))):
            await ws.close(code=4401, reason="неверный токен")
            return

        try:
            since = int(first.get("since", 0) or 0)
        except (TypeError, ValueError):
            since = 0
        queue = state.hub.subscribe()
        try:
            # `fresh` — первое подключение страницы: прошлое ей не досылается
            # (состояние она читает целиком сама). Досылка всего кольца новой
            # странице заставляла её грузить устаревшую геометрию — сразу после
            # перезагрузки из-за нехватки памяти (аудит 2026-11-21).
            missed, complete = (([], True) if first.get("fresh") is True
                                else state.hub.since(since))
            await ws.send_json({"seq": state.hub.seq, "t": "hello",
                                "project": str(state.project),
                                "complete": complete,
                                "resync": not complete})
            for event in missed:
                await ws.send_json(event.as_dict())

            # Две задачи наперегонки, и вторая обязательна.
            #
            # Отправка спит на очереди событий и о разрыве связи не узнаёт
            # никак. Пока она спит, uvicorn при остановке ЖДЁТ закрытия этого
            # соединения — а ждать он будет вечно. Проверено: сервер пережил
            # SIGTERM и уходил только по запасному таймауту, а при обрыве
            # оболочки не уходил вовсе. Так и накопились осиротевшие серверы.
            #
            # Поэтому рядом работает задача, которая только и делает, что ждёт
            # от сокета весточку о разрыве. Чья первая — та и закрывает.
            async def pump() -> None:
                while True:
                    event = await queue.get()
                    if event is EventHub.CLOSED:
                        return
                    await ws.send_json(event.as_dict())

            async def watch() -> None:
                # Клиент после токена молчит; нас интересует только разрыв.
                while True:
                    message = await ws.receive()
                    if message.get("type") == "websocket.disconnect":
                        return

            tasks = {asyncio.create_task(pump()), asyncio.create_task(watch())}
            done, pending = await asyncio.wait(tasks,
                                               return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                task.result()                      # чтобы отказ не потерялся
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:                          # pragma: no cover
            pass
        finally:
            state.hub.unsubscribe(queue)

    return app


async def _json_body(request: Request):
    if not (await request.body()):
        return {}
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "тело запроса — не JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "тело запроса должно быть объектом"},
                            status_code=400)
    return body


async def _await_job(job, timeout: float = 600.0) -> None:
    if job._task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(job._task), timeout=timeout)
        except asyncio.TimeoutError:               # pragma: no cover
            pass
