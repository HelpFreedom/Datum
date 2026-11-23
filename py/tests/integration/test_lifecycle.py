"""Жизнь и смерть сервера как отдельного процесса.

Оба случая здесь — настоящие отказы, найденные на практике, а не выдуманные
крайности. Их общая цена: двадцать семь осиротевших серверов, каждый со своим
портом, кешем и процессами сборки.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PYTHON = sys.executable


def wait_for(path: Path, process: subprocess.Popen, limit: float = 30.0) -> dict:
    """Дождаться файла рукопожатия."""
    deadline = time.time() + limit
    while time.time() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            raise AssertionError(f"сервер упал на старте, код {process.returncode}")
        time.sleep(0.1)
    raise AssertionError("сервер не написал рукопожатия")


def died_within(process: subprocess.Popen, limit: float) -> float | None:
    """Сколько секунд занял уход процесса; None — не ушёл."""
    started = time.time()
    while time.time() - started < limit:
        if process.poll() is not None:
            return time.time() - started
        time.sleep(0.1)
    return None


def serve(project: Path, handshake: Path, *extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, "-m", "datum.cli", "serve", str(project),
         "--handshake", str(handshake), *extra],
        cwd=str(ROOT / "py"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@contextlib.contextmanager
def open_socket(port: int, token: str):
    """Открыть WebSocket и подтвердить, что он принят."""
    from websockets.sync.client import connect

    with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=15) as ws:
        ws.send(json.dumps({"token": token}))
        hello = json.loads(ws.recv(timeout=15))
        assert hello["t"] == "hello"
        yield ws


# ----------------------------------------------------------- остановка
def test_server_stops_even_with_a_window_attached(latch, tmp_path):
    """Найденный отказ: пока открыт WebSocket, сервер не уходил по SIGTERM.

    Причина — uvicorn при остановке ЖДЁТ закрытия соединений, а обработчик
    ленты спал на очереди событий и о разрыве не узнавал никак. Ждать он мог
    вечно. Именно поэтому рядом с отправкой работает задача, которая только и
    делает, что ждёт весточку о разрыве.
    """
    handshake = tmp_path / "hs.json"
    process = serve(latch, handshake)
    try:
        info = wait_for(handshake, process)
        with open_socket(info["port"], info["token"]):
            process.send_signal(signal.SIGTERM)
            spent = died_within(process, 4.0)
            assert spent is not None, "сервер пережил SIGTERM при открытом окне"
            # Запасной таймаут uvicorn — пять секунд; если мы укладываемся в
            # две, значит сработал именно чистый путь, а не он.
            assert spent < 2.0, f"ушёл только по запасному таймауту ({spent:.1f} с)"
    finally:
        if process.poll() is None:
            process.kill()


def test_server_stops_without_a_window(latch, tmp_path):
    handshake = tmp_path / "hs.json"
    process = serve(latch, handshake)
    try:
        wait_for(handshake, process)
        process.send_signal(signal.SIGTERM)
        assert died_within(process, 4.0) is not None
    finally:
        if process.poll() is None:
            process.kill()


# --------------------------------------------------------- сторож родителя
def test_server_dies_with_its_parent(latch, tmp_path):
    """Найденный отказ: оболочку убивают сигналом, её обработчики выхода не
    выполняются, и сервер остаётся жить сиротой с занятым портом.

    Полагаться на уборку родителя нельзя вовсе, поэтому сервер сам следит: у
    осиротевшего процесса меняется `getppid()`.
    """
    handshake = tmp_path / "hs.json"

    # Промежуточный процесс изображает оболочку: его мы и убьём.
    parent = subprocess.Popen(
        [PYTHON, "-c",
         "import subprocess, sys, os, time;"
         "p = subprocess.Popen([sys.executable, '-m', 'datum.cli', 'serve',"
         f" {str(latch)!r}, '--handshake', {str(handshake)!r},"
         " '--parent-pid', str(os.getpid())]);"
         "print(p.pid, flush=True); time.sleep(120)"],
        cwd=str(ROOT / "py"), stdout=subprocess.PIPE, text=True)
    try:
        child_pid = int(parent.stdout.readline().strip())
        deadline = time.time() + 30
        while not handshake.is_file() and time.time() < deadline:
            time.sleep(0.1)
        assert handshake.is_file(), "сервер не поднялся"

        parent.kill()
        parent.wait(timeout=10)

        # Сторож смотрит раз в секунду; трёх секунд с запасом достаточно.
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                os.kill(child_pid, 0)
            except OSError:
                break
            time.sleep(0.2)
        else:
            os.kill(child_pid, signal.SIGKILL)
            pytest.fail("сервер пережил своего родителя")
    finally:
        if parent.poll() is None:
            parent.kill()
