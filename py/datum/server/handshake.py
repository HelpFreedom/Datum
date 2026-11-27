"""Файл рукопожатия: как оболочка узнаёт, куда стучаться и с каким токеном.

Сервер садится на случайный порт петлевого интерфейса и записывает порт с
токеном в файл с правами 0600 в рантайм-каталоге пользователя. Оболочка читает
файл и сразу его удаляет.

Почему не через аргументы командной строки: они видны в `ps` любому процессу на
машине, а токен — это и есть ключ от сервера. Почему не через переменные
окружения: их видно в /proc/<pid>/environ, и для потомков они наследуются.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from datum.process import process_alive

__all__ = ["handshake_dir", "write_handshake", "read_handshake", "READY_MARKER",
           "announce", "withdraw", "announced"]

#: Печатается в stdout, когда сервер готов принимать запросы.
READY_MARKER = "DATUM_READY"


def handshake_dir() -> Path:
    if os.name == "nt":
        from datum.server import _win_handshake as native

        d = Path(os.environ.get("XDG_RUNTIME_DIR") or native.local_data()) / "datum"
        with native.directory(d, create=True):
            return d
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    d = Path(base) / "datum"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:                               # pragma: no cover
        pass
    # Каталог обязан быть НАШИМ и закрытым. Без XDG_RUNTIME_DIR это /tmp/datum:
    # чужой, созданный заранее, позволил бы подменить объявление сервера (агент
    # пошёл бы на чужой порт) или подложить ссылку под запись токена.
    st = d.lstat()
    import stat as _stat

    if (_stat.S_ISLNK(st.st_mode) or not _stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.getuid() or st.st_mode & 0o077):
        raise PermissionError(
            f"каталог рукопожатия {d} не наш или открыт другим (владелец {st.st_uid}, "
            f"права {_stat.S_IMODE(st.st_mode):o}) — токен туда не пишется. Задайте "
            f"XDG_RUNTIME_DIR или уберите этот каталог")
    return d


def write_handshake(*, port: int, token: str, project: str,
                    path: str | Path | None = None) -> Path:
    target = Path(path) if path else handshake_dir() / f"{uuid.uuid4().hex}.json"
    payload = {"port": port, "token": token, "pid": os.getpid(), "project": project}
    if os.name == "nt":
        from datum.server import _win_handshake as native

        native.write(target, payload)
        return target
    # Создаём файл сразу с правами 0600: между созданием и chmod иначе есть
    # окно, в которое токен может прочитать кто угодно.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return target


@dataclass(frozen=True, slots=True)
class Handshake:
    port: int
    token: str
    pid: int
    project: str


def read_handshake(path: str | Path, *, remove: bool = True) -> Handshake:
    p = Path(path)
    if os.name == "nt":
        from datum.server import _win_handshake as native

        data = native.read(p, remove=remove)
    else:
        data = json.loads(p.read_text(encoding="utf-8"))
        if remove:
            p.unlink(missing_ok=True)
    return Handshake(port=int(data["port"]), token=str(data["token"]),
                     pid=int(data.get("pid", 0)), project=str(data.get("project", "")))


# ------------------------------------------------------------ объявление
#
# Файл рукопожатия одноразовый: его читает и сразу удаляет оболочка. Агенту в
# ОБЫЧНОМ терминале (с `.mcp.json` проекта, без окна за спиной) было неоткуда
# узнать, что сервер поднят, и он считал всё у себя — его правки в открытое
# окно не попадали, хотя документация обещала обратное. Поэтому, пока сервер
# жив, он держит объявление: тот же порт с токеном, в том же каталоге с правами
# 0700, файлом 0600. Прочесть его может только тот же пользователь — тот, кто и
# так может всё, что умеет сервер.


def _servers_dir() -> Path:
    d = handshake_dir() / "servers"
    if os.name == "nt":
        from datum.server import _win_handshake as native

        with native.directory(d, create=True):
            return d
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:                               # pragma: no cover
        pass
    # Каталог обязан быть НАШИМ и закрытым. Без XDG_RUNTIME_DIR это /tmp/datum:
    # чужой, созданный заранее, позволил бы подменить объявление сервера (агент
    # пошёл бы на чужой порт) или подложить ссылку под запись токена.
    st = d.lstat()
    import stat as _stat

    if (_stat.S_ISLNK(st.st_mode) or not _stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.getuid() or st.st_mode & 0o077):
        raise PermissionError(
            f"каталог рукопожатия {d} не наш или открыт другим (владелец {st.st_uid}, "
            f"права {_stat.S_IMODE(st.st_mode):o}) — токен туда не пишется. Задайте "
            f"XDG_RUNTIME_DIR или уберите этот каталог")
    return d


def announce(*, port: int, token: str, project: str) -> Path:
    """Объявить работающий сервер. Возвращает путь, который потом снимается."""
    import hashlib

    key = hashlib.sha256(str(Path(project).resolve()).encode("utf-8")).hexdigest()[:16]
    return write_handshake(port=port, token=token, project=str(Path(project).resolve()),
                           path=_servers_dir() / f"{key}-{os.getpid()}.json")


def withdraw(path: Path | None) -> None:
    if path is not None:
        Path(path).unlink(missing_ok=True)


def announced(project: str | Path) -> list[Handshake]:
    """Объявления живых серверов этого проекта. Мёртвые попутно убираются.

    Живость по pid — необходимое условие, но не достаточное: pid могли
    переиспользовать. Окончательно сервер проверяет вызывающий — запросом.
    """
    target = str(Path(project).resolve())
    out: list[Handshake] = []
    d = handshake_dir() / "servers"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            h = read_handshake(f, remove=False)
        except (OSError, ValueError, KeyError):
            continue
        if not process_alive(h.pid):
            f.unlink(missing_ok=True)             # сервер упал, не прибрав за собой
            continue
        if h.project == target:
            out.append(h)
    return out
