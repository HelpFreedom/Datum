"""Дверь агента как тонкий клиент работающего сервера.

Смысл этого слоя один: сделать так, чтобы правка, сделанная агентом, была видна
в открытом окне НЕМЕДЛЕННО. Если бы MCP считал всё у себя, окно бы ничего не
узнало — оно живёт событиями сервера, а событий бы не было.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os

import pytest

from datum.tools.registry import OPS
from datum.tools.remote import (HANDSHAKE_ENV, Endpoint, endpoint_from_env,
                                remote_callable)

import datum.tools.ops  # noqa: F401  — объявление операций


@pytest.fixture
def handshake(tmp_path, monkeypatch):
    path = tmp_path / "hs.json"
    path.write_text(json.dumps(
        {"port": 45678, "token": "токен-пробы", "pid": 1, "project": "/тут/проект"}),
        encoding="utf-8")
    monkeypatch.setenv(HANDSHAKE_ENV, str(path))
    return path


# ------------------------------------------------------------- рукопожатие
def test_endpoint_is_read_from_the_file(handshake):
    endpoint = endpoint_from_env()
    assert endpoint is not None
    assert endpoint.port == 45678 and endpoint.token == "токен-пробы"
    assert endpoint.base == "http://127.0.0.1:45678"


def test_no_handshake_means_no_server(monkeypatch):
    """Агент запущен в обычном терминале, без окна. Это не беда: дверь просто
    считает у себя, как раньше."""
    monkeypatch.delenv(HANDSHAKE_ENV, raising=False)
    assert endpoint_from_env() is None


def test_a_broken_handshake_is_not_a_crash(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("не json", encoding="utf-8")
    monkeypatch.setenv(HANDSHAKE_ENV, str(bad))
    assert endpoint_from_env() is None

    monkeypatch.setenv(HANDSHAKE_ENV, str(tmp_path / "нет-такого"))
    assert endpoint_from_env() is None


def test_the_token_never_travels_through_the_environment(handshake):
    """В /proc/<pid>/environ окружение видно любому процессу пользователя.
    Поэтому в переменной лежит только ПУТЬ к файлу с правами 0600."""
    assert "токен-пробы" not in json.dumps(dict(os.environ), ensure_ascii=False)


# ------------------------------------------------------------- сигнатура
def test_project_becomes_optional_when_a_server_is_open():
    """Сервер открыт ровно на ОДНОМ проекте, и заставлять агента повторять путь
    — лишний повод ошибиться. Умолчания в теле мало: слой MCP проверяет
    аргументы по схеме, а схема выводится из сигнатуры."""
    endpoint = Endpoint(1, "probe-token", "/тут/проект")
    signature = inspect.signature(remote_callable(OPS["part_build"], endpoint))
    assert signature.parameters["project"].default == "/тут/проект"


def test_other_arguments_stay_required():
    endpoint = Endpoint(1, "probe-token", "/тут/проект")
    signature = inspect.signature(remote_callable(OPS["measure_probe"], endpoint))
    assert signature.parameters["part"].default is inspect.Parameter.empty
    assert signature.parameters["project"].default == "/тут/проект"


def test_annotations_survive_the_substitution():
    """Мина: в модуле операций стоит `from __future__ import annotations`, и без
    eval_str аннотации приезжают СТРОКАМИ. Схему по ним не построить — имя
    `Project` в модуле обёртки неизвестно."""
    endpoint = Endpoint(1, "probe-token", "/тут/проект")
    signature = inspect.signature(remote_callable(OPS["part_build"], endpoint))
    for parameter in signature.parameters.values():
        assert not isinstance(parameter.annotation, str), parameter


# ---------------------------------------------------------------- пересылка
def test_a_dead_server_is_reported_as_a_refusal_not_a_crash():
    """Окно закрыли, а агент ещё работает. Он обязан получить внятный отказ, а
    не трейсбек в середине разговора."""
    # Токен ASCII намеренно: заголовки HTTP — latin-1, и кириллица в них
    # уронила бы клиент раньше, чем он дошёл бы до сокета. Настоящий токен
    # выдаётся secrets.token_urlsafe и всегда ASCII.
    endpoint = Endpoint(1, "probe-token", "/тут/проект")   # порт 1 никто не слушает
    result = asyncio.run(remote_callable(OPS["project_info"], endpoint)())
    assert "недоступен" in result["error"]
    assert result["report"].startswith("ОШИБКА")


def test_tool_set_is_the_same_with_and_without_a_server(handshake, monkeypatch):
    """Главное обещание: набор возможностей у агента один и тот же. Меняется
    только исполнитель — сервер или свой процесс."""
    from datum.tools.mcp_adapter import build_server

    with_server = {t.name for t in asyncio.run(build_server().list_tools())}
    monkeypatch.delenv(HANDSHAKE_ENV, raising=False)
    without = {t.name for t in asyncio.run(build_server().list_tools())}
    assert with_server == without


# ------------------------------------------------ куда идёт вызов агента
@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """Настоящий сервер на копии latch, со своим рантайм-каталогом."""
    with _serving(tmp_path, monkeypatch) as served:
        yield served


from contextlib import contextmanager  # noqa: E402


@contextmanager
def _serving(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import sys
    import time
    from pathlib import Path

    runtime = tmp_path / "run"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv(HANDSHAKE_ENV, raising=False)
    project = tmp_path / "latch"
    shutil.copytree(Path(__file__).resolve().parents[3] / "examples" / "latch", project)
    hs = tmp_path / "hs.json"
    env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime)}
    proc = subprocess.Popen([sys.executable, "-m", "datum.cli", "serve", str(project),
                             "--handshake", str(hs)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            if hs.exists() and hs.stat().st_size:
                break
            time.sleep(0.05)
        info = json.loads(hs.read_text())
        # Рукопожатие пишется ДО того, как uvicorn поднял сокет: ждём, пока
        # сервер действительно ответит, иначе вызов законно уйдёт считаться
        # локально, и тест проверял бы гонку, а не маршрут.
        from datum.tools.remote import endpoint_alive

        ep = Endpoint(info["port"], info["token"], info["project"], info["pid"])
        for _ in range(200):
            if endpoint_alive(ep):
                break
            time.sleep(0.05)
        yield project, info, runtime
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def _jobs(info) -> list:
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/jobs",
                                 headers={"X-Datum-Token": info["token"]})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.load(opener.open(req, timeout=10))["jobs"]


def test_an_agent_without_a_handshake_finds_the_running_server(live_server):
    """Агент в обычном терминале: рукопожатия у него нет, а сервер поднят.
    Раньше он считал у себя, и его правки в открытое окно не попадали."""
    from datum.tools.remote import routed_callable

    project, info, _ = live_server
    out = asyncio.run(routed_callable(OPS["part_build"])(project=str(project)))
    assert out.get("ok"), out
    assert [(j["op"], j["actor"]) for j in _jobs(info)] == [("part_build", "agent")]


def test_a_relative_project_path_is_resolved_by_the_agent(live_server, monkeypatch):
    from datum.tools.remote import routed_callable

    project, info, _ = live_server
    monkeypatch.chdir(project)
    out = asyncio.run(routed_callable(OPS["project_info"])(project="."))
    assert out.get("name") == "latch", out
    assert [j["op"] for j in _jobs(info)] == ["project_info"]


def test_the_announcement_is_withdrawn_when_the_server_stops(tmp_path, monkeypatch):
    """После SIGTERM uvicorn заново поднимает сигнал, и `finally` не доходит:
    снимать объявление приходится при остановке приложения."""
    import time

    with _serving(tmp_path, monkeypatch) as (project, info, runtime):
        servers = runtime / "datum" / "servers"
        assert len(list(servers.glob("*.json"))) == 1
    for _ in range(40):
        if not list(servers.glob("*.json")):
            break
        time.sleep(0.1)
    assert not list(servers.glob("*.json"))


def test_a_stale_handshake_does_not_kill_the_tools(tmp_path, monkeypatch):
    """Окно закрыли, переменная у агента осталась. Раньше после этого ВСЕ
    инструменты отвечали «сервер недоступен», даже не нуждавшиеся в нём."""
    from pathlib import Path

    from datum.tools.remote import routed_callable

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    stale = Endpoint(1, "x", str(tmp_path), pid=999999)
    status = asyncio.run(routed_callable(OPS["datum_status"], stale)())
    assert "error" not in status
    latch = Path(__file__).resolve().parents[3] / "examples" / "latch"
    info = asyncio.run(routed_callable(OPS["project_info"], stale)(project=str(latch)))
    assert info.get("name") == "latch", info


# ------------------------------------- аудит 2026-11-21: пути от каталога агента
def test_relative_file_paths_are_resolved_on_the_agent_side(tmp_path, monkeypatch):
    """Сервер окна запущен из каталога Datum: относительный путь агента он
    разрешал от своего каталога — «нет такого файла» или архив не там."""
    import asyncio
    from dataclasses import replace

    import datum.tools.ops  # noqa: F401
    from datum.tools import remote
    from datum.tools.registry import OPS

    seen: dict = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return {"report": "ok"}

    monkeypatch.chdir(tmp_path)
    for name, args in (("mesh_diagnose", {"path": "scan.stl"}),
                       ("mesh_repair", {"path": "scan.stl", "out": "вышло/fixed.stl"})):
        seen.clear()
        spec = replace(OPS[name], fn=fake, isolate=False)
        asyncio.run(remote.routed_callable(spec)(**args))
        for key, value in args.items():
            assert seen[key] == str(tmp_path / value), (name, key, seen)
    # Все аргументы-пути реестра известны обёртке, и других с такими именами нет.
    named = {(s.name, a.name) for s in OPS.values() for a in s.args
             if a.name in remote._PATH_ARGS}
    assert named == {("project_save", "dest"), ("project_unpack", "path"),
                     ("project_unpack", "into"), ("mesh_diagnose", "path"),
                     ("import_model", "path"), ("import_to_bed", "path"),
                     ("mesh_repair", "path"), ("mesh_repair", "out")}
