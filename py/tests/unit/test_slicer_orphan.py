"""Слайсер не переживает отменённое задание.

Аудит 2026-11-21: слайсер запущен своей сессией, а отмена снимает группу
воркера — слайсер оставался сиротой и резал в уже удалённом каталоге.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PY_ROOT = Path(__file__).resolve().parents[2]

_CHILD = """
import sys
sys.path.insert(0, {root!r})
from pathlib import Path
from datum.io import slicer
slicer._run([sys.executable, "-c",
             "import os, time; open({pidfile!r}, 'w').write(str(os.getpid())); time.sleep(120)"],
            Path({cwd!r}), 100)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_slicer_dies_with_the_worker_that_was_killed(tmp_path):
    pidfile = tmp_path / "pid"
    worker = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(root=str(PY_ROOT), pidfile=str(pidfile),
                                             cwd=str(tmp_path))],
        start_new_session=True)
    try:
        for _ in range(100):
            if pidfile.is_file() and pidfile.read_text().strip():
                break
            time.sleep(0.1)
        slicer_pid = int(pidfile.read_text())
        assert _alive(slicer_pid)
        os.killpg(worker.pid, signal.SIGKILL)          # так отмена снимает воркер
        worker.wait(timeout=10)
        deadline = time.time() + 12
        while _alive(slicer_pid) and time.time() < deadline:
            time.sleep(0.2)
        assert not _alive(slicer_pid), "слайсер остался сиротой"
    finally:
        if worker.poll() is None:
            worker.kill()


def test_a_finished_run_leaves_no_guard_behind(tmp_path):
    from datum.io import slicer

    before = _guard_pids()
    done = slicer._run([sys.executable, "-c", "print('ok')"], tmp_path, 30)
    assert done.returncode == 0 and b"ok" in done.stdout
    time.sleep(0.3)
    mine = _guard_pids() - before
    assert not mine, f"сторож обязан уйти вместе с отработавшим слайсером: {mine}"


#: Строка, по которой узнаётся сторож слайсера: он содержит её в своей
#: командной строке (`datum/io/slicer.py`).
_GUARD_MARK = "os.killpg(group"


def _guard_pids() -> set[int]:
    """Живые сторожа слайсера. Linux — по `/proc`, macOS — через `ps`.

    `/proc` есть только в Linux, а прежняя проверка читала его безусловно:
    на macOS тест падал с FileNotFoundError, ничего не проверив.
    """
    if os.path.isdir("/proc"):
        pids = set()
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                cmd = Path(f"/proc/{name}/cmdline").read_bytes()
            except OSError:
                continue
            if _GUARD_MARK.encode() in cmd:
                pids.add(int(name))
        return pids
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if _GUARD_MARK in cmd and pid.isdigit():
            pids.add(int(pid))
    return pids
