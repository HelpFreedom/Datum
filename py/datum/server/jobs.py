"""Задания: выполнение операций с отменой и отчётом о ходе.

Операция уходит в отдельный процесс (см. datum.build.worker), поэтому отмена
здесь настоящая: процесс убивается, а не уговаривается. Это важно именно для
геометрии — длинная булева операция идёт в C++ и кооперативным флажком не
прерывается, что измерено и записано в заголовке модуля воркера.

События задания идут в шину, так что интерфейс видит ход работы, не опрашивая
сервер.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field

from datum.build.worker import BuildWorker, WorkerLimits, limits_for
from datum.server.events import EventHub
from datum.tools.registry import OpSpec

__all__ = ["Job", "JobManager"]

#: Виды событий, которые операция вправе прислать в своём результате.
#: Всё служебное (`hello`, `job.*`, `error`) рассылает только сам сервер.
PUBLISHABLE_EVENTS = frozenset({"geom.updated", "artifact", "log", "window.show",
                                "sim.result", "build.progress"})
#: Как часто сервер смотрит, не сообщила ли идущая операция о чём-то (`worker.emit`).
EMIT_POLL_S = 0.2


#: Вызовы, которыми окно обслуживает само себя: отчёт о виде после каждой
#: остановки камеры, крышки среза на каждую деталь, чтение состояния при
#: обновлении. В ленте они вытесняли действия агента — то, ради чего лента и
#: есть, — поэтому помечаются и уходят из памяти первыми. Те же операции,
#: вызванные АГЕНТОМ, служебными не считаются: его шаги видны все.
SERVICE_OPS = frozenset({
    "window_report", "window_state", "section", "section_scene", "measure_probe",
    "project_info",
    "params_list", "imported_list", "imported_views", "snapshot_list", "sim_list",
    "cache_stats", "fonts_list", "check_list", "trash_list"})


@dataclass
class Job:
    id: str
    op: str
    kwargs: dict
    #: Кто затеял: человек в окне, агент через MCP или кто-то снаружи.
    actor: str = "external"
    status: str = "pending"          # pending | running | done | failed | cancelled
    result: dict | None = None
    started: float = 0.0
    finished: float = 0.0
    _worker: BuildWorker | None = field(default=None, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)
    #: События, уже разосланные по ходу работы (`worker.emit`), — чтобы не повторять
    #: их, когда они же придут в результате.
    _emitted: set = field(default_factory=set, repr=False)

    @property
    def ms(self) -> float:
        end = self.finished or time.time()
        return (end - self.started) * 1000 if self.started else 0.0

    @property
    def service(self) -> bool:
        return self.actor == "user" and self.op in SERVICE_OPS

    def as_dict(self, brief: bool = False) -> dict:
        d = {"id": self.id, "op": self.op, "actor": self.actor, "service": self.service,
             "status": self.status, "ms": round(self.ms, 1),
             "started": round(self.started, 3), "finished": round(self.finished, 3)}
        if brief:
            # Ленте окна нужен ход дел, а не результаты: у check_run это
            # десяток килобайт, а заданий в памяти сотня.
            d["error"] = (self.result or {}).get("error") if self.status == "failed" else None
            return d
        d["result"] = self.result if self.status in ("done", "failed") else None
        return d


class JobManager:
    """Пул заданий. Хранит недавние, чтобы интерфейс мог их перечитать."""

    def __init__(self, hub: EventHub, *, limits: WorkerLimits | None = None,
                 keep: int = 100, max_workers: int | None = None):
        import os

        self.hub = hub
        self.limits = limits or WorkerLimits()
        self.keep = keep
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        # Потолок одновременных процессов. Каждый воркер получает предел памяти
        # в несколько гигабайт виртуального пространства, и сам по себе он
        # безопасен; но их число никто не ограничивал, и агент, насыпавший
        # десяток заданий подряд, упирал в память всю машину. Лишние ждут в
        # очереди в статусе pending и отменяются оттуда же.
        self.max_workers = max_workers or max(2, min(8, os.cpu_count() or 2))
        self._slots: asyncio.Semaphore | None = None

    # ------------------------------------------------------------ доступ
    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, brief: bool = False) -> list[dict]:
        return [self._jobs[i].as_dict(brief) for i in self._order]

    def _remember(self, job: Job) -> None:
        self._jobs[job.id] = job
        self._order.append(job.id)
        # Вытесняются только ЗАКОНЧЕННЫЕ задания, самые старые: идущее,
        # выпав из сотни, становилось неотменяемым, и остановка сервера его
        # не видела (окно шлёт по заданию на каждое движение — сотня
        # набирается за минуту).
        while len(self._order) > self.keep:
            done = [i for i in self._order
                    if self._jobs[i].status not in ("pending", "running")]
            # Сначала уходит служебное окна, потом — самое старое из остального.
            victim = next((i for i in done if self._jobs[i].service), None) \
                or (done[0] if done else None)
            if victim is None:
                break
            self._order.remove(victim)
            self._jobs.pop(victim, None)

    # ------------------------------------------------------------ запуск
    def submit(self, spec: OpSpec, kwargs: dict, *, actor: str = "external") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], op=spec.name, kwargs=dict(kwargs),
                  actor=actor)
        self._remember(job)
        self.hub.publish("job.submitted", job=job.id, op=spec.name,
                         actor=actor, danger=spec.danger)
        job._task = asyncio.create_task(self._run(job, spec))
        return job

    async def _run(self, job: Job, spec: OpSpec) -> None:
        if not spec.isolate:
            await self._execute(job, spec)
            return
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.max_workers)
        async with self._slots:
            if job.status != "pending":           # отменено, пока ждало очереди
                return
            await self._execute(job, spec)

    async def _execute(self, job: Job, spec: OpSpec) -> None:
        job.status = "running"
        job.started = time.time()
        self.hub.publish("job.started", job=job.id, op=spec.name, actor=job.actor)

        loop = asyncio.get_running_loop()
        try:
            if spec.isolate:
                # Файл событий «сейчас»: операция дописывает в него строки по
                # ходу работы (`worker.emit`), сервер рассылает их, не дожидаясь
                # результата. Во временном каталоге: туда перила пускают запись.
                fd, emit_path = tempfile.mkstemp(prefix="datum-emit-", suffix=".jsonl")
                os.close(fd)
                worker = BuildWorker(
                    _job_body(spec, job.kwargs, emit_path),
                    limits=limits_for(job.kwargs, self.limits,
                                      headroom_mb=spec.memory_headroom_mb))
                job._worker = worker
                worker.start()
                waiting = loop.run_in_executor(None, worker.wait)
                tail = _EmitTail(emit_path)
                try:
                    while True:
                        done, _ = await asyncio.wait({waiting}, timeout=EMIT_POLL_S)
                        self._publish_emitted(job, tail)
                        if done:
                            break
                    outcome = waiting.result()
                finally:
                    try:
                        os.unlink(emit_path)
                    except OSError:
                        pass
                if outcome.ok:
                    job.result = outcome.payload.get("result") or {}
                    # Процесс завершился чисто, но операция могла отказать — как
                    # у неизолированных. Прежде 29 операций из 42 при отказе
                    # значились в ленте «выполнено» (найдено обзором).
                    job.status = "failed" if job.result.get("error") else "done"
                else:
                    job.result = {"error": outcome.message,
                                  "report": f"ОШИБКА: {outcome.message}",
                                  "worker_status": outcome.status}
                    job.status = "cancelled" if outcome.status == "cancelled" else "failed"
            else:
                from datum.doc.window import SERVING

                serving = SERVING.set(True)
                try:
                    job.result = await spec.fn(**job.kwargs)
                finally:
                    SERVING.reset(serving)
                job.status = "failed" if job.result.get("error") else "done"
        except Exception as e:                    # pragma: no cover
            job.result = {"error": f"{type(e).__name__}: {e}",
                          "report": f"ОШИБКА: {type(e).__name__}: {e}"}
            job.status = "failed"
        finally:
            job.finished = time.time()
            job._worker = None
            # События самой операции идут ПЕРЕД «задание готово»: окну полезнее
            # получить геометрию до того, как ему скажут, что всё кончилось.
            self._republish(job)
            self.hub.publish(f"job.{job.status}", job=job.id, op=spec.name,
                             actor=job.actor, ms=round(job.ms, 1),
                             error=(job.result or {}).get("error"))

    def _publish_emitted(self, job: "Job", tail: "_EmitTail") -> None:
        """Разослать то, о чём операция сообщила по ходу работы. Вид события
        сверяется с тем же списком, что у событий из результата."""
        for event in tail.read():
            kind = str(event.get("t", ""))
            if kind not in PUBLISHABLE_EVENTS:
                continue
            job._emitted.add(_event_key(event))
            self.hub.publish(kind, job=job.id, actor=job.actor,
                             **{k: v for k, v in event.items() if k != "t"})

    def _republish(self, job: "Job") -> None:
        """Разослать события, о которых сообщила сама операция.

        Операция не знает о сервере и ничего не рассылает: она лишь складывает
        в результат список `events`. Так `part_build` остаётся одинаковым на
        всех трёх поверхностях, а сервер не обязан разбираться в том, что такое
        деталь.

        Вид события сверяется со списком: иначе операция — а через неё и чужой
        скрипт проекта — могла бы прислать `hello` или `job.done` и наврать
        окну о том, чего не было.
        """
        for event in (job.result or {}).get("events") or []:
            if not isinstance(event, dict):        # pragma: no cover
                continue
            kind = str(event.get("t", ""))
            if kind not in PUBLISHABLE_EVENTS:
                continue
            if _event_key(event) in job._emitted:
                continue                           # уже разослано по ходу работы
            self.hub.publish(kind, job=job.id, actor=job.actor,
                             **{k: v for k, v in event.items() if k != "t"})
        # Разослали — убираем. Это внутренняя передача от операции к серверу, и
        # в ответе клиенту ей делать нечего: окну она не нужна, а агенту — шум.
        if isinstance(job.result, dict):
            job.result.pop("events", None)

    # ------------------------------------------------------------ отмена
    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status not in ("pending", "running"):
            return False
        if job._worker is not None:
            job._worker.cancel()
            return True
        if job._task is not None:
            job._task.cancel()
            job.status = "cancelled"
            job.finished = time.time()
            self.hub.publish("job.cancelled", job=job.id, op=job.op, actor=job.actor)
            return True
        return False                              # pragma: no cover

    async def shutdown(self) -> None:
        for job in list(self._jobs.values()):
            if job.status in ("pending", "running"):
                self.cancel(job.id)
        tasks = [j._task for j in self._jobs.values() if j._task and not j._task.done()]
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
                pass


def _event_key(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)


class _EmitTail:
    """Читает файл событий операции с того места, где остановился. Строка без
    перевода в конце — недописанная: остаётся до следующего раза."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.offset = 0

    def read(self) -> list[dict]:
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self.offset += end + 1
        out = []
        for line in data[:end].split(b"\n"):
            try:
                event = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue                           # чужая строка — мимо
            if isinstance(event, dict):
                out.append(event)
        return out


def _job_body(spec: OpSpec, kwargs: dict, emit_path: str | None = None):
    """Тело для дочернего процесса. Замыкание годится: fork наследует всё."""

    def body() -> tuple[dict, bytes]:
        from datum.build.worker import emit_to
        from datum.tools.ops import clear_project_cache

        emit_to(emit_path)
        clear_project_cache()          # унаследованным соединением sqlite не пользуемся
        result = asyncio.run(spec.fn(**kwargs))
        return ({"status": "ok", "result": result}, b"")

    return body
