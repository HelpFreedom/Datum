"""Запуск сервера Datum.

Сервер садится на СЛУЧАЙНЫЙ порт петлевого интерфейса и сообщает порт с токеном
через файл рукопожатия, а не через аргументы командной строки: те видны в `ps`
любому процессу на машине.
"""

from __future__ import annotations

import atexit
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from datum.server.app import create_app
from datum.server.handshake import READY_MARKER, announce, withdraw, write_handshake
from datum.server.security import (LOOPBACK_NAMES, ServerSecurity,
                                   hostname_only, new_token)

__all__ = ["serve", "free_port", "watch_parent"]


def free_port(host: str = "127.0.0.1") -> int:
    """Свободный порт. Сокет закрывается сразу — гонка возможна, но на петле
    она умозрительна, а просить у ядра порт заранее удобнее."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def watch_parent(parent_pid: int, period: float = 1.0) -> None:
    """Умереть вместе с родителем.

    Полагаться на то, что оболочка приберёт за собой, нельзя: её саму могут
    убить сигналом, и тогда обработчики выхода не выполняются вовсе. Проверено
    на практике — после прогонов накопилось 27 осиротевших серверов, каждый со
    своим портом и процессами сборки.

    Признак сиротства на Linux надёжен и дёшев: у потерявшего родителя процесса
    меняется `getppid()` — его усыновляет init или ближайший сборщик. Дальше
    посылаем себе SIGTERM, а не `os._exit`: uvicorn по нему гасит соединения и
    снимает незавершённые задания, и это занимает полсекунды.
    """

    def loop() -> None:
        while True:
            time.sleep(period)
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    thread = threading.Thread(target=loop, name="datum-parent-watch", daemon=True)
    thread.start()


def serve(project: str | Path, *, host: str = "127.0.0.1", port: int = 0,
          token: str = "", handshake: str | Path | None = None,
          ui: str | Path | None = None, parent_pid: int = 0,
          log_level: str = "warning") -> int:
    project = Path(project).resolve()
    if not project.is_dir():
        print(f"нет каталога проекта: {project}", file=sys.stderr)
        return 2

    # Петля — не умолчание, а условие. Сервер исполняет произвольные скрипты
    # сборки и раздаёт файлы проекта; вынести его в сеть означает раздать это
    # же всем, кто до машины дотянется. Отказ здесь честнее, чем привязка,
    # которую охрана потом всё равно отвергнет запрос за запросом.
    if hostname_only(host) not in LOOPBACK_NAMES:
        print(f"сервер поднимается только на петлевом интерфейсе, "
              f"а запрошен {host!r}. Допустимо: "
              f"{', '.join(sorted(LOOPBACK_NAMES))}", file=sys.stderr)
        return 2

    # Порт открывается ЗДЕСЬ, до объявления готовности, и отдаётся uvicorn
    # готовым: соединения, пришедшие раньше, чем он начнёт принимать, ждут в
    # очереди ядра. Прежде порт брался «свободный», закрывался и открывался
    # снова уже uvicorn — после DATUM_READY; под нагрузкой окно стучалось в
    # эту щель, получало отказ и закрывалось вместе с сервером.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port or 0))
    sock.listen(128)
    port = int(sock.getsockname()[1])
    security = ServerSecurity(token=token or new_token())
    app = create_app(project, security=security, ui=ui)

    # Сторож ставится ДО объявления готовности: если оболочка умерла, пока мы
    # поднимались, сервер не должен пережить её ни секунды.
    if parent_pid:
        watch_parent(parent_pid)

    # Объявление для агентов в обычном терминале (см. handshake.announce) —
    # раньше рукопожатия: тот, кто дождался рукопожатия, вправе рассчитывать,
    # что сервер уже виден и остальным. Снимается при выходе; если сервер убит
    # сигналом и не успел, читатель уберёт его сам — по мёртвому pid.
    notice = announce(port=port, token=security.token, project=str(project))
    app.state.datum.on_shutdown.append(lambda: withdraw(notice))
    atexit.register(withdraw, notice)
    path = write_handshake(port=port, token=security.token, project=str(project),
                           path=handshake)
    print(f"{READY_MARKER} {path}", flush=True)
    print(f"Datum слушает http://{host}:{port} — проект {project.name}",
          file=sys.stderr, flush=True)

    try:
        config = uvicorn.Config(app, host=host, port=port, log_level=log_level,
                                access_log=False,
                                # Второй рубеж на случай соединения, которое почему-то не
                                # закрылось: через пять секунд сервер уходит в любом случае.
                                timeout_graceful_shutdown=5)
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        withdraw(notice)
    return 0
