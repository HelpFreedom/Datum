"""Показ агента доходит до окна и из ДРУГОГО процесса.

`window_show` из командной строки (или из своего процесса MCP) исполняется не
сервером окна: событие оттуда до окна не дошло бы. Операция сама пересылает
вызов живому серверу проекта, и тот рассылает `window.show` по ленте. Здесь —
настоящий процесс сервера, настоящая лента WebSocket и вызов из CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from tests.integration.test_lifecycle import open_socket, serve, wait_for

ROOT = Path(__file__).resolve().parents[3]


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "datum.cli", *args], cwd=str(ROOT / "py"),
                          capture_output=True, text=True, timeout=120)


def test_show_from_the_command_line_reaches_the_window(latch, tmp_path):
    from datum.doc import window as W

    # Сервера нет — отказ словами, код 3.
    alone = _cli("show", str(latch), "--parts", "крышка")
    assert alone.returncode == 3 and "не открыто" in alone.stderr + alone.stdout

    handshake = tmp_path / "hs.json"
    process = serve(latch, handshake)
    try:
        info = wait_for(handshake, process)
        # Окно открыто: о себе сообщил сервер этого проекта (его pid).
        W.save(latch, W.sanitize({"selected": None, "hidden": [], "ghosts": [], "tool": "none",
                                  "section": None, "spread": 0, "camera": None,
                                  "readout": None, "picked": [],
                                  "parts": ["корпус", "крышка"]}), pid=process.pid)
        with open_socket(info["port"], info["token"]) as ws:
            done = _cli("show", str(latch), "--parts", "крышка", "--note", "вот губка")
            assert done.returncode == 0, done.stderr + done.stdout
            assert "Показано в окне" in done.stdout
            deadline, shown = time.time() + 15, None
            while time.time() < deadline and shown is None:
                event = json.loads(ws.recv(timeout=15))
                if event.get("t") == "window.show":
                    shown = event
            assert shown is not None, "событие показа не пришло в ленту окна"
            assert shown["parts"] == ["крышка"] and shown["note"] == "вот губка"
            assert shown["actor"] == "agent"
    finally:
        process.terminate()
        process.wait(timeout=10)
