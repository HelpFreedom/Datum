"""Проверка PID наблюдает за процессом, которым владеет тест."""

import subprocess
import sys

from datum.process import process_alive


def test_process_survives_probe_and_exit_is_seen():
    code = "import sys; sys.stdin.readline(); print('ok')"
    with subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, text=True) as child:
        try:
            assert process_alive(child.pid)
            out, _ = child.communicate("\n", timeout=10)
            assert out.strip() == "ok" and child.returncode == 0
            assert not process_alive(child.pid)
        finally:
            if child.poll() is None:
                child.kill()


def test_invalid_pid():
    for pid in (None, True, 0, -1, 2**40):
        assert not process_alive(pid)
